"""UsePod discovery: what is configured, what the live marketplace lists and costs, what this credential
can see, and which routes the owner approved -- each labelled with the evidence it rests on.

Two kinds of call, kept apart on purpose:

* :func:`discovery_view` is CACHE-ONLY. Settings may poll it; it never touches the network.
* :func:`refresh_discovery` and :func:`approve_model_route` are owner actions. They fetch the public
  marketplace feed and, when a token is configured, ask UsePod what that token sees
  (``/v1/models``, ``/balance``). Neither runs inference or spends.
* :func:`observe_balance` is the ONE account-bound balance observation door: cached while fresh,
  otherwise one read-only ``GET /proxy/<token>/balance``. Settings (Test/Refresh), the composer
  preflight and the dispatch reservation all obtain the balance through it, and the money law
  judges the observation it records. It never spends and is never an authority.

Credential-dependent results are stored under :func:`core.usepod.descriptor.credential_fingerprint`,
so rotating the token (or re-pointing the origin) orphans the old discovery instead of letting it
describe a credential it was never observed for -- and the file name reveals nothing about the token.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.normalized_provider_result import MalformedProviderResponseError
from core.usepod import descriptor, lane, pricing, routing, trust
from core.usepod.transport import (
    UsePodHttpTransport,
    UsePodTransportError,
    list_token_models,
    read_token_balance,
)

_DISCOVERY_SCHEMA = "vool.usepod.credential_discovery.v1"
_FINGERPRINT_RE = re.compile(r"^upc_[0-9a-f]{32}$")
#: Preflight and dispatch reuse a REPORTED balance observation up to this old before reading again.
#: Well inside the money law's acceptance window (core.usepod.money_law.LIQUIDITY_FRESH_SECONDS,
#: 900 s), so an observation that passes here is still fresh when the reservation is judged a moment
#: later. The value serves freshness, not cost: a balance read is one small GET.
BALANCE_REUSE_SECONDS = 120.0


@dataclass(frozen=True)
class ResolvedCredential:
    token: str = field(repr=False)
    origin: str
    fingerprint: str
    source: str
    token_shape: str


def _stored_token() -> tuple[str, str]:
    from core.cloud_providers import config_for

    cfg = config_for(descriptor.PROVIDER_ID)
    if cfg is None:
        return "", ""
    for name in cfg.env_names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, "environment"
    try:
        from core import credential_store

        value = str(credential_store.get_credential(cfg.credential_slot) or "").strip()
    except Exception:
        value = ""
    return (value, "credential_store") if value else ("", "")


class UsePodCredentialPairUnavailableError(RuntimeError):
    """The token/origin pair exists but cannot be used as ONE committed binding right now: a save or
    delete whose completion is unknown, bytes that disagree with the committed binding, or an
    unreadable store. Nothing may be sent with it until reconcile (restart or on demand) decides it."""

    code = "usepod_credential_pair_unresolved"


def resolve_credential(*, strict: bool = False) -> ResolvedCredential | None:
    """The configured token and the origin it is bound to, read as ONE pair, or None when no usable
    token is configured. Registers the token for redaction.

    The token and its origin commit together in one credential transaction, and they are read here by
    the credential owner's pair reader (``core.cloud_providers.resolved_provider_pair``), so a current
    token is never combined with an origin another save left behind. An environment token is bound to
    the provider table's origin: the environment never re-points a credential. A pair that cannot be
    admitted (unresolved, incoherent or unreadable) raises
    :class:`UsePodCredentialPairUnavailableError` when ``strict`` -- the dispatch path, which must name
    that refusal -- and reads as None for display callers."""
    from core.cloud_providers import resolved_provider_pair
    from core.credential_intelligence.store import StorageConflictError, StorageUnavailableError

    token, source = _stored_token()
    if not token:
        return None
    try:
        origin_raw, pair_token = resolved_provider_pair(descriptor.PROVIDER_ID)
    except (StorageConflictError, StorageUnavailableError) as exc:
        if strict:
            raise UsePodCredentialPairUnavailableError(str(exc)) from None
        return None
    if not pair_token:
        return None
    try:
        clean = descriptor.validate_token(pair_token)
        origin = descriptor.normalize_origin(origin_raw)
    except descriptor.UsePodConfigError:
        return None
    descriptor.remember_token_for_redaction(clean)
    return ResolvedCredential(
        token=clean,
        origin=origin,
        fingerprint=descriptor.credential_fingerprint(origin, clean),
        source=source,
        token_shape=descriptor.token_shape(clean),
    )


@dataclass(frozen=True)
class CredentialBindingPlan:
    """What Settings will store for a UsePod paste, decided before anything is written."""

    token: str = field(repr=False)
    origin: str
    origin_is_default: bool
    fingerprint: str
    token_shape: str
    surface_hint: str


def plan_credential_binding(value: str, *, explicit_origin: str = "") -> CredentialBindingPlan:
    """Split a pasted token or proxy base URL into (token, origin) -- or refuse with a stable code.

    The origin defaults to the provider table's. Any other origin -- a staging gateway, a loopback
    test service -- must be named explicitly in ``explicit_origin`` (the Settings base URL field),
    and a pasted URL must agree with it: a token never follows a pasted link to a host the owner did
    not also choose.
    """
    parsed = descriptor.parse_credential_input(value)
    chosen = descriptor.normalize_origin(explicit_origin) if str(explicit_origin or "").strip() else parsed.origin
    if parsed.origin_from_paste and parsed.origin != chosen:
        raise descriptor.UsePodConfigError("pasted_origin_differs_from_chosen_origin")
    if chosen != descriptor.DEFAULT_ORIGIN and not str(explicit_origin or "").strip():
        raise descriptor.UsePodConfigError("non_default_origin_must_be_chosen_explicitly")
    return CredentialBindingPlan(
        token=parsed.token,
        origin=chosen,
        origin_is_default=chosen == descriptor.DEFAULT_ORIGIN,
        fingerprint=descriptor.credential_fingerprint(chosen, parsed.token),
        token_shape=parsed.token_shape,
        surface_hint=parsed.surface_hint,
    )


def configured_origin() -> str:
    from core.cloud_providers import usepod_origin

    try:
        return descriptor.normalize_origin(usepod_origin())
    except descriptor.UsePodConfigError:
        return descriptor.DEFAULT_ORIGIN


class AccountlessOriginRefusedError(ValueError):
    """The accountless origin was not changed, and why (``code``, ``http_status``)."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def save_accountless_origin(value: str) -> str:
    """Point accountless x402 at an origin the owner chose explicitly, or back at the documented one (an empty value).

    Only while no UsePod token is stored: a stored token's origin is half of its credential pair and changes only
    through the credential transaction, so a token never inherits an origin chosen here and this door never re-points
    a token. Written under the credential writers' lock, and refused while a credential operation on either slot is
    unresolved. Returns the origin in effect afterwards."""
    from core import credential_store
    from core.cloud_providers import PROVIDERS
    from core.credential_intelligence._state_lock import credential_state_lock
    from core.credential_intelligence.store import JOURNAL_FILE
    from core.runtime_paths import active_data_dir

    text = str(value or "").strip()
    try:
        origin = descriptor.normalize_origin(text) if text else descriptor.DEFAULT_ORIGIN
    except descriptor.UsePodConfigError as exc:
        raise AccountlessOriginRefusedError(f"origin_invalid:{exc.code}", "that is not a UsePod origin; nothing was changed", http_status=400) from None
    cfg = PROVIDERS[descriptor.PROVIDER_ID]
    with credential_state_lock():
        token, _source = _stored_token()
        if token:
            raise AccountlessOriginRefusedError("origin_bound_to_stored_token", "a UsePod token is stored; its origin changes only when the token is saved again")
        journal = active_data_dir() / JOURNAL_FILE
        if journal.exists():
            try:
                rows = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise AccountlessOriginRefusedError("credential_pair_unresolved", "the credential journal is unreadable; nothing was changed") from None
            watched = {cfg.endpoint_slot, cfg.credential_slot}
            if any(
                isinstance(row, dict) and row.get("phase") in ("write_pending", "delete_pending") and str(row.get("slot") or "") in watched
                for row in (rows if isinstance(rows, list) else [])
            ):
                raise AccountlessOriginRefusedError("credential_pair_unresolved", "a UsePod credential operation is unresolved; nothing was changed")
        if origin == descriptor.DEFAULT_ORIGIN:
            credential_store.delete_credential(cfg.endpoint_slot)
        else:
            credential_store.store_credential(cfg.endpoint_slot, origin, label="UsePod origin (accountless x402)")
    return configured_origin()


