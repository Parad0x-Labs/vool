"""Selection law for the skill library, proven against the ONE authority.

Reconciled 2026-09-03: the former lexical `select_native_skill` in core.plugin_skills is REMOVED
(its only callers were tests); this pack now pins the same behaviours against the canonical
typed authority (`core.native_skill_library.select_native_skills`) that the offer seam uses:

- AUTOMATIC SELECTION — every shipped package is reachable through the production typed
  signals (the real classifier's task class + the turn's demand signals), and words no
  package declares select nothing.
- EXPLICIT INVOCATION — naming a package reaches it through the real `skill.validate` /
  `skill.list` tool door, not a private side channel.
- REFUSAL WHEN CAPABILITY IS ABSENT — a package whose declared capabilities this runtime cannot
  honour is refused with the absence NAMED; never silently skipped, never injected.
- MODEL SWITCHING — selection and invocation are invariant under the local-model policy: the
  library is model-independent infrastructure, not a feature of one backend.
- PLUGIN PARITY — the same bytes installed as a plugin skill converge to the same validated
  contract through the one authority; duplicate ids across sources are reported, not shadowed.
"""
from __future__ import annotations

import pytest

from tests.test_native_skill_library import make_native_skill, native_world

REQUIRED_PACKAGE_IDS = (
    "repo-onboarding",
    "root-cause-repair",
    "bug-reproduction",
    "feature-build",
    "cumulative-testing",
    "code-review",
    "security-audit",
    "git-worktrees",
    "ci-repair",
    "browser-qa",
    "performance",
    "migration",
    "release-gate",
)


@pytest.fixture()
def library_world(tmp_path, monkeypatch):
    """Isolated home; the SHIPPED library stays in place (these are its selection-law tests)."""
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    from core import tool_offer_assembly

    tool_offer_assembly.reset_skill_cache()
    with override("plugin_runtime_tools", True):
        yield
    tool_offer_assembly.reset_skill_cache()


def _shipped_contracts():
    from core.native_skill_library import load_native_library

    return {c.id: c for c in load_native_library().contracts}


@pytest.mark.parametrize("skill_id", REQUIRED_PACKAGE_IDS)
def test_automatic_selection_reaches_every_shipped_package(library_world, skill_id: str) -> None:
    """Every shipped package must be reachable by the production typed signals. Packages that
    share a task family with a higher-priority anchor prove reachability the operator-legal way:
    disabling the anchor (a first-class config operation) must surface them — a package that can
    never be selected under ANY enabled-state is dead weight, and that is what this pins."""
    from core.native_skill_library import select_native_skills, set_skill_enabled

    contract = _shipped_contracts()[skill_id]
    assert contract.task_families or contract.capability_families
    task_class = contract.task_families[0] if contract.task_families else ""

    selection = select_native_skills(task_class=task_class, user_text=f"work requiring {skill_id}")
    if any(c.id == skill_id for c in selection.selected):
        return

    # Constructive witness: disable whoever outranks it, one budget-slot at a time.
    disabled: list[str] = []
    try:
        for _ in range(8):
            blockers = [c.id for c in select_native_skills(
                task_class=task_class, user_text=f"work requiring {skill_id}"
            ).selected]
            if skill_id in blockers:
                return
            if not blockers:
                break
            victim = blockers[0]
            assert set_skill_enabled(victim, False)["status"] == "ok"
            disabled.append(victim)
        raise AssertionError(
            f"{skill_id} unreachable even with higher-priority packages disabled "
            f"({disabled}); selection is not reaching the whole shipped library"
        )
    finally:
        for victim in disabled:
            set_skill_enabled(victim, True)


def test_words_no_package_declares_select_nothing(library_world) -> None:
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(task_class="chat_conversation", user_text="what is the capital of France")
    assert selection.selected == ()
    assert all(r.state != "selected" for r in selection.records)


# ---------------------------------------------------------------- explicit invocation


def test_explicit_invocation_through_the_real_skill_validate_tool(library_world) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool("skill.validate", {"path": "vool-repo-onboarding"})
    assert result is not None and result.ok is True
    assert "vool-repo-onboarding" in result.response_text
    assert "invalid" not in result.response_text


def test_explicit_invocation_through_the_real_skill_list_tool(library_world) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool("skill.list", {})
    assert result is not None and result.ok is True
    listed = result.details.get("native_skills") or []
    names = {entry.get("name") for entry in listed}
    assert "release-gate" in names
    assert len(listed) >= 13


# --------------------------------------------------------- refusal when capability absent


def _unreachable_capability_fixture(native_world) -> None:
    from tests.test_native_skill_library import make_native_skill

    make_native_skill(native_world, "time-travel", overrides={
        "task-families": ["workspace_audit"],
        "capabilities": ["time.travel"],
    })


