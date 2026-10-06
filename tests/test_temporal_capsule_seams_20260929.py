"""Temporal seams end-to-end: turn times into the store, the capsule
composed from the real candidate set, receipts that expose the actual date
metadata and selected source occurrence ids — never a bare date token.

Rows run against a real per-test VoolMemory profile through the production
seams (store_turn / append_conversation_event / inject_retrieved). The
deterministic hash-BoW backend is forced for reproducibility; the fresh
domains differ from every corpus case (a bindery, a shrimp dock, a ski
waxing cabin, a bell-foundry visiting lane).

The retraction row is a TRUE two-process proof: the writer subprocess exits
before the reader subprocess opens the store, so the retraction surviving
into the reader's capsule demonstrates persistence, not process state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parents[1]

UTC = timezone.utc


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "home"
    profile.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        VOOL_HOME=str(profile),
        VOOL_WORKSPACE_ROOT=str(profile / "workspace"),
    )
    return profile


@pytest.fixture(autouse=True)
def _hash_backend():
    from core import embedding_service

    original = embedding_service._best_embed_model
    embedding_service._best_embed_model = lambda: None
    yield
    embedding_service._best_embed_model = original


def _ingest_live(profile: Path, chat: str, turns: list[dict]) -> None:
    """Write finalized turns through the LIVE seam with explicit source
    times, exactly as a caller that knows when the turn happened does."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import append_conversation_event

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for turn in turns:
        append_conversation_event(
            session_id=chat,
            user_input=turn.get("user", ""),
            assistant_output=turn.get("assistant", ""),
            source_context={
                "surface": "api", "platform": "api", "chat_id": chat,
                "runtime_home": str(profile),
                "statement_at": turn.get("statement_at"),
                "event_at": turn.get("event_at"),
            },
            access_policy=policy,
        )


def _capsule(profile: Path, chat: str, question: str, *, as_of=None, mode="v2"):
    from core.context_retrieval import inject_retrieved
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    messages = inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={
            "chat_id": chat, "runtime_home": str(profile),
            "question_as_of": as_of,
        },
        env={"VOOL_CONTEXT_CAPSULE_V2": "1" if mode == "v2" else "0"},
    )
    capsule = next(
        (str(m.get("content") or "") for m in messages
         if m.get("role") == "system"
         and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )
    from core.context_retrieval import get_last_retrieval_telemetry

    return capsule, get_last_retrieval_telemetry()


# ------------------------------------------------------------------ seams


def test_store_turn_carries_statement_and_event_times(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "bindery", [
        {"user": "The bindery's adhesive batch is B-214, mixed today.",
         "assistant": "Batch B-214 logged.",
         "statement_at": "2026-02-11T09:30:00"},
    ])
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=str(profile))
    try:
        rows = mem._conn.execute(
            "SELECT role, statement_at, event_at, recorded_at, body "
            "FROM source_occurrences WHERE chat_scope = 'bindery'"
        ).fetchall()
    finally:
        mem.close()
    by_role = {r["role"]: r for r in rows}
    assert by_role["user"]["statement_at"] == pytest.approx(
        datetime(2026, 2, 11, 9, 30, tzinfo=UTC).timestamp()
    )
    # the event time was not claimed by this turn — it stays unknown
    assert by_role["user"]["event_at"] is None
    assert by_role["assistant"]["statement_at"] == by_role["user"]["statement_at"]


def test_capsule_current_ask_serves_correction_with_true_date_and_ids(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "shrimp-dock", [
        {"user": "The shrimp dock's ice window opens at 05:50.",
         "assistant": "05:50 ice window — logged.",
         "statement_at": "2026-07-02T06:00:00"},
        {"user": "Correction — the ice window opens at 06:10 now.",
         "assistant": "06:10 it is.", "statement_at": "2026-07-16T06:00:00"},
    ])
    capsule, telemetry = _capsule(
        profile, "shrimp-dock", "When does the ice window open these days?"
    )
    assert "06:10" in capsule
    assert "05:50" not in capsule
    # The receipt exposes the ACTUAL date metadata and the selected source
    # occurrence ids, not any date token: the delivered line's statement_at
    # is the correction's July 16 instant and its occurrence id exists in
    # the store.
    receipts = [
        r for r in (telemetry.get("evidence_refs") or [])
        if r.get("delivered") and r.get("statement_at") is not None
    ]
    assert receipts, telemetry
    delivered_ids = {r["occurrence_id"] for r in receipts}
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=str(profile))
    try:
        stored = {
            r["occurrence_id"]: r for r in mem._conn.execute(
                "SELECT occurrence_id, statement_at, body FROM source_occurrences "
                "WHERE chat_scope = 'shrimp-dock'"
            ).fetchall()
        }
    finally:
        mem.close()
    assert delivered_ids <= set(stored)
    correction_stamps = {
        r["occurrence_id"] for r in receipts
        if r["statement_at"] == pytest.approx(
            datetime(2026, 7, 16, 6, 0, tzinfo=UTC).timestamp())
    }
    assert correction_stamps
    assert any("06:10" in str(stored[oid]["body"]) for oid in correction_stamps)


