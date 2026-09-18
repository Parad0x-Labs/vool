"""UsePod pricing: exact units, the live feed's observed shape, freshness and cache integrity.

Rows from ``fixtures/marketplace_models_recorded_20260914.json`` are RECORDED from the public feed (see
its .meta.json). Every hostile row below is SYNTHETIC and built in the test that uses it.
"""
from __future__ import annotations

import io
import json
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

from core.usepod import pricing

FIXTURE = Path(__file__).parent / "fixtures" / "marketplace_models_recorded_20260914.json"
ORIGIN = "https://api.usepod.ai"


def _snapshot(payload, *, fetched_at: float = 10_000.0, ttl: int = 120, label: str = "RECORDED 2026-09-14"):
    return pricing.parse_marketplace_feed(
        payload,
        source="fixture",
        origin=ORIGIN,
        fetched_at=fetched_at,
        ttl_seconds=ttl,
        evidence=pricing.EVIDENCE_FIXTURE,
        fixture_label=label,
    )


def _synthetic_row(**overrides):
    row = {
        "model_id": "lyra-7b-synth",
        "pricing_mode": "per_token",
        "cheapest_input_per_1m": 640_000,
        "cheapest_output_per_1m": 1_200_000,
        "centralized_input_per_1m": 900_000,
        "centralized_output_per_1m": 2_000_000,
        "centralized_providers": [{"provider": "together", "input_per_1m": 900_000, "output_per_1m": 2_000_000}],
        "marketplace_provider_count": 3,
        "marketplace_total_tps": 0.0,
        "uncensored": False,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.40", 400_000), ("0.0016", 1_600), ("12", 12_000_000), (Decimal("0.000001"), 1), (7, 7_000_000)],
)
def test_decimal_usdc_converts_to_exact_microunits(value, expected) -> None:
    assert pricing.usdc_decimal_to_microunits(value) == expected


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (0.4, "float_or_bool_not_accepted"),
        (True, "float_or_bool_not_accepted"),
        ("0.0000001", "sub_microunit_precision"),
        ("-1", "negative"),
        ("NaN", "not_finite"),
        ("forty cents", "not_a_decimal"),
    ],
)
def test_a_price_that_cannot_be_exact_is_refused_not_rounded(value, code) -> None:
    with pytest.raises(pricing.PriceUnitError) as caught:
        pricing.usdc_decimal_to_microunits(value)
    assert caught.value.code == code


