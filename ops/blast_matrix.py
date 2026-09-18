"""Name what a change can break, then test exactly that.

THE PROBLEM THIS SOLVES. A fix to Z that touches module B can degrade feature A that also
depends on B — and the only two existing answers are both wrong: run the full 1000+-file
suite on every one-line fix (an hour of wall clock, so it stops being run), or run only the
fix's own tests (and A breaks silently, which is how this repo keeps finding regressions a
day late). The missing piece is the middle: given a diff, name the set of tests whose
subject can actually be reached from the changed files, and run THAT.

HOW. Two detection channels, both static, both stdlib-only (nothing may be installed on
this machine):

1. IMPORT REACH. Parse every production module and every test file with ast, resolve
   imports to repo modules (function-local lazy imports included -- this repo uses them
   heavily and ast.walk sees them), build the reverse graph, and take the transitive
   importers of every changed module. A test is selected when it imports anything in that
   closure.

2. PATH-LITERAL REACH. Some tests reach a file without importing it -- the chat-page tests
   read core/vool_chat_page.py as source text and drive its JavaScript. An import graph is
   blind to that class, so any test whose source mentions a changed file's stem in a string
   is selected too.

WHAT THIS IS NOT. Not proof of safety -- a selected-and-green blast set is evidence, not a
release gate; the full suite still runs at integration/release boundaries. Not a coverage
tool -- it never executes anything. And deliberately not clever: dynamic dispatch through
strings (getattr chains, importlib with computed names) is invisible to both channels,
which is why HUB ESCALATION exists -- a changed module whose blast set exceeds a fraction
of all tests is a hub, and the honest recommendation for a hub is the full suite, printed
as such rather than silently truncated.

USAGE
    .venv/bin/python ops/blast_matrix.py --files core/within_turn_retraction.py
    .venv/bin/python ops/blast_matrix.py --diff HEAD~1
    .venv/bin/python ops/blast_matrix.py --files core/task_router.py --explain tests/test_market_intent.py
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import deque
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("core", "apps", "tools", "storage", "network", "relay", "ops")
TESTS_DIR = REPO / "tests"

#: A changed module whose selection exceeds this fraction of all tests is a HUB: the graph
#: is telling us the change is repo-wide, and pretending a subset covers it would be the
#: exact false economy this tool exists to prevent.
HUB_FRACTION = 0.35

#: Always-run canaries: one fast file per core product lane, independent of the diff. These
#: are not keyed to any bug report -- they are the product's pulse (front-door routing,
#: output contracts, the clock, retraction, the event ledger). A missing sentinel is a
#: warning, not a crash, so a renamed file degrades loudly instead of silently.
SENTINELS = (
    "tests/test_response_constraint_router.py",
    "tests/test_an_exact_output_contract_survives_every_decorator.py",
    "tests/test_a_named_place_never_gets_the_local_clock.py",
    "tests/test_an_instruction_taken_back_is_not_an_instruction.py",
    "tests/test_the_event_log_shows_the_whole_chat.py",
)


def _module_name(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(REPO)
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[0] not in PACKAGES:
        return None
    return ".".join(parts)


def _iter_production_files() -> list[Path]:
    files: list[Path] = []
    for pkg in PACKAGES:
        root = REPO / pkg
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _imports_in(path: Path, known: set[str], self_name: str | None) -> set[str]:
    """Repo modules imported by this file, resolved through packages and relative levels."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    found: set[str] = set()

    def _note(candidate: str) -> None:
        # "from core.agent_runtime import response" names the MODULE core.agent_runtime.response;
        # resolve to the deepest known module so the edge lands on the real file.
        while candidate:
            if candidate in known:
                found.add(candidate)
                return
            candidate = candidate.rpartition(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _note(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and self_name:
                anchor = self_name.split(".")
                anchor = anchor[: len(anchor) - node.level] if node.level <= len(anchor) else []
                base = ".".join([*anchor, base]) if base else ".".join(anchor)
            for alias in node.names:
                _note(f"{base}.{alias.name}" if base else alias.name)
            if base:
                _note(base)
    found.discard(self_name or "")
    return found


class BlastMatrix:
    def __init__(self) -> None:
        self._files = _iter_production_files()
        self._by_name: dict[str, Path] = {}
        for p in self._files:
            name = _module_name(p)
            if name:
                self._by_name[name] = p
        known = set(self._by_name)
        self._importers: dict[str, set[str]] = {n: set() for n in known}
        for name, path in self._by_name.items():
            for dep in _imports_in(path, known, name):
                self._importers.setdefault(dep, set()).add(name)
        self._test_files = sorted(
            p for p in TESTS_DIR.rglob("test_*.py") if "__pycache__" not in p.parts
        )
        self._test_imports: dict[Path, set[str]] = {
            t: _imports_in(t, known, None) for t in self._test_files
        }

    @property
    def total_tests(self) -> int:
        return len(self._test_files)

    def affected_modules(self, changed: list[str]) -> tuple[dict[str, int], dict[str, str]]:
        """Transitive importer closure of the changed files as {module: hop distance},
        with parent links for --explain. Distance 0 is the changed module itself; each
        reverse-import edge adds one hop. Distance is what makes the closure USABLE:
        the full closure of a core module legitimately reaches half this repo's tests
        (measured: 607/1085 for a leaf, because everything funnels through the agent),
        and hop-bounding is how a per-fix run stays a few well-aimed tests instead."""

        seeds = []
        for c in changed:
            name = _module_name((REPO / c) if not Path(c).is_absolute() else Path(c))
            if name:
                seeds.append(name)
        dist: dict[str, int] = {s: 0 for s in seeds}
        parent: dict[str, str] = {}
        queue = deque(seeds)
        while queue:
            cur = queue.popleft()
            for importer in self._importers.get(cur, ()):
                if importer not in dist:
                    dist[importer] = dist[cur] + 1
                    parent[importer] = cur
                    queue.append(importer)
        return dist, parent

    def select(self, changed: list[str], max_hops: int | None = None) -> dict:
        dist, parent = self.affected_modules(changed)
        closure = set(dist) if max_hops is None else {m for m, d in dist.items() if d <= max_hops}
        selected: dict[Path, str] = {}
        for test, deps in self._test_imports.items():
            hit = deps & closure
            if hit:
                nearest = min(hit, key=lambda m: dist[m])
                selected[test] = f"imports {nearest} (hop {dist[nearest]})"
        # Path-literal channel: tests that read a changed file as text rather than import it.
        stems = {Path(c).stem for c in changed if Path(c).suffix in {".py", ".js", ".html", ".tab"}}
        stems.discard("")
        for test in self._test_files:
            if test in selected:
                continue
            try:
                source = test.read_text(encoding="utf-8")
            except OSError:
                continue
            for stem in stems:
                if stem in source:
                    selected[test] = f"mentions {stem!r} in source"
                    break
        full_closure_hits = sum(1 for deps in self._test_imports.values() if deps & set(dist))
        hub = self.total_tests > 0 and full_closure_hits / self.total_tests > HUB_FRACTION
        sentinels = [s for s in SENTINELS if (REPO / s).is_file()]
        missing = [s for s in SENTINELS if not (REPO / s).is_file()]
        return {
            "changed": changed,
            "max_hops": max_hops,
            "affected_module_count": len(closure),
            "full_closure_test_count": full_closure_hits,
            "selected": {str(t.relative_to(REPO)): why for t, why in sorted(selected.items())},
            "sentinels": sentinels,
            "missing_sentinels": missing,
            "hub_escalation": hub,
            "total_tests": self.total_tests,
            "parent": parent,
        }

    def explain(self, changed: list[str], test: str) -> list[str]:
        """The dependency chain from a changed file to the named test, for humans."""

        dist, parent = self.affected_modules(changed)
        test_path = (REPO / test).resolve()
        deps = self._test_imports.get(test_path, set())
        hits = sorted(deps & set(dist), key=lambda m: dist[m])
        if not hits:
            return []
        chain = [hits[0]]
        while chain[-1] in parent:
            chain.append(parent[chain[-1]])
        return [test, *chain]


def _changed_from_diff(rev: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--name-only", rev],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", nargs="*", default=[], help="changed files (repo-relative)")
    parser.add_argument("--diff", help="git rev to diff against for the changed set")
    parser.add_argument("--explain", help="show why this test is in the blast set")
    parser.add_argument("--max-hops", type=int, default=2,
                        help="importer hops from the changed file (default 2); -1 = unbounded")
    args = parser.parse_args(argv)

    changed = list(args.files)
    if args.diff:
        changed.extend(_changed_from_diff(args.diff))
    if not changed:
        parser.error("nothing changed: pass --files or --diff")

    max_hops = None if args.max_hops < 0 else args.max_hops
    matrix = BlastMatrix()
    if args.explain:
        chain = matrix.explain(changed, args.explain)
        if chain:
            print(" -> ".join(chain))
        else:
            print(f"{args.explain}: NOT in the blast set for {changed}")
        return 0

    report = matrix.select(changed, max_hops=max_hops)
    print(f"changed: {report['changed']}")
    print(f"affected modules (within {report['max_hops']} hops): {report['affected_module_count']}")
    print(f"blast set: {len(report['selected'])} of {report['total_tests']} test files "
          f"(full closure would hit {report['full_closure_test_count']})")
    for test, why in report["selected"].items():
        print(f"  {test}    [{why}]")
    for s in report["missing_sentinels"]:
        print(f"WARNING: sentinel missing on disk: {s}")
    if report["hub_escalation"]:
        print("\nHUB ESCALATION: this change reaches more than "
              f"{int(HUB_FRACTION * 100)}% of all tests -- run the FULL suite:")
        print("  python3.12 ops/verify.py --workers 2")
        return 0
    run_set = sorted(set(report["selected"]) | set(report["sentinels"]))
    print("\nrun (blast set + sentinels):")
    print(
        'T=$(mktemp -d); VOOL_HOME="$T" VOOL_KEY_STORAGE_MODE=file VOOL_KEY_PASSPHRASE=probe '
        ".venv/bin/python -m pytest -q " + " ".join(run_set)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
