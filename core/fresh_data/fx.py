"""Credential-free FX retrieval with an explicit evidence and staleness contract.

An FX number is valid only as a directed pair at an observed time.  This module therefore never
returns a bare float and never carries a fallback rate.  Callers receive either an available quote
with source/time/direction, or an explicit unavailable/conflicting/stale result.

The default adapter targets Frankfurter's public institutional-rate endpoint.  HTTP is injected at
the JSON boundary so tests never use the network and alternative local proxies can implement the
same contract without changing parsing, arithmetic, conductor, or rendering code.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

JsonFetcher = Callable[[str, float, Mapping[str, str]], Mapping[str, Any]]


class FxQuoteStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class FxQuote:
    base: str
    quote: str
    status: FxQuoteStatus
    rate: Decimal | None = None
    observed_at: str = ""
    retrieved_at: str = ""
    source: str = ""
    source_url: str = ""
    stale_after_seconds: int = 0
    failure_reason: str = ""
    compared_sources: tuple[str, ...] = ()

    @property
    def direction(self) -> str:
        return f"{self.base}_to_{self.quote}"

    @property
    def available(self) -> bool:
        return bool(
            self.status is FxQuoteStatus.AVAILABLE
            and self.rate is not None
            and self.source
            and self.observed_at
            and self.retrieved_at
        )

    def convert(self, amount: Decimal) -> Decimal:
        if not self.available or self.rate is None:
            raise ValueError(f"no usable {self.base}/{self.quote} rate: {self.status.value}")
        return Decimal(amount) * self.rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "quote": self.quote,
            "direction": self.direction,
            "status": self.status.value,
            "rate": str(self.rate) if self.rate is not None else None,
            "observed_at": self.observed_at,
            "retrieved_at": self.retrieved_at,
            "source": self.source,
            "source_url": self.source_url,
            "stale_after_seconds": self.stale_after_seconds,
            "failure_reason": self.failure_reason,
            "compared_sources": list(self.compared_sources),
        }


@dataclass(frozen=True)
class FxRetrievalReceipt:
    """Secret-free proof that an external FX observation was attempted for this turn."""

    retrieval_id: str
    base: str
    quote: str
    status: str
    source_providers: tuple[str, ...]
    source: str = ""
    rate: str = ""
    provider_attempt_count: int = 0
    started_at: str = ""
    completed_at: str = ""
    observed_at: str = ""
    retrieved_at: str = ""
    failure_class: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.fx_retrieval_receipt.v1",
            "retrieval_id": self.retrieval_id,
            "kind": "fx_quote",
            "base": self.base,
            "quote": self.quote,
            "direction": f"{self.base}_to_{self.quote}",
            "status": self.status,
            "source_providers": list(self.source_providers),
            "source": self.source,
            "rate": self.rate,
            "provider_attempt_count": self.provider_attempt_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "observed_at": self.observed_at,
            "retrieved_at": self.retrieved_at,
            "failure_class": self.failure_class,
        }


class FxProvider(Protocol):
    name: str

    def quote(self, base: str, quote: str, *, timeout_s: float = 8.0) -> FxQuote: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_json_fetcher(
    url: str,
    timeout_s: float,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    from core.remote_fetch_policy import open_remote_url

    # The built-in endpoint is fixed HTTPS; an operator-supplied endpoint is explicit runtime
    # configuration, not an argument derived from a model or user message. Fetched through the
    # ONE outbound door so the per-turn veto applies here too.
    with open_remote_url(str(url), headers=dict(headers), method="GET", timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("FX endpoint returned a non-object payload")
    return payload


def _normalize_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"invalid ISO currency code {value!r}")
    return code


def _iso_day(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return datetime.combine(parsed, time.min, tzinfo=timezone.utc)


def _receipt_timestamp(value: Any) -> str:
    """Admit only a real ISO timestamp into a public retrieval receipt."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.isoformat()


