"""A chat turn request that fails after it took the chat's one-turn slot must free the slot at once.

The chat door takes the slot (core.runtime_continuity.admit_chat_turn) before it spends anything,
then hands it to the turn's worker, whose end releases it. A request that throws in between never
reaches a worker: before this repair its slot stayed held until the 30 s lease ran out, so for half
a minute every send to that chat was refused or queued behind a turn that did not exist. The
failure is injected at a real seam inside that window (the door's build/model status check); the
door itself, the slot law and the queue door are all the production code.
"""

from __future__ import annotations

import json

import pytest

from core import live_turns, runtime_continuity, runtime_paths
from storage.migrations import run_migrations

SESSION = "openclaw:eeeeeeeeeeeeeeeeeeee"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    db = tmp_path / "continuity.db"
    run_migrations(db_path=db)
    runtime_continuity.configure_runtime_continuity_db_path(str(db))
    runtime_continuity.reset_runtime_continuity_state()
    live_turns.reset_live_turns()
    yield
    live_turns.reset_live_turns()
    runtime_continuity.reset_runtime_continuity_state()
    runtime_continuity.configure_runtime_continuity_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _post(body: dict):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path="/api/chat",
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )


def _page_turn(text: str, turn_id: str) -> dict:
    return {
        "session_id": SESSION,
        "stream": False,
        "turn_id": turn_id,
        "on_busy": "refuse",
        "messages": [{"role": "user", "content": text}],
    }


def test_a_request_that_fails_after_taking_the_slot_frees_it_at_once(monkeypatch) -> None:
    from core.web.api import runtime as api_runtime
    from core.web.api import service

    def _broken(*_args, **_kwargs):
        raise RuntimeError("status check failed")

    monkeypatch.setattr(service, "runtime_version_response", _broken)
    try:
        response = _post(_page_turn("hello there", "t-broken-1"))
        status = response.status
    except RuntimeError:
        status = None  # the door let it propagate; either way no worker ever ran
    assert status != 200, status
    assert runtime_continuity.chat_turn_active(SESSION) is False, "the failed request left the chat's slot held"
    assert runtime_continuity.admitted_chat_turn_token(SESSION) == ""

    # And the chat is usable at once: the next send from the page runs, not a 409.
    monkeypatch.undo()
    ran: list[str] = []

    def _turn(runtime, user_text, *, session_id=None, source_context=None, workspace_root_provider=None):
        ran.append(user_text)
        return {"response": "ok", "confidence": 0.9}

    monkeypatch.setattr(api_runtime, "_run_agent_locked", _turn)
    response = _post(_page_turn("and now this", "t-after-1"))
    assert response.status == 200, json.loads(response.body.decode("utf-8") or "{}")
    assert ran == ["and now this"]
    assert runtime_continuity.chat_turn_active(SESSION) is False
