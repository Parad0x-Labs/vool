"""Typed task-fulfillment truth, independent from runtime transport lifecycle.

``runtime_checkpoints.status`` answers whether execution is running or terminal.  It does not
answer whether the user's request was fulfilled.  This module supplies that second, deliberately
small contract and keeps legacy checkpoint rows readable while the durable schema rolls forward.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any


class FulfillmentStatus(str, Enum):
    FULFILLED = "fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


_UNFULFILLED = {
    FulfillmentStatus.PARTIALLY_FULFILLED,
    FulfillmentStatus.BLOCKED,
    FulfillmentStatus.FAILED,
}


@dataclass(frozen=True)
class RuntimeTaskOutcome:
    fulfillment_status: FulfillmentStatus
    failure_stage: str = ""
    failure_codes: tuple[str, ...] = ()
    retryable: bool = False
    origin_task_id: str = ""
    origin_checkpoint_id: str = ""
    original_request_hash: str = ""

    @property
    def is_unfulfilled(self) -> bool:
        return self.fulfillment_status in _UNFULFILLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "fulfillment_status": self.fulfillment_status.value,
            "failure_stage": self.failure_stage,
            "failure_codes": list(self.failure_codes),
            "retryable": self.retryable,
            "origin_task_id": self.origin_task_id,
            "origin_checkpoint_id": self.origin_checkpoint_id,
            "original_request_hash": self.original_request_hash,
        }


def request_identity_hash(request_text: str) -> str:
    value = str(request_text or "").encode("utf-8")
    return f"sha256:{hashlib.sha256(value).hexdigest()}" if value else ""


def normalize_runtime_task_outcome(
    value: Any,
    *,
    transport_status: str = "",
    failure_text: str = "",
    task_id: str = "",
    checkpoint_id: str = "",
    request_text: str = "",
) -> RuntimeTaskOutcome:
    raw = dict(value or {}) if isinstance(value, dict) else {}
    status_value = str(raw.get("fulfillment_status") or "").strip().lower()
    if status_value not in {item.value for item in FulfillmentStatus}:
        lifecycle = str(transport_status or "").strip().lower()
        if lifecycle == "cancelled":
            status_value = FulfillmentStatus.CANCELLED.value
        elif str(failure_text or "").strip() or lifecycle == "failed":
            status_value = FulfillmentStatus.FAILED.value
        else:
            status_value = FulfillmentStatus.FULFILLED.value

    codes: list[str] = []
    for item in list(raw.get("failure_codes") or []):
        code = str(item or "").strip().lower().replace(" ", "_")
        if code and code not in codes:
            codes.append(code)
    if not codes and status_value == FulfillmentStatus.FAILED.value and failure_text:
        codes.append("legacy_runtime_failure")

    retryable = bool(raw.get("retryable"))
    if "retryable" not in raw and status_value in {
        FulfillmentStatus.FAILED.value,
        FulfillmentStatus.PARTIALLY_FULFILLED.value,
    }:
        # Legacy failed checkpoints were already eligible for explicit user retry. Preserve that
        # behavior without treating a completed legacy row as unfulfilled.
        retryable = True

    return RuntimeTaskOutcome(
        fulfillment_status=FulfillmentStatus(status_value),
        failure_stage=str(raw.get("failure_stage") or "").strip().lower(),
        failure_codes=tuple(codes),
        retryable=retryable,
        origin_task_id=str(raw.get("origin_task_id") or task_id or "").strip(),
        origin_checkpoint_id=str(
            raw.get("origin_checkpoint_id") or checkpoint_id or ""
        ).strip(),
        original_request_hash=str(
            raw.get("original_request_hash") or request_identity_hash(request_text)
        ).strip(),
    )


def fulfillment_outcome_from_attempt_lifecycle(
    lifecycle_state: str, *, terminal_reason: str = "", retryable: bool | None = None
) -> dict[str, Any] | None:
    """Translate a finalized runtime attempt's lifecycle into fulfillment truth.

    Exists so a lane whose canonical state is the attempt store (the live-data lane) can hand the
    turn trace the SAME verdict instead of letting prose decide. Measured at b7857c6c: a live-data
    turn whose attempt finalized PARTIAL_SUCCESS reached `terminal_fulfillment_outcome` with no
    explicit outcome and was recorded FULFILLED because the rendered answer was non-empty. Prose
    may never upgrade authoritative finality; this is the bridge that carries the canonical state
    instead.
    """
    state = str(lifecycle_state or "").strip().upper()
    if not state:
        return None
    if state == "SUCCEEDED":
        return {"fulfillment_status": FulfillmentStatus.FULFILLED.value}
    if state == "PARTIAL_SUCCESS":
        return {
            "fulfillment_status": FulfillmentStatus.PARTIALLY_FULFILLED.value,
            "failure_stage": "task_execution",
            "failure_codes": ["partial_task_execution"],
            "retryable": True if retryable is None else retryable,
        }
    if state == "WAITING_APPROVAL":
        return {
            "fulfillment_status": FulfillmentStatus.BLOCKED.value,
            "failure_stage": "task_execution",
            "failure_codes": ["approval_required"],
            "retryable": False,
        }
    if state in {"CANCELLED"}:
        return {
            "fulfillment_status": FulfillmentStatus.CANCELLED.value,
            "failure_stage": "task_execution",
            "failure_codes": ["cancelled"],
            "retryable": False,
        }
    # FAILED_* / ABANDONED / anything else terminal-but-not-success.
    code = str(terminal_reason or "").strip().lower().replace(" ", "_")[:120]
    return {
        "fulfillment_status": FulfillmentStatus.FAILED.value,
        "failure_stage": "task_execution",
        "failure_codes": [code or "task_execution_failed"],
        "retryable": False if retryable is None else retryable,
    }


def output_validation_outcome(response_control: Any) -> dict[str, Any] | None:
    """Translate terminal response-control evidence into fulfillment truth.

    A bounded repair that eventually produced a compliant answer is still fulfilled.  A safe
    fallback emitted because validation remained unsatisfied is a failed, retryable task even
    though the provider call and runtime transport both completed normally.
    """

    control = dict(response_control or {}) if isinstance(response_control, dict) else {}

    # Evidence the turn actually retrieved, and whether any of it reached the answer. This is a
    # SEPARATE question from `answer_completeness`, which reads text shape alone: a promise to go
    # and look is not cut off, so the shape inspector rightly passes it, and the turn was stamped
    # `fulfilled`/`retryable: false` while its own receipts recorded a completed retrieval
    # (measured on c6eed761, both lanes). Discarded evidence is a PARTIAL turn: something was
    # obtained, the user was not given it. See `core.evidence_binding` for why this is decided from
    # retrieved terms rather than from the wording of the reply.
    evidence = dict(control.get("evidence_binding") or {})
    evidence_discarded = bool(evidence.get("evidence_discarded"))
    # Partial coverage is PARTIAL support: some claims restate the evidence, some do not. It is
    # still a partial turn, but the code must not say the evidence was absent from the answer
    # (measured 2026-09-06: six of seven claims supported, code "retrieved_evidence_absent").
    coverage = str(evidence.get("coverage") or dict(evidence.get("claim_support") or {}).get("coverage") or "")
    partially_supported = evidence_discarded and coverage == "partial"
    discarded_outcome = {
        "fulfillment_status": FulfillmentStatus.PARTIALLY_FULFILLED.value,
        "failure_stage": "output_validation",
        "failure_codes": [
            "evidence_binding:partially_supported" if partially_supported
            else "evidence_binding:retrieved_evidence_absent_from_answer"
        ],
        "retryable": True,
    }

    # A provider's terminal truncation is execution evidence, not a prose
    # heuristic. Even a complete-looking last sentence can be missing rows.
    provider_final = dict(dict(control.get("provider_completion") or {}).get("final") or {})
    if provider_final.get("incomplete"):
        return {
            "fulfillment_status": (
                FulfillmentStatus.PARTIALLY_FULFILLED.value
                if provider_final.get("has_content") else FulfillmentStatus.FAILED.value
            ),
            "failure_stage": "output_validation",
            "failure_codes": [
                "provider_completion:" + str(reason)
                for reason in provider_final.get("reasons", [])
            ] or ["provider_completion:incomplete"],
            "retryable": True,
        }

    explicit = control.get("fulfillment_outcome")
    if isinstance(explicit, dict):
        # An explicit verdict wins, EXCEPT an explicit claim of full fulfilment over evidence the
        # answer never carried -- that is the exact untruth this check exists to stop.
        if evidence_discarded and str(
            explicit.get("fulfillment_status") or ""
        ) == FulfillmentStatus.FULFILLED.value:
            return dict(discarded_outcome)
        return dict(explicit)

    if evidence_discarded:
        return dict(discarded_outcome)

    final_ui = dict(control.get("final_ui") or {})
    completeness = dict(final_ui.get("answer_completeness") or {})
    if completeness.get("incomplete") and completeness.get("has_content"):
        codes = [
            f"answer_completeness:{str(reason or '').strip()}"
            for reason in list(completeness.get("reasons") or [])
            if str(reason or "").strip()
        ] or ["answer_completeness:incomplete"]
        return {
            "fulfillment_status": FulfillmentStatus.PARTIALLY_FULFILLED.value,
            "failure_stage": "output_validation",
            "failure_codes": codes,
            "retryable": True,
        }
    fallback_applied = bool(control.get("fallback_applied"))
    if not fallback_applied and final_ui.get("fallback_applied"):
        ordinary = dict(final_ui.get("ordinary_chat_output") or {})
        language = dict(final_ui.get("response_language") or {})
        fallback_applied = bool(
            ordinary.get("allowed") is False
            or language.get("compliant") is False
            or completeness.get("degenerate")
        )
    if not fallback_applied:
        return None

    failure_codes: list[str] = []
    ordinary = dict(control.get("ordinary_chat_output") or {})
    for reason in list(ordinary.get("reasons") or []):
        code = str(reason or "").strip()
        if code and code not in failure_codes:
            failure_codes.append(code)
    language = dict(control.get("response_language") or {})
    for violation in list(language.get("violations") or []):
        code = f"response_language:{str(violation or '').strip()}"
        if code != "response_language:" and code not in failure_codes:
            failure_codes.append(code)
    raw_output = dict(control.get("raw_output") or {})
    for violation in list(raw_output.get("violations") or []):
        code = f"raw_output:{str(violation or '').strip()}"
        if code != "raw_output:" and code not in failure_codes:
            failure_codes.append(code)
    for violation in list(control.get("constraint_violations") or []):
        code = f"response_constraint:{str(violation or '').strip()}"
        if code != "response_constraint:" and code not in failure_codes:
            failure_codes.append(code)
    if not failure_codes:
        failure_codes.append("output_validation_fallback")
    return {
        "fulfillment_status": FulfillmentStatus.FAILED.value,
        "failure_stage": "output_validation",
        "failure_codes": failure_codes,
        "retryable": True,
    }


def fulfillment_outcome_from_source_context(source_context: Any) -> dict[str, Any] | None:
    context = dict(source_context or {}) if isinstance(source_context, dict) else {}
    outcome = output_validation_outcome(context.get("response_control"))
    if outcome is not None:
        return outcome
    constraint = dict(context.get("response_constraint_final") or {})
    if constraint.get("fallback_applied") and constraint.get("compliant") is False:
        codes = [
            f"response_constraint:{str(item or '').strip()}"
            for item in list(constraint.get("violations") or [])
            if str(item or "").strip()
        ] or ["response_constraint:noncompliant"]
        return {
            "fulfillment_status": FulfillmentStatus.FAILED.value,
            "failure_stage": "output_validation",
            "failure_codes": codes,
            "retryable": True,
        }
    return None


_PROVIDER_FAILURE_SOURCES = frozenset(
    {
        "no_provider_available",
        "provider_fallback_budget_exceeded",
        "required_tools_not_offered",
    }
)


def _normalized_failure_codes(values: Any) -> list[str]:
    codes: list[str] = []
    for value in list(values or []):
        code = str(value or "").strip().lower().replace(" ", "_")
        if code and code not in codes:
            codes.append(code)
    return codes


def provider_terminal_outcome(model_execution: Any, *, call_accounting: Any = None) -> RuntimeTaskOutcome | None:
    """Translate the router's terminal decision, not failed attempts behind a recovered answer."""
    execution = model_execution if isinstance(model_execution, dict) else {}
    source = str(execution.get("source") or "").strip().lower()
    details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
    blocked = source in {"model_unavailable", "autopilot_blocked", "routing_identity_missing"}
    if source == "selected_model_blocked":
        blocked = not bool(details.get("model_was_attempted"))
    elif not blocked and source not in _PROVIDER_FAILURE_SOURCES | {"explicit_heavy_lane_failed"}:
        return None
    accounting = call_accounting if isinstance(call_accounting, dict) else {}
    codes = _normalized_failure_codes(accounting.get("failed_error_classes")) if not blocked else []
    return normalize_runtime_task_outcome({
        "fulfillment_status": FulfillmentStatus.BLOCKED.value if blocked else FulfillmentStatus.FAILED.value,
        "failure_stage": "provider_execution",
        "failure_codes": codes or [source],
        "retryable": not blocked,
    })


