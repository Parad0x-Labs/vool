from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Web markup and styles were absent, which made a static site invisible to the audit: measured
# 2026-07-28 on a real landing-page project, `is_source_path("app-landing/index.html")` was False,
# so a workspace of HTML/CSS reported "14 JS source files" from a few stray scripts and the file
# the owner actually named could not be audited at all. HTML carries inline handlers, inline
# scripts and CSP decisions — exactly the security surface that was being asked about.
SOURCE_SUFFIXES = frozenset(
    {
        ".astro",
        ".bash",
        ".c",
        ".css",
        ".htm",
        ".html",
        ".scss",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".ps1",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sol",
        ".sql",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
        ".zsh",
    }
)
TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|specs?)(?:/|$)|(?:^|[/_.-])(?:test|spec)(?:[/_.-]|$)",
    re.IGNORECASE,
)
_REQUIREMENT_PIN_RE = re.compile(r"^[A-Za-z0-9_.-]+\s*(?:===|==)\s*[^;#\s]+")
_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class SourceFinding:
    severity: str
    path: str
    line: int
    title: str
    evidence: str
    impact: str
    recommendation: str
    rule_id: str


@dataclass(frozen=True)
class SourceAnalysis:
    findings: tuple[SourceFinding, ...]
    parsed_files: int
    parse_failures: int


# Directories the RUNTIME writes into. An audit that reads these is reading its own previous
# output, which is not evidence about the project — measured live 2026-08-01: an audit of one file
# pulled in EIGHT `generated/api-apache-…/test_…_bug.py` artifacts that earlier failed runs had
# written, handed them to the model as "existing tests", and the model went on renominating exactly
# the claim those artifacts encode. The audit was grading the project on its own discarded homework.
_RUNTIME_ARTIFACT_DIRS = ("generated",)


def is_runtime_artifact_path(path: str) -> bool:
    """True for a path under a directory this runtime generates into."""
    parts = PurePosixPath(str(path or "").replace("\\", "/")).parts
    return any(part in _RUNTIME_ARTIFACT_DIRS for part in parts)


def is_source_path(path: str) -> bool:
    if is_runtime_artifact_path(path):
        return False
    return PurePosixPath(str(path or "")).suffix.lower() in SOURCE_SUFFIXES


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_RE.search(str(path or "")))


