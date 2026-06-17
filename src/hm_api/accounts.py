"""Multi-account credential store."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import TOKEN_FILE
from .crypto import decrypt_value, load_auth_data, save_auth_data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.b64decode(payload).decode("utf-8")
        data = json.loads(decoded)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_legacy_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return decrypt_value(json.load(f))
    except Exception:
        return None


def _account_id(user_id: str | None, user_name: str | None) -> str:
    if user_id:
        return user_id
    if user_name:
        return user_name
    return uuid.uuid4().hex


def _ensure_store() -> dict[str, Any]:
    data = load_auth_data()
    accounts = data.get("accounts")
    if isinstance(accounts, dict):
        data.setdefault("active_account_id", next(iter(accounts), None))
        return data

    data["accounts"] = {}
    legacy = data.get("deveco")
    legacy_token = _load_legacy_token()
    if isinstance(legacy, dict) and legacy_token:
        payload = _parse_jwt_payload(legacy_token)
        user_id = str(payload.get("userId") or "")
        user_name = str(payload.get("userName") or "")
        account_id = _account_id(user_id, user_name)
        data["accounts"][account_id] = {
            "id": account_id,
            "user_id": user_id,
            "user_name": user_name,
            "country_code": "CN",
            "language": "zh_CN",
            "is_real_name": False,
            "created_at": _now(),
            "updated_at": _now(),
            "deveco": {
                "type": "oauth",
                "access": legacy.get("access", ""),
                "refresh": legacy.get("refresh", ""),
                "token": legacy_token,
            },
        }
        data["active_account_id"] = account_id
        save_auth_data(data)
    return data


def _public_account(account: dict[str, Any], active_id: str | None) -> dict[str, Any]:
    return {
        "id": account.get("id", ""),
        "user_id": account.get("user_id", ""),
        "user_name": account.get("user_name", ""),
        "country_code": account.get("country_code", "CN"),
        "language": account.get("language", "zh_CN"),
        "is_real_name": bool(account.get("is_real_name")),
        "created_at": account.get("created_at", ""),
        "updated_at": account.get("updated_at", ""),
        "active": account.get("id") == active_id,
    }


def list_accounts() -> list[dict[str, Any]]:
    data = _ensure_store()
    active_id = data.get("active_account_id")
    accounts = data.get("accounts", {})
    return [
        _public_account(account, active_id)
        for account in accounts.values()
        if isinstance(account, dict)
    ]


def list_account_ids() -> list[str]:
    data = _ensure_store()
    accounts = data.get("accounts", {})
    return [str(account_id) for account_id in accounts.keys()]


def get_account(account_id: str | None = None) -> dict[str, Any] | None:
    data = _ensure_store()
    accounts = data.get("accounts", {})
    selected_id = account_id or data.get("active_account_id")
    account = accounts.get(selected_id) if selected_id else None
    return account if isinstance(account, dict) else None


def get_active_session() -> dict[str, Any] | None:
    account = get_account()
    if not account:
        return None
    deveco = account.get("deveco", {})
    return {
        "account_id": account.get("id", ""),
        "user_id": account.get("user_id", ""),
        "user_name": account.get("user_name", ""),
        "access_token": deveco.get("access", ""),
        "refresh_token": deveco.get("refresh", ""),
        "jwt_token": deveco.get("token", ""),
    }


def current_access_token(account_id: str | None = None) -> str | None:
    account = get_account(account_id)
    if not account:
        return None
    deveco = account.get("deveco", {})
    token = deveco.get("access")
    return token if token else None


def save_account(
    *,
    user_id: str,
    user_name: str,
    access_token: str,
    refresh_token: str,
    jwt_token: str,
    country_code: str = "CN",
    language: str = "zh_CN",
    is_real_name: bool = False,
    set_active: bool = True,
) -> dict[str, Any]:
    data = _ensure_store()
    accounts = data.setdefault("accounts", {})
    account_id = _account_id(user_id, user_name)
    existing = accounts.get(account_id, {})
    created_at = existing.get("created_at") or _now()
    accounts[account_id] = {
        "id": account_id,
        "user_id": user_id,
        "user_name": user_name,
        "country_code": country_code,
        "language": language,
        "is_real_name": is_real_name,
        "created_at": created_at,
        "updated_at": _now(),
        "deveco": {
            "type": "oauth",
            "access": access_token,
            "refresh": refresh_token,
            "token": jwt_token,
        },
    }
    if set_active:
        data["active_account_id"] = account_id
    save_auth_data(data)
    return _public_account(accounts[account_id], data.get("active_account_id"))


def set_active_account(account_id: str) -> dict[str, Any] | None:
    data = _ensure_store()
    accounts = data.get("accounts", {})
    account = accounts.get(account_id)
    if not isinstance(account, dict):
        return None
    data["active_account_id"] = account_id
    save_auth_data(data)
    return _public_account(account, account_id)


def delete_account(account_id: str) -> bool:
    data = _ensure_store()
    accounts = data.get("accounts", {})
    if account_id not in accounts:
        return False
    del accounts[account_id]
    if data.get("active_account_id") == account_id:
        data["active_account_id"] = next(iter(accounts), None)
    save_auth_data(data)
    return True


def has_accounts() -> bool:
    return bool(list_accounts())


def export_accounts_data() -> dict[str, Any]:
    data = _ensure_store()
    accounts = data.get("accounts", {})
    portable_accounts = {
        account_id: account
        for account_id, account in accounts.items()
        if isinstance(account_id, str) and isinstance(account, dict)
    }
    return {
        "version": 1,
        "active_account_id": data.get("active_account_id"),
        "accounts": portable_accounts,
    }


def export_accounts(path: Path) -> int:
    payload = export_accounts_data()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(payload["accounts"])


def import_accounts_data(payload: dict[str, Any]) -> int:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Unsupported accounts export format")
    incoming = payload.get("accounts")
    if not isinstance(incoming, dict):
        raise ValueError("Invalid accounts export: accounts must be an object")

    data = _ensure_store()
    accounts = data.setdefault("accounts", {})
    imported = 0
    for account_id, account in incoming.items():
        if not isinstance(account_id, str) or not isinstance(account, dict):
            continue
        if not isinstance(account.get("deveco"), dict):
            continue
        account["id"] = account_id
        account["updated_at"] = _now()
        accounts[account_id] = account
        imported += 1

    active_id = payload.get("active_account_id")
    if isinstance(active_id, str) and active_id in accounts:
        data["active_account_id"] = active_id
    elif imported and not data.get("active_account_id"):
        data["active_account_id"] = next(iter(accounts), None)

    save_auth_data(data)
    return imported


def import_accounts(path: Path) -> int:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return import_accounts_data(payload)
