"""Independent repairs are validated one unit at a time (coding revision 3, P1-3).

The independent review proved that two unrelated, individually approved repairs behaved as one
atomic batch: after the first landed, the task stayed at `mutate` and refused the validation the
owner asked for between repairs. A batch of approvals does not make changes indivisible.

Here every proposal is its own repair unit unless it declares a shared ``unit``; after a unit's
changes land the task stops at a checkpoint where the focused and full checks validate THAT
unit's bytes (recorded with the unit and revision they verified), while the remaining approved
repairs keep their approvals; the next unit may start only once the checkpoint has been
validated. A genuinely indivisible change -- a rename and its only caller, which break the
import when applied alone (proven below by applying each half in a scratch copy) -- is declared
as one unit, lands whole, and is validated after all of its changes.

Novel data (a small shipping project) beside the review's arithmetic/label cases. Real files and
real commands through the production door; no model is involved and none is claimed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEGACY = REPO / "tests" / "fixtures" / "code_task_journals"

PRICING = "def total(subtotal, tax):\n    return subtotal - tax\n"
PRICING_FIXED = "def total(subtotal, tax):\n    return subtotal + tax\n"
LABELS = "def slug(words):\n    return '_'.join(words)\n"
LABELS_FIXED = "def slug(words):\n    return '-'.join(words)\n"
CHECK_PRICING = "from pricing import total\nassert total(40, 8) == 48, total(40, 8)\nprint('pricing ok')\n"
CHECK_LABELS = "from labels import slug\nassert slug(['next', 'day']) == 'next-day', slug(['next', 'day'])\nprint('labels ok')\n"
CHECK_ALL = CHECK_PRICING + CHECK_LABELS
UNITS = "def kg_to_lb(kg):\n    return kg * 2\n"
UNITS_FIXED = "def kilograms_to_pounds(kg):\n    return round(kg * 2.20462, 2)\n"
SHIPPING = "from units import kg_to_lb\n\ndef parcel_pounds(kg):\n    return kg_to_lb(kg)\n"
SHIPPING_FIXED = "from units import kilograms_to_pounds\n\ndef parcel_pounds(kg):\n    return kilograms_to_pounds(kg)\n"
CHECK_SHIPPING = (
    "from shipping import parcel_pounds\nfrom units import kilograms_to_pounds\n"
    "assert kilograms_to_pounds(10) == 22.05, kilograms_to_pounds(10)\n"
    "assert parcel_pounds(3) == 6.61, parcel_pounds(3)\nprint('shipping ok')\n"
)
MANIFEST = "shipping manifest: operator owned\n"
CHECK_MONEY = CHECK_PRICING + "assert total(0, 0) == 0, total(0, 0)\nprint('money ok')\n"
CHANGELOG = "# Changelog\n\n## Unreleased\n"
TEAMMATE_LINE = "- labels: separator reviewed by the docs team\n"
PRICING_ENTRY = "- pricing: totals add tax\n"


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def shipping(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "shipping"
    root.mkdir()
    files = {"pricing.py": PRICING, "labels.py": LABELS, "check_pricing.py": CHECK_PRICING,
             "check_labels.py": CHECK_LABELS, "check_all.py": CHECK_ALL, "units.py": UNITS,
             "shipping.py": SHIPPING, "check_shipping.py": CHECK_SHIPPING, "MANIFEST.txt": MANIFEST,
             "check_money.py": CHECK_MONEY, "CHANGELOG.md": CHANGELOG}
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    code_task_runtime().reset()
    reset_mode_permission_state()


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def _journal(task_id: str) -> dict:
    return json.loads((Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json").read_text(encoding="utf-8"))


class Task:
    def __init__(self, root: Path, session: str, *, repro: str, owner: str) -> None:
        self.root = root
        self.ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
                    "runtime_session_id": session, "operating_mode": "auto"}
        self.id = _door("code.task.open", {"objective": "Repair the shipping project and check after each repair"},
                        self.ctx).details["task_id"]
        self.n = 0
        red = self.check(repro)
        assert red.details["tool_result"]["success"] is False
        assert _door("code.task.identify", {"task_id": self.id, "path": owner, "reason": "the check fails"}, self.ctx).ok

    def step(self, intent: str, arguments: dict, step_id: str | None = None):
        self.n += 1
        return _door("code.task.step", {"task_id": self.id, "step_id": step_id or f"s{self.n}", "intent": intent,
                                        "arguments": arguments}, self.ctx)

    def check(self, command: str):
        result = self.step("workspace.run_tests", {"command": command})
        return result

    def read(self, path: str):
        assert self.step("workspace.read_file", {"path": path}).ok

    def propose(self, pid: str, path: str, content: str, *, unit: str = ""):
        arguments = {"task_id": self.id, "proposal_id": pid, "intent": "workspace.write_file",
                     "arguments": {"path": path, "content": content},
                     "rationale": f"Owner {path}: repair the behavior its check requires."}
        if unit:
            arguments["unit"] = unit
        return _door("code.task.propose", arguments, self.ctx)

    def approve(self, pid: str):
        return _door("code.task.approve", {"task_id": self.id, "proposal_id": pid}, self.ctx)

    def write(self, path: str, content: str):
        return self.step("workspace.write_file", {"path": path, "content": content})

    def report(self):
        return _door("code.task.report", {"task_id": self.id}, self.ctx).details


def _two_independent(root: Path, session: str) -> Task:
    task = Task(root, session, repro="python3 check_all.py", owner="pricing.py")
    task.read("pricing.py")
    task.read("labels.py")
    assert task.propose("pricing", "pricing.py", PRICING_FIXED).ok
    assert task.propose("labels", "labels.py", LABELS_FIXED).ok
    assert task.approve("pricing").ok and task.approve("labels").ok
    return task


def _verifications(task: Task) -> list[tuple[str, str, bool, int]]:
    return [(v["stage"], v["unit"], v["success"], v["revision"]) for v in _journal(task.id)["verifications"]]


# ---------------------------------------------------------------------------
# The failure class: validation between independently approved repairs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["pricing-first", "labels-first"])
def test_independent_approved_repairs_are_validated_between_units(shipping, order):
    task = _two_independent(shipping, f"between-{order}")
    first, second = (("pricing", "pricing.py", PRICING_FIXED, "python3 check_pricing.py"),
                     ("labels", "labels.py", LABELS_FIXED, "python3 check_labels.py"))
    if order == "labels-first":
        first, second = second, first
    assert task.write(first[1], first[2]).ok
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["checkpoint"]["unit"] == first[0]
    assert journal["proposals"][second[0]]["approved"] and not journal["proposals"][second[0]]["consumed_by"]
    # The next repair may not start before this unit is validated.
    early = task.write(second[1], second[2])
    assert early.ok is False and early.status == "checkpoint_required", early.status
    assert (shipping / second[1]).read_text(encoding="utf-8") in (PRICING, LABELS)
    focused = task.check(first[3])
    assert focused.details["tool_result"]["success"] is True and focused.details["stage"] == "cumulative"
    full = task.check("python3 check_all.py")
    assert full.details["tool_result"]["success"] is False  # the second defect is still present, and recorded
    assert full.details["verification"]["current"] is True
    landed = task.write(second[1], second[2])
    assert landed.ok, (landed.status, landed.response_text)
    assert _journal(task.id)["checkpoint"]["unit"] == second[0]
    assert task.check(second[3]).details["tool_result"]["success"] is True
    assert task.check("python3 check_all.py").details["tool_result"]["success"] is True
    assert task.step("workspace.git_diff", {}).ok
    report = task.report()
    assert report["verdict"] == "completed"
    assert _verifications(task) == [
        ("narrow_test", first[0], True, 1),
        ("cumulative", first[0], False, 1),
        ("narrow_test", second[0], True, 2),
        ("cumulative", second[0], True, 2),
    ]
    assert (shipping / "MANIFEST.txt").read_text(encoding="utf-8") == MANIFEST
    assert sorted(report["git_diff_paths"]) == ["labels.py", "pricing.py"]


def test_a_failed_focused_checkpoint_offers_recovery_and_the_pending_repair(shipping):
    task = _two_independent(shipping, "failed-checkpoint")
    assert task.write("pricing.py", PRICING_FIXED).ok
    failed = task.check("python3 check_all.py")  # used as the focused check: labels still fails
    assert failed.details["tool_result"]["success"] is False
    assert failed.details["verification_failed"] is True
    guidance = " ".join(failed.details["next"])
    assert "code.task.identify" in guidance and "labels" in guidance, guidance
    assert failed.details["pending_repairs"] == ["labels"]
    landed = task.write("labels.py", LABELS_FIXED)  # the checkpoint was validated, if red: continue lawfully
    assert landed.ok, landed.status
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["checkpoint"]["unit"] == "labels"
    assert task.check("python3 check_all.py").details["tool_result"]["success"] is True


@pytest.mark.parametrize("route", ["write-refused", "reread"])
def test_a_waiting_repair_made_stale_after_a_green_checkpoint_is_reproposed_and_validated(shipping, route):
    """A code fix and its changelog entry are two independent approved repairs. The fix lands and its
    checkpoint passes, so the task hands over to the waiting entry -- and a teammate edits the changelog
    first. The entry's approval no longer applies; the recovery its refusal names (re-read, propose again,
    approve) must stay lawful, and the rebased entry is validated at its own checkpoint."""
    from core.code_assistant.task_runtime import active_task_control_intents

    task = Task(shipping, f"stale-waiting-{route}", repro="python3 check_money.py", owner="pricing.py")
    task.read("pricing.py")
    task.read("CHANGELOG.md")
    entry = CHANGELOG + PRICING_ENTRY
    assert task.propose("pricing", "pricing.py", PRICING_FIXED).ok
    assert task.propose("changelog", "CHANGELOG.md", entry).ok
    assert task.approve("pricing").ok and task.approve("changelog").ok
    assert task.write("pricing.py", PRICING_FIXED).ok
    assert task.check("python3 check_pricing.py").details["tool_result"]["success"] is True
    assert task.check("python3 check_money.py").details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "mutate"  # a green checkpoint hands over to the waiting repair
    edited = CHANGELOG + TEAMMATE_LINE
    (shipping / "CHANGELOG.md").write_text(edited, encoding="utf-8")
    if route == "write-refused":
        refused = task.write("CHANGELOG.md", entry)
        assert refused.ok is False and refused.status == "stale_base", refused.status
    task.read("CHANGELOG.md")  # after a refusal the model re-reads; on the other route this read is what finds the edit
    journal = _journal(task.id)
    assert journal["proposals"]["changelog"]["invalidated"] and not journal["proposals"]["changelog"]["consumed_by"]
    assert journal["stage"] == "mutate", journal["stage"]  # the named recovery stays lawful
    assert "code.task.propose" in active_task_control_intents(task.ctx)
    assert (shipping / "CHANGELOG.md").read_text(encoding="utf-8") == edited
    rebased = edited + PRICING_ENTRY
    proposed = task.propose("changelog-rebased", "CHANGELOG.md", rebased)
    assert proposed.ok, (proposed.status, proposed.response_text)
    assert task.approve("changelog-rebased").ok
    assert task.write("CHANGELOG.md", rebased).ok
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["checkpoint"]["unit"] == "changelog-rebased"
    assert task.check("python3 check_pricing.py").details["tool_result"]["success"] is True
    assert task.check("python3 check_money.py").details["tool_result"]["success"] is True
    assert task.step("workspace.git_diff", {}).ok
    report = task.report()
    assert report["verdict"] == "completed", (report["verdict"], report.get("missing"))
    assert _verifications(task) == [
        ("narrow_test", "pricing", True, 1),
        ("cumulative", "pricing", True, 1),
        ("narrow_test", "changelog-rebased", True, 2),
        ("cumulative", "changelog-rebased", True, 2),
    ]
    assert (shipping / "CHANGELOG.md").read_text(encoding="utf-8") == rebased  # the teammate's line survives
    assert sorted(report["git_diff_paths"]) == ["CHANGELOG.md", "pricing.py"]


def test_a_declared_coupled_unit_lands_whole_and_is_validated_after_all_its_changes(shipping):
    task = Task(shipping, "coupled", repro="python3 check_shipping.py", owner="units.py")
    task.read("units.py")
    task.read("shipping.py")
    assert task.propose("rename", "units.py", UNITS_FIXED, unit="rename-conversion").ok
    assert task.propose("caller", "shipping.py", SHIPPING_FIXED, unit="rename-conversion").ok
    assert task.approve("rename").ok and task.approve("caller").ok
    assert task.write("units.py", UNITS_FIXED).ok
    journal = _journal(task.id)
    assert journal["stage"] == "mutate", journal["stage"]  # the unit is part-applied: no checkpoint yet
    mid = task.check("python3 check_shipping.py")
    assert mid.ok is False and mid.status == "stage_violation"  # no validation of half a unit
    assert task.write("shipping.py", SHIPPING_FIXED).ok
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["checkpoint"]["unit"] == "rename-conversion"
    assert journal["checkpoint"]["revision"] == 2
    assert task.check("python3 check_shipping.py").details["tool_result"]["success"] is True
    assert task.check("python3 check_shipping.py").details["tool_result"]["success"] is True
    assert _verifications(task) == [("narrow_test", "rename-conversion", True, 2),
                                    ("cumulative", "rename-conversion", True, 2)]


def test_the_coupled_fixture_really_breaks_when_either_half_lands_alone(shipping, tmp_path):
    """Evidence that the unit above is indivisible by construction, not by declaration."""
    outcomes = {}
    for label, units, caller in (("rename-only", UNITS_FIXED, SHIPPING), ("caller-only", UNITS, SHIPPING_FIXED),
                                 ("both", UNITS_FIXED, SHIPPING_FIXED)):
        copy = tmp_path / label
        shutil.copytree(shipping, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / "units.py").write_text(units, encoding="utf-8")
        (copy / "shipping.py").write_text(caller, encoding="utf-8")
        run = subprocess.run([sys.executable, "check_shipping.py"], cwd=copy, capture_output=True, text=True, timeout=60)
        outcomes[label] = (run.returncode, "ImportError" in run.stderr)
    assert outcomes == {"rename-only": (1, True), "caller-only": (1, True), "both": (0, False)}, outcomes
    # Contrast: each independent repair lands alone and its own focused check passes.
    for path, content, check in (("pricing.py", PRICING_FIXED, CHECK_PRICING), ("labels.py", LABELS_FIXED, CHECK_LABELS)):
        copy = tmp_path / f"alone-{path}"
        shutil.copytree(shipping, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / path).write_text(content, encoding="utf-8")
        run = subprocess.run([sys.executable, "-c", check], cwd=copy, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stderr


# ---------------------------------------------------------------------------
# Unit law: declared up front, fully approved, closed once it starts, one file per member
# ---------------------------------------------------------------------------


def _coupled_proposed(shipping: Path, session: str) -> Task:
    task = Task(shipping, session, repro="python3 check_shipping.py", owner="units.py")
    task.read("units.py")
    task.read("shipping.py")
    assert task.propose("rename", "units.py", UNITS_FIXED, unit="rename-conversion").ok
    assert task.propose("caller", "shipping.py", SHIPPING_FIXED, unit="rename-conversion").ok
    return task


def test_a_unit_cannot_start_until_every_member_is_approved(shipping):
    task = _coupled_proposed(shipping, "unit-approval")
    assert task.approve("rename").ok
    refused = task.write("units.py", UNITS_FIXED)
    assert refused.ok is False and refused.status == "unit_not_approved", refused.status
    assert (shipping / "units.py").read_text(encoding="utf-8") == UNITS
    assert task.approve("caller").ok
    assert task.write("units.py", UNITS_FIXED).ok


def test_a_unit_closes_at_its_first_change_and_blocks_other_repairs_until_whole(shipping):
    task = _coupled_proposed(shipping, "unit-closed")
    task.read("pricing.py")
    assert task.propose("pricing", "pricing.py", PRICING_FIXED).ok
    assert task.approve("rename").ok and task.approve("caller").ok and task.approve("pricing").ok
    assert task.write("units.py", UNITS_FIXED).ok
    late_member = task.propose("late-member", "check_shipping.py", CHECK_SHIPPING + "# more\n", unit="rename-conversion")
    assert late_member.ok is False and late_member.status == "unit_closed", late_member.status
    other = task.write("pricing.py", PRICING_FIXED)
    assert other.ok is False and other.status == "unit_in_progress", other.status
    assert (shipping / "pricing.py").read_text(encoding="utf-8") == PRICING
    assert task.write("shipping.py", SHIPPING_FIXED).ok
    assert _journal(task.id)["proposals"]["pricing"]["approved"]  # still approved, waiting for the checkpoint


def test_a_unit_changes_each_file_once(shipping):
    task = _coupled_proposed(shipping, "unit-duplicate")
    duplicate = task.propose("rename-again", "units.py", UNITS_FIXED + "# again\n", unit="rename-conversion")
    assert duplicate.ok is False and duplicate.status == "unit_duplicate_target", duplicate.status


# ---------------------------------------------------------------------------
# Preservation: pre-mutation refusals, cancellation, restart, guidance and offers
# ---------------------------------------------------------------------------


def test_commands_between_approval_and_the_first_change_are_still_refused(shipping):
    task = _two_independent(shipping, "pre-mutation")
    refused = task.check("python3 check_all.py")
    assert refused.ok is False and refused.status == "stage_violation"
    assert _journal(task.id)["stage"] == "mutate"


def test_the_checkpoint_survives_a_restart_and_cancellation_stays_terminal(shipping):
    from core.code_assistant.task_runtime import code_task_runtime

    task = _two_independent(shipping, "checkpoint-restart")
    assert task.write("pricing.py", PRICING_FIXED).ok
    code_task_runtime().reset()
    assert task.write("labels.py", LABELS_FIXED).status == "checkpoint_required"
    assert task.check("python3 check_pricing.py").details["tool_result"]["success"] is True
    code_task_runtime().reset()
    assert task.write("labels.py", LABELS_FIXED).status == "checkpoint_required"  # the full check is still owed
    assert task.check("python3 check_all.py").details["tool_result"]["success"] is False
    code_task_runtime().reset()
    assert _door("code.task.cancel", {"task_id": task.id, "reason": "operator"}, task.ctx).ok
    assert task.write("labels.py", LABELS_FIXED).status == "cancelled"
    assert (shipping / "labels.py").read_text(encoding="utf-8") == LABELS


def test_guidance_and_offer_name_the_checkpoint_and_the_pending_repair(shipping):
    from core.code_assistant.task_runtime import enforce_code_task_completion
    from core.tool_offer_assembly import assemble_tool_offer

    task = _two_independent(shipping, "guidance")
    landed = task.write("pricing.py", PRICING_FIXED)
    assert landed.details["pending_repairs"] == ["labels"]
    assert "labels" in " ".join(landed.details["next"]) and "check" in " ".join(landed.details["next"])
    refused = task.write("labels.py", LABELS_FIXED)
    assert "code.task.step" in refused.response_text and "workspace.run_tests" in refused.response_text
    offer = assemble_tool_offer(user_text="continue the repair", task_class="debugging", source_context=dict(task.ctx))
    assert "code.task.step" in offer.intents
    verdict = enforce_code_task_completion(
        {"response": "done", "success": True, "details": {}},
        [{"tool_name": "code.task.step", "details": dict(landed.details)}],
    )
    row = verdict["details"]["unfinished_code_tasks"][0]
    assert row["stage"] == "narrow_test" and "labels" in " ".join(row["next"]), row


def _install_recorded(tmp_path: Path, label: str, scenario: str):
    """A journal an older runtime wrote, reinstalled as recorded: its workspace bytes in a fresh repository and
    the journal byte-for-byte except `workspace_root`, which points at the recreated folder."""
    source = LEGACY / label / scenario
    recorded = json.loads((source / "journal.json").read_text(encoding="utf-8"))
    workspace = tmp_path / f"legacy-{label}-{scenario}"
    shutil.copytree(source / "workspace", workspace)
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "recorded")
    target = Path(os.environ["VOOL_CODE_TASK_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{recorded['task_id']}.json").write_text(
        json.dumps({**recorded, "workspace_root": str(workspace.resolve())}), encoding="utf-8")
    ctx = {"workspace": str(workspace), "workspace_root": str(workspace), "session_id": recorded["session_id"],
           "runtime_session_id": recorded["session_id"], "operating_mode": "auto"}

    def step(sid: str, intent: str, arguments: dict):
        return _door("code.task.step", {"task_id": recorded["task_id"], "step_id": sid, "intent": intent,
                                        "arguments": arguments}, ctx)

    return recorded, ctx, step, workspace


@pytest.mark.parametrize("label", ["v1_df49", "v2_e064"])
def test_a_recorded_part_applied_batch_migrates_to_a_checkpoint(shipping, tmp_path, label):
    recorded, _ctx, step, workspace = _install_recorded(tmp_path, label, "two_pending_units")
    assert recorded["stage"] == "mutate"  # the older runtimes kept the batch at mutate
    b = recorded["proposals"]["b"]["arguments"]
    assert step("b-early", "workspace.write_file", b).status == "checkpoint_required"
    narrow = step("migrated-narrow", "workspace.run_tests", {"command": "node check.js"})
    assert narrow.details["tool_result"]["success"] is True and narrow.details["stage"] == "cumulative"
    cumulative = step("migrated-full", "workspace.run_tests", {"command": "node check.js"})
    assert cumulative.details["tool_result"]["success"] is True and cumulative.details["stage"] == "mutate"
    assert step("b", "workspace.write_file", b).ok
    assert (workspace / "labels.js").read_text(encoding="utf-8") == b["content"]
    assert _journal(recorded["task_id"])["checkpoint"]["unit"] == "b"


@pytest.mark.parametrize("label", ["v1_df49", "v2_e064"])
def test_a_migrated_batch_is_at_its_checkpoint_before_any_step(shipping, tmp_path, label):
    """The first thing read about a migrated part-applied batch is already its checkpoint, so the check the law
    asks for runs first -- rather than being refused as a command at `mutate` until a premature write happens to
    re-place the task."""
    recorded, ctx, step, _workspace = _install_recorded(tmp_path, label, "two_pending_units")
    report = _door("code.task.report", {"task_id": recorded["task_id"]}, ctx).details
    assert report["stage"] == "narrow_test", report["stage"]
    assert any("focused" in action for action in report.get("next") or []), report.get("next")
    narrow = step("migrated-narrow-first", "workspace.run_tests", {"command": "node check.js"})
    assert narrow.details["executed"] and narrow.details["stage"] == "cumulative", (narrow.status, narrow.response_text)


@pytest.mark.parametrize("label", ["v1_df49", "v2_e064"])
def test_a_recorded_task_awaiting_its_full_check_keeps_its_passed_focused_check(shipping, tmp_path, label):
    """An older runtime recorded this task after its repair's focused check passed and before the full check. The
    migration keeps that checkpoint: the task stays at `cumulative` with the focused check still current, and the
    full check alone completes the validation."""
    recorded, ctx, step, _workspace = _install_recorded(tmp_path, label, "narrow_passed")
    assert recorded["stage"] == "cumulative" and recorded["narrow"]["success"] is True
    report = _door("code.task.report", {"task_id": recorded["task_id"]}, ctx).details
    assert report["stage"] == "cumulative", report["stage"]
    assert (report.get("checkpoint") or {}).get("revision") == 1, report.get("checkpoint")
    full = step("migrated-full", "workspace.run_tests", {"command": "node check.js"})
    assert full.details["tool_result"]["success"] is True and full.details["stage"] == "inspect_diff", \
        (full.status, full.details.get("stage"))
    history = [(v["stage"], v["success"], v["revision"]) for v in _journal(recorded["task_id"])["verifications"]]
    assert history[-2:] == [("narrow_test", True, 1), ("cumulative", True, 1)], history
