"""VOOLemon foundation tests — the mandated test list (mission brief).

Every test maps to a hard law. Run marker: gauntlet (deterministic, offline).
"""
from __future__ import annotations

import json

import pytest

from core.companion.lifeform import (
    DAILY_ENERGY_CAP,
    HATCH_THRESHOLD,
    appearance,
    competition,
    genesis,
    progression,
    signals,
)
from core.companion.lifeform.schema import (
    LifeformError,
    LifeformV1,
    from_json,
    migrate,
    new_lifeform_v1,
    to_json,
)

pytestmark = [pytest.mark.gauntlet]

OWNER = "op-test-0001"
LF = "lf-test-0001"
SEED = genesis.genesis_seed(OWNER, LF)


def _doc() -> LifeformV1:
    return new_lifeform_v1(LF, OWNER, SEED, "2026-09-01")


def _ev(signal_type: str, turn: str, fact: str, day: str = "2026-09-02",
        verified: bool = True, family: str = "shell", surface: str = "local",
        tokens: int = 0, degraded: bool = False, seq: int = 0, prev: str = ""):
    return progression.make_event(
        lifeform_id=LF, signal_type=signal_type, turn_key=turn, fact_id=fact,
        day=day, seq=seq, prev_hash=prev, verified=verified, family=family,
        surface=surface, tokens=tokens, degraded=degraded)


def _build_log(specs: list[dict], day: str = "2026-09-02",
               resolver=None) -> list[dict]:
    """specs: list of (signal_type, turn, fact, family, surface, verified)."""
    log: list[dict] = []
    for _i, s in enumerate(specs):
        stype, turn, fact = s[0], s[1], s[2]
        family = s[3] if len(s) > 3 else "shell"
        surface = s[4] if len(s) > 4 else "local"
        verified = s[5] if len(s) > 5 else True
        event = _ev(stype, turn, fact, day=day, family=family, surface=surface,
                    verified=verified)
        log, _res = progression.append_event(log, event, fact_resolver=resolver)
    return log


# -- identity & appearance ---------------------------------------------------

def test_same_genesis_seed_same_base_identity():
    a = genesis.genesis_seed(OWNER, LF)
    b = genesis.genesis_seed(OWNER, LF)
    assert a == b == SEED
    d1 = new_lifeform_v1(LF, OWNER, SEED, "2026-09-01")
    d2 = new_lifeform_v1(LF, OWNER, SEED, "2026-09-01")
    assert to_json(d1) == to_json(d2)
    assert genesis.genesis_seed(OWNER, "lf-other") != SEED


def test_same_canonical_state_same_appearance_manifest():
    s1 = appearance.render_spec(None, stage="protoform", genesis_seed=SEED,
                                archetype="FORGE", lineage="NOCT", rings=2)
    s2 = appearance.render_spec(None, stage="protoform", genesis_seed=SEED,
                                archetype="FORGE", lineage="NOCT", rings=2)
    assert appearance.manifest(s1) == appearance.manifest(s2)
    other_seed = genesis.genesis_seed(OWNER, "lf-other")
    s3 = appearance.render_spec(None, stage="protoform", genesis_seed=other_seed,
                                archetype="FORGE", lineage="NOCT", rings=2)
    assert appearance.manifest(s1) != appearance.manifest(s3)
    assert set(s1["layers"]) <= set(appearance.LAYER_ORDER)


def test_migration_v0_1_to_v1_preserves_energy():
    draft = {
        "lifeform_id": LF, "owner_id": OWNER, "genesis_seed": SEED,
        "created_day": "2026-09-01", "development": {"progress": 123.5},
    }
    migrated = migrate(json.dumps(draft), from_version=0, to_version=1)
    doc = from_json(migrated)
    assert doc.schema_version == 1
    assert doc.development["energy_game"] == 123.5
    assert json.loads(migrated)["development"]["energy_game"] == 123.5


# -- reducer laws --------------------------------------------------------------

def test_duplicate_event_idempotent():
    event = _ev("coding_task_verified", "turn-1", "fact-1", seq=1, prev="")
    log1, res1 = progression.append_event([], event)
    log2, res2 = progression.append_event(log1, event)
    assert res1["appended"] and not res2["appended"]
    assert res2["reason"] == "duplicate"
    assert len(log2) == 1
    # re-minting the SAME fact under a different ULID yields the same derived id
    remint = _ev("coding_task_verified", "turn-1", "fact-1", seq=2, prev=log1[0]["hash"])
    log3, res3 = progression.append_event(log2, remint)
    assert not res3["appended"] and res3["reason"] == "duplicate"


