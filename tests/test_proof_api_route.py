"""The narrow proof read route: GET /api/chat/proof — one bound turn's evidence, nothing else.

The route is a thin door over the strictly read-only projection (`core.proof_projection`). It
owns no evidence logic of its own: parameters in, typed refusal or the proof document out. The
history endpoint gains the assistant message's `request_id` (additive), because the browser
needs the canonical turn identity to address the chip — without it a reloaded transcript could
only have guessed.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from core import runtime_paths
from core.persistent_memory import append_conversation_event
from core.semantic.semantic_admissions import bound_request_context
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get(path: str, query: dict[str, list[str]]):
    return dispatch_get(path=path, query=query, runtime=_rt(), model_name="vool")


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def _bind_turn(session: str, request_id: str, answer: str = "Answer.") -> None:
    with bound_request_context(request_id):
        append_conversation_event(
            session_id=session,
            user_input="Question",
            assistant_output=answer,
        )


def _insert_finalization(request_id: str, turn_id: str, content: str) -> str:
    from storage.db import get_connection

    finalization_id = "fc:" + hashlib.sha256(request_id.encode()).hexdigest()[:32]
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO a7_finalizations (
                finalization_id, semantic_result_id, turn_id, content_hash,
                canonical_content, status, request_id, payload_ref, availability
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                finalization_id,
                "sr-" + request_id,
                turn_id,
                "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                content,
                "answer_present",
                request_id,
                "available",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return finalization_id


def test_proof_route_serves_a_bound_turn() -> None:
    session = "proof-route-ok"
    _bind_turn(session, "req-route-ok", "The answer bytes.")
    _insert_finalization("req-route-ok", "turn-route-ok", "The answer bytes.")

    response = _get(
        "/api/chat/proof",
        {"session": [session], "request_id": ["req-route-ok"]},
    )
    assert response.status == 200
    payload = _body(response)
    assert payload["schema"] == "vool.turn_proof.v1"
    assert payload["bound"] is True
    assert payload["state"] in {"VERIFIED", "RECORDED", "INCOMPLETE", "UNVERIFIED"}
    assert payload["compact"]["state"] == payload["state"]


def test_proof_route_refuses_an_unbound_pair_with_typed_error() -> None:
    session = "proof-route-unbound"
    _bind_turn(session, "req-real")

    response = _get(
        "/api/chat/proof",
        {"session": [session], "request_id": ["req-never-served"]},
    )
    assert response.status == 404
    payload = _body(response)
    assert payload["error"] == "proof_not_bound"


def test_proof_route_requires_both_parameters() -> None:
    assert _get("/api/chat/proof", {"session": ["s"], "request_id": [""]}).status == 400
    assert _get("/api/chat/proof", {"session": [""], "request_id": ["r"]}).status == 400


def test_history_exposes_the_request_id_the_chip_addresses() -> None:
    session = "proof-route-history"
    _bind_turn(session, "req-history-1", "Served answer.")

    response = _get("/api/chat/history", {"session": [session]})
    assert response.status == 200
    messages = _body(response)["messages"]
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert assistant, "the served turn must be in the transcript"
    assert assistant[-1].get("request_id") == "req-history-1"
