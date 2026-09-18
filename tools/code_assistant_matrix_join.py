#!/usr/bin/env python3
"""C12's requirement → production caller → offered tool → served proof join.

The 101-row acceptance matrix carried `plane`, `capability`, `status`, `evidence`,
`note` and `release_critical` — and **no caller column**, so the join C12 asks for
could not be read off it and had never been performed.

This performs it mechanically and writes the result back as three added columns.
It does not rename or drop a row, and it does not change a `status`: the join
records what is TRUE about each row's evidence, and where the evidence resolves to
nothing that is itself the finding.

    production_caller  the file:symbol or dotted tool intent the evidence names,
                       confirmed to exist in the tree. "" when the evidence names
                       nothing resolvable — which is the honest result for a row
                       whose capability is ABSENT.
    offered_tool       the runtime tool contract, when the evidence names an intent
                       the registry actually offers.
    served_proof       test files that exercise the evidence token, so a row's
                       claim can be walked back to a test rather than a memory.

## The typed authority, and the release gate

An evidence sentence can be prose; a release-critical row may not rest on one. A
row may therefore carry an `authority` — a TYPED reference, machine-validated
against the tree, never a sentence. Existence proves nothing and neither does a
mention: a list of independently existing symbols is not a call chain, a dead
reference is not a call, a token in a comment or an assignment is not a served
proof, and a function that exists is not therefore the authority a row claims.
The validator binds each claim to the tree:

    {"kind": "caller", "path": P, "symbol": CALLEE,
     "edges": [[CALLER_PATH, CALLER_SYMBOL], ...]}

    The caller→callee EDGE is checked with the AST and must be an actual CALL: the
    caller function's body must contain an ast.Call whose func is the callee's
    name or attribute. An assignment, a comparison, a dead reference or an unused
    expression does not call anything and fails the edge. Aliased / dynamically
    dispatched calls declare the edge as
    {"path": CP, "symbol": CS, "call_token": TOKEN, "behavior_test": "tests/t.py::test_x"}:
    TOKEN must occur INSIDE the declared caller function's body (comments
    stripped) — an occurrence elsewhere in the same file dispatches nothing — and
    the named behavioral test must exist and be collectable.

    {"kind": "served", "node_ids": ["tests/x.py::test_name", ...],
     "token": T, "assert_token": T2}

    Exact pytest node IDs, not files. Each must exist, define that test, and be
    GENUINELY PYTEST-COLLECTABLE (a real `pytest --collect-only` pass, not merely
    an AST def); T must occur inside at least one named node's body with comments
    stripped, and T2 must PARTICIPATE IN AN ACTUAL ASSERTION (an `assert`
    statement or an explicit assertion-helper call) in at least one named node.
    A token that lives only in a comment, an assignment, an unused string or a
    comparison without an assertion proves nothing. The join runs the collection
    pass itself and the evidence lane executes the exact node ids.

    {"kind": "refusal", "path": P, "symbol": S,
     "status_token": T, "before_dispatch_test": "tests/x.py::test_y",
     "test_token": T2}

    S must be the function that PRODUCES the refusal: T must occur in S's body
    (comments stripped), and the named test — which must exist and be
    collectable — must assert T2 in its own body via a real assertion, binding
    the refusal to a proof it fires before dispatch/effect.

    {"kind": "obsolete", "law": "...", "replacement": {<caller|served|refusal>}}

    The replacement law stated and a replacement authority that itself resolves.

Prose cannot satisfy a release-critical row: a bare string, an unknown kind, an
unknown key, a missing file, an undefined symbol, a severed edge, a mention where
a call is required, a comment-only or unasserted token, a non-refusing function,
or a node id pytest cannot collect all fail validation — and `--gate` exits
non-zero while any release-critical row resolves to nothing.

Run:  python -m tools.code_assistant_matrix_join           # report only
      python -m tools.code_assistant_matrix_join --write   # write the columns back
      python -m tools.code_assistant_matrix_join --gate    # exit 1 if a release-critical
                                                           # row rests on nothing
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import pathlib
import re
import subprocess
import sys
import textwrap
import tokenize

REPO = pathlib.Path(__file__).resolve().parents[1]
MATRIX = REPO / "docs" / "code_assistant" / "ACCEPTANCE_MATRIX.json"

_PATH_SYMBOL = re.compile(r"([\w/]+\.py)(?:\s+(\w+))?")
_DOTTED_INTENT = re.compile(r"^[a-z][\w-]*(?:\.[\w-]+)+$")

AUTHORITY_KINDS = ("caller", "served", "refusal", "obsolete")
_AUTHORITY_KEYS = {
    "kind", "path", "symbol", "edges",
    "node_ids", "token", "assert_token",
    "status_token", "before_dispatch_test", "test_token",
    "law", "replacement", "note",
}


def _resolve_path_symbol(evidence: str) -> str:
    """`core/x/y.py symbol` -> confirmed "path:symbol", or "" when it is not there."""
    match = _PATH_SYMBOL.search(evidence)
    if not match:
        return ""
    rel, symbol = match.group(1), match.group(2) or ""
    path = REPO / rel
    if not path.exists():
        return ""
    if not symbol:
        return rel
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if re.search(rf"^\s*(?:def|class)\s+{re.escape(symbol)}\b", text, re.M):
        return f"{rel}:{symbol}"
    return rel


def _resolve_intent(evidence: str, offered: set[str]) -> str:
    token = evidence.strip()
    if _DOTTED_INTENT.match(token) and token in offered:
        return token
    return ""


def _offered_intents() -> set[str]:
    try:
        from core.runtime_tool_contracts import runtime_tool_contracts

        return {str(getattr(c, "intent", "") or "") for c in runtime_tool_contracts()}
    except Exception:
        return set()


def _served_proof(evidence: str) -> list[str]:
    """Test files that mention the evidence token. Grep, not inference."""
    token = evidence.strip()
    if not token or len(token) < 6:
        return []
    needle = token.split()[-1] if " " in token else token
    if len(needle) < 6:
        return []
    try:
        out = subprocess.run(
            ["git", "grep", "-l", "--fixed-strings", needle, "--", "tests/"],
            cwd=str(REPO), capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return []
    return sorted(line for line in out.stdout.splitlines() if line.strip())[:4]


# --------------------------------------------------------------------------
# typed-authority validation: existence is not an edge, a mention is not a call
# --------------------------------------------------------------------------


def _def_spans(text: str, symbol: str) -> list[tuple[int, int]]:
    """Decorator-inclusive (start, end) 1-based line spans of every def/class `symbol`."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == symbol:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append((start, node.end_lineno))
    return spans


