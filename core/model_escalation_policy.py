from __future__ import annotations

from dataclasses import dataclass

from core.enum_compat import StrEnum


class EscalationReason(StrEnum):
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    TOOL_MISSING = "TOOL_MISSING"
    TOOL_FAILED = "TOOL_FAILED"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    LOCAL_MODEL_CAPABILITY = "LOCAL_MODEL_CAPABILITY"
    LOCAL_MODEL_INVALID_OUTPUT = "LOCAL_MODEL_INVALID_OUTPUT"
    LOCAL_MODEL_REPEATED_FAILURE = "LOCAL_MODEL_REPEATED_FAILURE"
    HIGH_RISK_REVIEW = "HIGH_RISK_REVIEW"
    USER_REQUESTED_PREMIUM = "USER_REQUESTED_PREMIUM"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class PaidModelMode(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    LOCAL_FIRST_ASK = "LOCAL_FIRST_ASK"
    AUTO_UNDER_LIMIT = "AUTO_UNDER_LIMIT"
    PINNED_MODEL = "PINNED_MODEL"


_CAPABILITY_REASONS = {
    EscalationReason.CONTEXT_TOO_LARGE,
    EscalationReason.LOCAL_MODEL_CAPABILITY,
    EscalationReason.LOCAL_MODEL_INVALID_OUTPUT,
    EscalationReason.LOCAL_MODEL_REPEATED_FAILURE,
    EscalationReason.HIGH_RISK_REVIEW,
    EscalationReason.USER_REQUESTED_PREMIUM,
}


@dataclass(frozen=True)
class PaidModelPolicy:
    mode: PaidModelMode = PaidModelMode.LOCAL_ONLY
    per_call_max_usd: float = 0.0
    per_task_max_usd: float = 0.0
    daily_max_usd: float = 0.0
    monthly_max_usd: float = 0.0
    model_allowlist: tuple[str, ...] = ()
    permitted_data_categories: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = (".env", "*.key", "*.pem", "*wallet*", "*seed*")
    pinned_model: str = ""


@dataclass(frozen=True)
class PaidPolicyDecision:
    action: str
    allowed: bool
    reason: str
    requires_approval: bool


def evaluate_paid_escalation(
    policy: PaidModelPolicy,
    *,
    reason: EscalationReason | str,
    model_id: str,
    openrouter_configured: bool,
    network_allowed: bool,
    estimated_max_usd: float,
    data_categories: tuple[str, ...] = (),
    contains_secrets: bool = False,
    approval_source: str = "",
) -> PaidPolicyDecision:
    resolved_reason = EscalationReason(reason)
    if policy.mode == PaidModelMode.LOCAL_ONLY:
        return PaidPolicyDecision("continue_local", False, "local_only", False)
    if resolved_reason not in _CAPABILITY_REASONS:
        return PaidPolicyDecision("continue_local", False, f"not_a_model_capability_failure:{resolved_reason}", False)
    if not openrouter_configured:
        return PaidPolicyDecision("continue_local", False, "openrouter_not_configured", False)
    if not network_allowed:
        return PaidPolicyDecision("continue_local", False, "network_policy_denied", False)
    if contains_secrets:
        return PaidPolicyDecision("continue_local", False, "secret_in_payload", False)
    if policy.model_allowlist and model_id not in policy.model_allowlist:
        return PaidPolicyDecision("continue_local", False, "model_not_allowlisted", False)
    if policy.per_call_max_usd <= 0 or estimated_max_usd > policy.per_call_max_usd:
        return PaidPolicyDecision("continue_local", False, "per_call_cap_exceeded", False)
    permitted = set(policy.permitted_data_categories)
    if any(category not in permitted for category in data_categories):
        return PaidPolicyDecision("continue_local", False, "data_category_not_permitted", False)
    if policy.mode == PaidModelMode.PINNED_MODEL and model_id != policy.pinned_model:
        return PaidPolicyDecision("continue_local", False, "pinned_model_mismatch", False)
    if policy.mode in {PaidModelMode.LOCAL_FIRST_ASK, PaidModelMode.PINNED_MODEL}:
        if approval_source == "user":
            return PaidPolicyDecision("paid_call", True, "explicit_user_approval", False)
        return PaidPolicyDecision("ask_user", False, "per_call_user_approval_required", True)
    return PaidPolicyDecision("paid_call", True, "automatic_policy_gates_passed", False)


__all__ = [
    "EscalationReason",
    "PaidModelMode",
    "PaidModelPolicy",
    "PaidPolicyDecision",
    "evaluate_paid_escalation",
]
