"""The mapping law: ONE failure maps ONCE, at its owning boundary; wrappers preserve it.

The defect this module exists to end is wrapper drift. A failure is born deep in a
transport, gets caught by a retry loop, wrapped in ``RuntimeError("turn failed")``,
caught again by a lane that prefixes its own sentence, and by the time a surface asks
"what happened?" the answer is the OUTER wrapper's prose -- which classifies as
something else entirely. Three rules close that:

* the OWNING BOUNDARY classifies, by exception TYPE and typed attributes, never by
  prose -- :func:`map_exception` is a pure function of the exception, so the same
  failure maps to the same record every time;
* a mapped failure travels as a :class:`FaultError` carrying its record, and
  :func:`map_exception` on anything in that exception's cause chain RETURNS THE
  EXISTING RECORD -- a wrapper cannot re-classify what a boundary already classified;
* what nobody classified is :data:`FAULT_UNKNOWN` -- typed, honest, never retryable,
  its cause chain redacted -- instead of a guessed code or a leaked ``str(exc)``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from core.faults.catalog import (
    FAULT_CANCELLED,
    FAULT_PERMISSION_DENIED,
    FAULT_PROVIDER_EXHAUSTED,
    FAULT_PROVIDER_UNAVAILABLE,
    FAULT_TIMEOUT,
    FAULT_UNKNOWN,
)
from core.faults.records import FaultRecord, cause_chain_for


class FaultError(Exception):
    """An exception carrying its already-mapped fault record.

    Owning boundaries raise (or wrap) with this so every generic handler upstream can
    preserve the mapping by re-raising -- or by handing the eventual exception back to
    :func:`map_exception`, which finds the record in the cause chain.
    """

    def __init__(self, record: FaultRecord) -> None:
        super().__init__(record.user_message)
        self.record = record


def _find_existing_record(exc: BaseException | None, *, depth: int = 8) -> FaultRecord | None:
    """The record an earlier boundary already mapped, if one rides this exception's chain."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and len(seen) < depth and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, FaultError):
            return current.record
        current = current.__cause__ or current.__context__
    return None


def _classify(exc: BaseException) -> str:
    """Exception type -> fault code. Typed attributes only; prose is never read."""
    if isinstance(exc, asyncio.CancelledError):
        # A cancellation is not a failure: it is the user (or the runtime) saying stop.
        return FAULT_CANCELLED
    if isinstance(exc, (PermissionError, IsADirectoryError)):
        return FAULT_PERMISSION_DENIED
    if isinstance(exc, (TimeoutError,)):
        # socket.timeout IS TimeoutError since 3.10; asyncio.TimeoutError since 3.11.
        return FAULT_TIMEOUT
    return FAULT_UNKNOWN


def map_exception(
    exc: BaseException,
    *,
    authority: str,
    turn_key: str = "",
    attempt_id: str = "",
    effect_id: str = "",
    session_id: str = "",
    evidence_refs: tuple[str, ...] = (),
    context: dict[str, Any] | None = None,
    dedupe: str = "",
) -> FaultRecord:
    """Map one exception to its fault record, preserving any earlier boundary's mapping.

    Pure and stable: the same exception with the same identity arguments yields the
    same ``fault_id`` every time, which is what makes recording idempotent.
    """
    existing = _find_existing_record(exc)
    if existing is not None:
        return existing
    code = _classify(exc)
    return FaultRecord.for_code(
        code,
        authority=authority,
        turn_key=turn_key,
        attempt_id=attempt_id,
        effect_id=effect_id,
        session_id=session_id,
        evidence_refs=evidence_refs,
        context=context,
        cause_chain=cause_chain_for(exc),
        dedupe=dedupe,
    )


_EXHAUSTED_MARKERS = ("quota", "rate", "exhaust", "429", "limit")
_TIMEOUT_MARKERS = ("timeout", "timed_out")


def fault_code_for_provider_error_class(error_class: str) -> str:
    """A provider call's TYPED error class -> its fault code.

    The provider-call ledger receives ``error_class`` -- a class name or typed slug the
    raising seam already computed, not free prose. A class nobody recognises is still
    an unavailable provider (the boundary KNOWS the provider call failed); it is never
    guessed into a more specific family.
    """
    normalized = str(error_class or "").strip().lower()
    if not normalized:
        return FAULT_PROVIDER_UNAVAILABLE
    if any(marker in normalized for marker in _EXHAUSTED_MARKERS):
        return FAULT_PROVIDER_EXHAUSTED
    if any(marker in normalized for marker in _TIMEOUT_MARKERS):
        return FAULT_TIMEOUT
    if "cancel" in normalized:
        return FAULT_CANCELLED
    return FAULT_PROVIDER_UNAVAILABLE


def retry_classification_from_faults(records: list[FaultRecord] | tuple[FaultRecord, ...]) -> str:
    """The turn-level retry hint, DERIVED from its fault records -- never guessed in prose.

    A user cancellation outranks everything: retrying it overrides the user. Policy
    refusals and unknown causes are never retry recommendations. Empty means "no fault
    said anything about retrying" -- a fact, not a claim that all is well.
    """
    records = list(records or [])
    if not records:
        return ""
    hints: list[str] = []
    for record in records:
        if record.code == FAULT_CANCELLED:
            return "do_not_retry"
        hints.append(record.retry)
    if "retry_now" in hints:
        return "retry_now"
    if "retry_after_change" in hints:
        return "retry_after_change"
    if "retry_later" in hints:
        return "retry_later"
    return "do_not_retry"


__all__ = [
    "FaultError",
    "fault_code_for_provider_error_class",
    "map_exception",
    "retry_classification_from_faults",
]
