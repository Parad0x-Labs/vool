from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from core.brain_hive_watch_config_loader import load_brain_hive_watch_config


class BrainHiveWatchConfigLoaderTests(unittest.TestCase):
    def test_loads_separated_watch_config(self) -> None:
        config = load_brain_hive_watch_config(
            Path("config/meet_clusters/separated_watch_4node/watch-edge-1.json")
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8788)
        self.assertEqual(config.request_timeout_seconds, 6)
        self.assertEqual(len(config.upstream_base_urls), 3)
        self.assertIn("https://meet-eu.parad0xlabs.com", config.upstream_base_urls)
        # Must be the committed env placeholder, never a real secret (scrubbed 2026-06-08).
        self.assertTrue(str(config.auth_token or "").startswith("set-via-env-before-startup"))
        self.assertIsNone(config.tls_certfile)
        self.assertIsNone(config.tls_keyfile)
        self.assertIsNone(config.tls_ca_file)

    @pytest.mark.skipif(
        not Path("config/meet_clusters/do_ip_first_4node/watch-edge-1.json").exists(),
        reason="gitignored config not present",
    )
    def test_loads_ip_first_watch_tls_config(self) -> None:
        config = load_brain_hive_watch_config(
            Path("config/meet_clusters/do_ip_first_4node/watch-edge-1.json")
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8788)
        self.assertIn("https://203.0.113.11:8766", config.upstream_base_urls)
        self.assertTrue(config.tls_insecure_skip_verify)
        self.assertIsNone(config.tls_certfile)
        self.assertIsNone(config.tls_keyfile)
        self.assertIsNone(config.tls_ca_file)

    def test_resolves_relative_tls_paths_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            tls_dir = config_dir / "tls"
            tls_dir.mkdir()
            payload = {
                "host": "0.0.0.0",
                "port": 8788,
                "upstream_base_urls": ["https://seed-eu.example.vool"],
                "tls_certfile": "tls/watch-cert.pem",
                "tls_keyfile": "tls/watch-key.pem",
                "tls_ca_file": "tls/cluster-ca.pem",
            }
            config_path = config_dir / "watch-edge-1.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_brain_hive_watch_config(config_path)

        self.assertEqual(config.tls_certfile, str((tls_dir / "watch-cert.pem").resolve()))
        self.assertEqual(config.tls_keyfile, str((tls_dir / "watch-key.pem").resolve()))
        self.assertEqual(config.tls_ca_file, str((tls_dir / "cluster-ca.pem").resolve()))


if __name__ == "__main__":
    unittest.main()
