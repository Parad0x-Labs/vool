"""UsePod marketplace prices: fetched from the live feed, validated, normalized in exact integer
units, and never trusted past their freshness window.

Where the prices come from
--------------------------
The public marketplace page reads ``GET <origin>/v1/marketplace/models`` (seen in the page's own
network activity on 2026-09-14; public, no credential, JSON). Each row names a model and carries,
per million tokens, the best price over all routes (``cheapest_*``), the cheapest centralized price,
every centralized provider's own price, and how many marketplace providers are online. The numbers
are integers in USDC microunits: the docs state that unit for "the API and headers", and the live
values matched the rendered marketplace table exactly (``gpt-oss-20b``: 1600 in the feed, "$0.0016"
per million on the page). ``/v1/models`` behind the token proxy lists models; it is not a price
source and is not read for one.

What this module refuses to do
------------------------------
* Invent a rate. A missing, fractional, negative, non-numeric or implausibly large value makes the
  row unusable for spend and records why. A fraction is a unit error, not something to round.
* Keep a rate alive past its window. A snapshot older than its TTL is STALE; routing will not
  authorize a dispatch against it (``core.usepod.routing``).
* Read cache prices nobody published. The feed carries no cache read/write rates, so they are
  reported as ``not_published_by_feed`` -- never as zero and never as the input rate.
* Treat a price as a bill. A feed price is what a listing asked at observation time; what a call
  is charged is decided at settlement by the route that actually served it.

All arithmetic is integer. Display strings are derived from the integers, never the reverse.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

from core.usepod.descriptor import (
    DEFAULT_ORIGIN,
    MARKETPLACE_MODELS_PATH,
    PROVIDER_ID,
    normalize_origin,
    public_target,
)

MICROUNITS_PER_USDC = 1_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000
PRICE_CURRENCY = "USDC"
PRICE_UNIT = "usdc_microunits_per_million_tokens"

#: How long a fetched snapshot may authorize spend. A marketplace moves; two minutes is long enough
#: that a burst of turns shares one fetch and short enough that a withdrawn listing is noticed
#: before the next approval. Overridable by policy within [MIN, MAX].
DEFAULT_TTL_SECONDS = 120
MIN_TTL_SECONDS = 15
MAX_TTL_SECONDS = 900
#: A sanity bound on the FEED, not a price: 10,000 USDC per million tokens. A larger integer is far
#: more likely a unit or encoding fault upstream than a real listing, and it is refused as such.
MAX_PLAUSIBLE_RATE = 10_000_000_000
MAX_FEED_BYTES = 8 * 1024 * 1024
#: Prompt tokens a provider may add that are not visible in the request body (chat templates,
#: gateway-side system text). Added to the byte bound; see :func:`input_token_upper_bound`.
PROMPT_OVERHEAD_TOKENS = 512
CACHE_RATES_STATE = "not_published_by_feed"
_CLOCK_SKEW_TOLERANCE_SECONDS = 5.0

ROUTE_MARKETPLACE = "marketplace"
ROUTE_CENTRALIZED = "centralized"
ROUTE_BEST_AVAILABLE = "best_available"

PRICING_MODE_PER_TOKEN = "per_token"
PRICING_MODE_PER_REQUEST = "per_request"

SNAPSHOT_FRESH = "fresh"
SNAPSHOT_STALE = "stale"
SNAPSHOT_UNAVAILABLE = "unavailable"

EVIDENCE_LIVE_FETCH = "live_fetch"
EVIDENCE_CACHE = "cache"
EVIDENCE_FIXTURE = "fixture"

MARKETPLACE_PRICE_INFERENCE = "best_price_with_marketplace_providers_online_is_a_listing_price_by_the_documented_cap_rule"

_CACHE_SCHEMA = "vool.usepod.marketplace_cache.v1"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: Row fields observed in the live feed on 2026-09-14. Anything else is kept out of the prices and
#: reported as a note, so schema drift is visible instead of silently ignored.
_KNOWN_ROW_FIELDS = frozenset(
    {
        "model_id",
        "pricing_mode",
        "modality",
        "cheapest_input_per_1m",
        "cheapest_output_per_1m",
        "centralized_input_per_1m",
        "centralized_output_per_1m",
        "centralized_providers",
        "marketplace_provider_count",
        "marketplace_total_tps",
        "request_price_microunits",
        "uncensored",
        "cheapest_price_per_image",
        "cheapest_price_per_edit",
        "centralized_price_per_image",
    }
)


class MarketplaceFeedError(RuntimeError):
    """The feed could not be read or did not have the observed shape. ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)


