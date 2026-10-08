"""Subject withdrawals carried by the durable forget authority.

Literal erasure and a withdrawal of an earlier statement are different owner
operations. A no-match forget can still retract its subject. Its command cannot
be persisted again as ordinary source text after revocation, so the reader uses
the durable target as control data, applying the existing temporal owner's law.
No control text is returned to a caller or added to a memory carrier.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.temporal_selection import (
    TemporalCandidate,
    _withdrawn_in_chain,
    retraction_marker,
)


def payload_is_withdrawn(payload: Any, revoked_tokens: Sequence[str]) -> bool:
    """Whether a subject-naming forget withdraws any authored part of payload.

    Only the temporal owner's live retraction grammar grants this authority;
    arbitrary revoked values do not become subject queries. Both synthetic
    candidates describe authority order, not a fabricated source timestamp.
    """
    controls = ["Forget " + str(token) for token in revoked_tokens
                if retraction_marker("Forget " + str(token)) is not None]
    if not controls:
        return False

    def withdrawn(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(withdrawn(item) for item in value.values())
        if isinstance(value, (tuple, list)):
            return any(withdrawn(item) for item in value)
        if not isinstance(value, str) or not value.strip():
            return False
        source = TemporalCandidate(key="source", body=value, role="user", seq=1)
        for control in controls:
            withdrawal = TemporalCandidate(key="withdrawal", body=control, role="user", seq=2)
            if _withdrawn_in_chain(0, [0, 1], [source, withdrawal]) is not None:
                return True
        return False

    return withdrawn(payload)
