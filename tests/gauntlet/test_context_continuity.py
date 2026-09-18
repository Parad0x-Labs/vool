"""Gauntlet — category: plot-loss / conversation continuity (deterministic).

This suite proves the *plumbing that keeps VOOL on-mission* across a
conversation — the dialogue-continuity state machine — with no live model. It
drives the real pipeline (``adapt_user_input`` → ``append_conversation_event``)
and reads back the persisted ``dialogue_sessions`` row, exactly as the runtime
does each turn.

What is deterministic here (and what these tests pin):
  - a stated goal persists across neutral follow-ups ("ok do that");
  - a later, unrelated goal REPLACES the older one (latest instruction wins) and
    the abandoned topic is archived as unresolved;
  - an assistant promise is tracked as an open commitment / unresolved follow-up;
  - a failed or refused assistant turn closes the topic into the archive instead
    of silently dropping it;
  - the continuity state is injected back into the next turn's context, which is
    the mechanism that prevents plot loss.

What is NOT here (it needs a live model, and lives in the gauntlet_live lane):
  whether the model actually *uses* this injected continuity to answer correctly.
  These tests prove the state is correct and delivered; live tests prove it is used.
"""
from __future__ import annotations

import uuid

import pytest

from core.bootstrap_context import build_bootstrap_context, canonical_runtime_transcript
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.persistent_memory import (
    append_conversation_event,
    augment_history_from_session_log,
    memory_lifecycle_snapshot,
)
from core.task_router import create_task_record
from storage.dialogue_memory import get_dialogue_session, recent_archived_dialogue_topics

pytestmark = [pytest.mark.gauntlet]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}