def _segment(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return "\n".join(text.splitlines()[start - 1:end])


def _span_tree(text: str, span: tuple[int, int]) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(_segment(text, span)))
    except SyntaxError:
        return None


def _body_calls(text: str, symbol: str, callee: str) -> bool:
    """True when the body of def/class `symbol` actually CALLS `callee`: an
    ast.Call whose func is the callee's name or attribute. Assignments, dead
    references and unused expressions are not calls."""
    for span in _def_spans(text, symbol):
        tree = _span_tree(text, span)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Name) and func.id == callee) or \
                        (isinstance(func, ast.Attribute) and func.attr == callee):
                    return True
    return False


def _strip_comments(text: str) -> str:
    """The source with COMMENT tokens removed: a token that lives only in a
    comment must not read as code evidence."""
    try:
        tokens = [
            tok for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type != tokenize.COMMENT
        ]
        return tokenize.untokenize(tokens)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return re.sub(r"#[^\n]*", "", text)


def _read(root: pathlib.Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _symbol_defined(text: str, symbol: str) -> bool:
    return bool(_def_spans(text, symbol))


def _assert_participates(tree: ast.AST, token: str) -> bool:
    """Does `token` participate in an ACTUAL assertion — an `assert` statement or
    an explicit assertion-helper call (`self.assertEqual`, `assert_...`)? A
    substring in an assignment, an unused string or a bare comparison proves
    nothing."""

    def involved(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id == token:
                return True
            if isinstance(n, ast.Attribute) and n.attr == token:
                return True
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and token in n.value:
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and involved(node.test):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name.startswith("assert") and involved(node):
                return True
    return False


def _node_asserts(node_id: str, root: pathlib.Path, token: str) -> bool:
    """The named test's body must assert `token` through a real assertion."""
    rel, _, name = str(node_id).partition("::")
    text = _read(root, rel)
    if text is None:
        return False
    for span in _def_spans(text, name):
        tree = _span_tree(text, span)
        if tree is not None and _assert_participates(tree, token):
            return True
    return False


# ---- real pytest collection: an AST def is not a collected test -------------

_COLLECTION_CACHE: dict[tuple[str, frozenset], frozenset[str]] = {}


def collect_node_ids(node_ids: list[str], root: pathlib.Path = REPO,
                     timeout: int = 600) -> frozenset[str]:
    """Run one real `pytest --collect-only` over the node ids and return what
    pytest actually collects. Failure collects nothing: fail closed."""
    wanted = sorted({str(n) for n in node_ids})
    if not wanted:
        return frozenset()
    key = (str(root), frozenset(wanted))
    cached = _COLLECTION_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header",
             *wanted],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
        )
        lines = out.stdout.splitlines()
    except Exception:
        lines = []
    collected = set()
    for line in lines:
        line = line.strip()
        if "::" in line and not line.startswith(("=", "!")):
            collected.add(line)
    frozen = frozenset(collected)
    _COLLECTION_CACHE[key] = frozen
    return frozen


