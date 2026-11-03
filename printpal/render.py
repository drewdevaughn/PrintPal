from __future__ import annotations

import base64
import binascii
import io
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from .fonts import FontLibrary
from .util import clamp, clamp_int, images_dir, read_json, write_json

try:
    from barcode import Code128 as _Code128
except Exception:
    _Code128 = None

DOTS_PER_MM = 8.0


@dataclass
class RenderResult:
    image: Image.Image
    gray: Image.Image
    width: int
    height: int
    coverage: float
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def mm(self) -> float:
        return self.height / DOTS_PER_MM


@dataclass
class _Prepared:
    kind: str
    height: int
    draw: Callable[[Image.Image, int], None]
    bbox: tuple[int, int, int, int] | None = None
    extra: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    barcode: dict | None = None


def _load_image_meta(index: dict, image_id: str) -> dict | None:
    return index.get(image_id)


class ImageStore:

    def __init__(self) -> None:
        self.directory = images_dir()
        self.index_path = self.directory / "index.json"
        self._index: dict[str, dict] = read_json(self.index_path, {}) or {}

    def reload(self) -> None:
        self._index = read_json(self.index_path, {}) or {}

    def _save_index(self) -> None:
        write_json(self.index_path, self._index)

    def add_bytes(self, raw: bytes, filename: str = "image") -> dict:
        source = Image.open(io.BytesIO(raw))
        source = ImageOps.exif_transpose(source)
        if source.mode in ("RGBA", "LA", "P"):
            source = source.convert("RGBA")
            background = Image.new("RGBA", source.size, (255, 255, 255, 255))
            source = Image.alpha_composite(background, source)
        source = source.convert("RGB")
        image_id = uuid.uuid4().hex[:16]
        path = self.directory / f"{image_id}.png"
        source.save(path, "PNG")
        meta = {
            "id": image_id,
            "name": (filename or "image")[:120],
            "width": source.width,
            "height": source.height,
            "path": str(path),
        }
        self._index[image_id] = meta
        self._save_index()
        return meta

    def add_data_url(self, data_url: str, filename: str = "image") -> dict:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        try:
            raw = base64.b64decode(data_url, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid image data") from exc
        return self.add_bytes(raw, filename)

    def get(self, image_id: str) -> Image.Image | None:
        meta = self._index.get(image_id)
        if not meta:
            return None
        path = Path(meta.get("path", ""))
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except OSError:
            return None

    def meta(self, image_id: str) -> dict | None:
        return self._index.get(image_id)

    def path(self, image_id: str) -> Path | None:
        meta = self._index.get(image_id)
        if not meta:
            return None
        path = Path(meta.get("path", ""))
        return path if path.exists() else None



BAYER_4 = np.array(
    [
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5],
    ],
    dtype=np.float32,
)

BAYER_8 = np.array(
    [
        [0, 32, 8, 40, 2, 34, 10, 42],
        [48, 16, 56, 24, 50, 18, 58, 26],
        [12, 44, 4, 36, 14, 46, 6, 38],
        [60, 28, 52, 20, 62, 30, 54, 22],
        [3, 35, 11, 43, 1, 33, 9, 41],
        [51, 19, 59, 27, 49, 17, 57, 25],
        [15, 47, 7, 39, 13, 45, 5, 37],
        [63, 31, 55, 23, 61, 29, 53, 21],
    ],
    dtype=np.float32,
)


def threshold_image(gray: Image.Image, threshold: int) -> Image.Image:
    cut = clamp_int(threshold, 1, 254, 160)
    return gray.point(lambda value: 0 if value < cut else 255).convert("1")


def bayer_image(gray: Image.Image, matrix: np.ndarray) -> Image.Image:
    data = np.asarray(gray, dtype=np.float32)
    height, width = data.shape
    size = matrix.shape[0]
    tile = np.tile(matrix, (math.ceil(height / size), math.ceil(width / size)))[:height, :width]
    levels = ((tile + 0.5) / (size * size) * 255.0).astype(np.float32)
    out = np.where(data < levels, 0, 255).astype(np.uint8)
    return Image.fromarray(out, mode="L").convert("1")


