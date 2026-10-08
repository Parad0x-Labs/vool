"""Tiered delivery rechecks real stored derivatives after their context rows were assembled."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

import core.context_retrieval as cr
import core.runtime_paths as runtime_paths
import core.tiered_context_loader as tiered
from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.identity_manager import load_active_persona
from core.memory.entries import resolve_memory_access_policy
from core.memory.files import session_summaries_path
from core.persistent_memory import maybe_handle_memory_command
from core.provenance_store import load_manifest
from core.task_router import classify, create_task_record
from core.vool_memory import VoolMemory
from storage.dialogue_memory import record_dialogue_turn
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile
from tests.test_tiered_context_loader import _interpretation

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.fixture
def runtime_profile(tmp_path):
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    profile = _profile(tmp_path)
    runtime_paths.configure_runtime_home(profile)
    try:
        assert runtime_paths.active_vool_home() == profile
        yield profile
    finally:
        runtime_paths.configure_runtime_home(previous)


def _forget(profile, chat):
    handled, reply = maybe_handle_memory_command(
        "forget MINT-84", session_id=chat, access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"surface": "api", "platform": "api", "chat_id": chat, "runtime_home": str(profile),
                        "client_turn_id": "tiered-forget-turn"},
    )
    assert handled, reply
    return str(reply)


def _turn(chat, body):
    record_dialogue_turn(
        chat, raw_input=body, normalized_input=body, reconstructed_input=body,
        topic_hints=["locker", "code"], reference_targets=[], understanding_confidence=1.0, quality_flags=[],
    )


def _load(chat):
    text = "Continue the previous chat discussion about the locker."
    assert not cr.exact_recall_requires_current_session(text)
    interpretation = _interpretation(text, topics=["locker", "code"])
    task = create_task_record(text)
    return tiered.TieredContextLoader().load(
        task=task, classification=classify(text, context=interpretation.as_context()),
        interpretation=interpretation, persona=load_active_persona("default"), session_id=chat,
        total_context_budget=5000,
        source_context={"surface": "api", "platform": "api", "chat_id": chat},
    )


def _source_summary(result, chat):
    return [item for item in result.relevant_items if item.source_type == "session_summary"
            and item.metadata.get("origin_chat_id") == chat]


def test_dialogue_delivery_rechecks_a_forget_after_its_initial_snapshot(runtime_profile, monkeypatch):
    profile = runtime_profile
    chat = "tiered-dialogue-reread"
    body = "My locker code is MINT-84."
    _ingest(profile, chat, [(1770109200.0, body)])
    _turn(chat, body)
    assert any("MINT-84" in item.content for item in tiered._dialogue_items(chat))
    read_turns = tiered.recent_dialogue_turns
    effects = []

    def forget_after_read(*args, **kwargs):
        turns = read_turns(*args, **kwargs)
        if args[0] == chat and not effects:
            effects.append("started")
            effects.append(_forget(profile, chat))
        return turns

    monkeypatch.setattr(tiered, "recent_dialogue_turns", forget_after_read)
    delivered = tiered._dialogue_items(chat)

    with VoolMemory(runtime_home=str(profile)) as memory:
        revoked = memory.revoked_tokens(chat, strict=True)
    assert effects and cr.text_carries_revoked_token(body, revoked)
    assert not any("MINT-84" in item.content for item in delivered)


@pytest.mark.parametrize("boundary", ["forget", "ledger_error", "partial_summary"])
def test_load_final_barrier_excludes_stale_import_and_records_the_exclusion(runtime_profile, monkeypatch, boundary):
    profile = runtime_profile
    origin, destination = "tiered-summary-origin", "tiered-summary-reader"
    _ingest(profile, origin, [(1770109200.0, "My locker code is MINT-84.")])
    ensure_chat_namespace(destination, grant_current_receipts=False)
    grant_context_import(destination, scope="chat", source_id="chat:" + origin)
    control = _load(destination)
    sources = _source_summary(control, origin)
    assert sources and any("MINT-84" in item.content for item in sources)
    source_ids = {item.item_id for item in sources}
    budget = tiered.budget_layer
    open_memory = cr._open_memory
    replace = os.replace
    calls, effects, denied = [], [], []

    def failed_open():
        memory = open_memory()
        if effects and memory is not None:
            def authorize(action, table, *_args):
                if action == sqlite3.SQLITE_READ and table == "memory_revocations":
                    denied.append(table)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            memory._conn.set_authorizer(authorize)
        return memory

    def fail_summary_replace(src, dst, *args, **kwargs):
        if Path(dst).resolve() == session_summaries_path().resolve():
            denied.append("session_summaries")
            raise OSError("synthetic summary replacement denied")
        return replace(src, dst, *args, **kwargs)

    def after_final_budget(*args, **kwargs):
        layer = budget(*args, **kwargs)
        calls.append(layer)
        if len(calls) == 3:
            effects.append("started")
            if boundary == "forget":
                effects.append(_forget(profile, origin))
            elif boundary == "partial_summary":
                with monkeypatch.context() as fault:
                    fault.setattr(os, "replace", fail_summary_replace)
                    effects.append(_forget(profile, origin))
        return layer

    monkeypatch.setattr(tiered, "budget_layer", after_final_budget)
    if boundary == "ledger_error":
        monkeypatch.setattr(cr, "_open_memory", failed_open)
    result = _load(destination)

    assert len(calls) == 3 and effects
    assert not _source_summary(result, origin)
    assert "MINT-84" not in result.assembled_context()
    assert any(item.source_type == "policy" for item in result.bootstrap_items)
    assert source_ids.isdisjoint({item["item_id"] for item in result.report.items_included})
    expected_reason = "forget_state_unavailable"
    if boundary != "ledger_error":
        with VoolMemory(runtime_home=str(profile)) as memory:
            revoked = memory.revoked_tokens(origin, strict=True)
            incomplete = memory.erasure_incomplete(origin)
        assert cr.text_carries_revoked_token("MINT-84", revoked)
        if boundary == "partial_summary":
            assert incomplete
        if not incomplete:
            expected_reason = "forget_revoked_source"
    assert any(item["item_id"] in source_ids and item["reason"] == expected_reason
               for item in result.report.items_excluded)
    manifest = load_manifest(result.report.context_manifest_id)
    assert manifest is not None
    assert source_ids.isdisjoint({item["item_id"] for item in manifest["selected_items"]})
    assert any(item["item_id"] in source_ids and item["reason"] == expected_reason
               for item in manifest["excluded_items"])
    assert result.report.relevant_tokens_used == sum(item.token_count for item in result.relevant_items)
    if boundary != "forget":
        assert denied


def test_load_keeps_identical_memory_from_an_independent_origin_chat(runtime_profile, monkeypatch):
    profile = runtime_profile
    forgotten, sibling, destination = "tiered-origin-forgotten", "tiered-origin-sibling", "tiered-origin-reader"
    for origin in (forgotten, sibling):
        _ingest(profile, origin, [(1770109200.0, "My locker code is MINT-84.")])
    ensure_chat_namespace(destination, grant_current_receipts=False)
    for origin in (forgotten, sibling):
        grant_context_import(destination, scope="chat", source_id="chat:" + origin)
    control = _load(destination)
    assert _source_summary(control, forgotten) and _source_summary(control, sibling)
    budget = tiered.budget_layer
    calls = []

    def forget_after_assembly(*args, **kwargs):
        layer = budget(*args, **kwargs)
        calls.append(layer)
        if len(calls) == 3:
            _forget(profile, forgotten)
        return layer

    monkeypatch.setattr(tiered, "budget_layer", forget_after_assembly)
    result = _load(destination)

    assert len(calls) == 3
    assert not _source_summary(result, forgotten)
    assert any("MINT-84" in item.content for item in _source_summary(result, sibling))
