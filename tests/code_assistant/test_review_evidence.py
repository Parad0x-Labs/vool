"""``code.review_evidence`` -- the journal-backed evidence projection, proven at the ONE door.

The mutation it reviews is produced by the canonical architecture itself (``code.task.*``
through ``execute_runtime_tool``), never by a direct handler call, so a green row here is a row
a served model could reach with the same intents. This file is the surviving coverage of the
2026-09-02 canonical-contract lane after reconciliation: the projection's VERIFIED / DRIFTED /
MISSING honesty against live bytes, on the one task runtime.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_code_assistant_task_runtime import BUGGY, FIXED, _ctx, _door, _sha, fixture_repo  # noqa: F401


def _run_task_to_mutation(root: Path, ctx: dict) -> str:
    opened = _door("code.task.open", {"objective": "repair calc"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "r", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}},
        ctx,
    )
    read = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "o", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    assert read.ok, read.response_text
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "subtracts instead of adds"}, ctx)
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": _sha(BUGGY)},
            # Canonical contract: a proposal carries the rationale naming owner and root cause.
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    write = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "w", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED}},
        ctx,
    )
    assert write.ok, write.response_text
    assert (root / "calc.py").read_text(encoding="utf-8") == FIXED
    return task_id


def _review(ctx: dict, *, turn_id: str = ""):
    arguments = {"turn_id": turn_id} if turn_id else {}
    result = _door("code.review_evidence", arguments, ctx)
    assert result is not None, "code.review_evidence is contracted at the production door"
    return result


def test_review_summarizes_the_journaled_mutation_with_verified_bytes(fixture_repo: Path) -> None:  # noqa: F811
    ctx = _ctx(fixture_repo)
    _run_task_to_mutation(fixture_repo, ctx)
    result = _review(ctx)
    assert result.ok, result.response_text
    rows = result.details["evidence"]
    row = next(item for item in rows if item["path"] == "calc.py")
    assert row["state"] == "VERIFIED", row
    assert row["operation"], row
    assert row["after_sha256"] == _sha(FIXED)
    assert row["current_sha256"] == _sha(FIXED)
    assert result.details["verified_count"] >= 1


def test_review_flags_post_mutation_tampering_as_drifted(fixture_repo: Path) -> None:  # noqa: F811
    ctx = _ctx(fixture_repo)
    _run_task_to_mutation(fixture_repo, ctx)
    (fixture_repo / "calc.py").write_text("# quietly rewritten after the fact\n", encoding="utf-8")
    result = _review(ctx)
    assert result.ok
    assert result.details["drifted_count"] >= 1
    row = next(item for item in result.details["evidence"] if item["path"] == "calc.py")
    assert row["state"] == "DRIFTED", row


def test_review_reports_a_deleted_mutated_file_as_missing(fixture_repo: Path) -> None:  # noqa: F811
    ctx = _ctx(fixture_repo)
    _run_task_to_mutation(fixture_repo, ctx)
    (fixture_repo / "calc.py").unlink()
    result = _review(ctx)
    assert result.ok
    row = next(item for item in result.details["evidence"] if item["path"] == "calc.py")
    assert row["state"] == "MISSING", row


def test_review_refuses_an_unknown_turn_typed(fixture_repo: Path) -> None:  # noqa: F811
    ctx = _ctx(fixture_repo)
    result = _review(ctx, turn_id="no-such-turn")
    assert result.status == "not_found"
    assert result.details.get("executed") is False


def test_review_after_task_rollback_reports_the_restored_bytes(fixture_repo: Path) -> None:  # noqa: F811
    """Rollback through the task's own control plane (bounded internal scope, the pattern the
    canonical suite proved): the journal then honestly shows the fix turn's rows as DRIFTED
    against the restored defect bytes, and the store marks the turn rolled back so no later
    review can claim the fix still stands."""
    from core.blackbox.store import default_store
    from tests._toolchain_fixtures import internal_scope

    ctx = _ctx(fixture_repo)
    _run_task_to_mutation(fixture_repo, ctx)
    result = _review(ctx)
    assert result.details["verified_count"] >= 1
    turn_id = str(result.details["turn_id"])

    scoped = {
        **ctx,
        **internal_scope(
            "code-review-rollback",
            "delete_files",
            "overwrite_existing_files",
            "modify_files",
            "create_files",
            intents=("workspace.rollback_last_change",),
        ),
    }
    rolled = _door("code.task.rollback", {"task_id": _task_id_of(fixture_repo)}, scoped)
    assert rolled.ok, rolled.response_text
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == BUGGY

    after = _review(ctx, turn_id=turn_id)
    row = next(item for item in after.details["evidence"] if item["path"] == "calc.py")
    assert row["state"] == "DRIFTED", row
    assert row["current_sha256"] == _sha(BUGGY)
    assert default_store().turn_index()[turn_id].rolled_back is True


def _task_id_of(root: Path) -> str:
    import json

    files = sorted((Path(root).parent / "code_tasks").glob("ct-*.json"))
    assert len(files) == 1, files
    return str(json.loads(files[0].read_text(encoding="utf-8"))["task_id"])
