from __future__ import annotations

import ctypes
import os
import sys

from printpal.app import main


def _ensure_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _fail(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, "PrintPal", 0x10)


if __name__ == "__main__":
    _ensure_streams()
    try:
        code = main()
    except BaseException as exc:
        _fail(f"PrintPal could not start.\n\n{type(exc).__name__}: {exc}")
        raise
    if code:
        _fail(f"PrintPal exited with code {code}.")
    raise SystemExit(code)
