"""An approval binds the reviewed base of every file it changes (coding revision 3, P1-1).

The independent review proved that an approved repair could be re-pointed at content nobody
reviewed: after an independent edit, supplying the NEW file hash let the old approval overwrite
that edit. These checks use genuinely different data from the review's JavaScript cases -- a
Python stock-keeping module, both full-file and in-place writer intents -- plus the controls
that keep the fix from passing by refusing everything: an unchanged approved write still lands,
a wrong hash on an unchanged file is an approval mismatch rather than a stale base, recovery
through a fresh proposal preserves the intervening edit, and the binding survives a restart,
competing tasks, competing processes and stale runtime caches.

Every call crosses the production door (``execute_runtime_tool``) with real files, real git
repositories and real commands. No model is involved and none is claimed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUGGY = "def restock(on_hand, delivered):\n    return on_hand - delivered\n"
FIXED = "def restock(on_hand, delivered):\n    return on_hand + delivered\n"
CHECK = "from inventory import restock\nassert restock(12, 5) == 17, restock(12, 5)\nprint('inventory ok')\n"
NOTES = "warehouse notes: do not edit\n"
EXTERNAL = BUGGY + "# counted by the warehouse team\n"
LEGACY = REPO / "tests" / "fixtures" / "code_task_journals"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def stock_repo(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "stock"
    root.mkdir()
    (root / "inventory.py").write_text(BUGGY, encoding="utf-8")
    (root / "check_inventory.py").write_text(CHECK, encoding="utf-8")
    (root / "notes.md").write_text(NOTES, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    code_task_runtime().reset()
    reset_mode_permission_state()


def _ctx(root: Path, session: str) -> dict:
    return {"workspace": str(root), "workspace_root": str(root), "session_id": session,
            "runtime_session_id": session, "operating_mode": "auto"}


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def _journal(task_id: str) -> dict:
    return json.loads((Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json").read_text(encoding="utf-8"))


def _repair_arguments(intent: str, content: str = FIXED) -> dict:
    if intent == "workspace.write_file":
        return {"path": "inventory.py", "content": content}
    return {"path": "inventory.py", "old_text": "on_hand - delivered", "new_text": "on_hand + delivered"}


def _approved(root: Path, *, intent: str, explicit_hash: bool, session: str, content: str = FIXED,
              proposal_id: str = "restock-fix"):
    ctx = _ctx(root, session)
    task_id = _door("code.task.open", {"objective": "Repair restock so the stock check passes"}, ctx).details["task_id"]

    def step(step_id: str, inner_intent: str, arguments: dict):
        return _door("code.task.step", {"task_id": task_id, "step_id": step_id, "intent": inner_intent,
                                        "arguments": arguments}, ctx)

    red = step("repro", "workspace.run_tests", {"command": "python3 check_inventory.py"})
    assert red.details["executed"] and red.details["tool_result"]["success"] is False, red.details
    assert _door("code.task.identify", {"task_id": task_id, "path": "inventory.py", "line": 2,
                                        "reason": "restock subtracts deliveries"}, ctx).ok
    read = step("read", "workspace.read_file", {"path": "inventory.py"})
    assert read.ok and read.details["tool_result"]["hash"] == _sha(BUGGY)
    arguments = _repair_arguments(intent, content)
    if explicit_hash:
        arguments["expected_hash"] = read.details["tool_result"]["hash"]
    proposed = _door("code.task.propose", {"task_id": task_id, "proposal_id": proposal_id, "intent": intent,
                                           "arguments": arguments,
                                           "rationale": "Owner inventory.py: deliveries must be added to stock."}, ctx)
    assert proposed.ok, proposed.response_text
    assert proposed.details["preview"]["base"] == {"inventory.py": "file:" + _sha(BUGGY)}
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": proposal_id}, ctx).ok
    return ctx, task_id, arguments, step


def _approve_notes_repair(ctx: dict, task_id: str, step) -> dict:
    """A second, independent approved repair in the same task (the notes file), so a mutation
    remains lawful after the first one lands."""
    read = step("read-notes", "workspace.read_file", {"path": "notes.md"})
    assert read.ok
    arguments = {"path": "notes.md", "content": NOTES + "restock rule: deliveries add stock\n"}
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "notes", "intent": "workspace.write_file",
                                       "arguments": arguments, "rationale": "Owner notes.md: record the restock rule."},
                 ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "notes"}, ctx).ok
    return arguments


INTENTS = ["workspace.write_file", "workspace.replace_in_file"]


# ---------------------------------------------------------------------------
# The failure class: an intervening edit after approval is never overwritten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hash_mode", ["replaced", "omitted", "reviewed"])
@pytest.mark.parametrize("intent", INTENTS)
def test_intervening_edit_after_approval_is_never_overwritten(stock_repo, intent, hash_mode):
    _ctx_unused, task_id, arguments, step = _approved(stock_repo, intent=intent, explicit_hash=True,
                                                      session=f"edit-{hash_mode}")
    (stock_repo / "inventory.py").write_text(EXTERNAL, encoding="utf-8")  # an independent editor, after approval
    call = {k: v for k, v in arguments.items() if k != "expected_hash"}
    if hash_mode == "replaced":
        call["expected_hash"] = _sha(EXTERNAL)  # re-pointing: a fresh hash is not fresh consent
    elif hash_mode == "reviewed":
        call["expected_hash"] = _sha(BUGGY)
    result = step("apply", intent, call)
    assert result.ok is False and result.status == "stale_base", (result.status, result.response_text)
    assert result.details["executed"] is False
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == EXTERNAL, "the intervening edit survives"
    proposal = _journal(task_id)["proposals"]["restock-fix"]
    assert proposal["consumed_by"] == "" and proposal["reserved_by"] == ""
    if hash_mode == "replaced":
        # refused before any dispatch: a re-pointed hash never reaches the writer
        assert proposal["invalidated"]["reason"] == "reviewed_base_changed"
        assert result.details["tool_result"] == {}
    else:
        # the physical writer enforced the REVIEWED base the runtime handed it, even when omitted
        assert proposal["invalidated"]["reason"] == "writer_refused_stale_base"
        assert result.details["tool_result"]["expected_hash"] == _sha(BUGGY)
        assert result.details["tool_result"]["current_hash"] == _sha(EXTERNAL)
    # The old approval is dead for good, even if the file is put back exactly as reviewed.
    (stock_repo / "inventory.py").write_text(BUGGY, encoding="utf-8")
    again = step("apply-again", intent, {**call, "expected_hash": _sha(BUGGY)})
    assert again.ok is False and again.status == "stale_base", again.status
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == BUGGY


@pytest.mark.parametrize("intent", INTENTS)
def test_recovery_proposes_against_the_edited_content_and_keeps_the_edit(stock_repo, intent):
    ctx, task_id, arguments, step = _approved(stock_repo, intent=intent, explicit_hash=False, session="recover")
    (stock_repo / "inventory.py").write_text(EXTERNAL, encoding="utf-8")
    refused = step("apply", intent, arguments)
    assert refused.status == "stale_base"
    reread = step("reread", "workspace.read_file", {"path": "inventory.py"})
    assert reread.details["tool_result"]["hash"] == _sha(EXTERNAL)
    revised = (
        {"path": "inventory.py", "content": EXTERNAL.replace("on_hand - delivered", "on_hand + delivered")}
        if intent == "workspace.write_file" else dict(_repair_arguments(intent))
    )
    proposed = _door("code.task.propose", {"task_id": task_id, "proposal_id": "restock-fix-2", "intent": intent,
                                           "arguments": revised,
                                           "rationale": "Owner inventory.py: add deliveries, keeping the audit note."}, ctx)
    assert proposed.ok, proposed.response_text
    assert proposed.details["preview"]["base"] == {"inventory.py": "file:" + _sha(EXTERNAL)}
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "restock-fix-2"}, ctx).ok
    landed = step("apply-revised", intent, revised)
    assert landed.ok, (landed.status, landed.response_text)
    final = (stock_repo / "inventory.py").read_text(encoding="utf-8")
    assert "on_hand + delivered" in final and "# counted by the warehouse team" in final
    journal = _journal(task_id)
    assert journal["proposals"]["restock-fix"]["invalidated"]["reason"] == "writer_refused_stale_base"
    assert journal["proposals"]["restock-fix-2"]["consumed_by"] == "apply-revised"
    assert journal["revision"] == 1 and journal["stage"] == "narrow_test"
    assert (stock_repo / "notes.md").read_text(encoding="utf-8") == NOTES


def test_after_a_stale_refusal_the_offer_seats_the_reproposal_path(stock_repo):
    """The recovery a stale refusal names must be OFFERED, not only lawful: seated with `step` alone at
    `mutate`, a model's re-proposal is rejected as unoffered before the runtime ever sees it."""
    from core.code_assistant.task_runtime import active_task_control_intents

    ctx, task_id, arguments, step = _approved(stock_repo, intent="workspace.write_file", explicit_hash=False,
                                              session="offer-after-stale")
    assert set(active_task_control_intents(ctx)) >= {"code.task.step", "code.task.report"}
    assert "code.task.propose" not in active_task_control_intents(ctx)  # a live approval: execute it
    (stock_repo / "inventory.py").write_text(EXTERNAL, encoding="utf-8")
    assert step("apply", "workspace.write_file", arguments).status == "stale_base"
    seats = active_task_control_intents(ctx)
    assert "code.task.propose" in seats and "code.task.step" in seats, seats
    assert step("reread", "workspace.read_file", {"path": "inventory.py"}).ok
    revised = {"path": "inventory.py", "content": EXTERNAL.replace("on_hand - delivered", "on_hand + delivered")}
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "rebased", "intent": "workspace.write_file",
                                       "arguments": revised, "rationale": "Owner inventory.py: add deliveries."}, ctx).ok
    seats = active_task_control_intents(ctx)
    assert "code.task.approve" in seats and "code.task.step" in seats, seats
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "rebased"}, ctx).ok
    seats = active_task_control_intents(ctx)
    assert "code.task.step" in seats and "code.task.approve" not in seats and "code.task.propose" not in seats, seats
    assert step("apply-rebased", "workspace.write_file", revised).ok


