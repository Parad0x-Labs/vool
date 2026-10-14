"""Absolute provider-call deadlines owned by the turn that requested the call.

Provider manifests describe the longest call a provider can normally tolerate.  They do not own
the enclosing turn's wall clock.  A conductor turn, for example, has one deadline covering its
planner and every generated node; letting a manifest's 180 second socket timeout escape a 45
second conductor abandons a live request thread after the answer has already been returned.

This module carries the enclosing deadline through the existing ``source_context``/``ModelRequest``
copy chain.  The value is monotonic and underscore-prefixed: it is process-local control state,
never prompt content or a caller-authorized policy field.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core import runtime_active_clock

PROVIDER_DEADLINE_KEY = "_provider_call_deadline_monotonic"
PROVIDER_DEADLINE_REASON_KEY = "_provider_call_deadline_reason"
#: Per-call transport-timeout floor, set by a RETRY that widened the output ceiling. The value
#: is process-local control state like the deadline key: a wider ceiling means MORE tokens must
#: transfer, so the transport timeout that sized the first attempt must not also size the retry
#: (measured 2026-09-18: a +2048-token retry inherited the 180s transport timeout and was killed
#: by the transfer watchdog while the enclosing deadline still had room). Still clamped by the
#: enclosing absolute deadline -- an override may widen transport, never turn authority.
PROVIDER_TRANSPORT_TIMEOUT_KEY = "_provider_transport_timeout_seconds"
#: Marks a manifest copy whose ``runtime_config.timeout_seconds`` a caller already resolved against
#: its own wall-clock budget (the router's fallback-budget cap writes the remaining window there).
#: That value is real seconds left, not a ceiling measured on a GPU host, so
#: :func:`local_manifest_call_timeout_seconds` must not scale it a second time. Process-local
#: control state on a transient copy, like the keys above; never read from a stored manifest.
BOUND_CALL_TIMEOUT_KEY = "_timeout_seconds_bound"

# Leave enough room for requests to raise, the router to classify and emit ``model.call_failed``,
# the conductor worker to publish its node outcome, and the API to emit the terminal turn trace.
DEFAULT_CLEANUP_MARGIN_SECONDS = 3.0


class ProviderCallDeadlineExceededError(TimeoutError):
    """The enclosing runtime deadline expired before the provider call could finish."""


# Compatibility alias for integrations that imported the pre-release name.
ProviderCallDeadlineExceeded = ProviderCallDeadlineExceededError


def bind_provider_deadline(
    source_context: Mapping[str, Any] | None,
    *,
    turn_deadline_monotonic: float,
    cleanup_margin_seconds: float = DEFAULT_CLEANUP_MARGIN_SECONDS,
    reason: str = "enclosing turn deadline",
) -> dict[str, Any]:
    """Return a context copy whose provider deadline is earlier than the turn deadline.

    Nested owners may only shorten an existing deadline.  A helper can therefore participate in a
    parent turn without accidentally extending authority the parent already bounded.
    """

    context = dict(source_context or {})
    deadline = float(turn_deadline_monotonic) - max(0.0, float(cleanup_margin_seconds))
    existing = _float_or_zero(context.get(PROVIDER_DEADLINE_KEY))
    if existing > 0:
        deadline = min(deadline, existing)
    context[PROVIDER_DEADLINE_KEY] = deadline
    context[PROVIDER_DEADLINE_REASON_KEY] = str(reason or "enclosing turn deadline")
    return context


def copy_deadline_to_request_metadata(
    metadata: Mapping[str, Any] | None,
    source_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Copy only the typed deadline controls into a request's private metadata."""

    copied = dict(metadata or {})
    context = source_context or {}
    deadline = _float_or_zero(context.get(PROVIDER_DEADLINE_KEY))
    if deadline > 0:
        existing = _float_or_zero(copied.get(PROVIDER_DEADLINE_KEY))
        copied[PROVIDER_DEADLINE_KEY] = min(deadline, existing) if existing > 0 else deadline
        copied[PROVIDER_DEADLINE_REASON_KEY] = str(
            context.get(PROVIDER_DEADLINE_REASON_KEY) or "enclosing turn deadline"
        )
    return copied


def deadline_expired(source: Mapping[str, Any] | Any | None) -> bool:
    """Whether a context or ``ModelRequest`` has crossed its absolute provider deadline."""

    deadline = _deadline_from(source)
    return deadline > 0 and runtime_active_clock.monotonic() >= deadline