def atkinson_image(gray: Image.Image) -> Image.Image:
    data = np.asarray(gray, dtype=np.float32).copy()
    height, width = data.shape
    out = np.empty((height, width), dtype=bool)
    for y in range(height):
        row = data[y]
        next_row = data[y + 1] if y + 1 < height else None
        next_next = data[y + 2] if y + 2 < height else None
        for x in range(width):
            old = row[x]
            new = 0.0 if old < 128.0 else 255.0
            out[y, x] = new == 0.0
            error = (old - new) / 8.0
            if error == 0.0:
                continue
            if x + 1 < width:
                row[x + 1] += error
            if x + 2 < width:
                row[x + 2] += error
            if next_row is not None:
                if x > 0:
                    next_row[x - 1] += error
                next_row[x] += error
                if x + 1 < width:
                    next_row[x + 1] += error
            if next_next is not None:
                next_next[x] += error
    return Image.fromarray(np.where(out, 0, 255).astype(np.uint8), mode="L").convert("1")


def dither_image(gray: Image.Image, mode: str, threshold: int) -> Image.Image:
    mode = (mode or "auto").lower()
    if mode in ("floyd", "floyd-steinberg", "floydsteinberg", "auto"):
        return gray.convert("1")
    if mode == "atkinson":
        return atkinson_image(gray)
    if mode == "bayer4":
        return bayer_image(gray, BAYER_4)
    if mode == "bayer8":
        return bayer_image(gray, BAYER_8)
    return threshold_image(gray, threshold)




def _font_measure(font, text: str, letter_spacing: float) -> float:
    if not text:
        return 0.0
    return float(font.getlength(text)) + letter_spacing * max(0, len(text) - 1)


def _split_long_token(token: str, font, max_width: float, letter_spacing: float) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in token:
        candidate = current + char
        if not current or _font_measure(font, candidate, letter_spacing) <= max_width:
            current = candidate
        else:
            chunks.append(current)
            current = char
    if current:
        chunks.append(current)
    return chunks


