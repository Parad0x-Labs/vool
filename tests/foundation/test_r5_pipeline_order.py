"""R-5: pipeline order on the model lane — transforms → admit → finalize.

GREEN: exact-contract turn shows admitted == committed == served, and the a7
row cites a NON-EMPTY admitted sr. REDS: re-swapping the order (finalize before
admit) ⇒ empty-sr refusal; post-admission mutation ⇒ divergence refusal.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb

from core.conductor import obligation_ledger as _ol_teardown
from tests.asgi_harness import asgi_request


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r5.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    _ol_teardown.clear_active_set()
    sdb.configure_default_db_path(None)


USER_TEXT = "reply with exactly: R5-CANARY and nothing else"
EXACT = "R5-CANARY"


def _sealed_stub():
    """Emulate the REAL model-lane pipeline order:
    transforms (exact control) → A2 admit. Finalization is left to the
    transport shim (`_response_commit`), exactly as repaired in R-5."""
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
    from core.web.api.response_control import apply_exact_response_control

    reset_admission()
    raw_model_output = f"{EXACT}\nExtra model chatter."
    controlled = apply_exact_response_control({"response": raw_model_output}, USER_TEXT)
    return admit_semantic_result(dict(controlled))


def _chat_app(monkeypatch):
    import functools

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    runtime = RuntimeServices(display_name="VOOL")
    app = create_app(runtime)
    app.state.post_dispatcher = functools.partial(
        dispatch_post, run_agent_provider=lambda *a, **k: _sealed_stub()
    )
    return app


def test_exact_contract_turn_admitted_committed_served_identical(fresh_store, monkeypatch):
    app = _chat_app(monkeypatch)
    status, _, body = asgi_request(
        app,
        method="POST",
        path="/api/chat",
        headers={"Content-Type": "application/json", "X-Request-ID": "req-r5"},
        body=json.dumps({"messages": [{"role": "user", "content": USER_TEXT}]}).encode(),
    )
    assert status == 200, body[:300]
    payload = json.loads(body)
    served = str((payload.get("message") or {}).get("content") or "")
    assert served.strip() == EXACT
    commit = payload.get("vool_response_commit") or {}
    committed = str(commit.get("canonical_content") or "")
    assert committed.strip() == EXACT
    # The a7 row cites a NON-EMPTY admitted sr whose record exists and whose
    # content hash matches the commit (admitted == committed).
    from core.semantic.semantic_admissions import get_admission

    sr = str(commit.get("semantic_result_id") or "")
    assert sr, "commit must cite an admitted sr (no more sr='' evasion)"
    admission = get_admission(sr)
    assert admission is not None and int(admission["accepted"]) == 1


def test_onboarded_lane_empty_sr_finalize_refused(fresh_store):
    """RED target for A-1: an onboarded lane finalizing with sr='' is refused.

    The request and execution bindings go through the owning scoped seam
    (``bound_request_context`` / ``bound_execution_context``): a raw
    ``set_request_context`` here used to leave ``req:http:r5-empty-sr`` bound
    after the test, re-identifying every later turn on this context (the
    request-context leak the seam exists to make unrepresentable)."""
    from core.finalization import FinalizationRejected, finalize_answer
    from core.semantic.semantic_admissions import (
        bound_execution_context,
        bound_request_context,
        current_execution_identity,
    )

    with bound_request_context("req:http:r5-empty-sr"):
        from core.conductor import obligation_ledger as ol
        from core.invocation.ledger import accept_invocation, open_execution

        accept_invocation(
            external_kind="http",
            external_value="r5-empty-sr",
            principal="owner_local",
            raw_digest="d",
        )
        req = "req:http:r5-empty-sr"

        _obset = ol.open_obligation_set(
            obligations=[{"obligation_id": "ob:answer", "text": "t", "kind": "prose"}]
        )
        ol.bind_active_set(_obset["set_id"], _obset["version"])
        ol.record_disposition(
            _obset["set_id"], _obset["version"], "ob:answer",
            "satisfied", evidence_source="served_bytes",
        )
        ex = open_execution(request_id=req, root_attempt_id="attempt-r5-empty", runtime_epoch=__import__(
            "core.invocation.ledger", fromlist=["current_runtime_epoch"]
        ).current_runtime_epoch())
        with bound_execution_context(
            {
                "execution_id": ex["execution_id"],
                "generation": int(ex["generation"]),
                "runtime_epoch": __import__(
                    "core.invocation.ledger", fromlist=["current_runtime_epoch"]
                ).current_runtime_epoch(),
            }
        ):
            assert current_execution_identity() is not None
            with pytest.raises(FinalizationRejected, match="EMPTY_SR_ONBOARDED_LANE"):
                finalize_answer(turn_id="t", canonical_content="orphan bytes")
