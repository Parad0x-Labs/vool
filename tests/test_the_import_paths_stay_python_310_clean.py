from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every directory whose modules can be reached at import time — the conftest chain
# (tests -> apps -> core -> storage) plus everything an installed runtime or an
# installer script imports. requires-python is >=3.10 and the shell installers accept
# >=3.10, but the 3.10 interpreter row only runs on the WEEKLY scheduled matrix, so a
# 3.11-only symbol on any of these paths turns Monday's whole matrix red (fail-fast)
# a week after it lands and no sooner. Live case: `from datetime import UTC` in
# core/agent_runtime/orchestrator.py killed conftest collection on the 3.10 row
# (run 30803615367) while every 3.11+ dev machine stayed green.
IMPORT_CRITICAL_DIRS = ("apps", "core", "installer", "ops", "relay", "storage", "tests", "tools")

# Generated build/audit output is not part of the import graph the suite or the
# runtime loads, and the repo already excludes it from audit scope for the same reason.
SKIPPED_DIR_NAMES = {"__pycache__", "generated"}

# The one file allowed to say `from enum import StrEnum`: the 3.10 shim itself.
STRENUM_SHIM = Path("core") / "enum_compat.py"


def _import_critical_python_files() -> list[Path]:
    files: list[Path] = []
    for dir_name in IMPORT_CRITICAL_DIRS:
        root = REPO_ROOT / dir_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIPPED_DIR_NAMES.intersection(part.name for part in path.parents):
                continue
            files.append(path)
    return files


def _311_only_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(REPO_ROOT)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "UTC":
                    findings.append(
                        f"{rel}:{node.lineno} imports UTC from datetime (3.11+ alias; use timezone.utc)"
                    )
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "UTC"
            and isinstance(node.value, ast.Name)
            and node.value.id == "datetime"
        ):
            findings.append(
                f"{rel}:{node.lineno} reads datetime.UTC (3.11+ alias; use timezone.utc)"
            )
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "enum"
            and rel != STRENUM_SHIM
            and any(alias.name == "StrEnum" for alias in node.names)
        ):
            findings.append(
                f"{rel}:{node.lineno} imports StrEnum from enum (3.11+; import it from core.enum_compat)"
            )
    return findings


def test_no_import_critical_file_uses_a_311_only_symbol() -> None:
    files = _import_critical_python_files()
    # If the walk finds nothing the guard is scanning the wrong tree, not proving cleanliness.
    assert len(files) > 100, f"suspiciously few files scanned ({len(files)}) — guard is miswired"

    findings: list[str] = []
    for path in files:
        findings.extend(_311_only_uses(path))

    assert not findings, (
        "3.11-only symbols on import-critical paths break the Python 3.10 weekly CI row "
        "at conftest collection:\n" + "\n".join(findings)
    )
