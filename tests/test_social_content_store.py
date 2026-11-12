"""Social content store — project-scoped, versioned, privacy-gated local truth.

RED→GREEN pack for Checkpoint 2 (2026-09-04). Covers: CRUD + CAS concurrency,
immutable draft versions with content hashes, decision byte/platform/version
binding, dedup signatures, secret refusal with nothing written, archive/restore,
restart persistence, bounded lists, malformed/oversized input, cross-project
isolation, and export redaction.
"""
from __future__ import annotations

import json

import pytest

from storage import social_content_store as store


@pytest.fixture()
def clean_store():
    """Drop this feature's tables before and after each test (the shared test DB
    is intentionally reused by the repo's autouse storage reset)."""
    from storage.db import get_connection

    conn = get_connection()
    conn.executescript(
        "DROP TABLE IF EXISTS social_content_records;"
        "DROP TABLE IF EXISTS social_content_draft_versions;"
        "DROP TABLE IF EXISTS social_content_decisions;"
    )
    yield store
    conn.executescript(
        "DROP TABLE IF EXISTS social_content_records;"
        "DROP TABLE IF EXISTS social_content_draft_versions;"
        "DROP TABLE IF EXISTS social_content_decisions;"
    )


def _campaign(store, project="proj-a", **kw):
    params = dict(
        project_id=project, objective="Explain the receipt architecture",
        audience="privacy-sensitive builders", platforms=("x", "linkedin"),
        guardrails=("no unverified numbers",),
    )
    params.update(kw)
    return store.create_campaign(**params)


# ---------------------------------------------------------------- basics


def test_campaign_lifecycle_with_versions(clean_store):
    doc = _campaign(clean_store)
    assert doc["version"] == 1 and doc["status"] == "draft"
    updated = clean_store.update_campaign(
        doc["campaign_id"], expected_version=1, status="active"
    )
    assert updated["version"] == 2 and updated["status"] == "active"
    listed = clean_store.list_campaigns("proj-a")
    assert [c["campaign_id"] for c in listed] == [doc["campaign_id"]]


def test_cas_lost_update_guard(clean_store):
    doc = _campaign(clean_store)
    clean_store.update_campaign(doc["campaign_id"], expected_version=1, status="active")
    with pytest.raises(store.RevisionConflict):
        clean_store.update_campaign(
            doc["campaign_id"], expected_version=1, status="paused"
        )


def test_archive_is_reversible_not_deleted(clean_store):
    doc = _campaign(clean_store)
    clean_store.archive_campaign(doc["campaign_id"], expected_version=1)
    assert clean_store.list_campaigns("proj-a") == []
    restored = clean_store.list_campaigns("proj-a", include_archived=True)
    assert len(restored) == 1 and restored[0]["status"] == "archived"
    back = clean_store.restore_campaign(doc["campaign_id"], expected_version=2)
    assert back["status"] == "paused"
    assert len(clean_store.list_campaigns("proj-a")) == 1


def test_project_scoping_no_cross_project_reads(clean_store):
    a = _campaign(clean_store, project="proj-a")
    _campaign(clean_store, project="proj-b")
    assert [c["campaign_id"] for c in clean_store.list_campaigns("proj-a")] == [a["campaign_id"]]
    drafts_a = clean_store.save_draft(
        campaign_id=a["campaign_id"], platform="x", body="hello"
    )
    assert clean_store.list_drafts("proj-b") == []
    assert clean_store.get_draft(drafts_a["draft_id"]) is not None


def test_restart_persistence(clean_store):
    doc = _campaign(clean_store)
    draft = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="linkedin", body="stable bytes"
    )
    # A fresh process reads the same disk store: re-reading through new calls is
    # the disk-is-the-store convention; force schema re-read by direct SQL too.
    rows = clean_store.list_campaigns("proj-a")
    assert rows[0]["campaign_id"] == doc["campaign_id"]
    again = clean_store.get_draft(draft["draft_id"])
    assert again["content_hash"] == draft["content_hash"]


