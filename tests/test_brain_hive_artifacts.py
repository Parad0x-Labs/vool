from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.brain_hive_artifacts import (
    count_artifact_manifests,
    get_artifact_manifest,
    search_artifact_manifests,
    store_artifact_manifest,
)
from storage.db import get_connection
from storage.migrations import run_migrations


class BrainHiveArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM artifact_manifests")
            conn.commit()
        finally:
            conn.close()

    def test_store_and_search_artifact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            manifest = store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle",
                summary="Compressed bundle for manual trader heuristics.",
                payload={"queries": ["best way to research mooners"], "heuristics": [{"metric": "max_price_change"}]},
                topic_id="topic-1",
                tags=["trading", "research"],
            )
            self.assertTrue(Path(manifest["file_path"]).exists())

        self.assertEqual(count_artifact_manifests(topic_id="topic-1"), 1)
        fetched = get_artifact_manifest(manifest["artifact_id"])
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["source_kind"], "research_bundle")
        matches = search_artifact_manifests("mooners", topic_id="topic-1", limit=5)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["artifact_id"], manifest["artifact_id"])


if __name__ == "__main__":
    unittest.main()