def test_ceiling_headers_are_integer_microunits_never_a_display_price() -> None:
    assert pricing.price_header_value(400_000) == "400000"
    for bad in (0, -5, True, 0.4, "400000"):
        with pytest.raises(pricing.PriceUnitError):
            pricing.price_header_value(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(("microunits", "display"), [(1_600, "0.0016"), (77_138, "0.077138"), (5_000_000, "5"), (1, "0.000001"), (0, "0")])
def test_display_decimal_is_derived_from_the_integer(microunits, display) -> None:
    assert pricing.microunits_to_usdc_decimal(microunits) == display


def test_the_charge_bound_uses_both_axes_and_rounds_up() -> None:
    assert pricing.charge_upper_bound_microunits(input_tokens=1_000, output_tokens=64, input_rate=1_600, output_rate=8_000) == 3
    assert pricing.charge_upper_bound_microunits(input_tokens=1_000_000, output_tokens=0, input_rate=1, output_rate=999) == 1
    assert pricing.charge_upper_bound_microunits(input_tokens=0, output_tokens=0, input_rate=5, output_rate=5) == 0
    for bad in (-1, True, 1.5):
        with pytest.raises(pricing.PriceUnitError):
            pricing.charge_upper_bound_microunits(input_tokens=bad, output_tokens=1, input_rate=1, output_rate=1)  # type: ignore[arg-type]


def test_the_input_bound_is_body_bytes_plus_declared_overhead_and_absent_for_images() -> None:
    body = b'{"model":"x","messages":[{"role":"user","content":"hi"}]}'
    assert pricing.input_token_upper_bound(body, has_non_text_parts=False) == len(body) + pricing.PROMPT_OVERHEAD_TOKENS
    assert pricing.input_token_upper_bound(body, has_non_text_parts=True) is None


def test_recorded_rows_normalize_with_the_semantics_the_live_feed_showed() -> None:
    snapshot = _snapshot(json.loads(FIXTURE.read_text()))
    assert snapshot.row_count == 12 and snapshot.rejected_rows == 0
    gpt = snapshot.models["gpt-oss-20b"]
    assert gpt.unusable_reason == ""
    assert (gpt.marketplace.input_microunits_per_million, gpt.marketplace.output_microunits_per_million) == (1_600, 8_000)
    assert gpt.best_available == pricing.RoutePrice("best_available", "", 1_600, 8_000)
    assert {price.provider for price in gpt.centralized} == {"nousresearch", "openrouter", "uomi", "together"}
    # With no marketplace provider online, the feed's "cheapest" is the centralized price itself.
    bge = snapshot.models["bge-base-en-v1-5"]
    assert bge.marketplace is None and bge.marketplace_provider_count == 0
    assert (bge.best_available.input_microunits_per_million, bge.centralized_cheapest.input_microunits_per_million) == (8_000, 8_000)
    assert snapshot.models["c0mpute-pro"].unusable_reason == "pricing_mode_per_request_unsupported"
    assert snapshot.models["dall-e-2"].unusable_reason == "non_text_modality_unsupported:image"
    empty = snapshot.models["anthropic-claude-opus-4-7"]
    assert empty.unusable_reason == "" and empty.marketplace is None and empty.centralized == ()
    rendered = gpt.as_dict()
    assert rendered["marketplace"]["cache_read"] == pricing.CACHE_RATES_STATE
    assert rendered["marketplace"]["input_usdc_per_million"] == "0.0016"


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"cheapest_input_per_1m": 1600.5}, "cheapest_input_per_1m:fractional_not_microunits"),
        ({"cheapest_output_per_1m": -1}, "cheapest_output_per_1m:negative"),
        ({"centralized_input_per_1m": 10**13}, "centralized_input_per_1m:implausibly_large"),
        ({"cheapest_input_per_1m": "640000"}, "cheapest_input_per_1m:not_an_integer"),
        ({"centralized_output_per_1m": True}, "centralized_output_per_1m:not_an_integer"),
        ({"cheapest_input_per_1m": 950_000}, "best_price_above_centralized_price"),
        ({"pricing_mode": "per_second"}, "pricing_mode:unrecognized"),
        ({"marketplace_provider_count": -2}, "marketplace_provider_count:invalid"),
        ({"centralized_providers": [{"provider": "Venice!", "input_per_1m": 1, "output_per_1m": 1}]}, "centralized_providers[0].provider:invalid"),
        ({"centralized_providers": "together"}, "centralized_providers:not_a_list"),
    ],
    ids=["fraction", "negative", "huge", "string", "bool", "above-cap", "mode", "count", "provider-name", "providers-shape"],
)
def test_a_hostile_synthetic_row_is_unusable_for_spend_and_says_why(overrides, problem) -> None:
    snapshot = _snapshot({"models": [_synthetic_row(**overrides)]}, label="SYNTHETIC hostile row")
    model = snapshot.models["lyra-7b-synth"]
    assert model.unusable_reason == "price_row_malformed"
    assert problem in model.problems


def test_duplicate_rows_poison_the_model_instead_of_picking_one() -> None:
    snapshot = _snapshot({"models": [_synthetic_row(), _synthetic_row(cheapest_input_per_1m=100)]}, label="SYNTHETIC duplicate")
    assert "duplicate_model_id" in snapshot.models["lyra-7b-synth"].problems


def test_schema_drift_is_reported_as_a_note_not_silently_ignored() -> None:
    snapshot = _snapshot({"models": [_synthetic_row(cheapest_price_per_minute=12)]}, label="SYNTHETIC drift")
    model = snapshot.models["lyra-7b-synth"]
    assert model.unusable_reason == ""
    assert any(note.startswith("unrecognized_fields:") and "cheapest_price_per_minute" in note for note in model.notes)


@pytest.mark.parametrize(
    ("payload", "code"),
    [([], "feed_not_an_object"), ({"data": []}, "feed_models_not_a_list"), ({"models": [{"id": "x"}, 3]}, "feed_has_no_readable_rows")],
)
def test_an_unreadable_feed_is_a_typed_error(payload, code) -> None:
    with pytest.raises(pricing.MarketplaceFeedError) as caught:
        _snapshot(payload)
    assert caught.value.code == code


def test_freshness_window_and_clock_skew() -> None:
    snapshot = _snapshot({"models": [_synthetic_row()]}, fetched_at=10_000.0, ttl=120)
    assert not snapshot.is_stale(10_000.0 + 120)
    assert snapshot.is_stale(10_000.0 + 121)
    assert snapshot.is_stale(10_000.0 - 60)  # "from the future" is a clock fault, never extra-fresh


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield tmp_path
    runtime_paths.configure_runtime_home(None)


class _FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.status = status
        self.headers = {"Content-Type": content_type, "Date": "Mon, 14 Sep 2026 18:33:37 GMT"}
        self._body = io.BytesIO(body)

    def read(self, limit: int = -1) -> bytes:
        return self._body.read(limit)

    def close(self) -> None:
        return None


