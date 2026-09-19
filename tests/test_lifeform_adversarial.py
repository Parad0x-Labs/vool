"""VOOLemon blue-team suite — attacks the red team demanded (wave 2).

Each test is an attack on our own progression system. If one fails, the
anti-farm/anti-cheat story is false and the foundation must not ship its claims.
"""
from __future__ import annotations

import json

import pytest

from core.companion.lifeform import (
    DAILY_ENERGY_CAP,
    genesis,
    progression,
    signals,
)
from core.companion.lifeform.schema import LifeformError, new_lifeform_v1, to_json

pytestmark = [pytest.mark.gauntlet]

OWNER = "op-adv"
LF = "lf-adv"
SEED = genesis.genesis_seed(OWNER, LF)

EV = progression.make_event


def _log(specs, day="2026-09-02", resolver=None):
    log = []
    for _i, (stype, turn, fact, *rest) in enumerate(specs):
        family = rest[0] if rest else "shell"
        surface = rest[1] if len(rest) > 1 else "local"
        verified = rest[2] if len(rest) > 2 else True
        event = EV(lifeform_id=LF, signal_type=stype, turn_key=turn, fact_id=fact,
                   day=day, seq=0, prev_hash="", family=family, surface=surface,
                   verified=verified)
        log, _ = progression.append_event(log, event, fact_resolver=resolver)
    return log


def test_faucet_capped_10k_research_signals_one_day():
    specs = [("research_task_completed", f"t{i}", f"f{i}", "retrieval")
             for i in range(10_000)]
    state = progression.reduce_events(_log(specs), SEED)
    assert state["energy_game"] <= progression._TYPE_DAILY_CAP["research_task_completed"] + 1e-9
    # all types spammed together still bounded by the global day cap
    mixed = []
    types = [("research_task_completed", "retrieval"), ("coding_task_verified", "shell"),
             ("test_suite_passed", "test"), ("tool_family_used", "network"),
             ("long_task_survived", "long"), ("daily_active_use", "presence")]
    for i in range(2_000):
        stype, fam = types[i % len(types)]
        mixed.append((stype, f"t{i}", f"f{i}", fam))
    state2 = progression.reduce_events(_log(mixed), SEED)
    assert state2["energy_game"] <= DAILY_ENERGY_CAP + 1e-9


def test_cap_replay_stability_and_ruleset_pin():
    specs = [("coding_task_verified", f"t{i}", f"f{i}", "shell") for i in range(100)]
    log = _log(specs)
    snap = progression.sign_snapshot(log, b"key", "kid", SEED)
    ok, problems = progression.verify_snapshot(snap, log, {"kid": b"key"}, SEED)
    assert ok
    # a snapshot reduced under a DIFFERENT ruleset must be rejected (rebuilt), not blended
    stale = dict(snap, ruleset_version="lifeform-ruleset-0.0.9")
    ok2, problems2 = progression.verify_snapshot(stale, log, {"kid": b"key"}, SEED)
    assert not ok2 and any("ruleset" in p for p in problems2)


def test_fact_binding_fabricated_receipt_rejected():
    specs = [("coding_task_verified", "turn-real", "fact-real", "shell")]
    # resolver that knows nothing about the claimed fact
    state = progression.reduce_events(_log(specs), SEED)
    log_with_resolver = _log(specs, resolver=lambda tk, fid, st: False)
    state_bound = progression.reduce_events(log_with_resolver, SEED)
    assert state_bound["energy_game"] == 0.0          # no GAME credit
    assert state_bound["energy_observed"] > 0.0        # but honest OBSERVED trace
    assert state_bound["degraded_event_ids"], "unresolvable fact must be flagged"
    # and the degradation is chain-hashed: stripping it breaks verification
    snap = progression.sign_snapshot(log_with_resolver, b"key", "kid", SEED)
    tampered = [dict(e, degraded=False) for e in log_with_resolver]
    ok, problems = progression.verify_snapshot(snap, tampered, {"kid": b"key"}, SEED)
    assert not ok


def test_hundred_ulids_one_turn_one_signal():
    specs = [("coding_task_verified", "turn-1", "fact-1", "shell")] * 100
    log = _log(specs)
    assert len(log) == 1  # 100 ULID re-mints collapse to the single derived event
    state = progression.reduce_events(log, SEED)
    assert state["energy_game"] == 6.0  # one verified coding fact, diminishing n=0


def test_long_task_cap_per_day():
    specs = [("long_task_survived", f"t{i}", f"f{i}", "long") for i in range(50)]
    state = progression.reduce_events(_log(specs), SEED)
    assert state["energy_game"] <= progression._TYPE_DAILY_CAP["long_task_survived"] + 1e-9


def test_unknown_tool_families_zero_game_energy():
    facts = [{"fact_id": f"f{i}", "turn_key": f"t{i}", "session_id": "",
              "kind": "tool", "name": f"exotic_tool_{i}", "ok": True,
              "status": "executed", "detail": {}, "created_at": "2026-09-02T10:00:00Z",
              "categories": ["tool"]} for i in range(1000)]
    drafts = [d for f in facts for d in signals.fact_to_signals(f)]
    assert drafts and all(d["family"] == "unknown" and d["degraded"] for d in drafts)
    log = _log([(d["signal_type"], d["turn_key"], d["fact_id"]) for d in drafts])
    for e in log:
        e["degraded"] = True
    state = progression.reduce_events(log, SEED)
    assert state["energy_game"] == 0.0