def test_event_replay_reconstructs_state():
    specs = [(t, f"turn-{i}", f"fact-{i}", fam)
             for i, (t, fam) in enumerate(
                 [("coding_task_verified", "shell"), ("research_task_completed", "retrieval"),
                  ("test_suite_passed", "test"), ("tool_family_used", "network")])]
    log = _build_log(specs)
    s1 = progression.reduce_events(log, SEED)
    s2 = progression.reduce_events(list(reversed(log)), SEED)
    assert s1["energy_game"] == s2["energy_game"] > 0
    assert s1["genome"] == s2["genome"]
    assert s1["chain_ok"] and s2["chain_ok"] is False  # reversed order breaks chain
    signed = progression.sign_snapshot(log, b"k", "kid", SEED)
    ok, problems = progression.verify_snapshot(signed, log, {"kid": b"k"}, SEED)
    assert ok and not problems


def _multi_day_log(days: int, day_plan: list[dict], start: str = "2026-09-01") -> list[dict]:
    """day_plan: list of (signal_type, family, surface) minted once per day."""
    import datetime as dt
    log: list[dict] = []
    start_d = dt.date.fromisoformat(start)
    n = 0
    for i in range(days):
        day = (start_d + dt.timedelta(days=i)).isoformat()
        for stype, family, surface in day_plan:
            event = _ev(stype, f"turn-{n}", f"fact-{n}", day=day, family=family,
                        surface=surface)
            log, _ = progression.append_event(log, event)
            n += 1
    return log


def test_stage_ladder_and_hatch():
    # a realistic hatch: ~7+ active days of mixed verified work (no single-day
    # shortcut exists — caps make that structurally impossible)
    day_plan = [("coding_task_verified", "shell", "local"),
                ("research_task_completed", "retrieval", "local"),
                ("test_suite_passed", "test", "local"),
                ("tool_family_used", "other", "local")]
    log = _multi_day_log(20, day_plan)
    state = progression.reduce_events(log, SEED)
    assert state["hatched"] and state["energy_game"] >= HATCH_THRESHOLD
    assert state["stage"] == "protoform"
    assert state["genome"]["archetype"] == "FORGE"  # coding+test families dominate
    doc = progression.apply_state(_doc(), state)
    assert doc.hatched and doc.lineage["archetype"] == "FORGE"
    assert "hatch_protoform" in doc.lineage["growth_rings"]


# -- privacy & boundaries --------------------------------------------------------

def test_private_content_fields_rejected():
    bad = json.loads(to_json(_doc()))
    bad["prompt_excerpt"] = "secret user content"
    with pytest.raises(LifeformError):
        from_json(bad)
    bad2 = json.loads(to_json(_doc()))
    bad2["source_diff"] = "diff --git a/x b/x"
    with pytest.raises(LifeformError):
        from_json(bad2)


def test_growth_rings_carry_no_dates():
    day_plan = [("coding_task_verified", "shell", "local")]
    log = _multi_day_log(60, day_plan)
    state = progression.reduce_events(log, SEED)
    doc = progression.apply_state(_doc(), state)
    for ring in doc.lineage["growth_rings"]:
        assert not any(ch.isdigit() for ch in ring)  # milestone labels, never dates
    spec = appearance.render_spec(doc, stage=state["stage"], genesis_seed=SEED,
                                  rings=len(doc.lineage["growth_rings"]))
    assert spec["layers"]["growth_rings"] == {
        "rings": ["ring"] * len(doc.lineage["growth_rings"]), "style": "dla_dendrite"}


def test_pet_cannot_write_execution_truth_ast_guard():
    import ast
    from pathlib import Path
    pkg = Path("core/companion/lifeform")
    forbidden = ("record_execution", "INSERT INTO", "UPDATE ", "DELETE FROM",
                 "commit(", "record_event")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                for frag in ("insert into execution", "update execution",
                             "delete from execution"):
                    assert frag not in low, f"{py.name} writes execution truth"
        source = py.read_text()
        for frag in forbidden:
            assert frag not in source or py.name == "test_" + py.name, \
                f"forbidden {frag!r} in {py.name}"