# ---------------------------------------------------------------------------
# Preservation: legitimate approved writes still land, mismatches stay distinct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("intent", INTENTS)
def test_unchanged_approved_write_lands_over_the_reviewed_base(stock_repo, intent, explicit):
    _ctx_unused, task_id, arguments, step = _approved(stock_repo, intent=intent, explicit_hash=explicit,
                                                      session="unchanged")
    call = dict(arguments)
    if not explicit:
        call.pop("expected_hash", None)
    landed = step("apply", intent, call)
    assert landed.ok and landed.details["executed"], (landed.status, landed.response_text)
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    assert landed.details["tool_result"]["before_hash"] == _sha(BUGGY)
    journal = _journal(task_id)
    assert journal["proposals"]["restock-fix"]["consumed_by"] == "apply"
    assert journal["snapshots"]["inventory.py"]["sha256"] == _sha(FIXED)
    assert journal["snapshots"]["inventory.py"]["source"] == "mutation"
    assert journal["revision"] == 1
    # exact replay of the same step never re-executes, and no later step writes a second time
    (stock_repo / "inventory.py").write_text(FIXED, encoding="utf-8")
    replay = step("apply", intent, call)
    assert replay.details["replayed"] is True and replay.details["executed"] is False
    twice = step("apply-twice", intent, call)
    assert twice.ok is False and twice.details["executed"] is False, twice.status
    assert [s["step_id"] for s in _journal(task_id)["steps"].values() if s["executed"] and s["intent"] == intent] == ["apply"]