class PriceUnitError(ValueError):
    """A price value that cannot be expressed exactly in the unit it claims."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


# --- exact unit handling ------------------------------------------------------------------------


def usdc_decimal_to_microunits(value: str | int | Decimal) -> int:
    """``"0.40"`` -> ``400000``. Exact or refused.

    Binary floats are refused outright (``0.1`` is not 0.1), and a value with more precision than a
    microunit is refused rather than rounded: a price ceiling that silently rounds is a ceiling the
    owner did not set.
    """
    if isinstance(value, (bool, float)):
        raise PriceUnitError("float_or_bool_not_accepted")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise PriceUnitError("not_a_decimal") from exc
    if not number.is_finite():
        raise PriceUnitError("not_finite")
    if number < 0:
        raise PriceUnitError("negative")
    scaled = number * MICROUNITS_PER_USDC
    if scaled != scaled.to_integral_value():
        raise PriceUnitError("sub_microunit_precision")
    return int(scaled)


def microunits_to_usdc_decimal(microunits: int) -> str:
    """``1600`` -> ``"0.0016"``. Display only; derived from the integer, never parsed back."""
    if isinstance(microunits, bool) or not isinstance(microunits, int):
        raise PriceUnitError("microunits_not_an_integer")
    sign = "-" if microunits < 0 else ""
    whole, fraction = divmod(abs(microunits), MICROUNITS_PER_USDC)
    if not fraction:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{fraction:06d}".rstrip("0")


def price_header_value(microunits_per_million: int) -> str:
    """The exact header value for a price ceiling (``X-Pod-Max-Price-*``)."""
    if isinstance(microunits_per_million, bool) or not isinstance(microunits_per_million, int):
        raise PriceUnitError("ceiling_not_an_integer")
    if microunits_per_million < 1:
        raise PriceUnitError("ceiling_below_one_microunit")
    return str(microunits_per_million)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def charge_upper_bound_microunits(
    *, input_tokens: int, output_tokens: int, input_rate: int, output_rate: int
) -> int:
    """The most a call of this shape can be charged at these per-million rates, rounded UP."""
    for value in (input_tokens, output_tokens, input_rate, output_rate):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PriceUnitError("bound_inputs_must_be_non_negative_integers")
    return _ceil_div(input_tokens * input_rate + output_tokens * output_rate, TOKENS_PER_PRICE_UNIT)


def input_token_upper_bound(body: bytes, *, has_non_text_parts: bool) -> int | None:
    """A hard upper bound on the prompt tokens this exact request can be billed for, or None.

    For a text request the serialized body is the bound: a byte-level tokenizer emits at most one
    token per byte of text, and the JSON framing around every message and tool schema outweighs the
    few control tokens a chat template adds per message. :data:`PROMPT_OVERHEAD_TOKENS` covers text a
    provider injects that the body does not show. Images and other non-text parts are billed by
    rules unrelated to their byte length (a short image URL can cost more than a thousand tokens),
    so a request carrying them has no byte bound and gets None -- the caller must not pretend.
    """
    if has_non_text_parts:
        return None
    if not isinstance(body, (bytes, bytearray)):
        raise PriceUnitError("body_must_be_bytes")
    return len(body) + PROMPT_OVERHEAD_TOKENS


# --- the normalized shapes -------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutePrice:
    """One route's listed price per million tokens. ``provider`` is "" for an aggregate row."""

    route_class: str
    provider: str
    input_microunits_per_million: int | None
    output_microunits_per_million: int | None

    @property
    def complete(self) -> bool:
        return self.input_microunits_per_million is not None and self.output_microunits_per_million is not None

    def within(self, *, max_input: int, max_output: int) -> bool:
        return (
            self.complete
            and int(self.input_microunits_per_million or 0) <= int(max_input)
            and int(self.output_microunits_per_million or 0) <= int(max_output)
        )

    def as_dict(self) -> dict[str, Any]:
        def _display(value: int | None) -> str | None:
            return None if value is None else microunits_to_usdc_decimal(value)

        return {
            "route_class": self.route_class,
            "provider": self.provider,
            "input_microunits_per_million": self.input_microunits_per_million,
            "output_microunits_per_million": self.output_microunits_per_million,
            "input_usdc_per_million": _display(self.input_microunits_per_million),
            "output_usdc_per_million": _display(self.output_microunits_per_million),
            "cache_read": CACHE_RATES_STATE,
            "cache_write": CACHE_RATES_STATE,
        }


