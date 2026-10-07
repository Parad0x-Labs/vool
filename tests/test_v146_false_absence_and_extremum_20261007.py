"""v14.6 hardening item 5 (ASTRA Pro review, 2026-10-07): when the right record sits below top-k, VOOL must not answer
"never", "the first" or "the latest" as fact. Packet level (served store, typed completeness) and guard level
(core.evidence_kernel.coverage_guard), no reader call. Contributor: sls_0x."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_kernel.coverage_guard import conclusion_kind, guard_coverage
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d, hh=9):
    return float(calendar.timegm(datetime(y, m, d, hh, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as es

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_CONTEXT_CAPSULE_V2", "VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile); es._best_embed_model = lambda: None
    from storage.migrations import run_migrations
    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, user_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False); policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, "Noted.", access_policy=policy, source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat); cr.reset_retrieval_telemetry()
    cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    return dict(cr.get_last_retrieval_telemetry())["evidence_compiler"]


def _many_kettles(home, chat, n=20):
    # the record that answers "did I ever" / "the first" is the oldest and shares the fewest terms, so lexical top-k drops it
    _store(home, chat, "The kettle I ordered, a Bodum, arrived on 1 February.", _epoch(2025, 2, 1))
    for i in range(1, n):
        _store(home, chat, f"Kettle note {i}: I descaled the kettle again on {1 + i} March and bought descaler.", _epoch(2025, 3, 1 + i))


# ─── guard unit ────────────────────────────────────────────────────────────────────────────────

PARTIAL = {"obligation": {"class": "ABSENCE", "needs_coverage": True}, "coverage": {"candidates": 20, "shown": 14, "exhaustive": False}}


@pytest.mark.parametrize("reply", ["You never mentioned ordering a kettle.", "There is no record of a kettle order.", "I can't find any mention of a kettle.", "You didn't say anything about a kettle."])
def test_a_never_on_a_partial_view_is_replaced_by_a_scoped_statement(reply):
    d = guard_coverage(reply=reply, compiler_telemetry=PARTIAL)
    assert d.changed and d.reason == "qualified_absence"
    assert "never" not in d.text.lower() and "14 of 20 matching records" in d.text and "not checked" in d.text


@pytest.mark.parametrize("reply", ["Your first kettle was the Bodum, ordered on 1 February.", "The earliest one I see is the Bodum.", "the latest kettle note is from 20 March"])
def test_a_first_or_latest_on_a_partial_view_is_scoped_not_stated(reply):
    tel = {"obligation": {"class": "EXTREMUM", "needs_coverage": True}, "coverage": {"candidates": 20, "shown": 14, "exhaustive": False}}
    d = guard_coverage(reply=reply, compiler_telemetry=tel)
    assert d.changed and d.reason == "qualified_extremum" and d.text.startswith(reply) and "not established as the first or the latest" in d.text


def test_a_list_on_a_partial_view_is_marked_incomplete():
    tel = {"obligation": {"class": "LIST_ALL", "needs_coverage": True}, "coverage": {"candidates": 30, "shown": 14, "exhaustive": False}}
    d = guard_coverage(reply="- Bodum\n- Breville\n- Smeg", compiler_telemetry=tel)
    assert d.changed and "may be incomplete" in d.text


def test_an_exhausted_scope_leaves_the_conclusion_alone():
    tel = {"obligation": {"class": "ABSENCE", "needs_coverage": True}, "coverage": {"candidates": 3, "shown": 3, "exhaustive": True}}
    d = guard_coverage(reply="You never mentioned a kettle.", compiler_telemetry=tel)
    assert not d.changed and d.reason == "scope_exhausted"


def test_a_clean_refusal_with_no_candidate_is_untouched():
    tel = {"obligation": {"class": "ABSENCE", "needs_coverage": True}, "coverage": {"candidates": 0, "shown": 0, "exhaustive": True}}
    d = guard_coverage(reply="I have no record of you moving to Berlin.", compiler_telemetry=tel)
    assert not d.changed


def test_a_class_that_needs_no_coverage_is_untouched():
    tel = {"obligation": {"class": "SINGLE", "needs_coverage": False}, "coverage": {"candidates": 20, "shown": 14, "exhaustive": False}}
    d = guard_coverage(reply="You never said that.", compiler_telemetry=tel)
    assert not d.changed and d.reason == "class_needs_no_coverage"


def test_a_reply_without_a_scope_wide_conclusion_is_untouched():
    d = guard_coverage(reply="The Bodum arrived on 1 February.", compiler_telemetry=PARTIAL)
    assert not d.changed and d.reason == "no_scope_wide_conclusion"


def test_a_reply_that_already_scopes_itself_is_not_qualified_twice():
    d = guard_coverage(reply="The records I can see do not settle whether you ever said that.", compiler_telemetry=PARTIAL)
    assert not d.changed


@pytest.mark.parametrize("cls, reply, kind", [("ABSENCE", "nothing about that in your notes", "absence"), ("EXTREMUM", "The oldest entry is March.", "extremum"), ("LIST_ALL", "1. a\n2. b", "list"), ("AGGREGATE", "That is 12 in total.", "aggregate"), ("EXISTS", "You never said it.", "")])
def test_conclusion_kinds(cls, reply, kind):
    assert conclusion_kind(reply, cls) == kind


# ─── packet level on the served store ──────────────────────────────────────────────────────────

def test_the_first_kettle_below_lexical_top_k_is_a_partial_view_and_the_edge_is_still_shown(home):
    chat = "chat-kettles"
    _many_kettles(home, chat)
    ec = _ask(home, chat, "What was the first kettle I ordered?")
    assert ec["obligation"]["class"] == "EXTREMUM" and ec["complete"] is False and not ec["coverage"]["exhaustive"]
    assert ec["coverage"]["candidates"] >= 20 > ec["coverage"]["shown"]
    d = guard_coverage(reply="Your first kettle was the Bodum.", compiler_telemetry=ec)
    assert d.changed and "not established as the first" in d.text


def test_a_did_i_ever_over_a_truncated_scope_cannot_become_never(home):
    chat = "chat-kettles-ever"
    _many_kettles(home, chat)
    ec = _ask(home, chat, "Have I never ordered a kettle?")
    assert ec["obligation"]["class"] == "ABSENCE" and not ec["coverage"]["exhaustive"]
    d = guard_coverage(reply="You never ordered a kettle.", compiler_telemetry=ec)
    assert d.changed and "never" not in d.text.lower()


def test_a_did_i_ever_over_a_small_scope_keeps_its_answer(home):
    chat = "chat-kettles-few"
    _store(home, chat, "The kettle I ordered, a Bodum, arrived on 1 February.", _epoch(2025, 2, 1))
    _store(home, chat, "I descaled the kettle on 3 March.", _epoch(2025, 3, 3))
    ec = _ask(home, chat, "Have I never ordered a kettle?")
    assert ec["coverage"]["exhaustive"]
    d = guard_coverage(reply="You did order one: the Bodum, which arrived on 1 February.", compiler_telemetry=ec)
    assert not d.changed
