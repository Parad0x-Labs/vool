from __future__ import annotations

import json
from collections import Counter
from typing import Any

from core.identity_manager import load_active_persona
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.dialogue_memory import recent_dialogue_turns_any
from storage.knowledge_index import active_presence
from storage.knowledge_manifests import all_manifests
from storage.replica_table import all_holders
from storage.swarm_memory import get_recent_contexts
from storage.useful_output_store import sync_useful_outputs

_OUTBOUND_SENT_EVENTS = {
    "hello_ad_broadcast",
    "presence_heartbeat_broadcast",
    "knowledge_ad_broadcast",
    "capability_ad_broadcast",
    "query_shard_dispatched",
    "task_offer_broadcast",
    "credit_offer_broadcast",
}


def build_user_summary(*, limit_recent: int = 5) -> dict[str, Any]:
    local_peer_id = get_local_peer_id()
    persona = load_active_persona("default")
    useful_output_summary = sync_useful_outputs()
    conn = get_connection()
    try:
        learning_columns = _table_columns(conn, "learning_shards")
        candidate_columns = _table_columns(conn, "candidate_knowledge_lane")
        share_scope_supported = "share_scope" in learning_columns
        artifact_lane_supported = _table_exists(conn, "artifact_manifests")
        learning_rows = [dict(row) for row in conn.execute(
            """
            SELECT shard_id, problem_class, summary, source_type, quality_score, trust_score, freshness_ts, updated_at
            FROM learning_shards
            ORDER BY updated_at DESC
            """
        ).fetchall()]
        task_rows = [dict(row) for row in conn.execute(
            """
            SELECT task_id, task_class, task_summary, outcome, confidence, updated_at
            FROM local_tasks
            ORDER BY updated_at DESC
            LIMIT 50
            """
        ).fetchall()]
        final_rows = [dict(row) for row in conn.execute(
            """
            SELECT parent_task_id, rendered_persona_text, status_marker, confidence_score,
                   content_hash, created_at
            FROM finalized_responses
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit_recent,),
        ).fetchall()]
        review_count = int((conn.execute("SELECT COUNT(*) AS cnt FROM task_reviews").fetchone() or {"cnt": 0})["cnt"])
        result_count = int((conn.execute("SELECT COUNT(*) AS cnt FROM task_results").fetchone() or {"cnt": 0})["cnt"])
        local_task_count = int((conn.execute("SELECT COUNT(*) AS cnt FROM local_tasks").fetchone() or {"cnt": 0})["cnt"])
        outbound_audit = [dict(row) for row in conn.execute(
            """
            SELECT event_type, details_json, created_at
            FROM audit_log
            WHERE event_type IN ({})
            ORDER BY created_at DESC
            LIMIT 500
            """.format(",".join("?" for _ in _OUTBOUND_SENT_EVENTS)),
            tuple(sorted(_OUTBOUND_SENT_EVENTS)),
        ).fetchall()]
        inbound_audit = [dict(row) for row in conn.execute(
            """
            SELECT event_type, details_json, created_at
            FROM audit_log
            WHERE event_type IN ('credit_transfer_received', 'remote_task_reward_seen')
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall()]
        share_scope_counts = _learning_share_scope_counts(conn) if share_scope_supported else {}
        candidate_rows = _count_table_rows(
            conn,
            "candidate_knowledge_lane",
            where_clause="invalidated_at IS NULL" if "invalidated_at" in candidate_columns else None,
        )
        artifact_rows = _count_table_rows(conn, "artifact_manifests") if artifact_lane_supported else 0
    finally:
        conn.close()

    presence = active_presence(limit=256)
    manifests = all_manifests(limit=1000)
    holders = all_holders(limit=2000)
    sniffed_contexts = get_recent_contexts(limit=limit_recent)
    dialogue_turns = recent_dialogue_turns_any(limit=limit_recent)

    local_learning = [row for row in learning_rows if str(row.get("source_type")) == "local_generated"]
    peer_learning = [row for row in learning_rows if str(row.get("source_type")) == "peer_received"]
    web_learning = [row for row in learning_rows if str(row.get("source_type")) == "web_derived"]

    own_holders = [row for row in holders if row["holder_peer_id"] == local_peer_id]
    remote_holders = [row for row in holders if row["holder_peer_id"] != local_peer_id]
    own_shard_ids = {row["shard_id"] for row in own_holders}
    remote_shard_ids = {row["shard_id"] for row in remote_holders}

    problem_counter = Counter(str(row.get("problem_class") or "unknown") for row in learning_rows)
    topic_counter = Counter()
    for manifest in manifests:
        for tag in manifest.get("topic_tags") or []:
            topic_counter[str(tag)] += 1

    sent_messages_estimate = 0
    sent_breakdown: Counter[str] = Counter()
    for row in outbound_audit:
        event_type = str(row["event_type"])
        details = _safe_json(row.get("details_json"))
        if "sent" in details:
            count = int(details.get("sent") or 0)
        elif event_type == "request_shard_sent":
            count = 1 if bool(details.get("ok")) else 0
        else:
            count = 1
        sent_messages_estimate += max(0, count)
        sent_breakdown[event_type] += max(0, count)

    received_artifacts = {
        "peer_received_learning_shards": len(peer_learning),
        "remote_holder_records": len(remote_holders),
        "sniffed_context_rows": len(sniffed_contexts),
        "task_results_stored": result_count,
        "task_reviews_stored": review_count,
        "credit_transfer_events": sum(1 for row in inbound_audit if row["event_type"] == "credit_transfer_received"),
    }

    recent_learning = [
        {
            "shard_id": row["shard_id"],
            "problem_class": row["problem_class"],
            "summary": _trim(str(row.get("summary") or ""), 140),
            "source_type": row["source_type"],
            "quality_score": float(row.get("quality_score") or 0.0),
        }
        for row in learning_rows[:limit_recent]
    ]

    recent_final_responses = [
        {
            "task_id": row["parent_task_id"],
            "status": row["status_marker"],
            "confidence": float(row.get("confidence_score") or 0.0),
            "preview": _trim(str(row.get("rendered_persona_text") or ""), 160),
            # A7 W5 (S14): user-visible swarm summaries are DERIVED-DISPLAY —
            # persona-rendered artifacts bound to the durable content identity
            # of the immutable finalized row they were rendered from.
            "a7": {
                "status": "derived_display",
                "content_hash": str(row.get("content_hash") or ""),
            },
            "created_at": row["created_at"],
        }
        for row in final_rows
    ]

    recent_mesh_learning = [
        {
            "from_peer_id": row.get("parent_peer_id"),
            "prompt_preview": _trim(_extract_prompt_summary(row.get("prompt_json")), 120),
            "result_preview": _trim(_extract_result_summary(row.get("result_json")), 120),
            "timestamp": row.get("timestamp"),
        }
        for row in sniffed_contexts[:limit_recent]
    ]

    recent_dialogue = [
        {
            "raw_input": _trim(str(row.get("raw_input") or ""), 120),
            "reconstructed_input": _trim(str(row.get("reconstructed_input") or ""), 140),
            "understanding_confidence": float(row.get("understanding_confidence") or 0.0),
            "topic_hints": list(row.get("topic_hints") or []),
        }
        for row in dialogue_turns
    ]

    private_store_shards = int(share_scope_counts.get("local_only") or 0) if share_scope_supported else 0
    shareable_store_shards = (
        sum(int(count or 0) for scope, count in share_scope_counts.items() if str(scope or "").strip() != "local_only")
        if share_scope_supported
        else 0
    )
    legacy_unscoped_store_shards = len(learning_rows) if not share_scope_supported else 0

    return {
        "identity": {
            "peer_id": local_peer_id,
            "persona_id": persona.persona_id,
            "display_name": persona.display_name,
            "tone": persona.tone,
        },
        "learning": {
            "total_learning_shards": len(learning_rows),
            "local_generated_shards": len(local_learning),
            "peer_received_shards": len(peer_learning),
            "web_derived_shards": len(web_learning),
            "useful_output_count": int(useful_output_summary.get("total_count") or 0),
            "training_eligible_output_count": int(useful_output_summary.get("training_eligible_count") or 0),
            "high_signal_output_count": int(useful_output_summary.get("high_signal_count") or 0),
            "top_problem_classes": [{"problem_class": key, "count": count} for key, count in problem_counter.most_common(5)],
            "top_topic_tags": [{"tag": key, "count": count} for key, count in topic_counter.most_common(8)],
            "recent_learning": recent_learning,
        },
        "knowledge_lanes": {
            "private_store_shards": private_store_shards,
            "shareable_store_shards": shareable_store_shards,
            "legacy_unscoped_store_shards": legacy_unscoped_store_shards,
            "candidate_rows": int(candidate_rows),
            "artifact_manifests": int(artifact_rows),
            "mesh_manifests": len(manifests),
            "own_mesh_manifests": len(own_shard_ids),
            "remote_mesh_manifests": len(remote_shard_ids),
            "share_scope_supported": bool(share_scope_supported),
            "artifact_lane_supported": bool(artifact_lane_supported),
        },
        "mesh_index": {
            "active_peers": len(presence),
            "knowledge_manifests": len(manifests),
            "own_indexed_shards": len(own_shard_ids),
            "remote_indexed_shards": len(remote_shard_ids),
            "own_holder_records": len(own_holders),
            "remote_holder_records": len(remote_holders),
        },
        "data_flow": {
            "outbound_messages_estimate": sent_messages_estimate,
            "outbound_breakdown": dict(sent_breakdown),
            "received_artifacts": received_artifacts,
            "telemetry_note": "Outbound counts are based on persisted audit events and are conservative summaries, not packet-perfect byte accounting.",
        },
        "memory": {
            "local_task_count": local_task_count,
            "finalized_response_count": len(final_rows),
            "mesh_learning_rows": len(sniffed_contexts),
            "useful_output_count": int(useful_output_summary.get("total_count") or 0),
            "training_eligible_output_count": int(useful_output_summary.get("training_eligible_count") or 0),
            "archive_candidate_count": int(useful_output_summary.get("archive_candidate_count") or 0),
            "recent_mesh_learning": recent_mesh_learning,
            "recent_final_responses": recent_final_responses,
            "recent_dialogue_interpretations": recent_dialogue,
            "recent_tasks": [
                {
                    "task_id": row["task_id"],
                    "task_class": row["task_class"],
                    "outcome": row["outcome"],
                    "confidence": float(row.get("confidence") or 0.0),
                    "summary": _trim(str(row.get("task_summary") or ""), 120),
                }
                for row in task_rows[:limit_recent]
            ],
        },
    }


def render_user_summary(report: dict[str, Any]) -> str:
    identity = report["identity"]
    learning = report["learning"]
    knowledge = report.get("knowledge_lanes") or {}
    mesh = report["mesh_index"]
    flow = report["data_flow"]
    memory = report["memory"]

    lines = [
        "======================================",
        "VOOL MEMORY AND MESH SUMMARY",
        "======================================",
        "",
        "[IDENTITY]",
        f"Name            : {identity['display_name']}",
        f"Persona         : {identity['persona_id']}",
        f"Tone            : {identity['tone']}",
        f"Peer ID         : {identity['peer_id'][:24]}...",
        "",
        "[WHAT VOOL LEARNED]",
        f"Total Shards    : {learning['total_learning_shards']}",
        f"Own Shards      : {learning['local_generated_shards']}",
        f"From Mesh       : {learning['peer_received_shards']}",
        f"From Web        : {learning['web_derived_shards']}",
        f"Top Classes     : {_join_counts(learning['top_problem_classes'], 'problem_class')}",
        f"Top Topics      : {_join_counts(learning['top_topic_tags'], 'tag')}",
        "",
        "[MESH INDEX]",
        f"Active Peers    : {mesh['active_peers']}",
        f"Knowledge Index : {mesh['knowledge_manifests']} manifests",
        f"Own Indexed     : {mesh['own_indexed_shards']} shards",
        f"Remote Indexed  : {mesh['remote_indexed_shards']} shards",
        f"Own Holders     : {mesh['own_holder_records']}",
        f"Remote Holders  : {mesh['remote_holder_records']}",
        "",
        "[KNOWLEDGE LANES]",
        f"Private Store   : {int(knowledge.get('private_store_shards') or 0)} shards",
        f"Shareable Store : {int(knowledge.get('shareable_store_shards') or 0)} shards",
        f"Legacy Unscoped : {int(knowledge.get('legacy_unscoped_store_shards') or 0)} shards",
        f"Candidate Lane  : {int(knowledge.get('candidate_rows') or 0)} rows",
        f"Artifact Packs  : {int(knowledge.get('artifact_manifests') or 0)} manifests",
        f"Mesh Manifests  : {int(knowledge.get('mesh_manifests') or 0)}",
        "",
        "[DATA FLOW]",
        f"Sent Estimate   : {flow['outbound_messages_estimate']}",
        f"Sent Breakdown  : {_join_dict(flow['outbound_breakdown'])}",
        f"Received        : {_join_dict(flow['received_artifacts'])}",
        f"Telemetry Note  : {flow['telemetry_note']}",
        "",
        "[RECENT LEARNING]",
    ]
    lines.extend(_render_recent_learning(learning["recent_learning"]))
    lines.extend(
        [
            "",
            "[RECENT FINAL RESPONSES]",
        ]
    )
    lines.extend(_render_recent_responses(memory["recent_final_responses"]))
    lines.extend(
        [
            "",
            "[MESH LEARNING]",
        ]
    )
    lines.extend(_render_recent_mesh_learning(memory["recent_mesh_learning"]))
    return "\n".join(lines)


def _safe_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _trim(text: str, limit: int) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) if "name" in row else str(row[1]) for row in rows}


