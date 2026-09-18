"""Reject an audit claim that the collected evidence contradicts.

An audit of a real project, driven through a cloud model on 2026-07-29, produced a well-structured
report in which several load-bearing statements were simply false. Checked against the source:

    "The repository has zero tests"        -> 48 test files, a tests/ tree, a Makefile `test:` target
    "No external dependencies"             -> the file opens with `import zstandard as zstd`
    "decode() streams records"             -> no `decode` exists anywhere in that file
    "Extract varints into a shared helper"  -> they are already imported from liquefy_primitives

None of those needed a second model to catch. Every one is decidable by looking at the evidence the
audit had already gathered. The danger is specific: the report's formatting was excellent, so its
polish raised operator confidence in proportion to how wrong it was.

This module is the cheap half of a claim-verification gate. It checks only assertions that can be
settled mechanically against the audit's own evidence, and it does not attempt to judge reasoning,
severity, or remediation quality — a model is still the one doing the analysis. What it removes is
the class of statement that is confidently wrong about facts already sitting in the transcript.

Deliberately conservative: when a claim cannot be settled from the evidence it is left ALONE rather
than flagged. A verifier that fires on ambiguity trains the operator to ignore it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# "no tests", "zero tests", "no automated tests", "tests are missing", "lacks tests"
_NO_TESTS_RE = re.compile(
    r"\b(?:no|zero|without any|lacks?|missing|absence of)\s+"
    r"(?:automated\s+|unit\s+|integration\s+)?tests?\b"
    r"|\btests?\s+(?:are|is|were)\s+(?:entirely\s+)?(?:absent|missing|nonexistent)\b"
    r"|\bno\s+test\s+(?:suite|coverage|files?)\b",
    re.IGNORECASE,
)

# "no external dependencies", "no third-party imports", "dependency-free", "stdlib only"
_NO_DEPS_RE = re.compile(
    # `deps` included because that is what the model actually wrote: "No external deps beyond
    # stdlib." The long form was covered and the shorthand was not, so a false claim walked straight
    # through the gate that existed to catch exactly it.
    r"\bno\s+(?:external|third[- ]party)\s+(?:dependenc|dep\b|deps\b|import|librar|package)"
    r"|\bdependency[- ]free\b"
    r"|\b(?:only|just)\s+(?:the\s+)?(?:standard library|stdlib)\b"
    r"|\bstandard[- ]library[- ]only\b",
    re.IGNORECASE,
)

# A symbol the report says exists: `decode()`, `foo_bar()`, "the decode method".
_SYMBOL_CALL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,})\(\)`|\b([a-z_][a-z0-9_]{3,})\(\)")

# "its `decode()` method", "the `decode()` method in this file", "this module's `decode()`" — a
# claim that the audited code DEFINES the symbol, not merely that the name occurs somewhere in it.
# The distinction matters: an imported helper is mentioned in a file it is not defined in, and
# `defines_symbol` was written for exactly this and then never called.
_SYMBOL_OWNERSHIP_RE = re.compile(
    r"\b(?:its|the|this\s+(?:file|module|class)'?s?|it)\s+"
    r"(?:own\s+)?"
    r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?\s*\(\)"
    r"|`?([A-Za-z_][A-Za-z0-9_]{2,})`?\s*\(\)\s+(?:method|function|helper)\s+"
    r"(?:defined\s+)?(?:in|of)\s+(?:this|the)\s+(?:file|module|class)",
    re.IGNORECASE,
)

# A cited location: `path/to/file.py:123`, "file.py line 123", "at line 63 of file.py".
_CITATION_RES = (
    re.compile(r"`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,5}):(\d{1,6})`?"),
    re.compile(r"`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,5})`?[,\s]+line\s+(\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\bline\s+(\d{1,6})\s+(?:of|in)\s+`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,5})`?", re.IGNORECASE),
)

# Python's standard library is not an "external dependency"; only a non-stdlib import refutes the
# claim. Kept small and explicit rather than importing sys.stdlib_module_names, which varies by
# interpreter version and would make the check's behaviour depend on the host Python.
_STDLIB_PREFIXES = frozenset(
    ["abc", "argparse", "ast", "asyncio", "base64", "binascii", "bisect", "calendar", "cmath", "collections", "concurrent", "configparser", "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib", "dis", "email", "enum", "errno", "faulthandler", "filecmp", "fnmatch", "fractions", "functools", "gc", "getpass", "gettext", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "imaplib", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword", "linecache", "locale", "logging", "lzma", "marshal", "math", "mimetypes", "mmap", "multiprocessing", "numbers", "operator", "os", "pathlib", "pickle", "pkgutil", "platform", "plistlib", "pprint", "queue", "random", "re", "readline", "reprlib", "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal", "site", "smtplib", "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics", "string", "stringprep", "struct", "subprocess", "sys", "sysconfig", "tarfile", "tempfile", "textwrap", "threading", "time", "timeit", "token", "tokenize", "traceback", "types", "typing", "unicodedata", "unittest", "urllib", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser", "xml", "zipfile", "zlib", "zoneinfo"]
)

_IMPORT_RE = re.compile(r"^\s*(?:import\s+([A-Za-z_][\w.]*)|from\s+([A-Za-z_][\w.]*)\s+import)", re.M)


@dataclass
class AuditEvidence:
    """What the audit actually gathered. Every check is decided against this, not against a guess."""

    inspected_paths: tuple[str, ...] = ()
    all_paths: tuple[str, ...] = ()
    sources: dict[str, str] = field(default_factory=dict)
    workspace_root: str = ""
    # Files the audit did NOT read to the end. A citation past the end of one of these is a gap in
    # our own evidence, not a fabrication by the model, and must not be reported as one.
    incomplete_files: tuple[str, ...] = ()
    # The file this pass was SCOPED to, or "" for a repository-wide sweep. Without it there is no
    # way to tell "the target is first in scope" (true of a scoped audit, which orders target-first)
    # from "there is no target and this is merely the first file on disk" — and the second was being
    # reported as the first. Measured 2026-08-07: an unscoped sweep of 189 of 268 files was headed
    # `Target: api/__init__.py`, the alphabetically-first source file in that repository, which no
    # operator had named.
    scoped_target: str = ""

    def line_count(self, path: str) -> int:
        text = self.sources.get(str(path))
        return 0 if text is None else len(text.splitlines())

    def was_read_completely(self, path: str) -> bool:
        return str(path) in self.sources and str(path) not in set(self.incomplete_files)

    def line_at(self, path: str, line: int) -> str:
        """The text of 1-indexed ``line`` in ``path``, or "" when it is out of range.

        `sources` holds VERBATIM text — the audit issues its reads with `"verbatim": True` — so
        `splitlines()[n - 1]` really is line n. A cited `file:line` has been fully decidable from
        the evidence already gathered this whole time; nothing checked it.
        """

        lines = str(self.sources.get(str(path), "")).splitlines()
        return lines[line - 1] if 1 <= line <= len(lines) else ""

    def has_test_files(self) -> bool:
        """Test files anywhere in the project, not merely in the part the inventory reached.

        The audit's inventory is capped (200 paths). On a project with more files than that, the
        test tree can fall entirely outside the cap — which is what happened live: the repository
        holds 48 test files, the inventory never listed them, and the model's "zero test files"
        claim passed the gate because the evidence looked like it agreed. A capped listing is not
        evidence of absence, so fall back to the real tree when the inventory found none.
        """

        if any(_looks_like_test(path) for path in self.all_paths):
            return True
        root = str(self.workspace_root or "").strip()
        if not root:
            return False
        from pathlib import Path

        try:
            base = Path(root)
            if not base.is_dir():
                return False
            for candidate in base.rglob("*"):
                if candidate.is_file() and _looks_like_test(
                    str(candidate.relative_to(base)).replace("\\", "/")
                ):
                    return True
        except Exception:
            return False
        return False

    def third_party_imports(self) -> tuple[str, ...]:
        found: list[str] = []
        for text in self.sources.values():
            for module, from_module in _IMPORT_RE.findall(text):
                root = (module or from_module or "").split(".")[0]
                if root and root not in _STDLIB_PREFIXES and not root.startswith("_"):
                    found.append(root)
        return tuple(dict.fromkeys(found))

    def defines_symbol(self, name: str) -> bool:
        pattern = re.compile(rf"\b(?:def|class)\s+{re.escape(name)}\b|\b{re.escape(name)}\s*=")
        return any(pattern.search(text) for text in self.sources.values())

    def mentions_symbol(self, name: str) -> bool:
        return any(re.search(rf"\b{re.escape(name)}\b", text) for text in self.sources.values())


def _looks_like_test(path: str) -> bool:
    lowered = str(path or "").lower()
    base = lowered.rsplit("/", 1)[-1]
    return (
        base.startswith("test_")
        or base.endswith("_test.py")
        or base.endswith(".test.js")
        or base.endswith(".spec.ts")
        or "/tests/" in f"/{lowered}"
        or lowered.startswith("tests/")
    )


def _resolve_cited_path(cited: str, evidence: AuditEvidence) -> str:
    """Match the spelling in the report to a path this audit actually read.

    A report cites `liquefy_apache_repetition_v1.py` for a file the audit read as
    `api/apache/liquefy_apache_repetition_v1.py`. Matched on trailing segments, and ambiguity
    resolves to nothing — flagging a line against the wrong file would be its own fabrication.
    """

    wanted = [p for p in str(cited or "").replace("\\", "/").split("/") if p]
    if not wanted:
        return ""
    matches = []
    for path in evidence.sources:
        parts = [p for p in str(path).replace("\\", "/").split("/") if p]
        if len(parts) >= len(wanted) and parts[-len(wanted):] == wanted:
            matches.append(str(path))
    return matches[0] if len(matches) == 1 else ""


@dataclass
class ClaimDefect:
    kind: str
    claim: str
    contradiction: str


def verify_audit_claims(answer: str, evidence: AuditEvidence) -> tuple[str, tuple[ClaimDefect, ...]]:
    """Return the answer with contradicted claims marked, plus what was found.

    The answer is annotated rather than silently rewritten: the operator asked a model for an
    analysis and is entitled to see what it said, alongside the evidence that refutes it. Silently
    deleting a claim would leave a report that reads as if the model never made the mistake.
    """

    text = str(answer or "")
    if not text.strip():
        return text, ()

    defects: list[ClaimDefect] = []

    match = _NO_TESTS_RE.search(text)
    if match and evidence.has_test_files():
        tests = [p for p in evidence.all_paths if _looks_like_test(p)]
        defects.append(
            ClaimDefect(
                kind="tests_exist",
                claim=match.group(0),
                contradiction=(
                    f"the inventory lists {len(tests)} test file(s), including "
                    + ", ".join(f"`{p}`" for p in tests[:3])
                ),
            )
        )

    match = _NO_DEPS_RE.search(text)
    if match:
        third_party = evidence.third_party_imports()
        if third_party:
            defects.append(
                ClaimDefect(
                    kind="dependencies_exist",
                    claim=match.group(0),
                    contradiction="the inspected source imports "
                    + ", ".join(f"`{name}`" for name in third_party[:4]),
                )
            )

    # A symbol the report describes the behaviour of must at least appear in what was read. Only
    # checked when sources were actually collected, otherwise every mention would be "unverifiable".
    if evidence.sources:
        for whole, bare in _SYMBOL_CALL_RE.findall(text):
            name = whole or bare
            if not name or name in {"self", "cls"}:
                continue
            if evidence.mentions_symbol(name):
                continue
            defects.append(
                ClaimDefect(
                    kind="symbol_absent",
                    claim=f"{name}()",
                    contradiction="that name does not appear in any file this audit read",
                )
            )

    # A symbol the report attributes to the audited code must be DEFINED there. `defines_symbol` was
    # written for this and had no caller; the weaker `mentions_symbol` above only asks whether the
    # name occurs at all, which an imported helper satisfies without the file owning anything.
    if evidence.sources:
        seen_owned: set[str] = set()
        for whole, bare in _SYMBOL_OWNERSHIP_RE.findall(text):
            name = whole or bare
            if not name or name in {"self", "cls"} or name in seen_owned:
                continue
            seen_owned.add(name)
            if not evidence.mentions_symbol(name):
                continue  # already reported above as absent; do not say it twice
            if evidence.defines_symbol(name):
                continue
            defects.append(
                ClaimDefect(
                    kind="symbol_not_defined_here",
                    claim=f"{name}()",
                    contradiction=(
                        "that name appears in the source but is not defined in it — it is used or "
                        "imported, so a claim about how it behaves is not grounded in this audit"
                    ),
                )
            )

    # A cited `file:line`. `sources` holds verbatim text, so line n is decidable exactly — and was
    # never checked. Only a file this audit read to the END can be judged: a citation past the end
    # of a partially-read file is a gap in our own evidence, not a fabrication.
    seen_citations: set[str] = set()
    for pattern in _CITATION_RES:
        for first, second in pattern.findall(text):
            path, raw_line = (second, first) if first.isdigit() else (first, second)
            key = f"{path}:{raw_line}"
            if key in seen_citations:
                continue
            seen_citations.add(key)
            resolved = _resolve_cited_path(path, evidence)
            if not resolved or not evidence.was_read_completely(resolved):
                continue
            try:
                line = int(raw_line)
            except (TypeError, ValueError):
                continue
            total = evidence.line_count(resolved)
            if line < 1 or line > total:
                defects.append(
                    ClaimDefect(
                        kind="line_absent",
                        claim=key,
                        contradiction=(
                            f"`{resolved}` has {total} line(s), so line {line} does not exist"
                        ),
                    )
                )

    if not defects:
        return text, ()

    lines = [
        "",
        "---",
        "**Claims the evidence contradicts** — the analysis above is the model's; these specific "
        "statements were checked against the files this audit actually read and do not hold:",
        "",
    ]
    seen: set[str] = set()
    for defect in defects:
        key = f"{defect.kind}:{defect.claim}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- \"{defect.claim.strip()}\" — {defect.contradiction}.")
    lines.append("")
    lines.append("Treat the rest of the report with corresponding caution.")
    return text + "\n".join(lines), tuple(defects)