def _opener(response=None, error: Exception | None = None):
    seen: list[str] = []

    def _open(request, timeout):
        seen.append(request.full_url)
        if error is not None:
            raise error
        return response

    _open.seen = seen  # type: ignore[attr-defined]
    return _open


def test_a_fetch_is_validated_cached_and_the_cache_is_integrity_checked(isolated_home) -> None:
    body = json.dumps({"models": [_synthetic_row()]}).encode()
    opener = _opener(_FakeResponse(200, body))
    fetched = pricing.fetch_marketplace_snapshot(origin=ORIGIN, opener=opener, clock=lambda: 20_000.0, ttl_seconds=120)
    assert opener.seen == [f"{ORIGIN}/v1/marketplace/models"]
    assert fetched.evidence == pricing.EVIDENCE_LIVE_FETCH and fetched.http_date.startswith("Mon, 14 Sep")
    cached = pricing.load_cached_snapshot(origin=ORIGIN, ttl_seconds=120)
    assert cached is not None and cached.evidence == pricing.EVIDENCE_CACHE and cached.body_sha256 == fetched.body_sha256
    assert pricing.load_cached_snapshot(origin="https://gateway.example.test") is None  # another origin's cache is not this one's
    cache_file = isolated_home / "data" / "usepod" / "marketplace_models.json"
    if not cache_file.exists():
        cache_file = next(isolated_home.rglob("marketplace_models.json"))
    record = json.loads(cache_file.read_text())
    record["body"] = record["body"].replace("640000", "64000")  # a cheaper price written in by hand
    cache_file.write_text(json.dumps(record))
    assert pricing.load_cached_snapshot(origin=ORIGIN) is None


@pytest.mark.parametrize(
    ("response", "error", "code"),
    [
        (_FakeResponse(503, b"{}"), None, "feed_http_error"),
        (_FakeResponse(200, b"<html>marketplace</html>", "text/html"), None, "feed_not_json"),
        (_FakeResponse(200, b"{not json"), None, "feed_malformed_json"),
        (None, urllib.error.HTTPError("https://api.usepod.ai/v1/marketplace/models", 302, "redirect_refused:https://prices.example.test/", {}, None), "feed_redirect_refused"),
        (None, urllib.error.URLError("unreachable"), "feed_unreachable"),
    ],
    ids=["status", "html", "json", "redirect", "network"],
)
def test_a_bad_fetch_is_a_typed_refusal_and_writes_no_cache(isolated_home, response, error, code) -> None:
    with pytest.raises(pricing.MarketplaceFeedError) as caught:
        pricing.fetch_marketplace_snapshot(origin=ORIGIN, opener=_opener(response, error), ttl_seconds=120)
    assert caught.value.code == code
    assert pricing.load_cached_snapshot(origin=ORIGIN) is None


def test_an_oversized_feed_is_refused(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(pricing, "MAX_FEED_BYTES", 64)
    with pytest.raises(pricing.MarketplaceFeedError) as caught:
        pricing.fetch_marketplace_snapshot(origin=ORIGIN, opener=_opener(_FakeResponse(200, b"{" + b" " * 200 + b"}")))
    assert caught.value.code == "feed_too_large"


def test_current_snapshot_is_fresh_cache_then_fetch_then_stale_then_unavailable(isolated_home) -> None:
    calls: list[str] = []

    def fetch_ok(*, origin, ttl_seconds):
        calls.append("fetch")
        return _snapshot({"models": [_synthetic_row()]}, fetched_at=50_000.0, ttl=120)

    def fetch_fail(*, origin, ttl_seconds):
        calls.append("fetch-fail")
        raise pricing.MarketplaceFeedError("feed_unreachable")

    assert pricing.current_snapshot(origin=ORIGIN, allow_network=False, now=50_000.0).state == pricing.SNAPSHOT_UNAVAILABLE
    result = pricing.current_snapshot(origin=ORIGIN, allow_network=True, now=50_010.0, fetch=fetch_ok)
    assert result.state == pricing.SNAPSHOT_FRESH and calls == ["fetch"]
    unavailable = pricing.current_snapshot(origin=ORIGIN, allow_network=True, now=50_010.0, fetch=fetch_fail)
    assert unavailable.state == pricing.SNAPSHOT_UNAVAILABLE and unavailable.error_code == "feed_unreachable"
    old = pricing.current_snapshot(origin=ORIGIN, allow_network=True, now=90_000.0, fetch=fetch_ok)
    assert old.state == pricing.SNAPSHOT_STALE and old.error_code == "fetched_snapshot_already_stale"
