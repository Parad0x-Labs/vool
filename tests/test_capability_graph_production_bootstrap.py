"""Production bootstrap + bounded ranking tests for the capability graph (M1).

Proven defect these tests were built against (RED at base bb338f09):

    After a production import, the capability graph holds 0 implementations.
    ``populate_from_tool_registry`` has zero callers, so ``model_visible_specs``
    returns the same fixed fallback-eight tools for EVERY family hint —
    workspace, web and media are indistinguishable to the served model.
    Additionally, once populated, the 8-candidate selection was drawn from set
    iteration order: under PYTHONHASHSEED=2 the workspace selection contained
    ZERO read tools (all writes) while seed 1 returned all reads.

Unlike tests/test_capability_graph.py and tests/test_capability_graph_r2.py,
the production-state tests here register NO synthetic implementations: they
exercise the exact state a real daemon process is in.

Subprocess probes are used for import-state and determinism tests so each
probe sees a genuinely fresh interpreter (and its own PYTHONHASHSEED).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import capability_graph as cg
from core.capability_graph import (
    CapabilityId,
    init_graph,
    model_visible_specs,
    reset,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# The intents of every registry contract that is not supported on this runtime.
# These must never reach a model-visible set.
UNSUPPORTED_INTENTS = {
    "email.send",
    "email.read",
    "email.reply",
    "image.generate",
    "video.generate",
    "x.trending",
    "wallet.spend",
    "browser.render",
}

RESERVED = ("respond.direct", "operator.list_tools", "capability.expand_family")


@pytest.fixture(autouse=True)
def _production_like_graph():
    """Import-equivalent graph state: capabilities mapped, zero implementations."""
    reset()
    init_graph()
    yield
    reset()
    init_graph()


def _bootstrap():
    """Run the explicit production bootstrap (the repaired API)."""
    return cg.bootstrap_from_registry()


def _visible(**kw) -> list[str]:
    return [str(s.get("intent", "")).strip() for s in model_visible_specs(**kw)]


def _run_probe(code: str, *, hash_seed: str | None = None) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if hash_seed is not None:
        env["PYTHONHASHSEED"] = hash_seed
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc.stdout


# ---------------------------------------------------------------------------
#  P1: production import → first discovery query populates the graph
# ---------------------------------------------------------------------------

def test_P1_production_import_first_query_populates() -> None:
    """A fresh production import must not serve tool discovery from an empty graph.

    RED at base: total_implementations() stays 0 after the query and the
    workspace hint returns the family-navigation fallback.
    """
    out = _run_probe(
        """
import json
import core.capability_graph as cg
before = cg.total_implementations()
specs = [s["intent"] for s in cg.model_visible_specs(family_hint="workspace")]
after = cg.total_implementations()
disc = cg.discover(cg.DiscoveryRequest(family="web"))
print(json.dumps({
    "before": before,
    "after": after,
    "workspace": specs,
    "web_candidates": [c.tool_intent for c in disc.candidates],
}))
"""
    )
    data = json.loads(out)
    # Import itself stays side-effect free: no import-time registry magic.
    assert data["before"] == 0, "import must not eagerly populate (no import-time magic)"
    # The first discovery query must leave a populated graph behind.
    assert data["after"] >= 50, (
        f"graph still (nearly) empty after first query: {data['after']} implementations"
    )
    # And the answer itself must be workspace-true, not the navigation fallback.
    family_tools = [i for i in data["workspace"] if i not in RESERVED]
    assert family_tools, "workspace hint returned only reserved tools"
    for intent in family_tools:
        assert intent.startswith("workspace."), f"foreign tool in workspace set: {intent}"
    assert data["web_candidates"], "discover(family=web) returned no candidates in production state"


# ---------------------------------------------------------------------------
#  P2: family hints differentiate — workspace, web, media are distinct sets
# ---------------------------------------------------------------------------

def test_P2_family_hints_differentiate_workspace_web_media() -> None:
    """RED at base: all three hints return the identical fallback eight."""
    out = _run_probe(
        """