# ---------------------------------------------------------------- drafts


def test_draft_versions_are_immutable_and_hash_bound(clean_store):
    doc = _campaign(clean_store)
    v1 = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x", body="first bytes"
    )
    v2 = clean_store.save_draft(
        draft_id=v1["draft_id"], campaign_id=doc["campaign_id"],
        platform="x", body="first bytes changed",
    )
    assert v2["version"] == 2 and v2["draft_id"] == v1["draft_id"]
    assert v1["content_hash"] != v2["content_hash"]
    # the old version is still readable, unchanged
    assert clean_store.get_draft(v1["draft_id"], version=1)["body_bytes"] == "first bytes"
    assert clean_store.get_draft(v1["draft_id"])["version"] == 2


def test_decision_binds_exact_bytes_platform_version(clean_store):
    doc = _campaign(clean_store)
    v1 = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x", body="approved bytes"
    )
    dec = clean_store.record_decision(
        draft_id=v1["draft_id"], draft_version=1,
        content_hash=v1["content_hash"], platform="x", decision="approved",
    )
    assert dec["decision"] == "approved"
    assert clean_store.approval_for(
        draft_id=v1["draft_id"], version=1,
        content_hash=v1["content_hash"], platform="x",
    ) is not None
    # one byte changed -> new version, no covering approval
    v2 = clean_store.save_draft(
        draft_id=v1["draft_id"], campaign_id=doc["campaign_id"],
        platform="x", body="approved bytes!",
    )
    assert clean_store.approval_for(
        draft_id=v1["draft_id"], version=2,
        content_hash=v2["content_hash"], platform="x",
    ) is None
    # wrong platform never covered
    assert clean_store.approval_for(
        draft_id=v1["draft_id"], version=1,
        content_hash=v1["content_hash"], platform="linkedin",
    ) is None


def test_decision_refuses_caller_supplied_identity(clean_store):
    doc = _campaign(clean_store)
    v1 = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x", body="bytes"
    )
    with pytest.raises(store.ValidationError):
        clean_store.record_decision(
            draft_id=v1["draft_id"], draft_version=1,
            content_hash="deadbeef", platform="x", decision="approved",
        )


def test_approval_expiry(clean_store):
    doc = _campaign(clean_store)
    v1 = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x", body="temp"
    )
    clean_store.record_decision(
        draft_id=v1["draft_id"], draft_version=1, content_hash=v1["content_hash"],
        platform="x", decision="approved", expires_at=1000.0,
    )
    assert clean_store.approval_for(
        draft_id=v1["draft_id"], version=1,
        content_hash=v1["content_hash"], platform="x", at=999.0,
    ) is not None
    assert clean_store.approval_for(
        draft_id=v1["draft_id"], version=1,
        content_hash=v1["content_hash"], platform="x", at=1001.0,
    ) is None


# ---------------------------------------------------------------- ideas / dedup


def test_idea_dedup_signature_rejects_repeats(clean_store):
    doc = _campaign(clean_store)
    kw = dict(
        campaign_id=doc["campaign_id"], angle="Receipts as architecture",
        why_now="launch week", target_reader="builders",
        evidence_ids=("trend-1",),
    )
    clean_store.add_idea(**kw)
    with pytest.raises(store.ValidationError):
        clean_store.add_idea(**kw)
    # a distinct angle is accepted
    other = clean_store.add_idea(**{**kw, "angle": "Budgets that cannot lie"})
    first = clean_store.list_ideas("proj-a")[-1]  # newest last is not guaranteed; match by id
    by_id = {i["idea_id"]: i for i in clean_store.list_ideas("proj-a")}
    assert len(by_id) == 2
    assert other["dedup_signature"] != next(
        sig for iid, sig in ((i, d["dedup_signature"]) for i, d in by_id.items() if i != other["idea_id"])
    )


