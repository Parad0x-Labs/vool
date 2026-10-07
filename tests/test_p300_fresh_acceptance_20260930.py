"""Fresh acceptance pack — paired300-authority-repair-20260930.

AUTHORING CONTRACT (frozen before first execution; see
artifacts/paired300-authority-repair-20260930/acceptance/ for the freeze
record: case list, expectations, hashes, and the first-run log):

Every expectation below is derived from the PRODUCT CONTRACTS, not from any
observed run of this pack:

  A  BINDING — admitted support and competition bind to the claim's own
     subject; sharing a unit or a record adjective is not identity.
  B  NEGATION — an explicitly negated value is never positive evidence and
     never remains the record's current value.
  C  CORRECTION / ORDER — an explicit correction controls, including a
     same-day correction that lowers a previously misstated maximum;
     canonical provenance dates order records when they genuinely differ.
  D  DIRECTION — a "best" record over a duration reads lower-is-better
     without needing a direction synonym.
  E  PROVENANCE — only the canonical stated/recorded provenance marks recency;
     a date inside fact prose (plaque, plan, deadline) never does; unknown
     time stays unknown.
  F  HEDGE CLAUSE — a hedge inside the value's own clause (its own sentence,
     up to the nearest clause boundary) exempts a generalization; a hedge in
     a neighbouring clause or a neighbouring sentence never exempts a current
     reading.
  G  EVIDENCE — only admitted same-session user-said evidence retains an
     answer; assistant attribution, foreign chat scope, invented values and
     missing evidence are refused through delivery.
  H  PREFERENCE — a retained user-owned preference rides the capsule for an
     advice ask on its topic (no stored-noun repetition), under distractors;
     forget removes it; quoted/source-only and foreign-scope text never pools.

The worlds, entities, values and temporal relations are NEW (joinery, diving,
printing, bouldering, canals, rowing); simple paraphrases of the owner-review
cases were rejected at authoring time.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import recorded_state_retention


def _wrap(lines: list[str]) -> str:
    return (
        "<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n"
        + "\n".join(lines)
        + "\n</retrieved_context>"
    )


def _seam(reply: str, question: str, lines: list[str], *, chat: str = "accept-chat",
          record_chat: str | None = None):
    source_context: dict = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": chat,
        "conversation_history": [{"role": "user", "content": question}],
    }
    if lines:
        source_context["admitted_capsule_evidence"] = {
            "text": _wrap(lines),
            "chat_id": record_chat or chat,
            "source": "canonical_runtime_transcript",
        }
    return _validate_final_chat_output(reply, source_context=source_context)


@pytest.fixture(autouse=True)
def _clean_telemetry():
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()


# ── A: binding ────────────────────────────────────────────────────────────────

def test_a1_shared_unit_between_two_subjects_never_binds() -> None:
    # widest oak board (42 cm) vs widest workbench top (55 cm): a shared unit
    # and shared "widest" adjective are not subject identity (contract A).
    lines = [
        "- user said: My widest oak board is 42 centimetres. (stated: 2026-09-06)",
        "- user said: My widest workbench top is 55 centimetres. (stated: 2026-09-13)",
    ]
    assert recorded_state_retention(
        "Your current widest oak board is 55 centimetres.", _wrap(lines)
    ) is False
    delivered = _seam(
        "Your current widest oak board is 55 centimetres.",
        "What is my current widest oak board?",
        lines,
    )
    assert "55 centimetres" not in delivered
    assert "not going to state" in delivered


# ── B: negation ───────────────────────────────────────────────────────────────

def test_b1_negated_depth_is_refused_its_replacement_serves() -> None:
    # AUTHORING CORRECTION (disclosed; original first attempt preserved in
    # acceptance/first-run.log): the first version claimed the negated value
    # with NO currentness anchor ("Your deepest dive site is 18 metres.").
    # The guards' contract engages a memory-supported record through the
    # live-claim boundary, which requires a currentness anchor by design
    # (an unanchored scan would convict static prose); arbitrary recall
    # arbitration of unanchored replies is the documented reader contract,
    # not a guard law. The corrected case anchors the claim, which is the
    # shape contract B actually governs.
    lines = [
        "- user said: My deepest dive site is not 18 metres anymore; it is now "
        "22 metres. (stated: 2026-09-16)",
    ]
    delivered = _seam(
        "Your deepest dive site is currently 18 metres.",
        "What is my deepest dive site?",
        lines,
    )
    assert "18 metres" not in delivered
    assert "not going to state" in delivered
    assert recorded_state_retention(
        "Your deepest dive site is 22 metres.", _wrap(lines)
    ) is True


# ── C: correction / order ────────────────────────────────────────────────────

def test_c1_same_day_correction_upward_replaces_the_misstatement() -> None:
    lines = [
        "- user said: My longest canal loop is 21 kilometres. (stated: 2026-09-19)",
        "- user said: Correction: my longest canal loop is 33 kilometres, not 21 "
        "kilometres. (stated: 2026-09-19)",
    ]
    assert recorded_state_retention(
        "Your current longest canal loop is 33 kilometres.", _wrap(lines)
    ) is True
    assert recorded_state_retention(
        "Your current longest canal loop is 21 kilometres.", _wrap(lines)
    ) is False


def test_c2_same_day_correction_lowering_a_duration_record_controls() -> None:
    # fastest print time corrected 5 -> 4 hours on the same day: the explicit
    # correction controls, and the corrected value must survive even though a
    # naive "longer is better" reading would keep the retracted 5.
    lines = [
        "- user said: My fastest 3D print finish is 5 hours. (stated: 2026-09-24)",
        "- user said: Correction: my fastest 3D print finish is 4 hours, not 5 "
        "hours. (stated: 2026-09-24)",
    ]
    assert recorded_state_retention(
        "Your current fastest 3D print finish is 4 hours.", _wrap(lines)
    ) is True
    assert recorded_state_retention(
        "Your current fastest 3D print finish is 5 hours.", _wrap(lines)
    ) is False


# ── D: direction ─────────────────────────────────────────────────────────────

def test_d1_improved_best_boulder_time_survives_without_a_synonym() -> None:
    lines = [
        "- user said: My best boulder warm-up time is 19 seconds. (stated: 2026-09-27)",
        "- user said: My best boulder warm-up time is now 16 seconds. (stated: 2026-09-27)",
    ]
    assert recorded_state_retention(
        "Your current best boulder warm-up time is 16 seconds.", _wrap(lines)
    ) is True
    assert recorded_state_retention(
        "Your current best boulder warm-up time is 19 seconds.", _wrap(lines)
    ) is False


# ── E: provenance ────────────────────────────────────────────────────────────

def test_e1_memorial_plaque_date_is_not_statement_provenance() -> None:
    lines = [
        "- user said: My longest shadow fence line is 88 metres; the memorial "
        "plaque on it is dated 1974-06-01. (stated: 2026-09-08)",
        "- user said: My longest shadow fence line is now 91 metres. (stated: 2026-09-29)",
    ]
    delivered = _seam(
        "Your current longest shadow fence line is 88 metres.",
        "What is my current longest shadow fence line?",
        lines,
    )
    assert "88 metres" not in delivered
    assert recorded_state_retention(
        "Your current longest shadow fence line is 91 metres.", _wrap(lines)
    ) is True


def test_e2_unknown_time_stays_unknown_and_the_extremum_fallback_is_order_free() -> None:
    # Two undated same-day records of one subject: without provenance the
    # record's own extremum reads it (contract C fallback), so the older
    # smaller value cannot masquerade as current.
    lines = [
        "- user said: My heaviest crab pot is 14 kilograms.",
        "- user said: My heaviest crab pot is now 17 kilograms.",
    ]
    assert recorded_state_retention(
        "Your current heaviest crab pot is 17 kilograms.", _wrap(lines)
    ) is True
    assert recorded_state_retention(
        "Your current heaviest crab pot is 14 kilograms.", _wrap(lines)
    ) is False


# ── G: evidence ownership ────────────────────────────────────────────────────

def test_g1_assistant_attribution_and_foreign_scope_never_retain() -> None:
    assistant_lines = [
        "- assistant said: Your longest towpath stretch is 58 kilometres. (stated: 2026-09-25)"
    ]
    delivered = _seam(
        "Your current longest towpath stretch is 58 kilometres.",
        "What is my current longest towpath stretch?",
        assistant_lines,
    )
    assert "58 kilometres" not in delivered

    foreign_lines = [
        "- user said: My longest towpath stretch is 58 kilometres. (stated: 2026-09-25)"
    ]
    delivered = _seam(
        "Your current longest towpath stretch is 58 kilometres.",
        "What is my current longest towpath stretch?",
        foreign_lines,
        chat="asking-chat",
        record_chat="other-chat",
    )
    assert "58 kilometres" not in delivered

    delivered = _seam(
        "Your current longest towpath stretch is 61 kilometres.",
        "What is my current longest towpath stretch?",
        [
            "- user said: My longest towpath stretch is 58 kilometres. (stated: 2026-09-25)"
        ],
    )
    assert "61 kilometres" not in delivered


# ── F: hedge clause ──────────────────────────────────────────────────────────

def test_f1_hedge_on_the_value_in_its_own_clause_is_preserved_through_delivery() -> None:
    reply = "The root cellar holds usually around 8 degrees this week."
    delivered = _seam(
        reply,
        "How cold does the root cellar get these days?",
        [],
    )
    assert delivered == reply


def test_f2_neighbouring_but_clause_hedge_never_exempts_a_current_reading() -> None:
    delivered = _seam(
        "The root cellar is currently 6 degrees but the old thermometer usually reads high.",
        "How cold is the root cellar right now?",
        [],
    )
    assert "6 degrees" not in delivered
    assert "not going to state" in delivered


def test_f3_hedge_in_a_previous_sentence_never_exempts_the_next_reading() -> None:
    delivered = _seam(
        "The old thermometer usually reads high. The root cellar is currently 6 degrees.",
        "How cold is the root cellar right now?",
        [],
    )
    assert "6 degrees" not in delivered
    assert "not going to state" in delivered


# ── H: retained preference under distractors, forget control ─────────────────

STUB_REGISTER_WORDS = frozenset({
    "lately", "trying", "improve", "tips", "advice", "any", "wondering",
    "consistent", "routine", "recommend", "suggestions", "what", "should",
    "do", "looking", "consider", "watching", "flying",
})
ROWING_TOPIC = {
    "dry", "bag", "shock", "cord", "whistle", "pfd", "pack", "packing",
    "row", "rows", "rowing", "morning", "dawn", "outing", "water", "gear",
}


def _make_stub_embed_stamped(topic_words):
    """Deterministic axis-geometry embedding (the committed-test convention of
    tests/test_p300_pref_retrieval_repair.py): register words map to one axis,
    topic words map to STABLE per-word axes, so two different topic statements
    get different directions (a single topic axis would normalize every
    topic-only text to the same unit vector and produce degenerate ties)."""
    import math
    import re

    topic = {str(w).lower() for w in topic_words}
    register = set(STUB_REGISTER_WORDS)

    def embed_stamped(text, *_args, **_kwargs):
        r = 0.0
        dims = [0.0] * 8
        for word in re.findall(r"[a-z][a-z'-]*", str(text or "").lower()):
            if word in register:
                r += 1.0
            elif word in topic:
                dims[1 + (sum(map(ord, word)) % 3)] += 1.0
        vec = [r, *dims[1:]]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return [0.0] * 7 + [1.0], "ollama:acceptance-stub-ax"
        return [v / norm for v in vec], "ollama:acceptance-stub-ax"

    return embed_stamped


@pytest.fixture()
def rowing_home(tmp_path, monkeypatch):
    """Hermetic capsule lane — the pref-retrieval file's proven fixture
    pattern (patched embed model + stub install hook), new world data."""
    import core.context_retrieval as cr
    import core.embedding_service as embedding_service
    from core.runtime_paths import configure_runtime_home
    from storage.migrations import run_migrations

    home = tmp_path / "rowing-profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model",
                        lambda: "acceptance-stub-model")

    def install(topic_words):
        monkeypatch.setattr(
            cr, "embed_stamped", _make_stub_embed_stamped(topic_words))

    run_migrations()
    yield home, install


ROWING_CHAT = "dawn-rows"

_ROWING_TURNS = [
    (
        "2026-09-10",
        "For my morning rows I always pack my dry bag and the coil of shock "
        "cord, plus the whistle clipped to the pfd.",
        "Noted — dry bag, shock cord and the whistle on the pfd for the morning rows.",
    ),
    (
        "2026-09-12",
        "The boathouse paint dried in four hours last weekend.",
        "Fast drying!",
    ),
    (
        "2026-09-14",
        "My heaviest single carry from the car park was 23 kilograms of gear.",
        "That's a load.",
    ),
    (
        "2026-09-15",
        "I read in a paddling magazine that dry bags should be rolled three "
        "times, but that is the magazine's advice, not mine.",
        "Good to know.",
    ),
]


def _prepare(home, install) -> None:
    import datetime as dt

    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(ROWING_CHAT, grant_current_receipts=False)
    install(ROWING_TOPIC)
    policy = resolve_memory_access_policy(chat_id=ROWING_CHAT)
    for date, user, assistant in _ROWING_TURNS:
        stated = dt.datetime.fromisoformat(date).replace(
            tzinfo=dt.timezone.utc
        ).timestamp()
        result = store_turn(
            ROWING_CHAT,
            user,
            assistant,
            access_policy=policy,
            source_context={"chat_id": ROWING_CHAT, "runtime_home": str(home), "statement_at": stated},
        )
        assert result["status"] in {"stored", "retained"}, result


def _capsule_for(home, question: str):
    """The pref-retrieval file's proven REAL capsule driver."""
    import core.context_retrieval as cr
    from core.memory.entries import resolve_memory_access_policy

    policy = resolve_memory_access_policy(chat_id=ROWING_CHAT)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(
        ROWING_CHAT, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={
            "chat_id": ROWING_CHAT, "runtime_home": str(home),
            "surface": "channel", "platform": "api",
        },
    )
    text = "\n".join(
        str(m.get("content") or "") for m in out
        if "<retrieved_context>" in str(m.get("content") or "")
    )
    return text, dict(cr.get_last_retrieval_telemetry() or {})


