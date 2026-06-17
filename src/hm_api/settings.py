"""Local runtime configuration."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from .config import APP_CONFIG_FILE, CRED_DIR

AccountStrategy = Literal["round_robin", "sticky"]
Theme = Literal["dark", "light"]


@dataclass(frozen=True)
class ApiKey:
    name: str
    key: str
    enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    admin_password: str | None
    api_keys: list[ApiKey]
    account_strategy: AccountStrategy = "sticky"
    theme: Theme = "dark"


def generate_api_key() -> str:
    return f"sk-{secrets.token_urlsafe(32)}"


def load_app_config() -> AppConfig:
    if not APP_CONFIG_FILE.exists():
        return AppConfig(admin_password=None, api_keys=[])
    with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return AppConfig(admin_password=None, api_keys=[])

    api_keys: list[ApiKey] = []
    for item in raw.get("api_keys", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = str(item.get("key") or "").strip()
        if not name or not key:
            continue
        api_keys.append(ApiKey(name=name, key=key, enabled=bool(item.get("enabled", True))))

    admin_password = raw.get("admin_password")
    strategy = raw.get("account_strategy")
    if strategy not in ("round_robin", "sticky"):
        strategy = "sticky"
    theme = raw.get("theme")
    if theme not in ("dark", "light"):
        theme = "dark"
    return AppConfig(
        admin_password=str(admin_password) if admin_password else None,
        api_keys=api_keys,
        account_strategy=strategy,
        theme=theme,
    )


def read_app_config_data() -> dict[str, Any]:
    if not APP_CONFIG_FILE.exists():
        return {
            "admin_password": None,
            "api_keys": [],
            "account_strategy": "sticky",
            "theme": "dark",
        }
    with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("api_keys", [])
    raw.setdefault("account_strategy", "sticky")
    raw.setdefault("theme", "dark")
    return raw


def write_app_config_data(data: dict[str, Any]) -> dict[str, Any]:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(APP_CONFIG_FILE, 0o600)
    return read_app_config_data()


def create_default_app_config(force: bool = False) -> dict[str, Any]:
    if APP_CONFIG_FILE.exists() and not force:
        raise FileExistsError(str(APP_CONFIG_FILE))

    CRED_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "admin_password": secrets.token_urlsafe(18),
        "account_strategy": "sticky",
        "theme": "dark",
        "api_keys": [
            {
                "name": "default",
                "key": generate_api_key(),
                "enabled": True,
            }
        ],
    }
    return write_app_config_data(data)
