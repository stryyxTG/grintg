from pathlib import Path
import re
import time

from tglol.config import Config


SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._+\-]+")


def ensure_storage(config: Config) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.sessions_dir.mkdir(parents=True, exist_ok=True)
    config.json_dir.mkdir(parents=True, exist_ok=True)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str, fallback: str) -> str:
    clean = SAFE_FILENAME_RE.sub("_", Path(name).name).strip("._")
    return clean or fallback


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(filename, "file")
    path = directory / filename
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    stamp = int(time.time())
    for index in range(1, 1000):
        candidate = directory / f"{stem}_{stamp}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot allocate filename for {filename}")
