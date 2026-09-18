"""The KAS law, made mechanical.

"KAS owns external adapters and channels; VOOL owns permissions, semantic truth, effects,
privacy and finality" is an architecture claim, and an architecture claim that nothing checks
decays the first time somebody is in a hurry. This module turns it into a static scan with a
typed verdict, so the law is enforced by a test rather than by review attention.

The scan reads an adapter module's syntax tree and reports every construct through which the
adapter could acquire an authority it is not allowed to hold:

* importing a VOOL authority (permissions, effect gateway, policy, blackbox, faults, privacy,
  finality, the runtime execution door) — the adapter would be deciding, not translating;
* importing a transport (``urllib``, ``requests``, ``httpx``, ``socket``, ``http.client``) or a
  process/filesystem primitive — the adapter would have an egress VOOL never authorized;
* reading the environment or a credential store — the adapter would be holding a secret rather
  than naming an opaque binding;
* ``open``/``exec``/``eval``/``__import__`` — the escape hatches that make the import list a lie.

A finding names the file, the line, the construct and WHICH authority it would duplicate, so a
failure reads as an architecture verdict instead of a lint complaint.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: module prefix -> the authority it would duplicate if an adapter imported it
FORBIDDEN_MODULES: dict[str, str] = {
    "core.mode_permission_policy": "permissions",
    "core.policy_engine": "permissions",
    "core.effect_gateway": "effects",
    "core.effect_reconciliation": "effects",
    "core.remote_fetch_policy": "effects (egress)",
    "core.runtime_execution_tools": "effects (the runtime door)",
    "core.tool_intent_executor": "effects (the runtime door)",
    "core.blackbox": "effects (the flight recorder)",
    "core.faults": "semantic truth (the fault catalog)",
    "core.secret_redaction": "privacy",
    "core.credential_store": "privacy (credentials)",
    "core.credential_intelligence": "privacy (credentials)",
    "core.finalization": "finality",
    "core.final_response_store": "finality",
    "core.finalizer": "finality",
    "core.grounding_publication": "finality",
    "core.turn_contract": "finality",
    "core.runtime_continuity": "finality",
    "core.execution_records": "finality (evidence)",
    "core.authorized_tool_execution": "permissions",
    "core.execution_gate": "permissions",
    "core.kernel": "permissions (kernel capabilities)",
    "core.semantic": "semantic truth",
    "core.privacy_guard": "privacy",
    "core.egress_gate": "privacy",
    "core.cloud_privacy_policy": "privacy",
    "core.platform": "effects (a second execution broker)",
    "core.council": "semantic truth (the council authority)",
    # `urllib.parse` is pure string work and is deliberately NOT here: an adapter must be able
    # to build a URL. `urllib.request`/`urllib.error` are the egress half and are forbidden.
    "urllib.request": "effects (egress)",
    "urllib.error": "effects (egress)",
    "requests": "effects (egress)",
    "httpx": "effects (egress)",
    "http.client": "effects (egress)",
    "socket": "effects (egress)",
    "ssl": "effects (egress)",
    "subprocess": "effects (process)",
    "os": "privacy (environment) / effects (process)",
    "pathlib": "effects (filesystem)",
    "shutil": "effects (filesystem)",
    "tempfile": "effects (filesystem)",
    "sqlite3": "effects (storage)",
    "keyring": "privacy (credentials)",
}

#: bare builtins an adapter may not call — each one reopens a door the import scan closed
FORBIDDEN_CALLS: dict[str, str] = {
    "open": "effects (filesystem)",
    "exec": "any (arbitrary code)",
    "eval": "any (arbitrary code)",
    "compile": "any (arbitrary code)",
    "__import__": "any (dynamic import defeats the import scan)",
    "globals": "any (namespace escape)",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    construct: str
    authority: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: `{self.construct}` would duplicate VOOL's {self.authority} authority"


def _forbidden_module(name: str) -> str:
    clean = str(name or "").strip()
    if not clean:
        return ""
    parts = clean.split(".")
    for depth in range(len(parts), 0, -1):
        candidate = ".".join(parts[:depth])
        if candidate in FORBIDDEN_MODULES:
            return candidate
    return ""


def scan_source(source: str, *, path: str = "<adapter>") -> list[Finding]:
    """Every way this source could take an authority it is not allowed to hold."""

    findings: list[Finding] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding(path=path, line=int(exc.lineno or 0), construct="syntax error", authority="unparseable")]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _forbidden_module(alias.name)
                if hit:
                    findings.append(
                        Finding(path=path, line=node.lineno, construct=f"import {alias.name}", authority=FORBIDDEN_MODULES[hit])
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import stays inside core.kas.adapters, which is fine
                continue
            hit = _forbidden_module(node.module or "")
            if hit:
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        construct=f"from {node.module} import ...",
                        authority=FORBIDDEN_MODULES[hit],
                    )
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                findings.append(
                    Finding(path=path, line=node.lineno, construct=f"{func.id}(...)", authority=FORBIDDEN_CALLS[func.id])
                )
        elif isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            findings.append(
                Finding(path=path, line=node.lineno, construct=f"...{node.attr}", authority="privacy (environment)")
            )

    return sorted(findings, key=lambda f: (f.line, f.construct))


def scan_path(path: Path) -> list[Finding]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return scan_source(text, path=str(path))


def adapters_dir() -> Path:
    return Path(__file__).resolve().parent / "adapters"


def scan_adapters() -> list[Finding]:
    """Scan every shipped adapter. An empty list is the law holding."""

    findings: list[Finding] = []
    for module in sorted(adapters_dir().glob("*.py")):
        if module.name == "__init__.py":
            continue
        findings.extend(scan_path(module))
    return findings


__all__ = [
    "FORBIDDEN_CALLS",
    "FORBIDDEN_MODULES",
    "Finding",
    "adapters_dir",
    "scan_adapters",
    "scan_path",
    "scan_source",
]
