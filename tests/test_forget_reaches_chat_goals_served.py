"""SERVED: "Forget X" in a chat whose /goal named X completes, and no goal worker writes X back.

The real daemon (``apps.vool_api_server``) in its own home, the real /api/chat door for the /goal command and
for the forget, the real goal worker and step turns; the only scripted seam is the Ollama-dialect provider
behind a real socket. Measured before this change: the forget answered "Forget could not be verified as fully
applied: unclassified chat goals ... A retry will not clear chat goals", every time.

Marked ``served``: boots a daemon. A skip is not a pass.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._served_sufficiency_provider import BodyAwareScriptedProvider
from tests.test_forget_scrubs_run_history import _db_carriers, _file_carriers
from tests.test_goal_real_daemon_journey import _chat_stream, _goal_state, _wait_goal
from tests.test_memory_forget_served_p0 import MODEL, SEED, _certify, _reply_text

pytestmark = [pytest.mark.served]

CANARY = "Flourstone Bakehouse"
RHYME = "A short rhyme: the harbor lanterns burn, and quiet tides return."
STEP_MARKER = "You are executing one step of an explicit goal"
PLANNER_MARKER = "You are the planner for an explicit goal"


class GoalStepProvider(BodyAwareScriptedProvider):
    """Answers every turn with the rhyme, except a goal step: that one answers ``step_answer``. While
    ``hold`` is set, the goal's planner request waits for ``release`` (the goal's first turn in flight)."""

    def __init__(self) -> None:
        super().__init__({MODEL: RHYME})
        self.step_answer = RHYME
        self.hold = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in (body.get("messages") or []) if isinstance(m, dict))
        if PLANNER_MARKER in prompt and self.hold.is_set():
            self.hold.clear()
            self.entered.set()
            self.release.wait(180)
        if STEP_MARKER in prompt and not any(str(m.get("role")) == "tool" for m in body.get("messages") or []):
            return self.step_answer
        return super().reply(model, body)


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("forget-goals-served")
    home = tmp_path / "home"
    provider = GoalStepProvider()
    served = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WORKSPACE_ROOT": str(tmp_path / "ws"),
        "VOOL_MODEL_LOAD_FLOOR_GB": "0",
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "http_proxy": "", "https_proxy": "", "all_proxy": "", "no_proxy": "127.0.0.1,localhost",
    })
    provider.__enter__()
    try:
        try:
            served.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the change
            pytest.fail(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[MODEL]))
        assert _certify(served, MODEL).get("state") == "verified"
        provider.reset()
        yield {"daemon": served, "provider": provider, "db": home / "data" / "vool_web0_v2.db"}
    finally:
        provider.release.set()
        served.stop()
        provider.__exit__(None, None, None)


def _start_goal(daemon: ServedDaemon, session: str, objective: str) -> tuple[str, list[dict[str, Any]]]:
    lines = _chat_stream(daemon.base_url, {
        "session_id": session, "stream": True, "mode": "auto", "model": MODEL,
        "turn_id": f"{session}-goal", "messages": [{"role": "user", "content": "/goal " + objective}]})
    views = [line["vool_event"]["goal"] for line in lines
             if isinstance(line.get("vool_event"), dict) and line["vool_event"].get("type") == "goal.updated"]
    return (str(views[0]["session_id"]) if views else session), lines


def _say(daemon: ServedDaemon, session: str, text: str) -> str:
    return _reply_text(daemon.chat(text, session_id=session, mode="auto")).strip()


def _goal_rows(db: Path, session: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM chat_goals WHERE session_id = ?", (session,))]
    finally:
        conn.close()


def _settled(daemon: ServedDaemon, session: str, timeout: float = 60.0) -> dict[str, Any]:
    """The goal once no step of it is running and its worker is gone."""
    deadline = time.time() + timeout
    goal: dict[str, Any] = {}
    while time.time() < deadline:
        goal = _goal_state(daemon.base_url, session).get("goal") or {}
        if goal.get("state") not in ("running", "planning", "queued") and all(
                step.get("state") != "running" for step in goal.get("steps") or []):
            return goal
        time.sleep(0.5)
    return goal


def test_served_forget_in_a_chat_with_a_finished_goal_completes_and_leaves_no_bytes(daemon):
    served, db = daemon["daemon"], daemon["db"]
    session = "forget-goal-finished"
    assert _say(served, session, "Remember that my sister's bakery is called Flourstone Bakehouse.")
    session, _ = _start_goal(served, session, "Plan a visit to the Flourstone Bakehouse")
    goal = _wait_goal(served.base_url, session, {"completed", "failed", "blocked"})
    assert goal.get("state") == "completed", goal
    assert "chat_goals.objective" in _db_carriers(db, "flourstone")

    reply = _say(served, session, f"Forget {CANARY}.")

    assert reply.startswith("Forget applied"), reply
    # Nothing in the database file or its write-ahead log holds the words, the goal record included.
    assert _db_carriers(db, "flourstone") == []
    assert _file_carriers(db, "flourstone") == []
    after = _goal_state(served.base_url, session)["goal"]
    assert after["goal_id"] == goal["goal_id"] and after["state"] == "completed", after
    assert "flourstone" not in json.dumps(after).casefold(), after


def test_served_forget_stops_a_running_goal_and_its_worker_writes_nothing_back(daemon):
    served, provider, db = daemon["daemon"], daemon["provider"], daemon["db"]
    session = "forget-goal-running"
    assert _say(served, session, "Remember that my sister's bakery is called Flourstone Bakehouse.")
    provider.step_answer = "Booked: a table at the Flourstone Bakehouse at six."
    provider.release.clear()
    provider.entered.clear()
    provider.hold.set()
    session, _ = _start_goal(served, session, "Plan a visit to the Flourstone Bakehouse")
    assert provider.entered.wait(180), "the goal's planner turn never reached the provider"
    running = _goal_state(served.base_url, session)["goal"]
    assert running["state"] == "planning", running

    replies: list[str] = []
    forget = threading.Thread(target=lambda: replies.append(_say(served, session, f"Forget {CANARY}.")))
    forget.start()
    forget.join(20)  # the forget waits for the planner turn's slot in this chat (one turn at a time)
    # The planner turn settles after the forget is in line: the worker then writes its plan and starts
    # its step while the forget runs.
    provider.release.set()
    forget.join(240)
    assert replies and replies[0].startswith("Forget applied"), replies

    goal = _settled(served, session)
    # The goal ran on the forgotten words: it is stopped, not left running on a blanked objective.
    assert goal.get("state") == "cancelled", json.dumps(goal)
    assert all(step["state"] != "running" for step in goal["steps"]), goal
    time.sleep(2)  # let a late worker write, if it could
    rows = _goal_rows(db, session)
    assert rows and not [row for row in rows if "flourstone" in json.dumps(row).casefold()], rows
    assert _goal_state(served.base_url, session)["goal"]["state"] == "cancelled"


def test_served_goal_after_the_forget_cannot_record_the_words(daemon):
    served, provider, db = daemon["daemon"], daemon["provider"], daemon["db"]
    session = "forget-goal-afterwards"
    assert _say(served, session, "Remember that my sister's bakery is called Flourstone Bakehouse.")
    assert _say(served, session, f"Forget {CANARY}.").startswith("Forget applied")

    # A goal about something else whose step answer names them: the worker's writes do not keep them.
    provider.step_answer = "Noted the lanterns; the Flourstone Bakehouse sells lantern oil."
    session, _ = _start_goal(served, session, "Write a note about harbor lanterns")
    goal = _wait_goal(served.base_url, session, {"completed", "failed", "blocked"})
    assert goal.get("state") == "completed", json.dumps(goal)
    rows = _goal_rows(db, session)
    assert [row["objective"] for row in rows] == ["Write a note about harbor lanterns"], rows
    assert not [row for row in rows if "flourstone" in json.dumps(row).casefold()], rows

    # A new goal naming the forgotten words is not recorded.
    _, lines = _start_goal(served, session, "Plan a visit to the Flourstone Bakehouse")
    said = " ".join(str((line.get("message") or {}).get("content") or "") for line in lines)
    assert "The goal was not started: the goal names words this chat asked to forget." in said, lines[-3:]
