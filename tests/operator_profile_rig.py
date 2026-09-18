"""Shared rig for the Operator Profile suites (not a test module).

Isolated runtime home + SQLite per test, a REAL ``VoolAgent`` (no model: every drive here lands on
a deterministic lane), the production ``dispatch_post`` / ``dispatch_get`` door, and the A8
minting helpers from the freeze suite. Nothing is mocked on the path under test.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

import storage.db as sdb

OWNER = "owner_local"


def profile_env_generator(tmp_path, monkeypatch):
    """The isolated-state fixture body. Each suite wraps it in its own ``@pytest.fixture`` so
    the name is a local definition (the freeze suites do the same), not a re-exported import."""
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    db_path = tmp_path / "profile.db"
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
    try:
        from core import operator_profile

        operator_profile.reset_table_cache_for_tests()
    except Exception:
        pass
    yield {"home": home, "db_path": db_path}
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)
    for _module in (_trace_id, _task_state):
        _module._TABLE_READY = False


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


def simulate_restart(env: dict[str, Any]) -> None:
    """A daemon restart in-process: every in-memory cache dropped, the SAME files reopened."""
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(env["home"])
    sdb.configure_default_db_path(env["db_path"])
    from storage.migrations import run_migrations

    run_migrations()
    from core import operator_profile

    operator_profile.reset_table_cache_for_tests()


def make_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="profile-test", persona_id="default")


def chat_body(text: str, session: str, *, turn_id: str = "") -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": text}],
        "session_id": session,
        "turn_id": turn_id or f"turn-{uuid.uuid4().hex[:10]}",
    }


def in_request_thread(fn, *args, **kwargs):
    """Run one served request on a FRESH thread, exactly as ThreadingHTTPServer does in production.

    A thread starts with an empty ContextVar context. Driving two turns on the test thread instead
    lets the first turn's admission state (a ContextVar) leak into the second turn's finalization
    -- a rig artifact the production handler never has. The A8 freeze suite avoids it with real
    sockets; this rig avoids it the same way at the thread boundary."""
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=_run, name="profile-rig-request")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def served_post(agent, path: str, body: dict[str, Any], *, client_host: str = "127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    def _dispatch():
        response = dispatch_post(
            path=path,
            body=body,
            headers={"Host": "127.0.0.1", "Content-Type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL", agent=agent),
            model_name="test-backend",
            workspace_root_provider=lambda: tempfile.mkdtemp(prefix="profile-ws-"),
            client_host=client_host,
            request_id=f"auto-{uuid.uuid4().hex}",
        )
        if getattr(response, "stream", None) is not None and not response.body:
            # Drain the stream on the request thread too (the generator finalizes in its frame).
            chunks = list(response.stream)
            response.stream = iter(chunks)
        return response

    return in_request_thread(_dispatch)


def served_get(agent, path: str, query: dict[str, list[str]] | None = None, *, client_host: str = "127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    return in_request_thread(
        dispatch_get,
        path=path,
        query=dict(query or {}),
        runtime=RuntimeServices(display_name="VOOL", agent=agent),
        model_name="test-backend",
        client_host=client_host,
    )


def run_once_in_thread(agent, text: str, *, session_id: str, source_context: dict[str, Any]) -> dict[str, Any]:
    """An in-process channel turn on a fresh thread (the channel gateway's own shape)."""
    return in_request_thread(agent.run_once, text, session_id_override=session_id, source_context=source_context)


def body_json(response) -> dict[str, Any]:
    raw = response.body if isinstance(response.body, (bytes, bytearray)) else b""
    if not raw and getattr(response, "stream", None) is not None:
        raw = b"".join(response.stream)
    return json.loads(raw.decode("utf-8") or "{}")


def chat(agent, text: str, session: str) -> dict[str, Any]:
    """One buffered served turn; returns {"text", "payload"}."""
    response = served_post(agent, "/api/chat", chat_body(text, session))
    assert int(response.status) == 200, (response.status, getattr(response, "body", b"")[:300])
    payload = body_json(response)
    text_out = str(((payload.get("message") or {}).get("content")) or "")
    return {"text": text_out, "payload": payload, "status": int(response.status)}


def chat_stream(agent, text: str, session: str) -> dict[str, Any]:
    """One streamed served turn; returns every parsed frame + the committed text."""
    body = chat_body(text, session)
    body["stream"] = True
    response = served_post(agent, "/api/chat", body)
    assert int(response.status) == 200
    frames: list[dict[str, Any]] = []
    for chunk in response.stream:
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                frames.append(json.loads(line))
    committed = ""
    for frame in frames:
        commit = frame.get("vool_response_commit")
        if isinstance(commit, dict) and commit.get("canonical_content"):
            committed = str(commit["canonical_content"])
    return {"frames": frames, "text": committed}


# --- A8 governed payload minting (freeze-suite vocabulary) -------------------

_turn_seq = [0]


@contextlib.contextmanager
def request_scope(request_id: str):
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    token = set_request_context(request_id)
    try:
        yield
    finally:
        _CURRENT_REQUEST_ID.reset(token)


def admit_finalize(text: str, request_id: str = "") -> dict[str, Any]:
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    _turn_seq[0] += 1
    turn = f"pt{_turn_seq[0]}"
    admit_semantic_result({"response": text, "route_reason": "model_lane"}, turn_id=turn)
    with request_scope(request_id):
        return finalize_answer(turn_id=turn, canonical_content=text)


def withhold(fid: str) -> None:
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="profile-test")


