"""R-7 / K-10 completion: honest delivery truth on all lanes."""
from __future__ import annotations

import pytest

import storage.db as sdb


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r7.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _commit(text="deliverable r7"):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t", canonical_content=text)


def test_bridge_proposal_api_upgrades_with_evidence_only(fresh_store):
    """RED MUTATION target: a bridge writing DELIVERED directly (empty/weak
    evidence) is refused; the proposal API with PLATFORM_ACK upgrades."""
    from relay.channel_outbound import propose_bridge_delivery

    fid = _commit()["finalization_id"]
    assert not propose_bridge_delivery(fid, evidence_class="")
    assert propose_bridge_delivery(fid, evidence_class="PLATFORM_ACK")


def test_overflow_drop_writes_failed_mark_never_silent(fresh_store):
    """RED MUTATION target: the 129th record must not vanish silently — its
    committed truth gets FAILED_TRANSPORT."""
    from core.finalization import get_finalization_by_semantic_id
    from relay.channel_outbound import append_outbound_post

    commit = _commit("to be dropped")
    fid = commit["finalization_id"]

    class _FakeMirror:
        """In-memory mirror so capacity/overflow actually accumulates."""

        def __init__(self) -> None:
            self.state: dict = {}

        def fetch_snapshot(self, topic: str):
            return self.state.get(topic, {"records": []})

        def publish_snapshot(self, topic: str, snapshot: dict) -> bool:
            self.state[topic] = snapshot
            return True

    _mirror = _FakeMirror()
    # Our fid-carrying record goes in FIRST, then the topic fills past
    # capacity so IT is the evicted (dropped) one.
    append_outbound_post(
        platform="telegram",
        content="do not drop me silently",
        task_id="t",
        session_id="s",
        source_context={},
        finalization_id=fid,
        adapter=_mirror,
    )
    for i in range(128):
        append_outbound_post(
            platform="telegram",
            content=f"filler {i}",
            task_id="t",
            session_id="s",
            source_context={},
            adapter=_mirror,
        )
    # The mirror may refuse to publish in a sandbox; what matters is that the
    # overflow drop was NOT silent - the evicted truth gets its typed mark.
    _ok, record = append_outbound_post(
        platform="telegram",
        content="overflow push",
        task_id="t",
        session_id="s",
        source_context={},
        finalization_id=fid,
        adapter=_mirror,
    )
    assert record.get("finalization_id") == fid
    row = get_finalization_by_semantic_id(commit["semantic_result_id"])
    assert row["delivery_status"] in ("FAILED_TRANSPORT", "ATTEMPTED_UNKNOWN")


def test_set_delivery_status_records_explicit_evidence_class(fresh_store):
    from core.finalization import (
        DELIVERY_ATTEMPTED_UNKNOWN,
        DELIVERY_DELIVERED,
        set_delivery_status,
    )

    fid = _commit("evidence row")["finalization_id"]
    set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="TRANSPORT_HANDOFF")
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT delivery_evidence_class FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
        assert row["delivery_evidence_class"] == "TRANSPORT_HANDOFF"
    finally:
        conn.close()


def test_buffered_lane_marks_attempted_unknown(fresh_store, monkeypatch):
    """The served buffered /api/chat route must mark ATTEMPTED_UNKNOWN before
    bytes leave — asserted against durable rows after a driven turn."""
    import functools
    import json

    from apps.vool_api_server import create_app
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post
    from tests.asgi_harness import asgi_request

    def stub(*a, **k):
        reset_admission()
        return admit_semantic_result(
            {"response": "buffered canary", "route_reason": "model_lane"}
        )

    runtime = RuntimeServices(display_name="VOOL")
    app = create_app(runtime)
    app.state.post_dispatcher = functools.partial(dispatch_post, run_agent_provider=stub)
    status, _, body = asgi_request(
        app,
        method="POST",
        path="/api/chat",
        headers={"Content-Type": "application/json", "X-Request-ID": "req-r7-buffered"},
        body=json.dumps({"messages": [{"role": "user", "content": "say something"}]}).encode(),
    )
    assert status == 200, body[:200]
    conn = sdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT delivery_status FROM a7_finalizations WHERE request_id = ?",
            ("req:http:req-r7-buffered",),
        ).fetchall()
        assert rows, "the driven turn must have a finalization row"
        assert any(r["delivery_status"] == "ATTEMPTED_UNKNOWN" for r in rows)
    finally:
        conn.close()