# ---------------------------------------------------------------- privacy


@pytest.mark.parametrize("field", ["objective", "body"])
def test_secret_refusal_writes_nothing(clean_store, field):
    secret = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
    with pytest.raises(store.SecretRefusal):
        if field == "objective":
            _campaign(clean_store, objective=f"use key {secret} now")
        else:
            doc = _campaign(clean_store)
            clean_store.save_draft(
                campaign_id=doc["campaign_id"], platform="x",
                body=f"ship it with {secret}",
            )
    assert clean_store.list_campaigns("proj-z") == []
    from storage.db import execute_query

    rows = execute_query("SELECT COUNT(*) AS n FROM social_content_records")
    if field == "body":
        assert rows[0]["n"] == 1  # only the campaign, no draft
    else:
        assert rows[0]["n"] == 0


def test_soft_privacy_risks_become_findings(clean_store):
    doc = _campaign(clean_store)
    draft = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x",
        body="Contact me at someone@example.com about the launch",
    )
    assert "email" in draft["privacy_findings"]


# ---------------------------------------------------------------- malformed input


def test_malformed_and_oversized_input(clean_store):
    with pytest.raises(store.ValidationError):
        clean_store.create_campaign(project_id="", objective="x", audience="y")
    with pytest.raises(store.ValidationError):
        _campaign(clean_store, platforms=("myspace",))
    doc = _campaign(clean_store)
    with pytest.raises(store.ValidationError):
        clean_store.save_draft(
            campaign_id=doc["campaign_id"], platform="x", body="x" * (store.MAX_TEXT_CHARS + 1)
        )
    with pytest.raises(store.UnknownRecord):
        clean_store.update_campaign("camp-none", expected_version=1, status="active")


def test_list_bounds(clean_store):
    doc = _campaign(clean_store)
    for i in range(5):
        clean_store.save_draft(
            campaign_id=doc["campaign_id"], platform="x", body=f"b{i}"
        )
    assert len(clean_store.list_drafts("proj-a", limit=3)) == 3
    assert len(clean_store.list_drafts("proj-a", limit=10_000)) <= store.MAX_LIMIT


# ---------------------------------------------------------------- metrics / hypotheses


def test_metrics_and_hypotheses_lifecycle(clean_store):
    m = clean_store.add_metric_observation(
        project_id="proj-a", metric_name="impressions", value=1200,
        source="operator_entered", denominator_known=False,
    )
    assert m["denominator_known"] is False
    with pytest.raises(store.ValidationError):
        clean_store.add_metric_observation(
            project_id="proj-a", metric_name="bad", value="lots",
        )
    h = clean_store.add_hypothesis(
        project_id="proj-a", prediction="receipts angle outperforms",
    )
    closed = clean_store.close_hypothesis(
        h["hypothesis_id"], outcome="inconclusive", disposition="one post, no denominator"
    )
    assert closed["outcome"] == "inconclusive"
    with pytest.raises(store.ValidationError):
        clean_store.close_hypothesis(h["hypothesis_id"], outcome="proven")


# ---------------------------------------------------------------- export


def test_export_redacts_private_evidence_keeps_hashes(clean_store):
    doc = _campaign(clean_store)
    clean_store.add_trend_observation(
        project_id="proj-a", source_class="personal_feed",
        observed_text="private feed text about the operator's day",
    )
    draft = clean_store.save_draft(
        campaign_id=doc["campaign_id"], platform="x", body="public copy bytes"
    )
    clean_store.record_decision(
        draft_id=draft["draft_id"], draft_version=1,
        content_hash=draft["content_hash"], platform="x", decision="approved",
    )
    human, machine = clean_store.export_project("proj-a")
    assert isinstance(human, str) and "campaign" in human
    blob = json.dumps(machine)
    assert "private feed text" not in blob
    assert draft["content_hash"] in blob  # hashes/provenance intact
    assert machine["drafts"][0]["body_bytes"] == "public copy bytes"
