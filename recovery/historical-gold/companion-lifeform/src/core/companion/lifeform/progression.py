"""Event-sourced progression — the only truth is the append-only event log.

Anti-cheat posture (amended by the wave-2 red team):
- Stage 0 (this code) is TAMPER-EVIDENT, not tamper-proof: naive edits,
  corruption, duplicate replays and wrong-key signatures are detected; a
  consistent backup-restore (old log + snapshot + key) is NOT detectable
  locally — rollback protection arrives with Stage-1 Garden anchors. The test
  ``test_rollback_detection_is_stage1`` documents this boundary executably.
- Caps live HERE, pinned by RULESET_VERSION, not in config: the diminishing
  tail never becomes a faucet because each signal type has a hard daily cap
  and the whole day is capped again globally.
- GAME stats accumulate verified, fact-bound, non-degraded energy only.
  OBSERVED descriptors may count everything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any, Callable

from core.companion.lifeform.schema import LifeformError, LifeformV1

RULESET_VERSION = "lifeform-ruleset-0.1.0"

#: signal vocabulary — CLOSED. The council signal is deliberately ABSENT in
#: this ruleset (single-home co-signing is self-signing; it arrives only with
#: distinct-owner attestation in a later ruleset).
SIGNAL_TYPES = (
    "research_task_completed",
    "coding_task_verified",
    "test_suite_passed",
    "tool_family_used",
    "long_task_survived",
    "daily_active_use",
    "tokens_observed",
)

#: base weights (verified, unverified). Unverified energy never reaches GAME.
_WEIGHTS = {
    "research_task_completed": (4.0, 2.0),
    "coding_task_verified": (6.0, 3.0),
    "test_suite_passed": (3.0, 1.5),
    "tool_family_used": (5.0, 2.5),
    "long_task_survived": (5.0, 2.5),
    "daily_active_use": (2.0, 1.0),
    "tokens_observed": (0.0, 0.0),   # token energy comes from the daily curve
}

#: hard per-type daily GAME-energy caps (red-team S0 fix: the diminishing tail
#: is bounded by these, so spamming cannot out-earn a day of real work).
_TYPE_DAILY_CAP = {
    "research_task_completed": 6.0,
    "coding_task_verified": 9.0,
    "test_suite_passed": 4.5,
    "tool_family_used": 7.5,
    "long_task_survived": 7.5,
    "daily_active_use": 3.0,
    "tokens_observed": 15.0,
}

#: global daily cap = Dmax. 200k tokens/day already earn the full +15 (15%).
DAILY_ENERGY_CAP = 100.0
TOKEN_DAILY_ENERGY = 15.0
_TOKEN_CURVE_DENOM = math.log2(1.0 + 200_000.0)
_DIMINISH = 0.6

#: stage ladder (cumulative GAME energy); hatch = protoform threshold.
HATCH_THRESHOLD = 350.0
STAGES = (
    ("seed", 0.0),
    ("protoform", 350.0),
    ("form", 1400.0),
    ("specialized", 4200.0),
    ("ascended", 12000.0),
)

#: coarse tool families allowed to carry GAME energy (red-team F3: family is
#: the coarse class, never the raw tool name; unknown families stay OBSERVED).
TOOL_FAMILIES = (
    "shell", "file", "git", "test", "network", "search", "retrieval",
    "model", "effect",
)


def derive_event_id(turn_key: str, fact_id: str, signal_type: str) -> str:
    """Fact-bound event identity (red-team F2): the same witnessed fact can be
    credited once per signal type no matter how many ULIDs a client mints."""
    basis = f"{turn_key}|{fact_id}|{signal_type}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _day(day: str) -> str:
    if not (isinstance(day, str) and len(day) == 10 and day[4] == "-" and day[7] == "-"):
        raise LifeformError("event day must be an appender-stamped UTC YYYY-MM-DD")
    return day


def make_event(*, lifeform_id: str, signal_type: str, turn_key: str, fact_id: str,
               day: str, seq: int, prev_hash: str, verified: bool = True,
               family: str = "other", surface: str = "local",
               tokens: int = 0, degraded: bool = False) -> dict[str, Any]:
    if signal_type not in SIGNAL_TYPES:
        raise LifeformError(
            f"unknown signal type {signal_type!r}; vocabulary is closed for "
            f"ruleset {RULESET_VERSION}")
    if surface not in ("local", "cloud"):
        raise LifeformError("surface must be 'local' or 'cloud'")
    event = {
        "event_id": derive_event_id(turn_key, fact_id, signal_type),
        "lifeform_id": str(lifeform_id),
        "type": signal_type,
        "turn_key": str(turn_key),
        "fact_id": str(fact_id),
        "family": str(family),
        "surface": surface,
        "verified": bool(verified),
        "degraded": bool(degraded),
        "tokens": int(tokens),
        "day": _day(day),
        "seq": int(seq),
        "prev_hash": str(prev_hash),
        "issued_by": "execution_truth",
    }
    event["hash"] = _chain_hash(event)
    return event


def _chain_hash(event: dict[str, Any]) -> str:
    basis = json.dumps(
        {k: event[k] for k in (
            "event_id", "lifeform_id", "type", "turn_key", "fact_id", "family",
            "surface", "verified", "degraded", "tokens", "day", "seq", "prev_hash")},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def token_energy(tokens: float) -> float:
    """Strictly concave token curve, hard-capped at TOKEN_DAILY_ENERGY."""
    if tokens <= 0:
        return 0.0
    return round(TOKEN_DAILY_ENERGY * min(
        1.0, math.log2(1.0 + float(tokens)) / _TOKEN_CURVE_DENOM), 4)


def _energy_for(event: dict[str, Any]) -> float:
    if event["type"] == "tokens_observed":
        return 0.0  # token energy is computed per-day after aggregation
    weight = _WEIGHTS[event["type"]][0 if event.get("verified") else 1]
    return weight


def _stage_for(energy: float) -> str:
    stage = STAGES[0][0]
    for name, threshold in STAGES:
        if energy >= threshold:
            stage = name
    return stage


def _splitmix_tiebreak(genesis_seed: str, candidates: list[str]) -> str:
    state = int(hashlib.sha256(genesis_seed.encode()).hexdigest()[:16], 16) & ((1 << 64) - 1)
    state = (state + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    z = (state ^ (state >> 30)) * 0xBF58476D1CE4E5B9 & ((1 << 64) - 1)
    z = (z ^ (z >> 27)) * 0x94D049BB133111EB & ((1 << 64) - 1)
    idx = (z ^ (z >> 31)) % len(candidates)
    return candidates[idx]


def reduce_events(events: list[dict[str, Any]],
                  genesis_seed: str = "") -> dict[str, Any]:
    """Deterministic fold. Energy totals are order-independent (geometric
    diminishing per type+day depends only on the count; caps bound the day
    totals), so the same event multiset reduces identically in any order."""
    seen: set[str] = set()
    duplicates: list[str] = []
    chain_ok = True
    prev = ""
    seq_expected = 1
    ordered: list[dict[str, Any]] = []

    for event in events:
        eid = event.get("event_id") or ""
        if eid in seen:
            duplicates.append(eid)
            continue
        seen.add(eid)
        if event.get("prev_hash") != prev or int(event.get("seq", -1)) != seq_expected:
            chain_ok = False
        recomputed = _chain_hash(event)
        if recomputed != event.get("hash"):
            chain_ok = False
        prev = event.get("hash", prev)
        seq_expected += 1
        ordered.append(event)

    # per (day, type): count occurrences, geometric diminishing (order-free),
    # then per-type daily cap.
    per_day_type: dict[tuple[str, str], float] = {}
    per_day_type_count: dict[tuple[str, str], int] = {}
    per_day_tokens: dict[str, int] = {}
    family_game: dict[str, float] = {}
    surface_game: dict[str, float] = {"local": 0.0, "cloud": 0.0}
    observed_energy = 0.0
    observed_counts: dict[str, int] = {}
    degraded_ids: list[str] = []

    for event in ordered:
        stype = event["type"]
        observed_counts[stype] = observed_counts.get(stype, 0) + 1
        raw = _energy_for(event)
        observed_energy = round(observed_energy + raw, 4)
        if stype == "tokens_observed":
            per_day_tokens[event["day"]] = per_day_tokens.get(event["day"], 0) + int(
                event.get("tokens") or 0)
            continue
        # LAW L7: GAME energy comes only from VERIFIED, non-DEGRADED events.
        # Unverified or unresolvable facts are honest OBSERVED traces — they
        # never touch per-day GAME buckets, no matter their weight.
        if event.get("degraded") or not event.get("verified"):
            if event.get("degraded"):
                degraded_ids.append(event["event_id"])
            continue
        key = (event["day"], stype)
        n = per_day_type_count.get(key, 0)
        per_day_type_count[key] = n + 1
        gained = raw * (_DIMINISH ** n)
        per_day_type[key] = round(per_day_type.get(key, 0.0) + gained, 6)
        fam = "model" if event.get("family") == "model" else str(event.get("family"))
        family_game[fam] = round(family_game.get(fam, 0.0) + gained, 6)
        surface_game[event.get("surface", "local")] = round(
            surface_game.get(event.get("surface", "local"), 0.0) + gained, 6)

    # caps: per-type per-day, then token curve per-day, then the global day cap.
    per_day_game: dict[str, float] = {}
    for (day, stype), total in per_day_type.items():
        capped = min(total, _TYPE_DAILY_CAP[stype])
        per_day_game[day] = round(per_day_game.get(day, 0.0) + capped, 4)
    for day, tokens in per_day_tokens.items():
        per_day_game[day] = round(per_day_game.get(day, 0.0) + token_energy(tokens), 4)
    game_energy = 0.0
    capped_days = 0
    for day in sorted(per_day_game):
        credited = min(per_day_game[day], DAILY_ENERGY_CAP)
        if per_day_game[day] > DAILY_ENERGY_CAP:
            capped_days += 1
        game_energy = round(game_energy + credited, 4)

    stage = _stage_for(game_energy)
    hatched = game_energy >= HATCH_THRESHOLD
    genome: dict[str, Any] = {}
    rings: list[str] = []
    if hatched and genesis_seed:
        archetype = _archetype(family_game, genesis_seed)
        lineage = _lineage(surface_game)
        genome = {"archetype": archetype, "lineage": lineage}
        rings = ["hatch_protoform"]
        if stage in ("form", "specialized", "ascended"):
            rings.append(f"stage_{stage}")
        total_facts = sum(observed_counts.values())
        if total_facts >= 100:
            rings.append("facts_100")
        if observed_counts.get("long_task_survived", 0) >= 10:
            rings.append("endured_10_long_tasks")

    return {
        "ruleset_version": RULESET_VERSION,
        "energy_game": game_energy,
        "energy_observed": observed_energy,
        "stage": stage,
        "hatched": hatched,
        "genome": genome,
        "growth_rings": rings,
        "observed_counts": observed_counts,
        "degraded_event_ids": degraded_ids,
        "duplicate_event_ids": duplicates,
        "chain_ok": chain_ok,
        "event_count": len(ordered),
        "days_capped": capped_days,
        "active_days": len(per_day_game),
    }


def _archetype(family_game: dict[str, float], genesis_seed: str) -> str:
    family_to_archetype = {
        "retrieval": "MONOLITH", "search": "MONOLITH", "network": "MONOLITH",
        "shell": "FORGE", "file": "FORGE", "git": "FORGE", "test": "FORGE",
        "effect": "FORGE",
        "tool": "LATTICE", "other": "LATTICE",
        "long": "DRIFT", "presence": "WISP", "model": "WISP",
    }
    scores: dict[str, float] = {}
    for family, energy in family_game.items():
        arch = family_to_archetype.get(family)
        if arch:
            scores[arch] = round(scores.get(arch, 0.0) + energy, 6)
    if not scores:
        return "WISP"
    top = max(scores.values())
    tied = sorted(a for a, v in scores.items() if v == top)
    if len(tied) == 1:
        return tied[0]
    return _splitmix_tiebreak(genesis_seed, tied)


def _lineage(surface_game: dict[str, float]) -> str:
    total = surface_game.get("local", 0.0) + surface_game.get("cloud", 0.0)
    if total <= 0:
        return "HYBRID"
    local_share = surface_game.get("local", 0.0) / total
    if local_share >= 0.7:
        return "NOCT"
    if local_share <= 0.3:
        return "LUMEN"
    return "HYBRID"


def append_event(log: list[dict[str, Any]], event: dict[str, Any],
                 *, fact_resolver: Callable[[str, str, str], bool] | None = None
                 ) -> tuple[list[dict[str, Any], ], dict[str, Any]]:
    """Append with idempotency + chain + optional fact binding.

    ``fact_resolver(turn_key, fact_id, signal_type)`` is the runtime's
    read-only fact view. When provided, an event whose fact cannot be
    resolved is recorded but marked ``degraded`` (OBSERVED-only energy,
    flagged). When absent (offline replay) existing flags stand — logs are
    hash-chained, so flags cannot be silently stripped.
    """
    if log:
        last = log[-1]
        event = dict(event, seq=int(last["seq"]) + 1, prev_hash=last["hash"])
    else:
        event = dict(event, seq=1, prev_hash="")
    for existing in log:
        if existing["event_id"] == event["event_id"]:
            return log, {"appended": False, "reason": "duplicate",
                         "event_id": event["event_id"]}
    if fact_resolver is not None and not fact_resolver(
            event["turn_key"], event["fact_id"], event["type"]):
        event = dict(event, degraded=True)
    event = make_event(
        lifeform_id=event["lifeform_id"], signal_type=event["type"],
        turn_key=event["turn_key"], fact_id=event["fact_id"], day=event["day"],
        seq=event["seq"], prev_hash=event["prev_hash"],
        verified=event["verified"], family=event["family"],
        surface=event["surface"], tokens=event.get("tokens", 0),
        degraded=event.get("degraded", False))
    return log + [event], {"appended": True, "event_id": event["event_id"]}


# ---------------------------------------------------------------------------
# signed snapshots — tamper-EVIDENT caches (never the source of truth)
# ---------------------------------------------------------------------------

def sign_snapshot(events: list[dict[str, Any]], key: bytes, key_id: str,
                  genesis_seed: str = "") -> dict[str, Any]:
    state = reduce_events(events, genesis_seed)
    basis = json.dumps({"state": state, "key_id": key_id,
                        "ruleset_version": RULESET_VERSION},
                       sort_keys=True, separators=(",", ":"))
    sig = hmac.new(key, basis.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"key_id": key_id, "ruleset_version": RULESET_VERSION,
            "event_count": state["event_count"],
            "event_log_head": (events[-1]["hash"] if events else ""),
            "state": state, "sig": f"hmac-sha256:{sig}"}


def verify_snapshot(snapshot: dict[str, Any], events: list[dict[str, Any]],
                    keys: dict[str, bytes], genesis_seed: str = ""
                    ) -> tuple[bool, list[str]]:
    """Re-reduce the log and compare against the signed cache. Problems are
    returned, never raised; a failed snapshot is discarded and rebuilt."""
    problems: list[str] = []
    key = keys.get(snapshot.get("key_id"))
    if key is None:
        problems.append(f"unknown key_id {snapshot.get('key_id')!r}")
        return False, problems
    state = reduce_events(events, genesis_seed)
    basis = json.dumps({"state": state, "key_id": snapshot.get("key_id"),
                        "ruleset_version": RULESET_VERSION},
                       sort_keys=True, separators=(",", ":"))
    expected = hmac.new(key, basis.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"hmac-sha256:{expected}", str(snapshot.get("sig"))):
        problems.append("signature mismatch (wrong key or tampered state)")
    if snapshot.get("ruleset_version") != RULESET_VERSION:
        problems.append("snapshot was reduced under a different ruleset; rebuild required")
    if snapshot.get("event_count") != len(events):
        problems.append("event_count drift")
    head = events[-1]["hash"] if events else ""
    if snapshot.get("event_log_head") != head:
        problems.append("event_log_head drift")
    return (not problems), problems


def apply_state(doc: LifeformV1, state: dict[str, Any]) -> LifeformV1:
    """Fold a reduced state into the canonical document (presentation data
    only — never touches any VOOL execution structure)."""
    development = dict(doc.development)
    development.update({
        "stage": state["stage"],
        "energy_game": state["energy_game"],
        "energy_observed": state["energy_observed"],
        "active_days": state["active_days"],
    })
    lineage = dict(doc.lineage)
    if state.get("genome"):
        lineage["archetype"] = state["genome"].get("archetype")
        lineage["lineage"] = state["genome"].get("lineage")
    if state.get("growth_rings"):
        existing = [r for r in (lineage.get("growth_rings") or [])]
        for ring in state["growth_rings"]:
            if ring not in existing:
                existing.append(ring)
        lineage["growth_rings"] = existing
    provenance = dict(doc.provenance)
    provenance.update({
        "ruleset_version": state["ruleset_version"],
        "event_count": state["event_count"],
    })
    from dataclasses import replace
    return replace(doc, hatched=bool(state["hatched"]), development=development,
                   lineage=lineage, provenance=provenance)
