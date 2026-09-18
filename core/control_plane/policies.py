from __future__ import annotations

from typing import Any


def budget_caps_policy(*, policy_getter: Any, utcnow_fn: Any) -> dict[str, Any]:
    return {
        "generated_at": utcnow_fn(),
        "swarm_dispatch": {
            "free_tier_daily_swarm_points": float(policy_getter("economics.free_tier_daily_swarm_points", 24.0) or 24.0),
            "free_tier_max_dispatch_points": float(policy_getter("economics.free_tier_max_dispatch_points", 12.0) or 12.0),
        },
        "public_hive": {
            "daily_quota_low": float(policy_getter("economics.public_hive_daily_quota_low", 24.0) or 24.0),
            "daily_quota_mid": float(policy_getter("economics.public_hive_daily_quota_mid", 192.0) or 192.0),
            "daily_quota_high": float(policy_getter("economics.public_hive_daily_quota_high", 768.0) or 768.0),
            "bonus_per_active_claim": float(
                policy_getter("economics.public_hive_daily_quota_bonus_per_active_claim", 24.0) or 24.0
            ),
            "bonus_cap": float(
                policy_getter("economics.public_hive_daily_quota_max_active_claim_bonus", 192.0) or 192.0
            ),
            "route_costs": dict(policy_getter("economics.public_hive_route_costs", {}) or {}),
        },
        "adaptation": {
            "tick_interval_seconds": int(policy_getter("adaptation.tick_interval_seconds", 1800) or 1800),
            "max_running_jobs": int(policy_getter("adaptation.max_running_jobs", 1) or 1),
            "min_examples_to_train": int(policy_getter("adaptation.min_examples_to_train", 24) or 24),
            "min_structured_examples": int(policy_getter("adaptation.min_structured_examples", 12) or 12),
            "min_high_signal_examples": int(policy_getter("adaptation.min_high_signal_examples", 8) or 8),
            "min_new_examples_since_last_job": int(policy_getter("adaptation.min_new_examples_since_last_job", 8) or 8),
            "max_conversation_ratio": float(policy_getter("adaptation.max_conversation_ratio", 0.45) or 0.45),
            "promotion_margin": float(policy_getter("adaptation.promotion_margin", 0.03) or 0.03),
            "rollback_margin": float(policy_getter("adaptation.rollback_margin", 0.04) or 0.04),
        },
    }


def reviewer_lane_policy() -> dict[str, Any]:
    return {
        "lane": "reviewer",
        "purpose": "Validate schema, policy compliance, and output quality before durable promotion.",
        "source_of_truth": "task_results.status = submitted; useful_outputs stay ineligible until reviewed or approved",
        "required_checks": [
            "schema_valid",
            "policy_compliant",
            "unexpected_write_behavior_absent",
            "output_complete_or_explicitly_partial",
            "human_approval_required_if_risky",
            "training_eligibility_explicit",
        ],
    }


def archivist_lane_policy() -> dict[str, Any]:
    return {
        "lane": "archivist",
        "purpose": "Compact approved outputs into durable summaries without polluting memory with transient noise.",
        "source_of_truth": "useful_outputs.archive_state in candidate|approved",
        "archive_rules": [
            "approved_summaries_only",
            "strip transient chatter",
            "preserve task/result IDs for traceability",
            "prefer role-scoped or shared semantic memory, not raw chat dumps",
        ],
    }


def control_plane_policy_text() -> str:
    return (
        "# VOOL Control Plane Mirror\n\n"
        "This workspace is an operator-facing mirror of VOOL's real runtime state.\n"
        "It does not replace the DB-backed source of truth.\n\n"
        "Safety posture:\n"
        "- additive only\n"
        "- read-mostly mirrors\n"
        "- review before archive\n"
        "- approvals stay explicit\n"
        "- failed or policy-rejected work stays visible in deadletters\n"
    )


__all__ = [
    "archivist_lane_policy",
    "budget_caps_policy",
    "control_plane_policy_text",
    "reviewer_lane_policy",
]