import json
import core.capability_graph as cg
sets = {
    fam: [s["intent"] for s in cg.model_visible_specs(family_hint=fam)]
    for fam in ("workspace", "web", "media")
}
print(json.dumps(sets))
"""
    )
    sets = json.loads(out)
    ws, web, media = sets["workspace"], sets["web"], sets["media"]
    assert ws != web, f"workspace and web hints returned the identical set: {ws}"
    assert ws != media, f"workspace and media hints returned the identical set: {ws}"
    assert web != media, f"web and media hints returned the identical set: {web}"
    # Each set must be family-true, not merely different (the proven defect).
    for fam, visible in sets.items():
        family_tools = [i for i in visible if i not in RESERVED]
        assert family_tools, f"{fam} hint returned no family tools"
        for intent in family_tools:
            assert intent.startswith(f"{fam}."), f"foreign tool in {fam} set: {intent}"


# ---------------------------------------------------------------------------
#  P3: selection is deterministic across interpreter hash seeds
# ---------------------------------------------------------------------------

def test_P3_selection_deterministic_across_hash_seeds() -> None:
    """RED at base: seed 1 gave all workspace reads, seed 2 gave ALL WRITES."""
    probe = """
import json
import core.capability_graph as cg
boot = getattr(cg, "bootstrap_from_registry", cg.populate_from_tool_registry)
boot()
print(json.dumps({
    "workspace": [s["intent"] for s in cg.model_visible_specs(family_hint="workspace")],
    "media": [s["intent"] for s in cg.model_visible_specs(family_hint="media")],
    "filesystem": [s["intent"] for s in cg.model_visible_specs(family_hint="filesystem")],
}))
"""
    results = [json.loads(_run_probe(probe, hash_seed=seed)) for seed in ("1", "2", "3")]
    assert results[0] == results[1] == results[2], (
        "model-visible selection varies with PYTHONHASHSEED:\n"
        + "\n".join(json.dumps(r) for r in results)
    )


# ---------------------------------------------------------------------------
#  P4: direct-import / circular-import safety
# ---------------------------------------------------------------------------

def test_P4_direct_import_orders_no_cycle() -> None:
    """Both import orders work, and the bootstrap runs from a bare direct import."""
    _run_probe(
        "import core.capability_graph; import core.runtime_tool_contracts; print('ok')"
    )
    _run_probe(
        "import core.runtime_tool_contracts; import core.capability_graph; print('ok')"
    )
    out = _run_probe(
        """
