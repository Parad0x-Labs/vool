"""Review, privacy, approval and final-byte identity (Checkpoint 5)."""
from __future__ import annotations

import pytest

from core.social_content import review
from storage import social_content_store as store


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
    yield store
    conn.executescript(drop)


def _draft(body="approved bytes", platform="x", claims=(), **kw):
    campaign = store.create_campaign(
        project_id="p1", objective="explain receipts", audience="builders",
        platforms=(platform,),
    )
    return campaign, store.save_draft(
        campaign_id=campaign["campaign_id"], platform=platform, body=body,
        claim_ledger=claims, **kw
    )


# ---------------------------------------------------------------- review surface


def test_review_surfaces_everything(clean_store):
    campaign, draft = _draft(
        body="We measured 23ms. Details https://example.com/bench",
        claims=[
            {"text": "23ms", "status": "supported", "evidence_ids": ["e1"]},
            {"text": "I think it's the fastest", "status": "opinion"},
            {"text": "rumor number 500000", "status": "unknown"},
        ],
        media_brief="screenshot of the receipt chain",
    )
    report = review.review_draft(draft, campaign=campaign)
    for key in ("objective", "audience", "platform", "version", "content_hash",
                "claims", "privacy_findings", "findings", "links", "disposition",
                "exact_bytes"):
        assert key in report
    assert report["objective"] == "explain receipts"
    statuses = {c["status"] for c in report["claims"]}
    assert statuses == {"supported", "opinion", "unknown"}
    assert "claim_not_supported" in {f["code"] for f in report["findings"]}
    assert report["disposition"] == "needs_changes"
    assert report["links"] == ["https://example.com/bench"]


def test_unknown_claim_blocks_clean_copy(clean_store):
    _, draft = _draft(claims=[{"text": "x", "status": "unknown"}])
    assert review.review_draft(draft)["disposition"] == "needs_changes"


def test_clean_draft_gets_exact_bytes(clean_store):
    campaign, draft = _draft()
    report = review.review_draft(draft, campaign=campaign)
    assert report["disposition"] == "clean_copy"
    assert report["exact_bytes"] == "approved bytes"


def test_no_post_from_research(clean_store):
    _, draft = _draft()
    report = review.review_draft(
        draft, research_outcome={"decision": "NO_POST", "reason": "weak evidence"}
    )
    assert report["disposition"] == "NO_POST"
    assert report["exact_bytes"] is None


def test_sludge_findings_surface(clean_store):
    _, draft = _draft(body="In today's fast-paced world this is a game changer")
    report = review.review_draft(draft)
    assert "ai_sludge" in {f["code"] for f in report["findings"]}


# ---------------------------------------------------------------- approval


def test_approval_binds_bytes_and_replay_refused(clean_store):
    _, draft = _draft()
    first = review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    assert first["ok"] and first["receipt_state"] == "draft_approved"
    replay = review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    assert not replay["ok"] and replay["code"] == "approval_replay_refused"


def test_changed_byte_invalidates_approval(clean_store):
    campaign, draft = _draft()
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    v2 = store.save_draft(
        draft_id=draft["draft_id"], campaign_id=campaign["campaign_id"],
        platform="x", body="approved bytes ",
    )
    assert v2["version"] == 2
    status = review.approval_status(store, draft_id=draft["draft_id"])
    assert status["approved"] is False
    assert "NOT approved" in status["summary"]


def test_wrong_platform_never_covered(clean_store):
    campaign, draft = _draft(platform="x")
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    linked = store.save_draft(
        campaign_id=campaign["campaign_id"], platform="linkedin",
        body="approved bytes",
    )
    assert review.approval_status(store, draft_id=linked["draft_id"])["approved"] is False


def test_expiry_and_cancellation(clean_store):
    _, draft = _draft()
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1,
                         expires_at=1000.0)
    identity = {
        "draft_id": draft["draft_id"], "version": 1,
        "content_hash": draft["content_hash"], "platform": "x",
    }
    assert store.approval_for(**identity, at=999.0) is not None
    assert store.approval_for(**identity, at=1001.0) is None

    _, draft2 = _draft(body="other bytes")
    review.approve_exact(store, draft_id=draft2["draft_id"], draft_version=1)
    cancelled = review.cancel_approval(
        store, draft_id=draft2["draft_id"], draft_version=1, reason="operator pulled it",
    )
    assert cancelled["ok"] and cancelled["receipt_state"] == "draft_refused"
    assert review.approval_status(store, draft_id=draft2["draft_id"])["approved"] is False


def test_receipt_language_is_exact(clean_store):
    _, draft = _draft()
    status = review.approval_status(store, draft_id=draft["draft_id"])
    assert status["receipt_state"] == "draft_saved"
    assert "approval is not publication" in status["summary"]
    review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    status = review.approval_status(store, draft_id=draft["draft_id"])
    assert status["receipt_state"] == "draft_approved"
    assert "no external dispatch has occurred" in status["summary"]
    assert "approval_is_not_publication" not in status  # local truth only


def test_privacy_withhold_blocks_approval(clean_store, monkeypatch):
    _, draft = _draft()
    monkeypatch.setattr(review, "_publish_withhold", lambda body: True)
    result = review.approve_exact(store, draft_id=draft["draft_id"], draft_version=1)
    assert not result["ok"] and result["code"] == "privacy_withhold"
    # nothing was recorded
    assert store.list_decisions("p1") == []


def test_secret_never_reaches_the_draft_at_all(clean_store):
    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    with pytest.raises(store.SecretRefusal):
        store.save_draft(
            campaign_id=campaign["campaign_id"], platform="x",
            body="ship with sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
    assert store.list_drafts("p1") == []
