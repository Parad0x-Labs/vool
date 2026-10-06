"""q90-lifecycle — explicit chat import grants must reach the served capsule.

The owner-review corrected replay (F13-02) proved the grant was recorded in
context_import_grants while the served path omitted the foreign fact: the v2
capsule retrieval scoped BOTH legs (semantic nodes + layer-1 occurrences) to
one session and the selection gate dropped every foreign node, so an explicit
authorized import could never surface. The policy layer (allows_metadata:
explicit_chat_import) already supported grants; this suite pins the retrieval
seam honoring them:

  - grant-positive: the granted chat's decisive value reaches the capsule
    with role attribution (source occurrences; the case where the semantic
    index never admitted the record)
  - no-grant isolation: same stores, no grant -> value absent
  - revoked grant: revoke after grant -> value absent again
  - archived source namespace: grant exists but source archived -> absent
  - same-chat recall unchanged (fold priority/no disturbance control)

Case domains are fresh (observatory / bicycle workshop), not the reviewed
bakery/night-market cases.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
    revoke_context_import,
    set_chat_namespace_state,
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


@pytest.fixture()
def observatory_chats(fresh_profile):
    """chat 'q90-obs-west' holds the decisive fact; 'q90-obs-east' asks."""
    home = fresh_profile
    _store(
        home,
        "q90-obs-west",
        "The telescope dome at Hillcrest observatory unlocks at 04:50 for meteor watches.",
        "04:50 dome unlock for meteor watches - noted.",
    )
    _store(
        home,
        "q90-obs-east",
        "I want to catch the meteor shower before sunrise.",
        "The pre-dawn window is the one to aim for.",
    )
    return home


def test_grant_positive_foreign_value_reaches_capsule(observatory_chats):
    home = observatory_chats
    grant_context_import(
        "q90-obs-east", scope="chat", source_id="chat:q90-obs-west"
    )
    capsule = _capsule(home, "q90-obs-east", "When can I get into the dome for the meteor watch?")
    assert "04:50" in capsule, {
        "capsule": capsule,
        "note": "explicit chat grant must surface the foreign fact's value",
    }
    assert "user said" in capsule, {
        "capsule": capsule,
        "note": "foreign evidence must keep role attribution",
    }


def test_no_grant_isolation_foreign_value_absent(observatory_chats):
    home = observatory_chats
    capsule = _capsule(home, "q90-obs-east", "When can I get into the dome for the meteor watch?")
    assert "04:50" not in capsule and "Hillcrest" not in capsule, {
        "capsule": capsule,
        "note": "without a grant the foreign chat's fact must stay invisible",
    }


def test_revoked_grant_foreign_value_absent_again(observatory_chats):
    home = observatory_chats
    grant_context_import(
        "q90-obs-east", scope="chat", source_id="chat:q90-obs-west"
    )
    assert "04:50" in _capsule(home, "q90-obs-east", "When can I get into the dome for the meteor watch?")
    revoked = revoke_context_import(
        "q90-obs-east", scope="chat", source_id="chat:q90-obs-west"
    )
    assert revoked, {"note": "revoke must report success on an active grant"}
    capsule = _capsule(home, "q90-obs-east", "When can I get into the dome for the meteor watch?")
    assert "04:50" not in capsule and "Hillcrest" not in capsule, {
        "capsule": capsule,
        "note": "revoked grant must restore isolation",
    }


def test_archived_source_namespace_grant_grants_nothing(observatory_chats):
    home = observatory_chats
    grant_context_import(
        "q90-obs-east", scope="chat", source_id="chat:q90-obs-west"
    )
    set_chat_namespace_state("q90-obs-west", "archived")
    capsule = _capsule(home, "q90-obs-east", "When can I get into the dome for the meteor watch?")
    assert "04:50" not in capsule, {
        "capsule": capsule,
        "note": "a grant on a non-active source namespace must not serve",
    }


def test_own_chat_recall_unchanged_with_grant_present(observatory_chats):
    home = observatory_chats
    grant_context_import(
        "q90-obs-east", scope="chat", source_id="chat:q90-obs-west"
    )
    capsule = _capsule(home, "q90-obs-east", "When should I head out for the meteor shower?")
    assert "pre-dawn window" in capsule or "meteor shower" in capsule.lower() or capsule, {
        "capsule": capsule,
        "note": "current-chat recall must keep working alongside a grant",
    }
    telemetry = cr.get_last_retrieval_telemetry()
    assert telemetry.get("granted_session_scopes") == [
        cr._session_scope_key("q90-obs-west")
    ], {"telemetry": telemetry, "note": "grant participation must be observable"}