def _conductor_fulfillment_outcome(result: dict[str, Any]) -> dict[str, Any] | None:
    """Classify an explicitly exposed conductor receipt without reading its prose.

    Different conductor callers expose either the compact receipt counts or the node outcome
    dictionaries.  Both are execution evidence.  A composed response with at least one served
    and one unserved node is partial; a plan with no served nodes failed.  Absence of either shape
    means this helper has no conductor authority and must leave the result alone.
    """

    details = dict(result.get("details") or {}) if isinstance(result.get("details"), dict) else {}
    receipt: dict[str, Any] = {}
    for candidate in (
        result.get("conductor_receipt"),
        result.get("conductor"),
        details.get("conductor_receipt"),
        details.get("conductor"),
    ):
        if isinstance(candidate, dict):
            receipt = dict(candidate)
            break

    succeeded = int(receipt.get("succeeded_count") or receipt.get("answered_count") or 0)
    unserved = int(
        receipt.get("failed_count")
        or receipt.get("unserved_count")
        or receipt.get("unresolved_count")
        or 0
    )
    node_count = int(receipt.get("node_count") or 0)
    if node_count > succeeded + unserved:
        unserved = node_count - succeeded
    outcomes: list[dict[str, Any]] = []
    for candidate in (
        result.get("conductor_outcomes"),
        details.get("conductor_outcomes"),
        receipt.get("outcomes"),
    ):
        if isinstance(candidate, list):
            outcomes = [dict(item) for item in candidate if isinstance(item, dict)]
            break
    if outcomes:
        states = [str(item.get("state") or "").strip().lower() for item in outcomes]
        succeeded = sum(state in {"succeeded", "success", "completed"} for state in states)
        unserved = sum(
            state in {"failed", "unresolved", "dependency_failed", "unavailable", "blocked"}
            for state in states
        )

    if not succeeded and not unserved:
        return None
    if succeeded and unserved:
        return {
            "fulfillment_status": FulfillmentStatus.PARTIALLY_FULFILLED.value,
            "failure_stage": "conductor_execution",
            "failure_codes": ["conductor_nodes_unserved"],
            "retryable": True,
        }
    if unserved:
        return {
            "fulfillment_status": FulfillmentStatus.FAILED.value,
            "failure_stage": "conductor_execution",
            "failure_codes": ["conductor_no_nodes_served"],
            "retryable": True,
        }
    return None


