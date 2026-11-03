from __future__ import annotations

import re
import struct
from typing import Iterable, Iterator

from PIL import Image


ESC_INIT = b"\x1b\x40"
ESC_START = b"\x1d\x49\xf0\x19"
ESC_STATUS = b"\x1e\x47\x03"
ESC_SERIAL = b"\x1d\x67\x39"
ESC_MODEL = b"\x1d\x67\x69"

ISSC_SERVICE = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
ISSC_WRITE_RELIABLE = "49535343-6daa-4d02-abf6-19569aca69fe"
ISSC_WRITE_FAST = "49535343-8841-43f4-a8d4-ecbe34729bb3"
ISSC_WRITE_ALT = "49535343-aca3-481c-91ec-d85e28a60318"
ISSC_NOTIFY = "49535343-1e4d-4bd9-ba61-23c647249616"

CAT_SERVICE = "0000ae30-0000-1000-8000-00805f9b34fb"
CAT_WRITE = "0000ae01-0000-1000-8000-00805f9b34fb"
CAT_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"


def escpos_raster_header(width_bytes: int, height: int) -> bytes:
    return struct.pack("<BBBBHH", 0x1D, 0x76, 0x30, 0x00, width_bytes, height)


def _burn_bytes(image: Image.Image) -> bytes:
    if image.mode != "1":
        image = image.convert("1")
    inverted = image.point(lambda value: 255 - value)
    return inverted.tobytes()


def escpos_chunks(image: Image.Image, rows_per_chunk: int = 1) -> Iterator[tuple[bytes, int]]:
    rows_per_chunk = max(1, int(rows_per_chunk))
    width_bytes = image.width // 8
    data = _burn_bytes(image)
    for top in range(0, image.height, rows_per_chunk):
        count = min(rows_per_chunk, image.height - top)
        payload = data[top * width_bytes:(top + count) * width_bytes]
        yield escpos_raster_header(width_bytes, count) + payload, count


def escpos_feed(lines: int) -> bytes:
    return b"\x0a" * max(1, min(32, int(lines)))


def escpos_whole_bitmap(image: Image.Image) -> bytes:
    width_bytes = image.width // 8
    return escpos_raster_header(width_bytes, image.height) + _burn_bytes(image)



CRC8_TABLE = [
    0x00, 0x07, 0x0e, 0x09, 0x1c, 0x1b, 0x12, 0x15, 0x38, 0x3f, 0x36, 0x31,
    0x24, 0x23, 0x2a, 0x2d, 0x70, 0x77, 0x7e, 0x79, 0x6c, 0x6b, 0x62, 0x65,
    0x48, 0x4f, 0x46, 0x41, 0x54, 0x53, 0x5a, 0x5d, 0xe0, 0xe7, 0xee, 0xe9,
    0xfc, 0xfb, 0xf2, 0xf5, 0xd8, 0xdf, 0xd6, 0xd1, 0xc4, 0xc3, 0xca, 0xcd,
    0x90, 0x97, 0x9e, 0x99, 0x8c, 0x8b, 0x82, 0x85, 0xa8, 0xaf, 0xa6, 0xa1,
    0xb4, 0xb3, 0xba, 0xbd, 0xc7, 0xc0, 0xc9, 0xce, 0xdb, 0xdc, 0xd5, 0xd2,
    0xff, 0xf8, 0xf1, 0xf6, 0xe3, 0xe4, 0xed, 0xea, 0xb7, 0xb0, 0xb9, 0xbe,
    0xab, 0xac, 0xa5, 0xa2, 0x8f, 0x88, 0x81, 0x86, 0x93, 0x94, 0x9d, 0x9a,
    0x27, 0x20, 0x29, 0x2e, 0x3b, 0x3c, 0x35, 0x32, 0x1f, 0x18, 0x11, 0x16,
    0x03, 0x04, 0x0d, 0x0a, 0x57, 0x50, 0x59, 0x5e, 0x4b, 0x4c, 0x45, 0x42,
    0x6f, 0x68, 0x61, 0x66, 0x73, 0x74, 0x7d, 0x7a, 0x89, 0x8e, 0x87, 0x80,
    0x95, 0x92, 0x9b, 0x9c, 0xb1, 0xb6, 0xbf, 0xb8, 0xad, 0xaa, 0xa3, 0xa4,
    0xf9, 0xfe, 0xf7, 0xf0, 0xe5, 0xe2, 0xeb, 0xec, 0xc1, 0xc6, 0xcf, 0xc8,
    0xdd, 0xda, 0xd3, 0xd4, 0x69, 0x6e, 0x67, 0x60, 0x75, 0x72, 0x7b, 0x7c,
    0x51, 0x56, 0x5f, 0x58, 0x4d, 0x4a, 0x43, 0x44, 0x19, 0x1e, 0x17, 0x10,
    0x05, 0x02, 0x0b, 0x0c, 0x21, 0x26, 0x2f, 0x28, 0x3d, 0x3a, 0x33, 0x34,
    0x4e, 0x49, 0x40, 0x47, 0x52, 0x55, 0x5c, 0x5b, 0x76, 0x71, 0x78, 0x7f,
    0x6a, 0x6d, 0x64, 0x63, 0x3e, 0x39, 0x30, 0x37, 0x22, 0x25, 0x2c, 0x2b,
    0x06, 0x01, 0x08, 0x0f, 0x1a, 0x1d, 0x14, 0x13, 0xae, 0xa9, 0xa0, 0xa7,
    0xb2, 0xb5, 0xbc, 0xbb, 0x96, 0x91, 0x98, 0x9f, 0x8a, 0x8d, 0x84, 0x83,
    0xde, 0xd9, 0xd0, 0xd7, 0xc2, 0xc5, 0xcc, 0xcb, 0xe6, 0xe1, 0xe8, 0xef,
    0xfa, 0xfd, 0xf4, 0xf3,
]

