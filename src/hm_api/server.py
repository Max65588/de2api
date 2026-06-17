"""FastAPI OpenAI-compatible proxy server for DevEco Code."""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .accounts import (
    current_access_token,
    delete_account,
    export_accounts_data,
    import_accounts_data,
    list_account_ids,
    list_accounts,
    save_account,
    set_active_account,
)
from .config import (
    DEVECO_APP_ID,
    DEVECO_AUTH_URL,
    DEVECO_BASE_URL,
    DEVECO_JWT_TOKEN_CHECK_URL,
    DEVECO_TEMP_TOKEN_CHECK_URL,
    USER_AGENT,
)
from .login import _build_client, _parse_callback, _parse_jwt
from .logs import append_call_log, list_call_logs
from .response_adapter import (
    append_tool_delta,
    build_chat_request,
    chat_completion_to_response,
    chat_tool_call_to_response,
    new_response_id,
    output_text,
    sse_event,
)
from .response_store import delete_response, get_input_items, get_response, save_response
from .settings import ApiKey, generate_api_key, load_app_config, read_app_config_data, write_app_config_data


TARGET_BASE = f"{DEVECO_BASE_URL}/sse/codeGenie/maas"
ADMIN_COOKIE = "hm_admin_session"
ADMIN_DIR = Path(__file__).with_name("static") / "admin"
ADMIN_HTML = ADMIN_DIR / "index.html"
OAUTH_CALLBACK_PORT_ENV = "HM_API_OAUTH_CALLBACK_PORT"


class AdminLoginRequest(BaseModel):
    password: str


class ActiveAccountRequest(BaseModel):
    account_id: str


class AccountsImportRequest(BaseModel):
    payload: dict


class OAuthCompleteRequest(BaseModel):
    callback_url: str


class ApiKeyCreateRequest(BaseModel):
    name: str
    key: str | None = None
    enabled: bool = True


class ApiKeyUpdateRequest(BaseModel):
    name: str | None = None
    key: str | None = None
    enabled: bool | None = None


class ConfigUpdateRequest(BaseModel):
    admin_password: str | None = None
    account_strategy: str | None = None
    theme: str | None = None


def _request_account_id(request: Request) -> str | None:
    return (
        request.headers.get("x-hm-account-id")
        or request.headers.get("x-deveco-account")
        or request.query_params.get("account_id")
    )


def _current_access_token(account_id: str | None = None) -> str | None:
    return current_access_token(account_id)


def _request_api_key_name(request: Request) -> str:
    value = getattr(request.state, "api_key_name", "")
    return str(value) if value else ""


def _public_config() -> dict:
    data = read_app_config_data()
    return {
        "admin_password_set": bool(data.get("admin_password")),
        "account_strategy": data.get("account_strategy", "sticky"),
        "theme": data.get("theme", "dark"),
        "api_keys": [
            {
                "name": item.get("name", ""),
                "key": item.get("key", ""),
                "enabled": bool(item.get("enabled", True)),
            }
            for item in data.get("api_keys", [])
            if isinstance(item, dict)
        ],
    }


def _oauth_callback_port(request: Request) -> int:
    configured = os.getenv(OAUTH_CALLBACK_PORT_ENV, "").strip()
    if configured:
        try:
            port = int(configured)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"{OAUTH_CALLBACK_PORT_ENV} must be an integer",
            ) from exc
        if not 1 <= port <= 65535:
            raise HTTPException(
                status_code=500,
                detail=f"{OAUTH_CALLBACK_PORT_ENV} must be between 1 and 65535",
            )
        return port
    return request.url.port or (443 if request.url.scheme == "https" else 80)


def _upsert_api_key(name: str, key: str, enabled: bool = True, old_name: str | None = None) -> dict:
    name = name.strip()
    key = key.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Key name is required")
    if not key:
        raise HTTPException(status_code=400, detail="Key value is required")
    data = read_app_config_data()
    api_keys = [item for item in data.get("api_keys", []) if isinstance(item, dict)]
    target_name = old_name or name
    replaced = False
    for item in api_keys:
        if item.get("name") == target_name:
            item.update({"name": name, "key": key, "enabled": enabled})
            replaced = True
            break
    if not replaced:
        if any(item.get("name") == name for item in api_keys):
            raise HTTPException(status_code=409, detail="Key name already exists")
        api_keys.append({"name": name, "key": key, "enabled": enabled})
    data["api_keys"] = api_keys
    write_app_config_data(data)
    return {"name": name, "key": key, "enabled": enabled}