def accountless_x402_ready() -> dict[str, Any] | None:
    """Whether UsePod can serve a paid call right now with no token: the owner chose accountless x402, no UsePod token is
    stored, and the wallet payment authority verifies at least one network. What a usable cloud lane is, for surfaces that
    otherwise only know "a key exists". None when not ready; the lane label, networks and authority when it is."""
    token, _source = _stored_token()
    if token:
        return None
    preference, _error = lane.load_lane_preference()
    if str(preference.transport_mode) != "x402":
        return None
    from core.usepod.transport import payment_authority

    authority = payment_authority()
    label = str(getattr(authority, "label", "") or "")
    if not label or label.startswith("unavailable:"):
        return None
    networks = tuple(str(item) for item in (getattr(authority, "networks", ()) or ()))
    if not networks:
        return None
    from core.cloud_providers import config_for

    cfg = config_for(descriptor.PROVIDER_ID)
    # the provider's short name ("UsePod"), not its table description, so the lane reads as one label
    short = (cfg.label if cfg else "UsePod").split(" (", 1)[0]
    return {"label": f"{short} (wallet x402)", "networks": list(networks), "authority": label}


def credential_status() -> dict[str, Any]:
    """Configuration facts only -- no network, no secret."""
    token, source = _stored_token()
    if not token:
        return {"configured": False, "state": "no_token"}
    try:
        credential = resolve_credential(strict=True)
    except UsePodCredentialPairUnavailableError:
        return {"configured": True, "state": "credential_pair_unresolved", "source": source}
    if credential is None:
        return {"configured": True, "state": "token_or_origin_unusable", "source": source}
    return {
        "configured": True,
        "state": "configured_not_verified_here",
        "source": credential.source,
        "origin": credential.origin,
        "origin_is_default": credential.origin == descriptor.DEFAULT_ORIGIN,
        "fingerprint": credential.fingerprint,
        "token_shape": credential.token_shape,
    }


