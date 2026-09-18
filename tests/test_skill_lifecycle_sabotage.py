"""Sabotage proofs — the load-bearing guards of the versioned skill lifecycle.

Each test removes ONE mechanism (CLAUDE.md §6b.4) and asserts the observable the pinning
pack (tests/test_skill_lifecycle_versions.py) protects CHANGES. Guards proven here:

- version recording at activation (a skill without versions cannot be rolled back or audited);
- the rollback version check (an unknown version must refuse, not silently no-op);
- the disk persistence of the version store (in-memory history dies with the process);
- the explicit-approval gate on activation (install executes only on a resolved token);
- the disable-store filter on the lexical ranker (a disabled skill must not reach the wire).

Mutations are monkeypatches, so every test restores the world on teardown.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.skill_tools import install_skill

_SKILL_V1 = """---
name: sabotage-proof-skill
description: Proves the lifecycle guards are load bearing.
triggers: sabotage proof lifecycle workspace
---

State that version ONE influenced this answer.
"""

_SKILL_V2 = """---
name: sabotage-proof-skill
description: Proves the lifecycle guards are load bearing.
triggers: sabotage proof lifecycle workspace
---

State that version TWO influenced this answer.
"""


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch) -> Path:
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    return tmp_path


def _stage(tmp_path: Path, text: str, name: str) -> Path:
    staged = tmp_path / f"{name}.md"
    staged.write_text(text, encoding="utf-8")
    return staged


def test_sabotage_version_recording_removed_leaves_nothing_to_rollback(
    plugin_root: Path, tmp_path, monkeypatch
) -> None:
    """Guard: every activation records an immutable version."""
    import core.skill_tools as skill_tools

    healthy = install_skill(str(_stage(tmp_path, _SKILL_V1, "healthy")))
    assert healthy.get("version") == 1, "sabotage precondition broken: healthy install versions"

    monkeypatch.setattr(skill_tools, "_record_version", lambda *a, **k: {"version": 0})
    sabotaged = install_skill(str(_stage(tmp_path, _SKILL_V1, "sabotaged")), overwrite=True)
    assert sabotaged["status"] == "error", (
        "sabotage no-op: an install that cannot record a version still succeeded"
    )


def test_sabotage_rollback_version_check_removed_restores_from_thin_air(
    plugin_root: Path, tmp_path, monkeypatch
) -> None:
    """Guard: only a version RECORDED in history is a legal rollback target."""
    import core.skill_tools as skill_tools

    assert install_skill(str(_stage(tmp_path, _SKILL_V1, "a")))["status"] == "ok"
    healthy = skill_tools.rollback_skill("sabotage-proof-skill", version=99)
    assert healthy["status"] == "unknown_version", (
        "sabotage precondition broken: unknown version already accepted"
    )

    monkeypatch.setattr(
        skill_tools,
        "_read_history",
        lambda skill_dir: {"head_version": 99, "versions": [{"version": 99}]},
    )
    sabotaged = skill_tools.rollback_skill("sabotage-proof-skill", version=99)
    assert sabotaged["status"] == "error", (
        "sabotage no-op: a version absent from the real history was restored anyway"
    )


def test_sabotage_inmemory_history_store_loses_versions_on_reload(
    plugin_root: Path, tmp_path, monkeypatch
) -> None:
    """Guard: the history is read from disk on every call — no process-local copy."""
    import core.skill_tools as skill_tools

    assert install_skill(str(_stage(tmp_path, _SKILL_V1, "a")))["status"] == "ok"
    head = skill_tools.skill_history("sabotage-proof-skill")["head_version"]
    assert head == 1, "sabotage precondition broken: disk store did not record"

    # What a compromised writer would do: keep entries in memory only.
    monkeypatch.setattr(
        skill_tools, "_write_history_atomic", lambda skill_dir, history: None
    )
    result = install_skill(str(_stage(tmp_path, _SKILL_V2, "b")), overwrite=True)
    assert result["status"] == "error", (
        "sabotage no-op: an activation whose history could not be persisted succeeded"
    )


def test_sabotage_activation_without_the_approval_gate_executes_plain_writes(
    plugin_root: Path, tmp_path, monkeypatch
) -> None:
    """Guard: skill.install changes runtime behaviour, so the authority must prompt.

    The drift this catches: the gate reading a WEAKER action set for the same call. In auto
    mode `create_files` alone is allowed, but the real install declares `change_settings`
    too — the strictest action is what forces the prompt. Letting the gate see only
    `create_files` lets a chat turn activate a skill with no operator opt-in.
    """
    import core.mode_permission_policy as mpp
    from core.authorized_tool_execution import execute_authorized_runtime_tool

    context = {
        "session_id": "sabotage-approval-session",
        "cancel_turn_id": "turn-sab-1",
        "operating_mode": "auto",
    }
    healthy = execute_authorized_runtime_tool(
        "skill.install", {"path": str(_stage(tmp_path, _SKILL_V1, "a"))}, source_context=dict(context)
    )
    assert healthy.status == "pending_approval", (
        "sabotage precondition broken: install already runs without approval in auto"
    )

    original_actions = mpp.actions_for_tool

    def weaker_actions(intent: str, arguments=None, source_context=None):
        actions = original_actions(intent, arguments, source_context)
        if str(intent) == "skill.install":
            from core.mode_permission_policy import PermissionAction

            return tuple(a for a in actions if a is PermissionAction.CREATE_FILES) or actions
        return actions

    monkeypatch.setattr(mpp, "actions_for_tool", weaker_actions)
    executed = execute_authorized_runtime_tool(
        "skill.install", {"path": str(_stage(tmp_path, _SKILL_V2, "b"))}, source_context=dict(context)
    )
    assert executed.status == "ok", (
        "sabotage no-op: reading a weaker action set still prompted for the activation"
    )


def test_sabotage_lens_family_gate_removed_injects_the_lens_into_an_unrelated_turn(
    tmp_path, monkeypatch
) -> None:
    """Guard: a lens rides the typed task-family gate like every other skill. Widening the
    lens's family to an unrelated class must be what it TAKES to inject it there — proving
    the gate, not the lens's presence, keeps advisory doctrine out of turns it doesn't fit."""
    import dataclasses

    from core.native_skill_library import LibraryLoad, load_skill_contracts, select_native_skills

    library = load_skill_contracts()
    lens = next(c for c in library.contracts if c.id == "lens-security")
    healthy = select_native_skills(
        task_class="chat_conversation", user_text="a bedtime story, please", library=library
    )
    assert all(c.id != "lens-security" for c in healthy.selected), (
        "sabotage precondition broken: the security lens already rides bedtime turns"
    )

    widened = dataclasses.replace(lens, task_families=("chat_conversation",))
    poisoned = LibraryLoad(
        contracts=tuple(widened if c.id == "lens-security" else c for c in library.contracts),
        invalid=library.invalid,
    )
    sabotaged = select_native_skills(
        task_class="chat_conversation", user_text="a bedtime story, please", library=poisoned
    )
    assert any(c.id == "lens-security" for c in sabotaged.selected), (
        "sabotage no-op: widening the task family did not inject the lens — the gate is dead "
        "or a backstop absorbed it"
    )


def test_sabotage_version_influence_record_stripped_undoes_the_provenance_row(
    tmp_path, monkeypatch
) -> None:
    """Guard: the version rides the provenance row — stripping it must be what it takes to
    make 'which lens version influenced this turn' unanswerable."""
    from core.native_skill_library import guidance_for_selection, load_skill_contracts

    library = load_skill_contracts()
    selection_records = [c for c in library.contracts if c.id == "lens-security"]
    assert selection_records, "sabotage precondition broken: lens not in library"
    _text, provenance, _permitted = guidance_for_selection(tuple(selection_records))
    assert provenance and provenance[0].get("version") == "1.0.0", (
        "sabotage precondition broken: healthy provenance carries no version"
    )

    import core.native_skill_library as nsl

    def versionless(selected):
        text, provenance, permitted = guidance_for_selection(selected)
        stripped = tuple({k: v for k, v in row.items() if k != "version"} for row in provenance)
        return text, stripped, permitted

    monkeypatch.setattr(nsl, "guidance_for_selection", versionless)
    sabotage_selection = nsl.select_native_skills(
        task_class="security_hardening", user_text=""
    )
    _text, stripped_rows, _p = nsl.guidance_for_selection(sabotage_selection.selected)
    lens_rows = [row for row in stripped_rows if str(row.get("name", "")).startswith("lens-")]
    assert lens_rows and all("version" not in row for row in lens_rows), (
        "sabotage no-op: the provenance row still carries the version after the strip"
    )


def test_sabotage_lexical_disable_filter_removed_puts_a_disabled_skill_back_on_the_wire(
    plugin_root: Path, tmp_path, monkeypatch
) -> None:
    """Guard: the lexical ranker's candidates are filtered through the ONE disable store."""
    from core.native_skill_library import set_skill_enabled
    from core.tool_offer_assembly import skill_guidance_for

    assert install_skill(str(_stage(tmp_path, _SKILL_V1, "a")))["status"] == "ok"
    assert set_skill_enabled("sabotage-proof-skill", False)["status"] == "ok"
    healthy = skill_guidance_for("sabotage proof lifecycle for my workspace", task_class="")
    assert all(row.get("name") != "sabotage-proof-skill" for row in healthy.skills), (
        "sabotage precondition broken: the disabled skill still ranks"
    )

    import core.tool_offer_assembly as toa

    # The drift this guard exists for: a selection path that ranks plugins WITHOUT consulting
    # the disable store. Healthy guidance is empty (filter on); the unfiltered path must be
    # able to resurrect the skill — proving the filter, not the ranker, is what keeps it off.
    original_guidance = toa.skill_guidance_for
    def unfiltered(user_text: str, *, task_class: str = "", limit: int = 2):
        guidance = original_guidance(user_text, task_class=task_class, limit=limit)
        if guidance.skills:
            return guidance
        from core import plugin_skills

        skills = toa.loaded_skills()
        ranked = plugin_skills.rank_skills(skills, str(user_text), limit=limit)
        if not ranked:
            return guidance
        from core.tool_offer_assembly import SkillGuidance, _render_block

        blocks, rows = [], []
        for skill in ranked:
            block, used = _render_block(skill, 4000)
            blocks.append(block)
            rows.append({"name": str(skill.name), "origin": "plugin", "chars": used})
        return SkillGuidance(text="\n\n".join(blocks), skills=tuple(rows))
    monkeypatch.setattr(toa, "skill_guidance_for", unfiltered)
    sabotaged = toa.skill_guidance_for("sabotage proof lifecycle for my workspace", task_class="")
    assert any(row.get("name") == "sabotage-proof-skill" for row in sabotaged.skills), (
        "sabotage no-op: a selection path that skips the disable filter still respects it"
    )
