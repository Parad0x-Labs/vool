"""Shared person-path rig for the fast-path capture and calendar read suites (not a test module).

One isolated runtime home + SQLite per test (a temp fake home under pytest's tmp_path), a REAL
``VoolAgent`` on a backend with no model behind it (every deterministic lane stays itself), and
every ask driven through the turn function the daemon itself calls for a turn —
``VoolAgent.run_once`` — on a fresh thread with an owner-local channel source context, which is
the channel gateway's own invocation shape. Nothing on the path under test is mocked here.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

OWNER = "owner_local"


def person_env_generator(tmp_path, monkeypatch):
    """Isolated-state fixture body: temp fake home, fresh DB, every cross-test cache dropped."""
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    import storage.db as sdb

    db_path = tmp_path / "person.db"
    sdb.configure_default_db_path(db_path)
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    reset_runtime_continuity_state()
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    import core.task_state_machine as _task_state
    import core.trace_id as _trace_id

    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False
        _module._init_table()
    person_home = tmp_path / "person-home"
    for room in ("Desktop", "Pictures", "Documents", "Downloads"):
        (person_home / room).mkdir(parents=True)
    yield {"home": home, "db_path": db_path, "tmp_path": tmp_path, "person_home": person_home}
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)
    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False


@pytest.fixture()
def person_env(tmp_path, monkeypatch):
    yield from person_env_generator(tmp_path, monkeypatch)


def make_agent(device: str = "person-test"):
    """A real agent on the model-less test backend (deterministic lanes only)."""
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device=device, persona_id="default")
    agent.start()
    return agent


def person_source_context(**overrides) -> dict[str, Any]:
    """The owner-local channel turn context (what the channel gateway binds for the owner)."""
    from core.request_trust import OWNER_LOCAL_KEY

    ctx: dict[str, Any] = {"surface": "channel", "platform": "openclaw", OWNER_LOCAL_KEY: True}
    ctx.update(overrides)
    return ctx


def turn(agent, text: str, *, session: str = "person-chat", **ctx_overrides) -> dict[str, Any]:
    """One person turn through the daemon's own turn function, on a fresh thread.

    A thread starts with an empty ContextVar context; running the turn there is the channel
    gateway's invocation shape and stops one turn's admission state leaking into the next.
    """
    ctx = person_source_context(**ctx_overrides)
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = agent.run_once(text, session_id_override=session, source_context=ctx)
        except BaseException as exc:  # re-raised on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=_run, name="person-rig-turn")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    result = box.get("value") or {}
    if not isinstance(result, dict):
        return {"response": str(result)}
    return result


def reply(result: dict[str, Any]) -> str:
    """The text the person reads for a turn."""
    return str(result.get("response") or "")


def bind_person_home(monkeypatch, env: dict[str, Any]) -> None:
    """Point ``~`` at the fake person home (Desktop/Pictures/...), away from VOOL's own home.

    Lanes that resolve "my Desktop" or "Pictures" through the OS home land inside the test's
    tmp tree; VOOL's runtime home stays a different directory so "created in the person's
    home" and "created in VOOL's home" are distinguishable on disk.
    """
    import os
    from pathlib import Path

    monkeypatch.setenv("HOME", str(env["person_home"]))
    monkeypatch.setenv("USERPROFILE", str(env["person_home"]))
    monkeypatch.setenv("VOOL_HOME", str(env["home"]))
    assert Path(os.path.expanduser("~")) == Path(env["person_home"])
