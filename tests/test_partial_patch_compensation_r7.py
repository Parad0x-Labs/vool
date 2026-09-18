"""Revision 7: an approved patch stopped part-way returns its files to their state before the patch.

The protected writer (`_apply_protected_unified_diff`) writes an approved patch file by file. When a later
file cannot be written, every file it already wrote is compensated against its recorded pre-state: an
existing file gets its reviewed bytes and mode back, and a file the patch CREATED is removed -- but only
while it is still the entry this patch created, holding the bytes this patch wrote. A file another writer
edited, replaced (even with the same bytes) or turned into a link is kept, and the result names it as not
restored instead of claiming it was.

Every approval comes from the real control plane (open, read, reproduce, identify, propose, approve)
through the production door, and every write is a real write to a disposable workspace. The data differs
from the review's billing cases (`tests/test_partial_patch_review.py`): a stock module whose reorder point
adds instead of multiplying, a patch that creates a policy note and a thresholds file and updates two
modules. The first case is stopped by a real filesystem condition (a destination directory the process may
not write), with no timing hook. The controls that need another writer to act at a precise moment say so
in their docstrings: those moments are injected by wrapping a writer internal, and nothing else about the
approval, the step or the writer is replaced."""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

import pytest

from tests.repoops._harness import context, door
from tests.repoops.test_forge_actions import world  # noqa: F401 -- fixture
from tests.test_approved_destination_identity_r6 import _new_effects, _patch_step, _turn_ids

STOCK_BUGGY = "def reorder_point(daily_use, lead_days):\n    return daily_use + lead_days\n"
STOCK_FIXED = "def reorder_point(daily_use, lead_days):\n    return daily_use * lead_days\n"
ZONES_BEFORE = "ZONE_DAYS = {'north': 2, 'south': 4}\n"
ZONES_AFTER = "ZONE_DAYS = {'north': 3, 'south': 5}\n"
POLICY_TEXT = "Reorder when stock falls below daily use times lead days.\n"
THRESHOLDS_TEXT = "sku,reorder_at\nA-100,42\n"
README_TEXT = "counts are per warehouse\n"

RESTOCK_PATCH = (
    "diff --git a/inventory/stock.py b/inventory/stock.py\n"
    "--- a/inventory/stock.py\n"
    "+++ b/inventory/stock.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def reorder_point(daily_use, lead_days):\n"
    "-    return daily_use + lead_days\n"
    "+    return daily_use * lead_days\n"
    "diff --git a/inventory/restock_policy.md b/inventory/restock_policy.md\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/inventory/restock_policy.md\n"
    "@@ -0,0 +1 @@\n"
    "+Reorder when stock falls below daily use times lead days.\n"
    "diff --git a/inventory/thresholds.csv b/inventory/thresholds.csv\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/inventory/thresholds.csv\n"
    "@@ -0,0 +1,2 @@\n"
    "+sku,reorder_at\n"
    "+A-100,42\n"
    "diff --git a/shipping/zones.py b/shipping/zones.py\n"
    "--- a/shipping/zones.py\n"
    "+++ b/shipping/zones.py\n"
    "@@ -1 +1 @@\n"
    "-ZONE_DAYS = {'north': 2, 'south': 4}\n"
    "+ZONE_DAYS = {'north': 3, 'south': 5}\n"
)
ALL_PATHS = ("inventory/restock_policy.md", "inventory/stock.py", "inventory/thresholds.csv", "shipping/zones.py")


def _inventory_world(root: Path) -> None:
    (root / "inventory").mkdir()
    (root / "shipping").mkdir()
    stock = root / "inventory" / "stock.py"
    stock.write_text(STOCK_BUGGY, encoding="utf-8")
    stock.chmod(0o640)
    (root / "inventory" / "README.md").write_text(README_TEXT, encoding="utf-8")
    (root / "shipping" / "zones.py").write_text(ZONES_BEFORE, encoding="utf-8")


