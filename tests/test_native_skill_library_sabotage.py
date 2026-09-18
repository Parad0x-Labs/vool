"""Sabotage proofs — every load-bearing guard of the native skill library.

Each test removes ONE mechanism (CLAUDE.md §6b.4) and asserts the observable the pinning
pack (tests/test_native_skill_library.py) protects CHANGES. A guard whose removal changes
nothing is decoration. Mutations are monkeypatches / law-table edits, so every test restores
the world on teardown.
"""
from __future__ import annotations

import pytest

from tests.test_native_skill_library import (
    _iso_home,
    _fixture_library,
    make_native_skill,
)


@pytest.fixture()
def native_world(tmp_path, monkeypatch):
    from core.runtime_flags import override

    _iso_home(tmp_path, monkeypatch)
    lib = tmp_path / "native-skills"
    lib.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(lib))
    from core import tool_offer_assembly

    tool_offer_assembly.reset_skill_cache()
    with override("plugin_runtime_tools", True):
        yield lib
    tool_offer_assembly.reset_skill_cache()


def test_sabotage_typed_gate_replaced_by_always_select_flooding_chat_turns(native_world, monkeypatch) -> None:
    """Guard: only a typed signal (task class / demand) may select a skill."""
    from core import native_skill_library as nsl

    _fixture_library(native_world)
    healthy = nsl.select_native_skills(task_class="chat_conversation", user_text="a bedtime story, please")
    assert healthy.selected == ()

    original = nsl.select_native_skills

    def flooded(*, task_class: str = "", user_text: str = "", limit: int = 2, library=None):
        result = original(task_class=task_class, user_text=user_text, limit=limit, library=library)
        loaded = library or nsl.load_native_library()
        return nsl.SkillSelection(selected=loaded.contracts[:limit], records=result.records)

    monkeypatch.setattr(nsl, "select_native_skills", flooded)
    sabotaged = nsl.select_native_skills(task_class="chat_conversation", user_text="a bedtime story, please")
    # The observable the pinning test protects: an irrelevant turn now gets skills injected.
    assert sabotaged.selected != (), "sabotage no-op: the typed gate is not load-bearing"


