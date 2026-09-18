"""`ctx` for the blackbox coverage tests, in its own module.

`from conftest import ctx` resolved to whichever `conftest` pytest had imported first as a top-level
module -- in a shard that also collected tests/wallet, that was tests/wallet/conftest.py, and eight
files here failed collection (2026-09-07 cross-lane shard run). A conftest is pytest's, not an import
target; the helper lives here and is imported by package path.
"""
from __future__ import annotations

from pathlib import Path


def ctx(workspace: Path, session: str = "cov-sess", turn: str = "cov-turn", **extra) -> dict:
    from core.mode_permission_policy import set_active_mode

    set_active_mode(session, "auto")
    context = {
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "session_id": session,
        "surface": "api",
        "operating_mode": "auto",
    }
    context.update(extra)
    if "turn_id" not in context:
        context["turn_id"] = turn
    return context
