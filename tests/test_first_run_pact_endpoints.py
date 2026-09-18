"""M-P2 — the served pact endpoints: contract shapes, guards, and the one-command law."""
from __future__ import annotations

import json

import pytest

from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture


def test_get_pact_serves_the_full_projection_with_live_authority_truth(pact_rig):
    status, payload = pact_rig.get("/api/onboarding/pact")
    assert status == 200
    assert payload["schema_name"] == "vool.first_run_pact"
    assert payload["state"] in {"absent", "welcome", "not_applicable"}
    for key in ("revision", "seed_reason", "live", "facts", "provider", "steps"):
        assert key in payload
    live = payload["live"]
    for key in ("local_only_mode", "allow_web_fallback", "memory_paused", "share_scope",
                "agent_display_name", "cloud_connection", "egress"):
        assert key in live
    assert payload["task_prompt"] and payload["task_prompt_direct"] and payload["denial_prompt"]
    assert payload["denial_prompt_direct"]
    # The GET never echoes secrets and never returns provider key material.
    body = json.dumps(payload)
    assert "sk-" not in body


def test_post_is_one_registered_command_and_409s_are_typed(pact_rig):
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/begin", {})
    assert status == 200 and payload["ok"] is True and payload["state"] == "welcome"
    # wrong transition
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/advance", {"to": "provider_choice", "expect_revision": snap["revision"]})
    assert status == 409 and payload["error"] == "invalid_transition"
    # stale revision
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/advance", {"to": "naming", "expect_revision": snap["revision"] - 1})
    assert status == 409 and payload["error"] == "stale_revision"
    # evidence-locked transition refuses an asserted jump (the anti-canning seam)
    pact_rig.walk_to("local_task")
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/advance", {"to": "task_receipt", "expect_revision": snap["revision"]})
    assert status == 409 and payload["error"] == "invalid_transition"


def test_evidence_missing_is_the_typed_fault_for_a_fabricated_claim(pact_rig):
    pact_rig.walk_to("local_task")
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": "req:http:fabricated-0001",
        "expect_revision": snap["revision"],
    })
    assert status == 409
    assert payload["error"] in {"evidence_missing", "evidence_session_unknown", "evidence_unverifiable"}


def test_unknown_boundary_key_is_a_typed_400(pact_rig):
    pact_rig.pact()
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {"key": "bogus_key", "value": True, "expect_revision": snap["revision"]})
    assert status == 400 and payload["error"] == "boundary_key_unknown"


def test_get_endpoints_refuse_non_loopback_callers(pact_rig):
    from core.web.api.service import dispatch_get

    response = dispatch_get(path="/api/onboarding/pact", query={}, runtime=pact_rig.runtime,
                            model_name="vool", client_host="10.1.2.3")
    assert response.status == 403


def test_post_guards_cross_origin_and_non_json_and_non_loopback(pact_rig):
    snap = pact_rig.pact()
    status, payload = pact_rig.post(
        "/api/onboarding/pact/skip", {},
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )
    assert status == 403

    status, payload = pact_rig.post(
        "/api/onboarding/pact/skip", {},
        headers={"Content-Type": "text/plain"},
        client_host="127.0.0.1",
    )
    assert status == 415

    status, payload = pact_rig.post("/api/onboarding/pact/skip", {}, client_host="10.9.9.9")
    assert status == 403 and payload.get("error") == "owner_local_required"


def test_provider_state_endpoint_projects_the_choice_machine(pact_rig):
    status, payload = pact_rig.get("/api/onboarding/state")
    assert status == 200
    assert payload["state"] == "absent"
    status, payload = pact_rig.post("/api/onboarding/choice", {"choice": "local_only"})
    assert status == 200 and payload["state"] == "local_only_done"
    status, payload = pact_rig.post("/api/onboarding/choice", {"choice": "local_only"})
    assert status == 200  # idempotent on terminal
    status, payload = pact_rig.post("/api/onboarding/reset", {})
    assert status == 200 and payload["state"] == "card_visible"


def test_every_pact_command_appears_in_the_served_registry_projection(pact_rig):
    status, payload = pact_rig.get("/api/commands")
    assert status == 200
    ids = {row["command_id"] for row in payload["commands"]}
    expected = {
        "first_run.pact.read", "first_run.pact.seed", "first_run.pact.begin",
        "first_run.pact.advance", "first_run.pact.hide", "first_run.pact.skip",
        "first_run.pact.reset", "first_run.pact.name", "first_run.pact.facts.set",
        "first_run.pact.facts.forget", "first_run.pact.boundary.set",
        "first_run.pact.task.claim", "first_run.pact.denial.claim",
        "first_run.choice.local_only", "onboarding.state", "onboarding.choice",
        "onboarding.reset", "intake.begin", "intake.classify", "intake.preview",
        "intake.verify", "intake.complete",
    }
    missing = expected - ids
    assert not missing, f"registry projection missing: {missing}"
    rows = {row["command_id"]: row for row in payload["commands"]}
    assert rows["first_run.pact.read"]["permission"] == "open_read"
    assert rows["first_run.pact.boundary.set"]["permission"] == "operator_authority:first_run.pact"
    assert rows["intake.verify"]["effects"] == "external_send"