def test_sabotage_disable_gate_ignored_reenables_a_switched_off_skill(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl

    _fixture_library(native_world)
    nsl.set_skill_enabled("security-audit", False)
    assert all(c.id != "security-audit" for c in nsl.select_native_skills(
        task_class="security_hardening", user_text="harden this").selected)

    monkeypatch.setattr(nsl, "_read_disabled_ids", lambda: set())
    sabotaged = nsl.select_native_skills(task_class="security_hardening", user_text="harden this")
    assert "security-audit" in {c.id for c in sabotaged.selected}, (
        "sabotage no-op: the disable gate is not load-bearing"
    )


def test_sabotage_prerequisite_gate_removed_honours_unmeetable_prerequisites(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl

    make_native_skill(native_world, "needs-missing-binary", overrides={
        "task-families": ["workspace_audit"],
        "prerequisites": ["binary:no-such-binary-xyz"],
    })
    healthy = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert all(c.id != "needs-missing-binary" for c in healthy.selected)

    monkeypatch.setattr(nsl, "_unmet_prerequisites", lambda contract: [])
    sabotaged = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert "needs-missing-binary" in {c.id for c in sabotaged.selected}, (
        "sabotage no-op: the prerequisite gate is not load-bearing"
    )


def test_sabotage_capability_gate_removed_injects_unhonourable_skills(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl

    make_native_skill(native_world, "beyond-skill", overrides={
        "task-families": ["workspace_audit"],
        "capabilities": ["sky.fly"],
    })
    healthy = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert all(c.id != "beyond-skill" for c in healthy.selected)

    monkeypatch.setattr(nsl, "_unmet_capabilities", lambda contract: ())
    sabotaged = nsl.select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert "beyond-skill" in {c.id for c in sabotaged.selected}, (
        "sabotage no-op: the capability gate is not load-bearing"
    )


def test_sabotage_verification_law_erased_lets_an_unverified_skill_load(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl

    make_native_skill(native_world, "root-cause-repair", overrides={
        "task-families": ["debugging"],
        # Missing the REQUIRED_VERIFICATION tokens; carries the symptom doctrine so ONLY the
        # verification law is under test here.
        "verification": ["evidence_cited"],
        "stopping-conditions": ["symptom-only closure is not a stopping condition — name the mechanism"],
    })
    healthy = nsl.load_native_library()
    assert any(entry.id == "root-cause-repair" for entry in healthy.invalid)

    monkeypatch.setattr(nsl, "REQUIRED_VERIFICATION", {})
    sabotaged = nsl.load_native_library()
    assert all(entry.id != "root-cause-repair" for entry in sabotaged.invalid) and any(
        c.id == "root-cause-repair" for c in sabotaged.contracts
    ), "sabotage no-op: the load-time verification law is not load-bearing"


def test_sabotage_symptom_only_law_erased_lets_symptom_closure_back_in(native_world, monkeypatch) -> None:
    from core import native_skill_library as nsl

    make_native_skill(native_world, "root-cause-repair", overrides={
        "task-families": ["debugging"],
        "verification": ["failing_test_reproduces", "root_cause_state_confirmed"],
        # NOTE: the doctrine stopping condition is MISSING — the law must refuse this.
        "stopping-conditions": ["stop when the question is answered"],
    })
    healthy = nsl.load_native_library()
    assert any(entry.id == "root-cause-repair" for entry in healthy.invalid)

    # Removing the law = the required token is satisfied vacuously (empty string is in all text).
    monkeypatch.setattr(nsl, "SYMPTOM_ONLY_CLOSURE_TOKEN", "")
    sabotaged = nsl.load_native_library()
    assert all(entry.id != "root-cause-repair" for entry in sabotaged.invalid) and any(
        c.id == "root-cause-repair" for c in sabotaged.contracts
    ), "sabotage no-op: the symptom-only closure law is not load-bearing"


def test_sabotage_narrowing_intersection_removed_seats_unavailable_tools(
    native_world, tmp_path, monkeypatch
) -> None:
    """Guard: a matched skill's allowed-tools intersect the OFFERED set — they can never seat
    a tool the runtime does not offer. (The lever lives on the PLUGIN lexical-match path; a
    native match rests on task class alone and deliberately does not narrow — a task-class
    signal is too weak to resize the model's toolbelt with.) The mutation turns the lever
    into a widening one by injecting the skill's named tools as specs."""
    from tests._toolchain_fixtures import make_plugin, reset_toolchain_state, widget_skill

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    make_plugin(tmp_path, skills={
        "greedy-report": widget_skill(allowed_tools="[pack.echo, email.send]"),
    })
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    reset_toolchain_state()
    from core import tool_offer_assembly

    healthy = tool_offer_assembly.assemble_tool_offer(
        user_text="prepare the widget report", task_class="unknown",
    )
    assert "email.send" not in {s["intent"] for s in healthy.specs}

    def widening(specs, *, allowed, keep):
        seated = {str(s.get("intent") or "") for s in specs}
        for name in allowed:
            if name not in seated:
                specs = list(specs) + [{"intent": name, "description": "sabotage-seated"}]
        return specs

    monkeypatch.setattr(tool_offer_assembly, "_narrow", widening)
    sabotaged = tool_offer_assembly.assemble_tool_offer(
        user_text="prepare the widget report", task_class="unknown",
    )
    reset_toolchain_state()
    assert "email.send" in {s["intent"] for s in sabotaged.specs}, (
        "sabotage no-op: removing the intersection law does not change the offer, so "
        "never-widen is decoration"
    )


def test_sabotage_process_global_selection_memo_leaks_between_turns(native_world, monkeypatch) -> None:
    """Guard: selection is a pure per-turn function — no process-local last-answer memo."""
    from core import native_skill_library as nsl

    _fixture_library(native_world)
    repair = nsl.select_native_skills(
        task_class="debugging",
        user_text="the test suite fails after my change, run the tests and find the root cause",
    )
    assert repair.selected

    memo: dict = {}

    # Signature-agnostic on purpose: a double that pins the real function's keyword
    # defaults silently stops matching it the moment one moves (a stale `limit=2`
    # made this sabotage compare a 2-skill memo against a 3-skill live call and
    # report a no-op that was really a drifted fixture).
    def memoized(**kwargs):
        if "last" not in memo:
            memo["last"] = original(**kwargs)
        return memo["last"]

    original = nsl.select_native_skills
    monkeypatch.setattr(nsl, "select_native_skills", memoized)
    # First patched call fills the memo with the repair turn; the chat turn must then receive
    # the STALE repair selection — the leak the no-shared-state guard prevents.
    memoized(task_class="debugging", user_text="the test suite fails, run the tests")
    leaked = nsl.select_native_skills(task_class="chat_conversation", user_text="a bedtime story, please")
    assert leaked.selected == repair.selected, (
        "sabotage no-op: a process-global memo does not change behaviour, so the "
        "no-shared-state guard is decoration"
    )


def test_sabotage_in_memory_config_store_loses_state_across_restarts(native_world, monkeypatch) -> None:
    """Guard: the config store is the DISK, read fresh — never a process-local copy. The
    mutation makes enable/disable an in-memory set only; the restart path (a fresh read of
    the disk store) must then LOSE the state, proving persistence is load-bearing."""
    import json
    from pathlib import Path

    from core import native_skill_library as nsl

    _fixture_library(native_world)
    healthy = nsl.set_skill_enabled("repo-onboarding", False)
    assert healthy["status"] == "ok"
    store = Path(nsl._enabled_store_path())
    assert "repo-onboarding" in (json.loads(store.read_text()).get("disabled") or [])
    nsl.set_skill_enabled("repo-onboarding", True)  # reset the disk for the sabotage run

    memory_store: set[str] = set()

    def memory_write(skill_id: str, enabled: bool):
        if enabled:
            memory_store.discard(skill_id)
        else:
            memory_store.add(skill_id)
        return {"status": "ok", "id": skill_id, "enabled": bool(enabled)}

    monkeypatch.setattr(nsl, "set_skill_enabled", memory_write)
    assert memory_write("repo-onboarding", False)["status"] == "ok"
    # The "restart": whatever a fresh process sees is what the DISK holds.
    on_disk_after = (json.loads(store.read_text()).get("disabled") or []) if store.is_file() else []
    assert "repo-onboarding" not in on_disk_after, (
        "sabotage no-op: an in-memory store still reaches the disk, so the persistence "
        "guard is decoration"
    )


# ---------------------------------------------------------------------------
# Reconciliation guards (served injection, source validation, context budget)
# ---------------------------------------------------------------------------


def test_sabotage_prompt_injection_removed_strips_guidance_from_the_seam(native_world, monkeypatch) -> None:
    """Guard: the chat/action_plan prompt binds the selected skills' guidance. Neutralise the
    guidance source and the bound prompt must LOSE it — proving the seam test bites."""
    from types import SimpleNamespace

    from core import tool_offer_assembly

    make_native_skill(native_world, "inject-probe", overrides={
        "task-families": ["debugging"], "priority": 1,
    }, body="INJECTION_MARKER do the repair dance\n")
    healthy = tool_offer_assembly.skill_guidance_for(
        "the suite fails, find the root cause", task_class="debugging",
    )
    assert "INJECTION_MARKER" in healthy.text

    monkeypatch.setattr(
        tool_offer_assembly, "skill_guidance_for",
        lambda *a, **k: tool_offer_assembly.SkillGuidance(),
    )
    sabotaged = tool_offer_assembly.skill_guidance_for(
        "the suite fails, find the root cause", task_class="debugging",
    )
    assert sabotaged.text == "", (
        "sabotage no-op: removing the guidance source does not change the bound prompt"
    )


def test_sabotage_mcp_source_validation_skipped_lets_invalid_skills_ride(
    native_world, tmp_path, monkeypatch
) -> None:
    """Guard: MCP-shipped skills pass the SAME contract law. Skip the validation and an invalid
    MCP skill would be selected — the failure the convergence exists to prevent."""
    from core import native_skill_library as nsl

    bad_front = {
        "id": "mcp-pack-probe", "name": "mcp-pack-probe", "version": "1.0.0",
        "description": "invalid", "risk-class": "read_only",
        "task-families": ["not-a-family"], "stopping-conditions": ["stop"],
    }
    bad = nsl.contract_from_frontmatter(bad_front, source="mcp:stub")
    healthy = nsl.load_skill_contracts()
    assert all(c.id != "mcp-pack-probe" for c in healthy.contracts)

    monkeypatch.setattr(
        nsl, "load_mcp_skill_contracts",
        lambda: nsl.LibraryLoad(contracts=(bad,), invalid=()),
    )
    sabotaged = nsl.load_skill_contracts()
    assert any(c.id == "mcp-pack-probe" for c in sabotaged.contracts), (
        "sabotage no-op: skipping MCP validation changes nothing"
    )


def test_sabotage_budget_ceiling_raised_lets_context_flood(native_world, monkeypatch) -> None:
    """Guard: the guidance budget is a hard ceiling. Raising it must let the flood through —
    proving the budget, not luck, bounds the context."""
    from core import tool_offer_assembly

    for i in range(6):
        make_native_skill(native_world, f"bulk-skill-{i}", overrides={
            "task-families": ["workspace_audit"], "priority": 10 + i,
        }, body=("WORK " * 400) + f"MARKER_{i}\n")
    healthy = tool_offer_assembly.assemble_tool_offer(
        user_text="audit the workspace files", task_class="workspace_audit",
    )
    healthy_len = len(healthy.skill_guidance.text)
    # assemble_tool_offer is the TOOL-INTENT lane, so its ceiling is that lane's share.
    assert healthy_len <= (
        tool_offer_assembly.MAX_TOOL_LANE_SKILL_CHARS
        + tool_offer_assembly.MAX_TOOL_LANE_DOCTRINE_CHARS
    ), healthy_len

    # Raise the ceilings this lane actually spends against. Naming only the old global
    # constant would have made the sabotage a no-op and reported the guard as
    # decoration, when the guard had simply moved.
    monkeypatch.setattr(tool_offer_assembly, "MAX_TOOL_LANE_SKILL_CHARS", 100_000)
    monkeypatch.setattr(tool_offer_assembly, "MAX_TOOL_LANE_DOCTRINE_CHARS", 100_000)
    monkeypatch.setattr(tool_offer_assembly, "MAX_SKILL_CHARS", 100_000)
    monkeypatch.setattr(tool_offer_assembly, "MAX_DOCTRINE_CHARS", 100_000)
    sabotaged = tool_offer_assembly.assemble_tool_offer(
        user_text="audit the workspace files", task_class="workspace_audit",
    )
    assert len(sabotaged.skill_guidance.text) > healthy_len, (
        "sabotage no-op: raising the ceiling does not change the bound text "
        f"(healthy={healthy_len}, sabotaged={len(sabotaged.skill_guidance.text)})"
    )


def test_browser_render_is_typed_unavailable_not_silent() -> None:
    """Matrix gap: no browser backend is wired here — the door must return a TYPED unavailable
    result, never None (which would read as success to a skill-guided turn)."""
    from tests._toolchain_fixtures import reset_toolchain_state

    reset_toolchain_state()
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool("browser.render", {"url": "https://example.com"})
    reset_toolchain_state()
    assert result is not None, "browser.render returned None — silent absence"
    assert result.handled is True and result.ok is False
    assert result.status == "browser_backend_unavailable"


def test_machine_read_capability_resolves_from_runtime_evidence() -> None:
    """Matrix gap: the machine contracts used to declare a dead `machine.read` id that resolved
    to nothing while the tools themselves worked. They now declare the graph's real
    `filesystem.read` capability — implemented, not refused."""
    from core.capability_graph import (
        capabilities_for_skill,
        implementations_for_capability,
        ensure_registry_bootstrap,
    )
    from core.runtime_tool_contracts import runtime_tool_contract_map

    assert runtime_tool_contract_map()["machine.read_file"].capability_id == "filesystem.read"
    ensure_registry_bootstrap()
    resolved = capabilities_for_skill("", ["filesystem.read"])
    assert resolved
    assert any(i.available for c in resolved for i in implementations_for_capability(c))