def _node_collected(node_id: str, collected: frozenset[str]) -> bool:
    if node_id in collected:
        return True
    return any(c.startswith(node_id + "[") for c in collected)  # parametrized ids


def _authority_node_ids(authority: object) -> list[str]:
    if not isinstance(authority, dict):
        return []
    ids = [str(n) for n in authority.get("node_ids") or []]
    if authority.get("before_dispatch_test"):
        ids.append(str(authority["before_dispatch_test"]))
    for edge in authority.get("edges") or []:
        if isinstance(edge, dict) and edge.get("behavior_test"):
            ids.append(str(edge["behavior_test"]))
    return ids


def _validate_node_id(node_id: str, root: pathlib.Path,
                      collected: frozenset[str] | None = None) -> tuple[bool, str]:
    rel, _, name = str(node_id).partition("::")
    if not rel or not name or "::" in name:
        return False, f"not an exact pytest node id (path::test_name): {node_id!r}"
    if not rel.startswith("tests/"):
        return False, f"node id is not under tests/: {rel}"
    text = _read(root, rel)
    if text is None:
        return False, f"node id file not in tree: {rel}"
    if not _symbol_defined(text, name):
        return False, f"test {name!r} is not defined in {rel}"
    if collected is not None and not _node_collected(str(node_id), collected):
        return False, f"node id is not pytest-collectable: {node_id}"
    return True, ""


def _validate_edge(edge: object, callee: str, root: pathlib.Path,
                   collected: frozenset[str] | None) -> tuple[bool, str]:
    """One caller→callee edge. The caller must exist AND actually call the callee."""
    dynamic = isinstance(edge, dict)
    if dynamic:
        epath, esymbol = str(edge.get("path") or ""), str(edge.get("symbol") or "")
    elif isinstance(edge, (list, tuple)) and len(edge) == 2:
        epath, esymbol = str(edge[0]), str(edge[1])
    else:
        return False, f"edge is not [caller_path, caller_symbol]: {edge!r}"
    if not epath or not esymbol:
        return False, f"edge needs caller path and symbol: {edge!r}"
    caller_text = _read(root, epath)
    if caller_text is None:
        return False, f"edge caller path not in tree: {epath}"
    if not _symbol_defined(caller_text, esymbol):
        return False, f"edge caller symbol not defined in {epath}: {esymbol}"
    if dynamic:
        call_token = str(edge.get("call_token") or "")
        behavior_test = str(edge.get("behavior_test") or "")
        if not call_token or not behavior_test:
            return False, (
                f"dynamic-dispatch edge {epath}:{esymbol} needs both call_token "
                "and behavior_test"
            )
        body = "\n".join(
            _strip_comments(_segment(caller_text, span))
            for span in _def_spans(caller_text, esymbol)
        )
        if call_token not in body:
            return False, (
                f"call_token {call_token!r} does not occur inside the body of "
                f"{epath}:{esymbol} — an occurrence elsewhere in the same file "
                "dispatches nothing"
            )
        ok, why = _validate_node_id(behavior_test, root, collected)
        if not ok:
            return False, f"behavior_test of {epath}:{esymbol} does not resolve: {why}"
        return True, ""
    if not _body_calls(caller_text, esymbol, callee):
        return False, (
            f"edge severed: {epath}:{esymbol} never CALLS the callee {callee!r} — "
            "an assignment, dead reference or unused expression is not a call"
        )
    return True, ""


