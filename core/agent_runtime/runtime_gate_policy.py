from __future__ import annotations

from typing import Any


def default_gate(agent: Any, plan: Any, classification: dict[str, Any]) -> Any:
    risk_flags = set(classification.get("risk_flags") or []) | set(getattr(plan, "risk_flags", None) or [])

    hard_block = {
        "destructive_command",
        "privileged_action",
        "persistence_attempt",
        "exfiltration_hint",
        "shell_injection_risk",
    }

    if any(flag in hard_block for flag in risk_flags):
        return agent.GateDecision(
            mode="blocked",
            reason="Blocked by safety policy due to risk flags.",
            requires_user_approval=False,
            allowed_actions=[],
        )

    if classification.get("task_class") == "risky_system_action":
        return agent.GateDecision(
            mode="advice_only",
            reason="System-sensitive task forced to advice-only.",
            requires_user_approval=True,
            allowed_actions=[],
        )

    return agent.GateDecision(
        mode="advice_only",
        reason="v1 defaults to advice-only.",
        requires_user_approval=False,
        allowed_actions=[],
    )
