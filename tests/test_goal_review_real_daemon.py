"""A goal across a daemon crash, on the REAL daemon: real HTTP, real agent, real restart.

The daemon boots from ``apps.vool_api_server`` in its own disposable home. Chat turns go through
the real ``/api/chat`` ingress, goal controls through the real ``/api/goal`` routes, approvals
through the real ``/api/mode`` door the permission bar uses, and step turns run the real agent
loop and the real permission authority. The only scripted seam is the local model transport
(the provider stub the other real-daemon goal journey uses).

Proven here: a daemon killed in the middle of a goal step comes back with the goal not
running, never resurrected by itself; an explicit Resume finishes it with each file written
once.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_goal_real_daemon_journey import MODEL_A, GoalJourneyProvider


def _request(base_url: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 120.0) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url + path, data=data, headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _chat(base_url: str, session: str, text: str, *, mode: str, turn_id: str) -> list[dict[str, Any]]:
    payload = {
        "session_id": session, "stream": True, "mode": mode, "model": MODEL_A, "turn_id": turn_id,
        "messages": [{"role": "user", "content": text}],
    }
    request = urllib.request.Request(
        base_url + "/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    lines: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass
    return lines


def _goal_from(lines: list[dict[str, Any]]) -> dict[str, Any]:
    for line in lines:
        event = line.get("vool_event")
        if isinstance(event, dict) and event.get("type") == "goal.updated" and isinstance(event.get("goal"), dict):
            return event["goal"]
    raise AssertionError(f"no goal event on the stream: {lines[-3:]}")


def _state(base_url: str, session: str) -> dict[str, Any]:
    return _request(base_url, "/api/goal/state?session=" + urllib.parse.quote(session))[1].get("goal") or {}


def _wait(base_url: str, session: str, states: set[str], timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    goal: dict[str, Any] = {}
    while time.time() < deadline:
        goal = _state(base_url, session)
        if goal.get("state") in states and all(step.get("state") != "running" for step in goal.get("steps") or []):
            return goal
        time.sleep(0.5)
    return goal


def _files(workspace) -> dict[str, int]:
    return {path.name: len(path.read_text(encoding="utf-8").splitlines()) for path in workspace.rglob("*") if path.is_file()}


def _step_requests(provider: GoalJourneyProvider) -> int:
    marker = "You are executing one step of an explicit goal"
    with provider._lock:
        return sum(
            1 for call in provider.calls
            if marker in json.dumps(call.get("body") or {})
        )


def _boot(daemon: ServedDaemon, home, provider: GoalJourneyProvider) -> None:
    run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {})]))
    daemon.start(timeout=240)
    run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {})]))
    status, payload = _request(
        daemon.base_url, "/api/model-tool-certification/run",
        {"provider_name": "ollama-local", "model_name": MODEL_A, "timeout_seconds": 60}, timeout=180,
    )
    assert status == 200 and payload.get("state") == "verified", json.dumps(payload)[:400]
    provider.reset()


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("goal-review-daemon")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = GoalJourneyProvider()
    env = {
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WORKSPACE_ROOT": str(workspace),
        "VOOL_MODEL_LOAD_FLOOR_GB": "0",
        "VOOL_FORCE_OLLAMA_MODEL": MODEL_A,
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "http_proxy": "", "https_proxy": "", "all_proxy": "", "no_proxy": "127.0.0.1,localhost",
    }
    daemon = ServedDaemon(home, env_extra=env)
    provider.__enter__()
    state = {"daemon": daemon, "home": home, "workspace": workspace, "provider": provider, "env": env}
    try:
        _boot(daemon, home, provider)
        yield state
    finally:
        log = home / "daemon.log"
        if log.exists():
            print("\n=== DAEMON LOG TAIL ===")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        state["daemon"].stop()
        provider.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def fresh_workspace(rig):
    import shutil

    shutil.rmtree(rig["workspace"])
    rig["workspace"].mkdir()
    rig["provider"].reset()


def test_a_daemon_killed_mid_step_comes_back_not_running_and_resume_finishes(rig) -> None:
    daemon, workspace, provider, home = rig["daemon"], rig["workspace"], rig["provider"], rig["home"]
    provider.step_entered.clear()
    provider.release_step.clear()
    provider.hold_step.set()
    goal = _goal_from(_chat(daemon.base_url, "review-restart", "/goal build the restart demo site", mode="auto", turn_id="rr-t1"))
    session, goal_id = goal["session_id"], goal["goal_id"]
    assert provider.step_entered.wait(120), "no step request reached the provider"
    assert _state(daemon.base_url, session)["state"] == "running"

    # The daemon dies in the middle of the step (no clean shutdown), then starts again.
    daemon.process.kill()
    daemon.process.wait(timeout=30)
    provider.release_step.set()
    restarted = ServedDaemon(home, env_extra=rig["env"])
    rig["daemon"] = restarted
    _boot(restarted, home, provider)
    base = restarted.base_url

    goal = _state(base, session)
    assert goal["state"] in {"interrupted", "blocked"}, goal
    assert all(step["state"] != "running" for step in goal["steps"]), goal
    time.sleep(2.0)
    assert _step_requests(provider) == 0, "the goal re-dispatched by itself after the restart"
    assert _state(base, session)["state"] == goal["state"]

    # A restarted daemon does not carry the chat's Auto mode over; the owner picks it again
    # (through the mode door the composer uses) before resuming.
    status, payload = _request(base, "/api/mode", {"session_id": session, "mode": "auto"})
    assert status == 200, payload
    status, payload = _request(base, "/api/goal/resume", {"session_id": session, "goal_id": goal_id})
    assert status == 200 and payload["ok"], payload
    goal = _wait(base, session, {"completed", "failed", "blocked", "waiting_approval"})
    files = _files(workspace)
    assert goal["state"] == "completed", json.dumps(goal)[:1500]
    assert set(files) == {"index.html", "README.md"} and all(count == 1 for count in files.values()), files

