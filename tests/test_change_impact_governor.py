"""The change-impact governor has to be load-bearing, and this is where that is proven.

A governor that only prints advice is indistinguishable from a governor that is broken, so
every test here is behavioral: a real git repository is built in a temp directory, real changes
are made to it, real pytest subprocesses are run against it under the real tracer, and the
governor's verdict is read back. Nothing asserts that a function exists or that a string appears
in a source file.

The load-bearing claim is proven the only way it can be: by removing an impact relation and
showing the verdict flips. Two independent edge sources feed the governor -- a declared surface
in the registry and an observed execution in the proof map -- so the tests below take them away
one at a time. Removing one alone must NOT blind it (that redundancy is the false-negative
defence). Removing both must make it refuse the change it previously cleared, and never make it
fall silent.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from ops.impact.governor import (
    ProofMap,
    audit_registry,
    build_report,
    judge_execution,
    main,
    run_proofs,
    unit_exists,
)
from ops.impact.registry import DEFAULT_REGISTRY, load_registry, proof_matches
from ops.impact.units import MODULE_SCOPE, changed_units, index_units, qualname_for_line, split_unit, unit_key

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_REGISTRY = REPO_ROOT / DEFAULT_REGISTRY
REAL_PROOF_MAP = REPO_ROOT / "ops/impact/proof_map.json"

BEHAVIOR_SOURCE = '''\
"""A tiny product whose behavior a proof can hold."""

GREETING = "hello"


def shout(text):
    return text.upper() + "!"


def whisper(text):
    return text.lower()


class Formatter:
    def render(self, text):
        return shout(text)
'''

UNRELATED_SOURCE = '''\
def never_proven(value):
    return value * 2
'''

SCAFFOLD_SOURCE = '''\
def build_world():
    return {"ready": True}
'''

CONFTEST_SOURCE = '''\
import pytest

from pkg.scaffold import build_world


@pytest.fixture(autouse=True)
def world():
    return build_world()
'''

PROOF_SOURCE = '''\
import pytest

from pkg.behavior import Formatter, shout


def test_shouting_is_loud():
    assert shout("hi") == "HI!"


def test_formatter_renders_loudly():
    assert Formatter().render("hi") == "HI!"


@pytest.mark.skipif(True, reason="stands in for this repo's live-model-gated proofs")
def test_only_runs_against_a_live_model():
    assert shout("hi") == "HI!"
'''

SYNTHETIC_REGISTRY = {
    "version": 1,
    "governed_scope": ["pkg/behavior.py", "pkg/settings.json"],
    "invariants": [
        {
            "id": "INV-LOUD",
            "statement": "Shouting a phrase returns it upper-cased with an exclamation mark.",
            "scenario": "a user asks for a phrase to be shouted",
            "severity": "blocker",
            "kill": "make shout() return its input unchanged",
            "proofs": ["tests_syn/test_behavior.py"],
            "surfaces": ["pkg/behavior.py"],
            "assets": ["pkg/settings.json"],
        }
    ],
}


def _git(root: Path, *args: str) -> str:
    # The synthetic repository is a throwaway fixture: a machine whose git config signs every
    # commit must not make it fail for a reason that has nothing to do with the governor.
    completed = subprocess.run(
        ["git", "-c", "user.name=impact", "-c", "user.email=impact@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_registry(root: Path, payload: dict, name: str = "registry.yaml") -> Path:
    target = root / name
    target.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return target


@pytest.fixture
def synthetic(tmp_path: Path) -> dict:
    """A real git repository holding a tiny product, its proof, and a registry."""
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests_syn").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "behavior.py").write_text(BEHAVIOR_SOURCE, encoding="utf-8")
    (root / "pkg" / "unrelated.py").write_text(UNRELATED_SOURCE, encoding="utf-8")
    (root / "pkg" / "scaffold.py").write_text(SCAFFOLD_SOURCE, encoding="utf-8")
    (root / "pkg" / "settings.json").write_text('{"volume": 1}\n', encoding="utf-8")
    (root / "tests_syn" / "conftest.py").write_text(CONFTEST_SOURCE, encoding="utf-8")
    (root / "tests_syn" / "test_behavior.py").write_text(PROOF_SOURCE, encoding="utf-8")
    registry_path = _write_registry(root, SYNTHETIC_REGISTRY)

    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD").strip()

    registry = load_registry(registry_path)
    observed = run_proofs(root, ["tests_syn/test_behavior.py"], out_dir=tmp_path / "work")
    # Deliberately outside the repository: a file written into the tree under test would show up
    # as an untracked change and the governor would be correct to report it.
    map_path = tmp_path / "proof_map.json"
    map_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "proofs": {
                    nodeid: {
                        "outcome": observed.outcomes.get(nodeid, "unknown"),
                        "units": sorted(units),
                        "setup_units": sorted(observed.setup_units_by_proof.get(nodeid, frozenset())),
                    }
                    for nodeid, units in observed.units_by_proof.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "base": base,
        "registry": registry,
        "registry_path": registry_path,
        "proof_map": ProofMap.load(map_path),
        "map_path": map_path,
        "work": tmp_path / "work",
    }


def _edit(root: Path, relative: str, old: str, new: str) -> None:
    target = root / relative
    source = target.read_text(encoding="utf-8")
    assert old in source, f"fixture drifted: {old!r} is not in {relative}"
    target.write_text(source.replace(old, new), encoding="utf-8")


def _report(synthetic: dict, *, registry=None, proof_map=None):
    return build_report(
        synthetic["root"],
        registry if registry is not None else synthetic["registry"],
        proof_map if proof_map is not None else synthetic["proof_map"],
        synthetic["base"],
    )


# ----------------------------------------------------------------------------------------
# a diff becomes units, precisely
# ----------------------------------------------------------------------------------------


def test_a_change_inside_one_function_names_that_function_and_no_other(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert changed.units == ("pkg/behavior.py::shout",)


def test_a_deleted_function_is_still_named_by_the_diff(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", "def whisper(text):\n    return text.lower()\n", "")

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert "pkg/behavior.py::whisper" in changed.units
    assert not unit_exists(synthetic["root"], "pkg/behavior.py::whisper")


def test_an_import_time_constant_lands_on_module_scope_not_on_a_function(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'GREETING = "hello"', 'GREETING = "hey"')

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert changed.units == (f"pkg/behavior.py::{MODULE_SCOPE}",)


def test_a_method_is_named_the_way_the_interpreter_names_it(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", "return shout(text)", "return shout(text).strip()")

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert changed.units == ("pkg/behavior.py::Formatter.render",)


def test_a_brand_new_untracked_file_is_not_invisible(synthetic: dict) -> None:
    (synthetic["root"] / "pkg" / "sneaky.py").write_text("def moved_logic():\n    return 1\n", encoding="utf-8")

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert "pkg/sneaky.py::moved_logic" in changed.units


def test_renaming_governed_code_is_not_a_way_out_of_governance(synthetic: dict) -> None:
    """A pure rename emits no hunks at all -- no `---`, no `+++`, nothing to parse.

    Moving a governed file to a new name would otherwise be the simplest possible way to leave
    the registry's scope with nothing noticing.
    """
    _git(synthetic["root"], "mv", "pkg/behavior.py", "pkg/behaviour.py")

    changed = changed_units(synthetic["root"], synthetic["base"])
    report = _report(synthetic)

    assert "pkg/behavior.py::shout" in changed.units
    assert "pkg/behaviour.py::shout" in changed.units
    assert "INV-LOUD" in report.at_risk


def test_a_path_git_has_to_quote_still_resolves_to_its_units(synthetic: dict) -> None:
    """Non-ASCII paths come back C-quoted and escaped. Read literally they name no file at all,
    and the units inside vanish from the report -- a miss in the direction that looks clean."""
    (synthetic["root"] / "pkg" / "café.py").write_text("def brew():\n    return 1\n", encoding="utf-8")
    _git(synthetic["root"], "add", "-A")
    _git(synthetic["root"], "commit", "-qm", "add unicode module")
    (synthetic["root"] / "pkg" / "café.py").write_text("def brew():\n    return 2\n", encoding="utf-8")

    changed = changed_units(synthetic["root"], "HEAD")

    assert "pkg/café.py::brew" in changed.units


def test_a_file_that_does_not_parse_still_maps_to_something(synthetic: dict) -> None:
    """Half-finished code is a normal state to be in. It must not silently map to nothing."""
    (synthetic["root"] / "pkg" / "behavior.py").write_text("def shout(text:\n    return ???\n", encoding="utf-8")

    changed = changed_units(synthetic["root"], synthetic["base"])

    assert f"pkg/behavior.py::{MODULE_SCOPE}" in changed.units
    assert any(unit.startswith("pkg/behavior.py") for unit in changed.units)


def test_line_resolution_prefers_the_innermost_named_scope() -> None:
    source = textwrap.dedent(
        """\
        def outer():
            def inner():
                return 1
            return inner
        """
    )
    spans = index_units(source)

    assert qualname_for_line(spans, 3) == "outer.<locals>.inner"
    assert qualname_for_line(spans, 4) == "outer"


# ----------------------------------------------------------------------------------------
# units become named behaviors and named required proofs
# ----------------------------------------------------------------------------------------


def test_a_governed_change_names_the_behavior_at_risk_and_the_proof_that_holds_it(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    report = _report(synthetic)

    assert report.at_risk == ("INV-LOUD",)
    assert report.required_proofs == ("tests_syn/test_behavior.py",)
    assert report.unproven == ()
    assert report.blocking() == ()


def test_a_surface_does_not_claim_a_file_whose_name_merely_starts_the_same_way(synthetic: dict) -> None:
    """`core/task_router.py` must not swallow `core/task_router_extra.py`.

    Substring path matching is what produced the false closures this governor replaces, so the
    absence of it is asserted rather than assumed.
    """
    (synthetic["root"] / "pkg" / "behavior_extra.py").write_text("def helper():\n    return 2\n", encoding="utf-8")

    report = _report(synthetic)
    extra = next(impact for impact in report.impacts if impact.unit == "pkg/behavior_extra.py::helper")

    assert extra.invariants == ()
    assert extra.governed is False
    assert "INV-LOUD" not in report.at_risk


def test_a_change_outside_the_registry_is_reported_as_ungoverned_not_as_proven(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/unrelated.py", "return value * 2", "return value * 3")

    report = _report(synthetic)

    assert report.ungoverned == ("pkg/unrelated.py::never_proven",)
    assert report.unproven == ()
    assert report.at_risk == ()


def test_a_new_file_dropped_beside_governed_code_has_to_be_claimed(synthetic: dict) -> None:
    (synthetic["root"] / "pkg" / "sneaky.py").write_text("def moved_logic():\n    return 1\n", encoding="utf-8")

    report = _report(synthetic)

    assert report.newly_ungoverned == ("pkg/sneaky.py",)
    assert any("unclaimed" in problem for problem in report.blocking())


def test_a_changed_asset_requires_its_proofs_without_requiring_an_execution_witness(synthetic: dict) -> None:
    (synthetic["root"] / "pkg" / "settings.json").write_text('{"volume": 2}\n', encoding="utf-8")

    report = _report(synthetic)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    assert report.at_risk == ("INV-LOUD",)
    assert report.required_proofs == ("tests_syn/test_behavior.py",)
    assert report.unexecuted == ()
    assert report.blocking() == ()


# ----------------------------------------------------------------------------------------
# the part a green suite cannot do
# ----------------------------------------------------------------------------------------


def test_code_no_required_proof_ever_enters_is_refused_even_though_every_proof_passes(synthetic: dict) -> None:
    """The centre of the whole design.

    The change below is inside a governed file that an invariant claims, so a proof IS required
    and that proof passes. A full-suite run would be green and would report nothing. The
    governor still refuses, because nothing it ran ever entered the new code -- "no test
    contradicted you" is not the same claim as "this change is proven".
    """
    _edit(
        synthetic["root"],
        "pkg/behavior.py",
        "def whisper(text):",
        "def never_called(text):\n    return text[::-1]\n\n\ndef whisper(text):",
    )

    report = _report(synthetic)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    assert report.required_proofs == ("tests_syn/test_behavior.py",)
    assert report.failed_proofs == ()
    assert report.missing_proofs == ()
    assert "pkg/behavior.py::never_called" in report.unexecuted
    assert any("never executed" in problem for problem in report.blocking())


def test_a_change_the_proof_does_execute_is_cleared_by_the_same_gate(synthetic: dict) -> None:
    """The negative control for the test above: the gate must not refuse everything."""
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return (text.upper() + "!")')

    report = _report(synthetic)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    assert report.unexecuted == ()
    assert report.executed_by["pkg/behavior.py::shout"]
    assert report.blocking() == ()


def test_a_fixture_inside_the_proof_file_counts_as_the_proof_running(synthetic: dict) -> None:
    """Setup is scaffolding for product code. For the proof's own file it IS the proof.

    Without this the governor refuses every edit to a fixture in a governed test file, because
    fixtures never execute in the call phase -- a false refusal that would train people to
    stop governing their test files.
    """
    _edit(
        synthetic["root"],
        "tests_syn/test_behavior.py",
        "from pkg.behavior import Formatter, shout",
        "import pytest\n\nfrom pkg.behavior import Formatter, shout\n\n\n@pytest.fixture(autouse=True)\ndef _local_setup():\n    return 1",
    )
    payload = json.loads(json.dumps(SYNTHETIC_REGISTRY))
    payload["governed_scope"].append("tests_syn/test_behavior.py")
    registry = load_registry(_write_registry(synthetic["root"], payload, "registry_with_proof_governed.yaml"))

    report = _report(synthetic, registry=registry)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    assert "tests_syn/test_behavior.py::_local_setup" in report.changed_units
    assert report.unexecuted == ()
    assert report.blocking() == ()


def test_a_required_proof_that_fails_is_named(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', "return text")

    report = _report(synthetic)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    assert report.failed_proofs
    assert all(nodeid.startswith("tests_syn/test_behavior.py::") for nodeid in report.failed_proofs)
    assert any("required proof failed" in problem for problem in report.blocking())


def test_a_required_proof_that_never_ran_is_named(synthetic: dict) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    report = _report(synthetic)
    judge_execution(report, ProofMap(units_by_proof={}, outcomes={}))

    assert report.missing_proofs == ("tests_syn/test_behavior.py",)
    assert any("did not run" in problem for problem in report.blocking())


# ----------------------------------------------------------------------------------------
# the tracer is real
# ----------------------------------------------------------------------------------------


def test_the_proof_map_records_what_ran_and_omits_what_did_not(synthetic: dict) -> None:
    proof_map = synthetic["proof_map"]
    nodeid = "tests_syn/test_behavior.py::test_shouting_is_loud"

    assert proof_map.outcomes[nodeid] == "passed"
    assert "pkg/behavior.py::shout" in proof_map.units_by_proof[nodeid]
    assert "pkg/behavior.py::Formatter.render" not in proof_map.units_by_proof[nodeid]
    assert "pkg/unrelated.py::never_proven" not in proof_map.units_by_proof[nodeid]
    assert "pkg/behavior.py::Formatter.render" in proof_map.units_by_proof[
        "tests_syn/test_behavior.py::test_formatter_renders_loudly"
    ]


def test_fixture_scaffolding_is_recorded_but_is_never_treated_as_an_edge(synthetic: dict) -> None:
    """Setup builds the world; it does not prove anything about it.

    Every proof in this repository runs a database migration during setup. Counting that as an
    edge would make one storage helper require every proof in the registry, which is the
    "run everything" answer this governor exists to stop giving.
    """
    proof_map = synthetic["proof_map"]
    nodeid = "tests_syn/test_behavior.py::test_shouting_is_loud"

    assert "pkg/scaffold.py::build_world" in proof_map.setup_units_by_proof[nodeid]
    assert "pkg/scaffold.py::build_world" not in proof_map.units_by_proof[nodeid]
    assert nodeid in proof_map.scaffolding_only("pkg/scaffold.py::build_world")

    _edit(synthetic["root"], "pkg/scaffold.py", 'return {"ready": True}', 'return {"ready": False}')
    report = _report(synthetic)
    scaffold = next(impact for impact in report.impacts if impact.unit == "pkg/scaffold.py::build_world")

    assert scaffold.proofs == ()


def test_a_parametrised_proof_is_matched_by_selector_not_by_exact_node_id() -> None:
    assert proof_matches("tests/t.py::test_x", "tests/t.py::test_x[Return only raw text.]")
    assert proof_matches("tests/t.py", "tests/t.py::test_x")
    assert proof_matches("tests/t.py::TestC", "tests/t.py::TestC::test_m")
    assert not proof_matches("tests/t.py::test_x", "tests/t.py::test_xylophone")


# ----------------------------------------------------------------------------------------
# sabotage: break an impact relation, the verdict must flip
# ----------------------------------------------------------------------------------------


def _registry_without_surface(synthetic: dict) -> object:
    payload = json.loads(json.dumps(SYNTHETIC_REGISTRY))
    payload["invariants"][0]["surfaces"] = []
    payload["invariants"][0]["assets"] = ["pkg/settings.json"]
    return load_registry(_write_registry(synthetic["root"], payload, "registry_sabotaged.yaml"))


def _proof_map_without_observed_edge(synthetic: dict) -> ProofMap:
    payload = json.loads(synthetic["map_path"].read_text(encoding="utf-8"))
    for entry in payload["proofs"].values():
        entry["units"] = [unit for unit in entry["units"] if not unit.startswith("pkg/behavior.py")]
    return ProofMap.from_payload(payload)


def test_removing_the_declared_surface_alone_does_not_blind_the_governor(synthetic: dict) -> None:
    """Redundancy is the false-negative defence, so it is asserted, not hoped for.

    The declared edge and the observed edge are independent. Losing the declaration must not
    lose the behavior, because the proof was still seen executing the code.
    """
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    report = _report(synthetic, registry=_registry_without_surface(synthetic))

    assert report.at_risk == ("INV-LOUD",)
    assert report.required_proofs == ("tests_syn/test_behavior.py",)


def test_removing_the_observed_edge_alone_does_not_blind_the_governor(synthetic: dict) -> None:
    """The mirror control: losing the observation must not lose the declaration."""
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    report = _report(synthetic, proof_map=_proof_map_without_observed_edge(synthetic))

    assert report.at_risk == ("INV-LOUD",)
    assert report.required_proofs == ("tests_syn/test_behavior.py",)


def test_removing_both_impact_relations_makes_the_gate_refuse_the_change_it_had_cleared(synthetic: dict) -> None:
    """The load-bearing proof.

    With the relation intact the gate clears this change. With the relation gone -- declared
    surface removed AND observed edge removed -- the governor must refuse it as unproven. It
    must not quietly report "no behaviors at risk" and pass, which is the failure mode of every
    impact tool that only prints.
    """
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    intact = _report(synthetic)
    assert intact.blocking() == ()
    assert intact.at_risk == ("INV-LOUD",)

    sabotaged = _report(
        synthetic,
        registry=_registry_without_surface(synthetic),
        proof_map=_proof_map_without_observed_edge(synthetic),
    )

    assert sabotaged.at_risk == ()
    assert sabotaged.required_proofs == ()
    assert sabotaged.unproven == ("pkg/behavior.py::shout",)
    assert any("no required proof covers the change" in problem for problem in sabotaged.blocking())


def test_the_mutation_used_by_the_sabotage_actually_lands(synthetic: dict) -> None:
    """A string replace that matched nothing produces a green run that reads as a passing guard.

    So the sabotage's own edit is verified: the bytes changed, and they change back.
    """
    target = synthetic["root"] / "pkg" / "behavior.py"
    before = _digest(target)

    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', "return text")
    during = _digest(target)

    target.write_text(BEHAVIOR_SOURCE, encoding="utf-8")

    assert during != before
    assert _digest(target) == before


# ----------------------------------------------------------------------------------------
# the registry cannot lie
# ----------------------------------------------------------------------------------------


def _audit_with(synthetic: dict, mutate) -> list[str]:
    payload = json.loads(json.dumps(SYNTHETIC_REGISTRY))
    mutate(payload)
    registry = load_registry(_write_registry(synthetic["root"], payload, "registry_variant.yaml"))
    return audit_registry(synthetic["root"], registry, synthetic["proof_map"])


def test_a_declared_surface_that_no_proof_executes_is_reported_as_a_false_claim(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0]["surfaces"].append("pkg/unrelated.py"))

    assert any("claimed, not evidenced" in error and "pkg/unrelated.py" in error for error in errors)


def test_a_surface_that_does_not_exist_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0]["surfaces"].append("pkg/imaginary.py"))

    assert any("declared surface does not exist" in error for error in errors)


def test_a_proof_file_that_does_not_exist_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0]["proofs"].append("tests_syn/test_ghost.py"))

    assert any("proof file does not exist" in error for error in errors)


def test_a_proof_selector_matching_nothing_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0]["proofs"].append("tests_syn/test_behavior.py::test_absent"))

    assert any("matches nothing in the observed map" in error for error in errors)


def test_a_blocker_with_no_stated_sabotage_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0].update({"kill": ""}))

    assert any("does not state the sabotage" in error for error in errors)


def test_declaring_observable_code_as_unobservable_is_reported(synthetic: dict) -> None:
    """`assets` waives the execution witness. Taking that waiver for code that IS observed
    would downgrade a checkable edge into an unchecked assertion, so it is refused."""
    errors = _audit_with(synthetic, lambda p: p["invariants"][0]["assets"].append("pkg/behavior.py"))

    assert any("declared unobservable but" in error for error in errors)


def test_a_governed_file_no_invariant_claims_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["governed_scope"].append("pkg/unrelated.py"))

    assert any("no invariant claims it" in error for error in errors)


def test_an_invariant_with_no_proof_is_reported(synthetic: dict) -> None:
    errors = _audit_with(synthetic, lambda p: p["invariants"][0].update({"proofs": []}))

    assert any("has no proofs" in error for error in errors)


def test_an_intact_registry_audits_clean(synthetic: dict) -> None:
    """The negative control for the whole audit: it must not report defects that are not there."""
    assert audit_registry(synthetic["root"], synthetic["registry"], synthetic["proof_map"]) == []


# ----------------------------------------------------------------------------------------
# the registry this repository actually ships
# ----------------------------------------------------------------------------------------


def test_the_shipped_registry_is_structurally_valid() -> None:
    registry = load_registry(REAL_REGISTRY)

    assert registry.invariants
    assert all(inv.statement and inv.proofs for inv in registry.invariants)
    assert all(inv.kill for inv in registry.invariants if inv.severity == "blocker")


def test_the_shipped_registry_tells_the_truth_about_this_repository() -> None:
    """Every claim in the shipped registry, checked against the shipped proof map.

    This is the entry that goes red when someone deletes a proof, renames a surface, or lets
    an invariant drift away from the code that implements it.
    """
    if not REAL_PROOF_MAP.exists():
        pytest.skip("no recorded proof map; run `python ops/impact/governor.py record`")

    errors = audit_registry(REPO_ROOT, load_registry(REAL_REGISTRY), ProofMap.load(REAL_PROOF_MAP))

    assert errors == []


def test_the_shipped_registry_governs_this_governor() -> None:
    registry = load_registry(REAL_REGISTRY)
    invariant = registry.get("INV-GOVERNOR-IS-LOAD-BEARING")

    assert invariant is not None
    assert "ops/impact/governor.py" in invariant.surfaces
    assert registry.governs("ops/impact/governor.py")
    assert any(proof_matches(selector, f"{Path(__file__).name}") or "test_change_impact_governor" in selector
               for selector in invariant.proofs)


# ----------------------------------------------------------------------------------------
# the command line CI and agents actually call
# ----------------------------------------------------------------------------------------


def _cli(synthetic: dict, *args: str, fmt: str = "text") -> int:
    return main(
        [
            "--repo-root",
            str(synthetic["root"]),
            "--registry",
            str(synthetic["registry_path"]),
            "--proof-map",
            str(synthetic["map_path"]),
            "--format",
            fmt,
            *args,
        ]
    )


def test_the_command_line_clears_a_covered_change_with_exit_zero(synthetic: dict, capsys) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return (text.upper() + "!")')

    code = _cli(synthetic, "gate", "--base", synthetic["base"])
    printed = capsys.readouterr().out

    assert code == 0
    assert "VERDICT: CLEAR" in printed
    assert "INV-LOUD" in printed


def test_the_command_line_refuses_an_unproven_change_with_a_non_zero_exit(synthetic: dict, capsys) -> None:
    """CI reads the exit code, so the exit code is what gets asserted -- not the wording."""
    _edit(
        synthetic["root"],
        "pkg/behavior.py",
        "def whisper(text):",
        "def never_called(text):\n    return text[::-1]\n\n\ndef whisper(text):",
    )

    code = _cli(synthetic, "gate", "--base", synthetic["base"])
    printed = capsys.readouterr().out

    assert code == 1
    assert "VERDICT: BLOCKED" in printed
    assert "pkg/behavior.py::never_called" in printed


def test_the_machine_readable_form_carries_the_obligations_an_agent_needs(synthetic: dict, capsys) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    code = _cli(synthetic, "map", "--base", synthetic["base"], fmt="json")
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["at_risk_invariants"] == ["INV-LOUD"]
    assert payload["required_proofs"] == ["tests_syn/test_behavior.py"]
    assert payload["unproven_units"] == []
    assert payload["blocking"] == []


def test_the_github_form_annotates_the_file_that_needs_the_work(synthetic: dict, capsys) -> None:
    (synthetic["root"] / "pkg" / "sneaky.py").write_text("def moved_logic():\n    return 1\n", encoding="utf-8")

    code = _cli(synthetic, "map", "--base", synthetic["base"], fmt="github")
    printed = capsys.readouterr().out

    assert code == 1
    assert "::error file=pkg/sneaky.py::" in printed


def test_the_offline_form_judges_a_trace_produced_by_another_run(synthetic: dict, capsys) -> None:
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    code = _cli(synthetic, "verify", "--base", synthetic["base"], "--trace", str(synthetic["map_path"]))
    printed = capsys.readouterr().out

    assert code == 0
    assert "VERDICT: CLEAR" in printed


def test_a_fresh_audit_catches_an_edge_the_stored_map_still_asserts(synthetic: dict, capsys) -> None:
    """The recorded map is evidence from whenever `record` last ran.

    A surface can quietly stop being reached because of a change somewhere else entirely, and
    the stored map goes on asserting the edge. `--fresh` re-earns every edge from a run
    happening now, so the stale claim has nowhere to hide.
    """
    stale = json.loads(synthetic["map_path"].read_text(encoding="utf-8"))
    for entry in stale["proofs"].values():
        entry["units"] = sorted(set(entry["units"]) | {"pkg/unrelated.py::never_proven"})
    stale_path = synthetic["root"].parent / "stale_map.json"
    stale_path.write_text(json.dumps(stale, indent=2), encoding="utf-8")

    payload = json.loads(json.dumps(SYNTHETIC_REGISTRY))
    payload["invariants"][0]["surfaces"].append("pkg/unrelated.py")
    registry_path = _write_registry(synthetic["root"], payload, "registry_stale_edge.yaml")
    base = ["--repo-root", str(synthetic["root"]), "--registry", str(registry_path), "--proof-map", str(stale_path)]

    assert main([*base, "audit"]) == 0
    capsys.readouterr()

    assert main([*base, "audit", "--fresh"]) == 1
    assert "claimed, not evidenced" in capsys.readouterr().out


def test_the_audit_command_reports_a_clean_registry_and_a_dirty_one(synthetic: dict, capsys) -> None:
    assert _cli(synthetic, "audit") == 0
    assert "VERDICT: CLEAR" in capsys.readouterr().out

    payload = json.loads(json.dumps(SYNTHETIC_REGISTRY))
    payload["invariants"][0]["surfaces"].append("pkg/imaginary.py")
    broken = _write_registry(synthetic["root"], payload, "registry_broken.yaml")

    code = main(["--repo-root", str(synthetic["root"]), "--registry", str(broken),
                 "--proof-map", str(synthetic["map_path"]), "audit"])

    assert code == 1
    assert "VERDICT: BLOCKED" in capsys.readouterr().out


def test_recording_rewrites_the_observed_map_from_a_real_run(synthetic: dict, tmp_path: Path) -> None:
    target = tmp_path / "recorded.json"

    code = main(["--repo-root", str(synthetic["root"]), "--registry", str(synthetic["registry_path"]),
                 "--proof-map", str(target), "record", "--work-dir", str(tmp_path / "rec-work")])
    recorded = ProofMap.load(target)

    assert code == 0
    assert "pkg/behavior.py::shout" in recorded.units_by_proof["tests_syn/test_behavior.py::test_shouting_is_loud"]
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == 3


def test_the_format_flag_works_on_the_side_of_the_subcommand_a_person_types_it(synthetic: dict, capsys) -> None:
    """`governor map --base X --format json` is what gets typed first, so it has to work."""
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return text.upper() + "!!"')

    code = main(
        [
            "--repo-root", str(synthetic["root"]),
            "--registry", str(synthetic["registry_path"]),
            "--proof-map", str(synthetic["map_path"]),
            "map", "--base", synthetic["base"], "--format", "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["at_risk_invariants"] == ["INV-LOUD"]


def test_a_broken_base_reference_fails_loudly_rather_than_silently_clearing(synthetic: dict) -> None:
    """Every path through main() used to return 0. An impact tool that cannot fail is decor."""
    assert _cli(synthetic, "map", "--base", "no-such-ref-anywhere") == 2


def test_unit_keys_round_trip() -> None:
    assert split_unit(unit_key("core/a.py", "Foo.bar")) == ("core/a.py", "Foo.bar")
    assert split_unit(unit_key("config/x.json", None)) == ("config/x.json", None)


def test_a_skipped_proof_is_not_a_failed_proof_but_is_not_a_witness_either(synthetic: dict) -> None:
    """Several proofs in this repository are gated on a live local model.

    Calling an environment skip a failure would make the gate cry wolf on the first change that
    touched one, and a gate that cries wolf gets switched off. The skip still costs something:
    a proof that never ran cannot witness anything, which the execution check picks up exactly.
    """
    _edit(synthetic["root"], "pkg/behavior.py", 'return text.upper() + "!"', 'return (text.upper() + "!")')

    report = _report(synthetic)
    observed = run_proofs(synthetic["root"], list(report.required_proofs), out_dir=synthetic["work"])
    judge_execution(report, observed)

    skipped = [n for n, outcome in observed.outcomes.items() if outcome == "skipped"]

    assert skipped, "fixture drifted: the synthetic proof file no longer contains a skipped test"
    assert report.failed_proofs == ()
    assert report.blocking() == ()
    assert all(unit not in observed.units_by_proof[skipped[0]] for unit in ("pkg/behavior.py::shout",))