@dataclass(frozen=True)
class MarketplaceModel:
    """One feed row, normalized. ``problems`` non-empty means: do not spend against this row.

    ``best_available`` is the feed's ``cheapest_*`` pair, which is the best price over EVERY route:
    measured on 2026-09-14, rows with ``marketplace_provider_count == 0`` report ``cheapest_*`` equal
    to the centralized price. ``marketplace`` is set only when at least one marketplace provider is
    online; then, by UsePod's documented cap-at-centralized rule, no centralized provider undercuts
    every listing, so the best price is a listing price. The feed does not label it -- this is an
    inference from the documented rule (:data:`MARKETPLACE_PRICE_INFERENCE`), and evidence says so.
    """

    model_id: str
    pricing_mode: str
    modality: str
    best_available: RoutePrice | None
    marketplace: RoutePrice | None
    marketplace_provider_count: int | None
    centralized_cheapest: RoutePrice | None
    centralized: tuple[RoutePrice, ...]
    request_price_microunits: int | None
    uncensored: bool | None
    problems: tuple[str, ...]
    notes: tuple[str, ...]
    row_sha256: str

    @property
    def unusable_reason(self) -> str:
        """Why this row cannot bound a token-billed call, or "" when it can."""
        if self.problems:
            return "price_row_malformed"
        if self.modality:
            return f"non_text_modality_unsupported:{self.modality}"
        if self.pricing_mode == PRICING_MODE_PER_REQUEST:
            return "pricing_mode_per_request_unsupported"
        if self.pricing_mode != PRICING_MODE_PER_TOKEN:
            return "pricing_mode_absent" if not self.pricing_mode else "pricing_mode_unrecognized"
        return ""

    def centralized_provider(self, name: str) -> RoutePrice | None:
        for price in self.centralized:
            if price.provider == name:
                return price
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "pricing_mode": self.pricing_mode,
            "modality": self.modality,
            "best_available": self.best_available.as_dict() if self.best_available else None,
            "marketplace": self.marketplace.as_dict() if self.marketplace else None,
            "marketplace_price_inference": MARKETPLACE_PRICE_INFERENCE if self.marketplace else "",
            "marketplace_provider_count": self.marketplace_provider_count,
            "centralized_cheapest": self.centralized_cheapest.as_dict() if self.centralized_cheapest else None,
            "centralized": [price.as_dict() for price in self.centralized],
            "request_price_microunits": self.request_price_microunits,
            "uncensored": self.uncensored,
            "problems": list(self.problems),
            "notes": list(self.notes),
            "unusable_reason": self.unusable_reason,
            "row_sha256": self.row_sha256,
        }


@dataclass(frozen=True)
class MarketplaceSnapshot:
    """One observation of the whole feed, with the provenance needed to judge it."""

    source: str
    origin: str
    fetched_at: float
    http_date: str
    body_sha256: str
    ttl_seconds: int
    models: Mapping[str, MarketplaceModel]
    row_count: int
    rejected_rows: int
    evidence: str
    #: Non-empty only for test fixtures, and then it SAYS what the data is (recorded vs synthetic).
    fixture_label: str = ""

    def age_seconds(self, now: float | None = None) -> float:
        return float(time.time() if now is None else now) - float(self.fetched_at)

    def is_stale(self, now: float | None = None) -> bool:
        age = self.age_seconds(now)
        # A snapshot "from the future" is a clock fault; it is not fresher than a fresh one.
        return age > float(self.ttl_seconds) or age < -_CLOCK_SKEW_TOLERANCE_SECONDS

    def provenance(self, now: float | None = None) -> dict[str, Any]:
        return {
            "source": self.source,
            "origin": self.origin,
            "fetched_at": self.fetched_at,
            "http_date": self.http_date,
            "body_sha256": self.body_sha256,
            "ttl_seconds": self.ttl_seconds,
            "age_seconds": round(self.age_seconds(now), 3),
            "stale": self.is_stale(now),
            "row_count": self.row_count,
            "readable_rows": len(self.models),
            "rejected_rows": self.rejected_rows,
            "evidence": self.evidence,
            "fixture_label": self.fixture_label,
            "currency": PRICE_CURRENCY,
            "unit": PRICE_UNIT,
        }


