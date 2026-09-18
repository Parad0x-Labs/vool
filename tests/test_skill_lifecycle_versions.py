"""The versioned skill lifecycle: edit → version → rollback, behind the ONE activation gate.

The 2026-07-30 creation lane ends at `install`: reinstalling an edited skill OVERWROTE the
active file with no trace of the previous version, so an edit that made a skill worse was
unrollbackable except by hand, and nothing recorded which version of a skill a turn was
influenced by. This pack pins the missing half of the lifecycle on the REAL functions:

- every activation records an immutable version (content sha + bytes) next to the skill;
- reinstalling an edited draft bumps the version and preserves the prior bytes exactly;
- rolling back restores a prior version byte-for-byte and records the rollback;
- refusals are typed (unknown version, no history) and mutate nothing;
- the version store lives on disk, so it survives a restart like the disable store does;
- concurrent activations of the same skill serialize into a consistent history;
- activation stays behind the explicit-approval gate: a chat-mode `skill.install` produces a
  pending approval, and ONLY the resolved token re-dispatch executes it;
- the turn provenance names the installed skill's version, so "which version influenced this
  turn" is answerable for user-authored skills too, not only typed contracts.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from core.skill_tools import (
    install_skill,
    validate_skill,
)

_SKILL_V1 = """---
name: version-proof-skill
description: Proves the versioned skill lifecycle end to end.
triggers: version proof lifecycle workspace
---

# Version proof

State that version ONE of the skill influenced this answer.
"""

_SKILL_V2 = """---
name: version-proof-skill
description: Proves the versioned skill lifecycle end to end.
triggers: version proof lifecycle workspace
---

# Version proof, edited

