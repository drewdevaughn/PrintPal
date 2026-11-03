from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

from . import protocol


class SessionError(RuntimeError):
    pass


LIKELY_NAMES = (
    "yhk",
    "cchw",
    "cat",
    "printer",
    "print",
    "walkprint",
    "mini",
    "phomemo",
    "paperang",
    "peri",
    "gb0",
    "yt0",
    "a35",
)

KNOWN_SERVICES = {
    protocol.ISSC_SERVICE,
    protocol.CAT_SERVICE,
    "0000ff00-0000-1000-8000-00805f9b34fb",
    "000018f0-0000-1000-8000-00805f9b34fb",
}


def _likely(name: str, services: list[str]) -> bool:
    lowered = (name or "").lower()
    if any(token in lowered for token in LIKELY_NAMES):
        return True
    return any(service.lower() in KNOWN_SERVICES for service in services)


class AsyncRuntime:

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="printpal-ble", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 60.0):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise
        except Exception:
            future.cancel()
            raise

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass


RUNTIME = AsyncRuntime()




def _service_map(client) -> dict[str, Any]:
    return {service.uuid.lower(): service for service in client.services}


def pick_profile(client, override: str = "auto") -> dict:
    services = _service_map(client)
    protocol_name = "escpos"
    if override == "cat":
        protocol_name = "cat"
    elif override == "escpos":
        protocol_name = "escpos"
    elif protocol.CAT_SERVICE in services:
        protocol_name = "cat"

    write_uuid = None
    notify_uuid = None

    def writable(service):
        return [
            char
            for char in service.characteristics
            if "write" in char.properties or "write-without-response" in char.properties
        ]

    def notifiable(service):
        return [char for char in service.characteristics if "notify" in char.properties or "indicate" in char.properties]

    if protocol_name == "cat":
        service = services.get(protocol.CAT_SERVICE)
        if service is None:
            raise SessionError("The printer does not expose the AE30 cat-printer service")
        candidates = {char.uuid.lower(): char for char in writable(service)}
        for uuid in (protocol.CAT_WRITE, "0000ae01-0000-1000-8000-00805f9b34fb"):
            if uuid in candidates:
                write_uuid = uuid
                break
        if write_uuid is None and candidates:
            write_uuid = next(iter(candidates))
        notify = {char.uuid.lower(): char for char in notifiable(service)}
        notify_uuid = protocol.CAT_NOTIFY if protocol.CAT_NOTIFY in notify else (next(iter(notify), None))
    else:
        service = services.get(protocol.ISSC_SERVICE)
        candidates: dict[str, Any] = {}
        if service is not None:
            candidates = {char.uuid.lower(): char for char in writable(service)}
            for uuid in (protocol.ISSC_WRITE_FAST, protocol.ISSC_WRITE_RELIABLE, protocol.ISSC_WRITE_ALT):
                if uuid in candidates:
                    write_uuid = uuid
                    break
            notify = {char.uuid.lower(): char for char in notifiable(service)}
            if protocol.ISSC_NOTIFY in notify:
                notify_uuid = protocol.ISSC_NOTIFY
            elif notify:
                notify_uuid = next(iter(notify))
        if write_uuid is None:
            for uuid, candidate_service in services.items():
                if uuid.startswith("00001800") or uuid.startswith("00001801") or uuid.startswith("0000180a"):
                    continue
                options = {char.uuid.lower(): char for char in writable(candidate_service)}
                if options:
                    write_uuid = next(iter(options))
                    notify_options = {char.uuid.lower(): char for char in notifiable(candidate_service)}
                    notify_uuid = next(iter(notify_options), None)
                    break
        if write_uuid is None:
            raise SessionError("No writable BLE characteristic was found on this device")

    profile = {
        "protocol": protocol_name,
        "write_uuid": write_uuid,
        "notify_uuid": notify_uuid,
        "services": [
            {
                "uuid": service.uuid,
                "description": service.description,
                "characteristics": [
                    {
                        "uuid": char.uuid,
                        "properties": list(char.properties),
                        "handle": char.handle,
                    }
                    for char in service.characteristics
                ],
            }
            for service in client.services
        ],
    }
    return profile


def _choose_response(write_uuid: str, properties: list[str], prefer_response: bool) -> tuple[bool, bool]:
    supports_no_response = "write-without-response" in properties
    if prefer_response or not supports_no_response:
        return True, supports_no_response
    return False, supports_no_response




async def _scan_ble(timeout: float) -> list[dict]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices: dict[str, dict] = {}
    for address, (device, advertisement) in found.items():
        name = device.name or advertisement.local_name or ""
        services = list(advertisement.service_uuids or [])
        rssi = advertisement.rssi
        entry = {
            "id": address,
            "address": address,
            "name": name or "(unnamed device)",
            "rssi": rssi,
            "services": services,
            "likely": _likely(name, services),
        }
        previous = devices.get(address)
        if previous is None or (entry["rssi"] or -999) > (previous["rssi"] or -999):
            devices[address] = entry
    ordered = sorted(
        devices.values(),
        key=lambda item: (not item["likely"], -(item["rssi"] or -999), item["name"].lower()),
    )
    return ordered