class FrankfurterFxProvider:
    """Public, credential-free institutional reference-rate adapter.

    It is suitable for ordinary current conversions, not tick-level trading or execution.  The
    daily observation date is retained separately from retrieval time so weekends and holidays are
    visible instead of being disguised as a live market tick.
    """

    name = "Frankfurter public institutional rates"

    def __init__(
        self,
        *,
        fetch_json: JsonFetcher | None = None,
        now: Callable[[], datetime] = _utc_now,
        stale_after_seconds: int = 4 * 24 * 60 * 60,
        endpoint: str = "https://api.frankfurter.dev/v2/rate",
    ) -> None:
        self._fetch_json = fetch_json or _default_json_fetcher
        self._now = now
        self._stale_after_seconds = max(1, int(stale_after_seconds))
        self._endpoint = endpoint.rstrip("?")

    def quote(self, base: str, quote: str, *, timeout_s: float = 8.0) -> FxQuote:
        base_code = _normalize_code(base)
        quote_code = _normalize_code(quote)
        retrieved = self._now().astimezone(timezone.utc)
        retrieved_at = retrieved.isoformat()
        if base_code == quote_code:
            return FxQuote(
                base=base_code,
                quote=quote_code,
                status=FxQuoteStatus.AVAILABLE,
                rate=Decimal("1"),
                observed_at=retrieved_at,
                retrieved_at=retrieved_at,
                source="currency identity",
                stale_after_seconds=self._stale_after_seconds,
            )
        url = f"{self._endpoint}/{urllib.parse.quote(base_code)}/{urllib.parse.quote(quote_code)}"
        try:
            payload = self._fetch_json(
                url,
                float(timeout_s),
                {"Accept": "application/json", "User-Agent": "VOOL-local/0.5 FX lookup"},
            )
            rates = payload.get("rates")
            # v2 returns one object with ``rate``.  Accepting the frozen v1 ``rates`` map as an
            # input compatibility shape costs no routing complexity and keeps self-hosted older
            # Frankfurter deployments usable through the same evidence contract.
            raw_rate = payload.get("rate")
            if raw_rate is None and isinstance(rates, Mapping):
                raw_rate = rates.get(quote_code)
            rate = Decimal(str(raw_rate))
            if not rate.is_finite() or rate <= 0:
                raise ValueError("rate must be a positive finite decimal")
        except Exception as exc:
            return FxQuote(
                base=base_code,
                quote=quote_code,
                status=FxQuoteStatus.UNAVAILABLE,
                retrieved_at=retrieved_at,
                source=self.name,
                source_url=url,
                stale_after_seconds=self._stale_after_seconds,
                failure_reason=f"{type(exc).__name__}: {exc}"[:200],
            )

        observed = _iso_day(payload.get("date"))
        observed_at = observed.isoformat() if observed is not None else ""
        stale = (
            observed is None
            or (retrieved - observed).total_seconds() > self._stale_after_seconds
        )
        return FxQuote(
            base=base_code,
            quote=quote_code,
            status=FxQuoteStatus.STALE if stale else FxQuoteStatus.AVAILABLE,
            rate=rate,
            observed_at=observed_at,
            retrieved_at=retrieved_at,
            source=self.name,
            source_url=url,
            stale_after_seconds=self._stale_after_seconds,
            failure_reason=(
                "reference rate is older than the configured freshness window" if stale else ""
            ),
        )


def resolve_fx_quote(
    base: str,
    quote: str,
    *,
    providers: Sequence[FxProvider],
    timeout_s: float = 8.0,
    max_relative_disagreement: Decimal = Decimal("0.02"),
) -> FxQuote:
    """Resolve providers without hiding conflict or promoting stale evidence.

    One available provider is enough.  When multiple providers are configured, all usable quotes
    must agree within the declared relative tolerance; otherwise no number is returned.
    """

    base_code = _normalize_code(base)
    quote_code = _normalize_code(quote)
    attempted: list[FxQuote] = []
    for provider in providers:
        try:
            result = provider.quote(base_code, quote_code, timeout_s=timeout_s)
            if not isinstance(result, FxQuote):
                raise TypeError("FX provider must return FxQuote")
            attempted.append(result)
        except Exception as exc:
            attempted.append(
                FxQuote(
                    base=base_code,
                    quote=quote_code,
                    status=FxQuoteStatus.UNAVAILABLE,
                    source=str(getattr(provider, "name", type(provider).__name__)),
                    retrieved_at=_utc_now().isoformat(),
                    failure_reason=f"{type(exc).__name__}: {exc}"[:200],
                )
            )
    usable = [item for item in attempted if item.available and item.rate is not None]
    names = tuple(item.source for item in attempted if item.source)
    if not usable:
        stale = next((item for item in attempted if item.status is FxQuoteStatus.STALE), None)
        if stale is not None:
            return FxQuote(**{**stale.__dict__, "compared_sources": names})
        reasons = "; ".join(item.failure_reason for item in attempted if item.failure_reason)
        return FxQuote(
            base=base_code,
            quote=quote_code,
            status=FxQuoteStatus.UNAVAILABLE,
            retrieved_at=_utc_now().isoformat(),
            source="unconfigured" if not attempted else "provider set",
            failure_reason=reasons or "no FX provider is configured",
            compared_sources=names,
        )
    reference = usable[0]
    for candidate in usable[1:]:
        assert reference.rate is not None and candidate.rate is not None
        relative = abs(candidate.rate - reference.rate) / reference.rate
        if relative > max_relative_disagreement:
            return FxQuote(
                base=base_code,
                quote=quote_code,
                status=FxQuoteStatus.CONFLICTING,
                retrieved_at=_utc_now().isoformat(),
                failure_reason=(
                    f"configured sources disagree by {relative:.4f}, above "
                    f"the {max_relative_disagreement:.4f} limit"
                ),
                compared_sources=names,
            )
    return FxQuote(**{**reference.__dict__, "compared_sources": names})