def test_capsule_as_of_ask_serves_the_value_that_applied(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "wax-cabin", [
        {"user": "Ski wax service at the cabin costs 18 francs.",
         "assistant": "18 francs — noted.", "statement_at": "2025-12-04T10:00:00"},
        {"user": "Wax service goes to 24 francs for the new season.",
         "assistant": "24 francs — noted.", "statement_at": "2026-03-02T10:00:00"},
        {"user": "One more change: wax service is 27 francs from now.",
         "assistant": "27 francs — noted.", "statement_at": "2026-06-15T10:00:00"},
    ])
    capsule, _ = _capsule(
        profile, "wax-cabin",
        "As of April 20, what did the wax service cost?",
        as_of="2026-04-20",
    )
    assert "24 francs" in capsule
    assert "18 francs" not in capsule
    assert "27 francs" not in capsule
    # the true statement date rides with the winning value
    assert "2026-03-02" in capsule


def test_capsule_window_expiry_and_reversion_pair(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "visitors-lane", [
        {"user": "A foundry visiting lane ticket is 9 pounds.",
         "assistant": "9 pounds — noted.", "statement_at": "2026-01-06T11:00:00"},
        {"user": "Open weekend: the visiting lane is free of charge from May 9 through May 11.",
         "assistant": "Free weekend — logged.", "statement_at": "2026-05-07T11:00:00"},
        {"user": "Open weekend over — normal tickets are back.",
         "assistant": "Normal tickets — noted.", "statement_at": "2026-05-12T11:00:00"},
    ])
    capsule, _ = _capsule(
        profile, "visitors-lane",
        "What does a visiting lane ticket cost?",
    )
    assert "9 pounds" in capsule
    assert "free of charge" not in capsule
    assert "normal tickets are back" in capsule


def test_decimal_value_survives_the_evidence_span(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "grain-loft", [
        {"user": "The loft's threshing record shows 2.75 tonnes this year.",
         "assistant": "2.75 tonnes — logged.", "statement_at": "2026-09-03T15:00:00"},
    ])
    capsule, _ = _capsule(
        profile, "grain-loft", "How many tonnes did the threshing record show?"
    )
    assert "2.75" in capsule


def test_legacy_path_does_not_serve_the_superseded_value(tmp_path) -> None:
    profile = _profile(tmp_path)
    _ingest_live(profile, "net-loft", [
        {"user": "The net loft's drying slot is 40 minutes.",
         "assistant": "40 minutes — logged.", "statement_at": "2026-04-02T08:00:00"},
        {"user": "Correction — the drying slot is 55 minutes now.",
         "assistant": "55 minutes — noted.", "statement_at": "2026-04-18T08:00:00"},
    ])
    capsule, _ = _capsule(
        profile, "net-loft", "How long is the drying slot?", mode="legacy",
    )
    assert "55 minutes" in capsule
    assert "40 minutes" not in capsule


# ------------------------------------------------- two-process restart row

_RETRACT_WRITER = r"""
import json, os, sys
sys.path.insert(0, {tree!r})
os.environ.update(VOOL_HOME={home!r}, VOOL_HOME={home!r},
                  VOOL_WORKSPACE_ROOT={home_wr!r})
from core.runtime_paths import configure_runtime_home
configure_runtime_home({home!r})
from storage.migrations import run_migrations
run_migrations()
from core import embedding_service
embedding_service._best_embed_model = lambda: None
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import append_conversation_event
chat = 'loom-shed'
ensure_chat_namespace(chat, grant_current_receipts=False)
policy = resolve_memory_access_policy(chat_id=chat)
append_conversation_event(
    session_id=chat,
    user_input='The loom-shed open day will feature the 1890 Jacquard demo, announce it.',
    assistant_output='Jacquard demo announcement drafted.',
    source_context={{'surface': 'api', 'platform': 'api', 'chat_id': chat,
                     'runtime_home': {home!r},
                     'statement_at': '2026-08-04T09:00:00'}},
    access_policy=policy,
)
append_conversation_event(
    session_id=chat,
    user_input='Scratch that entirely — the Jacquard demo is cancelled until the restorer returns.',
    assistant_output='Cancellation recorded.',
    source_context={{'surface': 'api', 'platform': 'api', 'chat_id': chat,
                     'runtime_home': {home!r},
                     'statement_at': '2026-08-12T09:00:00'}},
    access_policy=policy,
)
print(json.dumps({{'written': True}}))
"""