@pytest.mark.parametrize("intent", INTENTS)
def test_an_executed_approval_is_consumed_once_while_other_repairs_are_pending(stock_repo, intent):
    ctx, task_id, arguments, step = _approved(stock_repo, intent=intent, explicit_hash=True, session="consumed")
    notes = _approve_notes_repair(ctx, task_id, step)
    assert step("apply", intent, arguments).ok
    twice = step("apply-twice", intent, arguments)
    assert twice.ok is False and twice.status == "approval_consumed", twice.status
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    journal = _journal(task_id)
    assert journal["proposals"]["restock-fix"]["consumed_by"] == "apply"
    assert journal["proposals"]["notes"]["consumed_by"] == "" and not journal["proposals"]["notes"]["invalidated"]
    assert (stock_repo / "notes.md").read_text(encoding="utf-8") == NOTES
    assert notes["path"] == "notes.md"


@pytest.mark.parametrize("intent", INTENTS)
def test_a_wrong_explicit_hash_on_an_unchanged_file_is_an_approval_mismatch(stock_repo, intent):
    _ctx_unused, task_id, arguments, step = _approved(stock_repo, intent=intent, explicit_hash=True,
                                                      session="mismatch")
    wrong = step("apply-wrong", intent, {**arguments, "expected_hash": _sha("not what was reviewed\n")})
    assert wrong.ok is False and wrong.status == "approval_mismatch", wrong.status
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == BUGGY
    proposal = _journal(task_id)["proposals"]["restock-fix"]
    assert not proposal["invalidated"] and not proposal["consumed_by"] and not proposal["reserved_by"]
    landed = step("apply", intent, arguments)
    assert landed.ok, landed.response_text