def wrap_text(text: str, font, max_width: float, letter_spacing: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if paragraph == "":
            lines.append("")
            continue
        tokens = re.findall(r"\s+|\S+", paragraph)
        current = ""
        for token in tokens:
            if not current and token.isspace():
                current = token
                continue
            candidate = current + token
            if _font_measure(font, candidate, letter_spacing) <= max_width:
                current = candidate
                continue
            if current.strip():
                lines.append(current.rstrip())
                current = "" if token.isspace() else token
                if token.isspace():
                    continue
            if token.isspace():
                continue
            if _font_measure(font, token, letter_spacing) <= max_width:
                current = token
            else:
                pieces = _split_long_token(token, font, max_width, letter_spacing)
                lines.extend(pieces[:-1])
                current = pieces[-1]
        lines.append(current.rstrip())
    return lines




def _block_spacing(block: dict, scale: int) -> tuple[int, int]:
    before = clamp_int(block.get("spaceBefore", 0), 0, 400, 0) * scale
    after = clamp_int(block.get("spaceAfter", 0), 0, 400, 0) * scale
    return before, after


def _prepare_text(block: dict, fonts: FontLibrary, content_w: int, scale: int, margin: int) -> _Prepared | None:
    text = str(block.get("text", ""))
    if block.get("uppercase"):
        text = text.upper()
    if not text.strip():
        return None
    warnings: list[str] = []
    family = block.get("font")
    bold = bool(block.get("bold"))
    italic = bool(block.get("italic"))
    letter_spacing = clamp(block.get("letterSpacing", 0), -2, 20, 0) * scale
    line_spacing = clamp(block.get("lineSpacing", 1.18), 0.7, 3.0, 1.18)
    max_size = clamp_int(block.get("size", 17), 6, 160, 17) * scale
    size = max_size

    if block.get("fitWidth"):
        text = re.sub(r"\s+", " ", text).strip()
        min_size = min(max_size, clamp_int(block.get("minSize", 10), 6, 160, 10) * scale)
        fitted: int | None = None
        low, high = min_size, max_size
        while low <= high:
            probe_size = (low + high) // 2
            probe = fonts.load(family, bold, italic, probe_size)
            if _font_measure(probe, text, letter_spacing) <= content_w:
                fitted = probe_size
                low = probe_size + 1
            else:
                high = probe_size - 1
        if fitted is None:
            fitted = min_size
            snippet = text if len(text) <= 28 else text[:27] + "…"
            warnings.append(f'“{snippet}” is too long for one line at {max(6, min_size // scale)} dots — it will wrap.')
        size = fitted

    font = fonts.load(family, bold, italic, size)
    ascent, descent = font.getmetrics()
    line_height = max(1, int(round((ascent + descent) * line_spacing)))
    lines = wrap_text(text, font, float(content_w), float(letter_spacing))
    before, after = _block_spacing(block, scale)
    height = len(lines) * line_height + before + after
    align = str(block.get("align", "left")).lower()
    if align not in ("left", "center", "right"):
        align = "left"

    def draw(canvas: Image.Image, top: int) -> None:
        painter = ImageDraw.Draw(canvas)
        y = top + before
        for line in lines:
            if line:
                line_width = _font_measure(font, line, letter_spacing)
                if align == "center":
                    x = margin * scale + (content_w - line_width) / 2.0
                elif align == "right":
                    x = margin * scale + (content_w - line_width)
                else:
                    x = float(margin * scale)
                if abs(letter_spacing) > 0.01:
                    cursor = x
                    for char in line:
                        painter.text((cursor, y), char, font=font, fill=0, anchor="la")
                        cursor += float(font.getlength(char)) + letter_spacing
                else:
                    painter.text((x, y), line, font=font, fill=0, anchor="la")
            y += line_height

    return _Prepared(kind="text", height=height, draw=draw, warnings=warnings)


def _prepare_divider(block: dict, content_w: int, scale: int, margin: int) -> _Prepared:
    thickness = clamp_int(block.get("thickness", 2), 1, 16, 2) * scale
    before, after = _block_spacing(block, scale)
    style = str(block.get("style", "solid")).lower()
    height = thickness * (2 if style == "double" else 1) + before + after
    start = margin * scale
    end = (margin * scale) + content_w

    def draw(canvas: Image.Image, top: int) -> None:
        painter = ImageDraw.Draw(canvas)
        y = top + before
        if style == "dashed":
            step, dash = 7 * scale, 5 * scale
            x = start
            while x < end:
                painter.rectangle([x, y, min(end, x + dash), y + thickness - 1], fill=0)
                x += step
        elif style == "dotted":
            step, dot = 4 * scale, 2 * scale
            x = start
            while x < end:
                painter.rectangle([x, y, min(end, x + dot), y + thickness - 1], fill=0)
                x += step
        elif style == "double":
            painter.rectangle([start, y, end, y + max(1, thickness // 2) - 1], fill=0)
            y2 = y + thickness + max(1, scale)
            painter.rectangle([start, y2, end, y2 + max(1, thickness // 2) - 1], fill=0)
        else:
            painter.rectangle([start, y, end, y + thickness - 1], fill=0)

    return _Prepared(kind="divider", height=height, draw=draw)


def _prepare_spacer(block: dict, scale: int) -> _Prepared:
    height = clamp_int(block.get("height", 16), 1, 800, 16) * scale
    return _Prepared(kind="spacer", height=height, draw=lambda canvas, top: None)


def _prepare_pattern(
    block: dict,
    content_w: int,
    scale: int,
    margin: int,
) -> _Prepared:
    pattern = str(block.get("pattern", "lines")).lower()
    height = clamp_int(block.get("height", 40), 12, 400, 40) * scale
    start = margin * scale
    right = start + content_w

    def draw(canvas: Image.Image, top: int) -> None:
        painter = ImageDraw.Draw(canvas)
        band = max(1, height // 4)
        for index in range(1, 5):
            y0 = top + (index - 1) * band + 4 * scale
            y1 = top + index * band - 4 * scale
            if y1 <= y0:
                continue
            step = index * 2 * scale
            if pattern == "dots":
                yy = y0
                while yy < y1:
                    xx = start
                    while xx < right:
                        painter.rectangle(
                            [
                                xx,
                                yy,
                                min(xx + index * scale - 1, right - 1),
                                min(yy + index * scale - 1, y1 - 1),
                            ],
                            fill=0,
                        )
                        xx += step
                    yy += step
            else:
                xx = start
                while xx < right:
                    painter.rectangle(
                        [xx, y0, min(xx + index * scale - 1, right - 1), y1],
                        fill=0,
                    )
                    xx += step

    return _Prepared(kind="pattern", height=height, draw=draw)


def _prepare_image(
    block: dict,
    images: ImageStore,
    content_w: int,
    scale: int,
    margin: int,
) -> _Prepared | None:
    meta = images.meta(str(block.get("imageId", "")))
    source = images.get(str(block.get("imageId", "")))
    if source is None:
        return None

    rotate = clamp_int(block.get("rotate", 0), 0, 270, 0)
    rotate = (rotate // 90) * 90 % 360
    if rotate:
        source = source.rotate(-rotate, expand=True)

    align = str(block.get("align", "center")).lower()
    if align not in ("left", "center", "right"):
        align = "center"
    fit = str(block.get("fit", "width")).lower()
    max_height = block.get("maxHeight")
    limit_h = (clamp_int(max_height, 16, 4000, 600) if max_height else 600) * scale

    source_w, source_h = source.size
    if fit == "actual":
        target_w, target_h = source_w, source_h
        if target_w > content_w:
            ratio = content_w / target_w
            target_w, target_h = int(target_w * ratio), int(target_h * ratio)
    elif fit == "contain":
        ratio = min(content_w / source_w, limit_h / source_h)
        target_w, target_h = max(1, int(source_w * ratio)), max(1, int(source_h * ratio))
    elif fit == "cover":
        target_w = content_w
        target_h = limit_h
        ratio = max(target_w / source_w, target_h / source_h)
        scaled_w, scaled_h = max(1, int(source_w * ratio)), max(1, int(source_h * ratio))
        source = source.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        left = max(0, (scaled_w - target_w) // 2)
        top_crop = max(0, (scaled_h - target_h) // 2)
        source = source.crop((left, top_crop, left + target_w, top_crop + target_h))
    else:
        target_w = content_w
        target_h = max(1, int(round(source_h * (target_w / source_w))))

    if (target_w, target_h) != source.size:
        source = source.resize((target_w, target_h), Image.Resampling.LANCZOS)
    prepared = source.convert("L")

    before, after = _block_spacing(block, scale)
    height = target_h + before + after
    border = bool(block.get("border"))

    start = margin * scale
    if align == "center":
        x = start + (content_w - target_w) // 2
    elif align == "right":
        x = start + (content_w - target_w)
    else:
        x = start

    bbox = (
        int(x / scale),
        int((before) / scale),
        int(math.ceil((x + target_w) / scale)),
        int(math.ceil((before + target_h) / scale)),
    )

    def draw(canvas: Image.Image, top: int) -> None:
        canvas.paste(prepared, (x, top + before))
        if border:
            painter = ImageDraw.Draw(canvas)
            painter.rectangle(
                [x - max(1, scale // 2), top + before - max(1, scale // 2),
                 x + target_w, top + before + target_h],
                outline=0,
                width=max(1, scale // 2),
            )

    return _Prepared(kind="image", height=height, draw=draw, bbox=bbox, extra={"meta": meta})


def _prepare_qr(block: dict, content_w: int, scale: int, margin: int) -> _Prepared | None:
    data = str(block.get("data", "")).strip()
    if not data:
        return None
    level = str(block.get("level", "M")).upper()
    correction = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }.get(level, qrcode.constants.ERROR_CORRECT_M)
    qr = qrcode.QRCode(error_correction=correction, box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)
    module_img = Image.new("L", (modules, modules), 255)
    painter = ImageDraw.Draw(module_img)
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                painter.point((x, y), fill=0)
    target = clamp_int(block.get("size", 180), 48, 800, 180) * scale
    target = min(target, content_w, 1200 * scale)
    target = max(modules * scale, target)
    module_img = module_img.resize((target, target), Image.Resampling.NEAREST)

    before, after = _block_spacing(block, scale)
    height = target + before + after
    align = str(block.get("align", "center")).lower()
    start = margin * scale
    if align == "center":
        x = start + (content_w - target) // 2
    elif align == "right":
        x = start + (content_w - target)
    else:
        x = start
    bbox = (int(x / scale), int(before / scale), int(math.ceil((x + target) / scale)),
            int(math.ceil((before + target) / scale)))

    def draw(canvas: Image.Image, top: int) -> None:
        canvas.paste(module_img, (x, top + before))

    return _Prepared(kind="qr", height=height, draw=draw, bbox=bbox)


def encode_barcode(text: str) -> str:
    if _Code128 is None:
        raise RuntimeError("Barcode support is not installed")
    payload = text.encode("ascii", "ignore").decode("ascii").strip()
    if not payload:
        raise ValueError("Barcode text is empty")
    if len(payload) > 64:
        payload = payload[:64]
    modules = "".join(_Code128(payload).build())
    if not modules or set(modules) - {"0", "1"}:
        raise ValueError("Barcode could not be generated")
    return modules


def compact_barcode_text(text: str, limit: int = 12) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()[:limit]


def _prepare_shelf(
    block: dict,
    fonts: FontLibrary,
    content_w: int,
    scale: int,
    margin: int,
) -> _Prepared | None:
    title = str(block.get("title", "")).strip()
    footnote = str(block.get("footnote", "")).strip()
    if block.get("uppercaseFootnote", True):
        footnote = footnote.upper()
    barcode_enabled = bool(block.get("barcode"))
    barcode_payload = str(block.get("barcodeData", "")).strip()
    if barcode_enabled and not barcode_payload:
        barcode_payload = compact_barcode_text(footnote) or compact_barcode_text(title)
    if not title and not footnote and not (barcode_enabled and barcode_payload):
        return None

    warnings: list[str] = []
    family = block.get("font") or "Bahnschrift"
    bold = bool(block.get("bold", True))
    letter_spacing = clamp(block.get("letterSpacing", 0), -2, 20, 0) * scale
    line_spacing = clamp(block.get("lineSpacing", 1.02), 0.8, 2.0, 1.02)
    max_lines = clamp_int(block.get("maxLines", 2), 1, 4, 2)
    min_size = clamp_int(block.get("minSize", 18), 8, 160, 18) * scale
    max_size = clamp_int(block.get("maxSize", 64), 8, 200, 64) * scale
    max_size = max(max_size, min_size)

    chosen_size = min_size
    title_font = fonts.load(family, bold, False, min_size)
    title_lines: list[str] = []
    if title:
        def fit(max_line_count: int) -> tuple[int, list[str]] | None:
            found: tuple[int, list[str]] | None = None
            low, high = min_size, max_size
            while low <= high:
                probe_size = (low + high) // 2
                probe_font = fonts.load(family, bold, False, probe_size)
                probe_lines = wrap_text(title, probe_font, float(content_w), float(letter_spacing))
                if len(probe_lines) <= max_line_count:
                    found = (probe_size, probe_lines)
                    low = probe_size + 1
                else:
                    high = probe_size - 1
            return found

        single = fit(1)
        wrapped = fit(max_lines) if max_lines > 1 else None
        prefer_single_from = max(min_size, int(round(max_size * 0.5)))
        if single is not None and (wrapped is None or single[0] >= prefer_single_from):
            chosen_size, title_lines = single
        elif wrapped is not None:
            chosen_size, title_lines = wrapped
        else:
            chosen_size = min_size
            title_font = fonts.load(family, bold, False, chosen_size)
            title_lines = wrap_text(title, title_font, float(content_w), float(letter_spacing))
            warnings.append(
                f"Title needs {len(title_lines)} lines even at the smallest size — shorten it or allow more lines."
            )
        title_font = fonts.load(family, bold, False, chosen_size)
        if len(title_lines) > 1:
            warnings.append(
                f"Title wraps to {len(title_lines)} lines — the tag prints taller than a single-line label."
            )
            if chosen_size < max_size * 0.35:
                warnings.append("Title is long, so the letters print small. Shorten it for a bolder tag.")

    ascent, descent = title_font.getmetrics()
    title_line_height = max(1, int(round((ascent + descent) * line_spacing)))
    title_height = len(title_lines) * title_line_height

    footnote_size = clamp_int(block.get("footnoteSize", 22), 8, 60, 22) * scale
    footnote_spacing = clamp(block.get("footnoteSpacing", 0.6), -2, 20, 0.6) * scale
    footnote_font = fonts.load(family, True, False, footnote_size)
    footnote_lines = (
        wrap_text(footnote, footnote_font, float(content_w), float(footnote_spacing)) if footnote else []
    )
    if len(footnote_lines) > 2:
        warnings.append(f"Footnote wraps to {len(footnote_lines)} lines.")
    if footnote_lines and footnote_size // scale < 16:
        warnings.append(
            f"Footnote is only {footnote_size // scale} px tall — raise the footnote size for readability."
        )
    foot_ascent, foot_descent = footnote_font.getmetrics()
    footnote_line_height = max(1, int(round((foot_ascent + foot_descent) * 1.12)))
    footnote_height = len(footnote_lines) * footnote_line_height
    footnote_gap = 3 * scale if footnote_lines else 0

    barcode_height = clamp_int(block.get("barcodeHeight", 32), 4, 64, 32) * scale
    barcode_gap = 4 * scale
    barcode_width = 0
    barcode_modules = ""
    barcode_module_px = 0
    if barcode_enabled:
        if not barcode_payload:
            warnings.append("Barcode skipped: enter barcode data or a footnote.")
        else:
            try:
                barcode_modules = encode_barcode(barcode_payload)
            except (ValueError, RuntimeError) as exc:
                warnings.append(f"Barcode skipped: {exc}.")
                barcode_modules = ""
            if barcode_modules:
                final_content = max(1, content_w // scale)
                barcode_module_px = min(6, final_content // (len(barcode_modules) + 20))
                if barcode_module_px < 2:
                    warnings.append(
                        "Barcode is too long for the tag width — use 12 characters or fewer."
                    )
                    barcode_modules = ""
                    barcode_module_px = 0
                else:
                    barcode_width = barcode_module_px * len(barcode_modules)

    before, after = _block_spacing(block, scale)
    height = (
        title_height
        + footnote_gap
        + footnote_height
        + (barcode_gap + barcode_height if barcode_modules else 0)
        + before
        + after
    )
    start = margin * scale
    barcode_offset = 0
    barcode_x = margin
    if barcode_modules:
        barcode_offset = (
            before + title_height + footnote_gap + footnote_height + barcode_gap
        ) // scale
        barcode_x = margin + max(0, (content_w // scale - barcode_width) // 2)

    def draw(canvas: Image.Image, top: int) -> None:
        painter = ImageDraw.Draw(canvas)
        y = top + before
        for line in title_lines:
            if line:
                painter.text((float(start), y), line, font=title_font, fill=0, anchor="la")
            y += title_line_height
        y += footnote_gap
        for line in footnote_lines:
            if line:
                painter.text(
                    (float(start), y), line, font=footnote_font, fill=0, anchor="la",
                )
            y += footnote_line_height

    barcode_spec = None
    if barcode_modules:
        barcode_spec = {
            "x": barcode_x,
            "offset": barcode_offset,
            "modules": barcode_modules,
            "module": barcode_module_px,
            "height": max(1, barcode_height // scale),
        }

    return _Prepared(
        kind="shelf",
        height=height,
        draw=draw,
        warnings=warnings,
        extra={"size": chosen_size // scale, "lines": len(title_lines)},
        barcode=barcode_spec,
    )


def render_document(
    doc: dict,
    options: dict,
    fonts: FontLibrary,
    images: ImageStore,
) -> RenderResult:
    doc = doc or {}
    options = options or {}
    paper_w = clamp_int(doc.get("width", 384), 96, 832, 384)
    paper_w = (paper_w + 7) // 8 * 8
    margin = clamp_int(doc.get("margin", 10), 0, max(0, paper_w // 4), 10)
    content_w = max(32, paper_w - 2 * margin)
    ss = 2 if int(clamp(options.get("supersample", 2), 1, 2, 2)) >= 2 else 1

    prepared: list[_Prepared] = []
    blocks = doc.get("blocks") or []
    if not isinstance(blocks, list):
        blocks = []

    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type", "text")).lower()
        try:
            item: _Prepared | None
            if kind == "image":
                item = _prepare_image(block, images, content_w * ss, ss, margin)
            elif kind == "divider":
                item = _prepare_divider(block, content_w * ss, ss, margin)
            elif kind == "spacer":
                item = _prepare_spacer(block, ss)
            elif kind == "pattern":
                item = _prepare_pattern(block, content_w * ss, ss, margin)
            elif kind == "qr":
                item = _prepare_qr(block, content_w * ss, ss, margin)
            elif kind == "shelf":
                item = _prepare_shelf(block, fonts, content_w * ss, ss, margin)
            else:
                item = _prepare_text(block, fonts, content_w * ss, ss, margin)
        except Exception:
            item = None
        if item and item.height > 0:
            prepared.append(item)

    max_height = clamp_int(options.get("maxHeight", 16000), 100, 32000, 16000)
    total = sum(item.height for item in prepared)
    if total <= 0:
        total = ss
    truncated = total > max_height * ss
    warnings: list[str] = []
    for item in prepared:
        for warning in item.warnings:
            if warning not in warnings:
                warnings.append(warning)
    if truncated:
        warnings.append(f"Output was cut at the {max_height} dot maximum height.")
    if truncated:
        total = max_height * ss

    canvas = Image.new("L", (paper_w * ss, total), 255)
    y = 0
    image_boxes: list[tuple[int, int, int, int]] = []
    placements: list[tuple[_Prepared, int]] = []
    for item in prepared:
        if y >= total:
            break
        item.draw(canvas, y)
        if item.bbox:
            x0, y0, x1, y1 = item.bbox
            offset = round(y / ss)
            image_boxes.append(
                (
                    max(0, min(paper_w, x0)),
                    max(0, min(total // ss, y0 + offset)),
                    max(0, min(paper_w, x1)),
                    max(0, min(total // ss, y1 + offset)),
                )
            )
        placements.append((item, y))
        y += item.height

    if ss != 1:
        canvas = canvas.resize((paper_w, max(1, total // ss)), Image.Resampling.BOX)

    brightness = clamp(options.get("brightness", 0), -100, 100, 0)
    contrast = clamp(options.get("contrast", 0), -100, 100, 0)
    if brightness:
        canvas = ImageEnhance.Brightness(canvas).enhance(max(0.05, 1.0 + brightness / 100.0))
    if contrast:
        canvas = ImageEnhance.Contrast(canvas).enhance(max(0.05, 1.0 + contrast / 100.0))

    threshold = clamp_int(options.get("threshold", 160), 1, 254, 160)
    mode = str(options.get("dither", "auto")).lower()
    if mode == "auto":
        final = threshold_image(canvas, threshold)
        photo_mode = str(options.get("imageDither", "floyd")).lower()
        for box in image_boxes:
            x0, y0, x1, y1 = box
            if x1 <= x0 or y1 <= y0:
                continue
            patch = canvas.crop((x0, y0, x1, y1))
            dithered = dither_image(patch, photo_mode, threshold)
            final.paste(dithered, (x0, y0))
    else:
        final = dither_image(canvas, mode, threshold)

    barcode_painter = ImageDraw.Draw(final)
    for item, top in placements:
        spec = item.barcode
        if not spec:
            continue
        x = int(spec["x"])
        y = top // ss + int(spec["offset"])
        module = int(spec["module"])
        bar_height = int(spec["height"])
        if module < 1 or bar_height < 1:
            continue
        for symbol in spec["modules"]:
            if symbol == "1":
                x0 = max(0, x)
                x1 = min(final.width, x + module)
                y0 = max(0, y)
                y1 = min(final.height, y + bar_height)
                if x1 > x0 and y1 > y0:
                    barcode_painter.rectangle([x0, y0, x1 - 1, y1 - 1], fill=0)
            x += module

    if options.get("invert"):
        final = final.point(lambda value: 255 - value).convert("1")
    if options.get("rotate180"):
        final = final.rotate(180)

    black = final.histogram()[0]
    total_px = max(1, final.width * final.height)
    coverage = black / total_px

    return RenderResult(
        image=final,
        gray=canvas,
        width=final.width,
        height=final.height,
        coverage=coverage,
        truncated=truncated,
        warnings=warnings,
    )




def test_document() -> dict:
    return {
        "width": 384,
        "margin": 10,
        "blocks": [
            {"type": "text", "text": "PRINTPAL", "font": "Bahnschrift", "size": 40, "bold": True,
             "align": "center", "letterSpacing": 3, "spaceAfter": 2},
            {"type": "text", "text": "thermal print studio", "font": "Segoe UI", "size": 14,
             "align": "center", "spaceAfter": 10},
            {"type": "divider", "style": "solid", "thickness": 2, "spaceBefore": 2, "spaceAfter": 10},
            {"type": "text", "text": "PRINTER ONLINE", "font": "Consolas", "size": 22, "bold": True,
             "align": "center", "spaceAfter": 6},
            {"type": "text", "text": "YHK-0A73 / CCHW\nBLE ISSC UART\n384 dots  |  58 mm paper",
             "font": "Consolas", "size": 15, "align": "center", "lineSpacing": 1.35, "spaceAfter": 12},
            {"type": "text", "text": "The quick brown fox jumps over the lazy dog. 0123456789",
             "font": "Segoe UI", "size": 17, "align": "left", "spaceAfter": 8},
            {"type": "text", "text": "Text is rendered at high resolution and dithered for the 1-bit print head.",
             "font": "Segoe UI", "size": 14, "align": "left", "spaceAfter": 12},
            {"type": "qr", "data": "https://walkprint.net", "size": 160, "align": "center",
             "level": "M", "spaceBefore": 2, "spaceAfter": 6},
            {"type": "text", "text": "scan me", "font": "Segoe UI", "size": 12, "align": "center",
             "spaceAfter": 8},
            {"type": "divider", "style": "dashed", "thickness": 2, "spaceBefore": 2, "spaceAfter": 8},
            {"type": "text", "text": "12:34  28 OCT 2026", "font": "Consolas", "size": 12,
             "align": "center"},
        ],
    }


def calibration_document() -> dict:
    blocks: list[dict] = [
        {"type": "text", "text": "PRINTPAL CALIBRATION", "font": "Bahnschrift", "size": 30,
         "bold": True, "align": "center", "letterSpacing": 2, "spaceAfter": 2},
        {"type": "text", "text": "Keep this strip. Pick the smallest line that reads cleanly.",
         "font": "Segoe UI", "size": 12, "align": "center", "spaceAfter": 8},
        {"type": "divider", "style": "solid", "thickness": 1, "spaceBefore": 0, "spaceAfter": 6},
        {"type": "text", "text": "TEXT SIZE", "font": "Segoe UI", "size": 12, "bold": True,
         "spaceAfter": 2},
    ]
    for size in (10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 56, 64):
        blocks.append(
            {"type": "text", "text": f"{size} PX  Shelf 128 oz", "font": "Segoe UI",
             "size": size, "lineSpacing": 1.05, "spaceAfter": 0}
        )
    blocks.extend(
        [
            {"type": "divider", "style": "solid", "thickness": 1, "spaceBefore": 6, "spaceAfter": 6},
            {"type": "text", "text": "FOOTNOTE SIZE", "font": "Segoe UI", "size": 12,
             "bold": True, "spaceAfter": 2},
        ]
    )
    for size in (12, 14, 16, 18, 20, 22, 24, 28, 32):
        blocks.append(
            {"type": "text", "text": f"{size} PX  12.99 AISLE 4", "font": "Segoe UI",
             "size": size, "bold": True, "lineSpacing": 1.05, "spaceAfter": 0}
        )
    blocks.extend(
        [
            {"type": "divider", "style": "solid", "thickness": 1, "spaceBefore": 6, "spaceAfter": 6},
            {"type": "text", "text": "EDGE AND DOT GAIN", "font": "Segoe UI", "size": 12,
             "bold": True, "spaceAfter": 2},
            {"type": "pattern", "pattern": "lines", "height": 36, "spaceAfter": 4},
            {"type": "pattern", "pattern": "dots", "height": 36, "spaceAfter": 4},
            {"type": "divider", "style": "solid", "thickness": 1, "spaceBefore": 6, "spaceAfter": 6},
            {"type": "text", "text": "BARCODE 1234567890", "font": "Segoe UI", "size": 12,
             "bold": True, "spaceAfter": 2},
        ]
    )
    for height in (24, 32, 40):
        blocks.append(
            {"type": "shelf", "title": "", "footnote": "1234567890", "font": "Segoe UI",
             "barcode": True, "barcodeData": "1234567890", "barcodeHeight": height,
             "footnoteSize": 18, "spaceBefore": 4, "spaceAfter": 6}
        )
    blocks.append(
        {"type": "text", "text": "If text fills in, raise the threshold. If strokes break up, lower it.",
         "font": "Segoe UI", "size": 12, "align": "center", "spaceBefore": 4, "spaceAfter": 2}
    )
    return {"width": 384, "margin": 12, "blocks": blocks}
