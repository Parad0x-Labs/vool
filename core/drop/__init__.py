"""VOOL Drop: private VOOL-to-VOOL handoffs. Off by default.

UNREVIEWED: not for real messages. The DROP-HYBRID-1 session protocol has had no
independent cryptographic review; keep ``VOOL_DROP`` unset outside testing.
"""

from core.drop.session import DropError

WARNING = "UNREVIEWED: not for real messages"

__all__ = ["WARNING", "DropError", "enabled", "require_enabled"]


def enabled() -> bool:
    from core.drop.settings import load

    return load()["enabled"]


def require_enabled():
    if not enabled():
        raise DropError("DROP_DISABLED")
