"""Environment-conformance preflight — an under-installed runtime must fail
BEFORE serving, never as a mid-request HTTP 500.

Reproduced defect (2026-09-05 release proof): in a canonical ``uv sync
--frozen`` environment — zstandard is only a ``companion``/``runtime``
optional extra in pyproject while requirements*.txt declare it — the daemon
booted green, /healthz served 200, and POST /api/memory/forget returned
HTTP 500 because the Command Registry's eager group imports reached
``core.liquefy`` -> vendored codec -> ``import zstandard``.

These tests prove the boot gate without any stub or fake module: the
under-installed case uses a REAL stdlib-only interpreter whose site-packages
genuinely lack the dependencies.
"""
from __future__ import annotations

import json
import subprocess
import sys

from pathlib import Path

import pytest

from core.runtime_dependency_preflight import (
    ImportConformanceReport,
    assert_served_import_conformance,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_conformance_green_on_the_development_environment() -> None:
    """The registry import closure builds in a fully provisioned runtime."""
    report = assert_served_import_conformance()
    assert report.ok, report.failure_message()


def test_failed_report_names_module_and_repair() -> None:
    report = ImportConformanceReport(
        ok=False,
        missing_module="zstandard",
        repair_command="python -m pip install zstandard",
        detail="ModuleNotFoundError: No module named 'zstandard'",
    )
    message = report.failure_message()
    assert "REFUSING TO SERVE" in message
    assert "zstandard" in message
    assert "pip install zstandard" in message


def test_underinstalled_runtime_fails_closed_before_serving(tmp_path: Path) -> None:
    """A REAL interpreter with genuinely missing dependencies must refuse.

    Runs the conformance check under ``sys.executable -S`` — site-packages
    initialization skipped, so the interpreter genuinely has none of the
    project's dependencies (nothing stubbed, nothing faked) while stdlib and
    the repo on PYTHONPATH remain importable. The report must be a typed
    dependency failure naming a missing module with a repair hint, and the
    boot gate must exit non-zero — never serve.
    """
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from core.runtime_dependency_preflight import (\n"
        "    assert_served_import_conformance,\n"
        "    preflight_served_imports_or_exit,\n"
        ")\n"
        "report = assert_served_import_conformance()\n"
        "print(json.dumps({\n"
        "    'ok': report.ok,\n"
        "    'missing_module': report.missing_module,\n"
        "    'repair_command': report.repair_command,\n"
        "}))\n"
        "try:\n"
        "    preflight_served_imports_or_exit()\n"
        "    print(json.dumps({'gate_stopped_boot': False}))\n"
        "except SystemExit as exc:\n"
        "    print(json.dumps({'gate_stopped_boot': True, 'exit_code': exc.code}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=str(REPO_ROOT),
        env={"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [line for line in completed.stdout.strip().splitlines() if line.startswith("{")]
    assert lines, f"no report printed (rc={completed.returncode}): {completed.stderr[-800:]}"
    results = [json.loads(line) for line in lines]
    report_json = next(r for r in results if "ok" in r)
    gate_json = next(r for r in results if "gate_stopped_boot" in r)

    assert report_json["ok"] is False, (
        "an interpreter with genuinely no dependencies passed the conformance "
        "gate — the check is vacuous"
    )
    assert report_json["missing_module"], report_json
    assert report_json["repair_command"].startswith("python -m pip install ")
    assert gate_json["gate_stopped_boot"] is True, gate_json
    assert gate_json["exit_code"] == 2


def test_server_boot_refuses_to_serve_on_failed_conformance(monkeypatch, tmp_path: Path) -> None:
    """``apps.vool_api_server.main`` runs the gate BEFORE bootstrap/bind.

    Sabotage anchor: sever the wiring in main() and this goes RED — the boot
    would proceed into bootstrap instead of refusing.
    """
    from core import runtime_dependency_preflight as preflight_module

    failing = ImportConformanceReport(
        ok=False,
        missing_module="zstandard",
        repair_command="python -m pip install zstandard",
        detail="ModuleNotFoundError: No module named 'zstandard'",
    )
    monkeypatch.setattr(
        preflight_module,
        "assert_served_import_conformance",
        lambda **_k: failing,
    )

    import apps.vool_api_server as server

    def _bootstrap_must_not_run(*_a, **_k):
        raise AssertionError("bootstrap was reached despite failed import conformance")

    monkeypatch.setattr(server, "_bootstrap", _bootstrap_must_not_run)
    monkeypatch.setattr(
        server.sys, "argv", ["vool-api-server", "--port", "0", "--bind", "127.0.0.1"]
    )
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert excinfo.value.code == 2