@dataclass(frozen=True)
class SnapshotResult:
    snapshot: MarketplaceSnapshot | None
    state: str
    error_code: str = ""


# --- parsing ------------------------------------------------------------------------------------------


def _rate(raw: Any, field_name: str, problems: list[str]) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        problems.append(f"{field_name}:not_an_integer")
        return None
    if isinstance(raw, int):
        if raw < 0:
            problems.append(f"{field_name}:negative")
            return None
        if raw > MAX_PLAUSIBLE_RATE:
            problems.append(f"{field_name}:implausibly_large")
            return None
        return raw
    if isinstance(raw, float):
        problems.append(f"{field_name}:fractional_not_microunits")
        return None
    problems.append(f"{field_name}:not_an_integer")
    return None


def _row_sha256(row: dict[str, Any]) -> str:
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _parse_row(row: dict[str, Any]) -> MarketplaceModel | None:
    model_id = row.get("model_id")
    if not isinstance(model_id, str) or not _MODEL_ID_RE.fullmatch(model_id):
        return None
    problems: list[str] = []
    notes: list[str] = []
    unknown_fields = sorted(str(key) for key in row if key not in _KNOWN_ROW_FIELDS)
    if unknown_fields:
        notes.append("unrecognized_fields:" + ",".join(name[:48] for name in unknown_fields[:8]))

    raw_mode = row.get("pricing_mode")
    if raw_mode is None:
        pricing_mode = ""
    elif isinstance(raw_mode, str) and raw_mode in {PRICING_MODE_PER_TOKEN, PRICING_MODE_PER_REQUEST}:
        pricing_mode = raw_mode
    else:
        pricing_mode = "unrecognized"
        problems.append("pricing_mode:unrecognized")
    raw_modality = row.get("modality")
    modality = raw_modality.strip().lower()[:32] if isinstance(raw_modality, str) and raw_modality.strip() else ""

    best_in = _rate(row.get("cheapest_input_per_1m"), "cheapest_input_per_1m", problems)
    best_out = _rate(row.get("cheapest_output_per_1m"), "cheapest_output_per_1m", problems)
    central_in = _rate(row.get("centralized_input_per_1m"), "centralized_input_per_1m", problems)
    central_out = _rate(row.get("centralized_output_per_1m"), "centralized_output_per_1m", problems)

    raw_count = row.get("marketplace_provider_count")
    provider_count: int | None = None
    if raw_count is not None:
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
            provider_count = raw_count
        else:
            problems.append("marketplace_provider_count:invalid")

    centralized: list[RoutePrice] = []
    raw_centralized = row.get("centralized_providers")
    if raw_centralized is not None:
        if not isinstance(raw_centralized, list):
            problems.append("centralized_providers:not_a_list")
        else:
            seen: set[str] = set()
            for index, entry in enumerate(raw_centralized):
                if not isinstance(entry, dict):
                    problems.append(f"centralized_providers[{index}]:not_an_object")
                    continue
                name = entry.get("provider")
                if not isinstance(name, str) or not _PROVIDER_NAME_RE.fullmatch(name):
                    problems.append(f"centralized_providers[{index}].provider:invalid")
                    continue
                if name in seen:
                    problems.append(f"centralized_providers:duplicate:{name}")
                    continue
                seen.add(name)
                centralized.append(
                    RoutePrice(
                        ROUTE_CENTRALIZED,
                        name,
                        _rate(entry.get("input_per_1m"), f"centralized_providers[{name}].input_per_1m", problems),
                        _rate(entry.get("output_per_1m"), f"centralized_providers[{name}].output_per_1m", problems),
                    )
                )

    request_price = (
        _rate(row.get("request_price_microunits"), "request_price_microunits", problems)
        if "request_price_microunits" in row
        else None
    )
    best_available = (
        RoutePrice(ROUTE_BEST_AVAILABLE, "", best_in, best_out)
        if best_in is not None or best_out is not None
        else None
    )
    marketplace = (
        RoutePrice(ROUTE_MARKETPLACE, "", best_in, best_out)
        if best_available is not None and provider_count is not None and provider_count >= 1
        else None
    )
    centralized_cheapest = (
        RoutePrice(ROUTE_CENTRALIZED, "", central_in, central_out)
        if central_in is not None or central_out is not None
        else None
    )
    if (
        pricing_mode == PRICING_MODE_PER_TOKEN
        and best_available is not None
        and centralized_cheapest is not None
        and best_available.complete
        and centralized_cheapest.complete
        and (int(best_in or 0) > int(central_in or 0) or int(best_out or 0) > int(central_out or 0))
    ):
        # The best price over every route can never exceed the cheapest centralized price -- and the
        # provider's own cap rule says the same of every listing. A row that contradicts both is
        # not a row whose numbers can bound spend.
        problems.append("best_price_above_centralized_price")
    if pricing_mode == PRICING_MODE_PER_TOKEN and centralized_cheapest is not None and centralized_cheapest.complete:
        complete_rows = [price for price in centralized if price.complete]
        if complete_rows and not any(
            (price.input_microunits_per_million, price.output_microunits_per_million) == (central_in, central_out)
            for price in complete_rows
        ) and (central_in, central_out) != (
            min(int(price.input_microunits_per_million or 0) for price in complete_rows),
            min(int(price.output_microunits_per_million or 0) for price in complete_rows),
        ):
            notes.append("centralized_summary_matches_no_provider_row")

    raw_uncensored = row.get("uncensored")
    return MarketplaceModel(
        model_id=model_id,
        pricing_mode=pricing_mode,
        modality=modality,
        best_available=best_available,
        marketplace=marketplace,
        marketplace_provider_count=provider_count,
        centralized_cheapest=centralized_cheapest,
        centralized=tuple(centralized),
        request_price_microunits=request_price,
        uncensored=raw_uncensored if isinstance(raw_uncensored, bool) else None,
        problems=tuple(problems),
        notes=tuple(notes),
        row_sha256=_row_sha256(row),
    )