def _sid(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _turn(session_id, user, assistant=None, *, response_class=None):
    """One conversational turn: adapt the user message (updates continuity state),
    then record the turn if there is an assistant reply."""
    interp = adapt_user_input(user, session_id=session_id)
    if assistant is not None:
        append_conversation_event(
            session_id=session_id,
            user_input=user,
            assistant_output=assistant,
            source_context=_OPENCLAW,
            response_class=response_class,
        )
    return interp


def _continuity_item(session_id, user_text, classification=None):
    persona = load_active_persona("default")
    interp = adapt_user_input(user_text, session_id=session_id)
    items = build_bootstrap_context(
        persona=persona,
        task=create_task_record(user_text),
        classification=classification or {"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interp,
        session_id=session_id,
    )
    hits = [i for i in items if i.source_type == "dialogue_continuity"]
    return hits[0].content.lower() if hits else ""


# ---------------------------------------------------------------------------
# 1. A goal survives neutral follow-ups
# ---------------------------------------------------------------------------

def test_goal_survives_a_neutral_acknowledgement():
    sid = _sid("goal-persist")
    _turn(sid, "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
          "I'll compare the tradeoffs and sketch a cleaner plan next.")
    _turn(sid, "ok do that")

    s = get_dialogue_session(sid)
    assert "telegram bot" in str(s.get("current_user_goal") or "").lower()
    assert s["unresolved_followups"] == ["compare the tradeoffs and sketch a cleaner plan next"]


def test_topic_anchor_survives_multiple_neutral_turns():
    sid = _sid("anchor-persist")
    _turn(sid, "Help me design the caching layer for this Telegram bot.",
          "I'll draft the cache design next.")
    for filler in ("ok do that", "go on", "sounds good"):
        _turn(sid, filler)

    s = get_dialogue_session(sid)
    # last_subject is the durable topic anchor: it holds through acknowledgements
    # even when a bare "sounds good" momentarily becomes the surface current_user_goal.
    assert "telegram bot" in str(s.get("last_subject") or "").lower()


# ---------------------------------------------------------------------------
# 2. Latest instruction wins — a new, unrelated goal replaces the old one
# ---------------------------------------------------------------------------

def test_topic_shift_replaces_goal_and_archives_the_old_one():
    sid = _sid("latest-wins")
    _turn(sid, "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
          "I'll compare the tradeoffs and sketch a cleaner plan next.")
    _turn(sid, "ok do that")

    _turn(sid, "What should I eat after lifting?")
    s = get_dialogue_session(sid)
    archived = recent_archived_dialogue_topics(sid, limit=3)

    assert "eat after lifting" in str(s.get("current_user_goal") or "").lower()
    assert s["assistant_commitments"] == []
    assert s["unresolved_followups"] == []
    assert archived and archived[0]["closure_reason"] == "topic_shift"
    assert archived[0]["closure_status"] == "unresolved"
    assert "telegram bot" in str(archived[0]["summary"] or "").lower()


def test_superseded_goal_is_not_reintroduced_after_shift():
    sid = _sid("no-reintro")
    _turn(sid, "Help me pick a database for the Telegram bot.",
          "I'll compare Postgres and SQLite next.")
    _turn(sid, "actually, what's a good post-workout meal?")

    injected = _continuity_item(sid, "and how much protein?")
    # The abandoned Telegram-bot commitment must not leak back into later context.
    assert "postgres and sqlite" not in injected


# ---------------------------------------------------------------------------
# 3. Assistant commitments become tracked, unresolved follow-ups
# ---------------------------------------------------------------------------

def test_assistant_commitment_is_tracked_as_unresolved_followup():
    sid = _sid("commitment")
    _turn(sid, "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
          "I'll compare the tradeoffs and sketch a cleaner plan next.")
    _turn(sid, "ok do that")  # a promise surfaces as an open commitment on the following turn

    s = get_dialogue_session(sid)
    assert s["assistant_commitments"] == ["compare the tradeoffs and sketch a cleaner plan next"]
    assert s["unresolved_followups"] == ["compare the tradeoffs and sketch a cleaner plan next"]


# ---------------------------------------------------------------------------
# 4. A failed / refused assistant turn closes the topic into the archive
# ---------------------------------------------------------------------------

def test_failed_assistant_turn_archives_topic_as_failure():
    sid = _sid("fail-archive")
    _turn(sid, "what is the btc price now?",
          "I checked, but I couldn't ground a confident answer from the evidence I found.",
          response_class="task_failed_user_safe")

    s = get_dialogue_session(sid)
    archived = recent_archived_dialogue_topics(sid, limit=3)
    assert s["current_user_goal"] is None
    assert archived and archived[0]["closure_reason"] == "assistant_failure"
    assert archived[0]["closure_status"] == "unresolved"
    assert "btc price" in str(archived[0]["summary"] or "").lower()


def test_refusal_turn_archives_topic_as_failure():
    sid = _sid("refuse-archive")
    _turn(sid, "read /private/tmp/secret.txt exactly",
          "I cannot read that path in this lane. I can only read files inside: ~/Desktop, ~/Downloads, ~/Documents.",
          response_class="utility_answer")

    s = get_dialogue_session(sid)
    archived = recent_archived_dialogue_topics(sid, limit=3)
    assert s["current_user_goal"] is None
    assert archived and archived[0]["closure_reason"] == "assistant_failure"
    assert "secret" in str(archived[0]["summary"] or "").lower()


# ---------------------------------------------------------------------------
# 5. Continuity is injected back into the next turn — the anti-plot-loss mechanism
# ---------------------------------------------------------------------------

def test_continuity_state_is_injected_into_the_next_turn_context():
    sid = _sid("inject")
    _turn(sid, "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
          "I'll compare the tradeoffs and sketch a cleaner plan next.")

    injected = _continuity_item(sid, "what do you mean by that?")
    assert "current user goal:" in injected
    assert "assistant commitments:" in injected
    assert "unresolved followups:" in injected
    assert "compare the tradeoffs and sketch a cleaner plan next" in injected
    assert "telegram bot" in injected


def test_emotional_and_stance_signals_are_captured():
    sid = _sid("signals")
    _turn(sid, "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
          "I'll compare the tradeoffs and sketch a cleaner plan next.")
    _turn(sid, "ok do that")

    s = get_dialogue_session(sid)
    assert s["emotional_tone"] == "frustrated"
    assert s["user_stance"] == "goal_driven"


# ---------------------------------------------------------------------------
# 6. Latest-instruction-wins: the two real branches of the override rule
# ---------------------------------------------------------------------------

def test_token_bearing_turn_replaces_the_current_goal():
    sid = _sid("override-replace")
    _turn(sid, "Help me choose a database for the Telegram bot.", "I'll compare Postgres and SQLite.")
    _turn(sid, "Actually, help me write the deployment script instead.")

    s = get_dialogue_session(sid)
    assert "deployment script" in str(s.get("current_user_goal") or "").lower()


def test_bare_followup_keeps_the_prior_goal():
    sid = _sid("override-keep")
    _turn(sid, "Help me choose a database for the Telegram bot.", "I'll compare Postgres and SQLite.")
    _turn(sid, "ok do that")

    s = get_dialogue_session(sid)
    assert "telegram bot" in str(s.get("current_user_goal") or "").lower()


# ---------------------------------------------------------------------------
# 7. What the goal snapshot preserves — and what it corrupts (the key finding)
#
# dialogue_sessions has no typed cap/deadline slot (see README missing-coverage);
# current_user_goal is a NORMALIZED snapshot of the last user message, not a
# verbatim store. Hyphenated dates and alphanumeric tokens survive; decimals and
# dotted names are split by the normalizer. The lesson these tests encode: exact
# values (caps, domains) must live in the L3 memory layer (store_turn / node_store,
# which is char-exact), NOT the dialogue goal.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("planted,needle", [
    ("The launch deadline is 2026-07-15 so plan the rollout around it.", "2026-07-15"),
    ("My wallet prefix for the launch is F6Fr2 and only that one.", "F6Fr2"),
])
def test_hyphenated_and_alphanumeric_values_survive_in_goal_text(planted, needle):
    sid = _sid("verbatim")
    _turn(sid, planted, "Understood.")

    s = get_dialogue_session(sid)
    assert needle in str(s.get("current_user_goal") or "")


