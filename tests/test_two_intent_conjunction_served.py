"""Served proof: a noun-phrase conjunction that pairs a live-data ask with a demand no lane serves
dispatches BOTH halves, and the reply never reports the second half as "not dispatched".

Measured on a served daemon at f8937f80 (validation-logs/two-intent-routing-20260907/
TWO_INTENT_FINDING_origin.md): "get me latest on Iran and oil prices pls" answered the Brent quote
and closed with "get me latest on Iran -- not dispatched". The execution grain fused the headless
"oil prices" fragment into the Iran request, coverage read ONE unit, the live-data lane was allowed
to end the turn, and the demand census could only REPORT the news half instead of the runtime
ROUTING it. The same message with a question-shaped second clause ("... and what is gold trading
at?") split and served both halves, which is why this is a class defect and not a wording.

The daemon runs on a sealed fixture transport (tests/fixture_transport): quotes, a news feed and the
feed's pages are dated fixtures; every other host is refused. Assertions are environmental -- which
sub-requests the runtime opened and what ran under them, read from `runtime_session_events` -- never
the prose.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import tests._reader_served_rig as rig

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "two_intent_routing" / "two_intent_manifest.json"
SHIM = ROOT / "tests" / "fixture_transport"

OPERATOR_WORDING = "get me latest on Iran and oil prices pls"

#: (wording, the object of the demand no lane serves, a word from the live-data half). The
#: unserved half must open as its OWN sub-request and something must run under it.
TWO_INTENT_TURNS = [
    (OPERATOR_WORDING, "iran", "oil"),
    ("update me on Ukraine plus bitcoin price", "ukraine", "bitcoin"),
    ("oil prices and the latest on Iran", "iran", "oil"),
    ("tell me about Sudan and the silver price", "sudan", "silver"),
]

#: Events that prove a sub-request was DISPATCHED: a retrieval or a model call ran under it.
#: `model_lane_started` is the model lane handing the request to a provider -- the model call
#: itself; whether the scripted provider then answers well is the rig's business, not the route's.
DISPATCH_EVENTS = {
    "web_retrieval_started",
    "web_retrieval_completed",
    "model.call_started",
    "model_lane_started",
    "workflow_planner_step",
    "tool_selected",
    "live_data_plan_created",
}


def _events(home: Path, session_id: str, *, timeout_s: float = 90.0) -> list[tuple[int, str, str, dict]]:
    """Every runtime event of the session, once its trace is complete."""
    db = home / "data" / "vool_web0_v2.db"
    deadline = time.monotonic() + timeout_s
    rows: list[tuple[int, str, str, dict]] = []
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(str(db))
            try:
                raw = conn.execute(
                    "SELECT seq, event_type, message, details_json FROM runtime_session_events "
                    "WHERE session_id = ? ORDER BY seq",
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
            except Exception:
                parsed = {}
            rows.append((int(seq), str(event_type), str(message), parsed if isinstance(parsed, dict) else {}))
        if any(event_type == "turn.trace_completed" for _seq, event_type, _m, _d in rows):
            return rows
        time.sleep(0.5)
    return rows


def _sub_requests(rows: list[tuple[int, str, str, dict]]) -> list[tuple[int, str]]:
    """(seq, request text) of every request the runtime opened -- the whole turn first, then
    each planned sub-request."""
    out: list[tuple[int, str]] = []
    for seq, event_type, message, details in rows:
        if event_type != "task_received":
            continue
        preview = str(details.get("request_preview") or message.removeprefix("Received request: "))
        out.append((seq, preview))
    return out


def _ran_under(rows: list[tuple[int, str, str, dict]], start_seq: int, next_seq: int | None) -> list[str]:
    stop = next_seq if next_seq is not None else 10**9
    return [event_type for seq, event_type, _m, _d in rows if start_seq < seq < stop and event_type in DISPATCH_EVENTS]


@pytest.fixture(scope="module")
def drive(tmp_path_factory):
    assert MANIFEST.is_file(), MANIFEST
    tmp_path = tmp_path_factory.mktemp("two-intent-served")
    env_extra = {
        "PYTHONPATH": os.pathsep.join([str(SHIM), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
        "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(MANIFEST),
        "VOOL_FIXTURE_TRANSPORT_LOG": str(tmp_path / "transport.log"),
        "ALLOW_BROWSER_FALLBACK": "0",
        "WEB_SEARCH_PROVIDER_ORDER": "google_html",
    }
    # A plain sentence, not "ok": the planner calls cannot parse it (the conductor declines, as it
    # does for a model that returns no plan), and the answering lanes get a reply the synthesis
    # gate does not reject as empty.
    provider = rig.CapturingProvider(default="Nothing further was found in the sources available for this request.")
    provider.__enter__()
    daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra=env_extra)
    daemon.register_provider()
    daemon.start()
    try:
        cert = daemon.certify()
        assert cert.get("state") == "verified", cert
        yield daemon
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


@pytest.mark.parametrize("wording,unserved_object,live_word", TWO_INTENT_TURNS, ids=[t[0] for t in TWO_INTENT_TURNS])
def test_the_unserved_half_of_a_conjunction_is_dispatched_as_its_own_request(drive, wording, unserved_object, live_word):
    session = rig.canonical_session("two-intent-" + uuid.uuid4().hex[:8])
    reply = drive.chat(wording, session_id=session, mode="auto")
    content = str((reply.get("message") or {}).get("content") or "")
    rows = _events(drive.home, session)
    assert rows, f"no runtime events for the session\n{drive.log_tail()}"
    requests = _sub_requests(rows)
    assert requests and requests[0][1].lower().startswith(wording[:20].lower()), requests

    # THE ROUTE: the half no deterministic lane serves opens as its OWN request ...
    own = [
        (seq, text) for seq, text in requests[1:]
        if unserved_object in text.lower() and live_word not in text.lower()
    ]
    assert own, (
        f"the {unserved_object!r} half never became its own sub-request; requests opened: {requests}\n"
        f"events: {[(s, e) for s, e, _m, _d in rows]}"
    )
    # ... and something RAN under it: a retrieval or a model call, never a bare report.
    seqs = [seq for seq, _text in requests]
    for seq, _text in own:
        later = [s for s in seqs if s > seq]
        assert _ran_under(rows, seq, later[0] if later else None), (
            f"nothing ran under the {unserved_object!r} sub-request at seq {seq}: "
            f"{[(s, e) for s, e, _m, _d in rows if s > seq]}"
        )

    # THE REPLY never reports the second half as work the runtime declined to route.
    assert "not dispatched" not in content.lower(), content
    # And the live-data half is still served deterministically, not thrown away by the split.
    live_plans = [d for _s, e, _m, d in rows if e == "live_data_plan_created"]
    assert live_plans, f"the live-data half lost its typed plan: {[(s, e) for s, e, _m, _d in rows]}"


def test_a_conjunction_no_lane_serves_still_reaches_a_dispatch_and_never_reports_not_dispatched(drive):
    """Control at the class boundary: neither half is live data ("the euro rate" names no pair),
    so no deterministic lane may pre-empt; the whole turn must reach a retrieval or a model call
    and the reply must not disclose either half as not dispatched."""
    wording = "latest on Nvidia and the euro rate"
    session = rig.canonical_session("two-intent-" + uuid.uuid4().hex[:8])
    reply = drive.chat(wording, session_id=session, mode="auto")
    content = str((reply.get("message") or {}).get("content") or "")
    rows = _events(drive.home, session)
    assert any(e in DISPATCH_EVENTS for _s, e, _m, _d in rows), [(s, e) for s, e, _m, _d in rows]
    assert "not dispatched" not in content.lower(), content
