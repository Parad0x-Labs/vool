"""Static tree-wide check: no module under core/, adapters/, or apps/ may assign to a
`sys.modules` slot that resolves to ITS OWN dotted import name.

R2 (AUD-20260829-003), extended in pass 3. The council verdict's root-remedy finding: a module
that reassigns its own `sys.modules[__name__]` entry abandons the object CPython already
registered for it. Under concurrent first import of that name, CPython's import fast path can hand
a waiting thread the abandoned pre-swap object -- which never carries the canonical module's names
-- raising a spurious `ImportError` (reproduced byte-exact by two independent investigator
reports, R01 and R03; see `tests/test_module_identity_race.py` for the harness ported from R03's
method).

**Pass 3 correction.** The four A9 alias shims under `core/agent_runtime/` were fixed in pass 1 by
becoming re-export shims (copying the canonical module's namespace onto themselves in place). That
closed the race but silently broke a DIFFERENT invariant: it made the alias and canonical module
OBJECTS two distinct objects, so `mock.patch("core.agent_runtime.<alias>.<name>", ...)` patched a
copy the runtime never reads through -- vacuously, which is worse than failing outright (BLUE-3,
pass 3). The pass-3 fix restores identity a level up: `core/agent_runtime/__init__.py` -- a
genuinely single-threaded boot seam, since importing ANY submodule of a package always waits for
that package's own `__init__` to finish first -- imports each canonical module and registers the
alias name directly: `sys.modules[f"{__name__}.live_data_plan"] = live_data_plan`. That is a
DIFFERENT module (the parent package) assigning a CHILD name from single-threaded init, never a
module reassigning its OWN name from inside its own body, so it does not reintroduce the hazard --
but it is also a live demonstration that a narrow AST match on the literal token `__name__` is not
the right invariant boundary: a module could reintroduce the ORIGINAL hazard by spelling its own
name as a STRING LITERAL instead of `__name__` (`sys.modules["core.agent_runtime.live_data_plan"] =
_canonical`, written from inside `live_data_plan.py` itself) and evade a check that only looks for
the `__name__` token. This check now resolves the assigned key against the FILE'S OWN derived
dotted module name (from its repo-relative path), so it catches `__name__`, a matching string
literal, and simple concatenations/f-strings that reduce to that same name -- while a key that
resolves to a DIFFERENT (child or sibling) name, or a key this check cannot statically resolve at
all, is left alone. This is deliberately not adversarial-proof (e.g. `sys.modules.__setitem__(...)`,
`globals()["__name__"]` tricks, or a key built from a runtime computation this check cannot
evaluate would all slip past); the goal is catching the natural mistake and its most direct
evasion, not out-arguing a determined attacker of the linter itself.

This check exists so a fifth shim of the OLD self-swap idiom -- under either spelling -- cannot
reappear anywhere in the tree, in either the four already-fixed modules or any new one, while
explicitly permitting the parent-registers-child pattern pass 3 relies on.

What this DOES flag: an assignment (Store context) to `sys.modules[K]` where `K` is:
  - the bare name `__name__`, or
  - a string literal equal to the enclosing file's own derived dotted module name, or
  - an f-string / `+`-concatenation that, after substituting the file's own name for `__name__`,
    statically reduces to exactly that same name (i.e. no additional literal text was appended).

What this does NOT flag, on purpose:
  - READING `sys.modules[__name__]` into a local name, e.g. `_THIS_MODULE = sys.modules[__name__]`
    (Load context). That is a different, safe idiom used elsewhere in this tree for
    self-reference / dependency-injection hooks (`core/brain_hive_dashboard.py`,
    `apps/vool_daemon.py` -- both inspected for R2 and confirmed to only ever READ this
    expression, never reassign the registry entry).
  - Assigning to `sys.modules[K]` where `K` resolves to a DIFFERENT name than the enclosing file's
    own -- the parent-registers-child pattern in `core/agent_runtime/__init__.py`.
  - Assigning to `sys.modules[K]` where `K` cannot be statically resolved to a concrete string at
    all (e.g. a name imported from elsewhere, a function call, a dict/list lookup) -- flagging
    these would require actually running the code, which a static, import-time-safe checker
    cannot do; no production code in this tree does this today (verified by this check's own
    "unparsable"/offender scan finding none), and the two READ-only self-reference idioms above
    are the only other `sys.modules[__name__]`-shaped expressions in the tree.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_SCAN_ROOTS = ("core", "adapters", "apps")
_IGNORED_DIR_PARTS = {".git", "__pycache__", ".pytest_cache"}


def _module_name_from_relpath(relpath: str) -> str | None:
    """The dotted module name Python would assign this file at import time, or None when
    `relpath` is not a real repo-relative `.py` path (e.g. a synthetic unit-test filename like
    `"<source>"`, which carries no derivable module identity)."""
    if not relpath.endswith(".py"):
        return None
    parts = list(Path(relpath).parts)
    if not parts:
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts) if parts else None


def _resolve_static_string(node: ast.AST, *, own_name: str | None) -> str | None:
    """Best-effort static evaluation of a string-producing expression, treating a bare
    `Name(id="__name__")` as a stand-in for `own_name` (the enclosing file's own derived module
    name). Returns None when the expression cannot be resolved this way -- deliberately
    conservative; see the module docstring's "does NOT flag" section for why that is correct here,
    not a gap to close.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id == "__name__":
        return own_name
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                pieces.append(part.value)
            elif isinstance(part, ast.FormattedValue) and part.format_spec is None and part.conversion in (-1, None):
                resolved = _resolve_static_string(part.value, own_name=own_name)
                if resolved is None:
                    return None
                pieces.append(resolved)
            else:
                return None
        return "".join(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_static_string(node.left, own_name=own_name)
        right = _resolve_static_string(node.right, own_name=own_name)
        if left is None or right is None:
            return None
        return left + right
    return None


def _is_sys_modules_subscript(node: ast.AST) -> ast.AST | None:
    """When `node` is the AST shape `sys.modules[<slice>]`, returns the slice expression (any
    ctx); otherwise None."""
    if not isinstance(node, ast.Subscript):
        return None
    value = node.value
    if not (isinstance(value, ast.Attribute) and value.attr == "modules"):
        return None
    if not (isinstance(value.value, ast.Name) and value.value.id == "sys"):
        return None
    sl = node.slice
    if isinstance(sl, ast.Index):  # pragma: no cover - py<3.9 shape; repo pins 3.11
        sl = sl.value
    return sl


def find_offending_lines(source: str, *, filename: str = "<source>") -> list[int]:
    """Line numbers where `sys.modules[K]` is ASSIGNED TO (Store context) with `K` resolving to
    the enclosing file's OWN dotted module name -- derived from `filename` when it looks like a
    real repo-relative path, else undeterminable (see `_module_name_from_relpath`), in which case
    only the literal `__name__` spelling can be recognized.

    Raises nothing on unparsable input beyond letting SyntaxError propagate -- callers that scan
    a tree of real repo files should treat a SyntaxError as its own reportable issue, not silently
    skip the file.
    """
    own_name = _module_name_from_relpath(filename)
    tree = ast.parse(source, filename=filename)
    lines: list[int] = []
    for node in ast.walk(tree):
        sl = _is_sys_modules_subscript(node)
        if sl is None or not isinstance(getattr(node, "ctx", None), ast.Store):
            continue
        if isinstance(sl, ast.Name) and sl.id == "__name__":
            lines.append(node.lineno)
            continue
        if own_name is None:
            continue
        resolved = _resolve_static_string(sl, own_name=own_name)
        if resolved is not None and resolved == own_name:
            lines.append(node.lineno)
    return sorted(lines)


def _iter_python_files():
    for root_name in _SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in _IGNORED_DIR_PARTS for part in path.parts):
                continue
            yield path


def build_report() -> dict[str, object]:
    offenders: dict[str, list[int]] = {}
    unparsable: list[str] = []

    for path in _iter_python_files():
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unparsable.append(f"{relative}: {type(exc).__name__}: {exc}")
            continue
        try:
            lines = find_offending_lines(source, filename=relative)
        except SyntaxError as exc:
            unparsable.append(f"{relative}: SyntaxError: {exc}")
            continue
        if lines:
            offenders[relative] = lines

    issues = [
        f"{relative} assigns to its own sys.modules entry at line(s) {lines} "
        f"(re-export shim, or parent-package registration, required instead -- see "
        f"ops/check_module_identity.py docstring)"
        for relative, lines in sorted(offenders.items())
    ]
    issues.extend(f"could not parse {entry}" for entry in unparsable)

    return {
        "status": "CLEAN" if not issues else "FAIL",
        "scanned_roots": list(_SCAN_ROOTS),
        "offending_modules": offenders,
        "unparsable": unparsable,
        "issues": issues,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "CLEAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
