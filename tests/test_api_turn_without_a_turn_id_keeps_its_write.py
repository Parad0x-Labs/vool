"""An API turn that sends no turn id is still one turn, from ingress to the final check.

Measured on 1cbf433 (fresh-user test, 2026-10-29): in Auto, "make a new file on my desktop called
groceries.txt with milk, eggs and bread" sent as a plain API call (no `turn_id`, as a script, a
bridge or a phone plug sends it) wrote the file, and then the reply was a bare HTTP 500 "Internal
Server Error". The runtime recorded the write under the turn id it minted for itself, while the
served boundary's final honesty check looked the turn up by the request id, found no write, would
have replaced "Created file" with "I cannot verify that file", and raised. The person was told it
failed after it worked, and would retry into a duplicate.
"""

from __future__ import annotations

import json

import pytest

_ASK = "make a new file on my desktop called groceries.txt with milk, eggs and bread"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "person"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    from tests.gauntlet import harness

    harness.install(monkeypatch)
    return home


def _post(body: dict) -> tuple[int, dict]:
    from tests.gauntlet import harness
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/chat",
        body=body,
        headers={},
        runtime=harness.runtime(),
        model_name="gauntlet",
        workspace_root_provider=lambda: str(harness.PROJECT_ROOT),
        client_host="127.0.0.1",
    )
    raw = res.body.decode("utf-8", "replace") if isinstance(res.body, (bytes, bytearray)) else str(res.body)
    try:
        return res.status, json.loads(raw)
    except ValueError:
        return res.status, {"_raw": raw}


def _reply(payload: dict) -> str:
    return str((payload.get("message") or {}).get("content") or "")


@pytest.mark.parametrize("turn_id", ["", "page-turn-1"], ids=["api-without-turn-id", "page-with-turn-id"])
def test_an_auto_write_answers_what_it_did(home, turn_id) -> None:
    body = {
        "stream": False,
        "mode": "auto",
        "session_id": f"auto-write-{turn_id or 'none'}",
        "messages": [{"role": "user", "content": _ASK}],
    }
    if turn_id:
        body["turn_id"] = turn_id

    status, payload = _post(body)

    written = home / "Desktop" / "groceries.txt"
    assert written.is_file(), "the write itself must happen"
    assert status == 200, payload
    reply = _reply(payload)
    assert "Created file" in reply and "groceries.txt" in reply, reply
    assert "cannot verify" not in reply


def test_a_manual_api_turn_asks_under_a_turn_it_can_be_answered_for(home) -> None:
    """The same identity is the approval's task: without it the prompt could never be answered."""

    status, payload = _post(
        {
            "stream": False,
            "session_id": "manual-write-none",
            "messages": [{"role": "user", "content": _ASK}],
        }
    )

    assert status == 200, payload
    assert "needs your approval" in _reply(payload)
    assert not (home / "Desktop" / "groceries.txt").exists()
    from core.mode_permission_policy import _APPROVALS

    asked = [dict(row) for row in _APPROVALS.values() if row.get("request_text") == _ASK]
    assert asked, "the prompt must be stored"
    # A prompt with no task can never be answered: Allow re-sends the asking turn by this id.
    assert all(str(row.get("task_id") or "").strip() for row in asked), asked
