"""core.companion.lifeform — the VOOLemon digital-life foundation (greenfield).

A lifeform is born from verified work the runtime already witnessed. Canonical
laws this package implements (VOOLemon mission hard laws):

L1  Event-sourced: the append-only event log is the only truth; state is
    ``reduce(events)`` under one pinned ``RULESET_VERSION``; snapshots are
    signed caches that must re-reduce bit-for-bit or be discarded.
L2  Additive-only grammar: no event type subtracts energy; corrections are
    compensating events. Negative-energy is structurally impossible.
L3  Fact binding: an event's identity derives from the witnessed fact it
    credits — ``event_id = H(turn_key, fact_id, signal_type)`` — so a fact can
    be credited exactly once no matter how many times a client re-mints ULIDs.
L4  Bounded faucet: per-type daily caps and one global daily cap live inside
    the reducer, pinned by the ruleset version — deliberately NOT config, so
    no config edit can bend progression retroactively.
L5  Read-only subscription: this package never imports or writes execution
    truth. Mapping from witnessed facts to typed signals happens in
    ``signals.py`` over plain dicts; the only caller that touches
    ``core.execution_truth`` is the command adapter, which reads views only.
L6  No wall-clock authority: reducer state depends only on event content
    (including the UTC day stamped at append time). Timers cannot mint work.
L7  Honesty: failed or refused facts convert to nothing. Unverified or
    unresolvable events may feed OBSERVED descriptors but never GAME stats.
L8  Privacy: the schema has no free-text fields; names are bounded tokens;
    growth rings are milestone labels without dates.
L9  Non-transferable: a lifeform is bound to its owner identity and can never
    be traded; competition inputs are simulation-only.
"""
from core.companion.lifeform.genesis import (
    EGG_EVIDENCE_MIN_FACTS,
    EGG_EVIDENCE_MIN_TURN_KEYS,
    evidence_gate,
    genesis_seed,
)
from core.companion.lifeform.progression import (
    DAILY_ENERGY_CAP,
    HATCH_THRESHOLD,
    RULESET_VERSION,
    STAGES,
    append_event,
    derive_event_id,
    make_event,
    reduce_events,
    sign_snapshot,
    verify_snapshot,
)
from core.companion.lifeform.schema import (
    SCHEMA_VERSION,
    LifeformError,
    LifeformV1,
    from_json,
    migrate,
    new_lifeform_v1,
    to_json,
)

__all__ = [
    "DAILY_ENERGY_CAP",
    "EGG_EVIDENCE_MIN_FACTS",
    "EGG_EVIDENCE_MIN_TURN_KEYS",
    "HATCH_THRESHOLD",
    "RULESET_VERSION",
    "SCHEMA_VERSION",
    "STAGES",
    "LifeformError",
    "LifeformV1",
    "append_event",
    "derive_event_id",
    "evidence_gate",
    "from_json",
    "genesis_seed",
    "make_event",
    "migrate",
    "new_lifeform_v1",
    "reduce_events",
    "sign_snapshot",
    "to_json",
    "verify_snapshot",
]
