"""R-9b/H-7: the idle-commons lane traverses A2 admission + A7 finalization.

Driven through the REAL `maybe_run_idle_commons_once` with a stubbed bridge;
asserts a durable a7_finalizations row exists for the published body and that
the audit trail carries its finalization_id. Red mutation: bypassing the
seal/finalize leaves zero rows.
"""
from __future__ import annotations

import storage.db as sdb


class _Prefs:
    social_commons = True


def _agent_stub():
    from threading import Lock

    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    return SimpleNamespaceWith(
        curiosity=_Curiosity(),
        public_hive_bridge=_Bridge(),
        hive_activity_tracker=_Tracker(),
        _activity_lock=Lock(),
        _last_user_activity_ts=0.0,
        _last_idle_commons_ts=0.0,
        _idle_commons_seed_index=0,
        _idle_commons_session_id=lambda: "session-idle-commons",
    )


class SimpleNamespaceWith:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Curiosity:
    def run_idle_commons(self, *, session_id, task_id, trace_id, seed_index):
        return {
            "topic": {"topic": "t", "topic_kind": "technical"},
            "summary": "summary",
            "public_body": "public commons body",
            "topic_tags": ["a"],
            "candidate_id": "c1",
        }


class _Bridge:
    def __init__(self):
        self.published = None

    def publish_agent_commons_update(self, **kw):
        self.published = kw
        return {"topic_id": "topic-1", "status": "published"}


class _Tracker:
    def note_watched_topic(self, **kw):
        pass


def test_idle_commons_publishes_only_sealed_bytes(fresh_store=None):
    import time as _time

    from core.agent_runtime.presence import maybe_run_idle_commons_once

    if fresh_store is None:
        import tempfile

        from core.runtime_continuity import configure_runtime_continuity_db_path
        from storage.db import active_default_db_path, configure_default_db_path

        configure_default_db_path(
            __import__("pathlib").Path(tempfile.mkdtemp()) / "r9b.db"
        )
        from storage.migrations import run_migrations

        run_migrations()
        configure_runtime_continuity_db_path(active_default_db_path())

    agent = _agent_stub()
    audits: list[dict] = []
    maybe_run_idle_commons_once(
        agent,
        load_preferences_fn=lambda: _Prefs(),
        time_fn=lambda: _time.time() + 10_000.0,
        audit_log_fn=lambda *a, **k: audits.append(k.get("details") or {}),
    )
    published_body = str((agent.public_hive_bridge.published or {}).get("public_body") or "")
    assert published_body == "public commons body"
    # A7 row exists for the admitted sr with exactly these bytes.
    conn = sdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT canonical_content, status FROM a7_finalizations WHERE canonical_content = ?",
            (published_body,),
        ).fetchall()
        assert rows, "idle-commons body must be finalized (A2 -> A7) before publish"
    finally:
        conn.close()
    audit_details = audits[-1] if audits else {}
    assert audit_details.get("finalization_id")
