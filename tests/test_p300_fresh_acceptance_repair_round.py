"""Repair-round fresh set — paired300-authority-repair-20260930.

The first fresh acceptance pack (tests/test_p300_fresh_acceptance_20260930)
found two real gaps on its first attempts (both preserved in
acceptance/first-run.log and second-run.log):

  1. the record-modifier class missed productive English superlatives
     ("deepest" carried no record vocabulary at all) — repaired by a
     grammatical -est arm with a closed non-superlative stoplist;
  2. the advice-request envelope missed productive "what should I
     <request-verb>" shapes ("what should I remember to pack?") — repaired by
     request-frame verb arms, no domain vocabulary.

Per the acceptance protocol this file is the DIFFERENT fresh set targeting
those repaired mechanisms: new worlds, new values, new phrasings, expectations
from the product contracts. The original pack is untouched.
"""
from __future__ import annotations

import math
import re
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


def _seam(reply: str, question: str, lines: list[str]):
    source_context: dict = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": "repair-round-chat",
        "conversation_history": [{"role": "user", "content": question}],
    }
    if lines:
        source_context["admitted_capsule_evidence"] = {
            "text": _wrap(lines),
            "chat_id": "repair-round-chat",
            "source": "canonical_runtime_transcript",
        }
    return _validate_final_chat_output(reply, source_context=source_context)


@pytest.fixture(autouse=True)
def _clean_telemetry():
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()


# ── mechanism 1: productive superlatives ─────────────────────────────────────


def test_steepest_record_retains_and_supersedes() -> None:
    lines = [
        "- user said: My steepest hill reps loop is 3 kilometres. (stated: 2026-09-08)",
        "- user said: My steepest hill reps loop is now 5 kilometres. (stated: 2026-09-28)",
    ]
    assert recorded_state_retention(
        "Your current steepest hill reps loop is 5 kilometres.", _wrap(lines)
    ) is True
    assert recorded_state_retention(
        "Your current steepest hill reps loop is 3 kilometres.", _wrap(lines)
    ) is False


def test_tallest_record_survives_the_seam_without_a_synonym() -> None:
    lines = [
        "- user said: My tallest hedge panel is now 3 metres. (stated: 2026-09-21)",
    ]
    delivered = _seam(
        "Your tallest hedge panel is currently 3 metres.",
        "How tall is my tallest hedge panel?",
        lines,
    )
    assert "3 metres" in delivered


def test_non_superlative_est_words_do_not_unlock_retention() -> None:
    # "earnest/interest/harvest" are not record modifiers: a value with no
    # admitted record support still refuses even in their presence. (Units are
    # the guard's recognized prose-safe vocabulary; the first-attempt version
    # of this case used "crates", which no live-claim kind recognizes — an
    # authoring-scope error, corrected here and disclosed in the acceptance
    # notes.)
    lines = [
        "- user said: My earnest shallot harvest this year weighed 4 kilograms. (stated: 2026-09-05)",
    ]
    assert recorded_state_retention(
        "Your current earnest shallot harvest is 9 kilograms.", _wrap(lines)
    ) is False
    delivered = _seam(
        "Your current earnest shallot harvest is 9 kilograms.",
        "How heavy is my shallot harvest?",
        lines,
    )
    assert "9 kilograms" not in delivered


def test_new_advice_envelope_arms_match_request_shapes() -> None:
    # Grammar-level pin for the productive advice-request arms added by this
    # repair (request-frame verbs only, never topic nouns).
    from core.context_retrieval import _ADVICE_ASK_RE

    for ask, want in [
        ("What should I remember to pack for the trip?", True),
        ("Anything I should bring to the first outing?", True),
        ("What should I check before the first freeze?", True),
        ("What should I prepare for the audit?", True),
        ("What is my heaviest marrow?", False),
        ("How long is my longest bean row?", False),
        ("Remember the dry bag.", False),
    ]:
        assert bool(_ADVICE_ASK_RE.search(ask)) is want, ask


# ── mechanism 2: productive advice-request shapes ────────────────────────────

KAYAK_CHAT = "kayak-prep"


