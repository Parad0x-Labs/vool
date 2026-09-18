"""`resolution_receipt_v1` -- what actually decided a turn, written where receipts already live.

This is not a new store. It is one more event type on the **existing** runtime ledger
(`runtime_session_events`), emitted through the **existing** funnel
(`core.runtime_task_events.emit_runtime_event`), and therefore inheriting the secret redaction, the
per-session sequencing, the live Activity fan-out and the `core.turn_trace` reader that every other
runtime event already has. A second truth store would have to be kept in step with the first, and
receipts that disagree about the same turn are worse than one receipt nobody reads.

Six slots, and the discipline that makes them worth having:

* ``reach``      -- which gates were consulted and which one preempted the model. Copied from a
                    `ReachRecorder`, never recomputed.
* ``routing``    -- the lane decision the runtime actually took.
* ``shape``      -- the structural shape of the request.
* ``semantic``   -- the turn's RequestGraph projection (always), plus the resolver's shadow run.
* ``admission``  -- the semantic result admitted for the turn, read from the A2 seam.
* ``reason_codes`` -- typed codes, never prose.

**An unfilled slot says so.** A turn that consulted no resolver or admitted no result reads
``not_attempted`` with a reason decided at receipt time for THIS turn. They are not empty dicts and
not `None`: an absent value reads as "nothing to report" at a glance, and this receipt exists
precisely so that "we did not do this" and "this came back clean" can never be confused.
`SLOT_NOT_ATTEMPTED` is a value you have to look at.

Fidelity is checkable rather than asserted. `verify_receipt_against_reach` recomputes the receipt's
reach claims from the recorder and reports every disagreement, so a receipt that misreports the path
fails a test instead of being believed.
"""
from __future__ import annotations

from typing import Any

from core.semantic.reach import ReachRecorder
from core.semantic.types import ReasonCode, RequestShape

#: Versioned in the payload, matching the convention `conductor_plan_receipt_v1` already set.
RESOLUTION_RECEIPT_SCHEMA = "resolution_receipt_v1"

#: The ledger event type this receipt travels as.
RESOLUTION_RECEIPT_EVENT = "semantic_resolution_receipt"

#: What a slot holds when the phase does not fill it. A visible value, never `None` or `{}` -- the
#: whole point is that "not attempted" cannot be misread as "attempted and fine".
SLOT_NOT_ATTEMPTED = "not_attempted"

#: Why a slot reads not_attempted on a turn that consulted no resolver / admitted no result. A true
#: statement about THIS turn, decided at receipt time -- never a claim about which build wrote it.
RESOLVER_NOT_ATTEMPTED_REASON = "no semantic resolver was consulted for this turn"
ADMISSION_NOT_ATTEMPTED_REASON = "no semantic result was admitted for this turn"

#: The semantic slot's states, so a reader can tell "not run" from "run and unavailable".
SLOT_ATTEMPTED = "attempted"
SLOT_UNAVAILABLE = "unavailable"