import core.capability_graph as cg
count = cg.bootstrap_from_registry()
assert count > 0, count
assert cg.total_implementations() >= count
print('ok', count)
"""
    )
    assert out.startswith("ok")


# ---------------------------------------------------------------------------
#  P5: root cause — population is what changes the served result
# ---------------------------------------------------------------------------

def test_P5_root_cause_population_changes_model_visible_result() -> None:
    """populate_from_tool_registry() flips the answer: fallback → family-true.

    This is the root-cause proof: nothing else changes between the two calls.
    (Green at base by construction — it documents the mechanism.  After the
    repair, the query-time guard would populate the graph during the "before"
    capture, so it is held off for that one call to expose the base state.)
    """
    saved_guard = getattr(cg, "ensure_registry_bootstrap", None)
    if saved_guard is not None:
        cg.ensure_registry_bootstrap = lambda: False
    try:
        before = _visible(family_hint="media")
    finally:
        if saved_guard is not None:
            cg.ensure_registry_bootstrap = saved_guard
    n = cg.populate_from_tool_registry()
    assert n > 0
    after = _visible(family_hint="media")
    assert before != after, "population did not change the media-visible set"
    assert any(i.startswith("media.") for i in after), f"media set after population: {after}"


# ---------------------------------------------------------------------------
#  P6: every supported builtin contract lands exactly once
# ---------------------------------------------------------------------------

def test_P6_bootstrap_registers_every_supported_contract_exactly_once() -> None:
    from core.runtime_tool_contracts import runtime_tool_contracts

    _bootstrap()
    contracts = runtime_tool_contracts()
    intents = [c.intent for c in contracts]
    assert len(intents) == len(set(intents)), "registry itself carries duplicate intents"

    impls_by_intent: dict[str, list] = {}
    for impl in cg.all_implementations():
        impls_by_intent.setdefault(impl.tool_intent, []).append(impl)

    for contract in contracts:
        rows = impls_by_intent.get(contract.intent, [])
        assert len(rows) == 1, (
            f"{contract.intent}: expected exactly one implementation, got {len(rows)}"
        )
        assert rows[0].available == contract.supported, (
            f"{contract.intent}: availability {rows[0].available} != supported {contract.supported}"
        )
        if not contract.supported:
            assert rows[0].availability_reason, f"{contract.intent}: unsupported without a reason"


# ---------------------------------------------------------------------------
#  P7: repeated bootstrap is idempotent; refresh seam is typed and live
# ---------------------------------------------------------------------------

def test_P7_repeated_bootstrap_idempotent() -> None:
    first = _bootstrap()
    total_1 = cg.total_implementations()
    sets_1 = {f: _visible(family_hint=f) for f in ("workspace", "web", "media", "filesystem")}
    epoch_1 = cg.registry_epoch()

    second = _bootstrap()
    total_2 = cg.total_implementations()
    sets_2 = {f: _visible(family_hint=f) for f in ("workspace", "web", "media", "filesystem")}

    refreshed = cg.refresh_from_registry()
    total_3 = cg.total_implementations()
    sets_3 = {f: _visible(family_hint=f) for f in ("workspace", "web", "media", "filesystem")}

    assert first == second == refreshed, (first, second, refreshed)
    assert total_1 == total_2 == total_3, (total_1, total_2, total_3)
    assert sets_1 == sets_2 == sets_3
    assert cg.registry_epoch() > epoch_1, "refresh must advance the registry epoch"


# ---------------------------------------------------------------------------
#  P8: bound and reserved seats hold for every hint
# ---------------------------------------------------------------------------

def test_P8_bound_and_reserved_seats_for_every_hint() -> None:
    _bootstrap()
    hints = [*sorted(set(cg._TOOLSET_HINT_TO_FAMILY.values())), None]
    for family in hints:
        visible = _visible(family_hint=family) if family else _visible()
        label = family or "<no hint>"
        assert len(visible) <= cg._DEFAULT_MAX_CANDIDATES, (
            f"{label}: bound violated with {len(visible)} tools: {visible}"
        )
        assert len(visible) == len(set(visible)), f"{label}: duplicate intents: {visible}"
        for intent in RESERVED:
            assert intent in visible, f"{label}: reserved seat {intent} missing: {visible}"


# ---------------------------------------------------------------------------
#  P9: read-only tools rank before mutation tools within a family
# ---------------------------------------------------------------------------

def test_P9_read_only_ranked_before_mutation_within_family() -> None:
    """RED at base: media.open (a write) ranked ahead of media.inspect."""
    from core.runtime_tool_contracts import runtime_tool_contracts

    _bootstrap()
    read_only_intents = {c.intent for c in runtime_tool_contracts() if c.read_only}

    for family in ("media", "workspace", "filesystem"):
        visible = [i for i in _visible(family_hint=family) if i not in RESERVED]
        assert visible, f"{family}: no family tools exposed"
        seen_mutation = False
        for intent in visible:
            if intent in read_only_intents:
                assert not seen_mutation, (
                    f"{family}: read-only {intent} ranked after a mutation tool: {visible}"
                )
            else:
                seen_mutation = True


def test_P9b_workspace_includes_read_list_search_before_writes() -> None:
    _bootstrap()
    visible = [i for i in _visible(family_hint="workspace") if i not in RESERVED]
    for wanted in ("workspace.list_tree", "workspace.read_file", "workspace.search_text"):
        assert wanted in visible, f"{wanted} missing from workspace selection: {visible}"
    writes = [i for i in visible if i in {
        "workspace.write_file", "workspace.replace_in_file", "workspace.apply_unified_diff",
        "workspace.ensure_directory", "workspace.rollback_last_change",
    }]
    for write_intent in writes:
        for read_intent in ("workspace.list_tree", "workspace.read_file", "workspace.search_text"):
            assert visible.index(read_intent) < visible.index(write_intent), (
                f"{write_intent} ranked before {read_intent}: {visible}"
            )


# ---------------------------------------------------------------------------
#  P10: unsupported tools stay hidden from every model-visible set
# ---------------------------------------------------------------------------

def test_P10_unsupported_tools_hidden_everywhere() -> None:
    _bootstrap()
    hints = [*sorted(set(cg._TOOLSET_HINT_TO_FAMILY.values())), None]
    for family in hints:
        visible = set(_visible(family_hint=family) if family else _visible())
        leaked = visible & UNSUPPORTED_INTENTS
        assert not leaked, f"unsupported tools exposed for hint {family!r}: {sorted(leaked)}"


# ---------------------------------------------------------------------------
#  P11: duplicate and collision controls
# ---------------------------------------------------------------------------

def _fake_contract(intent: str, capability_id: str, *, supported: bool = True,
                   read_only: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        intent=intent,
        capability_id=capability_id,
        supported=supported,
        unsupported_reason="" if supported else "disabled for this test",
        source="builtin",
        read_only=read_only,
    )


def test_P11_duplicate_intents_register_once() -> None:
    snapshot = [
        _fake_contract("dupe.read", "dupetest.read"),
        _fake_contract("dupe.read", "dupetest.read"),
        _fake_contract("dupe.read", "dupetest.other"),
    ]
    count = cg.bootstrap_from_registry(snapshot)
    assert count == 1, f"duplicate intent registered {count} times"
    impls = [i for i in cg.all_implementations() if i.tool_intent == "dupe.read"]
    assert len(impls) == 1
    # First occurrence wins deterministically.
    assert impls[0].capability_id == CapabilityId("dupetest.read")


def test_P11b_bootstrap_never_clobbers_foreign_implementation() -> None:
    from core.capability_graph import (
        Capability,
        CapabilityFamily,
        Implementation,
        ImplementationId,
        register_capability,
        register_family,
        register_implementation,
    )

    register_family(CapabilityFamily(id="plugintest", label="Plugin Test", description=""))
    register_capability(Capability(
        id=CapabilityId("plugintest.read"), family="plugintest", label="read", description="",
    ))
    register_implementation(Implementation(
        id=ImplementationId("collide.tool"),
        capability_id=CapabilityId("plugintest.read"),
        tool_intent="collide.tool",
        label="plugin-owned tool",
        provider="plugin:thirdparty",
        source="plugin",
        available=True,
        availability_reason="Available",
    ))
    cg.bootstrap_from_registry([_fake_contract("collide.tool", "builtintest.read")])
    impl = cg.all_implementations()
    rows = [i for i in impl if i.id == "collide.tool"]
    assert len(rows) == 1
    assert rows[0].source == "plugin", "bootstrap clobbered a foreign-source implementation"
    assert rows[0].provider == "plugin:thirdparty"


# ---------------------------------------------------------------------------
#  P12: boot invariant — the graph cannot silently start empty
# ---------------------------------------------------------------------------

def test_P12_boot_invariant_raises_when_population_yields_empty() -> None:
    broken_snapshot = [
        SimpleNamespace(intent="ghost.tool", capability_id="", supported=True,
                        unsupported_reason="", source="builtin", read_only=True),
    ]
    with pytest.raises(cg.CapabilityGraphBootstrapError):
        cg.bootstrap_from_registry(broken_snapshot)


def test_P12b_empty_graph_query_never_serves_silently_empty() -> None:
    # Production state: zero implementations. A query must repair itself.
    assert cg.total_implementations() == 0
    result = cg.discover(cg.DiscoveryRequest(family="filesystem"))
    assert cg.total_implementations() > 0, "discover served from a silently empty graph"
    assert result.candidates, "filesystem discovery empty after bootstrap"


# ---------------------------------------------------------------------------
#  P13: a hand-assembled (synthetic) graph is never overwritten by the guard
# ---------------------------------------------------------------------------

def test_P13_synthetic_world_untouched_by_bootstrap_guard() -> None:
    from core.capability_graph import (
        Capability,
        CapabilityFamily,
        Implementation,
        ImplementationId,
        register_capability,
        register_family,
        register_implementation,
    )

    register_family(CapabilityFamily(id="synthfam", label="Synth", description=""))
    register_capability(Capability(
        id=CapabilityId("synthfam.cap"), family="synthfam", label="cap", description="",
    ))
    register_implementation(Implementation(
        id=ImplementationId("synthfam.tool"),
        capability_id=CapabilityId("synthfam.cap"),
        tool_intent="synthfam.tool",
        label="synth tool",
        provider="synthetic",
        source="synthetic",
        available=True,
        availability_reason="Available",
    ))
    visible = _visible(family_hint="synthfam")
    assert "synthfam.tool" in visible
    # The lazy guard must not have pulled the production registry into this world.
    assert not any(i.startswith("machine.") for i in visible), visible
    assert cg.total_implementations() == 1


# ---------------------------------------------------------------------------
#  SAB1: remove population → the family-differentiation test must bite
# ---------------------------------------------------------------------------

def test_SAB1_removing_population_reverts_to_identical_fallback() -> None:
    """Sabotage: no-op the bootstrap (the exact zero-callers regression).

    Proves test_P2/test_P1 are non-vacuous: with population gone, every family
    hint collapses back to one identical fallback set and the graph stays
    empty — exactly the assertions those tests make, inverted.
    """
    saved_ensure = cg.ensure_registry_bootstrap
    saved_bootstrap = cg.bootstrap_from_registry
    try:
        cg.ensure_registry_bootstrap = lambda: False  # MALICIOUS: population removed
        cg.bootstrap_from_registry = lambda snapshot=None: 0

        ws = _visible(family_hint="workspace")
        web = _visible(family_hint="web")
        media = _visible(family_hint="media")

        # SABOTAGE EFFECTIVE — this is the base defect resurrected, and it is
        # precisely what test_P2 (differentiation) and test_P1 (population)
        # assert against:
        assert ws == web == media, "sabotage did not bite: hints still differentiate"
        assert cg.total_implementations() == 0, "sabotage did not bite: graph populated anyway"
    finally:
        cg.ensure_registry_bootstrap = saved_ensure
        cg.bootstrap_from_registry = saved_bootstrap


# ---------------------------------------------------------------------------
#  SAB2: destroy ranking → the read-only-first test must bite
# ---------------------------------------------------------------------------

def test_SAB2_destroying_ranking_puts_mutations_first() -> None:
    """Sabotage: invert the ranking key (mutations first).

    Proves test_P9/test_P9b are non-vacuous: with ranking destroyed, a
    mutation tool leads the media family ahead of the read-only inspector.
    """
    saved = cg._ranked_candidates
    try:
        def hostile_rank(candidates):  # MALICIOUS: writes first
            return list(reversed(saved(candidates)))

        cg._ranked_candidates = hostile_rank
        _bootstrap()
        visible = [i for i in _visible(family_hint="media") if i not in RESERVED]
        assert visible, "no media tools exposed under sabotage"
        # SABOTAGE EFFECTIVE — the first family tool is now a mutation, which
        # is exactly what test_P9 forbids:
        assert visible[0] != "media.inspect", (
            "sabotage did not bite: read-only tool still ranked first"
        )
    finally:
        cg._ranked_candidates = saved