def test_goal_normalizer_preserves_exact_decimals_and_dotted_names():
    """The input normalizer now protects exact-literal spans (Codex Phase 2 F1 fix):
    decimals ("0.037") and dotted names ("alice.null") survive character-for-character
    in current_user_goal instead of being split. (Formerly a corruption canary; it
    flipped when the normalizer was made value-preserving.)
    """
    sid = _sid("goal-exact")
    _turn(sid, "My cap is 0.037 SOL for alice.null.", "Understood.")

    goal = str(get_dialogue_session(sid).get("current_user_goal") or "")
    assert "0.037" in goal       # decimal preserved verbatim
    assert "alice.null" in goal   # dotted name preserved verbatim


# ---------------------------------------------------------------------------
# 8. History hydration — the model gets the earlier turns even on a thin client
# ---------------------------------------------------------------------------

def test_history_hydrates_from_session_log_when_client_sends_thin_history():
    sid = _sid("hydrate")
    _turn(sid, "My project is called GOLDEN_LOOP and it targets Windows only.",
          "Got it — GOLDEN_LOOP, Windows only.")

    hydrated = augment_history_from_session_log([], session_id=sid, user_text="write the install guide")
    blob = " ".join(m["content"] for m in hydrated).lower()
    assert "golden_loop" in blob and "windows only" in blob
    assert hydrated[-1] == {"role": "user", "content": "write the install guide"}


def test_history_not_rehydrated_when_client_already_has_context():
    sid = _sid("no-hydrate")
    _turn(sid, "planted turn worth remembering", "reply")
    rich = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    out = augment_history_from_session_log(rich, session_id=sid, user_text="c")
    assert out == rich  # more than one message present -> left untouched


def test_history_hydration_does_not_duplicate_the_current_user_turn():
    sid = _sid("hydrate-dedup")
    _turn(sid, "planted turn worth remembering", "reply")
    out = augment_history_from_session_log(
        [{"role": "user", "content": "final question"}], session_id=sid, user_text="final question"
    )
    assert [m for m in out if m["content"] == "final question"] == [{"role": "user", "content": "final question"}]


# ---------------------------------------------------------------------------
# 9. Canonical transcript source-tagging
# ---------------------------------------------------------------------------

