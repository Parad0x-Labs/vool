"""Genesis: the egg is minted from witnessed evidence, never from nothing.

"Genesis by evidence": ``/egg`` is live only after the runtime has witnessed at
least ``EGG_EVIDENCE_MIN_FACTS`` execution facts across at least
``EGG_EVIDENCE_MIN_TURN_KEYS`` distinct turns for this home. The gate is pure —
the caller (the command adapter) supplies the read-only counts it witnessed.

The genesis seed is deterministic per (owner, lifeform): the same pair always
re-mints the same identity, so the creature's base look is stable even if the
log is rebuilt from scratch.
"""
from __future__ import annotations

import hashlib

EGG_EVIDENCE_MIN_FACTS = 5
EGG_EVIDENCE_MIN_TURN_KEYS = 3


def genesis_seed(owner_id: str, lifeform_id: str) -> str:
    basis = f"{owner_id}|{lifeform_id}|voolemon-genesis-v1"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def evidence_gate(fact_count: int, distinct_turn_keys: int) -> tuple[bool, dict]:
    """Pure gate -> (allowed, detail). The caller renders the prospectus UX."""
    detail = {
        "facts_witnessed": int(fact_count),
        "facts_required": EGG_EVIDENCE_MIN_FACTS,
        "turn_keys_witnessed": int(distinct_turn_keys),
        "turn_keys_required": EGG_EVIDENCE_MIN_TURN_KEYS,
    }
    allowed = (fact_count >= EGG_EVIDENCE_MIN_FACTS
               and distinct_turn_keys >= EGG_EVIDENCE_MIN_TURN_KEYS)
    return allowed, detail
