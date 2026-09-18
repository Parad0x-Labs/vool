from __future__ import annotations

from typing import Any


def schema_library() -> dict[str, dict[str, Any]]:
    return {
        "task-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["task_id", "summary", "status", "created_at", "updated_at"],
            "properties": {
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "string"},
                "created_at": {"type": "string"},
                "updated_at": {"type": "string"},
            },
        },
        "lease-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["assignment_id", "task_id", "helper_peer_id", "status", "assigned_at"],
            "properties": {
                "assignment_id": {"type": "string"},
                "task_id": {"type": "string"},
                "helper_peer_id": {"type": "string"},
                "status": {"type": "string"},
                "lease_expires_at": {"type": ["string", "null"]},
            },
        },
        "run-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["session_id", "status", "request_preview", "started_at", "updated_at"],
            "properties": {
                "session_id": {"type": "string"},
                "status": {"type": "string"},
                "request_preview": {"type": "string"},
                "task_class": {"type": "string"},
                "tool_receipts": {"type": "array"},
                "touched_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
        "approval-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["action_id", "session_id", "action_kind", "status", "created_at"],
            "properties": {
                "action_id": {"type": "string"},
                "session_id": {"type": "string"},
                "action_kind": {"type": "string"},
                "status": {"type": "string"},
                "scope": {"type": "object"},
            },
        },
        "budget-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["generated_at", "day_bucket", "items"],
            "properties": {
                "generated_at": {"type": "string"},
                "day_bucket": {"type": "string"},
                "items": {"type": "array"},
            },
        },
        "review-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["lane", "items"],
            "properties": {
                "lane": {"type": "string"},
                "items": {"type": "array"},
                "review_required": {"type": "boolean"},
            },
        },
    }


__all__ = ["schema_library"]
