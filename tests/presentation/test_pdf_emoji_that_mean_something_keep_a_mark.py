"""Emoji that mean something keep a drawable mark; emoji outside the two emoji blocks never refuse.

App bugs 2026-11-12, following the Nov 5 decision that emoji are decoration
(test_pdf_emoji_are_decoration.py):

- "Top pick ⭐ for families" and "Alarm ⏰ at 7" still refused the WHOLE document, because the star,
  the clocks and hourglasses live outside the two blocks the decoration law knew (U+1F000-1FFFF,
  U+2600-27BF). The same shape as the diet plan that started the decision.
- A comparison table of ✅ and ❌ came out as a row with two empty cells: the marks were stripped as
  decoration, and with them the table's answer.

Now a mark that means something is drawn as the plain mark the font has (✔, ✗, ★, !, ?), the other
emoji the font lacks are dropped wherever they sit in Unicode, and a real script the font cannot
read still refuses: a stripped letter is a lost word.
"""

from __future__ import annotations

import io

import pytest

from core.presentation.model import Paragraph, RenderDocument, Span
from core.presentation.render_pdf import (
    _DECORATIVE_EXTRA,
    PdfRefused,
    _font,
    markdown_document,
    render_document,
)


def _text_of(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def _render(text: str) -> str:
    pdf = render_document(RenderDocument(blocks=[Paragraph([Span(text)])]))
    assert pdf[:4] == b"%PDF"
    return _text_of(pdf)


def test_a_comparison_table_keeps_its_check_and_cross_marks() -> None:
    source = "| Feature | VOOL | Other |\n|---|---|---|\n| Offline | ✅ | ❌ |\n| Private | ✅ | ❎ |"
    body = _text_of(render_document(markdown_document(source)))
    assert body.count("✔") == 2, body
    assert body.count("✗") == 2, body


def test_a_star_rating_keeps_its_stars() -> None:
    body = _render("Rating: ⭐⭐⭐ and \U0001f31f top pick")
    assert "Rating: ★★★" in body, body
    assert "★ top pick" in body, body


def test_emphasis_marks_keep_their_meaning() -> None:
    body = _render("Important❗ Is it done❓")
    assert "Important!" in body and "Is it done?" in body, body


@pytest.mark.parametrize("text", ["Top pick ⭐ for families.", "Alarm ⏰ at 7.", "Wait ⌛ then go ⤴."])
def test_emoji_outside_the_two_blocks_no_longer_refuse_the_document(text: str) -> None:
    body = _render(text)
    for word in text.replace("⭐", " ").replace("⏰", " ").replace("⌛", " ").replace("⤴", " ").split():
        assert word.strip(".") in body, (word, body)


def test_every_listed_emoji_the_font_lacks_is_dropped_never_a_refusal() -> None:
    from reportlab.pdfbase import pdfmetrics

    _font()
    face = pdfmetrics.getFont("VoolPDF").face
    missing = [chr(cp) for cp in sorted(_DECORATIVE_EXTRA) if cp not in face.charToGlyph]
    assert missing, "the font draws every listed emoji: this test would prove nothing"
    body = _render("start " + " ".join(missing) + " end")
    assert "start" in body and "end" in body, body


def test_text_the_font_draws_is_untouched() -> None:
    """The arrows, the copyright and trademark signs and the info sign are on the list but drawn."""
    body = _render("Next → © ™ ℹ ✔ ★")
    for kept in ("→", "©", "™", "✔", "★"):
        assert kept in body, (kept, body)


def test_a_real_unknown_script_still_refuses() -> None:
    with pytest.raises(PdfRefused):
        render_document(RenderDocument(blocks=[Paragraph([Span("Desert \U00013000 oasis ⭐.")])]))
