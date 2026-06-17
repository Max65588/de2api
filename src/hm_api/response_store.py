"""Local storage for Responses API objects."""

from __future__ import annotations

import json
import os
from typing import Any

from .config import CRED_DIR, RESPONSE_STORE_FILE


MAX_STORED_RESPONSES = 1000


def _read_store() -> dict[str, Any]:
    if not RESPONSE_STORE_FILE.exists():
        return {"responses": {}}
    try:
        with open(RESPONSE_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"responses": {}}
    if not isinstance(data, dict) or not isinstance(data.get("responses"), dict):
        return {"responses": {}}
    return data


def _write_store(data: dict[str, Any]) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    responses = data.get("responses")
    if isinstance(responses, dict) and len(responses) > MAX_STORED_RESPONSES:
        ordered = sorted(
            responses.items(),
            key=lambda item: item[1].get("created_at", 0) if isinstance(item[1], dict) else 0,
        )
        data["responses"] = dict(ordered[-MAX_STORED_RESPONSES:])
    with open(RESPONSE_STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(RESPONSE_STORE_FILE, 0o600)


def save_response(response: dict[str, Any], input_items: list[dict[str, Any]]) -> None:
    data = _read_store()
    responses = data.setdefault("responses", {})
    responses[response["id"]] = {
        "response": response,
        "input_items": input_items,
        "created_at": response.get("created_at", 0),
    }
    _write_store(data)


def get_response(response_id: str) -> dict[str, Any] | None:
    item = _read_store().get("responses", {}).get(response_id)
    if not isinstance(item, dict):
        return None
    response = item.get("response")
    return response if isinstance(response, dict) else None


def get_input_items(response_id: str) -> list[dict[str, Any]] | None:
    item = _read_store().get("responses", {}).get(response_id)
    if not isinstance(item, dict):
        return None
    input_items = item.get("input_items")
    return input_items if isinstance(input_items, list) else []


def delete_response(response_id: str) -> bool:
    data = _read_store()
    responses = data.get("responses", {})
    if response_id not in responses:
        return False
    del responses[response_id]
    _write_store(data)
    return True
