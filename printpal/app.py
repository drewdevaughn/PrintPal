from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__
from .server import PrintPalServer
from .state import PrinterState
from .util import data_dir, resource_dir


def _free_port(host: str, preferred: int) -> int:
    import socket

    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    return preferred


def _browser_candidates() -> list[Path]:
    roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs",
    ]
    relative = [
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("BraveSoftware/Brave-Browser/Application/brave.exe"),
        Path("Microsoft/Edge Beta/Application/msedge.exe"),
    ]
    found = []
    for root in roots:
        for part in relative:
            candidate = root / part
            if candidate.is_file():
                found.append(candidate)
    return found


def open_window(url: str, width: int = 1480, height: int = 940) -> None:
    profile = data_dir() / "browser"
    profile.mkdir(parents=True, exist_ok=True)
    for executable in _browser_candidates():
        args = [
            str(executable),
            f"--app={url}",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
            "--disable-background-networking",
        ]
        try:
            subprocess.Popen(args, close_fds=True)
            return
        except OSError:
            continue
    webbrowser.open(url, new=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="printpal", description="PrintPal thermal print studio")
    parser.add_argument("--port", type=int, default=8787, help="local HTTP port")
    parser.add_argument("--host", default="127.0.0.1", help="bind address")
    parser.add_argument("--server-only", action="store_true", help="do not open the app window")
    parser.add_argument("--no-autoconnect", action="store_true", help="skip connecting to the last printer")
    parser.add_argument("--no-browser", action="store_true", help="alias for --server-only")
    parser.add_argument("--version", action="version", version=f"PrintPal {__version__}")
    args = parser.parse_args(argv)

    state = PrinterState()
    web_dir = resource_dir() / "web"
    if not (web_dir / "index.html").exists():
        print(f"error: web assets not found at {web_dir}", file=sys.stderr)
        return 2

    port = _free_port(args.host, args.port)
    server = PrintPalServer(state, web_dir, host=args.host, port=port)
    try:
        port = server.start()
    except OSError as exc:
        print(f"error: could not start local server: {exc}", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{port}/"
    print(f"{APP_NAME} {__version__}")
    print(f"  local app : {url}")
    print("  close this window (or press Ctrl+C) to quit")

    if not state.settings.get().get("auto_connect", True) or args.no_autoconnect:
        pass
    else:
        state.auto_connect()

    if not (args.server_only or args.no_browser):
        window = state.settings.get().get("window", {})
        open_window(url, int(window.get("width", 1480)), int(window.get("height", 940)))
    else:
        print(f"  open this URL in a browser: {url}")

    try:
        while True:
            time.sleep(0.8)
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        try:
            state.disconnect(silent=True)
        finally:
            server.stop()
    return 0
