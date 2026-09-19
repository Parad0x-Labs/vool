"""Served purposeful-verification journeys (mission 2026-09-15): real daemon, real
``/api/chat``, production approvals, disposable Git fixtures, real filesystem and command
effects. The MODEL IS SCRIPTED (one planned tool call per turn) and says so: nothing here is a
live-autonomy claim.

What these journeys prove on the served door, beside the in-process pack
(``test_code_task_purposeful_verification.py``):

* ORIGINAL CLASS, WITH THE LOOP: a wrong first fix fails its focused check, the model then
  re-issues the SAME broad check twice while the defect is unresolved, and each repeat is
  answered by a bounded ``repeated_verification`` correction carrying the prior outcome and the
  recovery path -- never by another identical campaign. The model then re-diagnoses, lands a
  revised APPROVED repair, verifies the repaired behavior and the prior scope, and the report
  completes honestly.
* NOVEL: a different repository layout and two dependent defects. Fixing the second discards
  nothing: landing it retires the first fix's green evidence at that revision and the task
  verifies BOTH again. A daemon RESTART between the failed cumulative check and the resumed
  repair proves the journal -- not a process -- is the authority.
* UNCOOPERATIVE: a model that only ever re-issues the failed check reaches a bounded, honest,
  unresolved outcome; no unauthorized mutation happens and no verification is invented.

Every assertion reads the task journal, the disk or the provider's recorded calls; a refusal is
proven by the absence of an effect plus the journal's typed status, never by reply prose.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import iniconfig
import packaging
import pytest

from tests._blackbox_served_rig import (
    REPO_ROOT,
    SEED_MANIFEST,
    ScriptedProvider,
    ServedDaemon,
    _chat_with_operator,
    run_in_home,
)
from tests.test_code_assistant_served_journey import _call

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
BUGGY = "def add(a, b):\n    return a - b\n"
WRONG = "def add(a, b):\n    return a * b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
FOCUSED = "python3 -m pytest -q test_calc.py"


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original(self)

    handler.do_GET = do_GET


RATES_BUGGY = "def rate(units):\n    return units * 2\n"
RATES_FIXED = "def rate(units):\n    return units * 3\n"
INVOICE_BUGGY = "from shapes.rates import rate\n\n\ndef invoice(units):\n    return 'total ' + str(rate(units))\n"
INVOICE_FIXED = "from shapes.rates import rate\n\n\ndef invoice(units):\n    return 'TOTAL ' + str(rate(units))\n"
CHECK_RATES = (
    "import sys\nsys.path.insert(0, '.')\n"
    "from shapes.rates import rate\nassert rate(4) == 12, rate(4)\nprint('rates ok')\n"
)
CHECK_ALL = (
    "import sys\nsys.path.insert(0, '.')\n"
    "from shapes.rates import rate\nfrom shapes.invoice import invoice\n"
    "assert rate(4) == 12, rate(4)\nassert invoice(4) == 'TOTAL 12', invoice(4)\nprint('all ok')\n"
)
NOVEL_FILES = {
    "README.md": "# billing pack: operator owned; the assistant repairs only what the checks name\n",
    "shapes/__init__.py": "",
    "shapes/rates.py": RATES_BUGGY,
    "shapes/invoice.py": INVOICE_BUGGY,
    "checks/check_rates.py": CHECK_RATES,
    "checks/check_all.py": CHECK_ALL,
}


def _boot_rig(tmp_path: Path, index: str, workspace_files: dict[str, str]):
    """A disposable served rig: daemon + scripted provider, one chat session's worth of home."""
    home = tmp_path / f"home-{index}"
    workspace = tmp_path / f"repo-{index}"
    store_dir = tmp_path / f"store-{index}"
    workspace.mkdir(parents=True)
    for rel, content in workspace_files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # Harness repair (Goal 2, 2026-09-18): the served r3/r4 invocation suites issue real
    # `python3 -m pytest` commands, and the workspace sandbox on THIS machine cannot import
    # pytest from either interpreter's site-packages (the shared runtime delegates site-packages
    # to a root outside the sandbox read set; the system python3's user site is likewise
    # unreadable). Seeding pytest and its pure-Python deps INTO the disposable workspace keeps
    # the commands, the argv-identity machinery and the real pytest execution unchanged -- `cwd`
    # is sys.path[0] for `-m` -- instead of skipping or weakening the pytest-family verifications.
    import shutil
    # Seed from the interpreter-neutral user-site pytest (8.x) when present: the suites issue
    # commands that resolve BOTH to this runtime's python (3.12) and the system python3 (3.9,
    # for option-prefixed forms like `-O -m pytest`), and pytest 9 is 3.10+ only.
    seed_root = Path.home() / "Library/Python/3.9/lib/python/site-packages"
    if not (seed_root / "_pytest").is_dir():
        import _pytest
        import pluggy
        import py  # a single-module dependency (py.py), not a package
        import pygments
        for module in (pytest, _pytest, pluggy, iniconfig, packaging, pygments):
            src = Path(module.__file__).parent
            shutil.copytree(src, workspace / src.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (workspace / "py.py").write_text(Path(py.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    else:
        for name in ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments",
                     "exceptiongroup"):
            src = seed_root / name
            if src.is_dir():
                shutil.copytree(src, workspace / name, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(seed_root / "py.py", workspace / "py.py")  # pytest 8 still imports py
        shutil.copyfile(seed_root / "typing_extensions.py", workspace / "typing_extensions.py")
    # NOTE (Goal 2 harness repair): the seeded set lives at the workspace root. A verification
    # that runs pytest from a DIFFERENT cwd (the decoy cases) needs the set beside that cwd too --
    # the sandbox sanitizes PYTHONPATH and `-m` resolves imports from the process cwd only. Suites
    # with such cases call `_seed_pytest_dir` for those directories.
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "seed")
    provider = ScriptedProvider({MODEL: "no script yet"})
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_CODE_TASK_DIR": str(store_dir / "code_tasks"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    _stub_models_are_resident(provider)
    provider.__enter__()
    try:
        daemon.start(timeout=240)
    except Exception as exc:  # pragma: no cover - environment, not the runtime
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
    provider.reset()
    return {"home": home, "workspace": workspace, "store_dir": store_dir,
            "daemon": daemon, "provider": provider}


def _seed_pytest_dir(rig: dict[str, Any], relative: str) -> None:
    """Copy the sandbox-importable pytest set into another directory of the rig's workspace.

    For verifications whose command runs with ``cwd`` under a subdirectory: ``-m pytest`` resolves
    imports from that cwd, the sandbox sanitizes PYTHONPATH, and site-packages roots are outside
    its read set (see _boot_rig's seeding note)."""
    import shutil as _shutil
    from pathlib import Path as _Path
    home = _Path(__file__).resolve().parent.parent
    seed_root = _Path.home() / "Library/Python/3.9/lib/python/site-packages"
    names = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "exceptiongroup")
    target = _Path(rig["workspace"]) / relative
    target.mkdir(parents=True, exist_ok=True)
    if (seed_root / "_pytest").is_dir():
        for name in names:
            src = seed_root / name
            if src.is_dir():
                _shutil.copytree(src, target / name, dirs_exist_ok=True,
                                 ignore=_shutil.ignore_patterns("__pycache__", "*.pyc"))
        _shutil.copyfile(seed_root / "py.py", target / "py.py")
        _shutil.copyfile(seed_root / "typing_extensions.py", target / "typing_extensions.py")
    else:
        import _pytest as __pytest_impl
        import iniconfig as __iniconfig
        import packaging as __packaging
        import pluggy as __pluggy
        import py as __py
        import pygments as __pygments
        import pytest as __pytest
        for module in (__pytest, __pytest_impl, __pluggy, __iniconfig, __packaging, __pygments):
            src = _Path(module.__file__).parent
            _shutil.copytree(src, target / src.name, dirs_exist_ok=True,
                             ignore=_shutil.ignore_patterns("__pycache__", "*.pyc"))
        _shutil.copyfile(_Path(__py.__file__), target / "py.py")


def _restart_daemon(rig: dict[str, Any]) -> None:
    """Stop the daemon and start a FRESH process on the same home, store and workspace: the
    journal on disk, not any process memory, must carry the open task across the restart."""
    old_port = rig["daemon"].port
    rig["daemon"].stop()
    daemon = ServedDaemon(rig["home"], env_extra=rig["daemon"].env_extra)
    assert daemon.port != old_port
    daemon.start(timeout=240)
    rig["daemon"] = daemon


def _task(store_dir: Path, task_id: str) -> dict[str, Any]:
    return json.loads((store_dir / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


def _only_task_id(store_dir: Path) -> str:
    files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
    assert len(files) == 1, files
    return files[0].stem


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


class TurnDrive:
    """One scripted model call per operator turn, through the real chat door with the
    production operator Allow for anything the runtime pauses on."""

    def __init__(self, rig: dict[str, Any], session: str) -> None:
        self.rig = rig
        self.session = session

    def turn(self, text: str, call: dict[str, Any]) -> dict[str, Any]:
        self.rig["provider"].table[MODEL] = call
        reply = _chat_with_operator(self.rig["daemon"], self.rig["home"], self.session, text, model=MODEL)
        self.settle()
        return reply

    def settle(self) -> None:
        """Drain approvals a turn paused on but its bounded rounds did not resume: resolve each
        through the production /api/mode door and resume, until the session holds none."""
        from urllib.request import Request as _Request
        from urllib.request import urlopen as _urlopen

        from tests._blackbox_served_rig import pending_session_approvals

        for _round in range(4):
            entries = pending_session_approvals(self.rig["home"], self.session)
            if not entries:
                return
            token = ""
            for entry in entries:
                token = str(entry["approval_id"])
                request = _Request(
                    f"{self.rig['daemon'].base_url}/api/mode",
                    data=json.dumps({"op": "resolve_approval", "session_id": self.session,
                                     "approval_id": str(entry["approval_id"]), "decision": "allow"}).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with _urlopen(request, timeout=30) as response:
                    assert json.loads(response.read().decode()).get("ok") is True
            self.rig["provider"].table[MODEL] = _call(
                "respond__direct", {"message": "continue the approved repair."}, f"cont-{_round}")
            self.rig["daemon"].chat("continue the approved repair", session_id=self.session,
                                    model=MODEL, mode="auto", approval_token=token, timeout=900.0)

    def step(self, task_id: str, step_id: str, intent: str, arguments: dict[str, Any],
             text: str = "continue the task", **outer: Any) -> dict[str, Any]:
        payload = {"task_id": task_id, "step_id": step_id, "intent": intent, "arguments": arguments, **outer}
        self.turn(text, _call("code__task__step", payload, f"c-{step_id}"))
        return _task(self.rig["store_dir"], task_id)


# ---------------------------------------------------------------------------
# The original class, with the repeated-verification loop the runtime must interrupt
# ---------------------------------------------------------------------------


def test_served_repeated_checks_are_corrected_and_the_recovery_completes(tmp_path: Path) -> None:
    rig = _boot_rig(tmp_path, "repeat", {"calc.py": BUGGY, "test_calc.py": TEST})
    try:
        drive = TurnDrive(rig, "served-repeat")
        drive.turn("Fix the failing test in this project and explain the change.",
                   _call("code__task__open", {"objective": "repair calc.add so its test passes"}, "open"))
        task_id = _only_task_id(rig["store_dir"])

        task = drive.step(task_id, "repro", "workspace.run_tests", {"command": FOCUSED}, "reproduce the failure")
        assert task["stage"] == "identify" and task["steps"]["repro"]["result"]["success"] is False
        drive.turn("identify it", _call("code__task__identify",
                                        {"task_id": task_id, "path": "calc.py", "line": 2,
                                         "reason": "add subtracts where the test requires the sum"}, "id"))
        drive.step(task_id, "read", "workspace.read_file", {"path": "calc.py"}, "read the owner")
        # A competent-but-wrong first fix, approved and landed through the production door.
        drive.turn("propose", _call("code__task__propose",
                                    {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                     "arguments": {"path": "calc.py", "content": WRONG, "expected_hash": _sha(BUGGY)},
                                     "rationale": "Owner calc.py: add combines its operands wrongly."}, "prop"))
        drive.turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "w"}, "appr"))
        task = drive.step(task_id, "mut-w", "workspace.write_file",
                          {"path": "calc.py", "content": WRONG, "expected_hash": _sha(BUGGY)}, "apply it")
        assert task["stage"] == "narrow_test" and task["revision"] == 1

        task = drive.step(task_id, "check-1", "workspace.run_tests", {"command": FOCUSED}, "run the focused check")
        assert task["narrow"]["success"] is False and task["stage"] == "narrow_test"

        # The loop: the model re-issues the IDENTICAL broad check twice while the defect is
        # unresolved. Each repeat must be a bounded correction, not another campaign.
        for attempt in (2, 3):
            task = drive.step(task_id, f"check-{attempt}", "workspace.run_tests", {"command": FOCUSED},
                              "run the whole suite again to be sure")
            record = task["steps"][f"check-{attempt}"]
            assert record["status"] == "repeated_verification", record
            assert record["executed"] is False and record["ok"] is False
        assert len(task["verifications"]) == 1, task["verifications"]
        assert task["stage"] == "narrow_test" and task["narrow"]["success"] is False

        # The recovery the correction names: re-diagnose, revise, fresh approval, land.
        drive.turn("re-diagnose", _call("code__task__identify",
                                        {"task_id": task_id, "path": "calc.py", "line": 2,
                                         "reason": "the first fix multiplied; the test requires the sum"}, "reid"))
        drive.step(task_id, "reread", "workspace.read_file", {"path": "calc.py"}, "read the owner again")
        drive.turn("propose the corrected repair", _call("code__task__propose",
                                                         {"task_id": task_id, "proposal_id": "f", "intent": "workspace.write_file",
                                                          "arguments": {"path": "calc.py", "content": FIXED,
                                                                        "expected_hash": _sha(WRONG)},
                                                          "rationale": "Owner calc.py: the test requires addition."}, "prop2"))
        drive.turn("approve it", _call("code__task__approve", {"task_id": task_id, "proposal_id": "f"}, "appr2"))
        task = drive.step(task_id, "mut-f", "workspace.write_file",
                          {"path": "calc.py", "content": FIXED, "expected_hash": _sha(WRONG)}, "apply the repair")
        assert task["revision"] == 2 and task["stage"] == "narrow_test"
        # Landing the corrected bytes retired the first fix's evidence: nothing current survived.
        assert task["narrow"] is None and task["cumulative"] is None

        task = drive.step(task_id, "narrow", "workspace.run_tests", {"command": FOCUSED},
                          "run the focused test through the task now")
        assert task["stage"] == "cumulative" and task["narrow"]["success"] is True
        task = drive.step(task_id, "cumulative", "workspace.run_tests", {"command": FOCUSED}, "run the full test command now")
        assert task["stage"] == "inspect_diff" and task["cumulative"]["success"] is True
        task = drive.step(task_id, "diff", "workspace.git_diff", {}, "show the diff")
        assert task["stage"] == "report"
        reply = drive.turn("report", _call("code__task__report", {"task_id": task_id}, "rep"))
        assert "completed." in _reply_text(reply), _reply_text(reply)[:400]

        task = _task(rig["store_dir"], task_id)
        assert [(v["stage"], v["success"], v["revision"]) for v in task["verifications"]] == [
            ("narrow_test", False, 1), ("narrow_test", True, 2), ("cumulative", True, 2)]
        assert (rig["workspace"] / "calc.py").read_text(encoding="utf-8") == FIXED
        assert (rig["workspace"] / "test_calc.py").read_text(encoding="utf-8") == TEST
        assert task["git_diff_paths"] == ["calc.py"]
    finally:
        rig["daemon"].stop()
        rig["provider"].__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Novel: dependent defects in a different layout, with a restart mid-repair
# ---------------------------------------------------------------------------


def test_served_dependent_defects_restart_and_retained_validation(tmp_path: Path) -> None:
    rig = _boot_rig(tmp_path, "dependent", NOVEL_FILES)
    try:
        drive = TurnDrive(rig, "served-dependent")
        drive.turn("Fix the failing test in this project and explain the change.",
                   _call("code__task__open", {"objective": "repair the billing pack so both checks pass"}, "open"))
        task_id = _only_task_id(rig["store_dir"])

        task = drive.step(task_id, "repro", "workspace.run_tests", {"command": "python3 checks/check_all.py"},
                          "reproduce the failure")
        assert task["stage"] == "identify" and task["steps"]["repro"]["result"]["success"] is False
        drive.turn("identify", _call("code__task__identify",
                                     {"task_id": task_id, "path": "shapes/rates.py", "line": 2,
                                      "reason": "rate doubles where the checks require tripling"}, "id"))
        drive.step(task_id, "read-1", "workspace.read_file", {"path": "shapes/rates.py"}, "read the owner")
        drive.step(task_id, "read-2", "workspace.read_file", {"path": "shapes/invoice.py"}, "read its only caller")
        drive.turn("propose", _call("code__task__propose",
                                    {"task_id": task_id, "proposal_id": "r", "intent": "workspace.write_file",
                                     "arguments": {"path": "shapes/rates.py", "content": RATES_FIXED,
                                                   "expected_hash": _sha(RATES_BUGGY)},
                                     "rationale": "Owner shapes/rates.py: rate doubles; the checks require triple."}, "prop"))
        drive.turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "r"}, "appr"))
        task = drive.step(task_id, "mut-r", "workspace.write_file",
                          {"path": "shapes/rates.py", "content": RATES_FIXED, "expected_hash": _sha(RATES_BUGGY)},
                          "apply it")
        assert task["stage"] == "narrow_test" and task["revision"] == 1

        task = drive.step(task_id, "narrow", "workspace.run_tests", {"command": "python3 checks/check_rates.py"},
                          "run the rates check command now")
        assert task["stage"] == "cumulative" and task["narrow"]["success"] is True
        # The full pack exposes the SECOND, dependent defect: invoice formats wrong while rate
        # itself is fixed. The failed cumulative reopens review honestly.
        task = drive.step(task_id, "cumulative", "workspace.run_tests", {"command": "python3 checks/check_all.py"},
                          "run the full pack test command")
        assert task["cumulative"]["success"] is False and task["stage"] == "cumulative"
        assert task["verifications"][-1]["obligation_match"] is True

        # RESTART between the failed verification and the resumed repair: the journal on disk
        # must carry the open task, its failed outcome and its approvals into the new process.
        journal_before = _task(rig["store_dir"], task_id)
        _restart_daemon(rig)
        assert _task(rig["store_dir"], task_id) == journal_before

        drive.turn("re-diagnose the second defect", _call("code__task__identify",
                                                          {"task_id": task_id, "path": "shapes/invoice.py", "line": 4,
                                                           "reason": "invoice labels the total lowercase where the pack requires TOTAL"},
                                                          "reid"))
        drive.step(task_id, "reread", "workspace.read_file", {"path": "shapes/invoice.py"}, "read the caller again through the task")
        drive.turn("propose the second repair", _call("code__task__propose",
                                                      {"task_id": task_id, "proposal_id": "i", "intent": "workspace.write_file",
                                                       "arguments": {"path": "shapes/invoice.py", "content": INVOICE_FIXED,
                                                                     "expected_hash": _sha(INVOICE_BUGGY)},
                                                       "rationale": "Owner shapes/invoice.py: the pack requires the TOTAL label."},
                                                      "prop2"))
        drive.turn("approve it", _call("code__task__approve", {"task_id": task_id, "proposal_id": "i"}, "appr2"))
        task = drive.step(task_id, "mut-i", "workspace.write_file",
                          {"path": "shapes/invoice.py", "content": INVOICE_FIXED, "expected_hash": _sha(INVOICE_BUGGY)},
                          "apply the second repair")
        # The second fix discarded NOTHING: landing it retired the first fix's green evidence at
        # this revision, and the task demands fresh focused AND full checks of the combined bytes.
        assert task["revision"] == 2 and task["stage"] == "narrow_test"
        assert task["narrow"] is None and task["cumulative"] is None

        task = drive.step(task_id, "narrow-2", "workspace.run_tests", {"command": "python3 checks/check_all.py"},
                          "run the full pack check command now")
        assert task["narrow"]["success"] is True and task["stage"] == "cumulative"
        task = drive.step(task_id, "cumulative-2", "workspace.run_tests", {"command": "python3 checks/check_all.py"},
                          "run the full pack test command again")
        assert task["cumulative"]["success"] is True and task["stage"] == "inspect_diff"
        drive.step(task_id, "diff", "workspace.git_diff", {}, "show the diff")
        reply = drive.turn("report", _call("code__task__report", {"task_id": task_id}, "rep"))
        assert "completed." in _reply_text(reply), _reply_text(reply)[:400]

        task = _task(rig["store_dir"], task_id)
        assert [(v["stage"], v["success"], v["revision"]) for v in task["verifications"]] == [
            ("narrow_test", True, 1), ("cumulative", False, 1),
            ("narrow_test", True, 2), ("cumulative", True, 2)]
        assert (rig["workspace"] / "shapes" / "rates.py").read_text(encoding="utf-8") == RATES_FIXED
        assert (rig["workspace"] / "shapes" / "invoice.py").read_text(encoding="utf-8") == INVOICE_FIXED
        assert (rig["workspace"] / "README.md").read_text(encoding="utf-8") == NOVEL_FILES["README.md"]
        assert sorted(task["git_diff_paths"]) == ["shapes/invoice.py", "shapes/rates.py"]
        # The final cumulative at revision 2 covered the FIRST repair's behavior too: the pack's
        # rates assertion ran green in the same command that validated the invoice repair.
        # The bytes binding covers both repaired files AND the check input the command named.
        assert task["cumulative"]["bytes"].keys() == {"shapes/invoice.py", "shapes/rates.py", "checks/check_all.py"}
    finally:
        rig["daemon"].stop()
        rig["provider"].__exit__(None, None, None)