def erase(fid: str) -> dict[str, Any]:
    from core.finalization import erase_finalization_payload

    return erase_finalization_payload(fid, reason="profile-test")


def store_email_credential(account: str = "work") -> str:
    from core.credential_store import store_credential

    name = f"email.smtp.{account}"
    store_credential(
        name,
        json.dumps({"host": "smtp.example.invalid", "port": 465, "username": f"{account}@example.invalid",
                    "password": "app-password-1234", "from_addr": f"{account}@example.invalid"}),
        label=f"{account} mailbox",
    )
    return name


def profile_source_context(**overrides) -> dict[str, Any]:
    from core.request_trust import OWNER_LOCAL_KEY

    ctx: dict[str, Any] = {"surface": "web", OWNER_LOCAL_KEY: True}
    ctx.update(overrides)
    return ctx


def set_owner_preferred_name(name: str) -> None:
    """Legacy-suite helper: put (or clear, with "") the owner's global preferred name in the ONE
    authority, on whatever runtime home / DB is active. Replaces the old prefs.user_address write."""
    from core import operator_profile as profile

    for item in profile.list_items(OWNER):
        if item.category == "preferred_name":
            profile.forget_item(item.item_id, actor="test")
    if str(name or "").strip():
        change = profile.remember(OWNER, "preferred_name", name, origin="settings", actor="test", replace=True)
        assert change.kind in {"saved", "updated", "unchanged"}, change


def set_owner_email_signature(signature: str) -> None:
    """Legacy-suite helper: put (or clear, with "") the owner's global email signature in the ONE
    authority. Replaces the old prefs.email_signature write."""
    from core import operator_profile as profile

    for item in profile.list_items(OWNER):
        if item.category == "email_signature":
            profile.forget_item(item.item_id, actor="test")
    if str(signature or "").strip():
        change = profile.remember(OWNER, "email_signature", signature, origin="settings", actor="test", replace=True)
        assert change.kind in {"saved", "updated", "unchanged"}, change


def canonical_session(handle: str) -> str:
    """The runtime's canonical id for a served session handle (what the store keys on)."""
    from core.web.api.runtime import stable_openclaw_session_id

    return stable_openclaw_session_id(body={"session_id": handle}, history=[], headers={})


def data_dir(env: dict[str, Any]) -> Path:
    return Path(env["home"]) / "data"
