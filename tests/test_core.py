from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from printpal import protocol
from printpal.fonts import FontLibrary
from printpal.render import (
    ImageStore,
    atkinson_image,
    calibration_document,
    encode_barcode,
    render_document,
    test_document,
    threshold_image,
)
from printpal.util import clamp, clamp_int, deep_merge, human_bytes

FONTS = FontLibrary()


def gradient(width: int = 64, height: int = 16) -> Image.Image:
    row = np.tile(np.linspace(0, 255, width, dtype=np.uint8), (height, 1))
    return Image.fromarray(row, "L")


class ProtocolTests(unittest.TestCase):
    def test_raster_header(self) -> None:
        self.assertEqual(protocol.escpos_raster_header(48, 3), b"\x1d\x76\x30\x00\x30\x00\x03\x00")
        self.assertEqual(protocol.escpos_raster_header(48, 300), b"\x1d\x76\x30\x00\x30\x00\x2c\x01")

    def test_burn_bytes_inverted_and_packed(self) -> None:
        white = Image.new("1", (8, 1), 255)
        black = Image.new("1", (8, 1), 0)
        first = Image.new("1", (8, 1), 255)
        first.putpixel((0, 0), 0)
        self.assertEqual(protocol._burn_bytes(white), b"\x00")
        self.assertEqual(protocol._burn_bytes(black), b"\xff")
        self.assertEqual(protocol._burn_bytes(first), b"\x80")

    def test_chunks_cover_every_row_once(self) -> None:
        image = Image.new("1", (16, 10), 255)
        for x in range(0, 16, 3):
            image.putpixel((x, 4), 0)
        chunks = list(protocol.escpos_chunks(image, rows_per_chunk=3))
        self.assertEqual([rows for _, rows in chunks], [3, 3, 3, 1])
        payload = b"".join(chunk[8:] for chunk, _ in chunks)
        self.assertEqual(payload, protocol._burn_bytes(image))
        for chunk, rows in chunks:
            self.assertEqual(chunk[:4], b"\x1d\x76\x30\x00")
            self.assertEqual(int.from_bytes(chunk[4:6], "little"), 2)
            self.assertEqual(int.from_bytes(chunk[6:8], "little"), rows)

    def test_feed_clamped(self) -> None:
        self.assertEqual(protocol.escpos_feed(4), b"\n\n\n\n")
        self.assertEqual(protocol.escpos_feed(0), b"\n")
        self.assertEqual(len(protocol.escpos_feed(100)), 32)

    def test_status_and_identity(self) -> None:
        status = protocol.parse_status(b"HV=H1.0,SV=V1.01,VOLT=7580mv,DPI=384,\x00")
        self.assertEqual(status["voltage_mv"], 7580)
        self.assertEqual(status["battery"], 59)
        self.assertEqual(status["dots"], 384)
        self.assertEqual(protocol.parse_serial(b"sn:CCHW240038000A73.\x00"), "CCHW240038000A73")
        self.assertEqual(protocol.parse_model(b"public id:0212.\x00"), "0212")

    def test_battery_curve(self) -> None:
        self.assertEqual(protocol.battery_percent(6400), 0)
        self.assertEqual(protocol.battery_percent(7400), 50)
        self.assertEqual(protocol.battery_percent(8400), 100)
        self.assertEqual(protocol.battery_percent(9000), 100)

    def test_cat_framing(self) -> None:
        message = protocol.cat_message(protocol.CAT_SET_QUALITY, [3])
        self.assertEqual(message[:3], b"Qx\xa4")
        self.assertEqual(message[4], 1)
        self.assertEqual(message[6], 3)
        self.assertEqual(message[7], protocol.crc8([3]))
        self.assertEqual(message[-1], 0xFF)
        self.assertTrue(protocol.cat_feed(2).startswith(b"Qx\xa1"))


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ImageStore()
        self.store.directory = Path(self.temp.name)
        self.store.index_path = Path(self.temp.name) / "index.json"
        self.store._index = {}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def render(self, doc: dict, options: dict | None = None):
        return render_document(doc, options or {"dither": "auto", "supersample": 2}, FONTS, self.store)

    def test_text_page_is_aligned_and_non_empty(self) -> None:
        result = self.render(
            {
                "width": 384,
                "margin": 12,
                "blocks": [
                    {"type": "text", "text": "Hello PrintPal", "font": "Segoe UI", "size": 24, "bold": True},
                    {"type": "divider", "style": "double", "thickness": 2},
                    {"type": "spacer", "height": 12},
                ],
            }
        )
        self.assertEqual(result.width % 8, 0)
        self.assertEqual(result.width, 384)
        self.assertGreater(result.height, 30)
        self.assertEqual(result.image.mode, "1")
        self.assertGreater(result.coverage, 0.0)
        self.assertLess(result.coverage, 0.8)

    def test_long_text_wraps_instead_of_clipping(self) -> None:
        short = self.render({"blocks": [{"type": "text", "text": "short", "size": 18}]})
        long = self.render(
            {
                "blocks": [
                    {
                        "type": "text",
                        "text": "The quick brown fox jumps over the lazy dog. " * 8,
                        "size": 18,
                    }
                ]
            }
        )
        self.assertGreater(long.height, short.height * 3)
        self.assertEqual(long.truncated, False)

    def test_empty_document_has_paper_strip(self) -> None:
        result = self.render({"blocks": []})
        self.assertGreaterEqual(result.height, 1)
        self.assertEqual(result.image.size, (384, result.height))

    def test_image_block_renders_dithered(self) -> None:
        buffer = io.BytesIO()
        gradient(120, 80).convert("RGB").save(buffer, "PNG")
        meta = self.store.add_bytes(buffer.getvalue(), "gradient.png")
        result = self.render(
            {
                "width": 384,
                "margin": 10,
                "blocks": [{"type": "image", "imageId": meta["id"], "fit": "width"}],
            }
        )
        self.assertGreater(result.height, 90)
        self.assertGreater(result.coverage, 0.25)
        self.assertLess(result.coverage, 0.75)

    def test_qr_block_renders(self) -> None:
        result = self.render({"blocks": [{"type": "qr", "data": "https://walkprint.net", "size": 160}]})
        self.assertGreater(result.height, 140)
        self.assertGreater(result.coverage, 0.05)

    def test_test_document_renders(self) -> None:
        result = self.render(test_document())
        self.assertEqual(result.width, 384)
        self.assertGreater(result.height, 300)
        self.assertFalse(result.truncated)

    def test_invert_flips_pixels(self) -> None:
        doc = {"blocks": [{"type": "text", "text": "INVERT", "size": 30}]}
        normal = self.render(doc)
        inverted = self.render(doc, {"dither": "auto", "invert": True})
        self.assertAlmostEqual(normal.coverage + inverted.coverage, 1.0, places=3)

    def test_threshold_and_atkinson(self) -> None:
        gray = gradient(256, 2)
        binary = threshold_image(gray, 128)
        self.assertGreater(binary.histogram()[0], 0)
        self.assertGreater(binary.histogram()[255], 0)
        dithered = atkinson_image(gray)
        self.assertEqual(dithered.mode, "1")
        self.assertGreater(dithered.histogram()[0], 0)
        self.assertGreater(dithered.histogram()[255], 0)


class LabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ImageStore()
        self.store.directory = Path(self.temp.name)
        self.store.index_path = Path(self.temp.name) / "index.json"
        self.store._index = {}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def render(self, doc: dict, options: dict | None = None):
        return render_document(doc, options or {"dither": "auto", "supersample": 2}, FONTS, self.store)

    def test_encode_barcode_returns_binary_modules(self) -> None:
        modules = encode_barcode("SALT")
        self.assertGreater(len(modules), 40)
        self.assertEqual(set(modules), {"0", "1"})
        self.assertEqual(len(modules), len(encode_barcode("SALT")))
        self.assertNotEqual(encode_barcode("SALT"), encode_barcode("SALT2"))
        with self.assertRaises(ValueError):
            encode_barcode("   ")

    def test_fit_width_text_fills_the_label(self) -> None:
        result = self.render(
            {
                "width": 384,
                "margin": 12,
                "blocks": [
                    {
                        "type": "text",
                        "text": "Pantry",
                        "font": "Bahnschrift",
                        "fitWidth": True,
                        "size": 160,
                        "minSize": 10,
                        "align": "left",
                    }
                ],
            }
        )
        ink = np.array(result.image) == 0
        cols = np.where(ink.any(axis=0))[0]
        self.assertTrue(cols.size)
        span = int(cols.max() - cols.min() + 1)
        content_w = 384 - 24
        self.assertLessEqual(span, content_w)
        self.assertGreaterEqual(span, int(content_w * 0.8))
        self.assertEqual(result.warnings, [])

    def test_fit_width_warns_when_text_must_wrap(self) -> None:
        result = self.render(
            {
                "blocks": [
                    {
                        "type": "text",
                        "text": "W" * 80,
                        "fitWidth": True,
                        "size": 48,
                        "minSize": 8,
                    }
                ]
            }
        )
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("wrap", result.warnings[0])

    def test_shelf_tag_prints_title_footnote_and_bottom_barcode(self) -> None:
        result = self.render(
            {
                "width": 384,
                "margin": 12,
                "blocks": [
                    {
                        "type": "shelf",
                        "title": "COFFEE",
                        "footnote": "AISLE 2 · 8.99",
                        "font": "Bahnschrift",
                        "barcode": True,
                        "barcodeData": "1234567890",
                        "barcodeHeight": 10,
                    }
                ],
            }
        )
        self.assertEqual(result.warnings, [])
        pixels = np.array(result.image) == 0
        bottom = pixels[-10:]
        self.assertTrue(bottom.any())
        cols = np.where(bottom.any(axis=0))[0]
        span = int(cols.max() - cols.min() + 1)
        self.assertGreater(span, 150)
        self.assertLessEqual(span, 384 - 24)
        self.assertTrue(pixels[-1].any())

    def test_shelf_barcode_adds_a_thirty_two_pixel_strip(self) -> None:
        base = {"type": "shelf", "title": "COFFEE", "footnote": "AISLE 2"}
        plain = self.render({"blocks": [dict(base)]})
        coded = self.render({"blocks": [dict(base, barcode=True, barcodeData="COFFEE")]})
        self.assertEqual(coded.height - plain.height, 36)
        self.assertEqual(plain.warnings, [])

    def test_shelf_two_line_title_warns(self) -> None:
        result = self.render(
            {
                "blocks": [
                    {
                        "type": "shelf",
                        "title": "ORGANIC EXTRA CRUNCHY PEANUT BUTTER WITH SEA SALT",
                        "footnote": "12.99",
                    }
                ]
            }
        )
        self.assertTrue(any("wraps to 2 lines" in warning for warning in result.warnings))

    def test_shelf_barcode_too_long_warns(self) -> None:
        result = self.render(
            {
                "blocks": [
                    {
                        "type": "shelf",
                        "title": "ITEM",
                        "barcode": True,
                        "barcodeData": "X" * 64,
                    }
                ]
            }
        )
        self.assertTrue(any("too long" in warning for warning in result.warnings))

    def test_shelf_barcode_modules_are_at_least_two_pixels(self) -> None:
        result = self.render(
            {
                "blocks": [
                    {
                        "type": "shelf",
                        "title": "COFFEE",
                        "barcode": True,
                        "barcodeData": "1234567890",
                        "barcodeHeight": 32,
                    }
                ]
            }
        )
        pixels = np.array(result.image) == 0
        bottom = pixels[-32:]
        columns = bottom.any(axis=0)
        runs = []
        current = 0
        for value in columns:
            if value:
                current += 1
            elif current:
                runs.append(current)
                current = 0
        if current:
            runs.append(current)
        self.assertTrue(runs)
        self.assertGreaterEqual(min(runs), 2)

    def test_calibration_document_renders(self) -> None:
        result = self.render(calibration_document())
        self.assertGreater(result.height, 800)
        self.assertLessEqual(result.height, 16000)
        self.assertEqual(result.warnings, [])
        self.assertFalse(result.truncated)


class UtilTests(unittest.TestCase):
    def test_clamp(self) -> None:
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-5, 0, 10), 0)
        self.assertEqual(clamp("x", 0, 10, 3), 3)
        self.assertEqual(clamp(float("nan"), 0, 10, 4), 4)
        self.assertEqual(clamp_int(9.6, 0, 10), 10)

    def test_deep_merge(self) -> None:
        merged = deep_merge({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"d": 4}, "e": 5})
        self.assertEqual(merged, {"a": 1, "b": {"c": 2, "d": 4}, "e": 5})

    def test_human_bytes(self) -> None:
        self.assertEqual(human_bytes(512), "512 B")
        self.assertEqual(human_bytes(2048), "2.0 KB")
        self.assertEqual(human_bytes(2 * 1024 * 1024), "2.0 MB")


if __name__ == "__main__":
    unittest.main()
