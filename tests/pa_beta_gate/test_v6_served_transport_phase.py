"""pa_beta_gate -- revision-6 served recovery for contract 3: acceptance survives a lost reply.

Real agent turns (``VoolAgent.run_once``) through the production conversational path against the
disposable loopback calendar services and the REAL VOOL transport (sockets on 127.0.0.1). MODEL STAND-IN
(labelled): the deterministic stand-in the served workflow tests use. The services lose a reply at a
precise point: after sending a success status (the body is cut off inside its first chunk), or after
applying a write without sending any reply at all. Every test counts the writes the service received
and reads durable approval state, not only the answer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tests.pa_beta_gate import _caldav_service
from tests.pa_beta_gate.test_served_calendar_notes_workflows import (  # noqa: F401 -- served_env is a fixture, requested via getfixturevalue
    VILNIUS_CAL,
    _approval_id,
    _turn,
    served_env,
)
from tests.pa_beta_gate.test_v6_served_outcome_recovery import (  # noqa: F401 -- served_graph_env is a fixture, requested via getfixturevalue
    GRAPH_CAL,
    _approval_row,
    _graph_titled,
    served_graph_env,
)


def _count(monkeypatch, handler, method):
    seen: list[str] = []
    name = f"do_{method}"
    real = getattr(handler, name)

    def counted(self):
        seen.append(self.path)
        return real(self)

    monkeypatch.setattr(handler, name, counted)
    return seen


def _cut_off_next_success_reply(monkeypatch, server, method):
    """The next success reply to ``method`` sends its status and headers, then half of its first chunk, and closes."""
    handler = server.RequestHandlerClass
    real_reply = handler._reply
    armed = {"on": True}

    def _reply(self, status, body, headers=None):
        if not (armed["on"] and self.command == method and 200 <= status < 300):
            return real_reply(self, status, body, headers)
        armed["on"] = False
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(f"{len(payload):x}\r\n".encode("ascii") + payload[: len(payload) // 2])
        self.wfile.flush()
        self.close_connection = True

    monkeypatch.setattr(handler, "_reply", _reply)


def _drop_next_success_reply(monkeypatch, method):
    """The CalDAV service applies the next ``method``, then closes the connection without any reply."""
    handler = _caldav_service._Handler
    real_reply = handler._reply
    armed = {"on": True}

    def _reply(self, status, body=b"", content_type="text/plain", headers=None):
        if not (armed["on"] and self.command == method and 200 <= status < 300):
            return real_reply(self, status, body, content_type, headers)
        armed["on"] = False
        self.close_connection = True

    monkeypatch.setattr(handler, "_reply", _reply)


def test_served_graph_create_whose_reply_body_is_cut_off_rests_accepted_and_reapproval_verifies(request, monkeypatch):
    """ORIGINAL defect served: a 201 whose body arrives incomplete (the review's 'incomplete' case)."""
    env = request.getfixturevalue("served_graph_env")
    harness, state, workspace, server = env["harness"], env["state"], env["workspace"], env["server"]
    posts = _count(monkeypatch, server.RequestHandlerClass, "POST")
    action_id = _approval_id(_turn(harness, 'propose "Transformer review" on 2026-09-16 11:00 Europe/Berlin for 30m', workspace))
    _cut_off_next_success_reply(monkeypatch, server, "POST")

    accepted = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "accepted the event" in accepted and "NOT reported as never created" in accepted, accepted
    assert len(posts) == 1 and len(_graph_titled(state, "Transformer review")) == 1
    row = _approval_row(harness.session_id, action_id)
    assert row["status"] == "outcome_unproven" and json.loads(row["result_json"])["operation"]["phase"] == "accepted", row

    verified = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in verified, verified
    assert len(posts) == 1 and len(_graph_titled(state, "Transformer review")) == 1
    assert _approval_row(harness.session_id, action_id)["status"] == "executed"


def test_served_caldav_create_whose_reply_never_arrives_is_unknown_and_reapproval_reconciles(request, monkeypatch):
    """NOVEL served: the service stores the event and closes the connection without sending any reply."""
    env = request.getfixturevalue("served_env")
    harness, state, workspace = env["harness"], env["state"], env["workspace"]
    puts = _count(monkeypatch, _caldav_service._Handler, "PUT")
    title = "Novel compressor swap"
    action_id = _approval_id(_turn(harness, f'propose "{title}" on 2026-09-16 13:00 Europe/Berlin for 45m', workspace))
    _drop_next_success_reply(monkeypatch, "PUT")

    unknown = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "could not be proven" in unknown and "can't reach" not in unknown, unknown
    stored = [uid for uid, row in state.snapshot()[VILNIUS_CAL].items() if row["summary"] == title]
    assert len(puts) == 1 and len(stored) == 1
    assert _approval_row(harness.session_id, action_id)["status"] == "outcome_unproven"

    reconciled = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in reconciled, reconciled
    assert len(puts) == 1 and [uid for uid, row in state.snapshot()[VILNIUS_CAL].items() if row["summary"] == title] == stored


def test_served_graph_update_whose_reply_body_is_cut_off_is_accepted_and_reapproval_verifies(request, monkeypatch):
    """NOVEL served: a move whose 200 reply body is cut off after the service applied it."""
    env = request.getfixturevalue("served_graph_env")
    harness, state, workspace, server = env["harness"], env["state"], env["workspace"], env["server"]
    state.seed_event(GRAPH_CAL, "survey@fixture", summary="Novel valve survey", start=datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc), minutes=30)
    patches = _count(monkeypatch, server.RequestHandlerClass, "PATCH")
    action_id = _approval_id(_turn(harness, 'move the "Novel valve survey" event to Friday at 11:00 Europe/Berlin', workspace))
    _cut_off_next_success_reply(monkeypatch, server, "PATCH")

    accepted = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "accepted the update" in accepted and "NOT reported as unchanged" in accepted, accepted
    assert len(patches) == 1

    verified = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "updated on the provider and verified" in verified and len(patches) == 1, verified
