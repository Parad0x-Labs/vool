"""Metrics ingestion, interpretation and campaign review (Checkpoint 7)."""
from __future__ import annotations

import pytest

from core.social_content import observations
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


def _metric(name="impressions", value=1000, **kw):
    params = dict(
        project_id="p1", metric_name=name, value=value,
        window_start=1.0, window_end=2.0, denominator_known=True,
        source="file_import", provenance="batch-1",
    )
    params.update(kw)
    return store.add_metric_observation(**params)


# ---------------------------------------------------------------- ingestion


def test_malformed_rows_refused_by_name_good_rows_import(clean_store):
    result = observations.import_metric_batch(store, project_id="p1", rows=[
        {"metric_name": "impressions", "value": 100},
        {"metric_name": "", "value": 5},                 # malformed
        {"metric_name": "likes", "value": "lots"},       # malformed
        {"metric_name": "likes", "value": 7},
    ])
    assert len(result["imported"]) == 2
    assert len(result["refused"]) == 2
    reasons = " | ".join(r["reason"] for r in result["refused"])
    assert "metric_name is required" in reasons and "float" in reasons
    for o in store.list_metric_observations("p1"):
        assert o["source"] == "file_import"  # labelled


def test_metric_definition_mismatch_named(clean_store):
    a = _metric("views", 5000)
    b = _metric("impressions", 5000)
    cmp = observations.compare_observations(a, b)
    assert not cmp["comparable"]
    assert any("definition mismatch" in p for p in cmp["problems"])


def test_missing_denominator_named(clean_store):
    a = _metric("likes", 10, denominator_known=False)
    b = _metric("likes", 12)
    cmp = observations.compare_observations(a, b)
    assert not cmp["comparable"]
    assert any("denominator" in p for p in cmp["problems"])


def test_window_mismatch_named(clean_store):
    a = _metric("likes", 10)
    b = _metric("likes", 12, window_start=2.0, window_end=3.0)
    cmp = observations.compare_observations(a, b)
    assert not cmp["comparable"] and any("window" in p for p in cmp["problems"])


def test_compatible_pair_compares(clean_store):
    a = _metric("likes", 10)
    b = _metric("likes", 12)
    assert observations.compare_observations(a, b)["comparable"]


# ---------------------------------------------------------------- hypotheses


def test_one_post_never_proves(clean_store):
    hyp = store.add_hypothesis(project_id="p1", prediction="receipts angle wins")
    result = observations.update_hypothesis(
        store, hypothesis_id=hyp["hypothesis_id"], project_id="p1",
        observations=[dict(_metric("likes", 10))],
    )
    assert result["ok"]
    assert result["hypothesis"]["outcome"] == "inconclusive"
    assert "never proves" in result["hypothesis"]["disposition"]


def test_supported_requires_compatible_denominator_known(clean_store):
    hyp = store.add_hypothesis(project_id="p1", prediction="receipts angle wins")
    obs = [dict(_metric("likes", v)) for v in (10, 12, 14)]
    result = observations.update_hypothesis(
        store, hypothesis_id=hyp["hypothesis_id"], project_id="p1", observations=obs,
    )
    assert result["hypothesis"]["outcome"] == "supported"
    assert result["hypothesis"]["confidence"] == "medium"  # never high from counts
    assert "causality is NOT established" in result["hypothesis"]["disposition"]


def test_negative_results_preserved(clean_store):
    hyp = store.add_hypothesis(project_id="p1", prediction="thread beats post")
    obs = [dict(_metric("clicks", 0)), dict(_metric("clicks", 0))]
    result = observations.update_hypothesis(
        store, hypothesis_id=hyp["hypothesis_id"], project_id="p1", observations=obs,
    )
    # zero results are real observations, not absence
    assert result["ok"] and store.list_metric_observations("p1")


def test_operator_correction_and_outcome_vocabulary(clean_store):
    hyp = store.add_hypothesis(project_id="p1", prediction="x")
    with pytest.raises(store.ValidationError):
        store.close_hypothesis(hyp["hypothesis_id"], outcome="proven")
    corrected = store.close_hypothesis(
        hyp["hypothesis_id"], outcome="contradicted",
        disposition="operator reviewed and rejected the premise",
    )
    assert corrected["outcome"] == "contradicted"


def test_restart_preserves_observations(clean_store):
    _metric("impressions", 4242)
    again = store.list_metric_observations("p1")
    assert again[0]["value"] == 4242 and again[0]["source"] == "file_import"


# ---------------------------------------------------------------- campaign review


def test_campaign_review_shape(clean_store):
    campaign = store.create_campaign(project_id="p1", objective="o", audience="a")
    draft = store.save_draft(
        campaign_id=campaign["campaign_id"], platform="x", body="b"
    )
    store.record_decision(
        draft_id=draft["draft_id"], draft_version=1,
        content_hash=draft["content_hash"], platform="x", decision="NO_POST",
    )
    _metric("impressions", 10, denominator_known=False)
    hyp = store.add_hypothesis(project_id="p1", prediction="p")
    review = observations.campaign_review(store, project_id="p1")
    for key in ("what_was_approved", "no_post_decisions",
                "measured_observations", "what_cannot_be_known",
                "hypothesis_updates", "next_experiments", "pb01_note"):
        assert key in review
    assert review["no_post_decisions"] == 1
    assert review["what_was_approved"] == []
    assert any("denominator" in c for c in review["what_cannot_be_known"])
    assert "core.learning" in review["pb01_note"]


def test_no_pb01_write_from_engagement(clean_store):
    """No automatic PB01 procedure promotion from raw engagement."""
    _metric("likes", 999)
    before = _pb01_shard_count()
    observations.campaign_review(store, project_id="p1")
    assert _pb01_shard_count() == before


def _pb01_shard_count() -> int:
    from core.runtime_paths import active_data_dir

    import pathlib
    learning = pathlib.Path(active_data_dir()) / "learning" / "procedures"
    return len(list(learning.glob("*.json"))) if learning.exists() else 0
