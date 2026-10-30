"""/api/chat/sessions while the store is still starting answers "starting", never an empty list.

After a restart the sidebar must not be able to read "you have no chats" out of a store that was
never read. These tests pin the server half of that contract:

* a seeded store answers 200 "ready" with the real list, counts and total;
* a genuinely empty store still answers 200 "ready" with an empty list -- only that answer may
  drive the page's true empty state;
* a read that raises while the store is opening answers 503 {"ok": false, "state": "starting"}
  with no "sessions" key at all, so the endpoint cannot fabricate an empty list it never read.

Driving pattern: tests/test_sidebar_counts_are_not_page_counts.py (own VOOL_HOME, dispatch_get).
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.persistent_memory import append_conversation_event
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _chat(index: int) -> str:
    sid = f"openclaw:{index:020d}"
    ensure_chat_namespace(sid, grant_current_receipts=False)
    append_conversation_event(
        session_id=sid,
        user_input="hi",
        assistant_output="hey",
        access_policy=resolve_memory_access_policy(chat_id=sid),
    )
    return sid


def _sessions_response():
    return dispatch_get(
        path="/api/chat/sessions",
        query={},
        runtime=RuntimeServices(),
        model_name="test-model",
        client_host="127.0.0.1",
    )


def test_a_read_store_answers_ready_with_the_real_list() -> None:
    _chat(0)
    _chat(1)

    res = _sessions_response()
    payload = json.loads(res.body.decode("utf-8"))

    assert res.status == 200
    assert payload["state"] == "ready"
    assert payload["sessions"], "a seeded store answered an empty list"
    assert payload["counts"][""] == 2
    assert payload["total"] == 2


def test_an_empty_store_still_answers_ready_with_an_empty_list() -> None:
    """A real empty store answers an empty list. The page may show its true empty state only then."""
    res = _sessions_response()
    payload = json.loads(res.body.decode("utf-8"))

    assert res.status == 200
    assert payload["state"] == "ready"
    assert payload["sessions"] == []
    assert payload["total"] == 0


def test_a_failed_read_answers_starting_and_never_fabricates_a_list(monkeypatch) -> None:
    _chat(0)   # a store WITH a chat: a fabricated empty list would be visible against this seed

    def _raising(*args, **kwargs):
        raise RuntimeError("store still opening")

    monkeypatch.setattr("core.persistent_memory.list_conversation_sessions", _raising)

    res = _sessions_response()
    body = json.loads(res.body.decode("utf-8"))

    assert res.status == 503
    assert body["ok"] is False
    assert body["state"] == "starting"
    assert "sessions" not in body, "a failed read fabricated a chat list"