# ---------------------------------------------------------------------------
# The uncooperative model: only ever re-issues the failed check
# ---------------------------------------------------------------------------


def test_served_uncooperative_repeated_checker_ends_bounded_and_honest(tmp_path: Path) -> None:
    rig = _boot_rig(tmp_path, "uncooperative", {"calc.py": BUGGY, "test_calc.py": TEST})
    try:
        drive = TurnDrive(rig, "served-stuck")
        drive.turn("Fix the failing test.", _call("code__task__open",
                                                  {"objective": "repair calc.add so its test passes"}, "open"))
        task_id = _only_task_id(rig["store_dir"])
        drive.step(task_id, "repro", "workspace.run_tests", {"command": FOCUSED}, "reproduce the failure")
        drive.turn("identify", _call("code__task__identify",
                                     {"task_id": task_id, "path": "calc.py", "line": 2,
                                      "reason": "add subtracts"}, "id"))
        drive.step(task_id, "read", "workspace.read_file", {"path": "calc.py"}, "read the owner")
        drive.turn("propose", _call("code__task__propose",
                                    {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                     "arguments": {"path": "calc.py", "content": WRONG, "expected_hash": _sha(BUGGY)},
                                     "rationale": "Owner calc.py: add combines its operands wrongly."}, "prop"))
        drive.turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "w"}, "appr"))
        drive.step(task_id, "mut-w", "workspace.write_file",
                   {"path": "calc.py", "content": WRONG, "expected_hash": _sha(BUGGY)}, "apply it")
        task = drive.step(task_id, "check-1", "workspace.run_tests", {"command": FOCUSED}, "run the check")
        assert task["narrow"]["success"] is False

        # The model ignores every correction and re-issues the same failed check, turn after turn.
        for attempt in range(2, 6):
            task = drive.step(task_id, f"check-{attempt}", "workspace.run_tests", {"command": FOCUSED},
                              "run the same test command again")
            assert task["steps"][f"check-{attempt}"]["status"] == "repeated_verification"
        reply = drive.turn("summarize", _call("respond__direct",
                                              {"message": "I will keep running the full suite until it passes."}, "final"))

        # Bounded and honest: no invented verification, no completion claim, no unauthorized
        # mutation; the failed outcome and the task itself are preserved for a real recovery.
        text = _reply_text(reply)
        assert "completed." not in text, text[:400]
        assert task["stage"] == "narrow_test" and len(task["verifications"]) == 1
        assert task["narrow"]["success"] is False
        assert (rig["workspace"] / "calc.py").read_text(encoding="utf-8") == WRONG
        assert (rig["workspace"] / "test_calc.py").read_text(encoding="utf-8") == TEST
        # And the door out stayed open the whole time: the recovery actions are in the record.
        reason = task["steps"]["check-5"]["reason"]
        assert "reopens diagnosis" in reason and "rerun_reason" in reason, reason[:300]
    finally:
        rig["daemon"].stop()
        rig["provider"].__exit__(None, None, None)
