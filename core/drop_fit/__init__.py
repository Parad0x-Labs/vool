"""Default-off local Drop experiment. UNREVIEWED: not for real messages."""

import os

WARNING = "UNREVIEWED: not for real messages"


class DropFitError(RuntimeError):
    """A bounded refusal code; never include decrypted content or keys."""


def require_enabled():
    if os.environ.get("VOOL_DROP_FIT") != "1":
        raise DropFitError("DROP_FIT_DISABLED")