def scan_ble(timeout: float = 7.0) -> list[dict]:
    try:
        return RUNTIME.run(_scan_ble(timeout), timeout=timeout + 25.0)
    except Exception as exc:
        raise SessionError(f"Bluetooth scan failed: {exc}") from exc


class BleSession:
    kind = "ble"

    def __init__(
        self,
        address: str,
        name: str = "",
        protocol_override: str = "auto",
        prefer_response: bool = False,
        command_delay_ms: int = 250,
        chunk_delay_ms: int = 10,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.address = address
        self.name = name
        self.protocol_override = protocol_override
        self.prefer_response = prefer_response
        self.command_delay = max(0, command_delay_ms) / 1000.0
        self.chunk_delay = max(0, chunk_delay_ms) / 1000.0
        self.log = logger or (lambda message: None)
        self.client = None
        self.profile: dict = {}
        self.connected = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)
        self.mtu = None

    def connect(self) -> dict:
        try:
            self.profile = RUNTIME.run(self._connect(), timeout=75.0)
        except SessionError:
            raise
        except Exception as exc:
            raise SessionError(self._friendly_error(exc)) from exc
        self.connected = True
        self.mtu = getattr(self.client, "mtu_size", None)
        return self.profile

    async def _connect(self) -> dict:
        device = await BleakScanner.find_device_by_address(self.address, timeout=20.0)
        if device is None:
            discovered = await BleakScanner.discover(timeout=8.0, return_adv=True)
            for address, (candidate, _adv) in discovered.items():
                if address.lower() == self.address.lower():
                    device = candidate
                    break
                if self.name and (candidate.name or "").lower() == self.name.lower():
                    device = candidate
                    break
        if device is None:
            raise SessionError(
                "Printer not found over Bluetooth LE. Make sure it is switched on, "
                "in range, and not connected to another device."
            )
        self.client = BleakClient(device, timeout=30.0, disconnected_callback=self._on_disconnect)
        await self.client.connect()
        if not self.client.is_connected:
            raise SessionError("Bluetooth connection was refused by the printer")
        await asyncio.sleep(0.35)
        profile = pick_profile(self.client, self.protocol_override)
        self.profile = profile
        self._queue = asyncio.Queue(maxsize=512)
        self._write_props = []
        for service in self.client.services:
            for char in service.characteristics:
                if char.uuid.lower() == profile["write_uuid"]:
                    self._write_props = list(char.properties)
                    break
        if profile.get("notify_uuid"):
            try:
                await self.client.start_notify(profile["notify_uuid"], self._on_notify)
            except Exception as exc:
                self.log(f"Status notifications unavailable: {exc}")
                profile["notify_uuid"] = None
        return profile

    def _on_disconnect(self, _client) -> None:
        self.connected = False

    def _on_notify(self, _sender, data: bytearray) -> None:
        try:
            self._queue.put_nowait(bytes(data))
        except asyncio.QueueFull:
            pass

    def _friendly_error(self, exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "not found" in lowered or "0x800710df" in lowered:
            return (
                "Could not reach the printer over BLE. Switch it on and make sure the "
                "WalkPrint app on your phone is disconnected."
            )
        if "access is denied" in lowered or "0x80070005" in lowered:
            return "Windows denied access to the Bluetooth device. Remove it in Settings and try again."
        if "not supported" in lowered or "0x80070032" in lowered:
            return "This Bluetooth adapter cannot connect to the printer over BLE."
        return f"Bluetooth connection failed: {text or type(exc).__name__}"

    def close(self) -> None:
        self.connected = False
        if self.client is not None:
            try:
                RUNTIME.run(self.client.disconnect(), timeout=10.0)
            except Exception:
                pass
            self.client = None

    def _response(self, reliable: bool) -> bool:
        if self.prefer_response:
            return True
        return bool(reliable or "write-without-response" not in getattr(self, "_write_props", []))

    def write(self, data: bytes, reliable: bool = False) -> None:
        if self.client is None or not self.client.is_connected:
            raise SessionError("Bluetooth printer is no longer connected")
        response = self._response(reliable)
        try:
            RUNTIME.run(
                self.client.write_gatt_char(self.profile["write_uuid"], data, response=response),
                timeout=20.0,
            )
        except Exception as exc:
            raise SessionError(f"BLE write failed: {exc}") from exc

    def query(self, data: bytes, timeout: float = 0.8) -> bytes:
        if self.profile.get("notify_uuid") is None:
            return b""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.write(data, reliable=True)
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                chunk = RUNTIME.run(self._queue.get(), timeout=remaining)
            except Exception:
                break
            chunks.append(chunk)
            if b"\x00" in chunk:
                break
        return b"".join(chunks)

    def status(self) -> dict:
        info: dict = {"protocol": self.profile.get("protocol", "escpos")}
        if self.profile.get("protocol") == "cat":
            return info
        info["status"] = protocol.parse_status(self.query(protocol.ESC_STATUS, 1.0))
        time.sleep(0.08)
        raw_serial = self.query(protocol.ESC_SERIAL, 0.9)
        if raw_serial:
            info["serial"] = protocol.parse_serial(raw_serial)
        time.sleep(0.08)
        raw_model = self.query(protocol.ESC_MODEL, 0.9)
        if raw_model:
            info["model"] = protocol.parse_model(raw_model)
        return info

    def feed(self, lines: int = 4) -> None:
        if self.profile.get("protocol") == "cat":
            self.write(protocol.cat_feed(max(1, lines)), reliable=True)
        else:
            self.write(protocol.escpos_feed(lines), reliable=True)




def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for info in list_ports.comports():
        hwid = info.hwid or ""
        description = info.description or ""
        is_bluetooth = "BTHENUM" in hwid.upper() or "bluetooth" in description.lower() or "rfcomm" in hwid.lower()
        ports.append(
            {
                "port": info.device,
                "description": description or "Serial port",
                "manufacturer": info.manufacturer or "",
                "hwid": hwid,
                "bluetooth": is_bluetooth,
            }
        )
    ports.sort(key=lambda item: (not item["bluetooth"], item["port"]))
    return ports


class SerialSession:
    kind = "serial"

    def __init__(
        self,
        port: str,
        baud: int = 115200,
        protocol_override: str = "auto",
        command_delay_ms: int = 120,
        chunk_delay_ms: int = 3,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.port_name = port
        self.baud = int(baud or 115200)
        self.protocol_override = protocol_override
        self.command_delay = max(0, command_delay_ms) / 1000.0
        self.chunk_delay = max(0, chunk_delay_ms) / 1000.0
        self.log = logger or (lambda message: None)
        self.port = None
        self.connected = False
        self.profile = {
            "protocol": "cat" if protocol_override == "cat" else "escpos",
            "write_uuid": None,
            "notify_uuid": None,
            "services": [],
            "port": port,
        }
        self.mtu = None

    def connect(self) -> dict:
        try:
            import serial
        except Exception as exc:
            raise SessionError("pyserial is not installed") from exc
        try:
            self.port = serial.Serial(
                port=self.port_name,
                baudrate=self.baud,
                timeout=0.08,
                write_timeout=4.0,
                rtscts=False,
                dsrdtr=False,
            )
            self.port.reset_input_buffer()
        except Exception as exc:
            raise SessionError(
                f"Could not open {self.port_name}. Pair the printer in Windows Bluetooth "
                f"settings first, then pick its outgoing COM port. ({exc})"
            ) from exc
        self.connected = True
        return self.profile

    def close(self) -> None:
        self.connected = False
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
            self.port = None

    def write(self, data: bytes, reliable: bool = False) -> None:
        if self.port is None:
            raise SessionError("Serial printer is no longer connected")
        try:
            self.port.write(data)
            self.port.flush()
        except Exception as exc:
            raise SessionError(f"Serial write failed: {exc}") from exc

    def query(self, data: bytes, timeout: float = 0.8) -> bytes:
        if self.port is None:
            return b""
        try:
            self.port.reset_input_buffer()
            self.port.write(data)
            self.port.flush()
            deadline = time.monotonic() + timeout
            chunks: list[bytes] = []
            while time.monotonic() < deadline:
                chunk = self.port.read(64)
                if chunk:
                    chunks.append(chunk)
                    if b"\x00" in chunk:
                        break
                else:
                    time.sleep(0.02)
            return b"".join(chunks)
        except Exception:
            return b""

    def status(self) -> dict:
        info: dict = {"protocol": self.profile.get("protocol", "escpos")}
        if self.profile.get("protocol") == "cat":
            return info
        info["status"] = protocol.parse_status(self.query(protocol.ESC_STATUS, 0.9))
        time.sleep(0.05)
        raw_serial = self.query(protocol.ESC_SERIAL, 0.8)
        if raw_serial:
            info["serial"] = protocol.parse_serial(raw_serial)
        time.sleep(0.05)
        raw_model = self.query(protocol.ESC_MODEL, 0.8)
        if raw_model:
            info["model"] = protocol.parse_model(raw_model)
        return info

    def feed(self, lines: int = 4) -> None:
        if self.profile.get("protocol") == "cat":
            self.write(protocol.cat_feed(max(1, lines)), reliable=True)
        else:
            self.write(protocol.escpos_feed(lines), reliable=True)


from bleak import BleakClient, BleakScanner
