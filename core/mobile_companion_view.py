from __future__ import annotations

from typing import Any

from core.vool_user_summary import build_user_summary


def build_mobile_companion_snapshot(*, limit_recent: int = 5) -> dict[str, Any]:
    summary = build_user_summary(limit_recent=limit_recent)
    return {
        "identity": summary["identity"],
        "privacy_mode": "metadata_first",
        "archive_included": False,
        "remote_payloads_included": False,
        "mesh_overview": {
            "active_peers": int(summary["mesh_index"]["active_peers"]),
            "knowledge_manifests": int(summary["mesh_index"]["knowledge_manifests"]),
            "remote_indexed_shards": int(summary["mesh_index"]["remote_indexed_shards"]),
        },
        "recent_activity": {
            "recent_tasks": list(summary["memory"]["recent_tasks"])[:limit_recent],
            "recent_final_responses": list(summary["memory"]["recent_final_responses"])[:limit_recent],
            "recent_learning": list(summary["learning"]["recent_learning"])[:limit_recent],
        },
        "top_tags": list(summary["learning"]["top_topic_tags"])[:6],
        "telemetry_note": summary["data_flow"]["telemetry_note"],
    }


def render_mobile_companion_snapshot(snapshot: dict[str, Any]) -> str:
    identity = snapshot["identity"]
    mesh = snapshot["mesh_overview"]
    activity = snapshot["recent_activity"]

    lines = [
        "VOOL MOBILE COMPANION SNAPSHOT",
        "",
        f"Name: {identity['display_name']}",
        f"Persona: {identity['persona_id']}",
        f"Privacy: {snapshot['privacy_mode']}",
        "",
        "Mesh Overview:",
        f"- Active peers: {mesh['active_peers']}",
        f"- Knowledge manifests: {mesh['knowledge_manifests']}",
        f"- Remote indexed shards: {mesh['remote_indexed_shards']}",
        "",
        "Recent Tasks:",
    ]
    for item in activity["recent_tasks"]:
        lines.append(f"- {item['task_class']}: {item['summary']}")
    lines.extend(["", "Recent Responses:"])
    for item in activity["recent_final_responses"]:
        lines.append(f"- {item['status']}: {item['preview']}")
    lines.extend(["", "Recent Learning:"])
    for item in activity["recent_learning"]:
        lines.append(f"- {item['problem_class']}: {item['summary']}")
    return "\n".join(lines)