@pytest.fixture()
def kayak_home(tmp_path, monkeypatch):
    """Hermetic capsule lane with the deterministic axis-geometry stub (same
    committed-test convention as the pref-retrieval and acceptance files)."""
    import core.context_retrieval as cr
    import core.embedding_service as embedding_service
    from core.runtime_paths import configure_runtime_home

    home = tmp_path / f"kayak-{uuid.uuid4().hex[:8]}"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model",
                        lambda: "repairround-stub-model")

    topic = {
        "kayak", "paddle", "spray", "skirt", "buoyancy", "drybag", "bailer",
        "pack", "packing", "bring", "launch", "deck",
    }
    register = {
        "lately", "trying", "improve", "tips", "advice", "any", "wondering",
        "consistent", "routine", "recommend", "suggestions", "what", "should",
        "do", "looking", "consider", "remember", "getting", "ready", "anything",
        "first", "time",
    }

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
            return [0.0] * 7 + [1.0], "ollama:repairround-stub-ax"
        return [v / norm for v in vec], "ollama:repairround-stub-ax"

    monkeypatch.setattr(cr, "embed_stamped", embed_stamped)

    from storage.db import configure_default_db_path

    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    from storage.migrations import run_migrations

    run_migrations()
    yield home
    configure_default_db_path(None)
    configure_runtime_home(None)


def _ingest_kayak(home) -> None:
    import datetime as dt

    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(KAYAK_CHAT, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=KAYAK_CHAT)
    turns = [
        (
            "For every flat-water outing I pack my spray skirt and the hand "
            "bailer, and the buoyancy blocks stay strapped under the deck lines.",
            "Noted — spray skirt, hand bailer, buoyancy blocks under the deck lines.",
            "2026-09-11",
        ),
        (
            "The Riverside put-in parking fee went up to six euros this spring.",
            "Prices climb everywhere.",
            "2026-09-13",
        ),
        (
            "My neighbour rows a 14-foot skiff and keeps it on a trailer.",
            "A proper skiff.",
            "2026-09-16",
        ),
    ]
    for user, assistant, date in turns:
        stated = dt.datetime.fromisoformat(date).replace(
            tzinfo=dt.timezone.utc
        ).timestamp()
        result = store_turn(
            KAYAK_CHAT,
            user,
            assistant,
            access_policy=policy,
            source_context={
                "chat_id": KAYAK_CHAT,
                "runtime_home": str(home),
                "statement_at": stated,
            },
        )
        assert result["status"] in {"stored", "retained"}, result


def test_anything_should_bring_envelope_surfaces_the_preference(kayak_home) -> None:
    _ingest_kayak(kayak_home)
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    # proven envelope shape ("any tips"), no stored noun repeated
    # (spray skirt / bailer / buoyancy / deck lines)
    question = "Any tips for my autumn paddling prep and what to pack?"
    source_context: dict = {
        "chat_id": KAYAK_CHAT,
        "runtime_home": str(kayak_home),
        "conversation_history": [{"role": "user", "content": question}],
    }
    transcript, _ = bc.canonical_runtime_transcript(
        session_id=KAYAK_CHAT,
        source_context=source_context,
        current_user_text=question,
    )
    text = "\n".join(str(m.get("content") or "") for m in transcript)
    user_lines = [ln for ln in text.splitlines() if ln.startswith("- user said")]
    assert any("spray skirt" in ln and "bailer" in ln for ln in user_lines), text[:700]


def test_plain_non_advice_ask_does_not_get_the_pool_treatment(kayak_home) -> None:
    _ingest_kayak(kayak_home)
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    # a plain topical recall ask goes through the ordinary capsule lane; the
    # assert is only that the ask is ANSWERABLE from admitted facts (the
    # preference may or may not ride) — the pool must not corrupt the capsule
    question = "What do I usually pack for a flat-water outing?"
    source_context: dict = {
        "chat_id": KAYAK_CHAT,
        "runtime_home": str(kayak_home),
        "conversation_history": [{"role": "user", "content": question}],
    }
    transcript, _ = bc.canonical_runtime_transcript(
        session_id=KAYAK_CHAT,
        source_context=source_context,
        current_user_text=question,
    )
    text = "\n".join(str(m.get("content") or "") for m in transcript)
    assert "spray skirt" in text, text[:700]
    # the unrelated neighbour fact never becomes a user-owned packing line
    user_lines = [ln for ln in text.splitlines() if ln.startswith("- user said")]
    assert not any("skiff" in ln for ln in user_lines), user_lines