def test_council_signal_unmintable_in_v0():
    with pytest.raises(LifeformError):
        EV(lifeform_id=LF, signal_type="council_review_participated",
           turn_key="t", fact_id="f", day="2026-09-02", seq=1, prev_hash="")


def test_clock_independence_of_reduced_state():
    specs = [("coding_task_verified", "t1", "f1", "shell"),
             ("research_task_completed", "t2", "f2", "retrieval")]
    log = _log(specs, day="2026-09-02")
    s1 = progression.reduce_events(log, SEED)
    # "advance the wall clock" — reducer has no clock input; same bytes in, same state out
    s2 = progression.reduce_events(json.loads(json.dumps(log)), SEED)
    assert s1 == s2


def test_day_boundary_two_buckets_at_most_double_cap():
    specs = ([(t, f"a{i}", f"af{i}", fam) for i, (t, fam) in enumerate(
        [("coding_task_verified", "shell")] * 30)]
             + [(t, f"b{i}", f"bf{i}", fam) for i, (t, fam) in enumerate(
                 [("coding_task_verified", "shell")] * 30)])
    day1 = _log([(s[0], s[1], s[2], "shell") for s in specs[:30]], day="2026-09-02")
    day2 = day1
    for s in specs[30:]:
        event = EV(lifeform_id=LF, signal_type=s[0], turn_key=s[1], fact_id=s[2],
                   day="2026-09-03", seq=0, prev_hash="", family="shell")
        day2, _ = progression.append_event(day2, event)
    state = progression.reduce_events(day2, SEED)
    # two UTC buckets: at most 2x the global daily cap, never more
    assert state["energy_game"] <= 2 * DAILY_ENERGY_CAP + 1e-9


def test_wrong_key_discard_rebuild_from_log():
    specs = [("coding_task_verified", "t1", "f1", "shell")]
    log = _log(specs)
    snap = progression.sign_snapshot(log, b"key-A", "kid-A", SEED)
    ok, problems = progression.verify_snapshot(snap, log, {"kid-B": b"key-B"}, SEED)
    assert not ok and any("key_id" in p for p in problems)
    # recovery = rebuild from the log under the current key (log is the truth)
    rebuilt = progression.sign_snapshot(log, b"key-B", "kid-B", SEED)
    ok2, _ = progression.verify_snapshot(rebuilt, log, {"kid-B": b"key-B"}, SEED)
    assert ok2


def test_rollback_detection_is_stage1():
    """Executable documentation of the honest trust boundary: a consistent
    backup-restore (old log + snapshot + key) verifies clean locally. Rollback
    PROTECTION requires Stage-1 Garden anchors. We refuse to claim otherwise."""
    specs = [("coding_task_verified", "t1", "f1", "shell")]
    log = _log(specs)
    key, kid = b"key", "kid"
    snap_old = progression.sign_snapshot(log, key, kid, SEED)
    log_longer, _ = progression.append_event(
        log, EV(lifeform_id=LF, signal_type="coding_task_verified", turn_key="t2",
                fact_id="f2", day="2026-09-03", seq=0, prev_hash="", family="shell"))
    # operator restores yesterday's backup: old log + old snapshot + same key
    ok, problems = progression.verify_snapshot(snap_old, log, {kid: key}, SEED)
    assert ok  # locally indistinguishable from honest — anchors are the fix
    assert progression.RULESET_VERSION.startswith("lifeform-ruleset-")


def test_schema_never_accepts_extra_or_transferable():
    doc = json.loads(to_json(new_lifeform_v1(LF, OWNER, SEED, "2026-09-01")))
    doc["trading_enabled"] = True
    with pytest.raises(LifeformError):
        from core.companion.lifeform.schema import from_json
        from_json(doc)
    doc2 = json.loads(to_json(new_lifeform_v1(LF, OWNER, SEED, "2026-09-01")))
    doc2["social"]["transferable"] = True
    with pytest.raises(LifeformError):
        from core.companion.lifeform.schema import from_json
        from_json(doc2)


def test_no_pace_or_guilt_language_in_user_surface():
    from pathlib import Path
    group = Path("core/command_registry/groups/lifeform_group.py").read_text()
    for banned in ("days remaining", "on pace", "deadline", "streak", "you must",
                   "don't lose", "keep your"):
        assert banned not in group.lower()


def test_same_multiset_any_order_same_archetype():
    import itertools
    specs = [("coding_task_verified", "t1", "f1", "shell"),
             ("coding_task_verified", "t2", "f2", "shell"),
             ("research_task_completed", "t3", "f3", "retrieval")]
    states = []
    for perm in itertools.permutations(specs):
        log = _log(list(perm))
        states.append(progression.reduce_events(log, SEED))
    first = states[0]
    for s in states[1:]:
        assert s["energy_game"] == first["energy_game"]
        assert s["genome"] == first["genome"]


def test_signature_gives_tamper_evidence_not_silent_failure():
    specs = [("coding_task_verified", "t1", "f1", "shell")]
    log = _log(specs)
    snap = progression.sign_snapshot(log, b"key", "kid", SEED)
    forged = [dict(log[0], tokens=999_999)]  # inflate via token field
    ok, problems = progression.verify_snapshot(snap, forged, {"kid": b"key"}, SEED)
    assert not ok and problems  # detected, never silently accepted