def retrieve_fx_quote(
    base: str,
    quote: str,
    *,
    providers: Sequence[FxProvider],
    source_context: dict[str, Any] | None,
    authorized: bool,
    timeout_s: float = 8.0,
    max_relative_disagreement: Decimal = Decimal("0.02"),
) -> FxQuote:
    """Execute one authorized external FX retrieval with counted, durable provenance.

    This is the authority boundary for direct FX consumers.  It emits the start before provider
    code can run, counts the remote attempt in the turn ledger, emits exactly one terminal event,
    and places the same typed receipt in ``source_context`` before an available rate can be used to
    author an answer.  A caller that has not admitted retrieval gets neither a provider call nor a
    misleading receipt.
    """

    base_code = _normalize_code(base)
    quote_code = _normalize_code(quote)
    if not authorized or base_code == quote_code:
        return unavailable_fx_quote(
            base_code,
            quote_code,
            "external FX retrieval was not authorized for this turn",
        )

    from core.remote_fetch_policy import note_remote_fetch_attempt, remote_fetch_forbidden

    if remote_fetch_forbidden():
        return unavailable_fx_quote(
            base_code,
            quote_code,
            "external FX retrieval was forbidden for this turn",
        )

    from core.runtime_task_events import emit_runtime_event
    from core.secret_redaction import redact_secrets

    retrieval_id = f"fx-retrieval-{uuid.uuid4().hex}"
    started_at = _utc_now().isoformat()
    source_providers = tuple(
        redact_secrets(str(getattr(provider, "name", type(provider).__name__) or ""))[:120]
        for provider in providers
    )
    common = {
        "schema": "vool.fx_retrieval_receipt.v1",
        "retrieval_id": retrieval_id,
        "kind": "fx_quote",
        "base": base_code,
        "quote": quote_code,
        "direction": f"{base_code}_to_{quote_code}",
        "source_providers": list(source_providers),
        "started_at": started_at,
    }
    emit_runtime_event(
        source_context,
        event_type="fx_retrieval_started",
        message=f"Started external FX retrieval for {base_code}/{quote_code}.",
        details={**common, "status": "started"},
    )

    class _CountedProvider:
        def __init__(self, provider: FxProvider) -> None:
            self._provider = provider
            self.name = str(getattr(provider, "name", type(provider).__name__) or "")

        def quote(self, requested_base: str, requested_quote: str, *, timeout_s: float = 8.0) -> FxQuote:
            # Increment immediately before EACH provider boundary. Counting after it returns would
            # make a timeout disappear from `web_calls`; counting once for a two-source conflict
            # would understate the external work that actually happened.
            # No URL here -- the provider owns its endpoint -- but the PROVIDER is the thing a
            # reader needs to tell a Frankfurter timeout from an ECB one, so it is named.
            note_remote_fetch_attempt(host=f"fx:{self.name}")
            return self._provider.quote(
                requested_base,
                requested_quote,
                timeout_s=timeout_s,
            )

    result = resolve_fx_quote(
        base_code,
        quote_code,
        providers=tuple(_CountedProvider(provider) for provider in providers),
        timeout_s=timeout_s,
        max_relative_disagreement=max_relative_disagreement,
    )
    completed_at = _utc_now().isoformat()
    failure_class = "" if result.available else f"fx_{result.status.value}"
    receipt = FxRetrievalReceipt(
        retrieval_id=retrieval_id,
        base=base_code,
        quote=quote_code,
        status=result.status.value,
        source_providers=source_providers,
        source=redact_secrets(str(result.source or ""))[:120],
        rate=str(result.rate) if result.available and result.rate is not None else "",
        provider_attempt_count=len(providers),
        started_at=started_at,
        completed_at=completed_at,
        observed_at=_receipt_timestamp(result.observed_at),
        retrieved_at=_receipt_timestamp(result.retrieved_at),
        failure_class=failure_class,
    )
    receipt_payload = receipt.to_dict()
    terminal_type = "fx_retrieval_completed" if result.available else "fx_retrieval_failed"
    if isinstance(source_context, dict):
        receipts = source_context.get("fresh_data_retrieval_receipts")
        if not isinstance(receipts, list):
            receipts = []
            source_context["fresh_data_retrieval_receipts"] = receipts
        receipts.append(receipt_payload)
    emit_runtime_event(
        source_context,
        event_type=terminal_type,
        message=(
            f"Completed external FX retrieval for {base_code}/{quote_code}."
            if result.available
            else f"External FX retrieval failed for {base_code}/{quote_code}."
        ),
        details=receipt_payload,
    )
    return result


def unavailable_fx_quote(
    base: str,
    quote: str,
    reason: str = "no FX provider is configured",
) -> FxQuote:
    return FxQuote(
        base=_normalize_code(base),
        quote=_normalize_code(quote),
        status=FxQuoteStatus.UNAVAILABLE,
        retrieved_at=_utc_now().isoformat(),
        source="unconfigured" if "no FX provider" in reason else "policy",
        failure_reason=reason,
    )


__all__ = [
    "FrankfurterFxProvider",
    "FxProvider",
    "FxQuote",
    "FxQuoteStatus",
    "FxRetrievalReceipt",
    "JsonFetcher",
    "resolve_fx_quote",
    "retrieve_fx_quote",
    "unavailable_fx_quote",
]