def test_a_proposal_binds_only_content_the_task_reviewed(stock_repo):
    ctx = _ctx(stock_repo, "review-first")
    task_id = _door("code.task.open", {"objective": "Repair restock"}, ctx).details["task_id"]
    _door("code.task.step", {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                             "arguments": {"command": "python3 check_inventory.py"}}, ctx)
    _door("code.task.identify", {"task_id": task_id, "path": "inventory.py", "reason": "subtracts"}, ctx)
    _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                             "arguments": {"path": "inventory.py"}}, ctx)

    def propose(pid: str, arguments: dict):
        return _door("code.task.propose", {"task_id": task_id, "proposal_id": pid, "intent": "workspace.write_file",
                                           "arguments": arguments, "rationale": "Owner inventory.py: add deliveries."}, ctx)

    unseen = propose("unseen-hash", {"path": "inventory.py", "content": FIXED, "expected_hash": _sha(EXTERNAL)})
    assert unseen.ok is False and unseen.status == "stale_base", unseen.status
    unread = propose("unread-sibling", {"path": "notes.md", "content": "overwritten\n"})
    assert unread.ok is False and unread.status == "base_not_reviewed", unread.status
    aliased = propose("aliased", {"path": "./inventory.py", "content": FIXED})
    assert aliased.ok and aliased.details["preview"]["targets"] == ["inventory.py"], aliased.details
    created = propose("new-file", {"path": "restock_log.py", "content": "LOG = []\n"})
    assert created.ok, created.response_text
    assert created.details["preview"]["base"] == {"restock_log.py": "absent"}
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "new-file"}, ctx).ok
    (stock_repo / "restock_log.py").write_text("LOG = ['kept']\n", encoding="utf-8")  # someone created it first
    refused = _door("code.task.step", {"task_id": task_id, "step_id": "create", "intent": "workspace.write_file",
                                       "arguments": {"path": "restock_log.py", "content": "LOG = []\n"}}, ctx)
    assert refused.ok is False and refused.status == "stale_base", refused.status
    assert (stock_repo / "restock_log.py").read_text(encoding="utf-8") == "LOG = ['kept']\n"
    assert (stock_repo / "notes.md").read_text(encoding="utf-8") == NOTES
    assert set(_journal(task_id)["proposals"]) == {"aliased", "new-file"}  # refused proposals were never recorded


