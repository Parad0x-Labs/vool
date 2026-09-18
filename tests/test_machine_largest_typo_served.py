"""Served: the operator's mistyped largest-file question is claimed by the machine fast path.

The packaged app (2026-09-07) sent "what is the alrgest single file on my machine?" to a model
lane, which proposed a sandbox command. Through a real isolated daemon and the real /api/chat
door, the turn must now run `machine.find_largest` itself: the tool appears in the session's
runtime events, and no model call is made for the turn (the scripted provider is never asked).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import tests._reader_served_rig as rig

TYPO = "what is the alrgest single file on my machine?"


def _events(home: Path, session_id: str, *, timeout_s: float = 90.0) -> list[tuple[int, str, str, dict]]:
    db = home / "data" / "vool_web0_v2.db"
    deadline = time.monotonic() + timeout_s
    rows: list[tuple[int, str, str, dict]] = []
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(str(db))
            try:
                raw = conn.execute(
                    "SELECT seq, event_type, message, details_json FROM runtime_session_events WHERE session_id = ? ORDER BY seq",
                    (session_id,),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            raw = []
        rows = []
        for seq, event_type, message, details in raw:
            try:
                parsed = json.loads(details or "{}")
            except ValueError:
                parsed = {}
            rows.append((int(seq), str(event_type), str(message or ""), parsed if isinstance(parsed, dict) else {}))
        if any(et == "turn.trace_completed" for _s, et, _m, _d in rows):
            return rows
        time.sleep(0.5)
    return rows


@pytest.mark.timeout(600)
def test_the_typo_turn_runs_the_largest_file_tool_without_a_model(tmp_path):
    provider = rig.CapturingProvider(default="I would have to guess; no tool result was given to me.")
    provider.__enter__()
    daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={})
    daemon.register_provider()
    try:
        daemon.start()
    except RuntimeError as exc:
        daemon.stop()
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    try:
        session = rig.canonical_session("largest-typo-" + uuid.uuid4().hex[:8])
        reply = daemon.chat(TYPO, session_id=session, mode="auto")
        content = str((reply.get("message") or {}).get("content") or "")
        rows = _events(daemon.home, session)
        kinds = [et for _s, et, _m, _d in rows]
        assert "turn.trace_completed" in kinds, kinds
        touched = [(et, m) for _s, et, m, d in rows if "machine.find_largest" in m or str(d.get("tool_name") or "") == "machine.find_largest"]
        assert touched, f"the fast path never ran machine.find_largest; events: {kinds}\nreply: {content[:300]}"
        assert "model.call_started" not in kinds and "model_lane_started" not in kinds, (
            "a model was consulted for a question the runtime answers deterministically: " + str(kinds)
        )
        assert "sandbox.run_command" not in " ".join(m for _s, _e, m, _d in rows)
        # New wording, same class (the operator's rule): a different typo, a different noun, same tool, no model.
        session2 = rig.canonical_session("largest-typo-b-" + uuid.uuid4().hex[:8])
        daemon.chat("whats the bigest file on this mac?", session_id=session2, mode="auto")
        rows2 = _events(daemon.home, session2)
        kinds2 = [et for _s, et, _m, _d in rows2]
        assert any("machine.find_largest" in m for _s, _e, m, _d in rows2), kinds2
        assert "model.call_started" not in kinds2 and "model_lane_started" not in kinds2, kinds2
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
