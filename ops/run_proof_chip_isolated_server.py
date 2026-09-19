"""Boot the chat API from THIS worktree on an isolated port + isolated VOOL_HOME.

Proof-rig launcher for the inline Proof Chip lane: the production `apps.vool_api_server`
bootstrap (the one that creates the agent, plugins and runtime services), pointed at a
throwaway runtime home so nothing here touches the operator's daemon, its stores, or its ports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("PROOF_HOME") or "/tmp/proof-chip-home-20260902")
PORT = int(os.environ.get("PROOF_PORT") or "11577")


def main() -> int:
    sys.path.insert(0, str(WORKTREE))
    os.environ["VOOL_HOME"] = str(HOME)
    # Proof-rig survival: the operator's parallel test sweeps SIGTERM every api-server-shaped
    # process they find. Under PROOF_IGNORE_TERM=1 this rig ignores TERM/INT (SIGKILL still
    # ends it) so the browser proof survives neighbouring teardowns.
    if str(os.environ.get("PROOF_IGNORE_TERM") or "") == "1":
        import signal

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    configure_runtime_home(HOME)
    configure_default_db_path(HOME / "data" / "vool_web0_v2.db")

    # The production boot path: runtime services (including the agent) + the real app factory.
    from apps.vool_api_server import _bootstrap, create_app

    runtime = _bootstrap(run_prewarm=False)

    import uvicorn

    app = create_app(runtime)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
