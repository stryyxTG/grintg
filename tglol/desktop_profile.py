from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Any

from tglol.config import Config


WINDOWS_DEVICES = [
    "Desktop",
    "PC",
    "Workstation",
    "Aspire M5802",
    "Aspire TC-1760",
    "Aspire XC-1660",
    "Predator G3-710",
    "Predator Orion 3000",
    "IdeaCentre 5",
    "ThinkCentre M720q",
    "ThinkCentre M920q",
    "ThinkCentre neo 50s",
    "ThinkPad T480",
    "ThinkPad T490",
    "ThinkPad T14",
    "ThinkPad T15",
    "ThinkPad X1 Carbon",
    "ThinkPad X1 Carbon Gen 9",
    "Lenovo Legion 5",
    "Lenovo Legion 7",
    "Dell XPS 13 9310",
    "Dell XPS 15 9500",
    "Dell Latitude 5420",
    "Dell Latitude 5520",
    "Dell Precision 3560",
    "Dell OptiPlex 7070",
    "Dell OptiPlex 7090",
    "HP EliteBook 840 G7",
    "HP EliteBook 850 G8",
    "HP ProBook 450 G8",
    "HP Pavilion Desktop",
    "HP ENVY Desktop",
    "HP OMEN 25L",
    "ASUS VivoBook 15",
    "ASUS ZenBook 14",
    "ASUS ROG Strix G15",
    "ASUS TUF Gaming F15",
    "MSI Modern 14",
    "MSI GF63 Thin",
    "MSI Katana GF66",
    "Gigabyte Brix",
    "Intel NUC",
    "Surface Laptop 4",
    "Surface Laptop 5",
    "Surface Pro 8",
    "Surface Pro 9",
]

LINUX_DEVICES = [
    "XPS 13 9310",
    "ThinkPad T14",
    "ThinkPad X1 Carbon",
    "Latitude 5420",
    "Precision 3560",
    "EliteBook 840 G7",
    "System76 Lemur Pro",
    "System76 Pangolin",
    "Framework Laptop",
    "Slimbook ProX",
    "TUXEDO InfinityBook",
    "Desktop Linux",
    "Linux Workstation",
]

WINDOWS_SDKS = [
    "Windows 11",
    "Windows 11 x64",
    "Windows 11 Pro",
    "Windows 11 Pro x64",
    "Windows 11 Home x64",
    "Windows 11 Enterprise x64",
    "Windows 10 x64",
    "Windows 10 Pro x64",
]

LINUX_SDKS = [
    "Ubuntu 24.04 x64",
    "Ubuntu 26.04 x64",
    "Debian 12 x64",
    "Fedora 40",
    "Fedora 41",
    "Arch Linux x64",
    "Linux Mint 22",
]

APP_VERSIONS = [
    "6.9.3 x64",
    "6.9.2 x64",
    "6.9.1 x64",
]


def random_desktop_runtime() -> dict[str, str]:
    platform = random.choices(["windows", "linux"], weights=[85, 15], k=1)[0]
    if platform == "windows":
        device = random.choice(WINDOWS_DEVICES)
        sdk = random.choice(WINDOWS_SDKS)
    else:
        device = random.choice(LINUX_DEVICES)
        sdk = random.choice(LINUX_SDKS)

    return {
        "device": device,
        "sdk": sdk,
        "app_version": random.choice(APP_VERSIONS),
        "lang_code": "en",
        "system_lang_code": "en-US",
        "lang_pack": "tdesktop",
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def random_created_at_iso() -> str:
    offset = timedelta(
        days=random.randint(0, 45),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return (datetime.now(timezone.utc) - offset).isoformat(timespec="seconds")


def generated_account_json(
    config: Config,
    *,
    runtime: dict[str, str] | None = None,
    twofa: str | None = None,
    session_file: str | None = None,
    phone: str | None = None,
    user_id: int | None = None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any]:
    runtime = runtime or random_desktop_runtime()
    return {
        "app_id": config.telegram_api_id,
        "app_hash": config.telegram_api_hash,
        "device": runtime["device"],
        "sdk": runtime["sdk"],
        "app_version": runtime["app_version"],
        "lang_code": config.default_lang_code,
        "system_lang_code": config.default_system_lang_code,
        "lang_pack": config.default_lang_pack,
        "twoFA": twofa,
        "phone": phone,
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "session_file": session_file,
        "created_at": random_created_at_iso(),
    }