CAT_RETRACT = 0xA0
CAT_FEED = 0xA1
CAT_DRAW_BITMAP = 0xA2
CAT_SET_MODE = 0xBE
CAT_SET_ENERGY = 0xAF
CAT_SET_QUALITY = 0xA4


def crc8(data: Iterable[int]) -> int:
    value = 0
    for byte in data:
        value = CRC8_TABLE[(value ^ byte) & 0xFF]
    return value & 0xFF


def cat_message(command: int, payload: bytes | bytearray | list[int]) -> bytes:
    payload = bytes(payload)
    if len(payload) > 255:
        raise ValueError("Cat-printer command payload is limited to 255 bytes")
    body = bytes([0x51, 0x78, command, 0x00, len(payload), 0x00]) + payload
    return body + bytes([crc8(payload), 0xFF])


def cat_settings(energy: int = 0x1000, quality: int = 5) -> list[bytes]:
    energy = max(0, min(0xFFFF, int(energy)))
    return [
        cat_message(CAT_SET_ENERGY, [(energy >> 8) & 0xFF, energy & 0xFF]),
        cat_message(CAT_SET_QUALITY, [max(1, min(5, int(quality)))]),
        cat_message(CAT_SET_MODE, [0x00]),
    ]


def cat_chunks(image: Image.Image, lines_per_chunk: int = 1) -> Iterator[tuple[bytes, int]]:
    lines_per_chunk = max(1, int(lines_per_chunk))
    width_bytes = image.width // 8
    data = _burn_bytes(image)
    for top in range(0, image.height, lines_per_chunk):
        count = min(lines_per_chunk, image.height - top)
        for row in range(top, top + count):
            yield cat_message(CAT_DRAW_BITMAP, data[row * width_bytes:(row + 1) * width_bytes]), 1


def cat_feed(lines: int = 1) -> bytes:
    lines = max(1, int(lines))
    return cat_message(CAT_FEED, [(lines >> 8) & 0xFF, lines & 0xFF])


def cat_set_energy(energy: int) -> bytes:
    energy = max(0, min(0xFFFF, int(energy)))
    return cat_message(CAT_SET_ENERGY, [(energy >> 8) & 0xFF, energy & 0xFF])


def cat_set_quality(quality: int) -> bytes:
    return cat_message(CAT_SET_QUALITY, [max(1, min(5, int(quality)))])




def decode_ascii(raw: bytes) -> str:
    return raw.decode("ascii", "ignore").replace("\x00", "").strip()


def parse_status(raw: bytes) -> dict:
    text = decode_ascii(raw)
    info: dict = {"raw": text}
    for part in text.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        info[key.strip().upper()] = value.strip()
    voltage = info.get("VOLT", "")
    match = re.search(r"(\d+)", str(voltage))
    if match:
        info["voltage_mv"] = int(match.group(1))
        info["battery"] = battery_percent(info["voltage_mv"])
    dpi = re.search(r"(\d+)", str(info.get("DPI", "")))
    if dpi:
        info["dots"] = int(dpi.group(1))
    return info


def parse_serial(raw: bytes) -> str | None:
    text = decode_ascii(raw)
    match = re.search(r"sn:\s*([A-Za-z0-9._-]+)", text, re.I)
    value = match.group(1) if match else text
    return value.rstrip(".") or None


def parse_model(raw: bytes) -> str | None:
    text = decode_ascii(raw)
    match = re.search(r"public\s+id:\s*([A-Za-z0-9._-]+)", text, re.I)
    value = match.group(1) if match else text
    return value.rstrip(".") or None


def battery_percent(voltage_mv: int) -> int:
    low, high = 6400.0, 8400.0
    percent = (float(voltage_mv) - low) / (high - low) * 100.0
    return max(0, min(100, int(round(percent))))
