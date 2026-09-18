#!/usr/bin/env python3
"""Record REAL code-task journals written by an older runtime, for migration/rollback tests.

Runs ordinary task flows through the production door (``execute_runtime_tool``) of the repo
named by ``--repo`` -- an untouched checkout of an older revision -- inside a disposable,
mission-owned environment, and copies each resulting journal plus the fixture workspace bytes
into ``--out/<scenario>/``. Nothing is hand-written: the journal bytes are whatever that
runtime persisted. A scenario the older runtime refuses is recorded as refused, not faked.

Usage: generate_legacy_journals.py --repo PATH --out DIR --label v1_df49|v2_e064
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUG = "exports.add = (a, b) => a - b;\n"
WRONG = "exports.add = (a, b) => a * b;\n"
FIXED = "exports.add = (a, b) => a + b;\n"
CHECK = "const {add}=require('./math.js');require('assert').strictEqual(add(7,4),11);\n"
OTHER = "exports.label = (parts) => parts.join(';');\n"
OTHER_FIXED = "exports.label = (parts) => parts.join(',');\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--only", default="", help="comma-separated scenario names")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    scratch = out / "_scratch"
    for name in ("home", "blackbox", "tasks", "tmp"):
        (scratch / name).mkdir(parents=True)
    os.environ.update(
        VOOL_HOME=str(scratch / "home"),
        VOOL_BLACKBOX_DIR=str(scratch / "blackbox"),
        VOOL_CODE_TASK_DIR=str(scratch / "tasks"),
        VOOL_KEY_STORAGE_MODE="file",
        VOOL_LOCAL_MODELS_ENABLED="0",
        VOOL_SKIP_PROVIDER_PREWARM="1",
        TMPDIR=str(scratch / "tmp"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.dont_write_bytecode = True
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    import core

    if not str(core.__file__).startswith(str(repo)):
        raise SystemExit(f"core imported from {core.__file__}, not {repo}")
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_execution_tools import execute_runtime_tool

    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    manifest: dict[str, object] = {"label": args.label, "runtime_repo": str(repo), "runtime_head": head, "scenarios": {}}

    def workspace(name: str, files: dict[str, str]) -> Path:
        root = scratch / "ws" / name
        root.mkdir(parents=True)
        for rel, text in files.items():
            (root / rel).write_text(text, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
               "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "seed"]):
            subprocess.run(["git", "-C", str(root), *cmd], check=True, capture_output=True, env=env)
        return root

    def run(name: str, files: dict[str, str], script) -> None:
        reset_mode_permission_state()
        root = workspace(name, files)
        ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": f"legacy-{name}",
               "runtime_session_id": f"legacy-{name}", "operating_mode": "auto"}
        calls: list[dict[str, object]] = []

        def door(intent: str, arguments: dict) -> object:
            result = execute_runtime_tool(intent, arguments, source_context=ctx)
            calls.append({"intent": intent, "arguments": arguments, "ok": bool(result.ok), "status": result.status,
                          "stage": dict(result.details or {}).get("stage")})
            return result

        opened = door("code.task.open", {"objective": f"legacy scenario {name}"})
        task_id = opened.details["task_id"]

        def step(step_id: str, intent: str, arguments: dict) -> object:
            return door("code.task.step", {"task_id": task_id, "step_id": step_id, "intent": intent, "arguments": arguments})

        script(task_id, door, step)
        dest = out / name
        (dest / "workspace").mkdir(parents=True)
        journal = Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json"
        shutil.copyfile(journal, dest / "journal.json")
        for rel in files:
            shutil.copyfile(root / rel, dest / "workspace" / rel)
        manifest["scenarios"][name] = {
            "task_id": task_id,
            "workspace_root_recorded": str(root.resolve()),
            "calls": calls,
            "journal_sha256": hashlib.sha256((dest / "journal.json").read_bytes()).hexdigest(),
        }

    def repro_identify_read(task_id, door, step, path="math.js"):
        step("repro", "workspace.run_tests", {"command": "node check.js"})
        door("code.task.identify", {"task_id": task_id, "path": path, "line": 1, "reason": "subtracts"})
        step("read", "workspace.read_file", {"path": path})

    def approved_with_hash(task_id, door, step):
        repro_identify_read(task_id, door, step)
        door("code.task.propose", {"task_id": task_id, "proposal_id": "p1", "intent": "workspace.write_file",
                                   "arguments": {"path": "math.js", "content": FIXED, "expected_hash": sha(BUG)},
                                   "rationale": "Owner math.js: restore addition."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"})

    def approved_without_hash(task_id, door, step):
        repro_identify_read(task_id, door, step)
        door("code.task.propose", {"task_id": task_id, "proposal_id": "p1", "intent": "workspace.write_file",
                                   "arguments": {"path": "math.js", "content": FIXED},
                                   "rationale": "Owner math.js: restore addition."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"})

    def completed(task_id, door, step):
        approved_with_hash(task_id, door, step)
        step("mut", "workspace.write_file", {"path": "math.js", "content": FIXED, "expected_hash": sha(BUG)})
        step("narrow", "workspace.run_tests", {"command": "node check.js"})
        step("cumulative", "workspace.run_tests", {"command": "node check.js"})
        step("diff", "workspace.git_diff", {})
        door("code.task.report", {"task_id": task_id})

    def narrow_passed(task_id, door, step):
        # The repair landed and its focused check passed; the full check has not run yet (stage cumulative).
        approved_with_hash(task_id, door, step)
        step("mut", "workspace.write_file", {"path": "math.js", "content": FIXED, "expected_hash": sha(BUG)})
        step("narrow", "workspace.run_tests", {"command": "node check.js"})

    def stale_failure_after_corrected_mutation(task_id, door, step):
        repro_identify_read(task_id, door, step)
        door("code.task.propose", {"task_id": task_id, "proposal_id": "wrong", "intent": "workspace.write_file",
                                   "arguments": {"path": "math.js", "content": WRONG, "expected_hash": sha(BUG)},
                                   "rationale": "Owner math.js: wrong operator."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "wrong"})
        step("mut-wrong", "workspace.write_file", {"path": "math.js", "content": WRONG, "expected_hash": sha(BUG)})
        step("verify-wrong", "workspace.run_tests", {"command": "node check.js"})
        door("code.task.identify", {"task_id": task_id, "path": "math.js", "line": 1, "reason": "first fix wrong"})
        step("reread", "workspace.read_file", {"path": "math.js"})
        door("code.task.propose", {"task_id": task_id, "proposal_id": "fixed", "intent": "workspace.write_file",
                                   "arguments": {"path": "math.js", "content": FIXED, "expected_hash": sha(WRONG)},
                                   "rationale": "Owner math.js: restore addition."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "fixed"})
        step("mut-fixed", "workspace.write_file", {"path": "math.js", "content": FIXED, "expected_hash": sha(WRONG)})

    def two_pending_units(task_id, door, step):
        repro_identify_read(task_id, door, step)
        step("read-other", "workspace.read_file", {"path": "labels.js"})
        door("code.task.propose", {"task_id": task_id, "proposal_id": "a", "intent": "workspace.write_file",
                                   "arguments": {"path": "math.js", "content": FIXED, "expected_hash": sha(BUG)},
                                   "rationale": "Owner math.js: restore addition."})
        door("code.task.propose", {"task_id": task_id, "proposal_id": "b", "intent": "workspace.write_file",
                                   "arguments": {"path": "labels.js", "content": OTHER_FIXED, "expected_hash": sha(OTHER)},
                                   "rationale": "Owner labels.js: join with a comma."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "a"})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "b"})
        step("mut-a", "workspace.write_file", {"path": "math.js", "content": FIXED, "expected_hash": sha(BUG)})

    def approved_unread_sibling(task_id, door, step):
        # A proposal over a file the task never read (only the defect owner must be read).
        repro_identify_read(task_id, door, step)
        door("code.task.propose", {"task_id": task_id, "proposal_id": "sibling", "intent": "workspace.write_file",
                                   "arguments": {"path": "labels.js", "content": OTHER_FIXED},
                                   "rationale": "Owner labels.js: join with a comma."})
        door("code.task.approve", {"task_id": task_id, "proposal_id": "sibling"})

    files = {"math.js": BUG, "check.js": CHECK, "labels.js": OTHER}
    scenarios = [
        ("approved_with_hash", approved_with_hash),
        ("approved_without_hash", approved_without_hash),
        ("completed", completed),
        ("narrow_passed", narrow_passed),
        ("stale_failure_after_corrected_mutation", stale_failure_after_corrected_mutation),
        ("two_pending_units", two_pending_units),
        ("approved_unread_sibling", approved_unread_sibling),
    ]
    only = {name for name in args.only.split(",") if name}
    for name, script in scenarios:
        if not only or name in only:
            run(name, files, script)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    shutil.rmtree(scratch / "ws")
    print(json.dumps({name: [c["status"] for c in row["calls"]] for name, row in manifest["scenarios"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