def _live_balance(credential: ResolvedCredential, client: UsePodHttpTransport, moment: float) -> tuple[dict[str, Any], BaseException | None]:
    """ONE read-only ``GET /proxy/<token>/balance`` for this credential, as the observation record it
    yields: the provider's reported balance, or the typed failure (never a zero, never a guess) with
    the failure's own time. No inference, no spend."""
    try:
        observed = read_token_balance(transport=client, origin=credential.origin, token=credential.token, clock=lambda: moment)
    except UsePodTransportError as exc:
        return {"state": "unavailable", "error_code": exc.code, "http_status": exc.http_status, "observed_at": moment}, exc
    except MalformedProviderResponseError as exc:
        return {"state": "response_shape_unrecognized", "observed_at": moment}, exc
    return observed.as_dict(), None


def _store_balance(credential: ResolvedCredential, balance: dict[str, Any], moment: float) -> None:
    """Merge one balance observation into this credential's discovery record, keeping the model
    listing it holds. The record is the ONE place a balance observation lives: Settings, the composer
    preflight and the dispatch reservation all read it from here, under the credential fingerprint,
    so rotating the token orphans the observation with everything else discovery knew."""
    record = _read_credential_discovery(credential.fingerprint) or {
        "schema": _DISCOVERY_SCHEMA,
        "fingerprint": credential.fingerprint,
        "origin": credential.origin,
        "observed_at": moment,
    }
    record["balance"] = dict(balance)
    _write_credential_discovery(record)


def probe_credential(*, transport: UsePodHttpTransport | None = None) -> tuple[str, str, int | None]:
    """The connection verdict for Settings: an auth-gated balance read, no inference, no spend.

    The balance it reads is recorded as this credential's observation (the same record discovery
    and the dispatch reservation use), so a verified connection is also a fresh balance rather than
    a discarded one."""
    credential = resolve_credential()
    if credential is None:
        return "no_key", "no usable UsePod token configured", None
    moment = time.time()
    balance, failure = _live_balance(credential, transport or UsePodHttpTransport(), moment)
    _store_balance(credential, balance, moment)
    if isinstance(failure, UsePodTransportError):
        if failure.http_status in (401, 403):
            return "failed", "unauthorized", failure.http_status
        if failure.code == "remote_fetch_refused":
            return "failed", "remote fetch refused", None
        if failure.http_status is None:
            return "failed", "unreachable", None
        return "failed", f"unexpected status {failure.http_status}", failure.http_status
    if failure is not None:
        return "failed", "unexpected response shape", 200
    return "ok", "authorized", 200