# ---------------------------------------------------------------------------
# Restart, competing writers, competing processes, stale caches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("changed", [True, False])
def test_the_binding_survives_a_cold_restart(stock_repo, changed):
    from core.code_assistant.task_runtime import code_task_runtime

    _ctx_unused, _task_id, arguments, step = _approved(stock_repo, intent="workspace.write_file",
                                                       explicit_hash=False, session="restart")
    code_task_runtime().reset()
    if changed:
        (stock_repo / "inventory.py").write_text(EXTERNAL, encoding="utf-8")
    result = step("apply", "workspace.write_file", arguments)
    if changed:
        assert result.status == "stale_base" and (stock_repo / "inventory.py").read_text(encoding="utf-8") == EXTERNAL
    else:
        assert result.ok and (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED


def test_two_tasks_racing_one_file_land_exactly_one_reviewed_write(stock_repo):
    contents = {"left": FIXED, "right": FIXED + "# right\n"}
    prepared = {
        label: _approved(stock_repo, intent="workspace.write_file", explicit_hash=True, session=f"race-{label}",
                         content=content)
        for label, content in contents.items()
    }
    results: dict[str, object] = {}
    barrier = threading.Barrier(len(prepared))

    def run(label: str) -> None:
        _ctx_unused, _task_id, arguments, step = prepared[label]
        barrier.wait()
        results[label] = step("apply", "workspace.write_file", arguments)

    threads = [threading.Thread(target=run, args=(label,)) for label in prepared]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    statuses = sorted(r.status for r in results.values())  # type: ignore[union-attr]
    assert sum(1 for r in results.values() if r.ok) == 1, statuses  # type: ignore[union-attr]
    assert "stale_base" in statuses, statuses
    winner = next(label for label, r in results.items() if r.ok)  # type: ignore[union-attr]
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == contents[winner]
    loser = next(label for label in prepared if label != winner)
    assert _journal(prepared[loser][1])["proposals"]["restock-fix"]["invalidated"]["reason"] == "writer_refused_stale_base"


_RACER = r"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["RACE_REPO"])
from core.runtime_execution_tools import execute_runtime_tool
go = Path(os.environ["RACE_GO"])
Path(os.environ["RACE_READY"]).write_text("ready")
deadline = time.monotonic() + 60
while not go.exists() and time.monotonic() < deadline:
    time.sleep(0.001)
result = execute_runtime_tool("code.task.step", json.loads(os.environ["RACE_STEP"]),
                              source_context=json.loads(os.environ["RACE_CTX"]))
print(json.dumps({"ok": bool(result.ok), "status": result.status,
                  "executed": bool((result.details or {}).get("executed"))}))
"""


def _race_processes(tmp_path: Path, ctx: dict, steps: list[dict]) -> list[dict]:
    go = tmp_path / "race.go"
    procs = []
    for index, step in enumerate(steps):
        ready = tmp_path / f"race.ready.{index}"
        env = {**os.environ, "RACE_REPO": str(REPO), "RACE_GO": str(go), "RACE_READY": str(ready),
               "RACE_STEP": json.dumps(step), "RACE_CTX": json.dumps(ctx), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": str(REPO)}
        procs.append((subprocess.Popen([sys.executable, "-c", _RACER], cwd=str(REPO), env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), ready))
    deadline = time.monotonic() + 120
    while not all(ready.exists() for _p, ready in procs) and time.monotonic() < deadline:
        time.sleep(0.01)
    go.write_text("go")
    outcomes = []
    for proc, _ready in procs:
        out, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err[-2000:]
        outcomes.append(json.loads(out.strip().splitlines()[-1]))
    return outcomes


def test_two_processes_racing_one_approval_execute_it_once_and_lose_no_journal_record(stock_repo, tmp_path):
    ctx, task_id, arguments, _step = _approved(stock_repo, intent="workspace.write_file", explicit_hash=True,
                                               session="process-race")
    outcomes = _race_processes(tmp_path, ctx, [
        {"task_id": task_id, "step_id": f"proc-{i}", "intent": "workspace.write_file", "arguments": arguments}
        for i in range(2)
    ])
    assert sum(1 for o in outcomes if o["ok"] and o["executed"]) == 1, outcomes
    assert {o["status"] for o in outcomes if not o["ok"]} <= {"approval_in_flight", "approval_consumed", "stage_violation"}, outcomes
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    journal = _journal(task_id)
    assert {"proc-0", "proc-1"} <= set(journal["steps"]), sorted(journal["steps"])  # no lost update
    executed = [s for s in journal["steps"].values() if s["intent"] == "workspace.write_file" and s["executed"]]
    assert len(executed) == 1 and journal["proposals"]["restock-fix"]["consumed_by"] == executed[0]["step_id"]


def test_two_processes_racing_two_approvals_of_one_base_land_exactly_one(stock_repo, tmp_path):
    ctx, task_id, arguments, _step = _approved(stock_repo, intent="workspace.write_file", explicit_hash=True,
                                               session="process-race-2")
    other = {"path": "inventory.py", "content": FIXED + "# second\n", "expected_hash": _sha(BUGGY)}
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "second", "intent": "workspace.write_file",
                                       "arguments": other, "rationale": "Owner inventory.py: add deliveries."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "second"}, ctx).ok
    outcomes = _race_processes(tmp_path, ctx, [
        {"task_id": task_id, "step_id": "proc-first", "intent": "workspace.write_file", "arguments": arguments},
        {"task_id": task_id, "step_id": "proc-second", "intent": "workspace.write_file", "arguments": other},
    ])
    assert sum(1 for o in outcomes if o["ok"]) == 1, outcomes
    assert {o["status"] for o in outcomes if not o["ok"]} <= {"stale_base", "stage_violation"}, outcomes
    final = (stock_repo / "inventory.py").read_text(encoding="utf-8")
    assert final in (FIXED, FIXED + "# second\n")
    journal = _journal(task_id)
    assert {"proc-first", "proc-second"} <= set(journal["steps"])
    assert sorted(bool(p["consumed_by"]) for p in journal["proposals"].values()) == [False, True]
    assert sum(1 for p in journal["proposals"].values() if p["invalidated"]) == 1


def test_a_stale_runtime_instance_can_neither_execute_nor_overwrite(stock_repo):
    from core.code_assistant.task_runtime import CodeTaskRuntime, JournalConflictError, code_task_runtime

    ctx, task_id, arguments, step = _approved(stock_repo, intent="workspace.write_file", explicit_hash=True,
                                              session="stale-cache")
    _approve_notes_repair(ctx, task_id, step)  # keeps a mutation lawful after the first one lands
    stale = CodeTaskRuntime()
    frozen = CodeTaskRuntime()
    assert stale._load(task_id).proposals["restock-fix"].approved  # both caches hold the unexecuted approval
    old_copy = copy.deepcopy(frozen._load(task_id))
    assert step("apply", "workspace.write_file", arguments).ok  # the served instance executes it
    late = stale.step({"task_id": task_id, "step_id": "apply-from-stale", "intent": "workspace.write_file",
                       "arguments": arguments}, source_context=ctx)
    assert late.ok is False and late.status == "approval_consumed", late.status
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    journal = _journal(task_id)
    assert journal["steps"]["apply"]["ok"] and journal["steps"]["apply-from-stale"]["status"] == "approval_consumed"
    # A copy decided from before that execution cannot be written back over it.
    with pytest.raises(JournalConflictError):
        frozen._persist(old_copy)
    assert _journal(task_id)["steps"]["apply"]["ok"] is True
    assert code_task_runtime()._load(task_id).proposals["restock-fix"].consumed_by == "apply"



def test_a_claimed_approval_cannot_be_taken_by_another_process_mid_flight(stock_repo, tmp_path, monkeypatch):
    """Deterministic cross-process interleaving at the runtime's own dispatch seam: the executing
    step has claimed the approval and passed its precondition; before its write reaches the door a
    REAL second process tries the same approval. The claim -- not luck -- must refuse it, and the
    journal must end with the approval consumed exactly once and never marked invalid."""
    from core.code_assistant.task_runtime import code_task_runtime

    ctx, task_id, arguments, step = _approved(stock_repo, intent="workspace.write_file", explicit_hash=True,
                                              session="mid-flight")
    runtime = code_task_runtime()
    original = runtime._dispatch
    seen: dict[str, dict] = {}

    def dispatch_after_a_competing_process(intent, dispatched, context):
        if intent == "workspace.write_file" and "child" not in seen:
            seen["child"] = _race_processes(tmp_path, ctx, [
                {"task_id": task_id, "step_id": "from-another-process", "intent": "workspace.write_file",
                 "arguments": arguments},
            ])[0]
        return original(intent, dispatched, context)

    monkeypatch.setattr(runtime, "_dispatch", dispatch_after_a_competing_process)
    landed = step("claimed", "workspace.write_file", arguments)
    assert landed.ok, (landed.status, landed.response_text)
    assert seen["child"] == {"ok": False, "status": "approval_in_flight", "executed": False}, seen
    assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    journal = _journal(task_id)
    proposal = journal["proposals"]["restock-fix"]
    assert proposal["consumed_by"] == "claimed" and not proposal["invalidated"], proposal
    assert journal["steps"]["from-another-process"]["status"] == "approval_in_flight"
    assert [s["step_id"] for s in journal["steps"].values() if s["executed"] and s["intent"] == "workspace.write_file"] == ["claimed"]

@pytest.mark.parametrize(("age_minutes", "expected"), [(20, "released"), (2, "held")])
def test_a_claim_left_by_a_stopped_instance_is_released_only_when_abandoned(stock_repo, age_minutes, expected):
    from datetime import datetime, timedelta, timezone

    from core.code_assistant.task_runtime import code_task_runtime

    _ctx_unused, task_id, arguments, step = _approved(stock_repo, intent="workspace.write_file",
                                                      explicit_hash=True, session=f"claim-{expected}")
    path = Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json"
    journal = json.loads(path.read_text(encoding="utf-8"))
    claimed_at = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    journal["steps"]["crashed"] = {**journal["steps"]["read"], "step_id": "crashed", "intent": "workspace.write_file",
                                   "arguments": arguments, "status": "in_flight", "executed": False, "ok": False,
                                   "result": {}, "completed_at": "", "proposal_id": "restock-fix"}
    journal["step_order"].append("crashed")
    journal["proposals"]["restock-fix"].update(reserved_by="crashed", reserved_instance="a-process-that-died",
                                               reserved_at=claimed_at)
    path.write_text(json.dumps(journal), encoding="utf-8")
    code_task_runtime().reset()
    result = step("apply", "workspace.write_file", arguments)
    after = _journal(task_id)
    if expected == "released":
        assert result.ok, result.response_text
        assert after["steps"]["crashed"]["status"] == "interrupted"
        assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == FIXED
    else:
        assert result.ok is False and result.status == "approval_in_flight"
        assert (stock_repo / "inventory.py").read_text(encoding="utf-8") == BUGGY


# ---------------------------------------------------------------------------
# Recorded legacy journals: forward migration and the rollback downgrade
# ---------------------------------------------------------------------------


LEGACY_SETS = ["v1_df49", "v2_e064"]


def _install_legacy(label: str, scenario: str, tmp_path: Path) -> tuple[dict, dict, Path]:
    """Recreate the recorded workspace bytes and install the recorded journal, changing ONLY its
    workspace_root to the recreated folder (the recorded folder was disposable)."""
    source = LEGACY / label / scenario
    recorded = json.loads((source / "journal.json").read_text(encoding="utf-8"))
    workspace = tmp_path / f"legacy-{label}-{scenario}"
    shutil.copytree(source / "workspace", workspace)
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "recorded")
    installed = {**recorded, "workspace_root": str(workspace.resolve())}
    target = Path(os.environ["VOOL_CODE_TASK_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{recorded['task_id']}.json").write_text(json.dumps(installed), encoding="utf-8")
    ctx = _ctx(workspace, recorded["session_id"])
    return recorded, ctx, workspace


@pytest.mark.parametrize("label", LEGACY_SETS)
def test_recorded_legacy_approvals_keep_their_recorded_base(label):
    from core.code_assistant.journal_schema import upgrade_payload

    with_hash = upgrade_payload(json.loads((LEGACY / label / "approved_with_hash" / "journal.json").read_text()))
    assert with_hash["schema_version"] == 3 and with_hash["schema_migrations"][-1]["from"] == int(label[1])
    p1 = with_hash["proposals"]["p1"]
    assert p1["base"]["math.js"]["source"] == "legacy_expected_hash" and not p1["invalidated"]
    without = upgrade_payload(json.loads((LEGACY / label / "approved_without_hash" / "journal.json").read_text()))
    derived = without["proposals"]["p1"]
    assert derived["base"]["math.js"]["source"] == "legacy_read" and not derived["invalidated"]
    assert derived["base"]["math.js"]["sha256"] == with_hash["proposals"]["p1"]["base"]["math.js"]["sha256"]
    completed = upgrade_payload(json.loads((LEGACY / label / "completed" / "journal.json").read_text()))
    assert completed["revision"] == 1 and completed["stage"] == "report"


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("label", LEGACY_SETS)
def test_a_migrated_approval_executes_only_over_its_recorded_base(stock_repo, tmp_path, label, changed):
    recorded, ctx, workspace = _install_legacy(label, "approved_without_hash", tmp_path)
    fixed = recorded["proposals"]["p1"]["arguments"]["content"]
    if changed:
        (workspace / "math.js").write_text("exports.add = (a, b) => a - b; // edited\n", encoding="utf-8")
    result = _door("code.task.step", {"task_id": recorded["task_id"], "step_id": "after-upgrade",
                                      "intent": "workspace.write_file",
                                      "arguments": {"path": "math.js", "content": fixed}}, ctx)
    if changed:
        assert result.status == "stale_base" and "edited" in (workspace / "math.js").read_text()
    else:
        assert result.ok, result.response_text
        assert (workspace / "math.js").read_text() == fixed
    migrated = _journal(recorded["task_id"])
    assert migrated["schema_version"] == 3 and migrated["journal_version"] >= 1


@pytest.mark.parametrize("label", LEGACY_SETS)
def test_downgrade_restores_the_recorded_shape_and_revokes_unexecuted_approvals(label):
    from core.code_assistant.journal_schema import downgrade_payload, upgrade_payload

    target = int(label[1])
    for scenario in ("approved_with_hash", "approved_without_hash", "completed", "two_pending_units"):
        recorded = json.loads((LEGACY / label / scenario / "journal.json").read_text())
        down, report = downgrade_payload(upgrade_payload(recorded), target)
        assert set(down) == set(recorded), (scenario, set(down) ^ set(recorded))
        for sid, step in recorded["steps"].items():
            assert set(down["steps"][sid]) == set(step), (scenario, sid)
        for pid, proposal in recorded["proposals"].items():
            assert set(down["proposals"][pid]) == set(proposal), (scenario, pid)
        unexecuted = sorted(pid for pid, p in recorded["proposals"].items() if p["approved"] and not p["consumed_by"])
        assert sorted(r["proposal_id"] for r in report["revoked_approvals"]) == unexecuted
        assert not any(down["proposals"][pid]["approved"] for pid in unexecuted)
        executed = [pid for pid, p in recorded["proposals"].items() if p["consumed_by"]]
        assert all(down["proposals"][pid]["approved"] for pid in executed)


def test_the_unread_sibling_legacy_approval_fails_closed(stock_repo, tmp_path):
    recorded, ctx, workspace = _install_legacy("v1_df49", "approved_unread_sibling", tmp_path)
    before = (workspace / "labels.js").read_text()
    approved_arguments = recorded["proposals"]["sibling"]["arguments"]
    result = _door("code.task.step", {"task_id": recorded["task_id"], "step_id": "after-upgrade",
                                      "intent": "workspace.write_file", "arguments": approved_arguments}, ctx)
    assert result.ok is False and result.status == "approval_requires_review", result.status
    assert (workspace / "labels.js").read_text() == before
    assert _journal(recorded["task_id"])["proposals"]["sibling"]["invalidated"]["reason"] == \
        "legacy_approval_without_reviewed_base"


# ---------------------------------------------------------------------------
# Migration reads the journal, never today's filesystem
# ---------------------------------------------------------------------------


def _link(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # a platform without symlinks cannot build this case
        pytest.skip(f"symlinks unavailable: {exc}")


def test_a_symlink_made_after_a_legacy_read_never_binds_an_unread_file(stock_repo, tmp_path):
    """The df49 runtime approved a write to `labels.js` although the task only ever read `math.js`. Today `math.js`
    is a link to `labels.js`, which holds the bytes that read recorded. Resolving recorded paths through today's
    filesystem hands the read to `labels.js`, and the approval overwrites a file nobody reviewed."""
    recorded, ctx, workspace = _install_legacy("v1_df49", "approved_unread_sibling", tmp_path)
    read_bytes = (workspace / "math.js").read_bytes()
    assert hashlib.sha256(read_bytes).hexdigest() == recorded["steps"]["read"]["result"]["hash"]
    (workspace / "labels.js").write_bytes(read_bytes)
    (workspace / "math.js").unlink()
    _link(workspace / "math.js", "labels.js")
    result = _door("code.task.step", {"task_id": recorded["task_id"], "step_id": "after-upgrade",
                                      "intent": "workspace.write_file",
                                      "arguments": recorded["proposals"]["sibling"]["arguments"]}, ctx)
    assert result.ok is False and result.status == "approval_requires_review", (result.status, result.response_text)
    assert (workspace / "labels.js").read_bytes() == read_bytes
    sibling = _journal(recorded["task_id"])["proposals"]["sibling"]
    assert sibling["invalidated"]["reason"] == "legacy_approval_without_reviewed_base" and sibling["base"] == {}, sibling


@pytest.mark.parametrize("scenario", ["approved_with_hash", "approved_without_hash"])
@pytest.mark.parametrize("label", LEGACY_SETS)
def test_a_legacy_approval_whose_path_resolves_elsewhere_today_is_withdrawn(stock_repo, tmp_path, label, scenario):
    """The recorded approval names `math.js`, reviewed as a regular file. Today `math.js` is a link to `other.js`,
    which holds the reviewed bytes, so executing the approval would write `other.js` -- a file its journal never
    names. Today's filesystem may withdraw a legacy approval; it never re-points one."""
    recorded, ctx, workspace = _install_legacy(label, scenario, tmp_path)
    reviewed = (workspace / "math.js").read_bytes()
    (workspace / "other.js").write_bytes(reviewed)
    (workspace / "math.js").unlink()
    _link(workspace / "math.js", "other.js")
    result = _door("code.task.step", {"task_id": recorded["task_id"], "step_id": "after-upgrade",
                                      "intent": "workspace.write_file",
                                      "arguments": recorded["proposals"]["p1"]["arguments"]}, ctx)
    assert result.ok is False and result.status == "approval_requires_review", (result.status, result.response_text)
    assert (workspace / "other.js").read_bytes() == reviewed and (workspace / "math.js").is_symlink()
    invalidated = _journal(recorded["task_id"])["proposals"]["p1"]["invalidated"]
    assert invalidated["reason"] == "legacy_destination_resolves_elsewhere", invalidated
    assert invalidated["path"] == "math.js" and invalidated["resolves_to"] == "other.js", invalidated


@pytest.mark.parametrize("path", ["math.js", "../math.js"], ids=["inside", "leaves-the-workspace"])
@pytest.mark.parametrize("label", LEGACY_SETS)
def test_a_recorded_journal_migrates_the_same_on_any_machine(stock_repo, tmp_path, label, path):
    """The recorded workspace does not exist on this machine; a recreated copy does. One recorded journal must
    migrate to the same targets, bases, snapshots and verdict against either, and a recorded path that leaves the
    workspace never binds. `inside` is the control."""
    from core.code_assistant.journal_schema import upgrade_payload

    recorded, _context, workspace = _install_legacy(label, "approved_without_hash", tmp_path)
    recorded = copy.deepcopy(recorded)
    recorded["proposals"]["p1"]["arguments"]["path"] = path
    elsewhere = upgrade_payload(recorded)
    here = upgrade_payload({**recorded, "workspace_root": str(workspace.resolve())})

    def verdict(data: dict) -> tuple:
        p1 = data["proposals"]["p1"]
        return p1["targets"], p1["base"], p1["invalidated"].get("reason", ""), data["snapshots"]

    assert verdict(elsewhere) == verdict(here), (verdict(elsewhere), verdict(here))
    if path.startswith(".."):
        assert verdict(here)[:3] == ([], {}, "legacy_approval_without_reviewed_base"), verdict(here)
    else:
        assert verdict(here)[0] == ["math.js"] and verdict(here)[2] == "", verdict(here)


def test_the_downgrade_cli_never_writes_in_place(tmp_path):
    from core.code_assistant.journal_schema import main, upgrade_payload

    source = tmp_path / "journals"
    source.mkdir()
    for scenario in ("approved_with_hash", "completed"):
        recorded = json.loads((LEGACY / "v2_e064" / scenario / "journal.json").read_text())
        (source / f"{recorded['task_id']}.json").write_text(json.dumps(upgrade_payload(recorded)))
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    dest = tmp_path / "downgraded"
    assert main(["downgrade", "--source", str(source), "--dest", str(dest), "--target-schema", "2"]) == 0
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    manifest = json.loads((dest / "JOURNAL_SCHEMA_MANIFEST.json").read_text())
    assert len(manifest["tasks"]) == 2
    assert main(["downgrade", "--source", str(source), "--dest", str(dest), "--target-schema", "2"]) == 2
