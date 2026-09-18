"""Boot-time environment conformance for the served command surface.

Why this exists (observed 2026-09-05, P0 release proof): the Command
Registry imports EVERY group module on first use, so an under-installed
runtime — a dependency the served surface needs missing from the active
environment — does not fail at BOOT. The daemon booted green, /healthz
served 200, and the first request across the registry (POST
/api/memory/forget) died as an HTTP 500:

    registry() -> groups/logs_group.py -> core.liquefy.operator
    -> core.liquefy.store -> core.liquefy.api -> vendor.columnar_gun_v1
    -> ModuleNotFoundError: No module named 'zstandard'

``zstandard`` is present in ``requirements.txt`` /
``requirements-runtime.txt`` but only in the ``companion``/``runtime``
OPTIONAL EXTRAS of ``pyproject.toml``, so a default ``pip install .`` /
``uv sync`` environment legitimately lacks it while the served command
door hard-requires it at import time. Environment drift of exactly this
shape must fail BEFORE any request is served, naming the missing module
and the repair — never as a mid-request 500.

The check executes the REAL import closure (building the Command Registry
in a short-lived child interpreter with the same executable), so it can
never drift from the served surface's actual requirements: no maintained
manifest, no dependency guessing, no stubs. The child runs against a
throwaway synthetic home with file-backed key storage so the conformance
check itself can never touch operator credential state (C15 discipline).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_CHILD_SCRIPT = (
    "from core.command_registry.registry import registry\n"
    "registry()\n"
)

_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError: No module named '([^']+)'"
)

# Import name -> distribution name where they differ (pip repair hints).
_DIST_HINTS = {
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "crypto": "pycryptodome",
    "Crypto": "pycryptodome",
}


@dataclass(frozen=True)
class ImportConformanceReport:
    """Typed outcome of the served-import conformance check."""

    ok: bool
    missing_module: str = ""
    repair_command: str = ""
    detail: str = ""
    child_returncode: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def failure_message(self) -> str:
        if self.ok:
            return "served import conformance OK"
        if self.missing_module:
            return (
                "REFUSING TO SERVE: this runtime is missing a dependency the "
                f"served command surface imports ({self.missing_module!r}). "
                f"Repair the environment (e.g. `{self.repair_command}`) or "
                "install the documented runtime requirements before starting "
                "the server. Detail: "
                + self.detail.strip().splitlines()[-1:].__str__()
            )
        return (
            "REFUSING TO SERVE: the served import conformance check could not "
            f"complete (exit {self.child_returncode}). Detail: {self.detail}"
        )


def _child_environment(scratch: str) -> dict[str, str]:
    env = dict(os.environ)
    # The check must never touch operator credential state: pin the child to a
    # throwaway home with non-interactive file-backed storage (C15 discipline).
    env["VOOL_HOME"] = str(Path(scratch) / "home")
    env["VOOL_KEY_STORAGE_MODE"] = "file"
    env["VOOL_KEY_PASSPHRASE"] = "import-conformance-preflight"
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def assert_served_import_conformance(*, timeout: float = 180.0) -> ImportConformanceReport:
    """Prove the served command surface's import closure imports in THIS runtime.

    Builds the Command Registry in a child interpreter (same executable, same
    environment, synthetic home). A ``ModuleNotFoundError`` becomes a typed
    dependency failure with a pip repair hint; any other child failure is an
    indeterminate environment failure — the gate fails CLOSED either way.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="vool-import-preflight-") as scratch:
            # Probe the interpreter AS CURRENTLY CONFIGURED: if this process
            # runs with site initialization disabled (``-S``), the child must
            # too — the conformance question is about the runtime environment
            # actually in effect, never about a differently-configured child.
            command = [sys.executable]
            if sys.flags.no_site:
                command.append("-S")
            command += ["-c", _CHILD_SCRIPT]
            completed = subprocess.run(
                command,
                cwd=str(_PROJECT_ROOT),
                env=_child_environment(scratch),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ImportConformanceReport(
            ok=False,
            detail=f"conformance child could not run: {exc}",
        )
    if completed.returncode == 0:
        return ImportConformanceReport(ok=True, child_returncode=0)
    combined = (completed.stderr or "") + "\n" + (completed.stdout or "")
    match = None
    for match in _MODULE_NOT_FOUND_RE.finditer(combined):
        pass  # keep the LAST ModuleNotFoundError (the root of the chain)
    if match is not None:
        module = match.group(1).split(".")[0]
        dist = _DIST_HINTS.get(module, module)
        return ImportConformanceReport(
            ok=False,
            missing_module=module,
            repair_command=f"python -m pip install {dist}",
            detail=combined.strip()[-2000:],
            child_returncode=completed.returncode,
        )
    return ImportConformanceReport(
        ok=False,
        detail=combined.strip()[-2000:],
        child_returncode=completed.returncode,
        notes=("no ModuleNotFoundError in child output — indeterminate failure",),
    )


def preflight_served_imports_or_exit(*, logger=None) -> ImportConformanceReport:
    """The boot gate: run the conformance check and refuse to serve on failure.

    Called by ``apps.vool_api_server.main`` BEFORE bootstrap and port bind.
    """
    report = assert_served_import_conformance()
    if not report.ok:
        message = report.failure_message()
        if logger is not None:
            logger.error("%s", message)
        else:  # logging may not be configured yet — the console is the floor
            print(message, file=sys.stderr)
        raise SystemExit(2)
    return report
