"""Launch a meet-and-greet node from a cluster JSON config.

    python3 ops/run_meet_node_from_config.py --config <path-to-node.json>

Referenced by ops/do_ip_first_bootstrap.sh and the config/meet_clusters/*/README.md
runbooks. Parses the full node config — service_config, replication_config,
seed_peers, TLS — via core.meet_and_greet_config_loader (no field is dropped),
builds a MeetAndGreetNode, serves it, and runs until SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

# Allow running as a file (`python3 ops/run_meet_node_from_config.py`) from any
# cwd by putting the repo root on sys.path before importing apps.*/core.*.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from apps.meet_and_greet_node import MeetAndGreetNode
from core.meet_and_greet_config_loader import load_meet_node_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="run-meet-node-from-config")
    parser.add_argument("--config", required=True, help="path to the meet node JSON config")
    args = parser.parse_args()

    config = load_meet_node_config(args.config)
    node = MeetAndGreetNode(config)
    node.start()
    print(
        f"meet node '{config.node_id}' serving on "
        f"{config.bind_host}:{config.bind_port} (region={config.region}, role={config.role})",
        flush=True,
    )

    stop_event = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    try:
        stop_event.wait()
    finally:
        node.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
