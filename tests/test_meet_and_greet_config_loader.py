from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from core.meet_and_greet_config_loader import load_meet_node_config


class MeetAndGreetConfigLoaderTests(unittest.TestCase):
    def test_loads_global_3node_seed_config(self) -> None:
        config = load_meet_node_config(
            Path("config/meet_clusters/global_3node/seed-eu-1.json")
        )

        self.assertEqual(config.node_id, "seed-eu-1")
        self.assertEqual(config.region, "eu")
        self.assertEqual(config.service_config.local_region, "eu")
        self.assertTrue(config.replication_config.cross_region_summary_only)
        # Must be the committed env placeholder, never a real secret (scrubbed 2026-06-08).
        self.assertTrue(str(config.replication_config.auth_token or "").startswith("set-via-env-before-startup"))
        self.assertEqual(len(config.seed_peers), 2)
        self.assertEqual({seed.region for seed in config.seed_peers}, {"us", "apac"})

    @pytest.mark.skipif(
        not Path("config/meet_clusters/do_ip_first_4node/seed-eu-1.json").exists(),
        reason="gitignored config not present",
    )
    def test_loads_do_ip_first_seed_config(self) -> None:
        config = load_meet_node_config(
            Path("config/meet_clusters/do_ip_first_4node/seed-eu-1.json")
        )
        self.assertEqual(config.node_id, "seed-eu-1")
        self.assertEqual(config.region, "eu")
        self.assertEqual(config.bind_port, 8766)
        self.assertEqual(config.public_base_url, "https://203.0.113.11:8766")
        self.assertEqual(config.service_config.local_region, "eu")
        self.assertTrue(bool(str(config.replication_config.auth_token or "").strip()))
        self.assertTrue(config.replication_config.tls_insecure_skip_verify)
        self.assertEqual(len(config.seed_peers), 2)
        self.assertEqual({seed.region for seed in config.seed_peers}, {"us", "apac"})

    def test_resolves_relative_tls_paths_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            tls_dir = config_dir / "tls"
            tls_dir.mkdir()
            payload = {
                "node_id": "seed-eu-1",
                "public_base_url": "https://seed-eu.example.vool",
                "tls_certfile": "tls/node-cert.pem",
                "tls_keyfile": "tls/node-key.pem",
                "tls_ca_file": "tls/cluster-ca.pem",
                "replication_config": {
                    "tls_ca_file": "tls/replication-ca.pem",
                },
                "service_config": {"local_region": "eu"},
            }
            config_path = config_dir / "seed-eu-1.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_meet_node_config(config_path)

        self.assertEqual(config.tls_certfile, str((tls_dir / "node-cert.pem").resolve()))
        self.assertEqual(config.tls_keyfile, str((tls_dir / "node-key.pem").resolve()))
        self.assertEqual(config.tls_ca_file, str((tls_dir / "cluster-ca.pem").resolve()))
        self.assertEqual(
            config.replication_config.tls_ca_file,
            str((tls_dir / "replication-ca.pem").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
