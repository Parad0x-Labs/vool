"""Cloud connection state — the truth behind the chat-header indicator, per provider.

Four states, derived from real evidence and never guessed:

  * ``no_key``   — no cloud key for the provider (env or credential store).  gray
  * ``untested`` — a key exists but has not passed a live auth probe.        yellow
  * ``ok``       — the last auth probe succeeded for THIS key.               green
  * ``failed``   — the last auth probe failed (unauthorized/unreachable)     red

The auth probe is the ONE verification policy the credential-intelligence verifier runs
(``verify_provider_credential`` against the provider registry descriptor): the key rides the
placement the provider documents (Bearer, a named header such as Anthropic's ``X-Api-Key``, or a
segment of the URL path for a provider whose credential is part of the path, UsePod), an
operator-entered endpoint is asked keylessly first, and each refusal keeps its own name.
OpenRouter's ``/models`` is public (200 with no key), so its probe is ``/key``; direct
providers use ``/models`` (auth-gated on those). A provider whose key and endpoint commit as ONE
pair (the custom endpoint's base URL, UsePod's origin) is probed on the pair the one pair reader
admits. Everything provider-specific comes from ``core.cloud_providers`` and the descriptor
registry built on it.

The cached result is persisted to ``active_data_dir()/cloud_connection_state.json`` as a dict
KEYED BY PROVIDER, each entry holding a SHA-256 digest of the probed key AND its resolved endpoint
— never the key itself — so a restart keeps the state, while a key OR base-URL change degrades a
stale green to ``untested`` and one provider's green can never be shown for another. Reads are
cache-only; probes are rate-limited per provider. With no provider given, the ACTIVE provider is
resolved (legacy OpenRouter default).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from typing import Any

STATE_NO_KEY = "no_key"
STATE_UNTESTED = "untested"
STATE_OK = "ok"
STATE_FAILED = "failed"

_STATE_FILENAME = "cloud_connection_state.json"
_PROBE_MIN_INTERVAL_SECONDS = 5.0    # POST /api/cloud/test cannot hammer the provider
_PROBE_STALE_AFTER_SECONDS = 300.0   # ?probe=1 re-probes only past this age

# Legacy alias kept for callers/tests that reference the OpenRouter slot directly.
_CREDENTIAL_NAME = "llm.cloud.openrouter"

_last_probe_ts: dict[str, float] = {}  # in-process per-provider rate limiter for live probes


def _state_path():
    from core.runtime_paths import active_data_dir

    return active_data_dir() / _STATE_FILENAME


def _resolve_active_provider() -> str:
    """The single active cloud provider id (explicit policy → keyed slot → OpenRouter legacy)."""
    try:
        from core.cloud_escalation_policy import load_policy
        from core.cloud_providers import active_provider

        return active_provider(str(load_policy().provider or "")) or "openrouter"
    except Exception:
        return "openrouter"


def _resolve_key(provider: str | None = None) -> str:
    """The key a provider's lane would use: env first, then the encrypted credential store."""
    from core.cloud_providers import config_for

    cfg = config_for(provider or _resolve_active_provider())
    if cfg is None:
        return ""
    for name in cfg.env_names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        from core import credential_store

        return str(credential_store.get_credential(cfg.credential_slot) or "").strip()
    except Exception:
        return ""


def _base_url(provider: str) -> str:
    """The base URL a PROBE goes to. Named providers are pinned to their catalog URL — env
    base-URL overrides were removed from the probe path on 2026-09-02 (credential
    intelligence P0): this request carries the Bearer key, and an env var silently pointing
    it at an arbitrary host is an exfiltration route, not a configuration. The sanctioned
    user-supplied endpoints stay the ``custom`` provider's own resolution and the origin an owner
    saved explicitly beside a UsePod token (never a URL containing the token)."""
    from core.cloud_providers import config_for

    cfg = config_for(provider)
    if cfg is None:
        return ""
    if provider == "custom":
        from core.cloud_providers import custom_base_url

        return custom_base_url()
    if provider == "usepod":
        from core.cloud_providers import usepod_origin

        return usepod_origin()
    return cfg.base_url.rstrip("/")


def _identity_digest(provider: str, key: str) -> str:
    """Cache identity: a one-way digest of the key AND the resolved endpoint (no key material).

    Binding the endpoint means a custom-provider base-URL change (same key, new host) flips the
    identity, so a stale green cached for the old host degrades to ``untested`` and a re-probe is
    required — the pill can never show a connection verdict for an endpoint that is no longer live.
    """
    return hashlib.sha256((key + "\0" + _base_url(provider)).encode("utf-8")).hexdigest()