def _count_table_rows(conn: Any, table_name: str, where_clause: str | None = None) -> int:
    if not _table_exists(conn, table_name):
        return 0
    query = f"SELECT COUNT(*) AS cnt FROM {table_name}"
    if where_clause:
        query += f" WHERE {where_clause}"
    row = conn.execute(query).fetchone()
    return int((row["cnt"] if row else 0) or 0)


def _learning_share_scope_counts(conn: Any) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT COALESCE(share_scope, 'local_only') AS share_scope, COUNT(*) AS cnt
        FROM learning_shards
        GROUP BY COALESCE(share_scope, 'local_only')
        """
    ).fetchall()
    return {str(row["share_scope"] or "local_only"): int(row["cnt"] or 0) for row in rows}


def _extract_prompt_summary(raw_json: str | None) -> str:
    data = _safe_json(raw_json)
    if isinstance(data, dict):
        if "summary" in data:
            return str(data["summary"])
        if "problem_class" in data:
            return f"{data.get('problem_class')}"
    return str(data)


def _extract_result_summary(raw_json: str | None) -> str:
    data = _safe_json(raw_json)
    if isinstance(data, dict):
        if "summary" in data:
            return str(data["summary"])
        if "result_type" in data:
            return f"{data.get('result_type')}"
    return str(data)


def _join_counts(rows: list[dict[str, Any]], key_name: str) -> str:
    if not rows:
        return "none yet"
    return ", ".join(f"{row[key_name]} ({row['count']})" for row in rows)


def _join_dict(values: dict[str, Any]) -> str:
    if not values:
        return "none yet"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _render_recent_learning(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- No learned shards yet."]
    return [
        f"- [{row['source_type']}] {row['problem_class']}: {row['summary']} (q={row['quality_score']:.2f})"
        for row in rows
    ]


def _render_recent_responses(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- No finalized responses yet."]
    return [
        f"- {row['status']} @ {row['confidence']:.2f}: {row['preview']}"
        for row in rows
    ]


def _render_recent_mesh_learning(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- No mesh-learned context captured yet."]
    return [
        f"- from {row['from_peer_id']}: prompt={row['prompt_preview']} | result={row['result_preview']}"
        for row in rows
    ]