#: What each conductor disposition is ALLOWED to persist as. The in-process `ProductDecision`
#: constructor already refuses a contradictory pair, but a decision crosses a serialization boundary
#: to get here and a dict has no constructor. This is the same rule restated where the wire is read.
_DISPOSITION_ALLOWS = {
    "fulfilled": {FulfillmentStatus.FULFILLED},
    "partially_fulfilled": {FulfillmentStatus.PARTIALLY_FULFILLED},
    "failed": {FulfillmentStatus.FAILED},
    "blocked": {FulfillmentStatus.BLOCKED},
    "integrity_failure": {FulfillmentStatus.FAILED},
}

#: What a turn persists as when its own decision contradicts itself on the wire.
WIRE_INCONSISTENCY_CODE = "conductor_integrity:wire_outcome_contradicts_disposition"

#: What a turn persists as when the disposition on the wire is not one this runtime recognizes.
WIRE_UNKNOWN_DISPOSITION_CODE = "conductor_integrity:wire_disposition_unrecognized"


def _wire_integrity_failure(codes: list[str]) -> RuntimeTaskOutcome:
    """The one outcome an untrue record may persist as."""
    return normalize_runtime_task_outcome(
        {
            "fulfillment_status": FulfillmentStatus.FAILED.value,
            "failure_stage": "conductor_product_decision",
            "failure_codes": codes,
            "retryable": False,
        }
    )


