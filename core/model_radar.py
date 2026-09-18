"""Model Radar — the normalized price/capability authority for honest discount discovery.

One authority owns the truth here. Provider adapters (``adapters/model_radar_feeds``)
translate wire formats into ``ModelObservation`` rows and nothing else; every decision
about whether a change is WORTH TELLING THE USER lives in this module, expressed as
pure functions over typed data with no I/O.

The radar qualifies exactly two shapes of news:

1. ``new_free``  — a model with a RECORDED paid past became genuinely free: every
   published price component is an explicit zero, the offer kind is one that can mean
   "free" (permanent / free quota / provider subsidy — never a trial, never an
   unknown), and the free arrangement's limits are STATED by evidence. "Genuinely
   free under clearly stated limits" is one condition, not two: an unstated limit
   is an unstated bill waiting to land.
2. ``price_drop`` — the SAME provider cut the recorded public price of the SAME
   canonical model by at least 50% on EVERY published component. Input, output and
   cached-input prices are compared each on their own; they are never blended into
   an average, because a 60% input cut paired with a 20% output rise is a
   repricing, not a discount, and the average would lie about which one it is.

Fail-closed defaults everywhere: unpublished prices are unknown (never zero, never
cheaper), stale evidence qualifies nothing and does not touch the recorded baseline,
trials and unknown offer kinds qualify nothing, and the BEFORE side of every
comparison is the operator's own previously recorded observation — a feed may state
its current price, but it can never supply the "before" that its own discount is
measured against.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from core.cloud_model_control import canonical_model_identity

# Offer taxonomy. A price is only one axis; WHAT KIND of arrangement produced it
# decides whether it can qualify at all.
OFFER_PERMANENT = "permanent"
OFFER_PROMOTION = "promotion"
OFFER_FREE_QUOTA = "free_quota"
OFFER_TRIAL = "trial"
OFFER_SUBSIDY = "subsidy"
OFFER_UNKNOWN = "unknown"

OFFER_KINDS = frozenset(
    {OFFER_PERMANENT, OFFER_PROMOTION, OFFER_FREE_QUOTA, OFFER_TRIAL, OFFER_SUBSIDY, OFFER_UNKNOWN}
)
# Kinds that can ground a "genuinely free" claim. A trial is time-boxed personal
# credit, and an unknown kind is an unread contract -- neither is "free".
_FREE_GROUNDING_KINDS = frozenset({OFFER_PERMANENT, OFFER_FREE_QUOTA, OFFER_SUBSIDY})
# Kinds that can ground a "public price reduced" claim. A trial's intro price is
# not the public price; an unknown kind is not a stated commitment.
_DROP_GROUNDING_KINDS = frozenset({OFFER_PERMANENT, OFFER_PROMOTION, OFFER_SUBSIDY})

# Evidence older than this window qualifies nothing. Catalog refreshes run at most
# hourly, so six hours tolerates missed refreshes without letting week-old numbers
# speak as news.
EVIDENCE_MAX_AGE_SECONDS = 6 * 3600

# The notification bar: a cut must reach HALF OFF on every published component.
DROP_THRESHOLD = 0.50

# Two observations of the same provider+model disagree by more than this fraction
# on any component -> the truth is unknown -> fail closed for that model.
CONFLICT_TOLERANCE = 0.05

COMPONENT_INPUT = "input_usd_per_m"
COMPONENT_OUTPUT = "output_usd_per_m"
COMPONENT_CACHED = "cached_input_usd_per_m"
_COMPONENT_FIELDS = (COMPONENT_INPUT, COMPONENT_OUTPUT, COMPONENT_CACHED)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def evidence_is_fresh(evidence_fetched_at: str, *, now: str | datetime) -> bool:
    """Whether the evidence timestamp is inside the qualification window.

    An unparseable or missing timestamp is NOT fresh: unknown evidence age is
    unknown evidence, and unknown evidence produces no notification.
    """
    moment = now if isinstance(now, datetime) else _parse_iso(str(now or ""))
    stamped = _parse_iso(evidence_fetched_at)
    if moment is None or stamped is None:
        return False
    age = (moment - stamped).total_seconds()
    return -300.0 <= age <= EVIDENCE_MAX_AGE_SECONDS  # 5min clock-skew allowance


def offer_expires_in_future(expires_at: str, *, now: str | datetime) -> bool:
    moment = now if isinstance(now, datetime) else _parse_iso(str(now or ""))
    stamp = _parse_iso(expires_at)
    if moment is None or stamp is None:
        return False
    return stamp > moment


@dataclass(frozen=True)
class PriceComponents:
    """Per-1M-token USD prices. ``None`` means the source did not publish one.

    None is never coerced to 0.0 anywhere in the radar: an unpublished price is an
    unknown price, and unknown is the wrong side of every fail-closed gate.
    """

    input_usd_per_m: float | None
    output_usd_per_m: float | None
    cached_input_usd_per_m: float | None = None

    def published(self) -> dict[str, float]:
        return {
            name: getattr(self, name)
            for name in _COMPONENT_FIELDS
            if getattr(self, name) is not None
        }

    def all_published_are_zero(self) -> bool:
        values = list(self.published().values())
        # The load-bearing pair must be readable for any free claim: a row with no
        # published input/output price is an unknown-cost row, not a free one.
        if self.input_usd_per_m is None or self.output_usd_per_m is None:
            return False
        return all(float(v) == 0.0 for v in values)


@dataclass(frozen=True)
class ModelObservation:
    """One provider-published fact row, already normalized. Adapters build these;
    the authority trusts nothing that did not come through this typed shape."""

    provider_id: str
    model_id: str
    display_name: str
    prices: PriceComponents
    offer_kind: str
    # The free arrangement's stated limits (e.g. "20 requests/minute, 50/day").
    # Empty string = the evidence states no limits -> free claims fail closed.
    limits_stated: str = ""
    limits_evidence_url: str = ""
    # ISO timestamp when this offer ends. Promotions require one in the future.
    expires_at: str = ""
    context_length: int = 0
    supports_tools: bool = False
    supports_images: bool = False
    # What the evidence says about data handling; empty = not stated.
    privacy_terms: str = ""
    evidence_url: str = ""
    evidence_fetched_at: str = ""
    source_feed: str = ""

    def __post_init__(self) -> None:
        if self.offer_kind not in OFFER_KINDS:
            raise ValueError(f"unknown offer kind: {self.offer_kind!r}")
        object.__setattr__(self, "provider_id", str(self.provider_id or "").strip().lower())
        object.__setattr__(self, "model_id", canonical_model_identity(self.model_id))
        if not self.provider_id or not self.model_id:
            raise ValueError("observation requires a provider and a canonical model id")

    def identity(self) -> tuple[str, str]:
        return (self.provider_id, self.model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "prices": {
                "input_usd_per_m": self.prices.input_usd_per_m,
                "output_usd_per_m": self.prices.output_usd_per_m,
                "cached_input_usd_per_m": self.prices.cached_input_usd_per_m,
            },
            "offer_kind": self.offer_kind,
            "limits_stated": self.limits_stated,
            "limits_evidence_url": self.limits_evidence_url,
            "expires_at": self.expires_at,
            "context_length": self.context_length,
            "supports_tools": self.supports_tools,
            "supports_images": self.supports_images,
            "privacy_terms": self.privacy_terms,
            "evidence_url": self.evidence_url,
            "evidence_fetched_at": self.evidence_fetched_at,
            "source_feed": self.source_feed,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelObservation:
        prices = dict(payload.get("prices") or {})
        return cls(
            provider_id=str(payload.get("provider_id") or ""),
            model_id=str(payload.get("model_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            prices=PriceComponents(
                input_usd_per_m=prices.get("input_usd_per_m"),
                output_usd_per_m=prices.get("output_usd_per_m"),
                cached_input_usd_per_m=prices.get("cached_input_usd_per_m"),
            ),
            offer_kind=str(payload.get("offer_kind") or OFFER_UNKNOWN),
            limits_stated=str(payload.get("limits_stated") or ""),
            limits_evidence_url=str(payload.get("limits_evidence_url") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            context_length=int(payload.get("context_length") or 0),
            supports_tools=bool(payload.get("supports_tools")),
            supports_images=bool(payload.get("supports_images")),
            privacy_terms=str(payload.get("privacy_terms") or ""),
            evidence_url=str(payload.get("evidence_url") or ""),
            evidence_fetched_at=str(payload.get("evidence_fetched_at") or ""),
            source_feed=str(payload.get("source_feed") or ""),
        )


@dataclass(frozen=True)
class RadarPreferences:
    """The operator's notification contract. Defaults stay quiet and unfiltered."""

    enabled: bool = True
    # Empty tuple = every provider. Otherwise only these provider ids notify.
    providers: tuple[str, ...] = ()
    # "any" | "cloud" | "local" — cloud price news is noise on a local-only setup.
    lane: str = "any"
    # Subset of {"tools", "images"}: capabilities a suggested model must support.
    required_capabilities: tuple[str, ...] = ()
    # Minimum hours between ISSUED findings for the same model. 0 = immediate.
    min_interval_hours: int = 0

    def __post_init__(self) -> None:
        if self.lane not in {"any", "cloud", "local"}:
            raise ValueError(f"unknown lane: {self.lane!r}")
        unknown = set(self.required_capabilities) - {"tools", "images"}
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "providers": list(self.providers),
            "lane": self.lane,
            "required_capabilities": list(self.required_capabilities),
            "min_interval_hours": self.min_interval_hours,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RadarPreferences:
        lane = str(payload.get("lane") or "any")
        if lane not in {"any", "cloud", "local"}:
            lane = "any"
        caps = tuple(
            cap
            for cap in (str(c) for c in list(payload.get("required_capabilities") or []))
            if cap in {"tools", "images"}
        )
        try:
            interval = max(0, int(payload.get("min_interval_hours") or 0))
        except (TypeError, ValueError):
            interval = 0
        providers = tuple(sorted({str(p).strip().lower() for p in list(payload.get("providers") or []) if str(p).strip()}))
        return cls(
            enabled=bool(payload.get("enabled", True)),
            providers=providers,
            lane=lane,
            required_capabilities=caps,
            min_interval_hours=interval,
        )


