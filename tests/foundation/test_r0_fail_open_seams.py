"""R-0 fail-open seam deletions + the P0 served regression (A-11).

The served regression drives the REAL buffered /api/chat route through ASGI
with a stubbed model call — the class of test whose absence let the A-11 500
escape 482 green unit tests.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb
from tests.asgi_harness import asgi_request


@pytest.fixture()
def fresh_store(tmp_path, monkeypatch):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r0.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    # Footer ON — the exact configuration that crashed exact-contract turns.
    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "1")
    yield
    monkeypatch.delenv("VOOL_SHOW_USAGE_FOOTER", raising=False)
    sdb.configure_default_db_path(None)


# --- A-11 P0: served buffered /api/chat exact-contract turn -------------------

USER_TEXT = "reply with exactly: TOKEN-42 and nothing else"
EXACT_BYTES = "TOKEN-42"


def _stub_agent_result() -> dict:
    from core.response_provenance import append_provenance_footer

    raw = append_provenance_footer(
        EXACT_BYTES,
        {"route_reason": "model_lane", "confidence": 0.9},
    )
    assert raw != EXACT_BYTES  # the stub really carries a footer
    return {
        "response": raw,
        "success": True,
        "confidence": 0.9,
        "route_reason": "model_lane",
        "mode": "advice_only",
    }


def _chat_app(monkeypatch):
    import functools

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    runtime = RuntimeServices(display_name="VOOL")
    app = create_app(runtime)
    app.state.post_dispatcher = functools.partial(
        dispatch_post, run_agent_provider=lambda *a, **k: _stub_agent_result()
    )
    return app


def test_buffered_chat_exact_contract_with_footer_returns_200_exact_bytes(fresh_store, monkeypatch):
    app = _chat_app(monkeypatch)
    status, _headers, body = asgi_request(
        app,
        method="POST",
        path="/api/chat",
        headers={"Content-Type": "application/json", "X-Request-ID": "req-r0-p0"},
        body=json.dumps({"messages": [{"role": "user", "content": USER_TEXT}]}).encode(),
    )
    assert status == 200, body[:400]
    payload = json.loads(body)
    # Commit-first envelope: served bytes live at message.content.
    served = str((payload.get("message") or {}).get("content") or payload.get("response") or "")
    assert served.strip() == EXACT_BYTES


def test_red_a11_pointing_assert_at_footered_text_crashes_turn(fresh_store, monkeypatch):
    """RED MUTATION for A-11: with the boundary comparison pointed back at the
    footered display text (pre-R-0 defect), this exact turn must 500."""
    import core.response_provenance as rp

    # Reproduce the defect: 'strip' now leaves the footer in place.
    monkeypatch.setattr(rp, "strip_provenance_footer", lambda text: str(text or ""))
    app = _chat_app(monkeypatch)
    with pytest.raises(RuntimeError, match="K-08 violation"):
        asgi_request(
            app,
            method="POST",
            path="/api/chat",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"messages": [{"role": "user", "content": USER_TEXT}]}).encode(),
        )


def test_delivered_empty_evidence_class_refused(fresh_store):
    from core.finalization import (
        DELIVERY_ATTEMPTED_UNKNOWN,
        DELIVERY_DELIVERED,
        finalize_answer,
        set_delivery_status,
    )
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": "deliver", "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content="deliver")
    fid = commit["finalization_id"]
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    # RED MUTATION: empty-class DELIVERED is refused.
    assert not set_delivery_status(fid, DELIVERY_DELIVERED)
    # Explicit evidence class is honored.
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="TRANSPORT_HANDOFF")


# --- A-13.1/.2: availability events honest + atomic ---------------------------

def test_refused_availability_transition_appends_no_event(fresh_store):
    from core.finalization import (
        AVAILABILITY_ERASED,
        finalize_answer,
        set_availability,
    )
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": "gov bytes", "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content="gov bytes")
    fid = commit["finalization_id"]
    # RED MUTATION: double-ERASE — second transition refused, no phantom event.
    assert set_availability(fid, AVAILABILITY_ERASED, reason="first")
    assert not set_availability(fid, AVAILABILITY_ERASED, reason="dup")
    conn = sdb.get_connection()
    try:
        events = conn.execute(
            "SELECT COUNT(*) c FROM a7_governance_events WHERE finalization_id = ? "
            "AND event_kind = 'availability_transition'",
            (fid,),
        ).fetchone()
        assert int(events["c"]) == 1
        # A8 pass-001: the single lawful ERASE also tombstones the PRE-ERASE
        # content_hash exactly once (digest-retention law); the refused
        # duplicate transition appends neither kind of event.
        tombstones = conn.execute(
            "SELECT COUNT(*) c FROM a7_governance_events WHERE finalization_id = ? "
            "AND event_kind = 'erasure_digest_tombstone'",
            (fid,),
        ).fetchone()
        assert int(tombstones["c"]) == 1
        row = conn.execute(
            "SELECT canonical_content, content_hash FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
        # One transaction: ERASE leaves no plaintext and salts the digest.
        assert row["canonical_content"] == ""
        assert row["content_hash"].startswith("salted-sha256:")
    finally:
        conn.close()
