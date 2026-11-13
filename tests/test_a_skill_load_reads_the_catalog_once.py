"""A skill library load reads the tool catalog once, and parses a file again only when its text changes.

Measured on CI #681 (the v0.8 CI branch at c8ae4380, shard 0): the semantic replay corpus ran past
its shard budget (4302 s, 1.5 times the 2868 s it took on main #680) with the stack in
``native_skill_library.contract_violations``. On a 24-turn sample of that corpus, on the branch's
head b9530c3c:

- every library load parsed each SKILL.md header twice and rebuilt the whole tool catalog
  (218 contracts) once per contract, only to read its names;
- every selection brought the capability graph up to date once per skill that declares
  capabilities, and each of those checks rebuilt the catalog again;
- every demand-signal reading rebuilt the catalog twice, once for each dotted-name reader.

The catalog was rebuilt 206 times a turn (97 on main), and a turn took 2.70 s (2.00 s on main).

These pins keep that work at one read per load, per selection and per reading, and keep every
check live: a file edit, a changed law and a tool the runtime gains are each judged by the very
next load.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from tests.test_native_skill_library import _iso_home, make_native_skill


@pytest.fixture()
def native_world(tmp_path, monkeypatch):
    from core import native_skill_library as nsl

    _iso_home(tmp_path, monkeypatch)
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "home" / "plugin_lifecycle.json"))
    lib = tmp_path / "native-skills"
    lib.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(lib))
    nsl.reset_contract_caches()
    yield lib
    nsl.reset_contract_caches()


def _counted(monkeypatch, module, name: str) -> list[int]:
    real = getattr(module, name)
    calls: list[int] = []

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counted)
    return calls


def test_a_load_reads_the_tool_catalog_once(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl
    from core import runtime_tool_contracts

    for index in range(6):
        make_native_skill(native_world, f"fixture-skill-{index}")
    reads = _counted(monkeypatch, runtime_tool_contracts, "runtime_tool_contract_map")
    load = nsl.load_native_library()
    assert len(load.contracts) == 6 and not load.invalid
    assert len(reads) == 1, f"one catalog read per load, not one per contract: {len(reads)}"


def test_a_tool_the_runtime_gains_is_seen_by_the_next_load(native_world, monkeypatch) -> None:
    """The tool names are read on every load and never kept: the catalog's names move with the
    lanes a person switches on."""
    from core import native_skill_library as nsl
    from core import runtime_tool_contracts

    make_native_skill(native_world, "needs-a-new-tool", overrides={
        "tool-intents": ["workspace.read_file", "fixture.new_tool"],
    })
    before = nsl.load_native_library()
    assert [error.id for error in before.invalid] == ["needs-a-new-tool"]
    assert any("fixture.new_tool" in violation for violation in before.invalid[0].violations)

    real = runtime_tool_contracts.runtime_tool_contract_map
    monkeypatch.setattr(
        runtime_tool_contracts, "runtime_tool_contract_map", lambda: {**real(), "fixture.new_tool": None}
    )
    after = nsl.load_native_library()
    assert [contract.id for contract in after.contracts] == ["needs-a-new-tool"] and not after.invalid


def test_an_edit_is_judged_by_the_next_load_at_the_same_size_and_time(native_world) -> None:
    from core import native_skill_library as nsl

    make_native_skill(native_world, "edited-skill")
    path = native_world / "edited-skill" / "SKILL.md"
    assert [contract.id for contract in nsl.load_native_library().contracts] == ["edited-skill"]

    stat = path.stat()
    text = path.read_text(encoding="utf-8")
    edited = text.replace('risk-class: "read_only"', 'risk-class: "readXonly"')
    assert edited != text and len(edited) == len(text)
    path.write_text(edited, encoding="utf-8")
    # Same length and the same timestamps: only the text says the file changed.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    load = nsl.load_native_library()
    assert not load.contracts
    assert any("risk-class" in violation for error in load.invalid for violation in error.violations)


def test_sabotage_a_parse_kept_by_anything_but_the_text_misses_the_edit(native_world, monkeypatch) -> None:
    """Guard: the reused parse is keyed on the file's TEXT. Keyed on anything blind to it (one
    constant here; a clock or a size in the wild, both of which an edit can leave as they were),
    the next load judges the old text."""
    from core import native_skill_library as nsl

    make_native_skill(native_world, "edited-skill")
    path = native_world / "edited-skill" / "SKILL.md"
    monkeypatch.setattr(
        nsl, "hashlib", SimpleNamespace(sha256=lambda _data: SimpleNamespace(hexdigest=lambda: "blind"))
    )
    assert [contract.id for contract in nsl.load_native_library().contracts] == ["edited-skill"]
    text = path.read_text(encoding="utf-8")
    edited = text.replace('risk-class: "read_only"', 'risk-class: "readXonly"')
    assert edited != text
    path.write_text(edited, encoding="utf-8")
    stale = nsl.load_native_library()
    assert [contract.id for contract in stale.contracts] == ["edited-skill"], (
        "sabotage no-op: a text-blind parse still sees the edit, so the text key is decoration"
    )


def test_a_changed_law_is_applied_by_the_next_load(native_world, monkeypatch) -> None:
    """Only the parse is reused: the checks run on every load, so a law edited in place applies
    at once to a file that did not change."""
    from core import native_skill_library as nsl

    make_native_skill(native_world, "plain-skill")
    assert [contract.id for contract in nsl.load_native_library().contracts] == ["plain-skill"]
    monkeypatch.setitem(nsl.REQUIRED_VERIFICATION, "plain-skill", frozenset({"served_proof"}))
    load = nsl.load_native_library()
    assert not load.contracts and [error.id for error in load.invalid] == ["plain-skill"]


def test_a_selection_brings_the_capability_graph_up_to_date_once(native_world, monkeypatch) -> None:
    from core import capability_graph
    from core import native_skill_library as nsl

    for index in range(4):
        make_native_skill(native_world, f"capable-skill-{index}", overrides={"capabilities": ["sky.fly"]})
    checks = _counted(monkeypatch, capability_graph, "ensure_registry_bootstrap")

    selection = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert len(checks) == 1, f"one graph check per selection, not one per skill: {len(checks)}"
    states = {record.id: record.state for record in selection.records}
    assert all(states[f"capable-skill-{index}"] == "capability_unavailable" for index in range(4))

    checks.clear()
    rows = {row["id"]: row for row in nsl.skill_inventory(native_world)}
    assert len(checks) == 1 and not any(rows[f"capable-skill-{index}"]["available"] for index in range(4))

    # Outside a pass over the library, a lone check still brings the graph up to date itself.
    checks.clear()
    assert nsl._unmet_capabilities(nsl.load_native_library().contracts[0])
    assert len(checks) == 1


def test_a_graph_that_cannot_answer_refuses_every_capable_skill_of_the_pass(native_world, monkeypatch) -> None:
    from core import capability_graph
    from core import native_skill_library as nsl

    for index in range(3):
        make_native_skill(native_world, f"capable-skill-{index}", overrides={"capabilities": ["workspace"]})
    healthy = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert {f"capable-skill-{index}" for index in range(3)} & {contract.id for contract in healthy.selected}

    def graph_down():
        raise RuntimeError("the graph cannot answer")

    monkeypatch.setattr(capability_graph, "ensure_registry_bootstrap", graph_down)
    refused = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert not refused.selected
    states = {record.id: record.state for record in refused.records}
    assert all(states[f"capable-skill-{index}"] == "capability_unavailable" for index in range(3))


def test_a_demand_reading_reads_the_tool_registry_once(monkeypatch) -> None:
    from core import tool_registry
    from core.tool_demand_signals import resolve_demand_signals

    text = "use vool-browser.inspect on the page I have open"
    first = resolve_demand_signals(text)  # warms the plugin vocabulary, which keeps its own copy
    assert "vool-browser.inspect" in first.explicit_intents
    reads = _counted(monkeypatch, tool_registry, "registered_tools")
    again = resolve_demand_signals(text)
    assert again == first
    assert len(reads) == 1, f"the two dotted-name readers share one registry read: {len(reads)}"
