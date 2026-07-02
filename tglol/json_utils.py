from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tglol.config import Config


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("корень JSON должен быть объектом")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def pick_api(data: dict[str, Any] | None, config: Config) -> tuple[int, str]:
    data = data or {}
    api_id = data.get("app_id", data.get("api_id", config.telegram_api_id))
    api_hash = data.get("app_hash", data.get("api_hash", config.telegram_api_hash))
    if api_id in (None, ""):
        api_id = config.telegram_api_id
    if api_hash in (None, ""):
        api_hash = config.telegram_api_hash
    return int(api_id), str(api_hash)


def pick_twofa(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    for key in ("twoFA", "twofa_password", "twofa", "two_fa", "password"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def pick_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def pick_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def runtime_from_json(data: dict[str, Any]) -> dict[str, str]:
    return {
        "device": pick_str(data, "device", "device_model") or "Desktop",
        "sdk": pick_str(data, "sdk", "system_version") or "Windows 11 x64",
        "app_version": pick_str(data, "app_version") or "6.9.3 x64",
        "lang_code": pick_str(data, "lang_code") or "en",
        "system_lang_code": pick_str(data, "system_lang_code") or "en-US",
        "lang_pack": pick_str(data, "lang_pack") or "tdesktop",
    }


def json_identity(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "phone": pick_str(data, "phone", "phone_number"),
        "telegram_user_id": pick_int(data, "user_id", "id"),
        "username": pick_str(data, "username"),
        "first_name": pick_str(data, "first_name"),
        "last_name": pick_str(data, "last_name"),
        "session_file": pick_str(data, "session_file"),
    }
