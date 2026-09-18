import uuid
from datetime import datetime
from typing import Any

from core.knowledge_registry import register_local_shard
from core.task_router import redact_text
from storage.db import execute_query


class MemoryManager:
    """
    Manages three distinct layers of memory:
    1) Short-term context (current task loop)
    2) Local long-term memory (saved tasks logic)
    3) Shared memory cache (accepted swarm shards stored locally)

    Ensures that raw task transcripts are NEVER accidentally merged into shared shards.
    """

    def __init__(self):
        # Short term transient memory exists only in memory for the run
        self.short_term: list[dict[str, Any]] = []

    def log_short_term(self, role: str, content: str):
        """Append to the current task execution context."""
        self.short_term.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

    def get_short_term_context(self) -> str:
        """Returns the formatted transcript of the current run."""
        output = []
        for msg in self.short_term[-10:]:  # Keep recent context window
            output.append(f"{msg['role'].upper()}: {msg['content'][:1500]}")
        return "\n".join(output)

    def save_local_task(self, task_class: str, summary: str, env_data: dict, outcome: str, confidence: float) -> str:
        """
        Saves a resolved task into local long-term memory.
        This table is private to the node and stores specific execution data.
        """
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        redacted = redact_text(summary)
        execute_query("""
            INSERT INTO local_tasks (
                task_id, session_id, task_class, task_summary, redacted_input_hash,
                environment_os, environment_runtime, environment_version_hint,
                plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            ) VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, 'local_only', ?, ?, 0, ?, ?)
        """, (
            task_id, task_class, redacted[:240], uuid.uuid4().hex,
            env_data.get("os", "unknown"),
            env_data.get("runtime", "unknown"),
            env_data.get("version", ""),
            "autonomous", confidence, outcome, now, now
        ))
        return task_id

    def load_relevant_local_memory(self, task_class: str) -> list[dict]:
        """Looks up similar local tasks to assist reasoning engine."""
        rows = execute_query(
            "SELECT * FROM local_tasks WHERE task_class = ? ORDER BY created_at DESC LIMIT 5",
            (task_class,)
        )
        return rows

    def cache_swarm_shard(self, shard: dict):
        """
        Stores an accepted shard from the network locally according to Principle B.
        Enforces schema requirements and strips out PII fields conceptually.
        """
        now = datetime.now().isoformat()
        execute_query("""
            INSERT OR REPLACE INTO learning_shards (
                shard_id, schema_version, problem_class, problem_signature,
                summary, resolution_pattern_json, environment_tags_json,
                source_type, source_node_id, quality_score, trust_score,
                local_validation_count, local_failure_count, quarantine_status,
                risk_flags_json, freshness_ts, expires_ts, signature,
                origin_task_id, origin_session_id, share_scope, restricted_terms_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'public_knowledge', '[]', ?, ?)
        """, (
            shard.get("shard_id"),
            shard.get("schema_version", 1),
            shard.get("problem_class", "unknown"),
            shard.get("problem_signature", ""),
            shard.get("summary", ""),
            shard.get("resolution_pattern_json", "[]"),
            shard.get("environment_tags_json", "{}"),
            shard.get("source_type", "local_generated"),
            shard.get("source_node_id"),
            shard.get("quality_score", 0.5),
            shard.get("trust_score", 0.5),
            shard.get("local_validation_count", 0),
            shard.get("local_failure_count", 0),
            shard.get("quarantine_status", "active"),
            shard.get("risk_flags_json", "[]"),
            shard.get("freshness_ts", now),
            shard.get("expires_ts"),
            shard.get("signature"),
            now,
            now
        ))
        if shard.get("shard_id"):
            register_local_shard(str(shard["shard_id"]))