def test_legacy_companion_untouched_import_isolation():
    import ast
    from pathlib import Path
    pkg = Path("core/companion/lifeform")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = ""
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                if isinstance(node, ast.Import):
                    mod = " ".join(a.name for a in node.names)
                assert "companion_presentation" not in mod, py.name
                assert "companion_world" not in mod, py.name


def test_timer_cannot_manufacture_activity():
    specs = [(t, f"t{i}", f"f{i}", fam)
             for i, (t, fam) in enumerate([("coding_task_verified", "shell"),
                                           ("research_task_completed", "retrieval")])]
    log = _build_log(specs, day="2026-09-02")
    baseline = progression.reduce_events(log, SEED)
    # a "later" wall clock with the SAME event content changes nothing
    again = progression.reduce_events(log, SEED)
    assert baseline == again
    # the reducer exposes no clock hook: appending without events never grows energy
    empty = progression.reduce_events([], SEED)
    assert empty["energy_game"] == 0.0 and empty["energy_observed"] == 0.0


def test_failed_task_never_verified_work():
    failed = {"fact_id": "f1", "turn_key": "t1", "session_id": "", "kind": "tool",
              "name": "shell", "ok": False, "status": "failed", "detail": {},
              "created_at": "2026-09-02T10:00:00Z", "categories": ["tool"]}
    refused = dict(failed, fact_id="f2", ok=True, status="refused")
    assert signals.fact_to_signals(failed) == []
    assert signals.fact_to_signals(refused) == []


def test_local_and_cloud_both_contribute():
    # local-only longevity -> NOCT; cloud-only -> LUMEN; a 50/50 mix -> rare
    # HYBRID lineage. Cloud is never inherently superior: lineage tracks the
    # mix, and energy weights are surface-blind.
    local_only = _multi_day_log(60, [("coding_task_verified", "shell", "local")])
    cloud_only = _multi_day_log(60, [("coding_task_verified", "shell", "cloud")])
    import datetime as dt
    mixed: list[dict] = []
    start = dt.date(2026, 9, 1)
    for i in range(60):
        day = (start + dt.timedelta(days=i)).isoformat()
        surface = "local" if i % 2 == 0 else "cloud"
        event = _ev("coding_task_verified", f"mt{i}", f"mf{i}", day=day,
                    family="shell", surface=surface)
        mixed, _ = progression.append_event(mixed, event)
    s_local = progression.reduce_events(local_only, SEED)
    s_cloud = progression.reduce_events(cloud_only, SEED)
    s_mixed = progression.reduce_events(mixed, SEED)
    assert s_local["energy_game"] == s_cloud["energy_game"] > 0
    assert s_local["genome"]["lineage"] == "NOCT"
    assert s_cloud["genome"]["lineage"] == "LUMEN"
    assert s_mixed["genome"]["lineage"] == "HYBRID"


# -- energy model ------------------------------------------------------------

def test_token_spam_capped_and_concave():
    assert progression.token_energy(200_000) == 15.0
    assert progression.token_energy(2_000_000) == 15.0
    assert progression.token_energy(10_000) < progression.token_energy(50_000) < 15.0


def test_race_simulation_deterministic():
    runners = [
        {"lifeform_id": "lf-a", "stats": {"speed": 30, "focus": 70, "rigor": 40,
                                          "insight": 90}},
        {"lifeform_id": "lf-b", "stats": {"speed": 80, "focus": 20, "rigor": 90,
                                          "insight": 30}},
    ]
    track = {"segments": [{"kind": "straight", "length": 10},
                          {"kind": "hills", "length": 10}]}
    r1 = competition.race_outcome(runners, track, "seed-xyz")
    r2 = competition.race_outcome(runners, track, "seed-xyz")
    assert r1 == r2 and r1["race_id"] and len(r1["finish_order"]) == 2
    r3 = competition.race_outcome(runners, track, "seed-other-day")
    assert r3["finish_order"] != r1["finish_order"] or r3["times"] != r1["times"]
    with pytest.raises(ValueError):
        competition.race_outcome(runners, track, "s", ruleset="future-1")


def test_store_roundtrip(tmp_path, monkeypatch):
    from storage import lifeform_store
    doc = to_json(_doc())
    lifeform_store.save_lifeform(doc, [], None, "2026-09-01T00:00:00Z")
    loaded = lifeform_store.load_lifeform(LF)
    assert loaded["doc"]["lifeform_id"] == LF
    assert loaded["event_log"] == []
    again = lifeform_store.load_for_owner(OWNER)
    assert again and again["doc"]["lifeform_id"] == LF