def _validated_conductor_outcome(
    decision: dict[str, Any], declared: dict[str, Any]
) -> RuntimeTaskOutcome:
    """The decision's own outcome, after checking it agrees with the decision's own disposition.

    VALIDATION, not reclassification. This does not re-derive fulfilment from evidence and it does
    not pick whichever of the two fields reads better -- picking is what the five rival authorities
    used to do. Two halves of one record that contradict each other mean the record is untrue, and a
    record that is untrue about a turn is an integrity failure whichever half you believe.

    Concretely: a serialized PARTIALLY_FULFILLED decision carrying a FULFILLED outcome is a claim
    that the turn both did and did not serve everything the user asked for. It persists as FAILED
    with an integrity code, never as fulfilled.

    ALLOW-LIST, and it has to be one. The first version asked only whether a RECOGNIZED disposition
    disagreed with its outcome, so an unrecognized one -- a version skew, a truncated field, a
    hand-edited record -- matched no rule and was waved through: `made_up_disposition` beside
    FULFILLED persisted as fulfilled. A validator whose unknown case is "accept" validates the
    records that were already going to be fine. Unknown fails closed, and so does a disposition
    this runtime knows carrying a status it does not.
    """
    status_value = str(declared.get("fulfillment_status") or "").strip().lower()
    disposition = str(decision.get("disposition") or "").strip().lower()
    allowed = _DISPOSITION_ALLOWS.get(disposition)
    if allowed is None:
        # Includes `nothing_captured`, which is not a persistable disposition at all: an unclaimed
        # decision carries no outcome, so one arriving here with a status is already untrue.
        return _wire_integrity_failure(
            [
                WIRE_UNKNOWN_DISPOSITION_CODE,
                f"disposition:{disposition}",
                f"declared:{status_value}",
            ]
        )
    try:
        status = FulfillmentStatus(status_value)
    except ValueError:
        status = None
    if status is None or status not in allowed:
        return _wire_integrity_failure(
            [WIRE_INCONSISTENCY_CODE, f"disposition:{disposition}", f"declared:{status_value}"]
        )
    return normalize_runtime_task_outcome(declared)