_RETRACT_READER = r"""
import json, os, sys
sys.path.insert(0, {tree!r})
os.environ.update(VOOL_HOME={home!r}, VOOL_HOME={home!r},
                  VOOL_WORKSPACE_ROOT={home_wr!r}, VOOL_CONTEXT_CAPSULE_V2='1')
from core.runtime_paths import configure_runtime_home
configure_runtime_home({home!r})
from core import embedding_service
embedding_service._best_embed_model = lambda: None
from core.context_retrieval import inject_retrieved
chat = 'loom-shed'
question = 'Which demo are we featuring at the open day?'
messages = inject_retrieved(
    chat, question, [{{'role': 'user', 'content': question}}],
    source_context={{'chat_id': chat, 'runtime_home': {home!r}}},
)
capsule = next((str(m.get('content') or '') for m in messages
                if m.get('role') == 'system'
                and '<retrieved_context>' in str(m.get('content') or '')), '')
print(json.dumps({{'capsule': capsule}}))
"""


def test_retraction_survives_a_true_process_restart(tmp_path) -> None:
    profile = tmp_path / "restart-home"
    profile.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        VOOL_HOME=str(profile),
        VOOL_WORKSPACE_ROOT=str(profile / "workspace"),
    )
    writer = subprocess.run(
        [sys.executable, "-c", _RETRACT_WRITER.format(
            tree=str(TREE), home=str(profile),
            home_wr=str(profile / "workspace"))],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert writer.returncode == 0, writer.stderr
    assert json.loads(writer.stdout)["written"] is True
    # the writer has EXITED; the reader is a fresh process
    reader = subprocess.run(
        [sys.executable, "-c", _RETRACT_READER.format(
            tree=str(TREE), home=str(profile),
            home_wr=str(profile / "workspace"))],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert reader.returncode == 0, reader.stderr
    capsule = json.loads(reader.stdout)["capsule"]
    assert "Jacquard" in capsule or "cancelled" in capsule or "cancellation" in capsule
    assert "1890 Jacquard demo, announce" not in capsule
    assert "announce it" not in capsule
    # the retraction is the statement of record, with its true date
    assert "Scratch that" in capsule
    assert "2026-08-12" in capsule


# ───────────── cross-lane regression rows (lead decision D8) ──────────────


@pytest.fixture()
def composition_env(tmp_path, monkeypatch):
    """Delegates to composition's own env fixture so these rows run against
    exactly the harness composition measures with."""
    import tests.test_composition_evidence_packing as composition_module

    original = composition_module.env
    maker = original.__wrapped__ if hasattr(original, "__wrapped__") else original
    yield maker(tmp_path, monkeypatch)

# The three composition counterexamples that reverted the first temporal
# integration, restated against the same seams this file exercises. They run
# against composition's own fixture; if that module moves, these rows follow
# it rather than silently passing on a stale copy.

def test_cross_lane_mixed_subjects_do_not_cross_pollinate(composition_env) -> None:
    chat = composition_env.build("t-mixedfarm", [
        ("user", "Duck tally: 14 ducks on the pond."),
        ("user", "Goose tally: 6 geese by the gate."),
    ])
    capsule = composition_env.ask(chat, "How many ducks are on the farm in total?")
    assert "14" in capsule, capsule
    assert "= 20" not in capsule and "6 geese" not in capsule, capsule


def test_cross_lane_recency_sensitive_question_keeps_strict_law(composition_env) -> None:
    chat = composition_env.build("t-ferry-tight", [
        ("user", "The night ferry departs at 23:10 from the east quay."),
        ("user", "Correction for the timetable: the night ferry now departs at 23:40, the 23:10 slot went to the freight run."),
    ])
    capsule = composition_env.ask(chat, "When does the night ferry depart?")
    assert not ("23:10" in capsule and "23:40" in capsule), capsule


def test_cross_lane_duplicate_occurrence_not_double_counted(composition_env) -> None:
    chat = composition_env.build("t-seedvault", [
        ("user", "Inventory: 22 packets of kale seed in drawer A."),
        ("user", "Inventory: 22 packets of kale seed in drawer A."),
    ])
    capsule = composition_env.ask(chat, "How many kale packets are in drawer A in total?")
    assert "44" not in capsule, capsule
