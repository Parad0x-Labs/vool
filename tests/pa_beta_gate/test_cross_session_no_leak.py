"""pa_beta_gate — rapid multi-session cross-session no-leak regression (Codex round 4).

Fix A: a recall turn in session B must never surface session A/C's exact cap/domain via the
cross-session "Prior session continuity" injection. Model-free: asserts the assembled continuity
context for a session never carries another session's exact value; the wrong-cap model output in the
live rapid run is downstream of this.
"""
from __future__ import annotations

import pytest

from core import tiered_context_loader as tcl
from core.active_mission import capture_active_mission_slots
from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.context_scope import ContextAccessPolicy
from core.memory.entries import keyword_tokens_filtered
from core.memory.files import append_jsonl, session_summaries_path

pytestmark = [pytest.mark.pa_beta]

# (session_id, cap, domain) — distinct exact values per session; N >= 4 distinct ids.
SESSIONS = [
    ("openclaw:sess-A", "0.037 SOL", "alice.null"),
    ("openclaw:sess-B", "0.05 SOL", "parad0x.null"),
    ("openclaw:sess-C", "0.12 SOL", "bob.null"),
    ("openclaw:sess-D", "0.007 SOL", "carol.null"),
]
QUERY = "summarize the current mission spend cap and domain"
HINTS = ["mission", "cap", "spend", "domain", "sol"]


def _seed_summary(session_id, cap, domain, ts):
    ensure_chat_namespace(session_id)
    summary = (
        f"Session topics: cap, sol, mission, domain. "
        f"Recent asks: Update: the cap is now {cap}. Active domain {domain}."
    )
    append_jsonl(session_summaries_path(), {
        "created_at": ts, "session_id": session_id, "summary": summary,
        "keywords": keyword_tokens_filtered(summary), "turn_count": 3,
        "scope": "chat", "source": "session_summary", "status": "active",
        "origin_chat_id": session_id, "origin_project_id": "",
        "provenance": {"kind": "test_summary", "origin_chat_id": session_id},
    })


def _seed_all():
    for i, (sid, cap, domain) in enumerate(SESSIONS):
        _seed_summary(sid, cap, domain, f"2026-07-05T10:0{i}:00Z")


def _policy(session_id):
    return ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context={"surface": "openclaw"},
    )


def test_conflicting_other_session_values_never_leak_into_active_mission_turn():
    _seed_all()
    target_sid, target_cap, target_domain = SESSIONS[0]
    capture_active_mission_slots(target_sid, f"Active mission: cap {target_cap}, domain {target_domain}.")
    literals = tcl._current_mission_literals(target_sid)
    assert literals == {"spend_cap": target_cap, "active_domain": target_domain}

    items = tcl._session_summary_items(
        QUERY,
        HINTS,
        session_id=target_sid,
        mission_literals=literals,
        policy=_policy(target_sid),
    )
    blob = " ".join(i.content for i in items)
    # no OTHER session's exact cap or domain may appear
    for sid, cap, domain in SESSIONS[1:]:
        assert cap not in blob, f"{cap} from {sid} leaked into {target_sid}: {blob!r}"
        assert domain not in blob, f"{domain} from {sid} leaked into {target_sid}: {blob!r}"


def test_rapid_run_no_cross_session_leak_symmetric():
    _seed_all()
    for target_sid, target_cap, target_domain in SESSIONS:
        capture_active_mission_slots(target_sid, f"Active mission: cap {target_cap}, domain {target_domain}.")
        literals = tcl._current_mission_literals(target_sid)
        items = tcl._session_summary_items(
            QUERY,
            HINTS,
            session_id=target_sid,
            mission_literals=literals,
            policy=_policy(target_sid),
        )
        blob = " ".join(i.content for i in items)
        for other_sid, other_cap, other_domain in SESSIONS:
            if other_sid == target_sid:
                continue
            assert other_cap not in blob, f"{other_cap} leaked into {target_sid}"
            assert other_domain not in blob, f"{other_domain} leaked into {target_sid}"


def test_cross_session_continuity_requires_an_explicit_chat_import():
    # With no active mission for the current session, cross-session continuity is UNCHANGED — the
    # guard must not degrade normal continuity for ordinary recall turns.
    _seed_all()
    fresh = "openclaw:fresh"
    assert not tcl._session_summary_items(
        QUERY,
        HINTS,
        session_id=fresh,
        mission_literals=None,
        policy=_policy(fresh),
    )
    grant_context_import(
        fresh,
        scope="chat",
        source_id=f"chat:{SESSIONS[0][0]}",
    )
    items = tcl._session_summary_items(
        QUERY,
        HINTS,
        session_id=fresh,
        mission_literals=None,
        policy=_policy(fresh),
    )
    assert items
    blob = " ".join(item.content for item in items)
    assert SESSIONS[0][1] in blob


def test_matching_value_is_not_dropped():
    # A prior session whose summary states the SAME cap/domain as the current mission is not a
    # conflict and must be preserved.
    _seed_summary(
        "openclaw:same",
        "0.037 SOL",
        "alice.null",
        "2026-07-05T11:00:00Z",
    )
    capture_active_mission_slots("openclaw:me", "Active mission: cap 0.037 SOL, domain alice.null.")
    ensure_chat_namespace("openclaw:me")
    grant_context_import(
        "openclaw:me",
        scope="chat",
        source_id="chat:openclaw:same",
    )
    literals = tcl._current_mission_literals("openclaw:me")
    items = tcl._session_summary_items(
        QUERY,
        HINTS,
        session_id="openclaw:me",
        mission_literals=literals,
        policy=_policy("openclaw:me"),
    )
    blob = " ".join(i.content for i in items)
    assert "0.037 SOL" in blob  # same-value continuity kept
