"""Deterministic, read-only machine probes.

Every external observation goes through :class:`ProbeRunner`, so tests inject fakes and the
default runner is a plain ``subprocess.run(argv, ..., timeout=10)`` that never passes the
process environment through to captured output. Probe stderr/stdout is only ever stored after
``models.evidence()`` scrubbing — the one channel that could echo secret material.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.toolbelt.models import evidence


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ProbeRunner:
    """Seam for deterministic tests. Default implementation shells out read-only."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: list[str], *, cwd: str | None = None) -> ProbeResult:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "NO_COLOR": "1"},
            )
            # Scrub immediately: raw output must not survive past this boundary unredacted.
            return ProbeResult(proc.returncode, evidence(proc.stdout), evidence(proc.stderr))
        except FileNotFoundError:
            return ProbeResult(127, "", "executable not found")
        except subprocess.TimeoutExpired:
            return ProbeResult(124, "", "probe timed out")
        except OSError as exc:
            return ProbeResult(126, "", evidence(str(exc)))


# --- probe recipes ---------------------------------------------------------------------------

VERSION_COMMANDS: dict[str, list[str]] = {
    "git": ["git", "--version"],
    "gh": ["gh", "--version"],
    "python3": ["python3", "--version"],
    "uv": ["uv", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "cargo": ["cargo", "--version"],
    "rustc": ["rustc", "--version"],
    "docker": ["docker", "--version"],
    "make": ["make", "--version"],
    "cmake": ["cmake", "--version"],
    "xcodebuild": ["xcodebuild", "-version"],
    "swift": ["swift", "--version"],
    "jq": ["jq", "--version"],
    "curl": ["curl", "--version"],
}

# Tools whose --version writes to stderr (python) or needs module invocation.
_SPECIAL_VERSIONS = {
    "python3": ["python3", "--version"],
}


def extract_version(tool_id: str, result: ProbeResult) -> str | None:
    """Pull a version string out of healthy version-command output."""
    if not result.ok:
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    if not text:
        return None
    line = text[0]
    for token in line.replace("(", " ").replace(")", " ").split():
        if token and token[0].isdigit() and "." in token:
            return token.rstrip(",")
        if token.startswith("v") and token[1:2].isdigit():
            return token[1:]
    return None


def project_venv_python(repo_path: str | Path) -> Path | None:
    p = Path(repo_path) / ".venv" / "bin" / "python"
    return p if p.exists() else None


def classify_source(executable_path: str | None) -> str | None:
    """Coarse origin class from path shape — enough to spot shadowing, nothing more."""
    if not executable_path:
        return None
    p = executable_path.lower()
    if p.startswith("/usr/bin/") or p.startswith("/bin/"):
        return "system"
    if "homebrew" in p or p.startswith("/opt/homebrew/"):
        return "homebrew"
    if ".venv" in p:
        return "project-venv"
    if "nvm" in p or ".local/bin" in p:
        return "user-local"
    if ".cargo" in p:
        return "cargo"
    if "library/python" in p:
        return "pip-user"
    return "path"


__all__ = ["ProbeRunner", "ProbeResult", "VERSION_COMMANDS", "extract_version",
           "project_venv_python", "classify_source"]