def analyze_sources(
    sources: dict[str, str],
    *,
    auxiliary_files: dict[str, str] | None = None,
    all_paths: tuple[str, ...] = (),
    incomplete_paths: tuple[str, ...] = (),
) -> SourceAnalysis:
    findings: list[SourceFinding] = []
    parsed_files = 0
    parse_failures = 0
    incomplete = frozenset(incomplete_paths)
    for path, content in sorted(sources.items()):
        # A prefix of a valid source file is not a valid compilation unit. Parsing it invents
        # syntax errors at the cut boundary, which is worse than admitting partial coverage.
        if path in incomplete:
            continue
        if PurePosixPath(path).suffix.lower() == ".py":
            file_findings, parsed = _analyze_python(path, content)
            findings.extend(file_findings)
            parsed_files += int(parsed)
            parse_failures += int(not parsed)
        else:
            findings.extend(_analyze_generic(path, content))

    extras = dict(auxiliary_files or {})
    requirements = extras.get("requirements.txt", "")
    if requirements:
        for line_number, raw_line in enumerate(requirements.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith(("#", "-", "git+", "http://", "https://")):
                continue
            if not _REQUIREMENT_PIN_RE.match(line):
                findings.append(
                    SourceFinding(
                        severity="P2",
                        path="requirements.txt",
                        line=line_number,
                        title="Dependency is not reproducibly pinned",
                        evidence=line[:220],
                        impact="Fresh installs can silently resolve different dependency versions and behavior.",
                        recommendation="Pin an exact reviewed version or use a lockfile with hashes.",
                        rule_id="dependency-unpinned",
                    )
                )

    source_paths = tuple(path for path in all_paths if is_source_path(path) and not is_test_path(path))
    test_paths = tuple(path for path in all_paths if is_test_path(path) and is_source_path(path))
    if source_paths and not test_paths:
        findings.append(
            SourceFinding(
                severity="P2",
                path=".",
                line=0,
                title="No automated test source was discovered",
                evidence=f"{len(source_paths)} source file(s), 0 test files in the inspected inventory",
                impact="Compression, decoding, malformed-input, and round-trip regressions have no executable safety net.",
                recommendation="Add focused unit tests plus malformed-input and round-trip fixtures before optimizing further.",
                rule_id="tests-missing",
            )
        )

    unique: dict[tuple[str, str, int], SourceFinding] = {}
    for finding in findings:
        unique.setdefault((finding.rule_id, finding.path, finding.line), finding)
    ordered = sorted(
        unique.values(),
        key=lambda item: (_SEVERITY_ORDER.get(item.severity, 9), item.path, item.line, item.rule_id),
    )
    return SourceAnalysis(tuple(ordered), parsed_files, parse_failures)


def _analyze_python(path: str, content: str) -> tuple[list[SourceFinding], bool]:
    lines = content.splitlines()
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as exc:
        line_number = max(1, int(exc.lineno or 1))
        return (
            [
                SourceFinding(
                    severity="P0",
                    path=path,
                    line=line_number,
                    title="Python source does not parse",
                    evidence=str(exc.msg or "syntax error"),
                    impact="The affected module cannot be imported or executed.",
                    recommendation="Correct the syntax error and add a syntax/test gate.",
                    rule_id="python-syntax-error",
                )
            ],
            False,
        )

    findings: list[SourceFinding] = []
    for node in ast.walk(tree):
        line_number = max(1, int(getattr(node, "lineno", 1) or 1))
        evidence = _line(lines, line_number)

        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append(
                    _finding(
                        "P1",
                        path,
                        line_number,
                        "Bare exception handler hides every failure",
                        evidence,
                        "Interrupts and programming errors are swallowed, so corrupt or partial output can look successful.",
                        "Catch the narrow expected exception, preserve context, and report or re-raise unexpected failures.",
                        "python-bare-except",
                    )
                )
            elif _is_exception_type(node.type, "Exception") and _body_only_suppresses(node.body):
                findings.append(
                    _finding(
                        "P2",
                        path,
                        line_number,
                        "Broad exception is reduced to logging or continuation",
                        evidence,
                        "Operational and programming failures are conflated, and callers cannot reliably distinguish degraded output.",
                        "Catch expected exception types and return or raise a typed failure that callers must handle.",
                        "python-silent-broad-except",
                    )
                )

        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        keyword_names = {str(item.arg or "") for item in node.keywords}

        if call_name in {"eval", "exec", "builtins.eval", "builtins.exec", "os.system"}:
            findings.append(
                _finding(
                    "P1",
                    path,
                    line_number,
                    "Dynamic code or shell execution",
                    evidence,
                    "Untrusted or incorrectly escaped input can become arbitrary code execution.",
                    "Remove dynamic execution or enforce a fixed allowlisted operation contract.",
                    "python-dynamic-execution",
                )
            )
        if call_name.startswith("subprocess.") and call_name.rsplit(".", 1)[-1] in {
            "call",
            "check_call",
            "check_output",
            "Popen",
            "run",
        }:
            shell_true = any(
                item.arg == "shell" and isinstance(item.value, ast.Constant) and item.value.value is True
                for item in node.keywords
            )
            if shell_true:
                findings.append(
                    _finding(
                        "P1",
                        path,
                        line_number,
                        "Subprocess enables shell interpretation",
                        evidence,
                        "Arguments can be reinterpreted by a shell, increasing command-injection risk.",
                        "Pass an argument vector with shell disabled.",
                        "python-subprocess-shell",
                    )
                )
            if call_name != "subprocess.Popen" and "timeout" not in keyword_names:
                findings.append(
                    _finding(
                        "P2",
                        path,
                        line_number,
                        "External command has no timeout",
                        evidence,
                        "A renderer or helper process can hang the entire operation indefinitely.",
                        "Set a bounded timeout and terminate the child cleanly on expiry.",
                        "python-subprocess-no-timeout",
                    )
                )
        if call_name in {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads"}:
            findings.append(
                _finding(
                    "P1",
                    path,
                    line_number,
                    "Unsafe object deserialization",
                    evidence,
                    "Loading attacker-controlled serialized objects can execute code or corrupt process state.",
                    "Use a non-executable validated format and enforce size/schema limits.",
                    "python-unsafe-deserialization",
                )
            )
        if call_name == "yaml.load" and not ({"Loader", "loader"} & keyword_names):
            findings.append(
                _finding(
                    "P1",
                    path,
                    line_number,
                    "YAML load has no safe loader",
                    evidence,
                    "Untrusted YAML may instantiate unsafe objects.",
                    "Use yaml.safe_load or an explicitly safe loader.",
                    "python-unsafe-yaml",
                )
            )
        if call_name in {
            "requests.delete",
            "requests.get",
            "requests.head",
            "requests.patch",
            "requests.post",
            "requests.put",
            "requests.request",
        } and "timeout" not in keyword_names:
            findings.append(
                _finding(
                    "P2",
                    path,
                    line_number,
                    "Network request has no timeout",
                    evidence,
                    "A stalled peer can block the process indefinitely.",
                    "Supply explicit connect and read timeouts.",
                    "python-request-no-timeout",
                )
            )
        if call_name == "hashlib.md5":
            # MD5 is still reasonable for non-adversarial bucketing or color selection. Only flag
            # the materially riskier shape: a digest visibly truncated into a persistent identity.
            if re.search(r"\.hexdigest\(\)\s*\[\s*:\s*\d+\s*\]", evidence):
                findings.append(
                    _finding(
                        "P2",
                        path,
                        line_number,
                        "Truncated MD5 digest is used as an identifier",
                        evidence,
                        "Distinct objects can receive the same identifier if downstream code assumes this shortened digest is unique.",
                        "Use a full SHA-256 digest, or document and enforce collision handling at the identity boundary.",
                        "python-md5-identifier",
                    )
                )
        if call_name == "lzma.decompress":
            findings.append(
                _finding(
                    "P2",
                    path,
                    line_number,
                    "Compressed input is decompressed without an output bound",
                    evidence,
                    "A small malicious archive can expand until memory is exhausted.",
                    "Stream with a maximum output budget and reject oversized metadata before allocation.",
                    "python-unbounded-decompression",
                )
            )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end_line = int(getattr(node, "end_lineno", node.lineno) or node.lineno)
        span = end_line - int(node.lineno) + 1
        if span >= 100:
            findings.append(
                _finding(
                    "P3",
                    path,
                    int(node.lineno),
                    "Oversized function impairs reviewability",
                    _line(lines, int(node.lineno)),
                    "Large functions make error handling, invariants, and test coverage harder to reason about.",
                    "Split parsing, transformation, I/O, and reporting into independently testable units.",
                    "python-large-function",
                )
            )

    return findings, True


def _analyze_generic(path: str, content: str) -> list[SourceFinding]:
    findings: list[SourceFinding] = []
    patterns = (
        (
            re.compile(r"\beval\s*\("),
            "P1",
            "Dynamic evaluation in source",
            "Untrusted input may become executable code.",
            "Remove eval or replace it with a typed parser.",
            "generic-eval",
        ),
        (
            re.compile(r"\bchild_process\.(?:exec|execSync)\s*\("),
            "P1",
            "Shell-based child process execution",
            "String commands are vulnerable to quoting mistakes and command injection.",
            "Use spawn/execFile with a fixed executable and argument array.",
            "generic-shell-exec",
        ),
    )
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        for pattern, severity, title, impact, recommendation, rule_id in patterns:
            if pattern.search(raw_line):
                findings.append(
                    _finding(
                        severity,
                        path,
                        line_number,
                        title,
                        raw_line.strip(),
                        impact,
                        recommendation,
                        rule_id,
                    )
                )
    return findings


def _body_only_suppresses(body: list[ast.stmt]) -> bool:
    return bool(body) and all(
        isinstance(item, (ast.Pass, ast.Continue))
        or (
            isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Call)
            and _call_name(item.value.func) in {"print", "logging.debug", "logging.info", "logging.warning"}
        )
        for item in body
    )


def _is_exception_type(node: ast.expr, name: str) -> bool:
    return _call_name(node) in {name, f"builtins.{name}"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _line(lines: list[str], line_number: int) -> str:
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()[:220]
    return ""


def _finding(
    severity: str,
    path: str,
    line: int,
    title: str,
    evidence: str,
    impact: str,
    recommendation: str,
    rule_id: str,
) -> SourceFinding:
    return SourceFinding(
        severity=severity,
        path=path,
        line=max(0, int(line)),
        title=title,
        evidence=str(evidence or "").strip()[:220],
        impact=impact,
        recommendation=recommendation,
        rule_id=rule_id,
    )


__all__ = [
    "SOURCE_SUFFIXES",
    "SourceAnalysis",
    "SourceFinding",
    "analyze_sources",
    "is_source_path",
    "is_test_path",
]