def resolve_ttl_seconds(explicit: int | None = None) -> int:
    value: Any = explicit
    if value is None:
        try:
            from core import policy_engine

            value = policy_engine.get("usepod.price_ttl_seconds", DEFAULT_TTL_SECONDS)
        except Exception:
            value = DEFAULT_TTL_SECONDS
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, seconds))


def parse_marketplace_feed(
    payload: Any,
    *,
    source: str,
    origin: str,
    fetched_at: float,
    http_date: str = "",
    body_sha256: str = "",
    ttl_seconds: int | None = None,
    evidence: str = EVIDENCE_LIVE_FETCH,
    fixture_label: str = "",
) -> MarketplaceSnapshot:
    """Normalize one feed body. Unreadable rows are counted, never guessed into prices."""
    if not isinstance(payload, dict):
        raise MarketplaceFeedError("feed_not_an_object")
    rows = payload.get("models")
    if not isinstance(rows, list):
        raise MarketplaceFeedError("feed_models_not_a_list")
    models: dict[str, MarketplaceModel] = {}
    duplicates: set[str] = set()
    rejected = 0
    for raw in rows:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        parsed = _parse_row(raw)
        if parsed is None:
            rejected += 1
            continue
        if parsed.model_id in models:
            duplicates.add(parsed.model_id)
            continue
        models[parsed.model_id] = parsed
    for model_id in duplicates:
        # Two rows for one model: neither can be trusted to be the one a request will be routed by.
        existing = models[model_id]
        models[model_id] = replace(existing, problems=(*existing.problems, "duplicate_model_id"))
    if rows and not models:
        raise MarketplaceFeedError("feed_has_no_readable_rows")
    return MarketplaceSnapshot(
        source=str(source),
        origin=normalize_origin(origin),
        fetched_at=float(fetched_at),
        http_date=str(http_date or "")[:64],
        body_sha256=str(body_sha256 or ""),
        ttl_seconds=resolve_ttl_seconds(ttl_seconds),
        models=MappingProxyType(models),
        row_count=len(rows),
        rejected_rows=rejected,
        evidence=str(evidence),
        fixture_label=str(fixture_label or ""),
    )