# Refusal reason codes -- machine-readable, asserted by tests, shown in diagnostics.
REFUSAL_NO_BASELINE = "no_baseline"
REFUSAL_STALE_EVIDENCE = "stale_evidence"
REFUSAL_UNKNOWN_AFTER_PRICING = "unknown_after_pricing"
REFUSAL_PRICE_INCREASE = "price_increase"
REFUSAL_BELOW_THRESHOLD = "below_threshold"
REFUSAL_OFFER_KIND_EXCLUDED = "offer_kind_excluded"
REFUSAL_LIMITS_NOT_STATED = "limits_not_stated"
REFUSAL_PROMO_NO_EXPIRY = "promo_no_expiry"
REFUSAL_EXPIRED_OFFER = "expired_offer"
REFUSAL_ALREADY_FREE_BEFORE = "already_free_before"
REFUSAL_CAPABILITY_MISMATCH = "capability_mismatch"
REFUSAL_PROVIDER_FILTERED = "provider_filtered"
REFUSAL_LANE_FILTERED = "lane_filtered"
REFUSAL_RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class QualificationRefusal:
    reason_code: str
    detail: str


@dataclass(frozen=True)
class RadarFinding:
    """A qualified, notify-worthy change. Self-contained: the card renders from
    this row alone, which is what keeps findings readable through outages."""

    fingerprint: str
    kind: str  # "new_free" | "price_drop"
    provider_id: str
    model_id: str
    display_name: str
    before: dict[str, float]
    after: dict[str, float]
    # Per-component fraction reduced, e.g. {"input_usd_per_m": 0.625}. One entry
    # per published-before-and-after component; NEVER a blended average.
    reductions: dict[str, float]
    offer_kind: str
    limits_stated: str
    limits_evidence_url: str
    expires_at: str
    context_length: int
    supports_tools: bool
    supports_images: bool
    privacy_terms: str
    evidence_url: str
    evidence_fetched_at: str
    before_evidence_fetched_at: str
    why: str
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "kind": self.kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "before": dict(self.before),
            "after": dict(self.after),
            "reductions": dict(self.reductions),
            "offer_kind": self.offer_kind,
            "limits_stated": self.limits_stated,
            "limits_evidence_url": self.limits_evidence_url,
            "expires_at": self.expires_at,
            "context_length": self.context_length,
            "supports_tools": self.supports_tools,
            "supports_images": self.supports_images,
            "privacy_terms": self.privacy_terms,
            "evidence_url": self.evidence_url,
            "evidence_fetched_at": self.evidence_fetched_at,
            "before_evidence_fetched_at": self.before_evidence_fetched_at,
            "why": self.why,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RadarFinding:
        return cls(
            fingerprint=str(payload.get("fingerprint") or ""),
            kind=str(payload.get("kind") or ""),
            provider_id=str(payload.get("provider_id") or ""),
            model_id=str(payload.get("model_id") or ""),
            display_name=str(payload.get("display_name") or ""),
            before={str(k): float(v) for k, v in dict(payload.get("before") or {}).items()},
            after={str(k): float(v) for k, v in dict(payload.get("after") or {}).items()},
            reductions={str(k): float(v) for k, v in dict(payload.get("reductions") or {}).items()},
            offer_kind=str(payload.get("offer_kind") or OFFER_UNKNOWN),
            limits_stated=str(payload.get("limits_stated") or ""),
            limits_evidence_url=str(payload.get("limits_evidence_url") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            context_length=int(payload.get("context_length") or 0),
            supports_tools=bool(payload.get("supports_tools")),
            supports_images=bool(payload.get("supports_images")),
            privacy_terms=str(payload.get("privacy_terms") or ""),
            evidence_url=str(payload.get("evidence_url") or ""),
            evidence_fetched_at=str(payload.get("evidence_fetched_at") or ""),
            before_evidence_fetched_at=str(payload.get("before_evidence_fetched_at") or ""),
            why=str(payload.get("why") or ""),
            created_at=str(payload.get("created_at") or ""),
        )


def _money(value: float) -> str:
    return f"${float(value):.4f}".rstrip("0").rstrip(".")


def _component_words(name: str) -> str:
    return {
        COMPONENT_INPUT: "input",
        COMPONENT_OUTPUT: "output",
        COMPONENT_CACHED: "cached input",
    }[name]


def finding_fingerprint(
    *, provider_id: str, model_id: str, kind: str, prices: PriceComponents, offer_kind: str, expires_at: str
) -> str:
    """Stable identity of a qualified state.

    Two observations of the same (provider, model, kind, resulting prices, offer
    kind, expiry) are the SAME finding: the fingerprint is what deduplicates a
    refresh storm and what a dismissal is recorded against. A further price change
    produces a different fingerprint -- it is genuinely new news.
    """
    basis = "|".join(
        (
            "v1",
            str(provider_id),
            str(model_id),
            str(kind),
            repr(prices.input_usd_per_m),
            repr(prices.output_usd_per_m),
            repr(prices.cached_input_usd_per_m),
            str(offer_kind),
            str(expires_at),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def prices_conflict(left: PriceComponents, right: PriceComponents) -> bool:
    """Whether two fresh observations of the same identity materially disagree.

    Any published-by-both component differing by more than CONFLICT_TOLERANCE means
    the truth is unknown; the caller must fail closed for that model rather than
    pick the cheaper story.
    """
    for name in _COMPONENT_FIELDS:
        a = getattr(left, name)
        b = getattr(right, name)
        if a is None or b is None:
            continue
        base = max(abs(float(a)), abs(float(b)), 1e-12)
        if abs(float(a) - float(b)) / base > CONFLICT_TOLERANCE:
            return True
    return False


def capabilities_match(observation: ModelObservation, required: tuple[str, ...]) -> bool:
    for cap in required:
        if cap == "tools" and not observation.supports_tools:
            return False
        if cap == "images" and not observation.supports_images:
            return False
    return True


def qualify_observation(
    before: ModelObservation,
    after: ModelObservation,
    *,
    now: str | datetime,
    preferences: RadarPreferences | None = None,
) -> RadarFinding | QualificationRefusal:
    """The one qualification decision. Pure; no storage, no clock reads.

    ``before`` is the operator's own previously recorded observation -- never a
    feed-supplied claim -- which is what makes a fabricated discount impossible:
    the attacker controls ``after`` but not the baseline it is measured against.
    """
    prefs = preferences or RadarPreferences()
    if before.identity() != after.identity():
        return QualificationRefusal(REFUSAL_NO_BASELINE, "identity changed; no comparable baseline")

    if not prefs.enabled:
        return QualificationRefusal("disabled", "radar disabled in preferences")
    if prefs.providers and after.provider_id not in prefs.providers:
        return QualificationRefusal(REFUSAL_PROVIDER_FILTERED, f"provider {after.provider_id} not watched")
    if prefs.lane == "local":
        return QualificationRefusal(REFUSAL_LANE_FILTERED, "lane=local suppresses cloud price news")

    if not evidence_is_fresh(after.evidence_fetched_at, now=now):
        return QualificationRefusal(REFUSAL_STALE_EVIDENCE, "after-evidence outside the freshness window")
    if after.expires_at and not offer_expires_in_future(after.expires_at, now=now):
        return QualificationRefusal(REFUSAL_EXPIRED_OFFER, "the offer already ended")

    if not capabilities_match(after, prefs.required_capabilities):
        missing = [
            cap
            for cap in prefs.required_capabilities
            if (cap == "tools" and not after.supports_tools) or (cap == "images" and not after.supports_images)
        ]
        return QualificationRefusal(REFUSAL_CAPABILITY_MISMATCH, f"model lacks: {', '.join(missing)}")

    # A price component that was published before and stopped being published is
    # an unknown after-price: unknown never counts as a reduction.
    for name in _COMPONENT_FIELDS:
        if getattr(before.prices, name) is not None and getattr(after.prices, name) is None:
            return QualificationRefusal(REFUSAL_UNKNOWN_AFTER_PRICING, f"{_component_words(name)} price stopped being published")

    after_free = after.prices.all_published_are_zero()
    before_free = before.prices.all_published_are_zero()

    if after_free:
        if before_free:
            return QualificationRefusal(REFUSAL_ALREADY_FREE_BEFORE, "was already free; nothing became free")
        if before.prices.input_usd_per_m is None or before.prices.output_usd_per_m is None:
            return QualificationRefusal(REFUSAL_UNKNOWN_AFTER_PRICING, "before-pricing incomplete; 'became free' is unfounded")
        if after.offer_kind not in _FREE_GROUNDING_KINDS:
            return QualificationRefusal(REFUSAL_OFFER_KIND_EXCLUDED, f"{after.offer_kind} cannot ground a free claim")
        if not after.limits_stated.strip():
            return QualificationRefusal(REFUSAL_LIMITS_NOT_STATED, "genuinely free REQUIRES clearly stated limits")
        return _build_finding(before, after, kind="new_free", now=now)

    if before_free:
        return QualificationRefusal(REFUSAL_PRICE_INCREASE, "free before, paid after: a rise, never radar news")

    # price_drop path: the load-bearing pair must be published on BOTH sides.
    if after.prices.input_usd_per_m is None or after.prices.output_usd_per_m is None:
        return QualificationRefusal(REFUSAL_UNKNOWN_AFTER_PRICING, "current input/output pricing unknown")
    if before.prices.input_usd_per_m is None or before.prices.output_usd_per_m is None:
        return QualificationRefusal(REFUSAL_NO_BASELINE, "baseline pricing incomplete")

    # Per-component verdicts. A single component that fails the bar (including a
    # RISE, however small) disqualifies the whole claim: no averaging, ever.
    reductions: dict[str, float] = {}
    worst = 1.0
    for name in _COMPONENT_FIELDS:
        b = getattr(before.prices, name)
        a = getattr(after.prices, name)
        if b is None or a is None:
            continue  # unpublished on one side: not a comparable component
        if a > b:
            return QualificationRefusal(REFUSAL_PRICE_INCREASE, f"{_component_words(name)} price rose")
        if float(b) <= 0.0:
            continue
        reduction = (float(b) - float(a)) / float(b)
        reductions[name] = round(reduction, 4)
        worst = min(worst, reduction)
    if not reductions:
        return QualificationRefusal(REFUSAL_BELOW_THRESHOLD, "no comparable published components")
    if worst < DROP_THRESHOLD:
        return QualificationRefusal(
            REFUSAL_BELOW_THRESHOLD,
            f"best component cut {worst:.0%} < required {DROP_THRESHOLD:.0%}",
        )

    if after.offer_kind not in _DROP_GROUNDING_KINDS:
        return QualificationRefusal(REFUSAL_OFFER_KIND_EXCLUDED, f"{after.offer_kind} cannot ground a price-drop claim")
    if after.offer_kind == OFFER_PROMOTION and not after.expires_at.strip():
        return QualificationRefusal(REFUSAL_PROMO_NO_EXPIRY, "a promotion with no stated end date is not a clearly stated offer")

    return _build_finding(before, after, kind="price_drop", reductions=reductions, now=now)


def _build_finding(
    before: ModelObservation,
    after: ModelObservation,
    *,
    kind: str,
    now: str | datetime,
    reductions: dict[str, float] | None = None,
) -> RadarFinding:
    before_pub = before.prices.published()
    after_pub = after.prices.published()
    if kind == "new_free":
        reductions = {
            name: round((float(b) - float(a)) / float(b), 4)
            for name, b in before_pub.items()
            for a in [after_pub.get(name)]
            if a is not None and float(b) > 0.0
        }
        parts = ", ".join(f"{_component_words(n)} {_money(b)}→{_money(0.0)}" for n, b in before_pub.items())
        offer_word = {
            OFFER_PERMANENT: "permanently free",
            OFFER_FREE_QUOTA: "free within a stated quota",
            OFFER_SUBSIDY: "free via a provider subsidy",
        }.get(after.offer_kind, "free")
        why = (
            f"Every published price went to zero ({parts}) — {offer_word}. "
            f"Stated limits: {after.limits_stated.strip()}. "
            f"Evidence fetched {after.evidence_fetched_at} from {after.evidence_url or 'the provider feed'}."
        )
    else:
        assert reductions is not None
        parts = ", ".join(
            f"{_component_words(n)} {_money(before_pub[n])}→{_money(after_pub[n])} per 1M (−{r:.1%})"
            for n, r in reductions.items()
        )
        unpublished = [
            _component_words(n)
            for n in _COMPONENT_FIELDS
            if n in before_pub and n not in after_pub
        ]
        offer_note = (
            f" Temporary promotion ending {after.expires_at}." if after.offer_kind == OFFER_PROMOTION else ""
        )
        why = (
            f"The same provider cut every published price by at least 50%: {parts}. "
            f"No published component rose.{'' if not unpublished else ' Not published now: ' + ', '.join(unpublished) + '.'}"
            f"{offer_note} Evidence fetched {after.evidence_fetched_at} from {after.evidence_url or 'the provider feed'}."
        )
    fingerprint = finding_fingerprint(
        provider_id=after.provider_id,
        model_id=after.model_id,
        kind=kind,
        prices=after.prices,
        offer_kind=after.offer_kind,
        expires_at=after.expires_at,
    )
    # The finding's clock is the QUALIFICATION moment the caller pinned, not wall
    # time: cadence math (min_interval_hours) compares issued findings against the
    # same clock the evidence was judged fresh on.
    created = now.isoformat() if isinstance(now, datetime) else str(now or "") or _utcnow_iso()
    return RadarFinding(
        fingerprint=fingerprint,
        kind=kind,
        provider_id=after.provider_id,
        model_id=after.model_id,
        display_name=after.display_name or after.model_id,
        before={n: float(v) for n, v in before_pub.items()},
        after={n: float(v) for n, v in after_pub.items()},
        reductions=reductions,
        offer_kind=after.offer_kind,
        limits_stated=after.limits_stated,
        limits_evidence_url=after.limits_evidence_url,
        expires_at=after.expires_at,
        context_length=after.context_length,
        supports_tools=after.supports_tools,
        supports_images=after.supports_images,
        privacy_terms=after.privacy_terms,
        evidence_url=after.evidence_url,
        evidence_fetched_at=after.evidence_fetched_at,
        before_evidence_fetched_at=before.evidence_fetched_at,
        why=why,
        created_at=created,
    )


__all__ = [
    "COMPONENT_CACHED",
    "COMPONENT_INPUT",
    "COMPONENT_OUTPUT",
    "CONFLICT_TOLERANCE",
    "DROP_THRESHOLD",
    "EVIDENCE_MAX_AGE_SECONDS",
    "OFFER_FREE_QUOTA",
    "OFFER_KINDS",
    "OFFER_PERMANENT",
    "OFFER_PROMOTION",
    "OFFER_SUBSIDY",
    "OFFER_TRIAL",
    "OFFER_UNKNOWN",
    "ModelObservation",
    "PriceComponents",
    "QualificationRefusal",
    "RadarFinding",
    "RadarPreferences",
    "capabilities_match",
    "evidence_is_fresh",
    "finding_fingerprint",
    "offer_expires_in_future",
    "prices_conflict",
    "qualify_observation",
    "replace",
]
