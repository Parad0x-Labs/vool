"""v14.6: a sequence ask ("in what order did I bring up X, Y and Z across our conversations") is a typed obligation
(kind sequence, class LIST_ALL): the user's dated records render earliest first, one per line, with coverage checked,
and the packet tells the reader the answer shape (one short item per line, in order, nothing bundled). Failure class
diagnosed on BEAM 100K/1's two spent event-ordering questions (bundled milestone lines matched no rubric event); the
cases here are authored, no BEAM wording. Contributor: sls_0x."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_compiler import obligations
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
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    packet = [l for l in block.split("\n") if l.startswith("- [") or l.startswith("- This question needs")]
    return packet, dict(cr.get_last_retrieval_telemetry())["evidence_compiler"]


@pytest.mark.parametrize("question", [
    "In what order did I bring up the greenhouse, the beehive and the rain barrel?",
    "Can you walk me through the order in which I mentioned the different parts of my garden project across our conversations?",
    "what order did i raise the greenhouse, beehive and rain barrel in",
    "Give me the sequence in which these topics came up: beehive, greenhouse, rain barrel.",
    "Chronologically, how did my garden project topics come up?",
    "the timeline of my garden project topics pls",
])
def test_a_sequence_ask_is_typed(question):
    ob = obligations(question)
    assert ob.kind == "sequence" and ob.cls == "LIST_ALL" and ob.needs_coverage, (question, ob)


@pytest.mark.parametrize("question", [
    "Which did I set up first, the beehive or the rain barrel?",   # two named things: order
    "When did I first mention the greenhouse?",
    "How many garden topics did I bring up?",
])
def test_a_two_item_order_or_a_single_date_is_not_a_sequence(question):
    assert obligations(question).kind != "sequence", question


def _garden(home, chat):
    _store(home, chat, "I'm planning a greenhouse for the back of the garden, about 3 by 4 metres.", _epoch(2025, 2, 3))
    _store(home, chat, "The beehive arrives next week; I ordered a Langstroth hive for the garden.", _epoch(2025, 3, 11))
    _store(home, chat, "Unrelated: my commute is 40 minutes each way.", _epoch(2025, 3, 20))
    _store(home, chat, "Now I'm adding a rain barrel to the garden to feed the greenhouse beds.", _epoch(2025, 4, 28))


def test_a_sequence_packet_renders_the_dated_records_earliest_first_with_the_shape_line(home):
    chat = "chat-garden-order"
    _garden(home, chat)
    packet, ec = _ask(home, chat, "In what order did I bring up the greenhouse, the beehive and the rain barrel in my garden project?")
    assert ec["obligation"]["kind"] == "sequence" and ec["complete"] is True, ec
    lines = [l for l in packet if l.startswith("- [")]
    days = [l[3:13] for l in lines]
    assert days == sorted(days), days
    assert "greenhouse" in lines[0] and "rain barrel" in lines[-1].lower()
    needs = [l for l in packet if l.startswith("- This question needs")][0]
    assert "one line per item" in needs and "earliest first" in needs and "nothing bundled" in needs
    assert "Coverage: exhaustive" in needs


def test_a_sequence_over_a_truncated_scope_is_partial(home):
    chat = "chat-garden-many"
    for i in range(22):
        _store(home, chat, f"Garden topic {i + 1}: I brought up the {['mulch', 'trellis', 'compost', 'pond'][i % 4]} for the garden on {1 + i} May.", _epoch(2025, 5, 1 + i))
    packet, ec = _ask(home, chat, "In what order did I bring up the garden topics?")
    assert ec["obligation"]["kind"] == "sequence" and ec["complete"] is False and not ec["coverage"]["exhaustive"]
    needs = [l for l in packet if l.startswith("- This question needs")][0]
    assert "Coverage: partial" in needs and "do not present the list as complete" in needs
    lines = [l for l in packet if l.startswith("- [")]
    assert "Garden topic 1:" in lines[0]   # earliest first even on a partial view
