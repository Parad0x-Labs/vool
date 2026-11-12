"""Emoji in model prose are decoration, not a reason to refuse a whole PDF.

Measured 2026-11-05 (operator's live 0.7.8 test): "create me iron rich diet … gief as doc pls
PDF" — the model wrote food emoji into the plan text, the per-glyph coverage law refused the
WHOLE document ("The PDF font cannot represent every source character"), and four approval
rounds of re-authored content later a smaller PDF shipped. The gate's intent is right — never
render glyphs the font lacks, never silently lose content — but emoji are DECORATION: stripping
them loses nothing. A real script the font cannot read still refuses honestly, because a
stripped letter is a lost word.
"""

from __future__ import annotations

import pytest

from core.presentation.model import Paragraph, RenderDocument, Span
from core.presentation.render_pdf import PdfRefused, render_document


def _doc(text: str) -> RenderDocument:
    return RenderDocument(blocks=[Paragraph([Span(text)])])


def _text_of(pdf: bytes) -> str:
    """Extract the visible text layer — TTF subsets encode glyphs, raw bytes never carry it."""
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_emoji_in_prose_no_longer_refuses_the_document() -> None:
    """The measured refusal shape: food emoji inside an ordinary diet plan."""
    pdf = render_document(_doc("Iron-rich foods: red meat, lentils, spinach 🥦 and sardines 🐟."))
    assert pdf[:4] == b"%PDF"
    body = _text_of(pdf)
    assert "lentils" in body and "sardines" in body, body


def test_lock_emoji_are_stripped_too() -> None:
    pdf = render_document(_doc("Padlock code 5599 🔒 — keep it safe."))
    assert pdf[:4] == b"%PDF"
    assert "5599" in _text_of(pdf)


def test_text_the_font_covers_is_untouched() -> None:
    text = "Kept: accented ėšž, dashes —, arrows →, ellipses …, checkmarks ✓."
    pdf = render_document(_doc(text))
    body = _text_of(pdf)
    for kept in ("ėšž", "—", "→", "…"):
        assert kept in body, (kept, body)


def test_a_real_unknown_script_still_refuses() -> None:
    """A stripped letter is a lost word: content outside the decorative ranges refuses."""
    with pytest.raises(PdfRefused):
        render_document(_doc("Desert \U00013000 oasis \U00013079 record."))


def test_zero_width_joiners_and_selectors_are_invisible() -> None:
    text = "family \U0001F468\u200D\U0001F469\u200D\U0001F467 emoji\uFE0F."
    pdf = render_document(_doc(text))
    assert pdf[:4] == b"%PDF"
