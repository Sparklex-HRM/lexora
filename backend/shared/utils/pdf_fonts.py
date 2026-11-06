"""Lexora HRM — Unicode PDFs: accented names (Québec French, …) and typographic symbols print as typed.

fpdf2's core fonts (Helvetica / Times / Courier) are Latin-1 only, so anything outside
Latin-1 (curly quotes, en/em dashes, Œ, ≥ …) became "?". Every PDF generator should build
on ``UnicodePDF`` instead of ``FPDF``:

    from shared.utils.pdf_fonts import UnicodePDF, cad, pdf_text

    pdf = UnicodePDF(format="A4")      # drop-in for FPDF(...)
    pdf.set_font("Helvetica", "B", 12) # core names are redirected to the bundled font
    pdf.cell(0, 6, f"Net pay {cad(123456.5)}")   # "Net pay $123,456.50"

The bundled font is Noto Sans (SIL Open Font License 1.1 — ``shared/fonts/OFL.txt``
ships with it). It covers Latin incl. diacritics, Greek and Cyrillic.
A few symbols it lacks (≥ ≤ ≠ →) are mapped to ASCII by ``pdf_text``; other missing
glyphs are dropped rather than crashing the document. If the font files are missing
the class falls back to the old Latin-1 behaviour so a PDF is still produced.
"""
from __future__ import annotations

import logging
import os

from fpdf import FPDF

log = logging.getLogger("lexora.pdf")
# fpdf2 subsets the embedded font through fontTools, which logs ~15 INFO lines per PDF
# (glyph lists) — noise in the JSON logs. Warnings and errors still come through.
logging.getLogger("fontTools").setLevel(logging.WARNING)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
FAMILY = "NotoSans"
_STYLE_FILES = {"": "NotoSans-Regular.ttf", "B": "NotoSans-Bold.ttf",
                "I": "NotoSans-Italic.ttf", "BI": "NotoSans-BoldItalic.ttf"}
_CORE = {"helvetica", "arial", "times", "courier"}

# Characters Noto Sans has no glyph for → readable ASCII.
_SWAP = {"≥": ">=", "≤": "<=", "≠": "!=", "→": "->", "←": "<-", "⇒": "=>", "✓": "Yes", "✔": "Yes",
         "✗": "x", "​": "", "﻿": ""}
# Latin-1 fallback (font files missing).
_LATIN1_SWAP = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
                "•": "-", " ": " ", "×": "x", "Œ": "OE", "œ": "oe"}


def font_available() -> bool:
    return all(os.path.isfile(os.path.join(FONT_DIR, f)) for f in _STYLE_FILES.values())


def pdf_text(v, unicode_ok: bool = True) -> str:
    """Text safe for a UnicodePDF (default) or, with ``unicode_ok=False``, a core-font PDF."""
    s = str(v if v is not None else "")
    for a, b in _SWAP.items():
        if a in s:
            s = s.replace(a, b)
    if unicode_ok:
        return s
    for a, b in _LATIN1_SWAP.items():
        if a in s:
            s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def cad(v, decimals: int = 2) -> str:
    """Canadian-dollar amount (en-CA): cad(123456.5) → '$123,456.50'; negatives '-$12.00'."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.{decimals}f}"


def register_unicode_font(pdf: FPDF) -> str | None:
    """Add the bundled family to ``pdf``; returns the family name or None if unavailable."""
    if not font_available():
        return None
    fonts = getattr(pdf, "fonts", {})
    for style, fname in _STYLE_FILES.items():
        key = (FAMILY + style).lower()
        if key not in fonts:
            pdf.add_font(FAMILY, style, os.path.join(FONT_DIR, fname))
    return FAMILY


class UnicodePDF(FPDF):
    """FPDF with the bundled Unicode font. Core font names are redirected to it, so
    existing ``set_font("Helvetica", ...)`` calls keep working and gain full Unicode support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unicode_family = register_unicode_font(self)
        if self.unicode_family is None:
            log.warning("Unicode font not found in %s — PDFs fall back to Latin-1", FONT_DIR)

    @property
    def unicode_ok(self) -> bool:
        return self.unicode_family is not None

    def set_font(self, family=None, style="", size=0):
        if self.unicode_family and family and family.lower() in _CORE:
            family = self.unicode_family
        return super().set_font(family, style, size)

    def normalize_text(self, text):
        # TTF font: map the few glyphs Noto Sans lacks; core font: the old Latin-1 swaps.
        return super().normalize_text(pdf_text(text, unicode_ok=self.is_ttf_font))
