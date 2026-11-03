from __future__ import annotations

import copy
import threading
from pathlib import Path

from .util import config_dir, deep_merge, read_json, write_json

DEFAULTS: dict = {
    "last_device": None,
    "auto_connect": True,
    "protocol": "auto",
    "ble": {
        "write_with_response": False,
        "command_delay_ms": 250,
        "chunk_delay_ms": 10,
    },
    "serial": {
        "baud": 115200,
        "chunk_delay_ms": 3,
    },
    "render": {
        "dither": "auto",
        "threshold": 160,
        "brightness": 0,
        "contrast": 0,
        "supersample": 2,
        "invert": False,
        "rotate180": False,
        "feed_lines": 4,
    },
    "window": {"width": 1480, "height": 940},
}


class Settings:
    def __init__(self) -> None:
        self.path: Path = config_dir() / "settings.json"
        self._lock = threading.RLock()
        self._data = deep_merge(DEFAULTS, read_json(self.path, {}) or {})

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            self._data = deep_merge(self._data, patch or {})
            write_json(self.path, self._data)
            return copy.deepcopy(self._data)

    def set_last_device(self, device: dict | None) -> None:
        self.update({"last_device": device})
