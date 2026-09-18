"""Fresh-home startup smoke tests.

Regression for the Phase 3 blocker: an agent starting on a brand-new/isolated
runtime home crashed with ``sqlite3.OperationalError: no such table:
runtime_checkpoints`` because ``start()`` queried the runtime-continuity tables
before any migration created them.

Two guards:
  1. the runtime-continuity query path creates its schema on demand, so a
     never-migrated runtime DB does not raise; and
  2. ``VoolAgent.start()`` runs migrations before the stale-checkpoint query.
"""
from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core import runtime_continuity as rc


def test_runtime_continuity_entrypoints_survive_a_fresh_unmigrated_home(tmp_path):
    fresh_db = tmp_path / "fresh_runtime.db"
    original = rc._DB_PATH_OVERRIDE
    rc.configure_runtime_continuity_db_path(fresh_db)
    # a fresh path has never been migrated in this process
    rc._MIGRATED_PATHS.discard(str(rc._runtime_db_path()))
    try:
        # none of these may raise on an empty home
        assert rc.mark_stale_runtime_checkpoints_interrupted() == 0
        checkpoint = rc.create_runtime_checkpoint(
            session_id="smoke", request_text="do a thing", source_context={}
        )
        assert checkpoint
        assert rc.latest_resumable_checkpoint("smoke") is not None
    finally:
        rc.configure_runtime_continuity_db_path(original)


def test_agent_start_runs_migrations_before_the_stale_checkpoint_query():
    order: list[str] = []
    agent = VoolAgent(backend_name="smoke", device="smoke", persona_id="default")
    with (
        mock.patch("core.agent_runtime.agent.setup_logging"),
        mock.patch(
            "core.agent_runtime.agent.run_migrations",
            side_effect=lambda *a, **k: order.append("migrate"),
        ),
        mock.patch(
            "core.agent_runtime.agent.mark_stale_runtime_checkpoints_interrupted",
            side_effect=lambda *a, **k: order.append("stale_check"),
        ),
        mock.patch("core.agent_runtime.agent.ensure_memory_files"),
        mock.patch("core.agent_runtime.agent.load_active_persona"),
        mock.patch.object(VoolAgent, "_sync_public_presence"),
        mock.patch.object(VoolAgent, "_idle_public_presence_status", return_value="idle"),
        mock.patch.object(VoolAgent, "_background_runtime_threads_enabled", return_value=False),
    ):
        runtime = agent.start()

    assert order == ["migrate", "stale_check"], (
        f"migrations must precede the checkpoint query, got {order}"
    )
    assert runtime.backend_name == "smoke"