def _node_body(node_id: str, root: pathlib.Path) -> str | None:
    rel, _, name = str(node_id).partition("::")
    text = _read(root, rel)
    if text is None:
        return None
    spans = _def_spans(text, name)
    if not spans:
        return None
    return "\n".join(_segment(text, span) for span in spans)


def validate_authority(authority: object, root: pathlib.Path = REPO,
                       collected: frozenset[str] | None = None) -> tuple[bool, str]:
    """A typed authority must resolve against the tree: real calls, real tokens,
    real assertions, really collected tests.

    Prose — any bare string, any untyped object, any unknown kind or key — is
    invalid by construction. Returns (ok, why-not). When `collected` is None the
    pytest-collection check is skipped (cheap static path); the join and the gate
    always supply it.
    """
    if not isinstance(authority, dict):
        return False, f"authority is not a typed object: {authority!r}"
    unknown = set(authority) - _AUTHORITY_KEYS
    if unknown:
        return False, f"authority carries unknown key(s) {sorted(unknown)} — not typed evidence"
    kind = authority.get("kind")
    if kind not in AUTHORITY_KINDS:
        return False, f"authority kind {kind!r} is not one of {AUTHORITY_KINDS}"

    if kind in ("caller", "refusal"):
        rel, symbol = str(authority.get("path") or ""), str(authority.get("symbol") or "")
        if not rel or not symbol:
            return False, f"{kind} authority needs both path and symbol"
        text = _read(root, rel)
        if text is None:
            return False, f"{kind} path not in tree: {rel}"
        if not _symbol_defined(text, symbol):
            return False, f"{kind} symbol not defined in {rel}: {symbol}"
        # An authority with no edges is a disconnected symbol, not a joined row:
        # the gate demands the production chain, and it demands it typed.
        edges = authority.get("edges")
        if not isinstance(edges, list):
            return False, (
                f"{kind} authority edges must be a list of [caller_path, "
                f"caller_symbol] pairs (or dynamic-dispatch objects), got "
                f"{type(edges).__name__}"
            )
        if not edges:
            return False, f"{kind} authority requires at least one caller→callee edge"

    if kind == "caller":
        for edge in authority.get("edges") or []:
            ok, why = _validate_edge(edge, symbol, root, collected)
            if not ok:
                return False, why
        return True, ""

    if kind == "refusal":
        status_token = str(authority.get("status_token") or "")
        before_test = str(authority.get("before_dispatch_test") or "")
        test_token = str(authority.get("test_token") or "")
        if not status_token:
            return False, "refusal authority needs the exact status_token it produces"
        body = "\n".join(
            _strip_comments(_segment(text, span)) for span in _def_spans(text, symbol)
        )
        if status_token not in body:
            return False, (
                f"status_token {status_token!r} never occurs in {rel}:{symbol} — "
                "this function does not produce the claimed refusal"
            )
        if not before_test:
            return False, "refusal authority needs a before_dispatch_test node id"
        ok, why = _validate_node_id(before_test, root, collected)
        if not ok:
            return False, f"before_dispatch_test does not resolve: {why}"
        if not test_token:
            return False, "refusal authority needs the test_token the named test asserts"
        if not _node_asserts(before_test, root, test_token):
            return False, (
                f"test_token {test_token!r} is never ASSERTED in the body of "
                f"{before_test} — an assignment, unused string or comparison "
                "without an assertion does not prove the refusal"
            )
        for edge in authority.get("edges") or []:
            ok, why = _validate_edge(edge, symbol, root, collected)
            if not ok:
                return False, why
        return True, ""

    if kind == "served":
        node_ids = authority.get("node_ids")
        if not isinstance(node_ids, list) or not node_ids:
            return False, "served authority needs a non-empty node_ids list"
        for node_id in node_ids:
            ok, why = _validate_node_id(node_id, root, collected)
            if not ok:
                return False, why
        token = str(authority.get("token") or "")
        if not token:
            return False, "served authority needs the capability token it exercises"
        if not any(
            token in _strip_comments(_node_body(n, root) or "") for n in node_ids
        ):
            return False, (
                f"token {token!r} occurs in no named node's body (comments do not count)"
            )
        assert_token = str(authority.get("assert_token") or "")
        if not assert_token:
            return False, "served authority needs an assert_token the named tests assert"
        if not any(_node_asserts(n, root, assert_token) for n in node_ids):
            return False, (
                f"assert_token {assert_token!r} participates in no real assertion in "
                "the named nodes' bodies — string presence alone is not a proof"
            )
        return True, ""

    # obsolete
    law = str(authority.get("law") or "").strip()
    replacement = authority.get("replacement")
    if not law:
        return False, "obsolete authority needs the replacement law stated"
    ok, why = validate_authority(replacement, root, collected)
    if not ok:
        return False, f"obsolete replacement does not resolve: {why}"
    return True, ""


