from __future__ import annotations

import re
from re import Pattern
from typing import Any

_PATH_KEY_RE = re.compile(r"(?:^|_)(?:path|paths|root|roots|dir|directory|file|target|destination)$", re.IGNORECASE)


def template_library(*, spawn_policy_fn: Any) -> dict[str, dict[str, Any]]:
    return {
        "research_worker": {
            "AGENT_ROLE.md": (
                "# Research Worker\n\n"
                "Purpose: bounded reading, synthesis, and evidence gathering.\n"
                "Must not mutate system state or widen permissions.\n"
            ),
            "TOOLS.md": (
                "- read-only file inspection\n"
                "- bounded web/retrieval when policy allows\n"
                "- no broad shell writes\n"
            ),
            "HEARTBEAT.md": "- confirm queue/task scope\n- confirm read-only posture\n- stop on ambiguity\n",
            "POLICY.md": "Default deny. Read-mostly. Human approval on any write or risky network action.\n",
            "spawn.json": spawn_policy_fn(
                purpose="bounded_research",
                allowed_tools=["read", "search", "summarize"],
                allowed_read_roots=["./workspace", "./docs", "./data"],
                allowed_write_roots=["./workspace/logs"],
                shell_allowed=False,
                network_allowed=True,
                credential_use=False,
                max_steps=12,
                max_lifetime_seconds=900,
                max_retries=1,
                max_requests_per_minute=12,
                review_required=True,
                archive_behavior="review_then_archive_summary",
            ),
        },
        "liquefy_worker": {
            "AGENT_ROLE.md": (
                "# Liquefy Worker\n\n"
                "Purpose: approved project work, compression/integration support, bounded file operations.\n"
                "Must stay inside approved project/workspace roots.\n"
            ),
            "TOOLS.md": "- bounded shell\n- git status/diff/show\n- python/node project tools within approved roots\n",
            "HEARTBEAT.md": "- verify path scope\n- verify no destructive command\n- stop for escalation\n",
            "POLICY.md": "Broader than research but still path-scoped and approval-gated for destructive or external changes.\n",
            "spawn.json": spawn_policy_fn(
                purpose="project_implementation",
                allowed_tools=["read", "write", "edit", "exec", "diff"],
                allowed_read_roots=["./workspace", "./docs", "./data", "./core", "./apps", "./storage", "./ops", "./tests"],
                allowed_write_roots=["./workspace", "./tmp", "./core", "./apps", "./storage", "./ops", "./tests"],
                shell_allowed=True,
                network_allowed=False,
                credential_use=False,
                max_steps=24,
                max_lifetime_seconds=1800,
                max_retries=1,
                max_requests_per_minute=24,
                review_required=True,
                archive_behavior="review_then_archive_summary",
            ),
        },
        "monitor_worker": {
            "AGENT_ROLE.md": "# Monitor Worker\n\nPurpose: inspect system health, logs, runtime status, and anomaly summaries.\n",
            "TOOLS.md": "- read-only diagnostics\n- log/status inspection\n- no state changes\n",
            "HEARTBEAT.md": "- verify read-only mode\n- capture failures with timestamps\n- no mutating actions\n",
            "POLICY.md": "Read-heavy and conservative. Never change system state without explicit approval.\n",
            "spawn.json": spawn_policy_fn(
                purpose="health_monitoring",
                allowed_tools=["read", "status", "inspect"],
                allowed_read_roots=["./workspace", "./data", "./logs", "./config"],
                allowed_write_roots=["./workspace/logs"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=10,
                max_lifetime_seconds=600,
                max_retries=1,
                max_requests_per_minute=10,
                review_required=False,
                archive_behavior="log_summary_only",
            ),
        },
        "personal_assistant": {
            "AGENT_ROLE.md": "# Personal Assistant\n\nPurpose: general user-facing help, notes, and bounded personal task organization.\n",
            "TOOLS.md": "- low-risk note and summary tools\n- no coding or infra powers by default\n",
            "HEARTBEAT.md": "- keep tone/user context clean\n- avoid coding/infra privileges\n",
            "POLICY.md": "No broad shell, no infra mutation, no credential use by default.\n",
            "spawn.json": spawn_policy_fn(
                purpose="general_assistance",
                allowed_tools=["read", "write_notes", "summarize"],
                allowed_read_roots=["./workspace", "./docs"],
                allowed_write_roots=["./workspace/logs", "./workspace/memory"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=8,
                max_lifetime_seconds=600,
                max_retries=0,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="summary_only",
            ),
        },
        "reviewer": {
            "AGENT_ROLE.md": "# Reviewer\n\nPurpose: validate worker output before durable promotion.\n",
            "TOOLS.md": "- schema validation\n- policy checks\n- no broad execution\n",
            "HEARTBEAT.md": "- verify schema\n- verify policy\n- block on ambiguity\n",
            "POLICY.md": "Reviewer must never approve ambiguous or policy-violating output.\n",
            "spawn.json": spawn_policy_fn(
                purpose="output_review",
                allowed_tools=["read", "validate_schema", "compare", "summarize"],
                allowed_read_roots=["./workspace/control", "./workspace/templates", "./data"],
                allowed_write_roots=["./workspace/control/approvals", "./workspace/control/queue"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=10,
                max_lifetime_seconds=600,
                max_retries=1,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="review_only",
            ),
        },
        "archivist": {
            "AGENT_ROLE.md": "# Archivist\n\nPurpose: compact approved outputs into durable summaries and logs.\n",
            "TOOLS.md": "- summary writing\n- memory compaction\n- no direct risky execution\n",
            "HEARTBEAT.md": "- archive only approved outputs\n- strip transient noise\n- preserve traceability IDs\n",
            "POLICY.md": "Archivist writes summaries only after review passes. No raw dump of transient chatter.\n",
            "spawn.json": spawn_policy_fn(
                purpose="approved_archive",
                allowed_tools=["read", "write_notes", "compact"],
                allowed_read_roots=["./workspace/control", "./workspace/memory", "./workspace/roles"],
                allowed_write_roots=["./workspace/memory", "./workspace/logs", "./workspace/roles"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=12,
                max_lifetime_seconds=900,
                max_retries=1,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="approved_summary_only",
            ),
        },
        "router": {
            "AGENT_ROLE.md": "# Router\n\nPurpose: classify incoming work and map it onto the narrowest safe worker template.\n",
            "TOOLS.md": "- classify\n- route\n- no direct execution\n",
            "HEARTBEAT.md": "- choose narrowest safe template\n- no self-spawn\n- no recursive fanout by default\n",
            "POLICY.md": "Router may propose worker selection but should not bypass reviewer or approval gates.\n",
            "spawn.json": spawn_policy_fn(
                purpose="routing_only",
                allowed_tools=["classify", "route", "summarize"],
                allowed_read_roots=["./workspace/control", "./workspace/templates", "./workspace/core"],
                allowed_write_roots=["./workspace/control/queue"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=6,
                max_lifetime_seconds=300,
                max_retries=0,
                max_requests_per_minute=12,
                review_required=False,
                archive_behavior="none",
            ),
        },
    }


def spawn_policy(
    *,
    purpose: str,
    allowed_tools: list[str],
    allowed_read_roots: list[str],
    allowed_write_roots: list[str],
    shell_allowed: bool,
    network_allowed: bool,
    credential_use: bool,
    max_steps: int,
    max_lifetime_seconds: int,
    max_retries: int,
    max_requests_per_minute: int,
    review_required: bool,
    archive_behavior: str,
) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "allowed_tools": allowed_tools,
        "allowed_read_roots": allowed_read_roots,
        "allowed_write_roots": allowed_write_roots,
        "shell_allowed": shell_allowed,
        "network_allowed": network_allowed,
        "credential_use": credential_use,
        "max_steps": max_steps,
        "max_lifetime_seconds": max_lifetime_seconds,
        "max_retries": max_retries,
        "max_requests_per_minute": max_requests_per_minute,
        "review_required": review_required,
        "archive_behavior": archive_behavior,
        "termination_conditions": [
            "task_complete",
            "timeout",
            "budget_exhausted",
            "policy_violation",
            "approval_required",
        ],
    }


def paths_from_payload(payload: dict[str, Any], *, path_key_pattern: Pattern[str] = _PATH_KEY_RE) -> list[str]:
    paths: set[str] = set()

    def _visit(obj: Any, key: str = "") -> None:
        if isinstance(obj, dict):
            for child_key, child_value in obj.items():
                _visit(child_value, str(child_key))
            return
        if isinstance(obj, list):
            for item in obj:
                _visit(item, key)
            return
        text = str(obj or "").strip()
        if not text:
            return
        if path_key_pattern.search(key) or text.startswith("/") or re.match(r"^[A-Za-z]:\\\\", text):
            paths.add(text)

    _visit(payload)
    return sorted(paths)


__all__ = [
    "paths_from_payload",
    "spawn_policy",
    "template_library",
]