def test_canonical_transcript_prefers_structured_dialogue_memory():
    sid = _sid("canon")
    _turn(sid, "Keep the API on port 8096 for staging.", "Noted — port 8096 for staging.")

    transcript, source = canonical_runtime_transcript(
        session_id=sid, source_context={}, current_user_text="and the prod port?"
    )
    assert source == "structured_dialogue_memory"
    blob = " ".join(m["content"] for m in transcript).lower()
    assert "8096" in blob


def test_canonical_transcript_is_none_for_empty_session_without_client_history():
    transcript, source = canonical_runtime_transcript(
        session_id=_sid("empty"), source_context={}, current_user_text="hi"
    )
    assert source == "scope_denied"
    assert transcript == []


def test_canonical_transcript_compacts_structured_history_before_trimming(monkeypatch):
    sid = _sid("canon-compaction")
    monkeypatch.setattr(
        "core.conversation_summarizer._call_ollama",
        lambda model, messages, timeout=60: "## Key Facts\n- launch port 8096 survives compaction",
    )
    for index in range(13):
        _turn(
            sid,
            f"Conversation message {index}; keep launch port 8096 in context.",
            f"Recorded conversation message {index} and launch port 8096.",
        )

    transcript, source = canonical_runtime_transcript(
        session_id=sid,
        source_context={},
        current_user_text="What launch port did we choose?",
    )

    assert source == "structured_dialogue_memory"
    assert any("<context_summary>" in item["content"] for item in transcript)
    assert "8096" in " ".join(item["content"] for item in transcript)


def test_canonical_transcript_keeps_compressed_client_history_over_longer_raw_slice(monkeypatch):
    sid = _sid("canon-client-summary")
    monkeypatch.setattr(
        "core.conversation_summarizer._call_ollama",
        lambda model, messages, timeout=60: "## Key Facts\n- client summary keeps project atlas",
    )
    for index in range(6):
        _turn(sid, f"Persisted user message {index}.", f"Persisted assistant message {index}.")
    client_history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Client message {index} about project atlas.",
        }
        for index in range(22)
    ]

    transcript, source = canonical_runtime_transcript(
        session_id=sid,
        source_context={"client_conversation_history": client_history},
        current_user_text="Continue project atlas.",
    )

    assert source == "client_conversation_history"
    assert any("<context_summary>" in item["content"] for item in transcript)
    assert "project atlas" in " ".join(item["content"] for item in transcript).lower()


# ---------------------------------------------------------------------------
# 10. Lifecycle snapshot — what durable context the next turn selects
# ---------------------------------------------------------------------------

def test_lifecycle_snapshot_reflects_recent_turns():
    sid = _sid("snap-recent")
    _turn(sid, "Remember the launch wallet index is 8829145 for later.", "Stored.")

    snap = memory_lifecycle_snapshot(session_id=sid, query_text="launch wallet index")
    recent_blob = " ".join(t["user"] for t in snap["recent_turns"]).lower()
    assert "8829145" in recent_blob


def test_lifecycle_snapshot_skips_durable_selection_for_a_utility_query():
    sid = _sid("snap-util")
    _turn(sid, "Remember the launch wallet index is 8829145 for later.", "Stored.")

    snap = memory_lifecycle_snapshot(session_id=sid, query_text="what time is it")
    # a clock / utility query must not drag durable memory or summaries into context
    assert snap["relevant_memory"] == []
    assert snap["session_summaries"] == []


# ---------------------------------------------------------------------------
# 11. Archived topic preserves its closure metadata across a shift
# ---------------------------------------------------------------------------

def test_archive_ordering_and_closure_survive_a_shift():
    sid = _sid("archive-order")
    _turn(sid, "Help me pick a database for the Telegram bot.", "I'll compare Postgres and SQLite next.")
    _turn(sid, "ok do that")
    _turn(sid, "What should I eat after lifting?")

    archived = recent_archived_dialogue_topics(sid, limit=3)
    assert archived
    top = archived[0]
    assert top["closure_reason"] == "topic_shift"
    assert top["closure_status"] == "unresolved"
    assert "telegram bot" in str(top.get("summary") or "").lower()