def join(write: bool = False, matrix: pathlib.Path = MATRIX,
         root: pathlib.Path = REPO) -> dict:
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    offered = _offered_intents()

    # one real collection pass over every node id any authority names
    wanted: set[str] = set()
    for row in rows:
        wanted.update(_authority_node_ids(row.get("authority")))
    collected = collect_node_ids(sorted(wanted), root) if wanted else frozenset()

    resolved = 0
    authority_resolved = 0
    for row in rows:
        evidence = str(row.get("evidence") or "")
        caller = _resolve_path_symbol(evidence)
        intent = _resolve_intent(evidence, offered)
        row["production_caller"] = caller or intent
        row["offered_tool"] = intent
        row["served_proof"] = _served_proof(evidence)
        if row["production_caller"]:
            resolved += 1

        ok, _why = validate_authority(row.get("authority"), root, collected)
        row["authority_resolved"] = bool(ok)
        if ok:
            authority_resolved += 1

    def _unresolved(row: dict) -> bool:
        return not row.get("production_caller") and not row.get("authority_resolved")

    release_critical = [r for r in rows if r.get("release_critical")]
    summary = {
        "rows": len(rows),
        "with_production_caller": resolved,
        "without_production_caller": len(rows) - resolved,
        "release_critical_without_caller": sum(
            1 for r in release_critical if not r.get("production_caller")
        ),
        "release_critical_without_authority": sum(
            1 for r in release_critical if not r.get("authority_resolved")
        ),
        # The release-critical census: a row is PROSE-ONLY when nothing resolvable
        # stands under it — no evidence-derived caller and no typed authority.
        "release_critical_prose_only": sum(1 for r in release_critical if _unresolved(r)),
        "release_critical_absent": sum(
            1 for r in release_critical if r.get("status") == "ABSENT"
        ),
        "with_served_proof": sum(1 for r in rows if r.get("served_proof")),
        "authorities": authority_resolved,
        "collected_node_ids": len(wanted),
    }
    if write:
        payload["rows"] = rows
        payload["join"] = summary
        matrix.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def gate(summary: dict) -> int:
    """The release gate: zero release-critical rows may rest on prose alone."""
    prose_only = summary.get("release_critical_prose_only", -1)
    absent = summary.get("release_critical_absent", -1)
    if prose_only != 0 or absent != 0:
        print(
            f"GATE FAIL: release_critical_prose_only={prose_only} "
            f"release_critical_absent={absent}",
            file=sys.stderr,
        )
        return 1
    print(
        f"GATE PASS: {summary['rows']} rows, {summary['release_critical_without_caller']} "
        f"release-critical rows carry no evidence-derived caller; every one of them is "
        f"held up by a typed authority with AST-verified call edges, body-scoped "
        f"tokens, asserted proofs and collected node ids. release_critical_prose_only=0."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the joined columns back")
    parser.add_argument(
        "--gate", action="store_true",
        help="exit 1 unless every release-critical row resolves to tree evidence",
    )
    args = parser.parse_args(argv)
    summary = join(write=args.write)
    print(json.dumps(summary, indent=2))
    if args.gate:
        return gate(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
