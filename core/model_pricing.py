"""
core/model_pricing.py
=====================
What a paid cloud call costs, in USD, derived from token counts and the model's published
per-token price.

Why this exists
---------------
Settlement used to read one field: ``usage.cost``. Exactly one of VOOL's eight provider slots
returns it. OpenRouter reports what it charged the account; Anthropic, OpenAI, Groq, Google,
DeepSeek, Moonshot and a custom OpenAI-compatible endpoint all return token counts and nothing
else. Reading a field seven providers never send and defaulting it to zero made every one of
those calls settle at $0.00, so the USD ledger that enforces the per-call/task/day/month caps
never moved and the caps could not bind. Measured before this module existed: 30 owner-picked
Claude Sonnet 4.5 turns of ~12k in / ~900 out settled at $0.0000 against a $5.00 daily maximum,
while the same 30 turns bill $1.4850 at Anthropic's published $3/$15 per 1M rate.

So: **tokens times the published price**, and ``usage.cost`` only where a provider actually
supplies it.

Where the prices come from
--------------------------
* **OpenRouter** publishes a live per-token price for every model in ``/models``, which
  :mod:`core.openrouter_catalog` already parses. That is the source used here, read from the
  on-disk cache only — settlement runs on the turn's hot path and must not make a network call
  to price a response that already happened.
* **The direct providers** have no machine-readable catalog VOOL can rely on (their ``/models``
  endpoints are inconsistent and several need auth), so :data:`_DIRECT_PRICE_TABLE` below is an
  explicit table with a dated source per entry, covering the models VOOL actually offers in
  :mod:`core.cloud_model_catalog`.

The unknown case
----------------
**Decision: an unpriced model settles at a deliberately conservative ceiling. It is never
refused, and it is never free.**

The three options were: settle at $0, refuse the call up front, or settle at a ceiling.

* $0 is what this module exists to remove. It is the failure mode that reads as "this call was
  free" and lets an unbounded number of unpriced calls run under a cap that never moves.
* Refusing up front is safe for the wallet but wrong for the user: the price table is a dated
  snapshot and provider model lists move faster than it does, so a brand-new model id the owner
  explicitly picked and holds a key for would be refused for no reason other than this file
  being a week old. That converts a metering gap into an outage.
* A ceiling keeps the call working AND keeps the ledger moving. The error is deliberately
  biased against the user's wallet: an unknown model is charged at :func:`unknown_price_ceiling`,
  which is the highest per-token rate in the table below (Claude Opus 4.1, $15/$75 per 1M), so
  a real unpriced model can only ever be cheaper than what it settles at. The cap therefore trips
  early rather than late, and the estimate is flagged ``known=False`` so a surface can render it
  as an upper bound rather than a bill.

The ceiling is computed from the table rather than hardcoded, so adding a more expensive model
raises it automatically and it can never drop below a model VOOL knows the price of.

Retries
-------
A retried call is billed for every attempt. :func:`estimate_call_usd` and
:func:`settlement_usd` take ``attempts`` and multiply, matching
:meth:`core.openrouter_catalog.OpenRouterModel.estimate_cost`'s ``retries`` semantics.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("vool.model_pricing")

_PER_MILLION = 1_000_000.0

# Basis labels for a CostEstimate — what the number is made of.
BASIS_PROVIDER_REPORTED = "provider_reported"  # the provider told us what it charged
BASIS_PUBLISHED_PRICE = "published_price"  # tokens x a price we can point at a source for
BASIS_UNKNOWN_CEILING = "unknown_ceiling"  # tokens x the conservative ceiling; not a bill
BASIS_NO_USAGE = "no_usage"  # provider returned neither a cost nor any token count
# Tokens x a ceiling the OWNER approved for a dynamic marketplace (UsePod). An upper bound: the
# serving route bills its own listed price, at or below the ceiling, and no exact charge is reported.
BASIS_APPROVED_CEILING = "approved_price_ceiling"


# --- the direct-provider price table ---------------------------------------------------------
#
# USD per 1M tokens, (prompt, completion). Every entry carries the vendor's own published rate
# and the date it was read, because an undated price table is indistinguishable from a guess.
# Standard (non-cached, non-batch) rates only: caching discounts and batch pricing make a call
# CHEAPER, so quoting the standard rate keeps the bias against the wallet, which is the direction
# a spend cap must err in.
#
# Model ids are matched by longest prefix on a separator boundary, so a dated or suffixed id
# ("claude-sonnet-4-5-20250929", "gpt-4.1-mini-2025-04-14") prices off its base entry.
_DIRECT_PRICE_TABLE: dict[str, dict[str, tuple[float, float]]] = {
    # anthropic.com/pricing — read 2026-07-20.
    "anthropic": {
        "claude-opus-4-1": (15.00, 75.00),
        "claude-opus-4": (15.00, 75.00),
        "claude-sonnet-4-5": (3.00, 15.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-3-5-haiku": (0.80, 4.00),
    },
    # openai.com/api/pricing — read 2026-07-20.
    "openai": {
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "o4-mini": (1.10, 4.40),
        "o3": (2.00, 8.00),
    },
    # groq.com/pricing — read 2026-07-20.
    "groq": {
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "llama-3.1-8b-instant": (0.05, 0.08),
        "moonshotai/kimi-k2-instruct": (1.00, 3.00),
    },
    # ai.google.dev/gemini-api/docs/pricing — read 2026-07-20. Gemini 2.5 Pro is tiered by prompt
    # size; the standard (<=200k prompt) tier is here and the larger tier in _PROMPT_TIERS below.
    "google": {
        "gemini-2.5-pro": (1.25, 10.00),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "gemini-2.0-flash": (0.10, 0.40),
    },
    # api-docs.deepseek.com/quick_start/pricing — read 2026-07-20. Cache-MISS input rate.
    "deepseek": {
        "deepseek-chat": (0.27, 1.10),
        "deepseek-reasoner": (0.55, 2.19),
    },
    # platform.moonshot.ai pricing — read 2026-07-20.
    "moonshot": {
        "kimi-k2-0711-preview": (0.60, 2.50),
        "moonshot-v1-128k": (0.60, 2.50),
        "moonshot-v1-32k": (0.24, 0.24),
        "moonshot-v1-8k": (0.12, 0.12),
    },
    # "custom" is a user-supplied OpenAI-compatible base URL. VOOL cannot know its price by
    # definition, so it has no entries and every custom model settles at the ceiling.
    "custom": {},
}

# Models whose rate steps up above a prompt size: {(slot, table key): (threshold, prompt, completion)}.
# Quoting only the cheap tier would undercharge a long-context turn, and quoting only the dear one
# would charge double for the ordinary turn AND disagree with the price the model picker displays.
# ai.google.dev/gemini-api/docs/pricing — read 2026-07-20.
_PROMPT_TIERS: dict[tuple[str, str], tuple[int, float, float]] = {
    ("google", "gemini-2.5-pro"): (200_000, 2.50, 15.00),
}

# A dated snapshot is only honest if a reader can tell how old it is.
PRICE_TABLE_READ_ON = "2026-07-20"

_SEPARATORS = frozenset("-._:/@ ")
_DATE_SUFFIX_RE = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2}|latest|preview)$")


@dataclass(frozen=True)
class ModelPrice:
    """A per-token price for one model, plus where the number came from."""

    prompt_usd_per_token: float
    completion_usd_per_token: float
    request_usd: float = 0.0
    source: str = ""
    # False = this is the conservative ceiling, not a price anyone published for this model.
    known: bool = True
    # What the rate is, when it is neither a published price nor the table ceiling (e.g. an
    # owner-approved marketplace ceiling). Empty = derive from ``known``.
    basis: str = ""

    def usd_for(self, *, prompt_tokens: int, completion_tokens: int, attempts: int = 1) -> float:
        per_attempt = (
            max(0, int(prompt_tokens)) * float(self.prompt_usd_per_token)
            + max(0, int(completion_tokens)) * float(self.completion_usd_per_token)
            + float(self.request_usd)
        )
        return round(per_attempt * max(1, int(attempts)), 8)


@dataclass(frozen=True)
class CostEstimate:
    """What a call cost, and how confident that number is.

    ``known`` is False when the figure rests on the ceiling rather than a published price, so a
    surface can render it as an upper bound ("up to $x") instead of a bill.
    """

    usd: float
    known: bool
    basis: str
    source: str
    provider_id: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    attempts: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "usd": round(float(self.usd), 8),
            "known": bool(self.known),
            "basis": str(self.basis),
            "source": str(self.source),
            "provider_id": str(self.provider_id),
            "model_id": str(self.model_id),
            "prompt_tokens": int(self.prompt_tokens),
            "completion_tokens": int(self.completion_tokens),
            "attempts": int(self.attempts),
        }

    def describe(self) -> str:
        """One line a cap explanation or usage panel can print verbatim."""
        money = f"${self.usd:.6f}"
        if self.basis == BASIS_PROVIDER_REPORTED:
            return f"{money} charged by {self.provider_id} for {self.model_id}"
        if self.basis == BASIS_NO_USAGE:
            return f"Cost unknown — {self.provider_id} reported no usage for {self.model_id}"
        tokens = f"{self.prompt_tokens:,} in / {self.completion_tokens:,} out"
        attempts = f" x{self.attempts} attempts" if self.attempts > 1 else ""
        if self.basis == BASIS_APPROVED_CEILING:
            return f"up to {money} for {tokens}{attempts} at the approved price ceiling for {self.model_id}"
        if self.known:
            return f"{money} for {tokens}{attempts} at {self.model_id}'s published rate"
        return f"up to {money} for {tokens}{attempts} — no published price for {self.model_id}"


def unknown_price_ceiling() -> tuple[float, float]:
    """The (prompt, completion) USD-per-token rate an unpriced model is charged at.

    Derived from the most expensive entry in the table rather than hardcoded, so it can never
    sit below a model VOOL does know the price of, and adding a pricier model raises it.
    """
    rates = [rate for provider in _DIRECT_PRICE_TABLE.values() for rate in provider.values()]
    rates += [(tier[1], tier[2]) for tier in _PROMPT_TIERS.values()]
    prompt_per_m = max((rate[0] for rate in rates), default=15.0)
    completion_per_m = max((rate[1] for rate in rates), default=75.0)
    return (prompt_per_m / _PER_MILLION, completion_per_m / _PER_MILLION)


def _ceiling_price() -> ModelPrice:
    prompt, completion = unknown_price_ceiling()
    return ModelPrice(
        prompt_usd_per_token=prompt,
        completion_usd_per_token=completion,
        request_usd=0.0,
        source="conservative ceiling (highest rate in the price table); no published price found",
        known=False,
    )


def _lookup_candidates(model_id: str) -> tuple[str, ...]:
    """The forms of a model id to try against a price table, most literal first.

    Order matters. The id exactly as the provider names it comes first, so a table key that
    itself ends in a suffix ("kimi-k2-0711-preview") still matches; only then are a trailing
    release-date/channel suffix and a vendor prefix stripped, so "claude-sonnet-4-5-20250929"
    and "anthropic/claude-sonnet-4-5" both reach the "claude-sonnet-4-5" entry. Groq's
    "moonshotai/kimi-k2-instruct" is keyed WITH its prefix, which is why the prefixed form is
    always tried before the stripped one.
    """
    name = str(model_id or "").strip().lower()
    if not name:
        return ()
    forms: list[str] = []
    for candidate in (name, _DATE_SUFFIX_RE.sub("", name)):
        for form in (candidate, candidate.split("/", 1)[-1] if "/" in candidate else ""):
            if form and form not in forms:
                forms.append(form)
    return tuple(forms)


def _prefix_match(candidates: dict[str, tuple[float, float]], model_id: str) -> str | None:
    """Longest key that prefixes ``model_id`` on a separator boundary, else None.

    The boundary check is what stops "gpt-4.1" from pricing "gpt-4.15": a key only matches when
    the id continues with a separator or ends there.
    """
    best: str | None = None
    for key in candidates:
        if not model_id.startswith(key):
            continue
        tail = model_id[len(key) :]
        if tail and tail[0] not in _SEPARATORS:
            continue
        if best is None or len(key) > len(best):
            best = key
    return best


def _openrouter_price(model_id: str) -> ModelPrice | None:
    """The live-catalog price for an OpenRouter model, or None when the cache cannot answer.

    Cache-only: settlement happens on the turn's hot path, after the money is already spent, and
    must not block on a network fetch to name a number.
    """
    try:
        from core.openrouter_catalog import model_pricing_is_known, safe_all_models

        models, _age = safe_all_models(allow_network=False)
        wanted = str(model_id or "").strip().lower()
        for model in models:
            if str(model.model_id or "").strip().lower() != wanted:
                continue
            if not model_pricing_is_known(model):
                # The catalog carries the row but published no usable per-token pair. That is
                # unknown, not free — fall through to the ceiling.
                return None
            return ModelPrice(
                prompt_usd_per_token=float(model.prompt_usd_per_token or 0.0),
                completion_usd_per_token=float(model.completion_usd_per_token or 0.0),
                request_usd=float(model.request_usd or 0.0),
                source=f"openrouter /models catalog, fetched {model.fetched_at}",
                known=True,
            )
    except Exception as exc:  # pragma: no cover - defensive; pricing never breaks a turn
        logger.debug("openrouter price lookup failed for %s (%s)", model_id, exc)
    return None


def provider_slot(provider_hint: str) -> str:
    """The :data:`core.cloud_providers.PROVIDERS` slot a provider string names, or "".

    A manifest's ``provider_id`` is ``"{provider_name}:{model_name}"`` and its ``provider_name``
    for a BYOK lane is ``"{slot}-byok"``, so the string that reaches settlement looks like
    ``"anthropic-byok:claude-sonnet-4-5"``, not ``"anthropic"``. Pricing off the raw string would
    miss the table on every real manifest and silently fall through to the ceiling, so both forms
    are reduced to the slot here.
    """
    raw = str(provider_hint or "").strip().lower()
    if not raw:
        return ""
    candidate = raw.split(":", 1)[0]
    if candidate.endswith("-byok"):
        candidate = candidate[: -len("-byok")]
    try:
        from core.cloud_providers import PROVIDERS

        known = set(PROVIDERS)
    except Exception:  # pragma: no cover - defensive
        known = set(_DIRECT_PRICE_TABLE) | {"openrouter"}
    return candidate if candidate in known else ""


def provider_slot_for_manifest(manifest: Any) -> str:
    """The provider slot a model manifest bills against, or "" when it cannot be determined.

    Read in order of how directly each signal names the provider: the credential slot the lane
    was wired to, then the manifest's provider name, then the base URL's host.
    """
    runtime_config = getattr(manifest, "runtime_config", None)
    runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
    try:
        from core.cloud_providers import PROVIDERS

        credential_key = str(runtime_config.get("credential_key") or "").strip().lower()
        if credential_key:
            for slot, config in PROVIDERS.items():
                if str(getattr(config, "credential_slot", "") or "").strip().lower() == credential_key:
                    return slot
    except Exception:  # pragma: no cover - defensive
        pass

    for hint in (getattr(manifest, "provider_name", ""), getattr(manifest, "provider_id", "")):
        slot = provider_slot(hint)
        if slot:
            return slot

    base_url = str(runtime_config.get("base_url") or "").strip().lower()
    if base_url:
        try:
            from urllib.parse import urlparse

            from core.cloud_providers import PROVIDERS

            host = urlparse(base_url).netloc
            for slot, config in PROVIDERS.items():
                known_host = urlparse(str(getattr(config, "base_url", "") or "")).netloc
                if host and known_host and host == known_host:
                    return slot
        except Exception:  # pragma: no cover - defensive
            pass
    return ""


def _usepod_approved_price(model_id: str) -> ModelPrice | None:
    """The owner-approved UsePod price ceiling for this model, in USD per token, or None.

    An UPPER bound, not a published price: UsePod bills the serving route's own listing at or below
    the ceiling and reports no exact charge. The integer microunits are converted here only for the
    legacy USD ledger, with USDC counted 1:1 as USD -- stated in ``source``, never implied.
    """
    try:
        from core.usepod.routing import load_route_state

        state = load_route_state()
        bound = state.bounds.get(str(model_id or ""))
        if state.error or bound is None:
            return None
        return ModelPrice(
            prompt_usd_per_token=bound.max_input_microunits_per_million / (_PER_MILLION * _PER_MILLION),
            completion_usd_per_token=bound.max_output_microunits_per_million / (_PER_MILLION * _PER_MILLION),
            request_usd=0.0,
            source=(
                f"usepod approved price ceiling {bound.approval_id} ({bound.max_input_microunits_per_million}/"
                f"{bound.max_output_microunits_per_million} USDC microunits per 1M; USDC counted 1:1 as USD)"
            ),
            known=False,
            basis=BASIS_APPROVED_CEILING,
        )
    except Exception:  # pragma: no cover - defensive; pricing never breaks a turn
        return None


def reservation_price_refusal(provider_id: str, model_id: str) -> str:
    """Why a paid reservation for this provider/model must not be sized at all, or "".

    A dynamic marketplace (UsePod) has no stable published price, and the table ceiling can sit below
    a real listing. Without an owner-approved bound there is no defensible number to reserve against, so
    the reservation is refused with this code instead of sized on a guess.
    """
    if provider_slot(provider_id) != "usepod":
        return ""
    try:
        from core.usepod.routing import load_route_state

        state = load_route_state()
    except Exception:
        return "usepod_route_state_unavailable"
    if state.error:
        return state.error
    if str(model_id or "") not in state.bounds:
        return "usepod_route_not_approved"
    return ""


def lookup_model_price(provider_id: str, model_id: str, *, prompt_tokens: int = 0) -> ModelPrice:
    """The per-token price for a model. Never returns None and never returns a free price for a
    model whose cost is unknown — an unpriced model comes back at the ceiling with ``known=False``.

    ``prompt_tokens`` selects the tier for a model whose published rate steps up with prompt size
    (see :data:`_PROMPT_TIERS`); it is ignored for every flat-rate model.
    """
    provider = provider_slot(provider_id)
    if provider == "openrouter":
        return _openrouter_price(model_id) or _ceiling_price()
    if provider == "usepod":
        return _usepod_approved_price(model_id) or _ceiling_price()

    table = _DIRECT_PRICE_TABLE.get(provider)
    if table:
        for candidate in _lookup_candidates(model_id):
            key = _prefix_match(table, candidate)
            if key is not None:
                prompt_per_m, completion_per_m = table[key]
                tier = _PROMPT_TIERS.get((provider, key))
                if tier is not None and max(0, int(prompt_tokens)) > tier[0]:
                    prompt_per_m, completion_per_m = tier[1], tier[2]
                return ModelPrice(
                    prompt_usd_per_token=prompt_per_m / _PER_MILLION,
                    completion_usd_per_token=completion_per_m / _PER_MILLION,
                    request_usd=0.0,
                    source=f"{provider} published price for {key} (${prompt_per_m}/${completion_per_m} per 1M, read {PRICE_TABLE_READ_ON})",
                    known=True,
                )
    return _ceiling_price()


def estimate_call_usd(
    *,
    provider_id: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    attempts: int = 1,
) -> CostEstimate:
    """What ``attempts`` calls of this shape cost, from tokens and the published price.

    ``attempts`` is the total number of calls billed under this reservation, not the retry count:
    a call that failed once and succeeded on the second try is ``attempts=2``, because the
    provider bills both.
    """
    price = lookup_model_price(provider_id, model_id, prompt_tokens=prompt_tokens)
    usd = price.usd_for(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, attempts=attempts)
    return CostEstimate(
        usd=usd,
        known=price.known,
        basis=price.basis or (BASIS_PUBLISHED_PRICE if price.known else BASIS_UNKNOWN_CEILING),
        source=price.source,
        provider_id=str(provider_id or ""),
        model_id=str(model_id or ""),
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        attempts=max(1, int(attempts)),
    )


def usage_tokens(usage: Any) -> tuple[int, int]:
    """(prompt, completion) token counts out of a provider usage block, across dialects.

    Ollama says ``prompt_eval_count``/``eval_count``, OpenAI-compatible providers say
    ``prompt_tokens``/``completion_tokens``, Anthropic says ``input_tokens``/``output_tokens``.
    """
    try:
        block = dict(usage or {})
    except Exception:
        return (0, 0)

    def _first(*keys: str) -> int:
        for key in keys:
            value = block.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        return 0

    return (
        _first("prompt_eval_count", "prompt_tokens", "input_tokens"),
        _first("eval_count", "completion_tokens", "output_tokens"),
    )


def provider_reported_usd(usage: Any) -> float | None:
    """The out-of-pocket USD the provider itself reported, or None when it reported none.

    Only OpenRouter populates ``usage.cost``, and there it is authoritative: it is what the
    account was actually charged, including the provider's own margin, which no per-token table
    can reproduce. ``cost_details.upstream_inference_cost`` is billed SEPARATELY to the user's own
    upstream account and only for a BYOK-upstream key, so it is added on top only when the
    response is flagged ``is_byok`` — otherwise ``usage.cost`` alone is the out-of-pocket and
    adding a stray upstream value would double count.
    """
    try:
        block = dict(usage or {})
    except Exception:
        return None
    cost = block.get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        return None
    total = float(cost)
    details = block.get("cost_details")
    details = details if isinstance(details, dict) else {}
    if bool(block.get("is_byok") or details.get("is_byok")):
        upstream = details.get("upstream_inference_cost")
        if isinstance(upstream, (int, float)) and upstream >= 0:
            total += float(upstream)
    return max(0.0, total)


def settlement_usd(
    *,
    provider_id: str,
    model_id: str,
    usage: Any,
    attempts: int = 1,
) -> CostEstimate:
    """What to settle a reservation at for a response with this usage block.

    Uses the provider's own reported cost when it supplies one (OpenRouter), and otherwise prices
    the reported token counts at the model's published rate. A model with no published price
    settles at the ceiling, never at $0 — see the module docstring for why that is the choice.
    """
    reported = provider_reported_usd(usage)
    prompt_tokens, completion_tokens = usage_tokens(usage)
    attempts = max(1, int(attempts))
    if reported is not None:
        return CostEstimate(
            usd=round(reported * attempts, 8),
            known=True,
            basis=BASIS_PROVIDER_REPORTED,
            source=f"{provider_id} reported usage.cost",
            provider_id=str(provider_id or ""),
            model_id=str(model_id or ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            attempts=attempts,
        )
    if prompt_tokens == 0 and completion_tokens == 0:
        # No cost field and no token counts: there is nothing to price. Returning the ceiling
        # here would charge for a response that may not exist (an empty/failed body), so this
        # settles at zero and says so in the basis rather than pretending to a number.
        return CostEstimate(
            usd=0.0,
            known=False,
            basis=BASIS_NO_USAGE,
            source=f"{provider_id} reported neither a cost nor any token count",
            provider_id=str(provider_id or ""),
            model_id=str(model_id or ""),
            prompt_tokens=0,
            completion_tokens=0,
            attempts=attempts,
        )
    return estimate_call_usd(
        provider_id=provider_id,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        attempts=attempts,
    )


__all__ = [
    "BASIS_APPROVED_CEILING",
    "BASIS_NO_USAGE",
    "BASIS_PROVIDER_REPORTED",
    "BASIS_PUBLISHED_PRICE",
    "BASIS_UNKNOWN_CEILING",
    "PRICE_TABLE_READ_ON",
    "CostEstimate",
    "ModelPrice",
    "estimate_call_usd",
    "lookup_model_price",
    "provider_reported_usd",
    "reservation_price_refusal",
    "settlement_usd",
    "unknown_price_ceiling",
    "usage_tokens",
]
