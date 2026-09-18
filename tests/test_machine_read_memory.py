"""The last grounded machine read must survive a server restart so an elliptical follow-up
("ok what about D?", "biggest file on that drive") re-runs the REAL tool instead of falling through
to the model — which is what re-opened the D-drive fabrication. Persistence closes that edge; the
in-process cache alone is dropped on restart.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.agent_runtime.fast_paths_machine as fp
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    recall_machine_read,
    remember_machine_read,
)


class MachineReadMemoryPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        configure_runtime_continuity_db_path(str(Path(self._tmp.name) / "runtime.db"))
        fp.reset_machine_followup_state()

    def tearDown(self) -> None:
        configure_runtime_continuity_db_path(None)
        fp.reset_machine_followup_state()
        self._tmp.cleanup()

    def test_persisted_read_roundtrips(self) -> None:
        remember_machine_read("openclaw:s1", kind="disk", drive="D:\\", source_turn_id="t1")
        self.assertEqual(
            recall_machine_read("openclaw:s1"),
            {"kind": "disk", "drive": "D:\\", "source_turn_id": "t1"},
        )

    def test_upsert_overwrites_prior_read_for_the_session(self) -> None:
        remember_machine_read("openclaw:s1", kind="disk", drive="C:\\")
        remember_machine_read("openclaw:s1", kind="largest", drive="D:\\")
        row = recall_machine_read("openclaw:s1")
        assert row is not None
        self.assertEqual((row["kind"], row["drive"]), ("largest", "D:\\"))

    def test_fast_path_recall_falls_back_to_db_after_cache_clear(self) -> None:
        # Write through the fast-path helper, then simulate a restart by clearing the in-process cache.
        fp._remember_machine_read("openclaw:s2", kind="disk", drive="D:\\", turn_id="t2")
        fp.reset_machine_followup_state()  # cache gone == process restarted
        self.assertEqual(fp._recall_machine_read("openclaw:s2"), {"kind": "disk", "drive": "D:\\"})

    def test_unknown_session_returns_none(self) -> None:
        self.assertIsNone(recall_machine_read("openclaw:nope"))
        self.assertIsNone(fp._recall_machine_read("openclaw:nope"))


if __name__ == "__main__":
    unittest.main()
