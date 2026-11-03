from __future__ import annotations

import re
import winreg
from pathlib import Path

from PIL import ImageFont

PREFERRED = [
    "Segoe UI",
    "Segoe UI Variable",
    "Bahnschrift",
    "Arial",
    "Tahoma",
    "Verdana",
    "Trebuchet MS",
    "Georgia",
    "Cambria",
    "Times New Roman",
    "Calibri",
    "Candara",
    "Corbel",
    "Consolas",
    "Cascadia Mono",
    "Cascadia Code",
    "Courier New",
    "Lucida Console",
    "Franklin Gothic Medium",
    "Palatino Linotype",
    "Book Antiqua",
    "Sylfaen",
    "Impact",
    "Comic Sans MS",
    "Segoe Print",
    "Segoe Script",
    "Ink Free",
]

SKIP_FAMILIES = {
    "symbol",
    "webdings",
    "wingdings",
    "marlett",
    "segoe mdl2 assets",
    "segoe fluent icons",
    "segoe ui emoji",
    "segoe ui symbol",
    "hololens mdl2 assets",
    "segoe ui historic",
    "lastresort",
    "opensymbol",
}

STYLE_PATTERNS = [
    (re.compile(r"\b(bold\s+italic|bold\s+oblique|semibold\s+italic|semibold\s+oblique|"
                r"demibold\s+italic|black\s+italic|heavy\s+italic|extrabold\s+italic|"
                r"ultrabold\s+italic|light\s+italic|thin\s+italic)\b", re.I), "bolditalic"),
    (re.compile(r"\b(bold|semibold|demibold|black|heavy|extrabold|ultrabold)\b", re.I), "bold"),
    (re.compile(r"\b(italic|oblique)\b", re.I), "italic"),
]


def _clean_name(name: str) -> str:
    name = re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name, flags=re.I)
    name = name.replace("&", "and")
    return re.sub(r"\s+", " ", name).strip()


def _classify(name: str) -> tuple[str, str]:
    family = name
    variant_parts: list[str] = []
    for pattern, variant in STYLE_PATTERNS:
        if pattern.search(family):
            variant_parts.append(variant)
            family = pattern.sub("", family)
    if "bolditalic" in variant_parts:
        variant = "bolditalic"
    elif "bold" in variant_parts:
        variant = "bold"
    elif "italic" in variant_parts:
        variant = "italic"
    else:
        variant = "regular"

    family = re.sub(r"\b(regular|roman|book|normal|medium|light|semilight|thin)\b\s*$", "", family, flags=re.I)
    family = re.sub(r"\s+", " ", family).strip(" -_")
    return family or name, variant


def _candidate_paths(filename: str) -> list[Path]:
    filename = filename.strip().strip('"')
    if not filename:
        return []
    path = Path(filename)
    if path.is_absolute() and path.exists():
        return [path]
    candidates = [
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts" / filename,
        Path("C:/Windows/Fonts") / filename,
    ]
    return [candidate for candidate in candidates if candidate.exists()]


def _registry_fonts() -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ]
    for root, key_path in keys:
        try:
            key = winreg.OpenKey(root, key_path)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(value, str):
                    continue
                for path in _candidate_paths(value):
                    found.append((_clean_name(name), path))
        finally:
            try:
                key.Close()
            except OSError:
                pass
    return found


class FontLibrary:
    def __init__(self) -> None:
        self._variants: dict[str, dict[str, tuple[Path, int]]] = {}
        self._display: dict[str, str] = {}
        self._cache: dict[tuple[str, bool, bool, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._scan()

    def _scan(self) -> None:
        for name, path in _registry_fonts():
            suffix = path.suffix.lower()
            if suffix not in {".ttf", ".otf", ".ttc", ".otc"}:
                continue
            family, variant = _classify(name)
            key = family.lower()
            if key in SKIP_FAMILIES or not family:
                continue
            variants = self._variants.setdefault(key, {})
            if variant not in variants:
                variants[variant] = (path, 0)
                self._display.setdefault(key, family)

        for fallback in ("segoeui.ttf", "arial.ttf", "consola.ttf"):
            path = Path("C:/Windows/Fonts") / fallback
            if path.exists():
                family = {
                    "segoeui.ttf": "Segoe UI",
                    "arial.ttf": "Arial",
                    "consola.ttf": "Consolas",
                }[fallback]
                key = family.lower()
                self._variants.setdefault(key, {}).setdefault("regular", (path, 0))
                self._display.setdefault(key, family)

    def families(self) -> list[dict]:
        preferred_rank = {name.lower(): index for index, name in enumerate(PREFERRED)}
        names = list(self._variants)
        names.sort(key=lambda key: (preferred_rank.get(key, 999), self._display.get(key, key).lower()))
        output = []
        for key in names:
            variants = self._variants[key]
            output.append(
                {
                    "name": self._display.get(key, key),
                    "variants": [v for v in ("regular", "bold", "italic", "bolditalic") if v in variants],
                }
            )
        return output

    def resolve(self, family: str | None, bold: bool = False, italic: bool = False) -> tuple[Path, int]:
        key = (family or "").lower()
        variants = self._variants.get(key)
        if not variants:
            for candidate in ("segoe ui", "arial", "consolas"):
                if candidate in self._variants:
                    key = candidate
                    variants = self._variants[candidate]
                    break
        if not variants:
            raise RuntimeError("No usable system fonts were found")

        wanted = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
        order = [wanted, "regular", "bold", "italic", "bolditalic"]
        for variant in order:
            if variant in variants:
                return variants[variant]
        return next(iter(variants.values()))

    def load(self, family: str | None, bold: bool, italic: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (family or "", bool(bold), bool(italic), int(size))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path, index = self.resolve(family, bold, italic)
        try:
            font = ImageFont.truetype(str(path), size=max(4, int(size)), index=index)
        except OSError:
            font = ImageFont.load_default(size=max(4, int(size)))
        self._cache[key] = font
        if len(self._cache) > 512:
            self._cache.clear()
            self._cache[key] = font
        return font
