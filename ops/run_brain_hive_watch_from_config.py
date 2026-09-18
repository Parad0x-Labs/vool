"""Launch a brain-hive watch edge from a cluster JSON config.

    python3 ops/run_brain_hive_watch_from_config.py --config <path-to-watch.json>

Referenced by ops/do_ip_first_bootstrap.sh and the config/meet_clusters/*/README.md
runbooks. Parses the watch config via core.brain_hive_watch_config_loader and
serves the read-only watch edge (uvicorn) until interrupted.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running as a file from any cwd by putting the repo root on sys.path
# before importing apps.*/core.*.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from apps.brain_hive_watch_server import serve
from core.brain_hive_watch_config_loader import load_brain_hive_watch_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="run-brain-hive-watch-from-config")
    parser.add_argument("--config", required=True, help="path to the watch edge JSON config")
    args = parser.parse_args()

    config = load_brain_hive_watch_config(args.config)
    print(
        f"watch edge '{getattr(config, 'node_id', 'watch')}' serving on "
        f"{config.host}:{config.port}",
        flush=True,
    )
    serve(config)  # blocks on uvicorn; handles its own SIGINT/SIGTERM
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