def _approved_restock_patch(ctx: dict) -> tuple[str, dict]:
    """open -> read -> reproduce (an executed, failing check) -> identify -> propose -> approve, through the door."""
    opened = door("code.task.open", {"objective": "Work out why reorder_point() in inventory/stock.py orders too little and fix it"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    read = door("code.task.step", {"task_id": task_id, "step_id": "read-stock", "intent": "workspace.read_file",
                                   "arguments": {"path": "inventory/stock.py"}}, ctx)
    assert read.ok, read.response_text
    # the coding mission's reviewed-base law binds every file a patch touches to content this
    # task read (the df49-era rig only read the owner); read the second patched file too
    read_zones = door("code.task.step", {"task_id": task_id, "step_id": "read-zones", "intent": "workspace.read_file",
                                         "arguments": {"path": "shipping/zones.py"}}, ctx)
    assert read_zones.ok, read_zones.response_text
    # Harness repair (Goal 2, 2026-09-18): the original probe was `python -c "import runpy; assert ..."`
    # -- semicolons inside the quoted argument, which the integrated sandbox's compound-syntax
    # guard blocks on the RAW text. On the pre-coding-merge tree the step still carried
    # tool_result.success=False for that refusal, so the assertion passed VACUOUSLY (the check
    # never executed); the coding mission's honest-evidence contract rightly stopped shaping
    # pre-execution refusals as validation results, which exposed this. A probe FILE is a real,
    # sandbox-legal, genuinely failing check -- the reproduction this rig always meant.
    (Path(ctx["workspace_root"]) / "reorder_probe.py").write_text(
        "import runpy\n"
        "assert runpy.run_path('inventory/stock.py')['reorder_point'](4, 3) == 12\n",
        encoding="utf-8",
    )
    reproduced = door("code.task.step", {"task_id": task_id, "step_id": "repro-stock", "intent": "workspace.run_tests",
                                         "arguments": {"command": "python reorder_probe.py"}}, ctx)
    assert reproduced.details["tool_result"]["success"] is False, reproduced.details
    identified = door("code.task.identify", {"task_id": task_id, "path": "inventory/stock.py", "line": 2,
                                             "reason": "reorder_point adds the lead days instead of multiplying by them"}, ctx)
    assert identified.ok, identified.response_text
    args = {"patch": RESTOCK_PATCH}
    proposed = door("code.task.propose", {"task_id": task_id, "proposal_id": "restock", "intent": "workspace.apply_unified_diff",
                                          "arguments": args, "rationale": "multiply by lead days; record the policy and thresholds"}, ctx)
    assert proposed.ok, (proposed.status, proposed.response_text)
    approved = door("code.task.approve", {"task_id": task_id, "proposal_id": "restock"}, ctx)
    assert approved.ok, (approved.status, approved.response_text)
    return task_id, args


def _writer(result) -> dict:
    return dict(result.details.get("tool_result") or {})


def _no_leftover_entries(directory: Path) -> list[str]:
    """Temporary or set-aside entries a writer or a compensation left behind (their names start with a dot)."""
    return sorted(name for name in os.listdir(directory) if name.startswith("."))


def _retarget_shipping_on_open(root: Path, monkeypatch, then=None) -> None:
    """Injected timing: when the writer opens `shipping` for the patch's last file, `shipping` is swapped for a
    link to `decoy`, so the writer refuses that file after it has written the first three. ``then`` runs in the
    same instant (another writer acting before the compensation starts)."""
    from core.execution import artifacts

    original = artifacts._open_pinned_directory

    def retarget(parent):
        if Path(parent).name == "shipping" and not (root / "shipping").is_symlink():
            os.rename(root / "shipping", root / "shipping-moved")
            (root / "shipping").symlink_to(root / "decoy", target_is_directory=True)
            if then is not None:
                then()
        return original(parent)

    monkeypatch.setattr(artifacts, "_open_pinned_directory", retarget)


def _decoy(root: Path) -> None:
    (root / "decoy").mkdir()
    (root / "decoy" / "zones.py").write_text(ZONES_BEFORE, encoding="utf-8")


# ---------------------------------------------------------------------------
# Novel case: a real filesystem refusal part-way through, no timing hook
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="a directory without write permission refuses a write only to an unprivileged POSIX process")
def test_a_patch_stopped_by_a_directory_it_may_not_write_removes_its_new_files_and_restores_the_rest(world) -> None:  # noqa: F811 -- the imported fixture
    root, _bare, _forge = world
    _inventory_world(root)
    ctx = context(root, session="compensation-read-only-directory")
    task_id, args = _approved_restock_patch(ctx)
    turns_before = _turn_ids()
    (root / "shipping").chmod(0o555)
    try:
        stopped = door("code.task.step", _patch_step(task_id, args), ctx)
    finally:
        (root / "shipping").chmod(0o755)
    writer = _writer(stopped)
    assert stopped.ok is False and stopped.status == "write_failed", (stopped.status, stopped.response_text)
    assert not (root / "inventory" / "restock_policy.md").exists(), writer
    assert not (root / "inventory" / "thresholds.csv").exists(), writer
    assert (root / "inventory" / "stock.py").read_text(encoding="utf-8") == STOCK_BUGGY
    assert stat.S_IMODE((root / "inventory" / "stock.py").stat().st_mode) == 0o640
    assert (root / "shipping" / "zones.py").read_text(encoding="utf-8") == ZONES_BEFORE
    assert (root / "inventory" / "README.md").read_text(encoding="utf-8") == README_TEXT
    assert _no_leftover_entries(root / "inventory") == [] and _no_leftover_entries(root / "shipping") == []
    assert sorted(writer.get("removed_paths") or []) == ["inventory/restock_policy.md", "inventory/thresholds.csv"], writer
    assert writer.get("restored_paths") == ["inventory/stock.py"], writer
    assert writer.get("restore_failures") == [] and writer.get("changed_paths") == [], writer
    assert "`shipping/zones.py` could not be written" in stopped.response_text, stopped.response_text
    assert "inventory/restock_policy.md" in stopped.response_text and "NOT restored" not in stopped.response_text
    effects = _new_effects(turns_before)
    assert {(path, outcome) for path, outcome, _status in effects} == {(path, "refused") for path in ALL_PATHS}, effects


# ---------------------------------------------------------------------------
# Preservation controls: another writer's change is never undone, and is reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("interference", ["edited-in-place", "saved-atomically-with-the-same-bytes", "replaced-by-a-link-outside"])
def test_a_new_file_another_writer_touched_before_compensation_is_kept_and_reported(world, tmp_path, monkeypatch, interference) -> None:  # noqa: F811 -- the imported fixture
    """Injected timing: at the refusal of the last file (see `_retarget_shipping_on_open`) another writer touches
    the patch's new `inventory/restock_policy.md`: rewrites it in place, atomically saves the very bytes the patch
    wrote (a new entry under the same name), or replaces it with a link to a file outside the workspace."""
    root, _bare, _forge = world
    _inventory_world(root)
    _decoy(root)
    outside = tmp_path / "outside" / "private.txt"
    outside.parent.mkdir()
    outside.write_text("outside the workspace\n", encoding="utf-8")
    ctx = context(root, session=f"compensation-{interference}")
    task_id, args = _approved_restock_patch(ctx)
    policy = root / "inventory" / "restock_policy.md"
    kept: dict[str, int] = {}

    def interfere() -> None:
        if interference == "edited-in-place":
            policy.write_text("edited by the operator\n", encoding="utf-8")
        elif interference == "saved-atomically-with-the-same-bytes":
            saved = policy.with_name(".restock_policy.md.editor-save")
            saved.write_text(POLICY_TEXT, encoding="utf-8")
            os.rename(saved, policy)
        else:
            policy.unlink()
            policy.symlink_to(outside)
        kept["inode"] = os.lstat(policy).st_ino

    _retarget_shipping_on_open(root, monkeypatch, then=interfere)
    turns_before = _turn_ids()
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    writer = _writer(refused)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    # the other writer's entry is exactly what remains at the name
    assert os.lstat(policy).st_ino == kept["inode"], writer
    if interference == "edited-in-place":
        assert policy.read_text(encoding="utf-8") == "edited by the operator\n"
    elif interference == "saved-atomically-with-the-same-bytes":
        assert not policy.is_symlink() and policy.read_text(encoding="utf-8") == POLICY_TEXT
    else:
        assert policy.is_symlink() and os.readlink(policy) == str(outside)
    assert outside.read_text(encoding="utf-8") == "outside the workspace\n"
    # everything the patch still owned is back
    assert not (root / "inventory" / "thresholds.csv").exists(), writer
    assert (root / "inventory" / "stock.py").read_text(encoding="utf-8") == STOCK_BUGGY
    assert (root / "shipping-moved" / "zones.py").read_text(encoding="utf-8") == ZONES_BEFORE
    assert (root / "decoy" / "zones.py").read_text(encoding="utf-8") == ZONES_BEFORE
    assert _no_leftover_entries(root / "inventory") == []
    # the residual is reported, never claimed restored
    assert writer.get("removed_paths") == ["inventory/thresholds.csv"], writer
    assert writer.get("restored_paths") == ["inventory/stock.py"], writer
    assert [row.get("path") for row in writer.get("restore_failures") or []] == ["inventory/restock_policy.md"], writer
    assert writer.get("changed_paths") == ["inventory/restock_policy.md"], writer
    assert "NOT restored" in refused.response_text, refused.response_text
    assert "inventory/restock_policy.md" in refused.response_text.split("NOT restored", 1)[1], refused.response_text
    outcomes = {path: outcome for path, outcome, _status in _new_effects(turns_before)}
    assert outcomes.get("inventory/restock_policy.md") == "failed", outcomes
    assert all(outcomes.get(path) == "refused" for path in ("inventory/stock.py", "inventory/thresholds.csv")), outcomes


def test_an_entry_replaced_while_the_compensation_removes_it_is_put_back_and_reported(world, monkeypatch) -> None:  # noqa: F811 -- the imported fixture
    """Injected timing inside the compensation itself: between the removal's examination of the patch's new
    `inventory/thresholds.csv` (it is still the file the patch created) and the removal, another writer atomically
    saves different bytes under that name. The removal must not take the other writer's file with it."""
    from core.execution import artifacts

    root, _bare, _forge = world
    _inventory_world(root)
    _decoy(root)
    ctx = context(root, session="compensation-replaced-during-removal")
    task_id, args = _approved_restock_patch(ctx)
    thresholds = root / "inventory" / "thresholds.csv"
    state: dict[str, object] = {"refused": False, "inode": None}
    _retarget_shipping_on_open(root, monkeypatch, then=lambda: state.update(refused=True))
    original_digest = artifacts._pinned_entry_sha256

    def examine_then_replace(name, fd):
        digest = original_digest(name, fd)
        if state["refused"] and name == "thresholds.csv" and state["inode"] is None:
            saved = thresholds.with_name(".thresholds.csv.editor-save")
            saved.write_text("sku,reorder_at\nB-200,7\n", encoding="utf-8")
            os.rename(saved, thresholds)
            state["inode"] = os.lstat(thresholds).st_ino
        return digest

    monkeypatch.setattr(artifacts, "_pinned_entry_sha256", examine_then_replace)
    refused = door("code.task.step", _patch_step(task_id, args), ctx)
    writer = _writer(refused)
    assert refused.ok is False and refused.status == "destination_changed", (refused.status, refused.response_text)
    assert state["inode"] is not None, "the compensation never examined the new file"
    assert thresholds.read_text(encoding="utf-8") == "sku,reorder_at\nB-200,7\n"
    assert os.lstat(thresholds).st_ino == state["inode"], writer
    assert _no_leftover_entries(root / "inventory") == [], writer
    assert not (root / "inventory" / "restock_policy.md").exists(), writer
    assert (root / "inventory" / "stock.py").read_text(encoding="utf-8") == STOCK_BUGGY
    assert [row.get("path") for row in writer.get("restore_failures") or []] == ["inventory/thresholds.csv"], writer
    assert writer.get("changed_paths") == ["inventory/thresholds.csv"], writer
    assert writer.get("removed_paths") == ["inventory/restock_policy.md"], writer
    assert "inventory/thresholds.csv" in refused.response_text.split("NOT restored", 1)[-1], refused.response_text


def test_a_legitimate_creation_in_an_approved_patch_still_applies(world) -> None:  # noqa: F811 -- the imported fixture
    root, _bare, _forge = world
    _inventory_world(root)
    ctx = context(root, session="compensation-control-creation")
    task_id, args = _approved_restock_patch(ctx)
    turns_before = _turn_ids()
    applied = door("code.task.step", _patch_step(task_id, args), ctx)
    writer = _writer(applied)
    assert applied.ok, (applied.status, applied.response_text)
    assert (root / "inventory" / "stock.py").read_text(encoding="utf-8") == STOCK_FIXED
    assert stat.S_IMODE((root / "inventory" / "stock.py").stat().st_mode) == 0o640
    assert (root / "inventory" / "restock_policy.md").read_text(encoding="utf-8") == POLICY_TEXT
    assert (root / "inventory" / "thresholds.csv").read_text(encoding="utf-8") == THRESHOLDS_TEXT
    assert (root / "shipping" / "zones.py").read_text(encoding="utf-8") == ZONES_AFTER
    assert (root / "inventory" / "README.md").read_text(encoding="utf-8") == README_TEXT
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE((root / "inventory" / "thresholds.csv").stat().st_mode) == 0o666 & ~umask
    assert sorted(writer.get("changed_paths") or []) == sorted(ALL_PATHS), writer
    assert not writer.get("removed_paths") and not writer.get("restore_failures"), writer
    assert _no_leftover_entries(root / "inventory") == [] and _no_leftover_entries(root / "shipping") == []
    effects = _new_effects(turns_before)
    assert {(path, outcome) for path, outcome, _status in effects} == {(path, "succeeded") for path in ALL_PATHS}, effects
