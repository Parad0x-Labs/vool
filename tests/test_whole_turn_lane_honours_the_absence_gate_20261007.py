"""The whole-turn lane serves no record the capsule's absence gate refused (core/context_retrieval.py).

The capsule drops a user record as absent-facet noise when two or more of the question's
discriminating terms were never retained anywhere the caller may read: an ask about a facet, a
place or a person the chat never mentioned. The whole-turn lane (ddb2d4e5) ranked every turn any
search leg returned and delivered it whole beside the capsule, so the same records came back under
"Evidence turns ... most relevant first", unmarked, and the reader could answer the wrong entity's
value from them. Every record and question below was written for this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import _hash_backend  # noqa: F401

BOOTH = ("The ferry ticket booth on the east quay uses booth number Q-58. Its awning colour is rust "
         "orange. Those are all the details I keep for that booth.")
SHED = ("The allotment shed padlock code is 7-3-1. Its door is painted moss green. Those are all the "
        "details I keep for the shed.")

KEEP = {
    "own_value": ("What is the booth number of the ferry ticket booth on the east quay?", "Q-58"),
    "own_facet": ("What is the awning colour of the ferry ticket booth on the east quay?", "rust orange"),
    "other_record": ("What is the padlock code of the allotment shed?", "7-3-1"),
}
# Present-tense asks: the gate deliberately leaves past-only and as-of asks to their own temporal laws.
REFUSE = {
    "absent_facet": "What are the opening hours of the ferry ticket booth on the east quay?",
    "wrong_entity": "What is the booth number of the ticket booth at the lighthouse jetty?",
    "wrong_person": "What is Tomas Varga's booth number for the ferry ticket booth on the east quay?",
}


def _store(tmp_path: Path, chat: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    profile = tmp_path / "home"
    profile.mkdir(parents=True, exist_ok=True)
    # monkeypatch, so no later test (or the sandboxed child another test starts) inherits this home
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_WORKSPACE_ROOT", "VOOL_WORKSPACE_ROOT"):
        monkeypatch.setenv(name, str(profile / "workspace"))
    ensure_chat_namespace(chat, grant_current_receipts=False)
    # Each reply adds advice of its own and shares a word with the asks, so the capsule has one
    # harmless line to deliver even when the gate drops both records: that opens the lane beside it.
    for body, reply in ((BOOTH, "Noted. A booth that size usually needs its awning re-proofed each spring."),
                        (SHED, "Noted. A shed padlock usually wants a drop of oil each autumn.")):
        receipt = cr.store_turn(chat, body, reply,
                                access_policy=resolve_memory_access_policy(chat_id=chat),
                                source_context={"runtime_home": str(profile), "statement_at": 1767600000.0})
        assert receipt["status"] in ("stored", "retained"), receipt
    return profile


def _capsule(profile: Path, chat: str, question: str) -> tuple[str, dict]:
    """The capsule an ordinary chat turn is served, at the caller's default budget."""
    from core.memory.entries import resolve_memory_access_policy

    messages = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                                   access_policy=resolve_memory_access_policy(chat_id=chat),
                                   source_context={"runtime_home": str(profile), "chat_id": chat},
                                   env={"NULLA_CONTEXT_CAPSULE_V2": "1", "VOOL_CONTEXT_CAPSULE_V2": "1"})
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    return capsule, cr.get_last_retrieval_telemetry()


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("case", sorted(REFUSE))
def test_a_record_the_absence_gate_refused_never_returns_through_the_lane(tmp_path, monkeypatch, case):
    profile = _store(tmp_path, "quay-" + case, monkeypatch)
    capsule, telemetry = _capsule(profile, "quay-" + case, REFUSE[case])
    assert "Q-58" not in capsule, (telemetry.get("whole_turn_lines"), capsule)
    assert "7-3-1" not in capsule, (telemetry.get("whole_turn_lines"), capsule)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("case", sorted(KEEP))
def test_an_ask_the_records_answer_still_gets_them(tmp_path, monkeypatch, case):
    question, value = KEEP[case]
    profile = _store(tmp_path, "quay-" + case, monkeypatch)
    capsule, _telemetry = _capsule(profile, "quay-" + case, question)
    assert value in capsule, capsule


ATTENDANT_ASK = "Who is the duty attendant at the ferry ticket booth on the east quay?"
ROTA = "Mirela minds the harbour kiosk on weekday mornings and Ilka covers the weekends."


def test_a_turn_anchored_by_meaning_survives_the_gate(tmp_path, monkeypatch):
    """The gate fires (duty, attendant were never said), yet the rota answers the ask in other words.

    The semantic arm ties it to the ask by meaning and shares no word with it, so the gate exempts it;
    the booth record, tied only by shared words, stays out of the capsule and of the lane."""
    import core.embedding_service as embedding_service
    from tests.test_capsule_allowance_item_limits import _make_embed

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: "gate-fixture-model")
    monkeypatch.setattr(cr, "embed_stamped", _make_embed(ATTENDANT_ASK, {"minds": 0.3}))
    profile = _store(tmp_path, "quay-rota", monkeypatch)
    from core.memory.entries import resolve_memory_access_policy

    receipt = cr.store_turn("quay-rota", ROTA, "Noted.",
                            access_policy=resolve_memory_access_policy(chat_id="quay-rota"),
                            source_context={"runtime_home": str(profile), "statement_at": 1767600000.0})
    assert receipt["status"] in ("stored", "retained"), receipt
    capsule, telemetry = _capsule(profile, "quay-rota", ATTENDANT_ASK)
    assert "Mirela" in capsule, (telemetry.get("whole_turn_lines"), capsule)
    assert "Q-58" not in capsule, (telemetry.get("whole_turn_lines"), capsule)
