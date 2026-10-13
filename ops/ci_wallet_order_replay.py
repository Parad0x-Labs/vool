"""Diagnostic Linux replay of one recorded shard; never a full merge gate.

The downloaded artifact is data. Its file list is pinned, validated and passed
as separate argv entries. Only the identified privacy node moves, once, to the
end of pytest's otherwise unchanged collection. Watchdog and timing instruments
retain the real verdict and incomplete/red evidence.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from itertools import groupby
from pathlib import Path, PurePosixPath

import pytest

SOURCE_RUN = 38248362279
SOURCE_SHA = "9464c58f7d2829041bd7db6c2426c66b6d00ec6f"
SOURCE_ARTIFACT = "verification-logs-shard-1"
LIST_SHA256 = "50380e3cb2a93a5e2b3456a57489a8a7a4f536c83e9ba1caf14afa5d620f78dd"
FILE_COUNT = 277
_RECORDED_TEST_KIT = {"test-kit/test_cp2_controls.py", "test-kit/test_cp1_incident_repro.py"}
TARGET = "tests/wallet/test_wallet_lifecycle.py::test_status_surface_is_public_safe_and_counts_pending_approvals"
_PLAN_ENV = "VOOL_WALLET_ORDER_REPLAY_PLAN"
_REPO = Path(__file__).resolve().parent.parent


def validate_files(raw: bytes, root: Path, *, expected_count: int = FILE_COUNT) -> list[str]:
    """Accept only unique, existing regular test files inside this checkout."""
    files = raw.decode("utf-8", errors="strict").splitlines()
    if len(files) != expected_count or len(set(files)) != len(files):
        raise ValueError("recorded file count or uniqueness differs")
    root = root.resolve()
    for name in files:
        path = PurePosixPath(name)
        if (
            not (re.fullmatch(r"tests/[A-Za-z0-9_./-]+\.py", name) or name in _RECORDED_TEST_KIT)
            or path.as_posix() != name
            or any(part in {".", ".."} for part in path.parts)
            or not (root / name).is_file()
            or (root / name).resolve() != root / name
        ):
            raise ValueError(f"invalid, missing or symlinked test file: {name!r}")
    if TARGET.split("::", 1)[0] not in files:
        raise ValueError("the privacy test file is absent")
    return files


def function_ast_hash(source: str) -> str:
    """Hash the original test function, including its decorators and assertions."""
    name = TARGET.split("::", 1)[1]
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(nodes) != 1:
        raise ValueError("expected exactly one original privacy test function")
    return hashlib.sha256(ast.dump(nodes[0], include_attributes=False).encode()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(_REPO), *args], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout.strip()


def prepare_plan(artifact_dir: Path, source_run_json: Path) -> dict:
    run = json.loads(source_run_json.read_text(encoding="utf-8"))
    if (not isinstance(run, dict)
            or run.get("databaseId") != SOURCE_RUN or run.get("headSha") != SOURCE_SHA
            or run.get("status") != "completed" or run.get("conclusion") != "failure"):
        raise ValueError("source run identity or failed/completed status differs")
    raw = (artifact_dir / "shard-1-files.txt").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LIST_SHA256:
        raise ValueError("source artifact file-list digest differs")
    files = validate_files(raw, _REPO)
    original_tracked = _git("ls-tree", "-r", "--name-only", SOURCE_SHA, "--", *files).splitlines()
    current_tracked = _git("ls-files", "--error-unmatch", "--", *files).splitlines()
    if set(original_tracked) != set(files) or set(current_tracked) != set(files):
        raise ValueError("recorded test files are not tracked in both original and current source")
    target_file = TARGET.split("::", 1)[0]
    original_hash = function_ast_hash(_git("show", f"{SOURCE_SHA}:{target_file}"))
    current_hash = function_ast_hash((_REPO / target_file).read_text(encoding="utf-8"))
    if original_hash != current_hash:
        raise ValueError("the original privacy test function changed")
    return {
        "diagnostic_only": True,
        "source_run": SOURCE_RUN,
        "source_artifact": SOURCE_ARTIFACT,
        "source_sha": SOURCE_SHA,
        "checkout_sha": _git("rev-parse", "HEAD"),
        "original_test_function_ast_sha256": original_hash,
        "checkout_test_function_ast_sha256": current_hash,
        "source_artifact_list_sha256": digest,
        "all_recorded_files_tracked_in_both_sources": True,
        "files": files,
        "moved_node": TARGET,
    }


class ReplayOrderPlugin:
    """Preserve every collected item and relative order except the one target."""

    def __init__(self, files: list[str], output: Path) -> None:
        self.files = files
        self.output = output
        self.ordered: list[str] | None = None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items) -> None:
        del session, config
        original = [item.nodeid for item in items]
        if len(set(original)) != len(original) or original.count(TARGET) != 1:
            raise pytest.UsageError("replay requires unique nodes and exactly one privacy target")
        file_blocks = [path for path, _ in groupby(node.split("::", 1)[0] for node in original)]
        if file_blocks != self.files:
            raise pytest.UsageError("collected file order or coverage differs from the recorded artifact")
        target_index = original.index(TARGET)
        target = items.pop(target_index)
        items.append(target)
        self.ordered = [node for node in original if node != TARGET] + [TARGET]
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "started-node-order.txt").write_text("", encoding="utf-8")
        (self.output / "node-order.json").write_text(json.dumps({
            "original_nodes": original, "ordered_nodes": self.ordered,
            "collected_count": len(original), "moved_node": TARGET,
        }, indent=2) + "\n", encoding="utf-8")

    def pytest_collection_finish(self, session) -> None:
        if self.ordered is None or [item.nodeid for item in session.items] != self.ordered:
            raise pytest.UsageError("another collection hook changed the replay order")

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        del location
        with (self.output / "started-node-order.txt").open("a", encoding="utf-8") as log:
            log.write(nodeid + "\n")


def pytest_configure(config) -> None:
    plan_path = os.environ.get(_PLAN_ENV)
    if not plan_path:
        return
    if sys.platform != "linux":
        raise pytest.UsageError("recorded wallet replay refuses non-Linux collection")
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    files = validate_files(("\n".join(plan["files"]) + "\n").encode(), _REPO)
    if hashlib.sha256(("\n".join(files) + "\n").encode()).hexdigest() != LIST_SHA256:
        raise pytest.UsageError("replay plan differs from pinned artifact order")
    config.pluginmanager.register(ReplayOrderPlugin(files, Path(plan_path).parent), "wallet-order")


def main(argv: list[str] | None = None) -> int:
    # This refusal precedes artifact reads, git, subprocess creation and pytest collection.
    if sys.platform != "linux":
        print("REFUSED: recorded wallet order replay requires Linux", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-run-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        plan = prepare_plan(args.artifact_dir, args.source_run_json)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        (output / "result.json").write_text(json.dumps({
            "diagnostic_only": True, "refused": str(exc), "exit_code": 2,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    plan_path = output / "replay-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if args.validate_only:
        print(json.dumps(plan, indent=2))
        return 0
    command = [
        sys.executable, "ops/pytest_watchdog.py", "--phase-timeout", "600",
        "--node-budgets", "ops/shard_weights.json", "--output", str(output / "watchdog"),
        "--", sys.executable, "ops/pytest_timing.py", "--output", str(output / "timing.json"),
        "--", "-q", "--tb=short", "-p", "no:cacheprovider", "-p", "ops.ci_wallet_order_replay",
        *plan["files"],
    ]
    env = {**os.environ, _PLAN_ENV: str(plan_path)}
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=_REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    (output / "result.json").write_text(json.dumps({
        **plan, "command": command, "exit_code": result.returncode,
    }, indent=2) + "\n", encoding="utf-8")
    print((output / "pytest.log").read_text(encoding="utf-8"))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