def semantic_shadow_slot(
    *,
    state: str = SLOT_NOT_ATTEMPTED,
    resolver: str = "",
    detail: str = RESOLVER_NOT_ATTEMPTED_REASON,
    proposals: list[dict[str, Any]] | None = None,
    graph: dict[str, Any] | None = None,
    shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The resolver's slot. ``graph`` is the turn's text-free RequestGraph projection (the
    deterministic reading every turn now carries); ``shadow`` is the shadow ticket/observation
    when a resolver ran beside it. Defaults to an explicit, reasoned `not_attempted`."""
    return {
        "state": str(state or SLOT_NOT_ATTEMPTED),
        "resolver": str(resolver or ""),
        "detail": str(detail or ""),
        "proposal_count": len(proposals or []),
        "proposals": list(proposals or []),
        "graph": dict(graph or {}),
        "shadow": dict(shadow or {}),
    }


def admission_slot(
    *,
    state: str = SLOT_NOT_ATTEMPTED,
    detail: str = ADMISSION_NOT_ATTEMPTED_REASON,
    admitted_count: int = 0,
    rejected_count: int = 0,
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The future admission decision's slot.

    `admitted_count` and `rejected_count` are carried separately from `results` so a partial -- some
    clauses admitted, some refused -- is readable without walking the list. A partial that reads as
    a success is the failure mode this whole receipt is defending against.
    """
    return {
        "state": str(state or SLOT_NOT_ATTEMPTED),
        "detail": str(detail or ""),
        "admitted_count": int(admitted_count),
        "rejected_count": int(rejected_count),
        "is_partial": bool(admitted_count and rejected_count),
        "results": list(results or []),
    }


def build_resolution_receipt(
    recorder: ReachRecorder | None,
    *,
    turn_id: str = "",
    session_id: str = "",
    routing_family: str = "",
    routing_handled: bool = False,
    routing_detail: str = "",
    shape: RequestShape | str = RequestShape.UNKNOWN,
    semantic: dict[str, Any] | None = None,
    admission: dict[str, Any] | None = None,
    reason_codes: list[ReasonCode | str] | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the receipt. Pure -- builds a dict, writes nothing, calls no model.

    A `None` recorder is reported rather than fatal: observation may be switched off, and a receipt
    that says `observed: false` is a true statement about a turn nobody watched. Inventing an empty
    reach record for it would claim the turn passed no gates, which is false.
    """
    reach_payload: dict[str, Any]
    if recorder is None:
        reach_payload = {
            "observed": False,
            "detail": "reach observation was not active for this turn",
            "consulted": [],
            "preempted_by": "",
            "blocked_by": [],
            "entered_model_lane": False,
            "provider_call_attempted": False,
            "provider_calls_attempted": [],
            "attempt_disagrees_with_self_report": False,
            "tool_offer_made": False,
            "turn_outcome": {"observed": False},
            "gate_count": 0,
            "events": [],
        }
    else:
        reach_payload = {"observed": True, "detail": "", **recorder.to_dict()}

    shape_value = shape.value if isinstance(shape, RequestShape) else str(shape or RequestShape.UNKNOWN.value)
    codes = [code.value if isinstance(code, ReasonCode) else str(code) for code in (reason_codes or [])]

    # What the turn ACTUALLY executed, from the authoritative ledger, reconciled against what the
    # turn said about itself. `reach` already carried two independent model-call observations and an
    # `attempt_disagrees_with_self_report` flag comparing them -- and nothing read the flag, so a
    # receipt could record a disagreement about whether a model ran and no part of the system was
    # any the wiser. The comparison now has a third party that is neither of the two disputants: the
    # execution ledger. `agrees` is the field a consumer can act on.
    execution_payload = dict(execution or {})
    if execution_payload:
        reported_calls = int((reach_payload.get("turn_outcome") or {}).get("model_calls") or 0)
        authoritative_calls = int(execution_payload.get("model_calls") or 0)
        execution_payload["self_reported_model_calls"] = reported_calls
        execution_payload["agrees_with_self_report"] = reported_calls == authoritative_calls

    return {
        "schema": RESOLUTION_RECEIPT_SCHEMA,
        "turn_id": str(turn_id or (recorder.turn_id if recorder else "")),
        "session_id": str(session_id or (recorder.session_id if recorder else "")),
        "reach": reach_payload,
        "execution": execution_payload,
        "routing": {
            "family": str(routing_family or ""),
            "handled": bool(routing_handled),
            "detail": str(routing_detail or ""),
        },
        "shape": shape_value,
        "semantic": dict(semantic or semantic_shadow_slot()),
        "admission": dict(admission or admission_slot()),
        "reason_codes": codes,
    }


def verify_receipt_against_reach(
    receipt: dict[str, Any], recorder: ReachRecorder | None
) -> tuple[bool, tuple[str, ...]]:
    """Recompute the receipt's reach claims from the recorder. Returns (ok, disagreements).

    This is the mechanism that makes receipt fidelity testable instead of assumed. Every claim the
    receipt makes about the path is derived from one source; this recomputes each from that source
    and names the ones that differ. A receipt that reports a gate the recorder never saw, or that
    names the wrong preempting gate, comes back with a specific disagreement rather than a bare
    False -- so a red test says which field lied.
    """
    problems: list[str] = []
    reach = receipt.get("reach")
    if not isinstance(reach, dict):
        return False, ("receipt carries no reach section",)

    if recorder is None:
        if reach.get("observed"):
            problems.append("receipt claims reach was observed, but no recorder existed")
        return not problems, tuple(problems)

    if not reach.get("observed"):
        problems.append("receipt claims reach was not observed, but a recorder existed")

    expected = recorder.to_dict()
    for key in ("consulted", "blocked_by"):
        claimed_value = list(reach.get(key) or [])
        real_value = list(expected.get(key) or [])
        if claimed_value != real_value:
            problems.append(f"{key}: receipt says {claimed_value!r}, recorder says {real_value!r}")
    for key in (
        "preempted_by",
        "entered_model_lane",
        "provider_call_attempted",
        "tool_offer_made",
        "gate_count",
    ):
        if reach.get(key) != expected.get(key):
            problems.append(f"{key}: receipt says {reach.get(key)!r}, recorder says {expected.get(key)!r}")
    # `turn_outcome` is the turn's own self-report, and it is NOT the source for
    # `provider_call_attempted` -- that comes from the seam, and a review showed the two disagreeing.
    # It is compared here because a receipt that rewrote the self-report while leaving the seam's
    # record alone would still be misreporting the turn, just from the other side.
    if reach.get("turn_outcome") != expected.get("turn_outcome"):
        problems.append(
            f"turn_outcome: receipt says {reach.get('turn_outcome')!r}, "
            f"recorder says {expected.get('turn_outcome')!r}"
        )

    # The ordered event list is the ground truth the summaries are derived from; a receipt whose
    # summary agrees but whose events were rewritten is still lying about the path.
    claimed_events = [
        (str(item.get("gate")), str(item.get("outcome")))
        for item in (reach.get("events") or [])
        if isinstance(item, dict)
    ]
    real_events = [(str(item["gate"]), str(item["outcome"])) for item in expected.get("events") or []]
    if claimed_events != real_events:
        problems.append(f"events: receipt says {claimed_events!r}, recorder says {real_events!r}")

    return not problems, tuple(problems)


def emit_resolution_receipt(
    source_context: dict[str, Any] | None, receipt: dict[str, Any]
) -> bool:
    """Write the receipt onto the existing runtime ledger. Returns whether it was written.

    Fail-soft by contract: a receipt that cannot be stored must not take the turn with it. The
    boolean is for tests and for the caller's own record -- no runtime branch reads it.
    """
    try:
        from core.runtime_task_events import emit_runtime_event

        reach = receipt.get("reach") or {}
        preempted = str(reach.get("preempted_by") or "") or "nothing"
        emit_runtime_event(
            source_context,
            event_type=RESOLUTION_RECEIPT_EVENT,
            message=(
                f"Resolution: {reach.get('gate_count', 0)} gates consulted, "
                f"preempted by {preempted}"
            ),
            details={"receipt": receipt, "reason": str(receipt.get("routing", {}).get("family") or "")},
        )
        return True
    except Exception:
        return False


__all__ = [
    "ADMISSION_NOT_ATTEMPTED_REASON",
    "RESOLUTION_RECEIPT_EVENT",
    "RESOLUTION_RECEIPT_SCHEMA",
    "RESOLVER_NOT_ATTEMPTED_REASON",
    "SLOT_ATTEMPTED",
    "SLOT_NOT_ATTEMPTED",
    "SLOT_UNAVAILABLE",
    "admission_slot",
    "build_resolution_receipt",
    "emit_resolution_receipt",
    "semantic_shadow_slot",
    "verify_receipt_against_reach",
]
