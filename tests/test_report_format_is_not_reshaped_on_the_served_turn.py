"""On a real served /v1/chat/completions turn, the automatic presentation repair never rewrites an agent's turn or a
reply in the format the person spelled out; a plain question still gets the repair.

Measured on the live agent-team comparison (2026-10-07): each agent's correct `RESULT: {...}` draft was followed by
an automatic "Present the answer as a table" call, and the published replies lost their report lines. The selector's
stand-downs are proven at the selector in tests/test_requested_report_format_is_not_reshaped.py; this file proves the
served seam carries what they read (the request's turn_author and the user's own text) to the router, so the bounded
repair call is never bound for those turns. Rig and free_local reclassification as in
tests/test_presentation_served_turns.py::test_s1b_free_local_repair_journey_then_paid_cloud_restore.
"""
from __future__ import annotations

import json

import pytest

from tests._served_skill_rig import PROVIDER_MANIFEST_ID, ProviderState, ServedDaemon, make_provider_server
from tests.test_presentation_served_turns import (
    _PROSE_WITH_TABLE_SHAPE,
    _REPAIRED_TABLE,
    RETRY_MARKER,
    _read_daemon_manifest,
    _register_daemon_manifest,
    _selection_of,
)

PLAIN_ASK = "walk me through both plans in detail"
FORMAT_ASK = "walk me through both plans, one line per plan, as `Plan X: deductible, coverage`"


@pytest.fixture(scope="module")
def free_local_rig(tmp_path_factory):
    """A certified daemon whose provider lane is free_local, the only lane the automatic repair runs on."""
    tmp = tmp_path_factory.mktemp("report-format-served-rig")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    daemon = ServedDaemon(home, port).start()
    daemon.pin_provider_model()
    cert = daemon.certify_provider_model()
    result = cert.get("result") or cert
    assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"
    rig = type("Rig", (), {"state": state, "daemon": daemon, "home": home, "tmp": tmp})
    original = _read_daemon_manifest(rig)
    _register_daemon_manifest(rig, original.model_copy(update={"metadata": {
        **(original.metadata or {}), "cost_class": "free_local", "deployment_class": "local"}}))
    assert (_read_daemon_manifest(rig).metadata or {}).get("cost_class") == "free_local"
    try:
        yield rig
    finally:
        _register_daemon_manifest(rig, original)
        daemon.stop()
        server.shutdown()


def _turn(rig, text: str, turn_id: str, **body_extra) -> tuple[dict, list]:
    rig.state.requests.clear()
    rig.state.script = [
        {"final": _PROSE_WITH_TABLE_SHAPE},
        {"prompt_contains": RETRY_MARKER, "final": _REPAIRED_TABLE},
    ]
    reply = rig.daemon.post("/v1/chat/completions", {
        "model": PROVIDER_MANIFEST_ID,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "mode": "manual",
        "session_id": f"report-format-{turn_id}",
        "turn_id": turn_id,
        **body_extra,
    })
    return reply, list(rig.state.turn_requests)


def _repairs(calls: list) -> list:
    """The bound repair calls (a turn may also bind other calls, such as the request splitter)."""
    return [call for call in calls if RETRY_MARKER in json.dumps(call, ensure_ascii=False)]


def _answer(reply: dict) -> str:
    return str(reply["choices"][0]["message"].get("content") or "")


def test_an_agent_turn_binds_no_repair_and_ships_its_own_bytes(free_local_rig):
    reply, calls = _turn(free_local_rig, PLAIN_ASK, "agent-1", turn_author="agent")
    assert _repairs(calls) == [], f"an agent's turn must bind no repair call, saw {len(_repairs(calls))}"
    assert "Plan B: deductible €25, coverage full." in _answer(reply), _answer(reply)[:300]
    assert _selection_of(reply).get("disabled_by") == "agent_turn"


def test_a_request_that_spells_out_its_format_binds_no_repair(free_local_rig):
    reply, calls = _turn(free_local_rig, FORMAT_ASK, "format-1")
    assert _repairs(calls) == [], f"a reply in the requested format must bind no repair call, saw {len(_repairs(calls))}"
    assert "Plan B: deductible €25, coverage full." in _answer(reply), _answer(reply)[:300]
    assert _selection_of(reply).get("disabled_by") == "request_format"


def test_a_plain_question_still_gets_the_one_repair(free_local_rig):
    reply, calls = _turn(free_local_rig, PLAIN_ASK, "plain-1")
    assert len(_repairs(calls)) == 1, f"expected ONE repair call, saw {len(_repairs(calls))}"
    assert "| B | €25 | full |" in _answer(reply), _answer(reply)[:300]
