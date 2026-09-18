"""Model Radar — the honest free / >=50% price-drop discovery authority.

The radar is a QUIET surface: it qualifies an observation into a finding only when

1. the model became genuinely FREE under clearly stated limits, or
2. the SAME provider cut the recorded public price of the SAME canonical model by
   at least 50% on every published component (input, output, cached input tracked
   separately -- never averaged into one misleading number).

Everything else -- 49% cuts, trials, unknown offers, stale evidence, conflicting
feeds, identity renames, capability mismatches, increases -- must stay silent.

These tests drive the qualification engine and the persistence layer through the
real service seam (`observe_feed`) with mocked feeds, per the task contract. The
store is isolated per test via a temp sqlite connection.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace as _dc_replace

import pytest

from adapters.model_radar_feeds import FeedMalformedError, parse_normalized_feed
from core.model_radar import (
    EVIDENCE_MAX_AGE_SECONDS,
    OFFER_FREE_QUOTA,
    OFFER_PERMANENT,
    OFFER_PROMOTION,
    OFFER_SUBSIDY,
    OFFER_TRIAL,
    ModelObservation,
    PriceComponents,
)
from core.model_radar_service import (
    dismiss,
    observe_feed,
    save_preferences,
    unread_findings,
)

# --------------------------------------------------------------------------------------
# Isolated store: every service call in these tests reads/writes through this connection.
# --------------------------------------------------------------------------------------


@pytest.fixture()
def radar_store(tmp_path, monkeypatch):
    # One DB file, a fresh connection per call: the same shape storage.db hands
    # out for non-default paths (test isolation), and every call may close().
    db_file = tmp_path / "radar.db"

    def _fresh_connection():
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("storage.model_radar.get_connection", _fresh_connection)
    yield db_file


_NOW = "2026-09-01T12:00:00+00:00"


def obs(
    provider: str = "openrouter",
    model: str = "vendor/alpha",
    pin: float | None = 1.0,
    pout: float | None = 4.0,
    pcached: float | None = None,
    offer: str = OFFER_PERMANENT,
    limits: str = "",
    expiry: str = "",
    fetched: str = _NOW,
    tools: bool = True,
    images: bool = False,
    source: str = "test-feed",
) -> ModelObservation:
    return ModelObservation(
        provider_id=provider,
        model_id=model,
        display_name=model.split("/")[-1],
        prices=PriceComponents(
            input_usd_per_m=pin, output_usd_per_m=pout, cached_input_usd_per_m=pcached
        ),
        offer_kind=offer,
        limits_stated=limits,
        limits_evidence_url="https://provider.example/limits" if limits else "",
        expires_at=expiry,
        context_length=131072,
        supports_tools=tools,
        supports_images=images,
        privacy_terms="",
        evidence_url="https://provider.example/models",
        evidence_fetched_at=fetched,
        source_feed=source,
    )


def prefs(**changes):
    from core.model_radar import RadarPreferences

    return _dc_replace(RadarPreferences(), **changes)


def _issued(feed_result) -> list[dict]:
    return list(getattr(feed_result, "findings", ()) or ())


# --------------------------------------------------------------------------------------
# The twelve contract scenarios, RED -> GREEN.
# --------------------------------------------------------------------------------------


def test_new_free_qualifies_with_stated_limits(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (
            obs(
                pin=0.0,
                pout=0.0,
                offer=OFFER_FREE_QUOTA,
                limits="20 requests/min, 50 requests/day on the free tier",
            ),
        ),
        now=_NOW,
    )
    findings = _issued(result)
    assert len(findings) == 1, "paid -> genuinely free under stated limits must notify exactly once"
    finding = findings[0]
    assert finding.kind == "new_free"
    assert finding.provider_id == "openrouter" and finding.model_id == "vendor/alpha"
    assert finding.after["input_usd_per_m"] == 0.0 and finding.after["output_usd_per_m"] == 0.0
    assert finding.limits_stated.startswith("20 requests/min")
    assert "free" in finding.why.lower() and finding.evidence_fetched_at == _NOW


def test_subsidized_free_with_verified_limits_qualifies(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=6.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (
            obs(
                model="vendor/beta:free",
                pin=0.0,
                pout=0.0,
                offer=OFFER_SUBSIDY,
                limits="Free tier: 20 requests/minute, 50 requests/day",
            ),
        ),
        now=_NOW,
    )
    # No baseline for the :free id itself; give it one paid past first.
    assert _issued(result) == [], "an id with no recorded paid baseline did not 'become' anything"
    observe_feed("openrouter", (obs(model="vendor/beta:free", pin=2.0, pout=6.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (
            obs(
                model="vendor/beta:free",
                pin=0.0,
                pout=0.0,
                offer=OFFER_SUBSIDY,
                limits="Free tier: 20 requests/minute, 50 requests/day",
            ),
        ),
        now=_NOW,
    )
    findings = _issued(result)
    assert len(findings) == 1 and findings[0].offer_kind == OFFER_SUBSIDY


def test_trial_free_never_qualifies(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (obs(pin=0.0, pout=0.0, offer=OFFER_TRIAL, limits="14-day trial, then billed"),),
        now=_NOW,
    )
    assert _issued(result) == [], "a trial is time-boxed credit, not a genuinely free model"


def test_free_without_stated_limits_stays_silent(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    result = observe_feed(
        "openrouter", (obs(pin=0.0, pout=0.0, offer=OFFER_PERMANENT, limits=""),), now=_NOW
    )
    assert _issued(result) == [], "'genuinely free UNDER CLEARLY STATED LIMITS' -- no limits stated, no notification"


def test_49_percent_cut_stays_silent(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=0.52, pout=2.02),), now=_NOW)  # 48%/49.5%
    assert _issued(result) == [], "below the 50% bar on both components: not radar material"


def test_exactly_50_percent_qualifies(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)  # exactly 50%/50%
    findings = _issued(result)
    assert len(findings) == 1 and findings[0].kind == "price_drop"
    red = findings[0].reductions
    assert red["input_usd_per_m"] == pytest.approx(0.5) and red["output_usd_per_m"] == pytest.approx(0.5)
    assert findings[0].before["input_usd_per_m"] == 2.0 and findings[0].after["output_usd_per_m"] == 4.0


def test_temporary_promotion_qualifies_with_expiry_surfaced(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (
            obs(
                pin=0.5,
                pout=2.0,
                offer=OFFER_PROMOTION,
                expiry="2026-10-01T00:00:00+00:00",
            ),
        ),
        now=_NOW,
    )
    findings = _issued(result)
    assert len(findings) == 1, "a >=50% promotion is a real cut the user benefits from..."
    assert findings[0].offer_kind == OFFER_PROMOTION
    assert findings[0].expires_at == "2026-10-01T00:00:00+00:00", "...but the card must carry the expiry"


def test_promotion_without_stated_expiry_fails_closed(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter", (obs(pin=0.5, pout=2.0, offer=OFFER_PROMOTION, expiry=""),), now=_NOW
    )
    assert _issued(result) == [], "a promotion with no stated end date is not a clearly stated offer"


def test_expired_promotion_never_qualifies(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (obs(pin=0.5, pout=2.0, offer=OFFER_PROMOTION, expiry="2026-08-01T00:00:00+00:00"),),
        now=_NOW,
    )
    assert _issued(result) == [], "the offer already ended: nothing to tell the user"


def test_price_returning_to_normal_after_promotion_is_silent(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=0.5, pout=2.0, offer=OFFER_PROMOTION,
                                    expiry="2026-08-01T00:00:00+00:00"),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    assert _issued(result) == [], "the promotion ending (price reverting UP) is never radar material"


def test_price_increase_is_silent(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.4, pout=6.0),), now=_NOW)
    assert _issued(result) == [], "the radar announces savings, never surcharges"


def test_stale_evidence_produces_no_notification(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    stale = "2026-08-01T00:00:00+00:00"  # a month old
    result = observe_feed("openrouter", (obs(pin=0.0, pout=0.0, offer=OFFER_FREE_QUOTA,
                                             limits="50/day", fetched=stale),), now=_NOW)
    assert _issued(result) == [], "unknown or stale evidence produces no notification"
    # And the stale row must not poison the recorded baseline either.
    result2 = observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    assert _issued(result2) == [], "baseline survived the stale feed untouched (no fake rise/drop cycle)"


def test_conflicting_feeds_for_same_provider_and_model_fail_closed(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (
            obs(pin=0.5, pout=2.0, source="feed-a"),
            obs(pin=4.0, pout=9.0, source="feed-b"),
        ),
        now=_NOW,
    )
    assert _issued(result) == [], "two feeds disagreeing about the same provider+model price = unknown truth"
    assert result.conflicts, "the disagreement itself must be observable for diagnosis"


def test_malformed_feed_fails_closed_and_keeps_baseline(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    poisoned = {"results": [{"id": "vendor/alpha", "input_usd_per_m": 0.0}]}
    with pytest.raises(FeedMalformedError):
        parse_normalized_feed("openrouter", poisoned, fetched_at=_NOW, source_feed="poison")
    # A structurally broken payload must never partially land in the store.
    result = observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    assert _issued(result) == [], "baseline untouched after the malformed feed was rejected whole"


def test_duplicate_observation_never_double_notifies(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    first = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert len(_issued(first)) == 1
    second = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert _issued(second) == [], "the same qualified state observed twice is one finding, not two"
    assert len(unread_findings()) == 1


def test_requalified_state_after_a_revert_is_not_a_duplicate(radar_store) -> None:
    # Issue, revert, re-drop: the SAME qualified state returns and must not notify twice.
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    first = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert len(_issued(first)) == 1
    fingerprint = _issued(first)[0].fingerprint
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)  # provider reverts
    again = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)  # same drop returns
    assert _issued(again) == [], "a fingerprint already issued stays issued exactly once"
    assert sum(1 for f in unread_findings() if f.fingerprint == fingerprint) == 1


def test_dismissal_is_permanent_no_nag(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    first = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert len(_issued(first)) == 1
    fingerprint = _issued(first)[0].fingerprint
    dismissed = dismiss(fingerprint, now=_NOW)
    assert dismissed.ok
    assert unread_findings() == []
    # Roll the recorded price back up (a normal revert), then re-observe the SAME
    # qualified state: the dismissal must hold even when the discount re-qualifies.
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    requalified = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert all(
        f.fingerprint != fingerprint for f in _issued(requalified)
    ), "a dismissed finding may never be re-issued, even after the state re-qualifies"
    assert all(f.fingerprint != fingerprint for f in unread_findings())
    # A genuinely deeper cut is NEW news with a different fingerprint: allowed.
    deeper = observe_feed("openrouter", (obs(pin=0.5, pout=2.0),), now=_NOW)
    assert any(f.fingerprint != fingerprint for f in _issued(deeper))


def test_capability_incompatibility_suppresses_the_finding(radar_store) -> None:
    save_preferences(prefs(required_capabilities=("tools",)))
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0, tools=True),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.0, pout=4.0, tools=False),), now=_NOW)
    assert _issued(result) == [], "a model the user cannot use (no tool support) is not worth a notification"
    suppressed = [s for s in result.suppressed if s.reason_code == "capability_mismatch"]
    assert suppressed, "the suppression reason is recorded, not silent"


# --------------------------------------------------------------------------------------
# Pricing-truth invariants.
# --------------------------------------------------------------------------------------


def test_cached_token_price_is_tracked_separately_and_a_rise_vetoes(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0, pcached=0.5),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (obs(pin=1.0, pout=4.0, pcached=0.6),),  # input/output -50%, cached +20%
        now=_NOW,
    )
    assert _issued(result) == [], "input/output -50% with cached tokens UP is a misleading discount"


def test_cached_token_drop_counts_and_is_reported_per_component(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0, pcached=1.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.0, pout=4.0, pcached=0.25),), now=_NOW)
    findings = _issued(result)
    assert len(findings) == 1
    red = findings[0].reductions
    assert red["cached_input_usd_per_m"] == pytest.approx(0.75), "each component's cut reported on its own"


def test_unpublished_after_price_fails_closed(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    # Limits stated and offer grounded, so the ONLY thing standing between this
    # observation and a false "became free" is the unknown-pricing gate.
    result = observe_feed(
        "openrouter",
        (obs(pin=None, pout=None, offer=OFFER_FREE_QUOTA, limits="50 requests/day"),),
        now=_NOW,
    )
    assert _issued(result) == [], "a price that stopped being published is unknown, not a discount"


def test_new_free_requires_a_recorded_paid_past(radar_store) -> None:
    result = observe_feed(
        "openrouter", (obs(pin=0.0, pout=0.0, offer=OFFER_FREE_QUOTA, limits="50/day"),), now=_NOW
    )
    assert _issued(result) == [], "first-ever sight of a free model: nothing 'became' free"


# --------------------------------------------------------------------------------------
# Identity laws -- a rename or provider hop can never ride an old baseline.
# --------------------------------------------------------------------------------------


def test_model_rename_cannot_fake_a_discount(radar_store) -> None:
    observe_feed("openrouter", (obs(model="vendor/alpha", pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter", (obs(model="vendor/alpha-v2", pin=1.0, pout=4.0),), now=_NOW
    )
    assert _issued(result) == [], "a different model id has no baseline: 'became cheaper' is unfounded"


def test_free_suffix_is_a_distinct_identity(radar_store) -> None:
    observe_feed("openrouter", (obs(model="vendor/alpha", pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed(
        "openrouter",
        (obs(model="vendor/alpha:free", pin=0.0, pout=0.0, offer=OFFER_SUBSIDY, limits="50/day"),),
        now=_NOW,
    )
    assert _issued(result) == [], ":free is a distinct route id -- it cannot inherit the paid id's history"


def test_provider_change_cannot_fake_a_discount(radar_store) -> None:
    observe_feed("openrouter", (obs(provider="openrouter", pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("mirror", (obs(provider="mirror", pin=1.0, pout=4.0),), now=_NOW)
    assert _issued(result) == [], "the 50% rule is per provider: a cheaper mirror is a different offer"


def test_identity_matching_is_case_and_whitespace_insensitive(radar_store) -> None:
    observe_feed("openrouter", (obs(model="Vendor/Alpha", pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(model=" vendor/ALPHA ", pin=1.0, pout=4.0),), now=_NOW)
    findings = _issued(result)
    assert len(findings) == 1, "canonical identity folding must match the catalog's own classifier law"


# --------------------------------------------------------------------------------------
# Preferences and cadence.
# --------------------------------------------------------------------------------------


def test_provider_preference_filters_findings(radar_store) -> None:
    save_preferences(prefs(providers=("openrouter",)))
    observe_feed("openrouter", (obs(provider="openrouter", pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("mirror", (obs(provider="mirror", pin=1.0, pout=4.0),), now=_NOW)
    assert _issued(result) == [], "the operator asked to watch openrouter only"


def test_lane_preference_local_suppresses_cloud_findings(radar_store) -> None:
    save_preferences(prefs(lane="local"))
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert _issued(result) == [], "lane=local means cloud price news is noise"


def test_frequency_preference_spaces_repeated_findings(radar_store) -> None:
    save_preferences(prefs(min_interval_hours=24))
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    first = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert len(_issued(first)) == 1
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)  # settle baseline
    deeper = observe_feed("openrouter", (obs(pin=0.4, pout=1.6),), now=_NOW)
    assert _issued(deeper) == [], "a deeper cut within 24h of the last issued finding waits its turn"
    later_now = "2026-09-02T12:00:00+00:00"
    later = observe_feed("openrouter", (obs(pin=0.4, pout=1.6, fetched=later_now),), now=later_now)
    observe_feed("openrouter", (obs(pin=0.4, pout=1.6, fetched=later_now),), now=later_now)
    assert len(_issued(later)) == 1, "after the interval passes, the new state qualifies again"


def test_disabled_preference_mutes_everything(radar_store) -> None:
    save_preferences(prefs(enabled=False))
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    result = observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert _issued(result) == []


# --------------------------------------------------------------------------------------
# Last-known-good behaviour across outages.
# --------------------------------------------------------------------------------------


def test_findings_survive_and_stay_readable_when_refresh_fails(radar_store, monkeypatch) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0),), now=_NOW)
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    before = unread_findings()
    assert len(before) == 1
    # Provider outage: the observation call itself blows up.
    def _explode(*args, **kwargs):
        raise OSError("provider unreachable")

    monkeypatch.setattr("storage.model_radar.record_observations", _explode)
    with pytest.raises(OSError):
        observe_feed("openrouter", (obs(pin=1.0, pout=4.0),), now=_NOW)
    assert unread_findings() == before, "the issued finding and its evidence survive the outage"


# --------------------------------------------------------------------------------------
# The normalized feed adapter contract (thin adapter -> typed authority).
# --------------------------------------------------------------------------------------


def test_parse_normalized_feed_maps_fields_and_keeps_unpublished_as_none() -> None:
    payload = {
        "models": [
            {
                "id": "vendor/alpha",
                "name": "Alpha",
                "input_usd_per_m": 1.5,
                "output_usd_per_m": 6.0,
                "cached_input_usd_per_m": None,
                "offer_kind": "permanent",
                "context_length": 200000,
                "supports_tools": True,
                "supports_images": False,
                "privacy_terms": "Prompts are retained 30 days",
            }
        ]
    }
    (observation,) = parse_normalized_feed("prov", payload, fetched_at=_NOW, source_feed="norm-json")
    assert observation.provider_id == "prov"
    assert observation.prices.input_usd_per_m == 1.5
    assert observation.prices.cached_input_usd_per_m is None, "unpublished stays None, never 0"
    assert observation.supports_tools is True
    assert observation.privacy_terms == "Prompts are retained 30 days"
    assert observation.evidence_fetched_at == _NOW


def test_parse_normalized_feed_rejects_unknown_offer_kind() -> None:
    payload = {"models": [{"id": "m", "input_usd_per_m": 1.0, "output_usd_per_m": 1.0, "offer_kind": "fire-sale"}]}
    with pytest.raises(FeedMalformedError):
        parse_normalized_feed("prov", payload, fetched_at=_NOW, source_feed="norm-json")


def test_parse_normalized_feed_rejects_non_dict_payload() -> None:
    with pytest.raises(FeedMalformedError):
        parse_normalized_feed("prov", [1, 2, 3], fetched_at=_NOW, source_feed="norm-json")


def test_parse_normalized_feed_rejects_negative_prices() -> None:
    payload = {"models": [{"id": "m", "input_usd_per_m": -1.0, "output_usd_per_m": 1.0}]}
    with pytest.raises(FeedMalformedError):
        parse_normalized_feed("prov", payload, fetched_at=_NOW, source_feed="norm-json")


def test_openrouter_adapter_maps_free_variant_with_verified_limits() -> None:
    from types import SimpleNamespace

    from adapters.model_radar_feeds import openrouter_observations

    row = SimpleNamespace(
        model_id="deepseek/deepseek-v3:free",
        name="DeepSeek V3 (free)",
        context_length=131072,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=None,
        supported_parameters=("tools", "stream"),
        input_modalities=("text", "image"),
        output_modalities=("text",),
        fetched_at=_NOW,
        max_output_tokens=0,
    )
    (observation,) = openrouter_observations((row,))
    assert observation.provider_id == "openrouter"
    assert observation.offer_kind == OFFER_SUBSIDY
    assert "20 requests/minute" in observation.limits_stated or "20 req" in observation.limits_stated
    assert observation.limits_evidence_url.startswith("https://openrouter.ai/docs")
    assert observation.prices.input_usd_per_m == 0.0
    assert observation.supports_tools and observation.supports_images
    assert observation.prices.cached_input_usd_per_m is None, "OpenRouter publishes no cached-token price"


def test_openrouter_adapter_marks_paid_rows_permanent_without_limits() -> None:
    from types import SimpleNamespace

    from adapters.model_radar_feeds import openrouter_observations

    row = SimpleNamespace(
        model_id="openai/gpt-4.1-mini",
        name="GPT-4.1 mini",
        context_length=1047576,
        prompt_usd_per_token=0.4 / 1_000_000,
        completion_usd_per_token=1.6 / 1_000_000,
        request_usd=None,
        supported_parameters=(),
        input_modalities=("text", "image"),
        output_modalities=("text",),
        fetched_at=_NOW,
        max_output_tokens=0,
    )
    (observation,) = openrouter_observations((row,))
    assert observation.offer_kind == OFFER_PERMANENT
    assert observation.limits_stated == "", "the payload states no limits; the adapter must not invent them"
    assert observation.prices.input_usd_per_m == pytest.approx(0.4)


def test_evidence_window_boundary() -> None:
    from datetime import datetime, timedelta, timezone

    from core.model_radar import evidence_is_fresh

    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    edge = now - timedelta(seconds=EVIDENCE_MAX_AGE_SECONDS)
    older = edge - timedelta(seconds=1)
    assert evidence_is_fresh(edge.isoformat(), now=now)
    assert not evidence_is_fresh(older.isoformat(), now=now)
    assert not evidence_is_fresh("not-a-date", now=now), "unparseable evidence timestamps are stale"


def test_finding_payload_round_trips_through_json(radar_store) -> None:
    observe_feed("openrouter", (obs(pin=2.0, pout=8.0, pcached=1.0),), now=_NOW)
    observe_feed("openrouter", (obs(pin=1.0, pout=4.0, pcached=0.25),), now=_NOW)
    (finding,) = unread_findings()
    text = json.dumps(finding.to_dict())
    assert json.loads(text)["fingerprint"] == finding.fingerprint