State that version TWO of the skill influenced this answer.
"""


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch) -> Path:
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    return tmp_path


def _installed_skill_dir(plugin_root: Path) -> Path:
    return plugin_root / "plugins" / "local-skills" / "skills" / "version-proof-skill"


def _history(plugin_root: Path) -> dict:
    return json.loads(
        (_installed_skill_dir(plugin_root) / "versions" / "history.json").read_text(encoding="utf-8")
    )


def _stage(tmp_path: Path, text: str, name: str = "draft") -> Path:
    staged = tmp_path / f"{name}.md"
    staged.write_text(text, encoding="utf-8")
    return staged


# ---------------------------------------------------------------------------
# Versions are recorded, immutable, and restorable
# ---------------------------------------------------------------------------


def test_first_install_records_version_one(plugin_root: Path, tmp_path) -> None:
    result = install_skill(str(_stage(tmp_path, _SKILL_V1)))
    assert result["status"] == "ok", result
    assert result.get("version") == 1, result

    history = _history(plugin_root)
    assert history["head_version"] == 1
    assert [entry["version"] for entry in history["versions"]] == [1]
    snapshot = _installed_skill_dir(plugin_root) / "versions" / "v1.md"
    assert snapshot.is_file(), "version one's bytes must be preserved, not just counted"
    assert snapshot.read_text(encoding="utf-8") == _SKILL_V1


def test_reinstalling_an_edit_bumps_the_version_and_preserves_the_prior_bytes(
    plugin_root: Path, tmp_path
) -> None:
    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    result = install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)
    assert result["status"] == "ok", result
    assert result.get("version") == 2, result

    active = (_installed_skill_dir(plugin_root) / "SKILL.md").read_text(encoding="utf-8")
    assert "version TWO" in active, "the activation must carry the edited body"
    v1 = (_installed_skill_dir(plugin_root) / "versions" / "v1.md").read_text(encoding="utf-8")
    assert v1 == _SKILL_V1, "the prior version's bytes must survive the edit verbatim"

    history = _history(plugin_root)
    assert history["head_version"] == 2
    assert [entry["version"] for entry in history["versions"]] == [1, 2]


def test_rollback_restores_a_prior_version_byte_for_byte(plugin_root: Path, tmp_path) -> None:
    from core.skill_tools import rollback_skill

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    assert install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)["status"] == "ok"

    result = rollback_skill("version-proof-skill", version=1)
    assert result["status"] == "ok", result

    active = (_installed_skill_dir(plugin_root) / "SKILL.md").read_text(encoding="utf-8")
    assert active == _SKILL_V1, "rollback must restore the prior bytes exactly"

    history = _history(plugin_root)
    assert history["head_version"] == 3, "the rollback is itself a recorded head move"
    last = history["versions"][-1]
    assert last.get("restored_from") == 1
    v1_snapshot = (_installed_skill_dir(plugin_root) / "versions" / "v1.md").read_text(
        encoding="utf-8"
    )
    assert v1_snapshot == _SKILL_V1, "rollback must not mutate the snapshot it restored from"


def test_rollback_refuses_an_unknown_version_without_mutating_anything(
    plugin_root: Path, tmp_path
) -> None:
    from core.skill_tools import rollback_skill

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    before = (_installed_skill_dir(plugin_root) / "SKILL.md").read_text(encoding="utf-8")

    result = rollback_skill("version-proof-skill", version=99)
    assert result["status"] == "unknown_version", result
    assert (_installed_skill_dir(plugin_root) / "SKILL.md").read_text(encoding="utf-8") == before
    assert _history(plugin_root)["head_version"] == 1, "a refused rollback records nothing"


def test_rollback_refuses_a_skill_that_was_never_versioned(plugin_root: Path, tmp_path) -> None:
    from core.skill_tools import rollback_skill

    result = rollback_skill("never-installed-skill", version=1)
    assert result["status"] == "not_found", result


def test_skill_history_names_the_versions_a_person_can_refer_to(plugin_root: Path, tmp_path) -> None:
    from core.skill_tools import skill_history

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    assert install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)["status"] == "ok"

    history = skill_history("version-proof-skill")
    assert history["status"] == "ok", history
    assert history["head_version"] == 2
    assert len(history["versions"]) == 2
    assert all(entry.get("sha256") for entry in history["versions"]), history


# ---------------------------------------------------------------------------
# The version store is durable and concurrency-safe
# ---------------------------------------------------------------------------


def test_versions_and_disable_survive_a_stateless_reload(plugin_root: Path, tmp_path) -> None:
    """The store has no process-local copy, so a fresh read (what a restarted daemon does)
    must answer identically — the same law the disable store is pinned by."""
    from core.native_skill_library import _read_disabled_ids, set_skill_enabled

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    assert set_skill_enabled("version-proof-skill", False)["status"] == "ok"
    assert install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)["status"] == "ok"

    assert _history(plugin_root)["head_version"] == 2
    assert "version-proof-skill" in _read_disabled_ids()

    # The disable store is the SESSION's VOOL_HOME store, not per-test state: leaving this
    # skill disabled would poison every later test in the file. Re-enable and confirm.
    assert set_skill_enabled("version-proof-skill", True)["status"] == "ok"
    assert "version-proof-skill" not in _read_disabled_ids()


def test_concurrent_activations_of_one_skill_serialize_into_a_consistent_history(
    plugin_root: Path, tmp_path
) -> None:
    drafts = [_stage(tmp_path, _SKILL_V1.replace("version proof lifecycle", f"variant {i} corpus"), name=f"d{i}")
              for i in range(6)]
    results: list[dict] = []
    lock = threading.Lock()

    def install_one(index: int) -> None:
        outcome = install_skill(str(drafts[index]), overwrite=(index > 0))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=install_one, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(r["status"] == "ok" for r in results), results
    history = _history(plugin_root)
    assert history["head_version"] == 6, history
    versions = sorted(entry["version"] for entry in history["versions"])
    assert versions == [1, 2, 3, 4, 5, 6], "concurrent activations must not fork the history"


# ---------------------------------------------------------------------------
# Activation stays behind the explicit approval gate — the joined flow
# ---------------------------------------------------------------------------


def _approval_context() -> dict:
    return {"session_id": "lifecycle-approval-session", "cancel_turn_id": "turn-activation-1"}


def test_install_activation_is_pending_then_executes_only_on_the_resolved_token(
    plugin_root: Path, tmp_path
) -> None:
    """The joined flow no test had driven: skill.install consults THE authority in a chat mode,
    lands as pending_approval, the operator resolves it, and the SAME exact call — replayed with
    the resolved token — is the one that executes."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool
    from core.mode_permission_policy import reset_mode_permission_state, resolve_approval

    reset_mode_permission_state()
    staged = _stage(tmp_path, _SKILL_V1)
    context = _approval_context()

    first = execute_authorized_runtime_tool(
        "skill.install", {"path": str(staged)}, source_context=dict(context)
    )
    assert first.status == "pending_approval", first.response_text
    request = dict(first.details.get("approval_request") or {})
    assert request.get("approval_id"), "the refusal must carry the approval request"

    assert (_installed_skill_dir(plugin_root) / "SKILL.md").exists() is False, (
        "a pending activation must not have written the active tree"
    )

    resolved = resolve_approval(str(request["approval_id"]), decision="allow")
    assert resolved is not None, "the operator's allow must resolve the pending approval"

    replay_context = dict(context)
    replay_context["mode_approval_token"] = str(request["approval_id"])
    second = execute_authorized_runtime_tool(
        "skill.install", {"path": str(staged)}, source_context=replay_context
    )
    assert second.status == "ok", second.response_text
    assert (_installed_skill_dir(plugin_root) / "SKILL.md").is_file()
    assert second.details.get("version") == 1


