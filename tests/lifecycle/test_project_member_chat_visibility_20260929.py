"""q90-lifecycle — project import grants admit member chats' capsule evidence.

Fresh domain (wind-farm repower campaign), distinct from the corpus's
radio-station/canal-locks cases:

  - positive: with an explicit project import grant, a member chat's capsule
    surfaces the project sibling chat's decisive evidence line
  - control 1: same project membership WITHOUT the grant changes nothing
    (isolation default unchanged)
  - control 2: an identically-worded fact in a NON-member chat stays hidden
    despite the lexical overlap (project boundary)
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
)
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _policy(chat: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat)


def _store(home: str, chat: str, user_text: str, assistant_text: str):
    return cr.store_turn(
        chat,
        user_text,
        assistant_text,
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _capsule(home: str, chat: str, query: str) -> str:
    out = cr.inject_retrieved(
        chat,
        query,
        [{"role": "user", "content": query}],
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return "\n".join(
        str(m.get("content") or "")
        for m in out
        if "<retrieved_context>" in str(m.get("content") or "")
    )


PROJECT = "q90-wind-repower"


def _seed_chats(home):
    ensure_chat_namespace("q90-wind-survey", project_id=PROJECT, grant_current_receipts=False)
    ensure_chat_namespace("q90-wind-crew", project_id=PROJECT, grant_current_receipts=False)
    ensure_chat_namespace("q90-wind-private", grant_current_receipts=False)
    _store(
        home,
        "q90-wind-survey",
        "The turbine crane pad at the Ravenscar site locks out at 05:20 for blade swaps.",
        "05:20 crane-pad lockout for blade swaps - logged.",
    )
    _store(
        home,
        "q90-wind-private",
        "The turbine crane pad at the Duncraft site locks out at 05:20 for blade swaps.",
        "05:20 crane-pad lockout at Duncraft - logged.",
    )
    _store(
        home,
        "q90-wind-crew",
        "Booking the crew transport for the swap week.",
        "Transport for swap week - noted.",
    )


def test_project_grant_admits_member_chat_evidence(fresh_profile):
    home = fresh_profile
    _seed_chats(home)
    grant_context_import("q90-wind-crew", scope="project", source_id=f"project:{PROJECT}")
    capsule = _capsule(home, "q90-wind-crew", "When is the crane pad locked out for blade swaps?")
    assert "05:20" in capsule, {
        "capsule": capsule,
        "note": "explicit project grant must surface the member chat's evidence",
    }
    assert "Ravenscar" in capsule and "Duncraft" not in capsule, {
        "capsule": capsule,
        "note": "project boundary: the non-member chat's identical fact stays hidden",
    }


def test_project_membership_alone_grants_nothing(fresh_profile):
    home = fresh_profile
    _seed_chats(home)
    capsule = _capsule(home, "q90-wind-crew", "When is the crane pad locked out for blade swaps?")
    assert "05:20" not in capsule and "Ravenscar" not in capsule and "Duncraft" not in capsule, {
        "capsule": capsule,
        "note": "same-project membership without the explicit grant must expose nothing",
    }
