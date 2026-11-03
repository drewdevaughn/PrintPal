from __future__ import annotations

import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__
from .render import test_document
from .state import BusyError, PrinterState
from .transport import SessionError, list_serial_ports


class _QuietServer(ThreadingHTTPServer):

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class PrintPalServer:
    def __init__(self, state: PrinterState, web_dir: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
        self.state = state
        self.web_dir = Path(web_dir)
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> int:
        handler = _make_handler(self.state, self.web_dir)
        self.httpd = _QuietServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="printpal-http", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _make_handler(state: PrinterState, web_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"PrintPal/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200) -> None:
            self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > 40 * 1024 * 1024:
                raise ValueError("Request is too large")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                value = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("Invalid JSON body") from exc
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _static(self, relative: str) -> None:
            relative = unquote(relative).lstrip("/")
            if not relative or relative.endswith("/"):
                relative = "index.html"
            candidate = (web_dir / relative).resolve()
            try:
                candidate.relative_to(web_dir.resolve())
            except ValueError:
                self._error(403, "Forbidden")
                return
            if not candidate.is_file():
                self._error(404, "Not found")
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if candidate.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif candidate.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif candidate.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            elif candidate.suffix == ".svg":
                content_type = "image/svg+xml"
            body = candidate.read_bytes()
            self._send(200, body, content_type)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    self._json({"ok": True, "version": __version__})
                    return
                if path == "/api/bootstrap":
                    settings = state.settings.get()
                    payload = state.snapshot(after_log=0)
                    payload.update(
                        {
                            "fonts": state.fonts.families(),
                            "ports": list_serial_ports(),
                            "settings": settings,
                        }
                    )
                    self._json(payload)
                    return
                if path == "/api/state":
                    after = int(query.get("after", ["0"])[0] or 0)
                    self._json(state.snapshot(after_log=after))
                    return
                if path == "/api/ports":
                    self._json({"ports": list_serial_ports()})
                    return
                if path.startswith("/api/image/"):
                    image_id = path.rsplit("/", 1)[-1]
                    image_path = state.images.path(image_id)
                    if image_path is None:
                        self._error(404, "Image not found")
                        return
                    self._send(
                        200,
                        image_path.read_bytes(),
                        "image/png",
                        {"Cache-Control": "public, max-age=86400"},
                    )
                    return
                if path.startswith("/api/"):
                    self._error(404, "Unknown endpoint")
                    return
                self._static(path)
            except Exception as exc:
                self._error(500, str(exc))

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._body()
                if path == "/api/scan":
                    timeout = float(body.get("timeout", 7.0))
                    timeout = max(2.0, min(20.0, timeout))
                    devices = state.scan(timeout)
                    self._json({"devices": devices})
                    return
                if path == "/api/connect":
                    kind = str(body.get("kind", "ble")).lower()
                    if kind == "serial":
                        info = state.connect_serial(str(body.get("port", "")), body.get("baud"))
                    else:
                        info = state.connect_ble(str(body.get("address", "")), str(body.get("name", "")))
                    self._json({"connection": info, "printer": state.printer_status})
                    return
                if path == "/api/disconnect":
                    state.disconnect()
                    self._json({"connection": state.connection_info()})
                    return
                if path == "/api/status":
                    info = state.refresh_status()
                    self._json({"printer": info, "connection": state.connection_info()})
                    return
                if path == "/api/preview":
                    doc = body.get("doc")
                    if not doc:
                        doc = test_document()
                    options = body.get("options") or {}
                    result = state.render(doc, options)
                    import io

                    buffer = io.BytesIO()
                    result.image.save(buffer, format="PNG", optimize=True)
                    headers = {
                        "X-Paper-Width": result.width,
                        "X-Paper-Height": result.height,
                        "X-Paper-Mm": f"{result.mm:.1f}",
                        "X-Coverage": f"{result.coverage:.4f}",
                        "X-Truncated": "1" if result.truncated else "0",
                        "X-Warnings": quote(json.dumps(result.warnings, ensure_ascii=False)),
                    }
                    self._send(200, buffer.getvalue(), "image/png", headers)
                    return
                if path == "/api/print":
                    doc = body.get("doc")
                    options = body.get("options") or {}
                    copies = max(1, min(20, int(body.get("copies", 1))))
                    if not doc:
                        doc = test_document()
                    result = state.render(doc, options)
                    job = state.start_print(result.image, options, copies=copies)
                    self._json({"job": job})
                    return
                if path == "/api/test":
                    options = body.get("options") or {}
                    job = state.print_test(options)
                    self._json({"job": job})
                    return
                if path == "/api/calibrate":
                    options = body.get("options") or {}
                    job = state.print_calibration(options)
                    self._json({"job": job})
                    return
                if path == "/api/feed":
                    lines = max(1, min(32, int(body.get("lines", 4))))
                    job = state.feed(lines)
                    self._json({"job": job})
                    return
                if path == "/api/cancel":
                    self._json({"cancelled": state.cancel_job()})
                    return
                if path == "/api/raw":
                    raw = str(body.get("hex", "")).replace(" ", "").replace("\n", "")
                    if not raw or len(raw) % 2:
                        raise ValueError("Hex payload must contain an even number of digits")
                    try:
                        data = bytes.fromhex(raw)
                    except ValueError as exc:
                        raise ValueError("Invalid hex payload") from exc
                    if len(data) > 4096:
                        raise ValueError("Raw payload is limited to 4096 bytes")
                    job = state.send_raw(data)
                    self._json({"job": job})
                    return
                if path == "/api/upload":
                    data = body.get("data")
                    if not isinstance(data, str):
                        raise ValueError("Missing image data")
                    meta = state.images.add_data_url(data, str(body.get("filename", "image")))
                    self._json(
                        {
                            "image": {
                                **meta,
                                "url": f"/api/image/{meta['id']}",
                            }
                        }
                    )
                    return
                if path == "/api/settings":
                    patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
                    settings = state.settings.update(patch)
                    self._json({"settings": settings})
                    return
                self._error(404, "Unknown endpoint")
            except BusyError as exc:
                self._error(409, str(exc))
            except SessionError as exc:
                state.log("error", str(exc))
                self._error(400, str(exc))
            except ValueError as exc:
                self._error(400, str(exc))
            except Exception as exc:
                state.log("error", f"API error: {exc}")
                self._error(500, str(exc))

    return Handler