# --- fetching and the short-lived cache ----------------------------------------------------------------


def _governed_open(request: Any, timeout: float) -> Any:
    """The ONE outbound door, with redirects refused: a redirected price source is a different source."""
    from core.remote_fetch_policy import open_remote

    return open_remote(
        request,
        timeout=timeout,
        provider_id=PROVIDER_ID,
        keyed_or_keyless="keyless",
        redirect_policy="refuse",
    )


def fetch_marketplace_snapshot(
    *,
    origin: str = DEFAULT_ORIGIN,
    timeout_seconds: float = 15.0,
    opener: Callable[[Any, float], Any] | None = None,
    clock: Callable[[], float] = time.time,
    ttl_seconds: int | None = None,
    persist: bool = True,
) -> MarketplaceSnapshot:
    """Fetch, validate and (by default) cache the live feed. Raises :class:`MarketplaceFeedError`."""
    import urllib.error
    import urllib.request

    target = public_target(origin=origin, path=MARKETPLACE_MODELS_PATH)
    request = urllib.request.Request(
        target.wire_url(),
        headers={"Accept": "application/json", "User-Agent": "vool-usepod-catalog/1"},
        method="GET",
    )
    open_call = opener or _governed_open
    from core.effect_gateway import named_background_effect_scope

    failure: MarketplaceFeedError | None = None
    response: Any = None
    try:
        with named_background_effect_scope("usepod.marketplace_price_refresh"):
            response = open_call(request, float(timeout_seconds))
    except urllib.error.HTTPError as exc:
        reason = str(getattr(exc, "msg", "") or "")
        code = "feed_redirect_refused" if reason.startswith("redirect_refused") else "feed_http_error"
        failure = MarketplaceFeedError(code, f"HTTP {exc.code}")
        with contextlib.suppress(Exception):
            exc.close()
    except Exception as exc:
        failure = MarketplaceFeedError("feed_unreachable", type(exc).__name__)
    if failure is not None:
        raise failure
    try:
        status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
        headers = getattr(response, "headers", None)
        content_type = str(headers.get("Content-Type") or "") if headers is not None else ""
        http_date = str(headers.get("Date") or "") if headers is not None else ""
        raw = response.read(MAX_FEED_BYTES + 1)
    finally:
        with contextlib.suppress(Exception):
            response.close()
    if status != 200:
        raise MarketplaceFeedError("feed_http_error", f"HTTP {status}")
    if not isinstance(raw, (bytes, bytearray)):
        raise MarketplaceFeedError("feed_body_unreadable")
    if len(raw) > MAX_FEED_BYTES:
        raise MarketplaceFeedError("feed_too_large")
    if content_type and "json" not in content_type.lower():
        raise MarketplaceFeedError("feed_not_json", content_type[:64])
    try:
        text = bytes(raw).decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        payload = None
        text = ""
    if payload is None:
        raise MarketplaceFeedError("feed_malformed_json")
    snapshot = parse_marketplace_feed(
        payload,
        source=target.redacted_url,
        origin=target.origin,
        fetched_at=float(clock()),
        http_date=http_date,
        body_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
        ttl_seconds=ttl_seconds,
        evidence=EVIDENCE_LIVE_FETCH,
    )
    if persist:
        _write_cache(text=text, snapshot=snapshot)
    return snapshot


def _cache_path() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / "usepod" / "marketplace_models.json").resolve()


def _write_cache(*, text: str, snapshot: MarketplaceSnapshot) -> None:
    path = _cache_path()
    record = {
        "schema": _CACHE_SCHEMA,
        "origin": snapshot.origin,
        "source": snapshot.source,
        "fetched_at": snapshot.fetched_at,
        "http_date": snapshot.http_date,
        "body_sha256": snapshot.body_sha256,
        "body": text,
    }
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=True, separators=(",", ":"))
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)


