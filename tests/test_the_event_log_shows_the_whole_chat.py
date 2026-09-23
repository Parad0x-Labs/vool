"""The Event log at chat scope must show the CHAT, not its newest turn."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path("core/vool_chat_page.py")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

def _run(view, scope="chat"):
    src = PAGE.read_text(encoding="utf-8")
    start = src.index("function eventLogSections() {")
    end = src.index("function eventLogText(sections)", start)
    body = src[start:end]
    script = (
        "function evTitle(ev){return String(ev.summary||ev.type||'');}\n"
        + f"let view = {json.dumps(view)};\nconst panelScope = {json.dumps(scope)};\n"
        + "const scopedEvidence = { sessions: [] };\n"
        + body
        + "\nprocess.stdout.write(JSON.stringify(eventLogSections()));\n"
    )
    p = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)

def _ev(seq, turn, msg):
    return {"seq": seq, "client_turn_id": turn, "event_type": "status", "message": msg}

def test_every_turn_in_the_chat_appears() -> None:
    view = {"chatLedger": [_ev(1,"t1","first"), _ev(2,"t2","second"), _ev(3,"t3","third")], "run": None}
    rows = [r for s in _run(view) for r in s["rows"]]
    assert any("first" in r for r in rows)
    assert any("second" in r for r in rows)
    assert any("third" in r for r in rows), "older turns were dropped -- the log shows only the newest"

def test_turns_are_grouped() -> None:
    view = {"chatLedger": [_ev(1,"t1","a"), _ev(2,"t2","b")], "run": None}
    titles = [s.get("title") for s in _run(view)]
    assert len([t for t in titles if t]) >= 2

def test_a_live_turn_is_appended_not_substituted() -> None:
    """The old branch REPLACED history with the in-flight turn."""
    view = {"chatLedger": [_ev(1,"t1","older turn")], "run": {"events": [{"type":"tool.started","summary":"live now"}]}}
    rows = [r for s in _run(view) for r in s["rows"]]
    assert any("older turn" in r for r in rows), "history vanished while a turn was running"
    assert any("live now" in r for r in rows)

def test_an_empty_chat_is_empty() -> None:
    assert _run({"chatLedger": [], "run": None}) == []

def test_the_recovered_fallback_still_works() -> None:
    view = {"chatLedger": [], "run": None, "recoveredLedger": [{"event_type":"status","message":"restored"}]}
    rows = [r for s in _run(view) for r in s["rows"]]
    assert any("restored" in r for r in rows)

def test_tagged_node_and_attempt_events_group_inside_their_turn() -> None:
    """The exact shapes that orphaned live on 2026-08-14 (session openclaw:ed890df3): agent_node_*
    and runtime_attempt_* rows, once tagged with the turn's client_turn_id, must land INSIDE the
    turn's section -- not in a separate untagged ("Between turns") group."""
    view = {"chatLedger": [
        {"seq": 1, "client_turn_id": "t1", "event_type": "task_received", "message": "weather in paris and berlin"},
        {"seq": 2, "client_turn_id": "t1", "event_type": "runtime_attempt_created", "message": "Runtime attempt a1 created (LIVE_DATA)."},
        {"seq": 3, "client_turn_id": "t1", "event_type": "agent_node_started", "message": "livedata-1:weather:paris started (weather_lookup)"},
        {"seq": 4, "client_turn_id": "t1", "event_type": "agent_node_completed", "message": "livedata-1:weather:paris succeeded in 0.4s"},
        {"seq": 5, "client_turn_id": "t1", "event_type": "runtime_attempt_completed", "message": "Runtime attempt a1 -> SUCCEEDED."},
        {"seq": 6, "client_turn_id": "t1", "event_type": "task_completed", "message": "done"},
    ], "run": None}
    sections = _run(view)
    assert len(sections) == 1, f"tagged events split into {len(sections)} sections: {sections}"
    rows = sections[0]["rows"]
    assert any("Runtime attempt a1 created" in r for r in rows)
    assert any("livedata-1:weather:paris started" in r for r in rows)

def test_untagged_events_still_form_their_own_group() -> None:
    """Negative control: an event with NO client_turn_id (a restart sweep, a retry dispatcher row)
    must stay out of the turn's section rather than be absorbed into an adjacent turn."""
    view = {"chatLedger": [
        {"seq": 1, "client_turn_id": "t1", "event_type": "task_received", "message": "in the turn"},
        {"seq": 2, "event_type": "runtime_attempt_completed", "message": "Runtime attempt swept -> ABANDONED."},
    ], "run": None}
    sections = _run(view)
    assert len(sections) == 2, sections
    turn_rows = next(section["rows"] for section in sections if any("in the turn" in row for row in section["rows"]))
    assert any("swept" in row for section in sections for row in section["rows"])
    assert not any("swept" in r for r in turn_rows), "an untagged sweep row was absorbed into the turn"
