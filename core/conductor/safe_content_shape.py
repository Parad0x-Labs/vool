"""Typed exact-shape contracts for safe in-chat content siblings.

The conductor may preserve an independent code request beside an unavailable physical action, but
the model's raw generation is not itself the deliverable.  This module parses constraints from the
safe clause and seals one Python artifact before composition.  Prose and unrelated fenced blocks
are discarded; invalid syntax, the wrong line count, or failure to print the requested literal are
rejected rather than shipped.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_COUNT = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_PREFIX_COUNT_RE = re.compile(
    rf"\b(?P<count>{_COUNT})\s*[- ]\s*lines?\s+(?:of\s+)?python\s+"
    r"(?:code|script|program|function|snippet)\b",
    re.IGNORECASE,
)
_SUFFIX_COUNT_RE = re.compile(
    rf"\bpython\s+(?:code|script|program|function|snippet)\b[^.!?;\n]{{0,100}}"
    rf"\b(?:in|using|with|of)\s+(?:exactly\s+)?(?P<count>{_COUNT})\s+(?:physical\s+)?lines?\b",
    re.IGNORECASE,
)
_PRINT_TARGET_RE = re.compile(
    r"\bprints?\s+(?:out\s+)?(?:the\s+)?(?:word|string|text|literal|message)?\s*"
    r'["“\'](?P<target>[^"”\'\r\n]{1,100})["”\']',
    re.IGNORECASE,
)
_BARE_PRINT_TARGET_RE = re.compile(
    r"\bprints?\s+(?:out\s+)?(?:the\s+)?(?:word|string|text|literal|message)\s+"
    r"(?P<target>[A-Za-z0-9_.-]{1,100})\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(
    r"```(?P<language>[A-Za-z0-9_+.-]*)[^\S\r\n]*\r?\n(?P<body>.*?)```",
    re.DOTALL,
)
_PYTHON_LANGUAGES = frozenset({"python", "py", "python3"})
_POEM_PREFIX_RE = re.compile(
    rf"\b(?P<count>{_COUNT})\s*[- ]\s*lines?\s+(?:poem|verse)\b"
    r"[^.!?;\n]{0,100}\babout\s+(?P<subject>[^.!?;\n]{1,80})",
    re.IGNORECASE,
)
_POEM_SUFFIX_RE = re.compile(
    rf"\b(?:poem|verse)\b[^.!?;\n]{{0,100}}\babout\s+(?P<subject>[^.!?;\n]{{1,80}}?)"
    rf"\s+(?:in|using|with|of)\s+(?:exactly\s+)?(?P<count>{_COUNT})\s+(?:physical\s+)?lines?\b",
    re.IGNORECASE,
)
_POEM_LINES_OF_RE = re.compile(
    rf"\b(?P<count>{_COUNT})\s+(?:physical\s+)?lines?\s+of\s+(?:a\s+)?(?:poem|verse)\b"
    r"[^.!?;\n]{0,100}\babout\s+(?P<subject>[^.!?;\n]{1,80})",
    re.IGNORECASE,
)
_POEM_META_RE = re.compile(r"\b(?:analy[sz]e|critique|explain|summari[sz]e)\b", re.IGNORECASE)
_SUBJECT_EDGE_WORDS = re.compile(
    r"^(?:a|an|the|my|our|your)\s+|\s+(?:please|only|exactly)$", re.IGNORECASE
)
_MOTHER_FAMILY = frozenset({"mother", "mothers", "mom", "moms", "mum", "mums", "mama"})


@dataclass(frozen=True)
class ExactPythonShape:
    exact_lines: int
    print_target: str = ""


@dataclass(frozen=True)
class SealedPythonArtifact:
    code: str
    exact_lines: int
    print_target: str = ""
    unrelated_output_removed: bool = False


@dataclass(frozen=True)
class ExactPoemShape:
    exact_lines: int
    subject: str


@dataclass(frozen=True)
class SealedPoemArtifact:
    poem: str
    exact_lines: int
    subject: str
    unrelated_output_removed: bool = False


def _count(value: str) -> int | None:
    lowered = str(value or "").casefold()
    if lowered in _COUNT_WORDS:
        return _COUNT_WORDS[lowered]
    try:
        parsed = int(lowered)
    except (TypeError, ValueError):
        return None
    return parsed if 1 <= parsed <= 40 else None


def parse_exact_python_shape(request: str) -> ExactPythonShape | None:
    """Return a shape only for an explicit bounded-line Python deliverable."""

    text = str(request or "")
    match = _PREFIX_COUNT_RE.search(text) or _SUFFIX_COUNT_RE.search(text)
    if match is None:
        return None
    exact_lines = _count(match.group("count"))
    if exact_lines is None:
        return None
    target_match = _PRINT_TARGET_RE.search(text) or _BARE_PRINT_TARGET_RE.search(text)
    target = target_match.group("target").strip() if target_match is not None else ""
    return ExactPythonShape(exact_lines=exact_lines, print_target=target)


def parse_exact_poem_shape(request: str) -> ExactPoemShape | None:
    """Return a shape only for an explicit bounded-line poem with a named subject."""

    text = str(request or "")
    if _POEM_META_RE.search(text):
        return None
    match = (
        _POEM_PREFIX_RE.search(text)
        or _POEM_LINES_OF_RE.search(text)
        or _POEM_SUFFIX_RE.search(text)
    )
    if match is None:
        return None
    exact_lines = _count(match.group("count"))
    subject = " ".join(match.group("subject").strip(" ,\"'“”").split())
    subject = _SUBJECT_EDGE_WORDS.sub("", subject).strip()
    if exact_lines is None or not subject or len(subject) > 80:
        return None
    return ExactPoemShape(exact_lines=exact_lines, subject=subject)


def _printed_literals(tree: ast.Module) -> set[str]:
    printed: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print" and node.args
        ):
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            printed.add(value.value)
    return printed


def _normalize_indirect_target(code: str, tree: ast.Module, target: str) -> str:
    """Rewrite a simple constant-backed ``print(name)`` to an auditable literal print."""

    bindings: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        name, value = statement.targets[0], statement.value
        if isinstance(name, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            bindings[name.id] = value.value
    lines = code.splitlines()
    for statement in tree.body:
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)):
            continue
        call = statement.value
        if not (
            isinstance(call.func, ast.Name)
            and call.func.id == "print"
            and len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Name)
            and bindings.get(call.args[0].id) == target
        ):
            continue
        index = statement.lineno - 1
        indentation = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
        lines[index] = f"{indentation}print({target!r})"
        return "\n".join(lines)
    return code


def seal_python_artifact(raw_generation: str, shape: ExactPythonShape) -> SealedPythonArtifact | None:
    """Extract, validate and seal one Python artifact from an untrusted model draft."""

    raw = str(raw_generation or "").strip()
    if not raw:
        return None
    fences = list(_FENCE_RE.finditer(raw))
    if fences:
        python_fences = [match for match in fences if match.group("language").strip().casefold() in _PYTHON_LANGUAGES]
        if len(python_fences) != 1:
            return None
        selected = python_fences[0]
        code = selected.group("body").strip("\r\n")
        unrelated_removed = raw != selected.group(0).strip()
    else:
        code = raw.strip("\r\n")
        unrelated_removed = False

    lines = code.splitlines()
    if len(lines) != shape.exact_lines or any(not line.strip() for line in lines):
        return None
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError):
        return None
    if shape.print_target and shape.print_target not in _printed_literals(tree):
        code = _normalize_indirect_target(code, tree, shape.print_target)
        try:
            tree = ast.parse(code, mode="exec")
        except (SyntaxError, ValueError):
            return None
        if shape.print_target not in _printed_literals(tree):
            return None
    return SealedPythonArtifact(
        code=code,
        exact_lines=shape.exact_lines,
        print_target=shape.print_target,
        unrelated_output_removed=unrelated_removed,
    )


def synthesize_exact_python_artifact(shape: ExactPythonShape) -> SealedPythonArtifact | None:
    """Build a deterministic exact-line script when the complete literal contract is known."""

    if not shape.print_target or not 1 <= shape.exact_lines <= 40:
        return None
    code = "\n".join(f"print({shape.print_target!r})" for _ in range(shape.exact_lines))
    return seal_python_artifact(code, shape)


def exact_python_retry_prompt(request: str, shape: ExactPythonShape) -> str:
    target = f" One line must directly call print({shape.print_target!r})." if shape.print_target else ""
    return (
        f"Original safe in-chat request: {request}\n"
        f"Return exactly one Python code block containing exactly {shape.exact_lines} non-empty "
        f"physical code lines.{target} Include no prose and no second code block."
    )


def _subject_terms(subject: str) -> tuple[frozenset[str], bool]:
    terms = {item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z'-]*", subject)}
    if terms & _MOTHER_FAMILY:
        return _MOTHER_FAMILY, True
    return frozenset(terms), False


def _poem_candidate(raw: str) -> tuple[str, bool] | None:
    fences = list(_FENCE_RE.finditer(raw))
    if fences:
        poem_fences = [
            match
            for match in fences
            if match.group("language").strip().casefold() in {"", "text", "plaintext", "poem"}
        ]
        if len(poem_fences) != 1:
            return None
        selected = poem_fences[0]
        return selected.group("body").strip("\r\n"), raw != selected.group(0).strip()
    return raw.strip("\r\n"), False


def seal_poem_artifact(raw_generation: str, shape: ExactPoemShape) -> SealedPoemArtifact | None:
    """Extract exactly N non-empty poem lines and require the named subject in the poem."""

    raw = str(raw_generation or "").strip()
    if not raw:
        return None
    candidate = _poem_candidate(raw)
    if candidate is None:
        return None
    poem, removed = candidate
    lines = poem.splitlines()
    if len(lines) != shape.exact_lines or any(not line.strip() for line in lines):
        return None
    lowered = {item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z'-]*", poem)}
    subject_terms, accepts_synonym = _subject_terms(shape.subject)
    subject_present = bool(subject_terms & lowered) if accepts_synonym else subject_terms <= lowered
    if not subject_terms or not subject_present:
        return None
    return SealedPoemArtifact(
        poem="\n".join(line.strip() for line in lines),
        exact_lines=shape.exact_lines,
        subject=shape.subject,
        unrelated_output_removed=removed,
    )


def exact_poem_retry_prompt(request: str, shape: ExactPoemShape) -> str:
    return (
        f"Original safe in-chat request: {request}\n"
        f"Return exactly {shape.exact_lines} non-empty physical poem lines about "
        f"{shape.subject!r}. At least one poem line must explicitly name that subject. "
        "Include no title, explanation, prose wrapper, markdown fence, or extra line."
    )


__all__ = [
    "ExactPoemShape",
    "ExactPythonShape",
    "SealedPoemArtifact",
    "SealedPythonArtifact",
    "exact_poem_retry_prompt",
    "exact_python_retry_prompt",
    "parse_exact_poem_shape",
    "parse_exact_python_shape",
    "seal_poem_artifact",
    "seal_python_artifact",
    "synthesize_exact_python_artifact",
]
