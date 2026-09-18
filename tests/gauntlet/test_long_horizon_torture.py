"""Gauntlet — category 7: long-horizon torture (deterministic subset).

Proves the memory/continuity PLUMBING holds across length, with no live model:

  - an exact-value "mission" planted early survives a long run of intervening L3
    stores and is still recalled char-exact (retrieval, not summary, is the
    survival guarantee);
  - the live L3 store preserves an exact early value after heavy distraction;
  - dialogue continuity survives a multi-turn same-thread session, and multiple
    topic shifts archive newest-first.

The genuine torture the user wants — a live model still honoring a planted cap /
deadline / goal after 20+ real turns — is the gauntlet_live subset (not here); it
is the only place behavioural cap/deadline retention can be asserted, because there
is no typed cap/deadline slot (see README missing-capabilities).
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from core.fact_extractor import stable_text_embedding
from core.human_input_adapter import adapt_user_input
from core.vool_memory import VoolMemory
from core.persistent_memory import append_conversation_event
from storage.dialogue_memory import get_dialogue_session, recent_archived_dialogue_topics

pytestmark = [pytest.mark.gauntlet]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}
_MISSION = "The launch mission: wallet index 8829145, cap 0.037 SOL, domain alice.null, deadline 2026-07-15."


def _sid(label):
    return f"openclaw:{label}:{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# 1. Exact-value mission survives a long run of intervening L3 stores
# ---------------------------------------------------------------------------

def test_mission_survives_120_intervening_stores_char_exact():
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="torture", db_path=str(Path(tmp) / "m.db"))
        mem.node_store(
            content=_MISSION, keywords=["launch", "mission", "wallet", "cap", "alice.null", "deadline"],
            tags=["user"], context_description="mission", embedding=stable_text_embedding(_MISSION),
            timestamp=1_000_000.0,
        )
        for i in range(120):
            distractor = f"Distractor turn {i}: chatter about lunch, weather, and unrelated errands for the day."
            mem.node_store(
                content=distractor, keywords=distractor.lower().split(), tags=["user"],
                context_description=f"noise {i}", embedding=stable_text_embedding(distractor),
                timestamp=1_000_001.0 + i,
            )
        hits = mem.node_search_hybrid(
            "launch mission wallet cap alice.null deadline",
            stable_text_embedding("launch mission wallet cap alice.null deadline"),
            top_k=3, min_score=0.0,
        )
        mem.close()

    top = hits[0][0].content
    assert top == _MISSION  # recalled char-exact, top-ranked, after 120 distractors
    assert "8829145" in top and "0.037 SOL" in top and "alice.null" in top and "2026-07-15" in top


# ---------------------------------------------------------------------------
# 2. Dialogue continuity under a multi-turn session + multi-shift archiving
# ---------------------------------------------------------------------------

def test_dialogue_commitment_survives_several_same_thread_turns():
    sid = _sid("long-thread")
    adapt_user_input("Help me design the deployment pipeline for this Telegram bot.", session_id=sid)
    append_conversation_event(
        session_id=sid, user_input="Help me design the deployment pipeline for this Telegram bot.",
        assistant_output="I'll draft the pipeline stages next.", source_context=_OPENCLAW,
    )
    for filler in ("ok do that", "go on", "and then?", "continue", "what next?"):
        adapt_user_input(filler, session_id=sid)

    s = get_dialogue_session(sid)
    assert "telegram bot" in str(s.get("last_subject") or "").lower()


def test_multiple_topic_shifts_archive_newest_first():
    sid = _sid("multi-shift")
    topics = [
        ("Help me pick a database for the app.", "I'll compare options."),
        ("Now help me write the Dockerfile.", "I'll draft the Dockerfile."),
        ("Actually, plan the marketing launch.", "I'll outline the launch."),
    ]
    for user, assistant in topics:
        adapt_user_input(user, session_id=sid)
        append_conversation_event(
            session_id=sid, user_input=user, assistant_output=assistant, source_context=_OPENCLAW
        )
    # a final unrelated turn shifts off the last topic too
    adapt_user_input("What should I eat after lifting?", session_id=sid)

    archived = recent_archived_dialogue_topics(sid, limit=5)
    assert len(archived) >= 2
    # newest-first ordering: the most recently abandoned topic is at index 0
    assert "marketing" in str(archived[0].get("summary") or "").lower() or \
           "launch" in str(archived[0].get("summary") or "").lower()