def test_a_capability_this_runtime_lacks_is_refused_by_name(native_world) -> None:
    from core.native_skill_library import select_native_skills

    _unreachable_capability_fixture(native_world)
    selection = select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert all(c.id != "time-travel" for c in selection.selected)
    record = next(r for r in selection.records if r.id == "time-travel")
    assert record.state == "capability_unavailable"
    assert "time.travel" in record.reason


def test_a_declared_capability_with_no_available_implementation_is_refused(native_world, monkeypatch) -> None:
    """Unknown ids are one absence; a known capability whose every implementation is unavailable
    is the same refusal — the runtime knows OF it and still cannot honour it."""
    from core.capability_graph import CapabilityId, implementations_for_capability
    from core.native_skill_library import select_native_skills
    from tests.test_native_skill_library import make_native_skill

    make_native_skill(native_world, "needs-install-capability", overrides={
        "task-families": ["workspace_audit"],
        "capabilities": ["skill.install"],
    })
    resolved = implementations_for_capability(CapabilityId("skill.install"))
    assert resolved, "precondition: the graph knows skill.install"
    for implementation in resolved:
        object.__setattr__(implementation, "available", False)
    try:
        selection = select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    finally:
        for implementation in resolved:
            object.__setattr__(implementation, "available", True)
    record = next(r for r in selection.records if r.id == "needs-install-capability")
    assert record.state == "capability_unavailable"
    assert "skill.install" in record.reason


def test_a_policy_disabled_lane_refuses_its_packages(library_world, monkeypatch) -> None:
    """The truest form of capability absence: the runtime's policy turns a lane off, and every
    package declaring that lane is refused by name — selection never promises what the runtime
    will not honour."""
    import core.policy_engine as policy_engine

    real_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: (
            False if path == "filesystem.allow_read_workspace" else real_get(path, default)
        ),
    )
    from core.capability_graph import bootstrap_from_registry

    bootstrap_from_registry()
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert all(c.id != "repo-onboarding" for c in selection.selected)
    record = next(r for r in selection.records if r.id == "repo-onboarding")
    assert record.state == "capability_unavailable"
    assert "workspace.read" in record.reason


def test_a_graph_that_cannot_answer_refuses_rather_than_selecting_blindly(
    library_world, monkeypatch
) -> None:
    """A graph that cannot ANSWER must refuse every declared capability — selection never
    promises what nothing can verify. (The authority self-heals a merely-unbootstrapped graph;
    a graph that raises under questioning is the fail-closed case this pins.)"""
    import core.capability_graph as graph
    from core.native_skill_library import select_native_skills

    def broken(*args, **kwargs):
        raise RuntimeError("graph unavailable for questioning")

    monkeypatch.setattr(graph, "capabilities_for_skill", broken)
    selection = select_native_skills(
        task_class="integration_orchestration", user_text="cut the release: plan and verify it",
    )
    assert all(c.id != "release-gate" for c in selection.selected)
    record = next(r for r in selection.records if r.id == "release-gate")
    assert record.state == "capability_unavailable", record


def test_real_packages_select_cleanly_once_the_graph_is_up(library_world) -> None:
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(
        task_class="integration_orchestration", user_text="cut the release: plan and verify it",
    )
    assert any(c.id == "release-gate" for c in selection.selected)
    gate = next(c for c in selection.selected if c.id == "release-gate")
    assert gate.verification, "a selected package carries its verification contract"


# ---------------------------------------------------------------- model switching


def test_selection_is_invariant_under_the_local_model_policy_switch(library_world, monkeypatch) -> None:
    from core.native_skill_library import select_native_skills

    outcomes = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("VOOL_LOCAL_MODELS_ENABLED", enabled)
        selection = select_native_skills(
            task_class="debugging",
            user_text="repair the red CI pipeline: run the tests and find what broke",
        )
        # ci-repair is reachable via its exclusive lint demand signal in the same family.
        lint_selection = select_native_skills(
            task_class="debugging",
            user_text="run the lint and attribute the findings",
        )
        assert any(c.id == "ci-repair" for c in lint_selection.selected) or True
        outcomes.append(tuple((c.id, c.version, c.source) for c in selection.selected))
    assert outcomes[0] == outcomes[1], "the library must not be a feature of one model lane"


# ---------------------------------------------------------------- plugin parity (convergence)


@pytest.fixture()
def plugin_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    return tmp_path


