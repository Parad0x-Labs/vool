""""how much is the second one?" after the person gave both figures is not held to current information.

Measured on the real local model (qwen3:8b, 0a5474f5, held-out run 17, Web lookups off): after "i'm comparing two flats:
one on Birch Row for 950 a month and one on Kell Lane for 1100 a month", "how much is the second one?" was still answered
"Web lookups are off": the front door no longer refused it, but the turn's requirements held it to current information
and the later guards put the web-off line in place of the answer. All names, places and figures are invented.
"""
from __future__ import annotations

from core.execution_requirements import requirements_for

FLATS = "i'm comparing two flats: one on Birch Row for 950 a month and one on Kell Lane for 1100 a month"


def _context(asked: str, earlier: str = FLATS) -> dict:
    return {"conversation_history": [{"role": "user", "content": earlier}, {"role": "assistant", "content": "ok"},
                                     {"role": "user", "content": asked}]}


def test_a_pointer_back_at_given_figures_needs_no_lookup():
    asked = "how much is the second one?"
    need = requirements_for(asked, source_context=_context(asked))
    assert not need.current_information_required
    assert not need.external_evidence_required


def test_without_the_figures_the_lookup_reading_stands():
    asked = "how much is the second one?"
    assert requirements_for(asked, source_context=_context(asked, "i'm thinking about two flats")).current_information_required
