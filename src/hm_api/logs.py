"""Local API call logging."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .config import CALL_LOG_FILE, CRED_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_call_log(entry: dict[str, Any]) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    record = {"created_at": _now(), **entry}
    with open(CALL_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.chmod(CALL_LOG_FILE, 0o600)


def list_call_logs(limit: int = 200) -> list[dict[str, Any]]:
    if not CALL_LOG_FILE.exists():
        return []
    limit = max(1, min(limit, 1000))
    with open(CALL_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    records: list[dict[str, Any]] = []
    for line in reversed(lines[-limit:]):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records
