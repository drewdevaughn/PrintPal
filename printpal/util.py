from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / "PrintPal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / "PrintPal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def images_dir() -> Path:
    path = data_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clamp(value: Any, low: float, high: float, fallback: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback if fallback is not None else low)
    if number != number:
        number = float(fallback if fallback is not None else low)
    return max(low, min(high, number))


def clamp_int(value: Any, low: int, high: int, fallback: int | None = None) -> int:
    return int(round(clamp(value, low, high, fallback)))


def now_ms() -> int:
    return int(time.time() * 1000)


def human_bytes(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KB"
    return f"{count / (1024 * 1024):.1f} MB"


def deep_merge(base: dict, updates: dict) -> dict:
    result = dict(base)
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