def _discovery_path(fingerprint: str) -> Path:
    if not _FINGERPRINT_RE.fullmatch(str(fingerprint or "")):
        raise ValueError("invalid credential fingerprint")
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / "usepod" / f"discovery_{fingerprint}.json").resolve()


def _read_credential_discovery(fingerprint: str) -> dict[str, Any] | None:
    try:
        record = json.loads(_discovery_path(fingerprint).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(record, dict) or record.get("schema") != _DISCOVERY_SCHEMA or record.get("fingerprint") != fingerprint:
        return None
    return record


def _write_credential_discovery(record: dict[str, Any]) -> None:
    path = _discovery_path(str(record["fingerprint"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True, separators=(",", ":"))
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def refresh_discovery(*, transport: UsePodHttpTransport | None = None, now: float | None = None) -> dict[str, Any]:
    """Owner action: fetch the marketplace feed; with a token, read its model list and balance."""
    moment = float(time.time() if now is None else now)
    client = transport or UsePodHttpTransport()
    credential = resolve_credential()
    origin = credential.origin if credential else configured_origin()
    outcome: dict[str, Any] = {"refreshed_at": moment, "origin": origin}
    try:
        snapshot = pricing.fetch_marketplace_snapshot(origin=origin)
        outcome["marketplace"] = {"state": "fetched", **snapshot.provenance(moment)}
    except pricing.MarketplaceFeedError as exc:
        outcome["marketplace"] = {"state": "unavailable", "error_code": exc.code}
    if credential is None:
        outcome["credential"] = {"state": "no_token"}
        return outcome
    record: dict[str, Any] = {
        "schema": _DISCOVERY_SCHEMA,
        "fingerprint": credential.fingerprint,
        "origin": credential.origin,
        "observed_at": moment,
    }
    try:
        listing = list_token_models(transport=client, origin=credential.origin, token=credential.token, clock=lambda: moment)
        record["models"] = {"state": "reported", **listing.as_dict()}
    except UsePodTransportError as exc:
        record["models"] = {"state": "unavailable", "error_code": exc.code, "http_status": exc.http_status}
    except MalformedProviderResponseError:
        record["models"] = {"state": "response_shape_unrecognized"}
    record["balance"], _failure = _live_balance(credential, client, moment)
    _write_credential_discovery(record)
    outcome["credential"] = {"state": "observed", "fingerprint": credential.fingerprint}
    return outcome


def balance_observation(fingerprint: str, *, now: float | None = None) -> dict[str, Any]:
    """Cache-only: the balance this credential last reported, with its age now.

    ``state`` is ``reported`` only for a non-negative integer balance the provider answered;
    ``not_observed`` when no read was ever recorded; ``malformed`` for a record whose reported value
    is not an integer (a hand-edited file is not evidence); otherwise the recorded failure state
    (``unavailable`` with its ``error_code``/``http_status``, ``response_shape_unrecognized``,
    ``field_absent``, ``field_malformed``). ``age_seconds`` is None when no observation time exists."""
    moment = float(time.time() if now is None else now)
    record = _read_credential_discovery(fingerprint)
    balance = (record or {}).get("balance") if isinstance(record, dict) else None
    if not isinstance(balance, dict):
        return {"state": "not_observed", "fingerprint": fingerprint, "age_seconds": None, "observed_at": None}
    view = {key: value for key, value in balance.items() if key != "note"}
    view["fingerprint"] = fingerprint
    observed_at = balance.get("observed_at")
    timed = isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool)
    view["observed_at"] = float(observed_at) if timed else None
    view["age_seconds"] = max(0.0, moment - float(observed_at)) if timed else None
    if view.get("state") == "reported":
        amount = balance.get("usdc_balance_microunits")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or not timed:
            view["state"] = "malformed"
    return view


def observe_balance(
    *,
    transport: UsePodHttpTransport | None = None,
    now: float | None = None,
    max_age_seconds: float | None = BALANCE_REUSE_SECONDS,
    credential: ResolvedCredential | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """The ONE account-bound balance observation for the configured credential.

    Returns the cached observation when it is a reported balance at most ``max_age_seconds`` old
    (``evidence: cache``); otherwise performs ONE read-only ``GET /proxy/<token>/balance`` and
    records what came back -- the reported balance, or the typed failure -- under the credential's
    fingerprint (``evidence: live_read``). ``max_age_seconds=None`` always reads. When the live read
    fails and an older reported balance exists, it rides along as ``previous`` for display only.

    Never a spend, never an authority: the money law still judges freshness, held debits and
    sufficiency inside the reservation transaction. ``strict`` raises
    :class:`UsePodCredentialPairUnavailableError` for an unresolved token/origin pair (the dispatch
    and the owner endpoint name that refusal); display callers read it as ``no_token``.
    """
    moment = float(time.time() if now is None else now)
    resolved = credential or resolve_credential(strict=strict)
    if resolved is None:
        return {"state": "no_token", "evidence": "none", "age_seconds": None, "observed_at": None, "fingerprint": ""}
    cached = balance_observation(resolved.fingerprint, now=moment)
    age = cached.get("age_seconds")
    if cached.get("state") == "reported" and max_age_seconds is not None and isinstance(age, float) and age <= float(max_age_seconds):
        return {**cached, "evidence": "cache"}
    balance, _failure = _live_balance(resolved, transport or UsePodHttpTransport(), moment)
    _store_balance(resolved, balance, moment)
    fresh = balance_observation(resolved.fingerprint, now=moment)
    fresh["evidence"] = "live_read"
    if fresh.get("state") != "reported" and cached.get("state") == "reported":
        fresh["previous"] = {"usdc_balance_microunits": cached.get("usdc_balance_microunits"), "observed_at": cached.get("observed_at")}
    return fresh


def capability_matrix(record: dict[str, Any] | None, snapshot: pricing.MarketplaceSnapshot | None) -> list[dict[str, Any]]:
    """Per surface: the claim, its evidence type, the credential-free endpoint, when it was observed and until when.

    ``documented`` rests on the public docs; ``live_observed`` on a response this runtime received, with its
    observation time (and expiry when the observation has a TTL); ``unverified``, ``not_published``,
    ``not_in_this_provider`` and ``not_available`` say that no such observation exists.
    """
    models = dict((record or {}).get("models") or {})
    balance = dict((record or {}).get("balance") or {})
    observed_at = (record or {}).get("observed_at")
    models_live = models.get("state") == "reported"
    balance_live = balance.get("state") == "reported"
    feed_live = snapshot is not None and snapshot.evidence in {pricing.EVIDENCE_LIVE_FETCH, pricing.EVIDENCE_CACHE}

    def row(surface: str, capability: str, evidence: str, source: str, endpoint: str, *, observed: Any = None, expires: Any = None) -> dict[str, Any]:
        return {
            "surface": surface,
            "capability": capability,
            "evidence": evidence,
            "source": source,
            "endpoint": endpoint,
            "observed_at": observed,
            "expires_at": expires,
        }

    documented, unverified, live = descriptor.EVIDENCE_DOCUMENTED, descriptor.EVIDENCE_UNVERIFIED, descriptor.EVIDENCE_LIVE_OBSERVED
    return [
        row("prepaid_openai", "chat_completions_and_streaming", documented, "https://docs.usepod.ai/api/proxy/", "POST /proxy/{token}/v1/chat/completions"),
        row("prepaid_openai", "native_tool_calls", unverified, "mirrors the upstream API per route; not exercised live", "POST /proxy/{token}/v1/chat/completions"),
        row("prepaid_anthropic", "messages_and_streaming", documented, "https://docs.usepod.ai/api/proxy/", "POST /proxy/{token}/v1/messages"),
        row("prepaid_anthropic", "native_tool_use", unverified, "mirrors the upstream API per route; not exercised live", "POST /proxy/{token}/v1/messages"),
        row(
            "prepaid",
            "model_listing",
            live if models_live else documented,
            "this credential's /v1/models" if models_live else "https://docs.usepod.ai/api/proxy/",
            "GET /proxy/{token}/v1/models",
            observed=observed_at if models_live else None,
        ),
        row(
            "prepaid",
            "balance_read",
            live if balance_live else documented,
            "this credential's /balance" if balance_live else "https://docs.usepod.ai/api/deposit-on-chain/",
            "GET /proxy/{token}/balance",
            observed=(balance.get("observed_at") if balance.get("observed_at") is not None else observed_at) if balance_live else None,
        ),
        row("prepaid", "route_and_provider_headers", documented, "https://docs.usepod.ai/api/proxy/", "response headers X-Pod-Route, X-Pod-Provider-Id, X-Balance-Remaining"),
        row("prepaid", "price_ceiling_headers", documented, "https://docs.usepod.ai/using/spend-controls/", "request headers X-Pod-Max-Price-Input, X-Pod-Max-Price-Output"),
        row(
            "marketplace_feed",
            "per_model_route_prices",
            live if feed_live else unverified,
            "GET <origin>/v1/marketplace/models",
            "GET /v1/marketplace/models",
            observed=snapshot.fetched_at if feed_live else None,
            expires=(snapshot.fetched_at + snapshot.ttl_seconds) if feed_live else None,
        ),
        row("marketplace_feed", "cache_read_write_rates", "not_published", "the feed carries none", "GET /v1/marketplace/models"),
        row("x402", "quote_and_paid_retry", documented, "https://docs.usepod.ai/api/x402-payments/", "POST /proxy/x402/v1/chat/completions, POST /proxy/x402/v1/messages"),
        row("x402", "payment_signing", "not_in_this_provider", "wallet authority; unavailable until integrated", "PAYMENT-SIGNATURE request header"),
        row("any_route", "cryptographic_route_or_model_verification", "not_available", "https://docs.usepod.ai/marketplace/trust/", ""),
    ]


def authority_status() -> dict[str, Any]:
    """Which paid-dispatch dependencies are actually installed -- configuration facts, no network.

    Settings shows these so a lane that will refuse can say WHY before a turn is attempted. The
    prepaid monetary authority is the production money law once merged: "installed" says the law
    is wired, and the grant summary says whether any operator authorization actually exists -- a
    lane with the law but no grant still refuses every paid dispatch, and the UI must say that
    rather than reading "installed" as "ready to spend". Test doubles label themselves and are
    reported as installed-but-labelled.
    """
    from core.usepod import monetary, money_law
    from core.usepod.transport import payment_authority

    money = monetary.monetary_authority()
    payment = payment_authority()
    money_label = str(money.label)
    grants: list[dict[str, Any]] = []
    if money_label == money_law.AUTHORITY_LABEL:
        grants = money_law.money_law_status()["grants"]
    return {
        "monetary": {
            "installed": not money_label.startswith("unavailable:"),
            "label": money_label,
            "grants": grants,
        },
        "payment": {
            "installed": not str(payment.label).startswith("unavailable:"),
            "label": str(payment.label),
            "verified_networks": sorted(str(network) for network in (getattr(payment, "networks", ()) or ())),
        },
    }


def _spend_approval_state() -> dict[str, Any] | None:
    """The spend consent's truthful state (pending/approved/denied/expired/minted) for Settings.
    Cache-only, secret-free; an approved-unminted consent survives refresh and restart here."""
    from core.usepod.spend_approval import spend_consent_state

    return spend_consent_state()


def discovery_view(*, now: float | None = None, limit: int = 250, model_filter: str = "") -> dict[str, Any]:
    """Everything Settings needs to show, from caches only."""
    moment = float(time.time() if now is None else now)
    credential = resolve_credential()
    origin = credential.origin if credential else configured_origin()
    snapshot = pricing.load_cached_snapshot(origin=origin)
    record = _read_credential_discovery(credential.fingerprint) if credential else None
    state = routing.load_route_state()
    listed_ids = set(((record or {}).get("models") or {}).get("model_ids") or [])
    wanted = str(model_filter or "").strip().lower()
    rows: list[dict[str, Any]] = []
    if snapshot is not None:
        for model_id in sorted(snapshot.models):
            if wanted and wanted not in model_id.lower():
                continue
            model = snapshot.models[model_id]
            row = model.as_dict()
            row["listed_for_this_credential"] = (model_id in listed_ids) if record and record.get("models", {}).get("state") == "reported" else None
            bound = state.bounds.get(model_id)
            row["approved_route"] = bound.to_dict() if bound else None
            rows.append(row)
            if len(rows) >= max(1, int(limit)):
                break
    return {
        "provider": descriptor.PROVIDER_ID,
        "origin": origin,
        "credential": credential_status(),
        "marketplace": snapshot.provenance(moment) if snapshot else {"state": "no_cached_snapshot"},
        "models": rows,
        "credential_discovery": {key: value for key, value in (record or {}).items() if key in {"models", "balance", "observed_at"}} or None,
        "route_policy": state.policy.to_dict(),
        "route_store_error": state.error,
        "approved_routes": {model_id: bound.to_dict() for model_id, bound in state.bounds.items()},
        "lane": lane.load_lane_preference()[0].to_dict(),
        "authorities": authority_status(),
        "spend_approval": _spend_approval_state(),
        "capabilities": capability_matrix(record, snapshot),
        "route_trust": {name: profile.as_dict() for name, profile in trust.ROUTE_TRUST.items()},
        "documented_facts": [fact.as_dict() for fact in descriptor.DOCUMENTED_FACTS],
    }


def approve_model_route(
    model_id: str,
    *,
    max_input_usdc_per_million: str | None = None,
    max_output_usdc_per_million: str | None = None,
    now: float | None = None,
) -> routing.ApprovedRouteBound:
    """Owner action: bind the stored policy and the CURRENT prices into a stored route bound.

    ``max_*_usdc_per_million`` are the optional TWO-AXIS reviewed maxima from the price review
    (exact decimal strings, USDC per 1M tokens). They are converted through the pricing module's
    own exact decimal utility — never binary float money arithmetic — and both axes must be
    given together; one alone is refused. Identity is the model id itself; freshness is the
    FRESH-snapshot requirement every approval already enforces.
    """
    moment = float(time.time() if now is None else now)
    reviewed_input: int | None = None
    reviewed_output: int | None = None
    if max_input_usdc_per_million is not None or max_output_usdc_per_million is not None:
        if max_input_usdc_per_million is None or max_output_usdc_per_million is None:
            raise routing.RoutePolicyError("owner_review_requires_both_axes")
        if not isinstance(max_input_usdc_per_million, str) or not isinstance(max_output_usdc_per_million, str):
            raise routing.RoutePolicyError("ceiling_invalid", "reviewed maxima must be decimal strings")
        try:
            reviewed_input = pricing.usdc_decimal_to_microunits(max_input_usdc_per_million)
            reviewed_output = pricing.usdc_decimal_to_microunits(max_output_usdc_per_million)
        except pricing.PriceUnitError as exc:
            raise routing.RoutePolicyError("ceiling_invalid", f"reviewed maximum: {exc.code}") from exc
    credential = resolve_credential()
    origin = credential.origin if credential else configured_origin()
    result = pricing.current_snapshot(origin=origin, allow_network=True, now=moment)
    if result.snapshot is None:
        raise routing.RouteUnavailableError("price_feed_unavailable", evidence={"error_code": result.error_code})
    if result.state != pricing.SNAPSHOT_FRESH:
        raise routing.RouteUnavailableError("price_stale", evidence={"error_code": result.error_code})
    state = routing.load_route_state()
    if state.error:
        raise routing.RoutePolicyError(state.error)
    existing = state.bounds.get(model_id)
    if existing and existing.wait_poll_seconds:
        return existing  # Selecting a model never overwrites an explicit wait target.
    bound = routing.approve_route_bound(
        result.snapshot,
        model_id=model_id,
        policy=state.policy,
        now=moment,
        reviewed_max_input_microunits=reviewed_input,
        reviewed_max_output_microunits=reviewed_output,
    )
    routing.save_approved_bound(bound)
    return bound


__all__ = [
    "BALANCE_REUSE_SECONDS",
    "AccountlessOriginRefusedError",
    "ResolvedCredential",
    "accountless_x402_ready",
    "approve_model_route",
    "authority_status",
    "balance_observation",
    "capability_matrix",
    "configured_origin",
    "credential_status",
    "discovery_view",
    "observe_balance",
    "probe_credential",
    "refresh_discovery",
    "resolve_credential",
    "save_accountless_origin",
]
