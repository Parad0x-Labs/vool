"""Delete a chat (transcript + memory + meta), delete-a-project-forgets-its-memory, and reveal."""

from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import (
    record_memory_entry,
    resolve_memory_access_policy,
)
from core.memory.files import load_jsonl
from core.persistent_memory import (
    append_conversation_event,
    delete_conversation_session,
    forget_sessions_memory,
    list_conversation_sessions,
    load_session_meta,
    memory_entries_path,
    recent_conversation_events,
    set_session_meta,
)
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

_A = "openclaw:" + "a" * 20
_B = "openclaw:" + "b" * 20


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _seed(sid: str, fact: str) -> None:
    ensure_chat_namespace(sid, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=sid)
    append_conversation_event(
        session_id=sid,
        user_input="hi",
        assistant_output="hey",
        access_policy=policy,
    )
    record_memory_entry(fact, category="fact", session_id=sid, source="test", confidence=0.9,
                        keywords=fact.lower().split()[:8], share_scope="local_only",
                        scope="chat", authority="confirmed_memory", fact_key=None,
                        expires_at=None, review_after=None, access_policy=policy)


def _mem_sessions() -> set:
    # Live memory sessions only. The P0 erasure law (see
    # tests/test_memory_forget_resurrection_p0.py) keeps every deleted row as a durable
    # tombstone -- status "erased", payload dropped -- so a forgotten fact cannot be
    # resurrected from the mirror; a tombstone has no session_id by design. A chat's
    # memory is purged when it has no LIVE row, which is what the product's own readers
    # (combined_memory_entries) filter on.
    return {
        str(r.get("session_id") or "")
        for r in load_jsonl(memory_entries_path())
        if str(r.get("status") or "") != "erased"
    }


def _tombstones() -> list:
    return [r for r in load_jsonl(memory_entries_path()) if str(r.get("status") or "") == "erased"]


def test_delete_conversation_session_removes_transcript_memory_and_meta() -> None:
    _seed(_A, "alpha secret fact")
    _seed(_B, "beta other fact")
    set_session_meta(_A, title="Chat A")
    assert delete_conversation_session(_A) is True
    assert recent_conversation_events(_A, limit=5) == []   # transcript gone
    assert _A not in load_session_meta()                    # meta gone
    assert _mem_sessions() == {_B}                          # only A's memory purged
    assert recent_conversation_events(_B, limit=5)          # B untouched
    # The purge is durable, not a marker skip: A's row survives as an erasure tombstone
    # carrying NO recoverable payload -- its fact text, keywords, and session id are gone
    # from the raw store, so nothing downstream can serve or resurrect them.
    tombstones = _tombstones()
    assert tombstones, "expected A's purge to leave a durable erasure tombstone"
    raw_store = json.dumps(load_jsonl(memory_entries_path()))
    assert "alpha secret fact" not in raw_store
    assert _A not in raw_store


def test_forget_sessions_memory_purges_only_those_sessions_keeps_transcripts() -> None:
    _seed(_A, "alpha fact")
    _seed(_B, "beta fact")
    assert forget_sessions_memory([_A]) >= 1
    assert _mem_sessions() == {_B}                 # A's memory gone
    assert recent_conversation_events(_A, limit=5)  # A's transcript kept (forget != delete)


def _post(path, body, host="127.0.0.1"):
    return dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                         runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
                         workspace_root_provider=lambda: "/tmp", client_host=host)


def _j(response):
    return json.loads(response.body.decode("utf-8"))


def test_bound_empty_chat_lists_before_its_first_message() -> None:
    sid = "openclaw:" + "f" * 20
    set_session_meta(sid, project_id="proj_x", title="Planning")   # bound + titled, no transcript yet
    listed = {s["session_id"]: s for s in list_conversation_sessions()}
    assert sid in listed
    assert listed[sid]["project_id"] == "proj_x"
    assert listed[sid]["turn_count"] == 0
    assert listed[sid]["title"] == "Planning"


def test_chat_delete_endpoint_removes_the_chat() -> None:
    _seed(_A, "alpha fact")
    res = _post("/api/chat/session", {"session_id": _A, "delete": True})
    assert res.status == 200 and _j(res)["deleted"] is True
    assert not any(s["session_id"] == _A for s in list_conversation_sessions())


def test_project_delete_forgets_memory_and_unbinds_chats(tmp_path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    pid = _j(_post("/api/projects", {"name": "P", "root": str(folder)}))["project"]["id"]
    _seed(_A, "alpha project fact")
    _post("/api/chat/session", {"session_id": _A, "project_id": pid})

    res = _post("/api/projects/delete", {"id": pid})
    assert res.status == 200
    body = _j(res)
    assert body["ok"] is True and body["forgot"] >= 1 and body["chats"] == 1
    assert _mem_sessions() == set()                                    # its memory purged
    assert load_session_meta().get(_A, {}).get("project_id", "") == ""  # chat unbound -> General


def test_project_reveal_opens_the_folder(tmp_path, monkeypatch) -> None:
    import subprocess

    calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: calls.append(list(args)))
    folder = tmp_path / "proj"
    folder.mkdir()
    pid = _j(_post("/api/projects", {"name": "P", "root": str(folder)}))["project"]["id"]
    res = _post("/api/projects/reveal", {"id": pid})
    assert res.status == 200 and _j(res)["ok"] is True
    # Some other Popen calls happen (hardware probes); the reveal opens the folder in Finder.
    assert any(c and c[0] == "open" and str(folder) in c for c in calls)
