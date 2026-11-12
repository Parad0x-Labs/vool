"""Command-registry lane for the social content group (Checkpoint 2).

Proves the compact command family goes through the canonical dispatch seam:
availability probe, OperatorAuthority gate (a model principal cannot mutate),
typed inputs, receipts, and read/mutate effect-class separation.
"""
from __future__ import annotations

import pytest

from core.command_registry.execute import ExecutionContext, execute_command


@pytest.fixture()
def clean_store():
    from storage.db import get_connection

    conn = get_connection()
    drop = (
        "DROP TABLE IF EXISTS social_content_records;"
        "DROP TABLE IF EXISTS social_content_draft_versions;"
        "DROP TABLE IF EXISTS social_content_decisions;"
    )
    conn.executescript(drop)
    yield
    conn.executescript(drop)


def _ok(envelope) -> dict:
    assert envelope.ok, (envelope.summary, envelope.fault)
    return envelope.data


def test_group_registers_all_commands(clean_store):
    from core.command_registry.registry import registry as get_registry

    reg = get_registry()
    ids = {s.command_id for s in reg.commands()}
    expected = {
        "campaign.create", "campaign.list", "campaign.inspect", "campaign.update",
        "campaign.archive", "draft.save", "draft.inspect", "draft.list",
        "draft.approve", "draft.reject", "metrics.import", "metrics.inspect",
        "hypothesis.inspect", "hypothesis.close",
    }
    assert expected <= ids


def test_campaign_create_list_inspect_roundtrip(clean_store):
    env = execute_command("campaign.create", {
        "project_id": "p1", "objective": "Explain receipts",
        "audience": "builders", "platforms": ("x", "linkedin"),
    })
    doc = _ok(env)
    cid = doc["campaign_id"]
    assert _ok(execute_command("campaign.list", {"project_id": "p1"}))["count"] == 1
    inspected = _ok(execute_command("campaign.inspect", {"campaign_id": cid}))
    assert inspected["objective"] == "Explain receipts"
    assert any(r["kind"] == "social_campaign_created" for r in env.receipts)


def test_model_principal_needs_approval_not_silent_mutation(clean_store):
    env = execute_command(
        "campaign.create",
        {"project_id": "p1", "objective": "x", "audience": "y"},
        context=ExecutionContext(principal="model"),
    )
    assert (not env.ok) and env.fault and env.fault.code == "permission_required"
    assert _ok(execute_command("campaign.list", {"project_id": "p1"}))["count"] == 0
    # a bare authority_token does NOT bypass: this lane binds no out-of-band
    # token grant, so an unauthenticated string cannot pose as one
    still_refused = execute_command(
        "campaign.create",
        {"project_id": "p1", "objective": "x", "audience": "y"},
        context=ExecutionContext(
            principal="model", approval_context={"authority_token": "operator-grant"},
        ),
    )
    assert (not still_refused.ok)
    assert _ok(execute_command("campaign.list", {"project_id": "p1"}))["count"] == 0
    # the operator principal mutates through the canonical gate
    approved = execute_command(
        "campaign.create",
        {"project_id": "p1", "objective": "x", "audience": "y"},
        context=ExecutionContext(principal="operator"),
    )
    assert approved.ok
    assert _ok(execute_command("campaign.list", {"project_id": "p1"}))["count"] == 1
    # content DECISIONS stay operator-only even with a token
    refused = execute_command(
        "draft.approve",
        {"draft_id": "d", "draft_version": 1, "decision": "approved"},
        context=ExecutionContext(
            principal="model", approval_context={"authority_token": "operator-grant"},
        ),
    )
    assert (not refused.ok) and refused.fault.code == "permission_denied"


def test_draft_decide_binds_bytes_and_receipts(clean_store):
    from storage import social_content_store as store

    campaign = store.create_campaign(
        project_id="p1", objective="o", audience="a"
    )
    draft = store.save_draft(
        campaign_id=campaign["campaign_id"], platform="x", body="exact bytes"
    )
    env = execute_command("draft.approve", {
        "draft_id": draft["draft_id"], "draft_version": 1, "decision": "approved",
    })
    data = _ok(env)
    assert data["content_hash"] == draft["content_hash"]
    assert any("draft_approved" in str(r) for r in env.receipts)
    inspected = _ok(execute_command("draft.inspect", {"draft_id": draft["draft_id"]}))
    assert inspected["approved"] is True


def test_draft_reject_is_a_separate_command(clean_store):
    from core.command_registry.registry import registry as get_registry

    reg = get_registry()
    approve = reg.lookup("draft.approve")
    reject = reg.lookup("draft.reject")
    assert approve is not reject
    assert approve.effects == reject.effects == "mutating"


def test_metrics_import_and_inspect(clean_store):
    env = execute_command("metrics.import", {
        "project_id": "p1", "metric_name": "impressions", "value": 42,
        "denominator_known": False,
    })
    _ok(env)
    rows = _ok(execute_command("metrics.inspect", {"project_id": "p1"}))
    assert rows["observations"][0]["denominator_known"] is False


def test_usage_fault_on_bad_input(clean_store):
    env = execute_command("campaign.create", {"project_id": "", "objective": "x", "audience": "y"})
    assert (not env.ok) and env.fault and env.fault.code == "usage"


def test_no_publishing_command_exists_in_the_group(clean_store):
    from core.command_registry.registry import registry as get_registry

    ids = {s.command_id for s in get_registry().commands()}
    for forbidden in (
        "campaign.publish", "draft.publish", "social.publish", "content.publish",
        "social.post", "campaign.send",
    ):
        assert forbidden not in ids


def test_palette_form_text_inputs_are_read_as_names_not_letters(clean_store):
    """The Cmd+K form sends every field as one text. Platforms "x, linkedin" are two platforms
    and a guardrail with a comma stays one guardrail (real palette, 2026-11-12: "linkedin" was
    refused as the platform 'l')."""
    doc = _ok(execute_command("campaign.create", {
        "project_id": "p-form", "objective": "Explain receipts", "audience": "builders",
        "platforms": "X, LinkedIn", "guardrails": "no numbers, no rival names",
    }))
    assert sorted(doc["platforms"]) == ["linkedin", "x"]
    assert doc["guardrails"] == ["no numbers, no rival names"]
    single = _ok(execute_command("campaign.create", {
        "project_id": "p-form", "objective": "Second", "audience": "builders", "platforms": "bluesky",
    }))
    assert single["platforms"] == ["bluesky"]
    refused = execute_command("campaign.create", {
        "project_id": "p-form", "objective": "Third", "audience": "builders", "platforms": "myspace",
    })
    assert not refused.ok and refused.fault and refused.fault.code == "usage"