def _delete_api_key(name: str) -> bool:
    data = read_app_config_data()
    api_keys = [item for item in data.get("api_keys", []) if isinstance(item, dict)]
    next_keys = [item for item in api_keys if item.get("name") != name]
    if len(next_keys) == len(api_keys):
        return False
    data["api_keys"] = next_keys
    write_app_config_data(data)
    return True


def _choose_account_id(
    *,
    requested_account_id: str | None,
    api_key_name: str,
    account_strategy: str,
    rr_state: dict[str, int],
) -> str | None:
    if requested_account_id:
        return requested_account_id
    account_ids = list_account_ids()
    if not account_ids:
        return None
    if account_strategy == "round_robin":
        idx = rr_state["index"] % len(account_ids)
        rr_state["index"] += 1
        return account_ids[idx]
    if api_key_name:
        idx = zlib.crc32(api_key_name.encode("utf-8")) % len(account_ids)
        return account_ids[idx]
    return None


def _duration_ms(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1000)


def _error_summary(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


def _extract_sse_data(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line[5:].strip()


def _list_response(data: list[dict], *, limit: int = 20, order: str = "desc") -> dict:
    limit = max(1, min(limit, 100))
    ordered = data if order == "asc" else list(reversed(data))
    page = ordered[:limit]
    return {
        "object": "list",
        "data": page,
        "first_id": page[0].get("id") if page else None,
        "last_id": page[-1].get("id") if page else None,
        "has_more": len(ordered) > limit,
    }


def build_app(
    api_key: str | None = None,
    proxy: str | None = None,
    admin_password: str | None = None,
) -> FastAPI:
    app = FastAPI(title="hm-api", version="0.1.0")
    app_config = load_app_config()
    admin_password = admin_password or os.getenv("HM_API_ADMIN_PASSWORD") or app_config.admin_password
    api_keys = [item for item in app_config.api_keys if item.enabled]
    account_strategy = app_config.account_strategy
    rr_state = {"index": 0}
    cli_api_key = api_key
    if api_key:
        api_keys.append(ApiKey(name="cli", key=api_key, enabled=True))
    admin_sessions: set[str] = set()
    pending_oauth: dict[str, float] = {}

    mounts: dict[str, httpx.AsyncHTTPTransport] | None = None
    if proxy:
        transport = httpx.AsyncHTTPTransport(proxy=proxy)
        mounts = {"http://": transport, "https://": transport}

    client = httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN"},
        timeout=httpx.Timeout(600.0),
        follow_redirects=True,
        mounts=mounts,
    )
    if ADMIN_DIR.exists():
        app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR / "assets"), name="admin-assets")

    def require_admin(request: Request) -> None:
        if not admin_password:
            raise HTTPException(status_code=503, detail="Admin UI is disabled")
        session_id = request.cookies.get(ADMIN_COOKIE)
        if not session_id or session_id not in admin_sessions:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def refresh_api_keys() -> None:
        nonlocal api_keys
        next_config = load_app_config()
        api_keys = [item for item in next_config.api_keys if item.enabled]
        if cli_api_key:
            api_keys.append(ApiKey(name="cli", key=cli_api_key, enabled=True))

    async def complete_oauth_callback(path: str, body: bytes = b"") -> dict:
        params = _parse_callback(path, body)
        code = params.get("code")
        temp_token = params.get("tempToken")
        site_id = params.get("siteId")
        if not code or code not in pending_oauth:
            raise HTTPException(status_code=400, detail="Invalid or expired login request")
        if time.time() - pending_oauth.pop(code) > 600:
            raise HTTPException(status_code=400, detail="Login request expired")
        if site_id != "1":
            raise HTTPException(
                status_code=400,
                detail="Only China site accounts are currently supported",
            )
        if not temp_token:
            raise HTTPException(status_code=400, detail="Missing tempToken")

        actual_temp_token = temp_token.split("&")[0]
        async with _build_client(proxy, timeout=30.0) as oauth_client:
            jwt_resp = await oauth_client.get(
                f"{DEVECO_BASE_URL}/{DEVECO_TEMP_TOKEN_CHECK_URL}",
                params={
                    "tempToken": actual_temp_token,
                    "site": "CN",
                    "version": "1.0.0",
                    "appid": DEVECO_APP_ID,
                },
            )
            if jwt_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to get DevEco JWT")
            jwt_token = jwt_resp.text.strip()
            info_resp = await oauth_client.get(
                f"{DEVECO_BASE_URL}/{DEVECO_JWT_TOKEN_CHECK_URL}",
                headers={"refresh": "false", "jwtToken": jwt_token},
            )
            if info_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to validate DevEco JWT")
            info_data = info_resp.json()
            if not info_data.get("status") or not info_data.get("userInfo"):
                raise HTTPException(status_code=502, detail="Invalid DevEco user info")

        raw = info_data["userInfo"]
        payload = _parse_jwt(jwt_token)
        return save_account(
            user_id=payload.get("userId", ""),
            user_name=payload.get("userName", ""),
            access_token=raw.get("accessToken", ""),
            refresh_token=raw.get("refreshToken", ""),
            jwt_token=jwt_token,
            country_code="CN",
            language="zh_CN",
            is_real_name=raw.get("realName") == "true",
            set_active=True,
        )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path.startswith("/admin") or request.url.path == "/callback":
            return await call_next(request)
        if not api_keys:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        provided_key = auth[7:]
        for item in api_keys:
            if secrets.compare_digest(provided_key, item.key):
                request.state.api_key_name = item.name
                return await call_next(request)
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    @app.get("/admin", response_model=None)
    async def admin_page() -> HTMLResponse:
        if ADMIN_HTML.exists():
            return HTMLResponse(ADMIN_HTML.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Admin UI is missing</h1>", status_code=500)

    @app.post("/admin/api/login", response_model=None)
    async def admin_login(body: AdminLoginRequest) -> JSONResponse:
        if not admin_password:
            raise HTTPException(status_code=503, detail="Admin UI is disabled")
        if not secrets.compare_digest(body.password, admin_password):
            raise HTTPException(status_code=401, detail="Invalid password")
        session_id = secrets.token_urlsafe(32)
        admin_sessions.add(session_id)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            ADMIN_COOKIE,
            session_id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 12,
        )
        return resp

    @app.post("/admin/api/logout", response_model=None)
    async def admin_logout(request: Request) -> JSONResponse:
        session_id = request.cookies.get(ADMIN_COOKIE)
        if session_id:
            admin_sessions.discard(session_id)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(ADMIN_COOKIE)
        return resp

    @app.get("/admin/api/session", response_model=None)
    async def admin_session(request: Request) -> JSONResponse:
        enabled = bool(admin_password)
        session_id = request.cookies.get(ADMIN_COOKIE)
        authenticated = bool(enabled and session_id in admin_sessions)
        return JSONResponse({"enabled": enabled, "authenticated": authenticated})

    @app.get("/admin/api/accounts", response_model=None)
    async def admin_accounts(request: Request) -> JSONResponse:
        require_admin(request)
        return JSONResponse({"accounts": list_accounts()})

    @app.get("/admin/api/logs", response_model=None)
    async def admin_logs(request: Request, limit: int = 200) -> JSONResponse:
        require_admin(request)
        return JSONResponse({"logs": list_call_logs(limit=limit)})

    @app.get("/admin/api/config", response_model=None)
    async def admin_config(request: Request) -> JSONResponse:
        require_admin(request)
        return JSONResponse(_public_config())

    @app.patch("/admin/api/config", response_model=None)
    async def admin_update_config(request: Request, body: ConfigUpdateRequest) -> JSONResponse:
        nonlocal admin_password, account_strategy
        require_admin(request)
        data = read_app_config_data()
        if body.admin_password is not None:
            if not body.admin_password.strip():
                raise HTTPException(status_code=400, detail="Admin password cannot be empty")
            data["admin_password"] = body.admin_password
            admin_password = body.admin_password
        if body.account_strategy is not None:
            if body.account_strategy not in {"round_robin", "sticky"}:
                raise HTTPException(status_code=400, detail="Invalid account strategy")
            data["account_strategy"] = body.account_strategy
            account_strategy = body.account_strategy
        if body.theme is not None:
            if body.theme not in {"dark", "light"}:
                raise HTTPException(status_code=400, detail="Invalid theme")
            data["theme"] = body.theme
        write_app_config_data(data)
        return JSONResponse(_public_config())

    @app.post("/admin/api/keys", response_model=None)
    async def admin_create_key(request: Request, body: ApiKeyCreateRequest) -> JSONResponse:
        require_admin(request)
        key = body.key.strip() if body.key else generate_api_key()
        item = _upsert_api_key(body.name, key, body.enabled)
        refresh_api_keys()
        return JSONResponse({"key": item})

    @app.patch("/admin/api/keys/{name}", response_model=None)
    async def admin_update_key(request: Request, name: str, body: ApiKeyUpdateRequest) -> JSONResponse:
        require_admin(request)
        data = read_app_config_data()
        api_keys = [item for item in data.get("api_keys", []) if isinstance(item, dict)]
        current = next((item for item in api_keys if item.get("name") == name), None)
        if current is None:
            raise HTTPException(status_code=404, detail="Key not found")
        next_name = body.name if body.name is not None else str(current.get("name") or "")
        next_key = body.key if body.key is not None else str(current.get("key") or "")
        next_enabled = body.enabled if body.enabled is not None else bool(current.get("enabled", True))
        item = _upsert_api_key(next_name, next_key, next_enabled, old_name=name)
        refresh_api_keys()
        return JSONResponse({"key": item})

    @app.delete("/admin/api/keys/{name}", response_model=None)
    async def admin_delete_key(request: Request, name: str) -> JSONResponse:
        require_admin(request)
        if not _delete_api_key(name):
            raise HTTPException(status_code=404, detail="Key not found")
        refresh_api_keys()
        return JSONResponse({"ok": True})

    @app.post("/admin/api/accounts/active", response_model=None)
    async def admin_set_active(request: Request, body: ActiveAccountRequest) -> JSONResponse:
        require_admin(request)
        account = set_active_account(body.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        return JSONResponse({"account": account})

    @app.delete("/admin/api/accounts/{account_id}", response_model=None)
    async def admin_delete_account(request: Request, account_id: str) -> JSONResponse:
        require_admin(request)
        if not delete_account(account_id):
            raise HTTPException(status_code=404, detail="Account not found")
        return JSONResponse({"ok": True})

    @app.get("/admin/api/accounts/export", response_model=None)
    async def admin_export_accounts(request: Request) -> JSONResponse:
        require_admin(request)
        payload = export_accounts_data()
        return JSONResponse(
            payload,
            headers={
                "Content-Disposition": 'attachment; filename="hm-api-accounts-export.json"',
            },
        )

    @app.post("/admin/api/accounts/import", response_model=None)
    async def admin_import_accounts(
        request: Request,
        body: AccountsImportRequest,
    ) -> JSONResponse:
        require_admin(request)
        try:
            count = import_accounts_data(body.payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"imported": count, "accounts": list_accounts()})

    @app.post("/admin/api/oauth/start", response_model=None)
    async def admin_oauth_start(request: Request) -> JSONResponse:
        require_admin(request)
        code = secrets.token_hex(16)
        pending_oauth[code] = time.time()
        port = _oauth_callback_port(request)
        login_url = (
            f"{DEVECO_BASE_URL}/{DEVECO_AUTH_URL}"
            f"?port={port}&appid={DEVECO_APP_ID}&code={code}"
        )
        return JSONResponse({"login_url": login_url})

    @app.post("/admin/api/oauth/complete", response_model=None)
    async def admin_oauth_complete(
        request: Request,
        body: OAuthCompleteRequest,
    ) -> JSONResponse:
        require_admin(request)
        account = await complete_oauth_callback(body.callback_url)
        return JSONResponse({"account": account})

    @app.api_route("/callback", methods=["GET", "POST"], response_model=None)
    async def oauth_callback(request: Request) -> HTMLResponse | RedirectResponse:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        try:
            await complete_oauth_callback(path, await request.body())
        except HTTPException as exc:
            return HTMLResponse(str(exc.detail), status_code=exc.status_code)
        return RedirectResponse("/admin?login=success", status_code=302)

    @app.get("/v1/models", response_model=None)
    async def list_models(request: Request) -> JSONResponse:
        started_at = time.perf_counter()
        requested_account_id = _request_account_id(request)
        api_key_name = _request_api_key_name(request)
        account_id = _choose_account_id(
            requested_account_id=requested_account_id,
            api_key_name=api_key_name,
            account_strategy=account_strategy,
            rr_state=rr_state,
        )
        request_id = uuid.uuid4().hex
        token = _current_access_token(account_id)
        if not token:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/models",
                    "method": "GET",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": 401,
                    "duration_ms": _duration_ms(started_at),
                    "error": "Not logged in",
                }
            )
            raise HTTPException(status_code=401, detail="Not logged in")
        resp = await client.get(
            f"{DEVECO_BASE_URL}/codeGenie/modelConfig?localVersion=0&pluginVersion=CLI.0.1.0",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/models",
                    "method": "GET",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": resp.status_code,
                    "duration_ms": _duration_ms(started_at),
                    "error": _error_summary(resp.text),
                }
            )
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
        models: list[dict] = []
        for group in data.get("body", {}).get("inner_models", []):
            for cfg in group.get("model_configs", []):
                model_id = cfg.get("model_id")
                if model_id:
                    models.append({"id": model_id, "object": "model", "owned_by": "deveco"})
        append_call_log(
            {
                "request_id": request_id,
                "endpoint": "/v1/models",
                "method": "GET",
                "account_id": account_id or "",
                "api_key_name": api_key_name,
                "status_code": 200,
                "duration_ms": _duration_ms(started_at),
                "model_count": len(models),
            }
        )
        return JSONResponse({"object": "list", "data": models})

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
        started_at = time.perf_counter()
        request_id = uuid.uuid4().hex
        api_key_name = _request_api_key_name(request)
        requested_account_id = _request_account_id(request)
        account_id = _choose_account_id(
            requested_account_id=requested_account_id,
            api_key_name=api_key_name,
            account_strategy=account_strategy,
            rr_state=rr_state,
        )
        token = _current_access_token(account_id)
        if not token:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/chat/completions",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": 401,
                    "duration_ms": _duration_ms(started_at),
                    "error": "Not logged in",
                }
            )
            raise HTTPException(status_code=401, detail="Not logged in")

        body_bytes = await request.body()
        if not body_bytes:
            body_bytes = b"{}"
        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/chat/completions",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": 400,
                    "duration_ms": _duration_ms(started_at),
                    "error": "Invalid JSON body",
                }
            )
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        stream = bool(body_json.get("stream"))
        model = str(body_json.get("model") or "")
        target_path = "v2/chat/completions"
        if not stream:
            target_path = "v2/no-stream/chat/completions"
        url = f"{TARGET_BASE}/{target_path}"

        session_id = request.headers.get("x-deveco-session") or request.headers.get("x-session-affinity")
        chat_id = uuid.uuid4().hex.replace("-", "")

        upstream_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "lang": "en",
            "Chat-Id": chat_id,
        }
        if session_id:
            upstream_headers["Session-Id"] = session_id

        for key, value in request.headers.items():
            lower = key.lower()
            if lower in {
                "host",
                "authorization",
                "x-hm-account-id",
                "x-deveco-account",
                "content-length",
                "content-type",
                "connection",
                "accept-encoding",
            }:
                continue
            upstream_headers[key] = value

        if stream:
            async def streamer() -> AsyncGenerator[bytes, None]:
                async with client.stream("POST", url, headers=upstream_headers, content=body_bytes) as upstream_resp:
                    if upstream_resp.status_code != 200:
                        text = await upstream_resp.aread()
                        error_text = text.decode("utf-8", errors="replace") or "Upstream error"
                        append_call_log(
                            {
                                "request_id": request_id,
                                "endpoint": "/v1/chat/completions",
                                "method": "POST",
                                "account_id": account_id or "",
                                "api_key_name": api_key_name,
                                "model": model,
                                "stream": True,
                                "status_code": upstream_resp.status_code,
                                "duration_ms": _duration_ms(started_at),
                                "error": _error_summary(error_text),
                            }
                        )
                        yield json.dumps({"error": error_text}).encode()
                        return
                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk
                append_call_log(
                    {
                        "request_id": request_id,
                        "endpoint": "/v1/chat/completions",
                        "method": "POST",
                        "account_id": account_id or "",
                        "api_key_name": api_key_name,
                        "model": model,
                        "stream": True,
                        "status_code": 200,
                        "duration_ms": _duration_ms(started_at),
                    }
                )

            return StreamingResponse(
                streamer(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        upstream_resp = await client.post(url, headers=upstream_headers, content=body_bytes)

        if upstream_resp.status_code != 200:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/chat/completions",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "model": model,
                    "stream": False,
                    "status_code": upstream_resp.status_code,
                    "duration_ms": _duration_ms(started_at),
                    "error": _error_summary(upstream_resp.text or "Upstream error"),
                }
            )
            return JSONResponse(
                content={"error": upstream_resp.text or "Upstream error"},
                status_code=upstream_resp.status_code,
            )

        append_call_log(
            {
                "request_id": request_id,
                "endpoint": "/v1/chat/completions",
                "method": "POST",
                "account_id": account_id or "",
                "api_key_name": api_key_name,
                "model": model,
                "stream": False,
                "status_code": upstream_resp.status_code,
                "duration_ms": _duration_ms(started_at),
            }
        )
        return JSONResponse(
            content=upstream_resp.json()
                if upstream_resp.headers.get("content-type", "").startswith("application/json")
                else {"data": upstream_resp.text},
            status_code=upstream_resp.status_code,
        )

    @app.post("/v1/responses", response_model=None)
    async def create_response(request: Request) -> StreamingResponse | JSONResponse:
        started_at = time.perf_counter()
        request_id = uuid.uuid4().hex
        response_id = new_response_id()
        api_key_name = _request_api_key_name(request)
        requested_account_id = _request_account_id(request)
        account_id = _choose_account_id(
            requested_account_id=requested_account_id,
            api_key_name=api_key_name,
            account_strategy=account_strategy,
            rr_state=rr_state,
        )
        token = _current_access_token(account_id)
        if not token:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/responses",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": 401,
                    "duration_ms": _duration_ms(started_at),
                    "error": "Not logged in",
                }
            )
            raise HTTPException(status_code=401, detail="Not logged in")

        body_bytes = await request.body()
        if not body_bytes:
            body_bytes = b"{}"
        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/responses",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "status_code": 400,
                    "duration_ms": _duration_ms(started_at),
                    "error": "Invalid JSON body",
                }
            )
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body_json, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")

        previous_response = None
        previous_response_id = body_json.get("previous_response_id")
        if previous_response_id:
            previous_response = get_response(str(previous_response_id))
            if previous_response is None:
                raise HTTPException(status_code=404, detail="Previous response not found")

        chat_body, input_items = build_chat_request(body_json, previous_response=previous_response)
        stream = bool(body_json.get("stream"))
        model = str(body_json.get("model") or "")
        target_path = "v2/chat/completions" if stream else "v2/no-stream/chat/completions"
        url = f"{TARGET_BASE}/{target_path}"
        upstream_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "lang": "en",
            "Chat-Id": uuid.uuid4().hex.replace("-", ""),
        }
        session_id = request.headers.get("x-deveco-session") or request.headers.get("x-session-affinity")
        if session_id:
            upstream_headers["Session-Id"] = session_id
        for key, value in request.headers.items():
            lower = key.lower()
            if lower in {
                "host",
                "authorization",
                "x-hm-account-id",
                "x-deveco-account",
                "content-length",
                "content-type",
                "connection",
                "accept-encoding",
            }:
                continue
            upstream_headers[key] = value

        chat_bytes = json.dumps(chat_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        if stream:
            async def response_streamer() -> AsyncGenerator[bytes, None]:
                text_parts: list[str] = []
                tool_calls: dict[int, dict[str, Any]] = {}
                usage: dict | None = None
                chat_id = f"chatcmpl_{uuid.uuid4().hex}"
                created = int(time.time())
                message_item_id = f"msg_{uuid.uuid4().hex}"
                message_added = False
                tool_added: set[int] = set()
                started_response = chat_completion_to_response(
                    response_id=response_id,
                    request_body=body_json,
                    chat_data={"id": chat_id, "choices": [], "usage": None},
                    created_at=created,
                    status="in_progress",
                )
                yield sse_event("response.created", started_response)

                async with client.stream("POST", url, headers=upstream_headers, content=chat_bytes) as upstream_resp:
                    if upstream_resp.status_code != 200:
                        text = await upstream_resp.aread()
                        error_text = text.decode("utf-8", errors="replace") or "Upstream error"
                        failed = chat_completion_to_response(
                            response_id=response_id,
                            request_body=body_json,
                            chat_data={"id": chat_id, "choices": [], "usage": None},
                            created_at=created,
                            status="failed",
                            error={"message": error_text, "type": "upstream_error"},
                        )
                        append_call_log(
                            {
                                "request_id": request_id,
                                "endpoint": "/v1/responses",
                                "method": "POST",
                                "account_id": account_id or "",
                                "api_key_name": api_key_name,
                                "model": model,
                                "stream": True,
                                "status_code": upstream_resp.status_code,
                                "duration_ms": _duration_ms(started_at),
                                "error": _error_summary(error_text),
                            }
                        )
                        yield sse_event("response.failed", failed)
                        return

                    async for line in upstream_resp.aiter_lines():
                        data_line = _extract_sse_data(line)
                        if not data_line:
                            continue
                        if data_line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(chunk, dict):
                            continue
                        chat_id = str(chunk.get("id") or chat_id)
                        chunk_created = chunk.get("created")
                        if isinstance(chunk_created, int | float | str):
                            created = int(chunk_created)
                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict):
                            usage = chunk_usage
                        choices = chunk.get("choices", [])
                        if not isinstance(choices, list):
                            continue
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            raw_delta = choice.get("delta")
                            delta: dict[str, Any] = raw_delta if isinstance(raw_delta, dict) else {}
                            content_delta = delta.get("content")
                            if content_delta:
                                if not message_added:
                                    message_added = True
                                    yield sse_event(
                                        "response.output_item.added",
                                        {
                                            "type": "response.output_item.added",
                                            "response_id": response_id,
                                            "output_index": 0,
                                            "item": {
                                                "id": message_item_id,
                                                "type": "message",
                                                "status": "in_progress",
                                                "role": "assistant",
                                                "content": [],
                                            },
                                        },
                                    )
                                    yield sse_event(
                                        "response.content_part.added",
                                        {
                                            "type": "response.content_part.added",
                                            "response_id": response_id,
                                            "item_id": message_item_id,
                                            "output_index": 0,
                                            "content_index": 0,
                                            "part": {"type": "output_text", "text": "", "annotations": []},
                                        },
                                    )
                                text_parts.append(str(content_delta))
                                yield sse_event(
                                    "response.output_text.delta",
                                    {
                                        "type": "response.output_text.delta",
                                        "response_id": response_id,
                                        "item_id": message_item_id,
                                        "output_index": 0,
                                        "content_index": 0,
                                        "delta": str(content_delta),
                                    },
                                )
                            for tool_call in delta.get("tool_calls") or []:
                                if not isinstance(tool_call, dict):
                                    continue
                                index = int(tool_call.get("index", 0))
                                append_tool_delta(tool_calls, tool_call)
                                current = tool_calls[index]
                                if index not in tool_added:
                                    tool_added.add(index)
                                    output_index = (1 if message_added else 0) + index
                                    yield sse_event(
                                        "response.output_item.added",
                                        {
                                            "type": "response.output_item.added",
                                            "response_id": response_id,
                                            "output_index": output_index,
                                            "item": chat_tool_call_to_response(current),
                                        },
                                    )
                                raw_function = tool_call.get("function")
                                function: dict[str, Any] = raw_function if isinstance(raw_function, dict) else {}
                                if function.get("arguments"):
                                    yield sse_event(
                                        "response.function_call_arguments.delta",
                                        {
                                            "type": "response.function_call_arguments.delta",
                                            "response_id": response_id,
                                            "item_id": current.get("id") or "",
                                            "output_index": (1 if message_added else 0) + index,
                                            "delta": str(function.get("arguments")),
                                        },
                                    )

                message = {
                    "role": "assistant",
                    "content": "".join(text_parts),
                    "tool_calls": [tool_calls[idx] for idx in sorted(tool_calls)],
                }
                chat_data = {
                    "id": chat_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": usage,
                }
                final_response = chat_completion_to_response(
                    response_id=response_id,
                    request_body=body_json,
                    chat_data=chat_data,
                    created_at=created,
                )
                if message_added:
                    yield sse_event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "response_id": response_id,
                            "item_id": message_item_id,
                            "output_index": 0,
                            "content_index": 0,
                            "text": output_text(final_response["output"]),
                        },
                    )
                for index, item in enumerate(final_response["output"]):
                    yield sse_event(
                        "response.output_item.done",
                        {
                            "type": "response.output_item.done",
                            "response_id": response_id,
                            "output_index": index,
                            "item": item,
                        },
                    )
                if body_json.get("store", True) is not False:
                    save_response(final_response, input_items)
                append_call_log(
                    {
                        "request_id": request_id,
                        "endpoint": "/v1/responses",
                        "method": "POST",
                        "account_id": account_id or "",
                        "api_key_name": api_key_name,
                        "model": model,
                        "stream": True,
                        "status_code": 200,
                        "duration_ms": _duration_ms(started_at),
                    }
                )
                yield sse_event("response.completed", final_response)

            return StreamingResponse(
                response_streamer(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        upstream_resp = await client.post(url, headers=upstream_headers, content=chat_bytes)
        if upstream_resp.status_code != 200:
            append_call_log(
                {
                    "request_id": request_id,
                    "endpoint": "/v1/responses",
                    "method": "POST",
                    "account_id": account_id or "",
                    "api_key_name": api_key_name,
                    "model": model,
                    "stream": False,
                    "status_code": upstream_resp.status_code,
                    "duration_ms": _duration_ms(started_at),
                    "error": _error_summary(upstream_resp.text or "Upstream error"),
                }
            )
            return JSONResponse(
                content={"error": {"message": upstream_resp.text or "Upstream error", "type": "upstream_error"}},
                status_code=upstream_resp.status_code,
            )
        chat_data = (
            upstream_resp.json()
            if upstream_resp.headers.get("content-type", "").startswith("application/json")
            else {"choices": [{"message": {"role": "assistant", "content": upstream_resp.text}}]}
        )
        if not isinstance(chat_data, dict):
            chat_data = {"choices": [{"message": {"role": "assistant", "content": str(chat_data)}}]}
        chat_created = chat_data.get("created")
        created_at = int(chat_created) if isinstance(chat_created, int | float | str) else int(time.time())
        response_obj = chat_completion_to_response(
            response_id=response_id,
            request_body=body_json,
            chat_data=chat_data,
            created_at=created_at,
        )
        if body_json.get("store", True) is not False:
            save_response(response_obj, input_items)
        append_call_log(
            {
                "request_id": request_id,
                "endpoint": "/v1/responses",
                "method": "POST",
                "account_id": account_id or "",
                "api_key_name": api_key_name,
                "model": model,
                "stream": False,
                "status_code": upstream_resp.status_code,
                "duration_ms": _duration_ms(started_at),
            }
        )
        return JSONResponse(content=response_obj, status_code=upstream_resp.status_code)

    @app.get("/v1/responses/{response_id}", response_model=None)
    async def retrieve_response(response_id: str) -> JSONResponse:
        response_obj = get_response(response_id)
        if response_obj is None:
            raise HTTPException(status_code=404, detail="Response not found")
        return JSONResponse(response_obj)

    @app.delete("/v1/responses/{response_id}", response_model=None)
    async def remove_response(response_id: str) -> JSONResponse:
        if not delete_response(response_id):
            raise HTTPException(status_code=404, detail="Response not found")
        return JSONResponse({"id": response_id, "object": "response.deleted", "deleted": True})

    @app.get("/v1/responses/{response_id}/input_items", response_model=None)
    async def list_response_input_items(
        response_id: str,
        limit: int = 20,
        order: str = "desc",
    ) -> JSONResponse:
        input_items = get_input_items(response_id)
        if input_items is None:
            raise HTTPException(status_code=404, detail="Response not found")
        return JSONResponse(_list_response(input_items, limit=limit, order=order))

    @app.post("/v1/responses/{response_id}/cancel", response_model=None)
    async def cancel_response(response_id: str) -> JSONResponse:
        response_obj = get_response(response_id)
        if response_obj is None:
            raise HTTPException(status_code=404, detail="Response not found")
        response_obj = {**response_obj, "status": "cancelled"}
        save_response(response_obj, get_input_items(response_id) or [])
        return JSONResponse(response_obj)

    @app.post("/v1/responses/{response_id}/compact", response_model=None)
    async def compact_response(response_id: str) -> JSONResponse:
        if get_response(response_id) is None:
            raise HTTPException(status_code=404, detail="Response not found")
        raise HTTPException(status_code=501, detail="Response compaction is not supported")

    return app


def run_server(
    host: str,
    port: int,
    api_key: str | None,
    proxy: str | None,
    admin_password: str | None,
) -> None:
    import uvicorn

    app = build_app(api_key=api_key, proxy=proxy, admin_password=admin_password)
    uvicorn.run(app, host=host, port=port)