def _read_cached() -> dict[str, Any]:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _provider_cache(provider: str) -> dict[str, Any]:
    """This provider's cache entry. An old FLAT cache (top-level 'state', pre-multi-provider) is
    ignored — it degrades to untested until the next probe rather than showing a stale verdict."""
    entry = _read_cached().get(provider)
    return entry if isinstance(entry, dict) else {}


def _write_cached(record: dict[str, Any], provider: str | None = None) -> None:
    """Merge a probe result for one provider into the per-provider cache file, atomically.

    ``provider`` defaults to the active provider; kept optional so existing tests that call
    ``_write_cached({...})`` still write under the active (OpenRouter) provider.
    """
    prov = provider or _resolve_active_provider()
    with contextlib.suppress(Exception):
        path = _state_path()
        current = _read_cached()
        # If the file is an old flat record, start fresh (its top-level keys are not provider ids).
        if any(k in current for k in ("state", "key_digest", "checked_at")):
            current = {}
        current[prov] = record
        # A UNIQUE temp per writer (mkstemp), then atomic os.replace: the routes are served on a
        # threadpool, so a PID-only temp name would let two concurrent writers truncate the same
        # file and promote a corrupt JSON. mkstemp gives each writer its own fd/path.
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(current, sort_keys=True))
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)


def _label(provider: str) -> str:
    from core.cloud_providers import config_for

    cfg = config_for(provider)
    return cfg.label if cfg else provider


def connection_status(*, provider: str | None = None, probe: bool = False, now: float | None = None) -> dict[str, Any]:
    """The current connection state for a provider (default: the active one), from evidence only.

    Cache-only by default so the UI can poll freely. With ``probe=True`` a live auth probe runs
    when the cached result is missing, for a different key, or older than the stale threshold.
    Returns ``{state, detail, checked_at, http_status, provider, label}`` — never any key material.
    """
    ts = time.time() if now is None else now
    prov = provider or _resolve_active_provider()
    base = {"provider": prov, "label": _label(prov)}
    key = _resolve_key(prov)
    if not key:
        return {**base, "state": STATE_NO_KEY, "detail": "no cloud key configured", "checked_at": None, "http_status": None}

    cached = _provider_cache(prov)
    digest = _identity_digest(prov, key)
    cache_is_for_this_key = str(cached.get("key_digest") or "") == digest
    checked_at = float(cached.get("checked_at") or 0.0) if cache_is_for_this_key else 0.0
    fresh = cache_is_for_this_key and (ts - checked_at) < _PROBE_STALE_AFTER_SECONDS

    if probe and not fresh:
        return run_auth_probe(provider=prov, now=ts)

    if cache_is_for_this_key and cached.get("state") in (STATE_OK, STATE_FAILED):
        return {
            **base,
            "state": str(cached.get("state")),
            "detail": str(cached.get("detail") or ""),
            "checked_at": checked_at,
            "http_status": cached.get("http_status"),
        }
    # Key present but no probe evidence for THIS key (never probed, or the key changed).
    return {**base, "state": STATE_UNTESTED, "detail": "key present, not yet verified", "checked_at": None, "http_status": None}