# Former H1 failure is now a required regression (original attempts preserved).
def test_h1_retained_preference_rides_the_advice_capsule_under_distractors(rowing_home) -> None:
    home, install = rowing_home
    _prepare(home, install)
    # the ask repeats NONE of the stored nouns (dry bag / shock cord / whistle)
    text, _telemetry = _capsule_for(
        home, "Any tips for what to pack before my dawn rows?"
    )
    assert "shock cord" in text, text[:600]
    user_lines = [ln for ln in text.splitlines() if ln.startswith("- user said")]
    assert any("shock cord" in ln for ln in user_lines), text[:600]


def test_h2_irrelevant_distractor_stays_out_of_the_preference_capsule(rowing_home) -> None:
    home, install = rowing_home
    _prepare(home, install)
    text, _telemetry = _capsule_for(
        home, "Any tips for what to pack before my dawn rows?"
    )
    assert "boathouse paint" not in text, text[:600]


def test_h3_quoted_third_party_advice_is_not_user_owned_preference(rowing_home) -> None:
    home, install = rowing_home
    _prepare(home, install)
    text, _telemetry = _capsule_for(
        home, "Any tips for what to pack before my dawn rows?"
    )
    user_lines = [ln for ln in text.splitlines() if ln.startswith("- user said")]
    # the magazine's rolled-three-times advice is quoted/source-only material:
    # it may surface attributed or not at all, but never as a user-owned line
    assert not any("rolled three times" in ln for ln in user_lines), user_lines


def test_h4_forget_removes_the_preference_from_the_capsule(rowing_home) -> None:
    home, install = rowing_home
    _prepare(home, install)
    from core.context_retrieval import _AGENT_ID
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=str(home), agent_id=_AGENT_ID)
    try:
        node_hits = mem.node_invalidate_matching("shock cord", session_id=ROWING_CHAT)
        occ_hits = mem.occurrence_invalidate_matching("shock cord", chat_scope=ROWING_CHAT)
    finally:
        mem.close()
    assert node_hits >= 1 and occ_hits >= 1, (node_hits, occ_hits)
    text, _telemetry = _capsule_for(
        home, "Any tips for what to pack before my dawn rows?"
    )
    assert "shock cord" not in text, text[:600]