def test_the_resolved_token_cannot_activate_a_different_install(plugin_root: Path, tmp_path) -> None:
    """The approval binds the exact call: the token must not cover a different skill."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool
    from core.mode_permission_policy import reset_mode_permission_state, resolve_approval

    reset_mode_permission_state()
    approved = _stage(tmp_path, _SKILL_V1, name="approved")
    other_text = _SKILL_V1.replace("version-proof-skill", "other-proof-skill").replace(
        "version proof lifecycle", "other corpus triggers"
    )
    other = _stage(tmp_path, other_text, name="other")

    context = _approval_context()
    first = execute_authorized_runtime_tool(
        "skill.install", {"path": str(approved)}, source_context=dict(context)
    )
    request = dict(first.details.get("approval_request") or {})
    resolve_approval(str(request["approval_id"]), decision="allow")

    replay_context = dict(context)
    replay_context["mode_approval_token"] = str(request["approval_id"])
    smuggled = execute_authorized_runtime_tool(
        "skill.install", {"path": str(other)}, source_context=replay_context
    )
    assert smuggled.status == "pending_approval", (
        "the resolved token must not activate a skill the operator never saw"
    )


# ---------------------------------------------------------------------------
# Preview, provenance version, and the conversational surface
# ---------------------------------------------------------------------------


def test_validate_previews_the_exact_markdown_that_would_be_activated(
    plugin_root: Path, tmp_path
) -> None:
    staged = _stage(tmp_path, _SKILL_V1)
    result = validate_skill(str(staged))
    assert result["status"] == "ok", result
    assert result.get("preview_markdown") == _SKILL_V1, (
        "the preview must be the file's exact bytes — what activation would install"
    )


def test_turn_provenance_names_the_installed_skill_version(plugin_root: Path, tmp_path) -> None:
    """'Which skill VERSION influenced this turn' must be answerable for a user-authored skill
    too — the guidance provenance row carries the installed head version."""
    from core.tool_offer_assembly import skill_guidance_for

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    assert install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)["status"] == "ok"

    guidance = skill_guidance_for(
        "run the version proof lifecycle for my workspace", task_class=""
    )
    rows = [row for row in guidance.skills if row.get("name") == "version-proof-skill"]
    assert rows, "the installed skill must rank for its own trigger words"
    assert rows[0].get("version") == 2, rows[0]


def test_rollback_is_a_contracted_gated_tool_with_a_legal_path_to_selection() -> None:
    """skill.rollback exists at the ONE contract authority, is a runtime capability change
    demanding explicit opt-in, and its demand signal fires on rollback phrasings only."""
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.tool_demand_signals import resolve_demand_signals

    contract = runtime_tool_contract_map()["skill.rollback"]
    assert contract.side_effect_class == "runtime_capability_change"
    assert contract.approval_requirement == "explicit_user_opt_in"
    assert "change_settings" in contract.permission_actions

    signals = resolve_demand_signals("roll back the version-proof-skill to version 1")
    assert "skill.rollback" in signals.explicit_intents, signals
    signals = resolve_demand_signals("restore the qa echo skill to its previous version")
    assert "skill.rollback" in signals.explicit_intents, signals
    # Negative controls: reverting a FILE change is the workspace lane, not the skill lane.
    signals = resolve_demand_signals("revert my last change to the file")
    assert "skill.rollback" not in signals.explicit_intents, signals
    signals = resolve_demand_signals("roll back the last edit in calc.py")
    assert "skill.rollback" not in signals.explicit_intents, signals


def test_dispatch_executes_the_rollback_tool(plugin_root: Path, tmp_path) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    assert install_skill(str(_stage(tmp_path, _SKILL_V1)))["status"] == "ok"
    assert install_skill(str(_stage(tmp_path, _SKILL_V2)), overwrite=True)["status"] == "ok"

    result = execute_runtime_tool(
        "skill.rollback", {"skill": "version-proof-skill", "version": 1}
    )
    assert result.status == "ok", result.response_text
    active = (_installed_skill_dir(plugin_root) / "SKILL.md").read_text(encoding="utf-8")
    assert active == _SKILL_V1

    missing = execute_runtime_tool("skill.rollback", {"skill": "version-proof-skill", "version": 42})
    assert missing.status == "unknown_version", missing.response_text
