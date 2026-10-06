"""Current-observation contract (q90-post-sealed-recovery-20260929).

A NOW-anchored question ("right now", "this evening", "at the moment") asks
for a live observation. A stored record that is itself a past-tense report
of a measured value ("the log pegged the flow at 210 liters in March") must
not ride as answer evidence — the capsule abstains and the turn routes to
tools. Standing present-tense states, identity facts, codes and counts are
NOT past reports and keep serving; past-anchored and mixed questions keep
their history halves.

Fresh domain (lakeside pumping station); no sealed phrasing reused.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

os.environ.setdefault("VOOL_HOME", str(TREE / ".vool_local_test"))
os.environ["VOOL_HOME"] = os.environ["VOOL_HOME"]
os.environ["VOOL_WORKSPACE_ROOT"] = os.environ["VOOL_HOME"] + "/workspace"
os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from core.runtime_paths import configure_runtime_home

    home = tmp_path / "vool-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(home / "workspace"))
    configure_runtime_home(home)
    from core import embedding_service

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)

    from storage.migrations import run_migrations

    run_migrations()

    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    def build(chat_id: str, turns: list[tuple[str, str, str]]) -> str:
        ensure_chat_namespace(chat_id, grant_current_receipts=False)
        policy = resolve_memory_access_policy(chat_id=chat_id)
        for role, text, stated in turns:
            if role != "user":
                continue
            cr.store_turn(
                chat_id, text, "",
                access_policy=policy,
                source_context={"chat_id": chat_id, "runtime_home": str(home),
                                "statement_at": stated},
            )
        return chat_id

    def ask(chat_id: str, question: str) -> str:
        out = cr.inject_retrieved(
            chat_id, question,
            [{"role": "user", "content": question}],
            access_policy=resolve_memory_access_policy(chat_id=chat_id),
            source_context={"chat_id": chat_id, "runtime_home": str(home)},
            env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
        )
        return next(
            (str(m.get("content") or "") for m in out
             if m.get("role") == "system"
             and "<retrieved_context>" in str(m.get("content") or "")), "")

    return type("Env", (), {"build": staticmethod(build), "ask": staticmethod(ask)})()


def test_current_ask_excludes_past_measured_report(env):
    chat = env.build("p-station", [
        ("user", "The March log pegged the pump flow at 210 liters per hour.",
         "2026-03-20T10:00:00"),
    ])
    capsule = env.ask(chat, "What's the pump flow right now?")
    assert "210" not in capsule, capsule


def test_current_ask_keeps_present_tense_state(env):
    chat = env.build("p-station-b", [
        ("user", "The standby pump's rated capacity is 300 liters per hour.",
         "2026-03-20T10:00:00"),
    ])
    capsule = env.ask(chat, "What's the standby pump capacity right now?")
    assert "300" in capsule, capsule


def test_past_ask_keeps_the_measured_report(env):
    chat = env.build("p-station-c", [
        ("user", "The March log pegged the pump flow at 210 liters per hour.",
         "2026-03-20T10:00:00"),
    ])
    capsule = env.ask(chat, "What did the March log say the pump flow was?")
    assert "210" in capsule, capsule


def test_mixed_now_and_then_keeps_both_halves(env):
    chat = env.build("p-station-d", [
        ("user", "The March log pegged the pump flow at 210 liters per hour.",
         "2026-03-20T10:00:00"),
    ])
    capsule = env.ask(chat, "What was the flow in March, and what is it now?")
    assert "210" in capsule, capsule


def test_dialogue_items_leg_excludes_stale_report_for_current_ask(env):
    """The 'Recent dialogue turn' context leg obeys the same law (the tier-R
    leak leg: the stale record re-entered the prompt as context even after
    retrieval excluded it)."""
    from core.context_namespace import ensure_chat_namespace
    from core.runtime_paths import configure_runtime_home
    from core.tiered_context_loader import _dialogue_items
    from storage.dialogue_memory import record_dialogue_turn

    home = Path(os.environ["VOOL_HOME"])
    chat = "p-dialog"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    record_dialogue_turn(
        chat, raw_input="The March log pegged the pump flow at 210 liters.",
        normalized_input="The March log pegged the pump flow at 210 liters.",
        reconstructed_input="The March log pegged the pump flow at 210 liters.",
        speaker_role="user", topic_hints=[], reference_targets=[],
        understanding_confidence=0.9, quality_flags=[])
    record_dialogue_turn(
        chat, raw_input="The standby pump is the blue one by the wall.",
        normalized_input="The standby pump is the blue one by the wall.",
        reconstructed_input="The standby pump is the blue one by the wall.",
        speaker_role="user", topic_hints=[], reference_targets=[],
        understanding_confidence=0.9, quality_flags=[])

    current = _dialogue_items(chat, query_text="What's the pump flow right now?")
    past = _dialogue_items(chat, query_text="What did the March log say?")
    joined_current = " ".join(item.content for item in current)
    joined_past = " ".join(item.content for item in past)
    assert "210" not in joined_current, joined_current
    assert "blue one by the wall" in joined_current, joined_current
    assert "210" in joined_past, joined_past
    configure_runtime_home(home)
