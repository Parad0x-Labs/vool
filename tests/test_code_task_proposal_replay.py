"""A recorded proposal id names exactly one request (coding revision 3, P2).

The independent review proved that proposing again under a recorded proposal_id with different
content reported a successful replay of the obsolete proposal: the caller was told its revision was
previewed while the journal -- and any approval of that id -- still held the old bytes. Here a retry
is compared with what the id recorded: the intent, the target paths, every other argument, an
explicit expected_hash, the repair unit, and the reviewed base the retry would bind now. An
identical retry replays idempotently and says what state the recorded proposal is in and what to do
next; any difference is a typed ``proposal_id_conflict`` that names what differs and a free
proposal_id for the revision, and leaves the recorded proposal untouched. A rationale is prose about
the change, not part of it, and may be reworded on a retry.

Novel data (an invoicing module) beside the review's JavaScript cases; recorded journals from both
older runtimes; restart, cancellation and a downgrade that revokes an approval. Real files and real
commands through the production door; no model is involved and none is claimed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEGACY = REPO / "tests" / "fixtures" / "code_task_journals"
INVOICE = "def line_total(quantity, unit_price):\n    return quantity + unit_price\n"
INVOICE_WRONG = "def line_total(quantity, unit_price):\n    return quantity - unit_price\n"
INVOICE_FIXED = "def line_total(quantity, unit_price):\n    return quantity * unit_price\n"
CHECK = "from invoice import line_total\nassert line_total(3, 4) == 12, line_total(3, 4)\nprint('invoice ok')\n"
CHECK_ALL = CHECK + "assert line_total(0, 9) == 0, line_total(0, 9)\nprint('all invoice checks ok')\n"
CURRENCY = "def cents(amount):\n    return int(amount * 100)\n"
NOTES = "finance notes: operator owned\n"
EDITED = INVOICE + "# rounding reviewed by finance\n"
RATIONALE = "Owner invoice.py: a line total is the quantity times the unit price."
WRONG = {"path": "invoice.py", "content": INVOICE_WRONG}
FIXED = {"path": "invoice.py", "content": INVOICE_FIXED}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def invoice_repo(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "invoicing"
    root.mkdir()
    for name, text in {"invoice.py": INVOICE, "check_invoice.py": CHECK, "check_all.py": CHECK_ALL,
                       "currency.py": CURRENCY, "FINANCE_NOTES.md": NOTES}.items():
        (root / name).write_text(text, encoding="utf-8")
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


class Task:
    def __init__(self, root: Path, session: str) -> None:
        self.root = root
        self.ctx = _ctx(root, session)
        self.id = _door("code.task.open", {"objective": "Repair the invoice line totals"}, self.ctx).details["task_id"]
        self.n = 0
        red = self.step("workspace.run_tests", {"command": "python3 check_invoice.py"})
        assert red.details["executed"] and red.details["tool_result"]["success"] is False, red.details
        assert _door("code.task.identify", {"task_id": self.id, "path": "invoice.py", "line": 2,
                                            "reason": "line totals add the unit price"}, self.ctx).ok
        assert self.step("workspace.read_file", {"path": "invoice.py"}).ok
        assert self.step("workspace.read_file", {"path": "currency.py"}).ok

    def step(self, intent: str, arguments: dict):
        self.n += 1
        return _door("code.task.step", {"task_id": self.id, "step_id": f"s{self.n}", "intent": intent,
                                        "arguments": arguments}, self.ctx)

    def propose(self, pid: str = "totals", *, intent: str = "workspace.write_file", arguments: dict | None = None,
                unit: str = "", rationale: str = RATIONALE):
        request = {"task_id": self.id, "proposal_id": pid, "intent": intent,
                   "arguments": dict(WRONG if arguments is None else arguments), "rationale": rationale}
        if unit:
            request["unit"] = unit
        return _door("code.task.propose", request, self.ctx)

    def approve(self, pid: str = "totals"):
        return _door("code.task.approve", {"task_id": self.id, "proposal_id": pid}, self.ctx)

    def recorded(self, pid: str = "totals") -> dict:
        return _journal(self.id)["proposals"][pid]


def _land_wrong_and_reopen_review(task: Task, arguments: dict) -> None:
    """The recorded repair runs, its focused check fails, and review reopens -- where proposing is lawful."""
    assert task.approve().ok
    assert task.step("workspace.write_file", arguments).ok
    failed = task.step("workspace.run_tests", {"command": "python3 check_invoice.py"})
    assert failed.details["tool_result"]["success"] is False
    assert _door("code.task.identify", {"task_id": task.id, "path": "invoice.py", "line": 2,
                                        "reason": "the first repair subtracts the unit price"}, task.ctx).ok


# ---------------------------------------------------------------------------
# An identical retry is idempotent in every state and says what to do next
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hash_mode", ["omitted", "explicit", "added-on-retry"])
@pytest.mark.parametrize("state", ["pending_approval", "approved", "consumed"])
def test_an_identical_retry_replays_the_recorded_proposal_and_names_the_next_action(invoice_repo, state, hash_mode):
    task = Task(invoice_repo, f"replay-{state}-{hash_mode}")
    recorded_arguments = {**WRONG, **({"expected_hash": _sha(INVOICE)} if hash_mode == "explicit" else {})}
    first = task.propose(arguments=recorded_arguments)
    assert first.ok, first.response_text
    if state == "approved":
        assert task.approve().ok
    if state == "consumed":
        _land_wrong_and_reopen_review(task, recorded_arguments)
    before = task.recorded()
    retry_arguments = {**WRONG, **({"expected_hash": _sha(INVOICE)} if hash_mode != "omitted" else {})}
    retry = task.propose(arguments=retry_arguments, rationale="Retrying the same repair. " + RATIONALE)
    assert retry.ok, (retry.status, retry.response_text)
    assert retry.details["replayed"] is True and retry.details["proposal_state"] == state, retry.details
    assert retry.details["preview"] == first.details["preview"]
    next_action = {"pending_approval": "code.task.approve", "approved": "code.task.step", "consumed": "already ran"}[state]
    assert next_action in retry.response_text, retry.response_text
    assert task.recorded() == before  # a replay changes nothing it recorded


# ---------------------------------------------------------------------------
# The failure class: a changed request under a recorded id is a typed conflict
# ---------------------------------------------------------------------------

CHANGES = {
    # change: (retry intent, retry arguments, retry unit, lawful next arguments)
    "content": ("workspace.write_file", FIXED, "", FIXED),
    "path": ("workspace.write_file", {"path": "currency.py", "content": INVOICE_WRONG}, "",
             {"path": "currency.py", "content": INVOICE_WRONG}),
    "intent": ("workspace.replace_in_file",
               {"path": "invoice.py", "old_text": "quantity + unit_price", "new_text": "quantity - unit_price"}, "",
               {"path": "invoice.py", "old_text": "quantity + unit_price", "new_text": "quantity - unit_price"}),
    "expected_hash": ("workspace.write_file", {**WRONG, "expected_hash": _sha(EDITED)}, "",
                      {**WRONG, "expected_hash": _sha(INVOICE)}),
    "unit": ("workspace.write_file", WRONG, "invoice-rework", WRONG),
    "base": ("workspace.write_file", WRONG, "", WRONG),
}


@pytest.mark.parametrize("recorded_state", ["pending_approval", "approved"])
@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_changed_request_under_a_recorded_id_is_a_typed_conflict(invoice_repo, change, recorded_state):
    task = Task(invoice_repo, f"conflict-{change}-{recorded_state}")
    assert task.propose(arguments=WRONG).ok
    if recorded_state == "approved":
        assert task.approve().ok
    if change == "base":
        # Someone edits the file and the task reads the edit: the recorded base is gone.
        (invoice_repo / "invoice.py").write_text(EDITED, encoding="utf-8")
        assert task.step("workspace.read_file", {"path": "invoice.py"}).ok
    before = task.recorded()
    intent, arguments, unit, next_arguments = CHANGES[change]
    out = task.propose(intent=intent, arguments=arguments, unit=unit)
    assert out.ok is False and out.status == "proposal_id_conflict", (out.status, out.response_text)
    assert change in out.details["differences"], out.details["differences"]
    suggested = out.details["suggested_proposal_id"]
    journal = _journal(task.id)
    assert suggested != "totals" and suggested not in journal["proposals"], suggested
    assert f"`{suggested}`" in out.response_text and "code.task.propose" in out.response_text, out.response_text
    assert task.recorded() == before and sorted(journal["proposals"]) == ["totals"]
    lawful = task.propose(suggested, intent=intent, arguments=next_arguments, unit=unit)
    assert lawful.ok, (lawful.status, lawful.response_text)


def test_the_suggested_proposal_id_is_never_one_already_recorded(invoice_repo):
    task = Task(invoice_repo, "suggested-id")
    assert task.propose(arguments=WRONG).ok
    assert task.propose("totals-2", arguments=FIXED).ok
    first = task.propose(arguments={"path": "invoice.py", "content": INVOICE_FIXED + "# third try\n"})
    assert first.status == "proposal_id_conflict" and first.details["suggested_proposal_id"] == "totals-3", first.details
    second = task.propose("totals-2", arguments=WRONG)
    assert second.status == "proposal_id_conflict" and second.details["suggested_proposal_id"] == "totals-3", second.details
    assert sorted(_journal(task.id)["proposals"]) == ["totals", "totals-2"]


def test_revised_bytes_never_ride_the_recorded_approval(invoice_repo):
    task = Task(invoice_repo, "no-riding")
    assert task.propose(arguments=WRONG).ok
    assert task.approve().ok
    assert task.propose(arguments=FIXED).status == "proposal_id_conflict"
    riding = task.step("workspace.write_file", FIXED)
    assert riding.ok is False, riding.status
    assert (invoice_repo / "invoice.py").read_text(encoding="utf-8") == INVOICE
    assert task.step("workspace.write_file", WRONG).ok  # the approval still covers exactly what it recorded
    assert (invoice_repo / "invoice.py").read_text(encoding="utf-8") == INVOICE_WRONG


def test_an_identical_retry_of_an_invalidated_proposal_names_the_recovery(invoice_repo):
    task = Task(invoice_repo, "invalidated-retry")
    recorded_arguments = {**WRONG, "expected_hash": _sha(INVOICE)}
    assert task.propose(arguments=recorded_arguments).ok
    assert task.approve().ok
    (invoice_repo / "invoice.py").write_text(EDITED, encoding="utf-8")
    assert task.step("workspace.read_file", {"path": "invoice.py"}).ok
    before = task.recorded()
    assert before["invalidated"]
    retry = task.propose(arguments=recorded_arguments)
    assert retry.ok is False and retry.details["proposal_state"] == "invalidated", (retry.status, retry.details)
    assert "new proposal_id" in retry.response_text, retry.response_text
    assert task.recorded() == before
    assert task.propose("totals-edited", arguments={**WRONG, "expected_hash": _sha(EDITED)}).ok


def test_a_failed_repair_revised_under_its_consumed_id_recovers_under_a_new_id(invoice_repo):
    task = Task(invoice_repo, "consumed-revision")
    assert task.propose(arguments=WRONG).ok
    _land_wrong_and_reopen_review(task, WRONG)
    assert task.step("workspace.read_file", {"path": "invoice.py"}).ok
    conflict = task.propose(arguments=FIXED)
    assert conflict.status == "proposal_id_conflict" and conflict.details["proposal_state"] == "consumed"
    assert conflict.details["differences"] == ["content"], conflict.details["differences"]
    suggested = conflict.details["suggested_proposal_id"]
    assert task.propose(suggested, arguments=FIXED).ok
    assert task.approve(suggested).ok
    assert task.step("workspace.write_file", FIXED).ok
    assert task.step("workspace.run_tests", {"command": "python3 check_invoice.py"}).details["tool_result"]["success"]
    # The wider entry point still runs as regression evidence; its DECLARED scope does not name
    # the obligation's own selector (a sibling file), so it stays evidence and the retained
    # obligation holds the stage until a covering check runs.
    assert task.step("workspace.run_tests", {"command": "python3 check_all.py"}).details["tool_result"]["success"]
    covering = task.step("workspace.run_tests", {"command": "python3 check_invoice.py"})
    assert covering.details["tool_result"]["success"] and covering.details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report.get("missing")
    journal = _journal(task.id)
    assert journal["proposals"]["totals"]["arguments"]["content"] == INVOICE_WRONG
    assert journal["proposals"][suggested]["arguments"]["content"] == INVOICE_FIXED
    assert journal["proposals"]["totals"]["consumed_by"] and journal["proposals"][suggested]["consumed_by"]
    assert (invoice_repo / "invoice.py").read_text(encoding="utf-8") == INVOICE_FIXED
    assert (invoice_repo / "FINANCE_NOTES.md").read_text(encoding="utf-8") == NOTES


# ---------------------------------------------------------------------------
# Preservation: restart, cancellation, a revoked approval, recorded journals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retry", ["identical", "changed"])
@pytest.mark.parametrize("event", ["restart", "cancel"])
def test_restart_and_cancellation_keep_the_recorded_request(invoice_repo, event, retry):
    from core.code_assistant.task_runtime import code_task_runtime

    task = Task(invoice_repo, f"{event}-{retry}")
    assert task.propose(arguments=WRONG).ok
    assert task.approve().ok
    before = task.recorded()
    if event == "restart":
        code_task_runtime().reset()
    else:
        assert _door("code.task.cancel", {"task_id": task.id, "reason": "operator stopped the repair"}, task.ctx).ok
    out = task.propose(arguments=WRONG if retry == "identical" else FIXED)
    if event == "cancel":
        assert out.ok is False and out.status == "cancelled", out.status
    elif retry == "identical":
        assert out.ok and out.details["proposal_state"] == "approved", (out.status, out.response_text)
    else:
        assert out.status == "proposal_id_conflict" and out.details["differences"] == ["content"], out.details
    assert task.recorded() == before


def test_an_approval_revoked_by_a_downgrade_replays_as_waiting_for_approval(invoice_repo):
    from core.code_assistant.journal_schema import downgrade_payload
    from core.code_assistant.task_runtime import code_task_runtime

    task = Task(invoice_repo, "revoked")
    assert task.propose(arguments=WRONG).ok
    assert task.approve().ok
    path = Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task.id}.json"
    downgraded, report = downgrade_payload(json.loads(path.read_text(encoding="utf-8")), 2)
    assert [row["proposal_id"] for row in report["revoked_approvals"]] == ["totals"]
    path.write_text(json.dumps(downgraded), encoding="utf-8")
    code_task_runtime().reset()
    same = task.propose(arguments=WRONG)
    assert same.ok and same.details["proposal_state"] == "pending_approval", (same.status, same.response_text)
    changed = task.propose(arguments=FIXED)
    assert changed.status == "proposal_id_conflict" and "content" in changed.details["differences"]
    refused = task.step("workspace.write_file", WRONG)  # the revoked approval must be given again first
    assert refused.ok is False and (invoice_repo / "invoice.py").read_text(encoding="utf-8") == INVOICE


def _install_legacy(label: str, scenario: str, tmp_path: Path) -> tuple[dict, dict, Path]:
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
    return recorded, _ctx(workspace, recorded["session_id"]), workspace


@pytest.mark.parametrize("label", ["v1_df49", "v2_e064"])
def test_a_recorded_legacy_proposal_is_compared_with_what_it_recorded(invoice_repo, tmp_path, label):
    recorded, ctx, _workspace = _install_legacy(label, "approved_with_hash", tmp_path)
    p1 = recorded["proposals"]["p1"]
    request = {"task_id": recorded["task_id"], "proposal_id": "p1", "intent": p1["intent"],
               "rationale": p1.get("rationale") or "Owner math.js: add the operands."}
    same = _door("code.task.propose", {**request, "arguments": p1["arguments"]}, ctx)
    assert same.ok and same.details["replayed"] and same.details["proposal_state"] == "approved", \
        (same.status, same.response_text)
    changed_arguments = {**p1["arguments"], "content": p1["arguments"]["content"] + "// revised\n"}
    changed = _door("code.task.propose", {**request, "arguments": changed_arguments}, ctx)
    assert changed.status == "proposal_id_conflict" and changed.details["differences"] == ["content"], changed.details
    assert _journal(recorded["task_id"])["proposals"]["p1"]["arguments"] == p1["arguments"]