def load_cached_snapshot(*, origin: str = DEFAULT_ORIGIN, ttl_seconds: int | None = None) -> MarketplaceSnapshot | None:
    """The cached snapshot for this origin, integrity-checked, or None. Freshness is the caller's call."""
    try:
        record = json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(record, dict) or record.get("schema") != _CACHE_SCHEMA:
        return None
    try:
        if normalize_origin(str(record.get("origin") or "")) != normalize_origin(origin):
            return None
        body = str(record["body"])
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != str(record.get("body_sha256") or ""):
            # Edited on disk: the prices no longer match the bytes that were fetched.
            return None
        return parse_marketplace_feed(
            json.loads(body),
            source=str(record.get("source") or ""),
            origin=str(record["origin"]),
            fetched_at=float(record["fetched_at"]),
            http_date=str(record.get("http_date") or ""),
            body_sha256=str(record["body_sha256"]),
            ttl_seconds=ttl_seconds,
            evidence=EVIDENCE_CACHE,
        )
    except Exception:
        return None


def current_snapshot(
    *,
    origin: str = DEFAULT_ORIGIN,
    allow_network: bool,
    now: float | None = None,
    ttl_seconds: int | None = None,
    fetch: Callable[..., MarketplaceSnapshot] | None = None,
) -> SnapshotResult:
    """Fresh cache, else (when allowed) a live fetch, else the stale cache marked stale, else unavailable."""
    moment = float(time.time() if now is None else now)
    cached = load_cached_snapshot(origin=origin, ttl_seconds=ttl_seconds)
    if cached is not None and not cached.is_stale(moment):
        return SnapshotResult(cached, SNAPSHOT_FRESH)
    if allow_network:
        fetcher = fetch or fetch_marketplace_snapshot
        try:
            fetched = fetcher(origin=origin, ttl_seconds=ttl_seconds)
        except MarketplaceFeedError as exc:
            if cached is not None:
                return SnapshotResult(cached, SNAPSHOT_STALE, exc.code)
            return SnapshotResult(None, SNAPSHOT_UNAVAILABLE, exc.code)
        if fetched.is_stale(moment):
            return SnapshotResult(fetched, SNAPSHOT_STALE, "fetched_snapshot_already_stale")
        return SnapshotResult(fetched, SNAPSHOT_FRESH)
    if cached is not None:
        return SnapshotResult(cached, SNAPSHOT_STALE, "cache_only_and_stale")
    return SnapshotResult(None, SNAPSHOT_UNAVAILABLE, "no_cached_snapshot")


__all__ = [
    "CACHE_RATES_STATE",
    "DEFAULT_TTL_SECONDS",
    "EVIDENCE_CACHE",
    "EVIDENCE_FIXTURE",
    "EVIDENCE_LIVE_FETCH",
    "MARKETPLACE_PRICE_INFERENCE",
    "MAX_PLAUSIBLE_RATE",
    "MICROUNITS_PER_USDC",
    "PRICE_CURRENCY",
    "PRICE_UNIT",
    "PRICING_MODE_PER_REQUEST",
    "PRICING_MODE_PER_TOKEN",
    "PROMPT_OVERHEAD_TOKENS",
    "ROUTE_BEST_AVAILABLE",
    "ROUTE_CENTRALIZED",
    "ROUTE_MARKETPLACE",
    "SNAPSHOT_FRESH",
    "SNAPSHOT_STALE",
    "SNAPSHOT_UNAVAILABLE",
    "MarketplaceFeedError",
    "MarketplaceModel",
    "MarketplaceSnapshot",
    "PriceUnitError",
    "RoutePrice",
    "SnapshotResult",
    "charge_upper_bound_microunits",
    "current_snapshot",
    "fetch_marketplace_snapshot",
    "input_token_upper_bound",
    "load_cached_snapshot",
    "microunits_to_usdc_decimal",
    "parse_marketplace_feed",
    "price_header_value",
    "resolve_ttl_seconds",
    "usdc_decimal_to_microunits",
]