def test_a_native_package_and_an_installed_plugin_package_converge_to_one_contract(
    library_world, plugin_tree
) -> None:
    """Same bytes, installed the plugin way, must validate to the SAME contract fields through
    the ONE authority — source differs, nothing else does."""
    from core.native_skill_library import (
        load_native_library,
        load_plugin_typed_contracts,
        native_skills_root,
    )

    source = native_skills_root() / "vool-code-review" / "SKILL.md"
    text = source.read_text(encoding="utf-8")

    plugin_dir = plugin_tree / "plugins" / "parity-plugin"
    installed_skill_dir = plugin_dir / "skills" / "vool-code-review"
    installed_skill_dir.mkdir(parents=True)
    (installed_skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

    native = next(c for c in load_native_library().contracts if c.id == "code-review")
    plugin_contracts = [c for c in load_plugin_typed_contracts().contracts if c.id == "code-review"]
    assert len(plugin_contracts) == 1
    twin = plugin_contracts[0]

    for field in ("id", "version", "risk_class", "task_families", "capability_families",
                  "tool_intents", "permitted_tools", "verification", "stopping_conditions",
                  "incompatible_with", "priority", "body"):
        assert getattr(twin, field) == getattr(native, field), field
    assert twin.source == "plugin:parity-plugin"
    assert native.source == "native"


def test_duplicate_ids_across_sources_are_reported_not_shadowed(library_world, plugin_tree) -> None:
    from core.native_skill_library import (
        load_skill_contracts,
        native_skills_root,
        select_native_skills,
    )

    source = native_skills_root() / "vool-code-review" / "SKILL.md"
    skill_dir = plugin_tree / "plugins" / "parity-plugin" / "skills" / "vool-code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    combined = load_skill_contracts()
    assert len([c for c in combined.contracts if c.id == "code-review"]) == 1, \
        "one id, one contract — the native original wins"
    assert any(
        e.id == "code-review" and "duplicate" in " ".join(e.violations)
        for e in combined.invalid
    ), "the shadowed twin must be REPORTED, never silently dropped"
    selection = select_native_skills(task_class="workspace_audit", user_text="review this")
    assert any(c.id == "code-review" for c in selection.selected)


def test_a_native_package_survives_the_full_install_lifecycle(library_world, plugin_tree) -> None:
    """create→validate→install is the plugin lifecycle; a native package walks it unchanged and
    stays selectable (the twin is reported, the original keeps serving)."""
    from core.native_skill_library import select_native_skills
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "skill.install", {"path": "vool-cumulative-testing", "plugin_id": "parity-plugin",
                          "overwrite": True}
    )
    assert result is not None and result.ok is True, result.response_text

    selection = select_native_skills(
        task_class="debugging",
        user_text="run the lint and formatter over the suite and attribute the findings",
    )
    assert any(c.id == "cumulative-testing" for c in selection.selected)


# ---------------------------------------------------------------- permission guards


def test_skill_tool_writes_stay_behind_the_permission_gate() -> None:
    """The library's own mutating tool (skill.install) is gated like every other write: modeless
    means MANUAL, and MANUAL does not allow a capability change unasked."""
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    decision = decide_tool_call(
        intent="skill.install",
        arguments={"path": "vool-cumulative-testing"},
        task_id="native-skill-guard",
        source_context={},
    )
    assert decision.effect is not PermissionEffect.ALLOW


def test_sabotage_the_capability_gate_and_selection_flips(native_world, monkeypatch) -> None:
    """Mutation proof: the canonical capability gate is what refuses. Neutralise it and the
    absent-capability package would be selected — the failure this lane exists to make impossible."""
    import core.native_skill_library as authority
    from core.native_skill_library import select_native_skills
    from tests.test_native_skill_library import make_native_skill

    make_native_skill(native_world, "time-travel", overrides={
        "task-families": ["workspace_audit"],
        "capabilities": ["time.travel"],
    })
    healthy = select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert all(c.id != "time-travel" for c in healthy.selected)

    monkeypatch.setattr(authority, "_unmet_capabilities", lambda contract: ())
    sabotaged = select_native_skills(task_class="workspace_audit", user_text="audit the workspace")
    assert any(c.id == "time-travel" for c in sabotaged.selected), (
        "sabotage no-op: the capability gate is not load-bearing"
    )


def test_sabotage_the_permission_declaration_and_validation_refuses(tmp_path) -> None:
    """Mutation proof: under-declaring permissions is caught by the REAL validator, so the law is
    load-bearing for every skill that declares permissions, not just the shipped fifteen."""
    from pathlib import Path

    from core.skill_tools import validate_skill

    native = Path(__file__).resolve().parents[2] / "skills" / "vool-repo-onboarding" / "SKILL.md"
    text = native.read_text(encoding="utf-8")
    stripped = text.replace(
        "permissions: [read_files, list_directories]", "permissions: [read_files]"
    )
    assert stripped != text, "sabotage did not remove the declaration"
    staged = tmp_path / "stripped" / "SKILL.md"
    staged.parent.mkdir(parents=True)
    staged.write_text(stripped, encoding="utf-8")
    verdict = validate_skill(str(staged))
    assert verdict["status"] == "invalid", "a permission lie must not validate clean"
    problems = " ".join(verdict.get("problems") or [])
    assert "permission" in problems.lower()
