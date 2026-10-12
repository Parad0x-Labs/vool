"""VOOL Drop: private VOOL-to-VOOL handoffs. Off by default.

UNREVIEWED: not for real messages. The DROP-HYBRID-1 session protocol has had no
independent cryptographic review; keep ``VOOL_DROP`` unset outside testing.
"""

import os

from core.drop.session import DropError

WARNING = "UNREVIEWED: not for real messages"

__all__ = ["WARNING", "DropError", "enabled", "require_enabled"]


def enabled() -> bool:
    return os.environ.get("VOOL_DROP") == "1"


def require_enabled():
    if not enabled():
        raise DropError("DROP_DISABLED")