def run_auth_probe(*, provider: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Live auth check for a provider. Rate-limited per provider; returns the status shape."""
    ts = time.time() if now is None else now
    prov = provider or _resolve_active_provider()
    base = {"provider": prov, "label": _label(prov)}
    key = _resolve_key(prov)
    if not key:
        return {**base, "state": STATE_NO_KEY, "detail": "no cloud key configured", "checked_at": None, "http_status": None}

    if (ts - _last_probe_ts.get(prov, 0.0)) < _PROBE_MIN_INTERVAL_SECONDS:
        # Too soon for another live call to THIS provider: serve the cached verdict.
        return connection_status(provider=prov, probe=False, now=ts)
    _last_probe_ts[prov] = ts

    state, detail, http_status = _probe_once(prov, key)
    record = {
        "state": state,
        "detail": detail,
        "http_status": http_status,
        "checked_at": ts,
        "key_digest": _identity_digest(prov, key),
    }
    _write_cached(record, provider=prov)
    return {**base, "state": state, "detail": detail, "checked_at": ts, "http_status": http_status}


def _probe_once(provider: str, key: str) -> tuple[str, str, int | None]:
    # The OWNING ENTRY POINT of the connection probe's network work (R2b1,
    # amended): the door grants nothing, so the probe authorizes itself here,
    # by name. Inside a turn the scope defers and the turn's policy governs.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("cloud.connection_probe"):
        return _probe_once_owned(provider, key)


def _probe_once_owned(provider: str, key: str) -> tuple[str, str, int | None]:
    """The Test button's live probe, run through the ONE verification policy Save uses
    (``verify_provider_credential`` against the provider registry descriptor), so the two doors
    can never disagree about the same answer or send the key two different ways.

    That buys the probe every law the verifier enforces: the key rides the placement the
    provider documents (Anthropic's Models API takes ``X-Api-Key``, not Bearer — a Bearer probe
    401'd every real Anthropic key; UsePod's token is a URL path segment), the header travels
    unredirected and a cross-origin redirect is refused, the body must be the provider's documented
    JSON shape, and an operator-entered
    endpoint (the custom OpenAI-compatible provider) is asked once WITHOUT the key first, so a
    public custom endpoint can no longer make Test certify an arbitrary key. The distinct
    refusal statuses (throttled, outage, missing endpoint, non-API page …) stay themselves —
    only ``invalid``/``unauthorized`` say the key was judged, and they say ``unauthorized`` in
    this state machine's four-state vocabulary while the detail keeps the verifier's word."""
    from core.cloud_providers import config_for, is_safe_key_transport
    from core.credential_intelligence.provider_registry import default_registry

    cfg = config_for(provider)
    if cfg is None:
        return STATE_FAILED, "unknown provider", None
    if cfg.endpoint_slot:
        # A key and its endpoint are ONE fact under the writers' transaction lock (the custom
        # endpoint's base URL, UsePod's origin): reading the two slots separately can observe a
        # mid-commit mixed pair, so the probe acts on the pair the one pair reader admits.
        from core.cloud_providers import resolved_provider_pair

        base, key = resolved_provider_pair(provider)
        if not base:
            return STATE_FAILED, "no base url configured", None
        # The SAME transport gate as every keyed request: the pair reader honours env
        # overrides, and an env base URL bypasses the save-time endpoint validation — so a
        # plaintext non-loopback base must be refused HERE, keeping the probe from becoming a
        # weaker cleartext key-exfiltration path than the completion adapter (restored 2026-09-15;
        # the r4 pair-reader change dropped this branch's gate and the transport suite's probe
        # control went red on the frozen r4 head with 'no base url configured').
        if not is_safe_key_transport(base):
            return STATE_FAILED, "insecure transport (key withheld)", None
        return _probe_verified(provider, key, base)
    base = _base_url(provider)
    if not base:
        return STATE_FAILED, "no base url configured", None
    # The request attaches the key, so it honours the SAME transport gate as the completion
    # adapter — a misconfigured http:// (non-loopback) base_url would make the probe a weaker
    # cleartext key-exfiltration path than a burst. The verifier applies the same gate again;
    # failing closed here keeps the refusal message the probe's own.
    if not is_safe_key_transport(base):
        return STATE_FAILED, "insecure transport (key withheld)", None
    descriptor = default_registry().get(provider)
    if descriptor is None:
        return STATE_FAILED, "unknown provider", None
    if descriptor.user_endpoint:
        descriptor = descriptor.with_base_url(base)
    return _probe_verified(provider, key, base)


def _probe_verified(provider: str, key: str, base: str) -> tuple[str, str, int | None]:
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.verification import (
        STATUS_INVALID,
        STATUS_UNAUTHORIZED,
        STATUS_VERIFIED,
        verify_provider_credential,
    )

    descriptor = default_registry().get(provider)
    if descriptor is None:
        return STATE_FAILED, "unknown provider", None
    if descriptor.user_endpoint or descriptor.endpoint_slot:
        descriptor = descriptor.with_base_url(base)
    outcome = verify_provider_credential(key, descriptor, scope="cloud.connection_probe")
    if outcome.status == STATUS_VERIFIED:
        return STATE_OK, "authorized", outcome.http_status
    if outcome.status in (STATUS_INVALID, STATUS_UNAUTHORIZED):
        return STATE_FAILED, "unauthorized", outcome.http_status
    return STATE_FAILED, outcome.status, outcome.http_status


def reset_probe_rate_limit_for_tests() -> None:
    _last_probe_ts.clear()


__all__ = [
    "STATE_FAILED",
    "STATE_NO_KEY",
    "STATE_OK",
    "STATE_UNTESTED",
    "connection_status",
    "reset_probe_rate_limit_for_tests",
    "run_auth_probe",
]