def terminal_fulfillment_outcome(
    result: Any,
    *,
    source_context: Any = None,
    call_accounting: Any = None,
) -> RuntimeTaskOutcome:
    """Derive final task truth from typed terminal evidence, never from apology prose.

    The HTTP request may complete successfully while the user's task does not.  This classifier
    deliberately preserves that transport compatibility and answers only the fulfillment
    question used by checkpoints and terminal trace receipts.
    """

    payload = dict(result or {}) if isinstance(result, dict) else {}
    context = dict(source_context or {}) if isinstance(source_context, dict) else {}
    response = str(payload.get("response") or payload.get("output_text") or "")

    # A CLAIMED conductor turn carries its own decision, and that decision already computed the
    # exact outcome to persist. Read first and read whole: everything below this line derives a
    # status from evidence about the turn, and deriving a second answer for a turn that already has
    # one is how `conductor_no_nodes_served` came to sit in a receipt beside a delivered answer, and
    # how a non-empty apology became `FULFILLED`. There is nothing to reconcile here because there
    # is only one authority.
    decision = payload.get("conductor_product_decision")
    if not isinstance(decision, dict):
        decision = context.get("conductor_product_decision")
    if isinstance(decision, dict):
        declared = decision.get("runtime_task_outcome")
        if isinstance(declared, dict) and declared.get("fulfillment_status"):
            return _validated_conductor_outcome(decision, declared)

    # The turn's own evidence verdict rides the payload, because provider execution crosses a
    # shallow copy of the request context and a verdict stamped there never reaches this front
    # door. Fold it into the response control the translator reads, so one function decides what
    # discarded evidence means for fulfilment.
    payload_control = dict(payload.get("response_control") or {})
    payload_binding = payload.get("evidence_binding")
    if isinstance(payload_binding, dict):
        payload_control["evidence_binding"] = dict(payload_binding)

    explicit = payload.get("fulfillment_outcome")
    validation = output_validation_outcome(payload_control) if payload_control else None
    if (
        isinstance(explicit, dict)
        and explicit.get("fulfillment_status") == FulfillmentStatus.FULFILLED.value
        and isinstance(validation, dict)
        and validation.get("fulfillment_status") != FulfillmentStatus.FULFILLED.value
    ):
        explicit = validation
    if not isinstance(explicit, dict):
        explicit = validation
    if not isinstance(explicit, dict):
        explicit = fulfillment_outcome_from_source_context(context)
    if isinstance(explicit, dict):
        return normalize_runtime_task_outcome(explicit)

    conductor = _conductor_fulfillment_outcome(payload)
    if conductor is not None:
        # Node accounting describes the CONDUCTOR, not the turn. Live evidence (set5-15,
        # 2026-08-13): a plan reported `conductor_no_nodes_served` while the turn committed a
        # complete, correct canonical answer -- every semantic contract passed and only the
        # terminal trace said `failed`. Another lane had answered. Reporting a total failure over
        # a delivered answer is the same untruth as reporting success over a broken turn, just
        # pointed the other way, so an unserved plan behind a real answer defers to the evidence
        # below rather than overruling it. A PARTIAL plan still stands: there the conductor is
        # saying part of what the user asked for is genuinely missing from the answer.
        partial = (
            str(conductor.get("fulfillment_status") or "")
            == FulfillmentStatus.PARTIALLY_FULFILLED.value
        )
        if partial or not response.strip():
            return normalize_runtime_task_outcome(conductor)

    task_outcome = str(payload.get("task_outcome") or "").strip().lower()
    response_class = str(payload.get("response_class") or "").strip().lower()
    if task_outcome in {"partially_fulfilled", "partial"}:
        return normalize_runtime_task_outcome(
            {
                "fulfillment_status": FulfillmentStatus.PARTIALLY_FULFILLED.value,
                "failure_stage": "task_execution",
                "failure_codes": ["partial_task_execution"],
                "retryable": True,
            }
        )
    if task_outcome in {"failed", "failure"} or response_class in {
        "task_failed_user_safe",
        "system_error_user_safe",
    }:
        details = dict(payload.get("details") or {}) if isinstance(payload.get("details"), dict) else {}
        retryable = bool(details.get("retryable"))
        return normalize_runtime_task_outcome(
            {
                "fulfillment_status": FulfillmentStatus.FAILED.value,
                "failure_stage": "task_execution",
                "failure_codes": [str(details.get("failure_code") or "task_execution_failed")],
                "retryable": retryable,
            }
        )
    if task_outcome in {"blocked", "pending_approval"} or response_class == "approval_required":
        return normalize_runtime_task_outcome(
            {
                "fulfillment_status": FulfillmentStatus.BLOCKED.value,
                "failure_stage": "task_execution",
                "failure_codes": [task_outcome or "approval_required"],
                "retryable": False,
            }
        )

    if str(payload.get("error") or "").strip():
        return normalize_runtime_task_outcome(
            {
                "fulfillment_status": FulfillmentStatus.FAILED.value,
                "failure_stage": "runtime_execution",
                "failure_codes": ["terminal_runtime_error"],
                "retryable": True,
            }
        )

    provider_outcome = provider_terminal_outcome(
        payload.get("model_execution"), call_accounting=call_accounting,
    )
    if provider_outcome is not None:
        return provider_outcome

    if not response.strip():
        return normalize_runtime_task_outcome(
            {
                "fulfillment_status": FulfillmentStatus.FAILED.value,
                "failure_stage": "output_validation",
                "failure_codes": ["empty_canonical_output"],
                "retryable": True,
            }
        )

    return normalize_runtime_task_outcome(
        {"fulfillment_status": FulfillmentStatus.FULFILLED.value}
    )


def terminal_trace_outcome(outcome: RuntimeTaskOutcome) -> str:
    """Map typed fulfillment to the legacy terminal trace spelling."""

    if outcome.fulfillment_status is FulfillmentStatus.FULFILLED:
        return "completed"
    return outcome.fulfillment_status.value
