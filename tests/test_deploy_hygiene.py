from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_do_ip_first_bootstrap_uses_canonical_watch_config() -> None:
    script = (PROJECT_ROOT / "ops" / "do_ip_first_bootstrap.sh").read_text(encoding="utf-8")

    assert "/tmp/watch-http.json" not in script
    assert "/var/lib/vool/watch-edge-1/watch-edge-config.json" in script
    assert "/opt/Decentralized_VOOL/ops/run_brain_hive_watch_from_config.py" in script
    assert "--config /var/lib/vool/watch-edge-1/watch-edge-config.json" in script
    assert "PYTHONPATH=/opt/Decentralized_VOOL VOOL_HOME=/var/lib/vool/watch-edge-1" in script


def test_checked_in_watch_edge_config_stays_as_cluster_template() -> None:
    payload = json.loads(
        (
            PROJECT_ROOT
            / "config"
            / "meet_clusters"
            / "separated_watch_4node"
            / "watch-edge-1.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert payload["public_url"] == "https://hive.parad0xlabs.com"
    assert payload["bind_host"] == "0.0.0.0"
    assert payload["bind_port"] == 8788
