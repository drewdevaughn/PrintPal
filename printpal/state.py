from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from PIL import Image

from . import __version__, protocol
from .fonts import FontLibrary
from .render import ImageStore, RenderResult, calibration_document, render_document, test_document
from .settings import Settings
from .transport import (
    BleSession,
    SerialSession,
    SessionError,
    list_serial_ports,
    scan_ble,
)
from .util import clamp_int, deep_merge, human_bytes, now_ms


class BusyError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class PrinterState:
    def __init__(self) -> None:
        self.settings = Settings()
        self.fonts = FontLibrary()
        self.images = ImageStore()
        self.session: BleSession | SerialSession | None = None
        self.printer_status: dict = {}
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._job_lock = threading.Lock()
        self._job_counter = 0
        self.job: dict = {
            "id": 0,
            "active": False,
            "kind": "",
            "phase": "idle",
            "percent": 0,
            "message": "Ready",
            "error": None,
            "started": 0,
            "finished": 0,
            "rows": 0,
            "sent": 0,
        }
        self._logs: list[dict] = []
        self._log_id = 0
        self._log_lock = threading.Lock()
        self.log("info", f"PrintPal {__version__} ready")

    def log(self, level: str, message: str) -> None:
        with self._log_lock:
            self._log_id += 1
            self._logs.append(
                {"id": self._log_id, "ts": now_ms(), "level": level, "message": str(message)}
            )
            if len(self._logs) > 600:
                del self._logs[:200]

    def logs_since(self, after: int = 0) -> list[dict]:
        with self._log_lock:
            return [entry for entry in self._logs if entry["id"] > int(after or 0)]

    def connection_info(self) -> dict:
        session = self.session
        if session is None:
            return {"connected": False}
        info = {
            "connected": bool(session.connected),
            "kind": session.kind,
            "protocol": session.profile.get("protocol", "escpos"),
            "write_uuid": session.profile.get("write_uuid"),
            "notify_uuid": session.profile.get("notify_uuid"),
            "mtu": getattr(session, "mtu", None),
            "services": session.profile.get("services", []),
        }
        if isinstance(session, BleSession):
            info.update({"name": session.name or "YHK printer", "address": session.address})
        else:
            info.update({"name": f"Serial {session.port_name}", "port": session.port_name})
        return info

    def connect_ble(self, address: str, name: str = "") -> dict:
        if not address:
            raise SessionError("No Bluetooth address was provided")
        self.disconnect(silent=True)
        settings = self.settings.get()
        session = BleSession(
            address=address,
            name=name,
            protocol_override=settings.get("protocol", "auto"),
            prefer_response=bool(settings.get("ble", {}).get("write_with_response")),
            command_delay_ms=int(settings.get("ble", {}).get("command_delay_ms", 250)),
            chunk_delay_ms=int(settings.get("ble", {}).get("chunk_delay_ms", 10)),
            logger=lambda message: self.log("info", message),
        )
        self.log("info", f"Connecting to {name or address} over Bluetooth LE…")
        try:
            profile = session.connect()
        except Exception:
            session.close()
            raise
        self.session = session
        self.settings.set_last_device({"kind": "ble", "address": address, "name": name})
        self.printer_status = {}
        self.log(
            "success",
            f"Connected to {name or address} — protocol {profile.get('protocol')} "
            f"({profile.get('write_uuid')})",
        )
        self.refresh_status()
        return self.connection_info()

    def connect_serial(self, port: str, baud: int | None = None) -> dict:
        if not port:
            raise SessionError("No COM port was selected")
        self.disconnect(silent=True)
        settings = self.settings.get()
        session = SerialSession(
            port=port,
            baud=int(baud or settings.get("serial", {}).get("baud", 115200)),
            protocol_override=settings.get("protocol", "auto"),
            command_delay_ms=120,
            chunk_delay_ms=int(settings.get("serial", {}).get("chunk_delay_ms", 3)),
            logger=lambda message: self.log("info", message),
        )
        self.log("info", f"Opening {port} at {session.baud} baud…")
        try:
            session.connect()
        except Exception:
            session.close()
            raise
        self.session = session
        self.settings.set_last_device({"kind": "serial", "port": port, "name": port})
        self.printer_status = {}
        self.log("success", f"Connected to {port}")
        self.refresh_status()
        return self.connection_info()

    def disconnect(self, silent: bool = False) -> None:
        session = self.session
        self.session = None
        self.printer_status = {}
        if session is not None:
            session.close()
            if not silent:
                self.log("info", "Printer disconnected")

    def refresh_status(self) -> dict:
        session = self.session
        if session is None or not session.connected:
            return {}
        try:
            info = session.status()
            self.printer_status = info
            status = info.get("status") or {}
            if status.get("voltage_mv"):
                self.log(
                    "info",
                    f"Printer status: {status.get('voltage_mv')} mV "
                    f"({status.get('battery', '?')}%), serial {info.get('serial') or '?'}",
                )
            return info
        except Exception as exc:
            self.log("warn", f"Could not read printer status: {exc}")
            return {}

    def render_options(self, options: dict | None = None) -> dict:
        base = self.settings.get().get("render", {})
        return deep_merge(base, options or {})

    def render(self, doc: dict, options: dict | None = None) -> RenderResult:
        return render_document(doc, self.render_options(options), self.fonts, self.images)

    def _publish(self, job: dict, **updates) -> None:
        with self._lock:
            job.update(updates)

    def start_job(self, kind: str, runner: Callable[[dict], None]) -> dict:
        if not self._job_lock.acquire(blocking=False):
            raise BusyError("A print job is already running")
        try:
            with self._lock:
                self._job_counter += 1
                job = {
                    "id": self._job_counter,
                    "active": True,
                    "kind": kind,
                    "phase": "starting",
                    "percent": 0,
                    "message": "Starting…",
                    "error": None,
                    "started": now_ms(),
                    "finished": 0,
                    "rows": 0,
                    "sent": 0,
                }
                self.job = job
            self._cancel.clear()
        except Exception:
            self._job_lock.release()
            raise

        def worker() -> None:
            try:
                runner(job)
                if job.get("phase") not in ("error", "cancelled"):
                    self._publish(
                        job,
                        active=False,
                        phase="done",
                        percent=100,
                        message=job.get("message") or "Done",
                        finished=now_ms(),
                    )
            except Cancelled:
                self._publish(
                    job, active=False, phase="cancelled", message="Cancelled", finished=now_ms()
                )
                self.log("warn", "Job cancelled")
            except Exception as exc:
                self._publish(
                    job,
                    active=False,
                    phase="error",
                    error=str(exc),
                    message=str(exc),
                    finished=now_ms(),
                )
                self.log("error", f"{kind.capitalize()} failed: {exc}")
            finally:
                self._job_lock.release()

        threading.Thread(target=worker, name=f"printpal-{kind}", daemon=True).start()
        return dict(job)

    def cancel_job(self) -> bool:
        if self.job.get("active"):
            self._cancel.set()
            return True
        return False

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _transmit(self, image: Image.Image, options: dict, job: dict, copies: int = 1) -> None:
        session = self.session
        if session is None or not session.connected:
            raise SessionError("Connect a printer first")
        protocol_name = session.profile.get("protocol", "escpos")
        is_ble = session.kind == "ble"
        default_rows = 1 if is_ble else 24
        rows_per_chunk = clamp_int(options.get("rowsPerChunk", default_rows), 1, 64, default_rows)
        chunk_delay = max(0.0, float(options.get("chunkDelayMs", session.chunk_delay * 1000)) / 1000.0)
        if is_ble:
            chunk_delay = max(chunk_delay, 0.006)
        command_delay = max(0.0, float(options.get("commandDelayMs", session.command_delay * 1000)) / 1000.0)
        feed_lines = clamp_int(options.get("feedLines", 4), 1, 12, 4)
        total_rows = image.height

        for copy_index in range(max(1, int(copies))):
            self._check_cancel()
            self._publish(
                job,
                phase="sending",
                message=f"Sending copy {copy_index + 1} of {copies}…",
                rows=total_rows,
                sent=0,
                percent=0,
            )
            if protocol_name == "cat":
                density = clamp_int(options.get("density", 3), 1, 5, 3)
                energy = int(0x0400 + (density - 1) * 0x0700)
                for message in protocol.cat_settings(energy=energy, quality=density):
                    session.write(message, reliable=True)
                    time.sleep(0.05)
                sent = 0
                for chunk, rows in protocol.cat_chunks(image, 1):
                    self._check_cancel()
                    session.write(chunk, reliable=False)
                    session.write(protocol.cat_feed(1), reliable=True)
                    sent += rows
                    self._publish(
                        job,
                        sent=sent,
                        percent=min(99, int(sent / total_rows * 100)),
                        message=f"Printing {sent}/{total_rows} lines",
                    )
                    time.sleep(0.025)
                session.write(protocol.cat_feed(feed_lines), reliable=True)
                time.sleep(0.6)
            else:
                session.write(protocol.ESC_INIT, reliable=True)
                time.sleep(max(0.15, command_delay))
                session.write(protocol.ESC_START, reliable=True)
                time.sleep(max(0.15, command_delay))
                sent = 0
                for chunk, rows in protocol.escpos_chunks(image, rows_per_chunk):
                    self._check_cancel()
                    session.write(chunk, reliable=False)
                    sent += rows
                    percent = min(99, int(sent / total_rows * 100))
                    self._publish(
                        job,
                        sent=sent,
                        percent=percent,
                        message=f"Printing {sent}/{total_rows} lines · {human_bytes(sent * (image.width // 8))}",
                    )
                    time.sleep(chunk_delay)
                session.write(protocol.escpos_feed(feed_lines), reliable=True)
                time.sleep(0.9 if is_ble else 0.35)

        self._publish(job, percent=100, message="Print complete", sent=total_rows)

    def start_print(
        self,
        image: Image.Image,
        options: dict | None = None,
        copies: int = 1,
        label: str = "print",
    ) -> dict:
        if self.session is None or not self.session.connected:
            raise SessionError("Connect a printer first")
        merged = self.render_options(options)

        def runner(job: dict) -> None:
            self._transmit(image, merged, job, copies=copies)

        self.log(
            "info",
            f"Print job started: {image.width}×{image.height} px "
            f"({image.height / 8.0:.0f} mm), {copies} copy/copies",
        )
        return self.start_job(label, runner)

    def feed(self, lines: int = 4) -> dict:
        session = self.session
        if session is None or not session.connected:
            raise SessionError("Connect a printer first")

        def runner(job: dict) -> None:
            self._publish(job, phase="sending", percent=50, message=f"Feeding {lines} lines")
            session.feed(lines)
            time.sleep(0.5)
            self._publish(job, percent=100, message="Paper fed")

        self.log("info", f"Feeding paper by {lines} line(s)")
        return self.start_job("feed", runner)

    def send_raw(self, data: bytes) -> dict:
        session = self.session
        if session is None or not session.connected:
            raise SessionError("Connect a printer first")

        def runner(job: dict) -> None:
            self._publish(job, phase="sending", percent=50, message=f"Sending {len(data)} bytes")
            session.write(data, reliable=True)
            self._publish(job, percent=100, message="Raw bytes sent")

        self.log("info", f"Raw TX: {data.hex(' ')}")
        return self.start_job("raw", runner)

    def print_test(self, options: dict | None = None) -> dict:
        result = self.render(test_document(), options)
        return self.start_print(result.image, options, label="test print")

    def print_calibration(self, options: dict | None = None) -> dict:
        result = self.render(calibration_document(), options)
        return self.start_print(result.image, options, label="calibration")

    def scan(self, timeout: float = 7.0) -> list[dict]:
        self.log("info", "Scanning for Bluetooth LE printers…")
        devices = scan_ble(timeout)
        self.log("info", f"Scan finished: {len(devices)} BLE device(s) found")
        return devices

    def ports(self) -> list[dict]:
        return list_serial_ports()

    def auto_connect(self) -> None:
        settings = self.settings.get()
        if not settings.get("auto_connect", True):
            return
        last = settings.get("last_device")
        if not last:
            return

        def attempt() -> None:
            try:
                if last.get("kind") == "serial":
                    self.connect_serial(last.get("port", ""))
                elif last.get("address"):
                    self.connect_ble(last.get("address", ""), last.get("name", ""))
            except Exception as exc:
                self.log("warn", f"Auto-connect skipped: {exc}")

        threading.Thread(target=attempt, name="printpal-autoconnect", daemon=True).start()

    def snapshot(self, after_log: int = 0) -> dict:
        with self._lock:
            job = dict(self.job)
        return {
            "version": __version__,
            "connection": self.connection_info(),
            "printer": self.printer_status,
            "job": job,
            "logs": self.logs_since(after_log),
            "settings": self.settings.get(),
        }