def effective_timeout_seconds(request: Any, configured_seconds: float) -> float:
    """Clamp a provider transport timeout to the enclosing absolute deadline.

    Raises before network I/O when no time remains.  The caller has already emitted
    ``model.call_started`` and entered the provider ledger by this point, so the router's normal
    exception path turns this into the matching typed terminal failure receipt.
    """

    configured = max(0.001, float(configured_seconds or 0.001))
    transport_override = _transport_override_from(request)
    if transport_override > 0:
        configured = max(configured, transport_override)
    deadline = _deadline_from(request)
    if deadline <= 0:
        return configured
    remaining = deadline - runtime_active_clock.monotonic()
    if remaining <= 0:
        reason = _reason_from(request)
        raise ProviderCallDeadlineExceededError(
            f"provider timeout: {reason} expired before invocation"
        )
    return max(0.001, min(configured, remaining))


def local_manifest_call_timeout_seconds(manifest: Any, configured_seconds: float) -> float:
    """A provider manifest's per-call transport ceiling, scaled once on a CPU-only machine.

    The manifests' ``timeout_seconds`` (180 s throughout ``core/runtime_provider_defaults.py``,
    30 s in the adapter when a manifest carries none) was sized on GPU hosts. On a machine with
    no usable GPU the same local model runs several times slower (``core.local_compute_budget``
    holds the Intel measurement), and the enclosing turn deadlines are already scaled at their
    call sites -- but this per-call ceiling stayed unscaled, so a local call was cut at 180 s
    while its enclosing CPU-only turn still had 117 s of room: the reported "local recall can
    time out" while the turn budget was never spent. Only a manifest that runs on this machine
    is scaled, decided by the routing layer's own locality rule
    (``core.local_model_policy.manifest_is_local``: local source type, loopback endpoint, or
    ``deployment_class: local``) -- never by a provider-name list, and never a cloud provider.
    A locality that cannot be read keeps the configured value, the same fail-soft direction
    ``core.local_compute_budget`` itself takes. An explicit transport override on the request
    (:data:`PROVIDER_TRANSPORT_TIMEOUT_KEY`) still wins inside :func:`effective_timeout_seconds`,
    and the bound absolute deadline still clamps the result: scaling may spend a turn's own
    budget, never extend it. A value a caller already bounded to its own budget
    (:data:`BOUND_CALL_TIMEOUT_KEY`) is returned unscaled.
    """
    value = max(0.001, float(configured_seconds or 0.001))
    runtime_config = getattr(manifest, "runtime_config", None)
    if isinstance(runtime_config, Mapping) and runtime_config.get(BOUND_CALL_TIMEOUT_KEY):
        # Already the caller's remaining budget in real seconds: scaling it again would let the
        # call outlive the budget that bounded it (4 x the router's cap on a CPU-only machine).
        return value
    try:
        from core.local_model_policy import manifest_is_local

        if manifest_is_local(manifest):
            from core.local_compute_budget import local_compute_seconds

            return local_compute_seconds(value)
    except Exception:
        pass
    return value


def _deadline_from(source: Mapping[str, Any] | Any | None) -> float:
    if isinstance(source, Mapping):
        return _float_or_zero(source.get(PROVIDER_DEADLINE_KEY))
    metadata = getattr(source, "metadata", None)
    if isinstance(metadata, Mapping):
        return _float_or_zero(metadata.get(PROVIDER_DEADLINE_KEY))
    return 0.0


def _transport_override_from(source: Mapping[str, Any] | Any | None) -> float:
    if isinstance(source, Mapping):
        return _float_or_zero(source.get(PROVIDER_TRANSPORT_TIMEOUT_KEY))
    metadata = getattr(source, "metadata", None)
    if isinstance(metadata, Mapping):
        return _float_or_zero(metadata.get(PROVIDER_TRANSPORT_TIMEOUT_KEY))
    return 0.0


def _reason_from(source: Mapping[str, Any] | Any | None) -> str:
    if isinstance(source, Mapping):
        value = source.get(PROVIDER_DEADLINE_REASON_KEY)
    else:
        metadata = getattr(source, "metadata", None)
        value = metadata.get(PROVIDER_DEADLINE_REASON_KEY) if isinstance(metadata, Mapping) else ""
    return str(value or "enclosing turn deadline")


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "BOUND_CALL_TIMEOUT_KEY",
    "DEFAULT_CLEANUP_MARGIN_SECONDS",
    "PROVIDER_DEADLINE_KEY",
    "PROVIDER_DEADLINE_REASON_KEY",
    "PROVIDER_TRANSPORT_TIMEOUT_KEY",
    "ProviderCallDeadlineExceeded",
    "ProviderCallDeadlineExceededError",
    "bind_provider_deadline",
    "copy_deadline_to_request_metadata",
    "deadline_expired",
    "effective_timeout_seconds",
    "local_manifest_call_timeout_seconds",
]
