"""Agentic workspace app-builder.

Turns a "create a small app in a folder, with tests" request into REAL files: ask the local model
for the file LIST (a small JSON array of paths -> clean, no code to mis-escape), then generate each
file's content as RAW text (no JSON escaping of code at all), write each into the target folder
(workspace-confined), run the tests, and on failure regenerate the source files with the error fed
back -- a bounded number of times. Reports the created files + the real test output; it never claims
success it did not observe (an app whose tests still fail after the fix budget is reported as such).

Per-file generation is deliberate: a small local model reliably emits a short path list and a single
raw file, but is unreliable at escaping code inside one big JSON blob (a `\\'` in a Python string is
invalid JSON and breaks the whole plan).

Dependency-injected for testing without a live model or a real sandbox:
  - generate_fn(prompt: str) -> str
  - run_tool_fn(intent: str, arguments: dict, *, trusted_local_only: bool = False) -> ToolOutcome
    (execute_runtime_tool wrapper)
"""
from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.agent_runtime.builder import mutation_scope as mutation_scope_module
from core.agent_runtime.builder.mutation_scope import MutationScope
from core.agent_runtime.builder.named_file_build import workspace_relative_path

MAX_FILES = 16
MAX_FILE_BYTES = 64_000
MAX_FIX_ROUNDS = 3

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass
class ToolOutcome:
    """Minimal shape returned by the injected run_tool_fn (mirrors RuntimeExecutionResult)."""

    ok: bool
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppBuildReport:
    handled: bool
    target_dir: str
    files_written: list[str] = field(default_factory=list)
    tests_ran: bool = False
    tests_passed: bool = False
    test_output: str = ""
    fix_rounds: int = 0
    error: str = ""
    # "Prove the bug by writing a test that FAILS" is a request this lane could not express, so it
    # did the opposite of what was asked: the fix loop below regenerates code specifically to turn a
    # failing test green. Measured 2026-08-01 -- asked to prove a bug, the lane produced a suite that
    # exits 0 and reported success, which is a fabricated proof. A run that was SUPPOSED to fail and
    # did not is a failed proof, not a passing build.
    expected_test_outcome: str = "unspecified"  # "pass" | "fail" | "unspecified"
    # The real exit code, when the runner surfaced one. Pass/fail used to be scraped out of stdout
    # because `sandbox.run_command` reports "executed" even for a failing suite -- but the code is
    # right there in the outcome details and was simply never read.
    test_returncode: int | None = None
    proof_failed: bool = False
    # WHY a prove-it run did not prove anything, so the reply can say which of the four states this
    # is instead of collapsing them into one wrong sentence: "" (the proof holds), "passed" (green,
    # so the claim is unreproduced), "errored" (the test blew up before asserting) or "did_not_run"
    # (no command ever executed -- it was waiting on approval).
    proof_note: str = ""
    # What the operator was actually asking about. The builder's only subject channel is the raw
    # sentence, which is why "prove the bug in the Apache codec" produced an unrelated security.py.
    subject_paths: tuple[str, ...] = ()
    # Rendering detail, so the reply can report size and skips rather than a bare list of names.
    file_lines: dict[str, int] = field(default_factory=dict)
    files_skipped: list[str] = field(default_factory=list)
    test_command: str = ""
    # The mutation authority this build ran under, and what it refused. A build that expanded one
    # named file into a four-file scaffold produced a receipt that looked entirely successful,
    # because nothing in the report recorded what had actually been ASKED for. See
    # builder/mutation_scope.py.
    scope_kind: str = mutation_scope_module.OPEN
    authorized_paths: tuple[str, ...] = ()
    paths_refused: list[str] = field(default_factory=list)
    commands_refused: list[str] = field(default_factory=list)


def _confine_relative_path(raw: str, *, target_dir: str, workspace_root: str = "") -> str | None:
    """Return ``target_dir/<clean-relative>``, or None for an unsafe path (outside, drive, ``..``).

    An absolute path INSIDE the workspace is rebased rather than refused. When the request names its
    destination in full -- "create /Users/me/ws/f1/palindrome.py with ..." -- the model repeats that
    absolute path in its file list, which is the obvious thing to do. Discarding all of them left
    zero usable paths and the build answered "the model did not return a usable file list" for a
    request it could have built. Outside the workspace it is still refused: confinement is the point.
    """
    candidate = str(raw or "").strip().replace("\\", "/")
    if not candidate:
        return None
    if candidate.startswith("/") or ":" in candidate:
        rebased = workspace_relative_path(candidate, workspace_root=workspace_root)
        if not rebased:
            return None
        candidate = rebased
    base = target_dir.rstrip("/") + "/"
    if candidate.startswith(base):
        candidate = candidate[len(base):]
    segments = [seg for seg in candidate.split("/") if seg not in ("", ".")]
    if not segments or any(seg == ".." or not _SAFE_SEGMENT_RE.match(seg) for seg in segments):
        return None
    # Validation FIRST, then flatten. Flattening before this check turned "../secret" into
    # "<target>/secret" -- taking the last segment discards the very `..` that makes it an escape.
    #
    # The project is FLAT: `_FLAT_IMPORT_RULE` tells the model so and tells the test module to
    # import by bare filename, so a nested source file cannot be imported by the tests written for
    # it. A live build of "build <ws>/g8/clamp.py ..." came back with `build/clamp.py` -- the file
    # existed, at a path the request never named, while the generated README described it at the top
    # level. A sub-directory is dropped rather than honoured, because honouring one produces a
    # project whose own tests cannot import it.
    if len(segments) > 1:
        segments = segments[-1:]
    joined = posixpath.join(target_dir.rstrip("/"), *segments)
    if posixpath.normpath(joined) != joined or not joined.startswith(target_dir.rstrip("/") + "/"):
        return None
    return joined


def _directories_to_create(
    paths: list[str], *, target_dir: str, scope: MutationScope
) -> list[str]:
    """The directories this build may create, in the order they must be made.

    Under an OPEN scope that is the single target directory, exactly as before. Under an EXACT
    scope it is the parent folders the authorized files actually need -- so "create src/foo.py"
    still makes `src/`, "create notes.txt" makes nothing at all, and neither can make a folder the
    request never named. The workspace root is never "created": it is where the workspace is.
    """

    if not scope.is_exact:
        return [target_dir] if str(target_dir or "").strip() else []
    wanted: list[str] = []
    for path in paths:
        parent = posixpath.dirname(str(path or "").strip("/")).strip("/")
        while parent:
            if parent not in wanted and scope.authorizes_directory(parent):
                wanted.append(parent)
            parent = posixpath.dirname(parent).strip("/")
    # Shallowest first: a nested folder cannot be made before its parent.
    return sorted(wanted, key=lambda item: (item.count("/"), item))


def strip_reasoning_monologue(text: str) -> str:
    """Drop a reasoning monologue that a thinking model left in its answer text.

    A thinking model's chat template opens ``<think>`` itself, so the reasoning arrives with only a
    CLOSING tag: ``...Final answer.</think>\\n\\n<the real answer>``. The reasoning is one leading
    block, so it ends at the FIRST ``</think>``; a later one belongs to the answer.

    Constraints. A reply whose reasoning was truncated before its closing tag carries no marker at
    all and is indistinguishable from a real answer, so callers must also keep Ollama's thinking
    parser on (see build_ollama_generate_fn) rather than rely on this alone. And a reply that opens
    the tag itself is content ABOUT the tags -- a file that parses them, say -- not reasoning, so it
    is left whole rather than silently truncated at a tag the user asked for.
    """
    body = str(text or "")
    opener, closer = "<think>", "</think>"
    index = body.find(closer)
    if index == -1:
        return body
    if opener in body[:index] and not body.lstrip().startswith(opener):
        return body
    return body[index + len(closer) :].strip("\n")


def _matching_bracket(text: str, start: int) -> int:
    """Index of the bracket closing the one at `start`, or -1.

    `rfind("]")` used to stand in for this, and it is wrong the moment the model keeps generating
    after the array it was asked for. Measured 2026-08-01 on a real turn: the reply was
    `["security.py", "test_security.py", "README.md"]import hmac\\n...` followed by the file bodies,
    and those bodies contain `list[str]` and `Optional[bytes]` -- so `rfind("]")` landed deep inside
    the generated Python, `json.loads` was handed the array PLUS a slab of code, it failed, the
    line fallback rejected every line for containing spaces, and the build reported `handled=False`.
    The turn then fell out of the build lane and printed the raw completion to the operator.

    Scans forward tracking depth, skipping string literals and their escapes, so trailing prose or
    code after a well-formed array is simply ignored rather than swallowed.
    """

    opener = text[start] if 0 <= start < len(text) else ""
    closer = {"[": "]", "{": "}"}.get(opener, "")
    if not closer:
        return -1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def extract_path_list(text: str) -> list[str]:
    """Parse a model reply into a list of relative file paths. Accepts a JSON array, a JSON object
    whose keys are paths, or a fenced version of either; falls back to line-by-line path-looking
    tokens. Returns [] when nothing usable is found."""
    raw = strip_reasoning_monologue(text).strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    # Try a JSON array first, then a JSON object's keys.
    for start, _end in (("[", "]"), ("{", "}")):
        i = raw.find(start)
        j = _matching_bracket(raw, i) if i != -1 else -1
        if i != -1 and j > i:
            try:
                parsed = json.loads(raw[i : j + 1])
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                return [str(p) for p in parsed if isinstance(p, str) and p.strip()]
            if isinstance(parsed, dict):
                return [str(p) for p in parsed if str(p).strip()]
    # Fallback: lines that look like a path (contain a dot or slash, no spaces).
    out: list[str] = []
    for line in raw.splitlines():
        token = line.strip().strip("-*`\"', ").strip()
        if token and " " not in token and ("." in token or "/" in token):
            out.append(token)
    return out


def strip_code_fences(text: str) -> str:
    """The file content a model meant to emit: its reasoning monologue and, if the whole reply is a
    single ``` fenced block, the fence, are removed. Otherwise the reply is returned as-is."""
    body = strip_reasoning_monologue(text)
    match = re.match(r"^\s*```[^\n]*\n(.*)\n```\s*$", body, re.DOTALL)
    return match.group(1) if match else body.strip("\n")


_LIST_PROMPT = (
    "List the files for this project. Return ONLY a JSON array of relative file path strings -- no "
    "file contents, no prose, no explanation. Keep paths relative (no leading slash, no `..`). Include "
    "a `unittest` test module named test_*.py and a short README.md. Python module filenames MUST be "
    "valid identifiers -- letters, digits and underscores only, never hyphens (use todo.py, not "
    "to-do.py). The test module must import the app module by that exact underscore name.\n\n"
    "Project:\n{request}\n"
)
_FLAT_IMPORT_RULE = (
    "The project is FLAT: all files sit directly in one folder, there are no packages or "
    "subdirectories. To use code from another file, import it by its bare filename without `.py` "
    "(for `todo.py` write `from todo import TodoApp` or `import todo` -- NEVER `from todo_app.todo "
    "import ...` or any invented package path). The test file MUST import the app module this way. "
    "A test_*.py file MUST use the stdlib `unittest` framework: `import unittest` at the top, tests as "
    "methods named test_* inside a class that subclasses `unittest.TestCase`, and end with "
    "`if __name__ == '__main__':\\n    unittest.main()`. Do NOT write bare pytest-style `def test_...()` "
    "functions -- `python -m unittest` cannot run those. Import EVERY module you use at the top of the "
    "file (e.g. `import json` if you call json.load, `import os`, `from datetime import datetime`)."
)
_FILE_PROMPT = (
    "Generate the COMPLETE contents of exactly one file for this project: `{path}`.\n"
    "Return ONLY the raw file content -- no explanation, no markdown fences, no JSON, no leading or "
    "trailing commentary. It must be complete and correct. The project contains exactly these files: "
    "{paths}. " + _FLAT_IMPORT_RULE + "\nUse only the standard library unless the request says "
    "otherwise.\n\nProject requirements:\n{request}\n"
)
# The file prompt for a request that named exactly one file. `_FILE_PROMPT` describes a PROJECT --
# it lists sibling files and carries the flat-import/unittest rules -- and handing that to a model
# asked for one `notes.txt` invites it to write the project those rules describe. Nothing here can
# widen the write set (the authorization gate below decides that), but a prompt that talks about a
# project produces file CONTENT that talks about a project too.
_EXACT_FILE_PROMPT = (
    "Generate the COMPLETE contents of exactly one file: `{path}`.\n"
    "Return ONLY the raw file content -- no explanation, no markdown fences, no JSON, no leading or "
    "trailing commentary. Write this one file and nothing else: do not invent, describe or refer to "
    "any other file, and do not turn this into a project.\n\nRequest:\n{request}\n"
)
_FIX_PROMPT = (
    "The project's tests FAILED. Return ONLY the corrected raw content of the single file `{path}` "
    "(no fences, no prose). If this file needs no change, return it unchanged. The project files are: "
    "{paths}. " + _FLAT_IMPORT_RULE + "\n\nTest command: {command}\nTest output:\n{output}\n\n"
    "Current `{path}`:\n{content}\n"
)


def _pick_test_command(paths: list[str], *, target_dir: str = "") -> str | None:
    """The command that runs THIS build's tests -- named, not discovered.

    `unittest discover -p 'test*.py'` collects every matching file in the directory, and an unrooted
    build directory is derived from a digest of the request text, so asking the same question twice
    lands in the same folder on purpose (a retry should overwrite its own attempt). Measured
    2026-08-01: attempt one wrote `test_auth.py`, attempt two wrote `test_app.py`, discover ran both,
    and the reported output carried a password-timing suite from a different question while the
    receipt named only the two files attempt two had written. Naming the modules keeps the run and
    the receipt describing the same build.
    """

    modules: list[str] = []
    for path in paths:
        name = posixpath.basename(path)
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        relative = posixpath.relpath(path, target_dir) if target_dir else name
        modules.append(relative[: -len(".py")].replace("/", "."))
    if modules:
        # Sorted so the same file set always produces the same command -- a receipt that changes
        # wording between identical runs reads as a different build.
        return "python -m unittest -v " + " ".join(sorted(dict.fromkeys(modules)))
    if any(posixpath.basename(p) == "package.json" for p in paths):
        return "npm test"
    return None


def _source_files_to_fix(file_map: dict[str, str]) -> list[str]:
    """Files worth regenerating on a test failure: python sources (incl. tests), never the README."""
    return [p for p in file_map if p.endswith(".py")]


def _render_impl_for_prompt(impl_map: dict[str, str], *, target_rel: str, limit_bytes: int = 8000) -> str:
    """The already-written implementation files, for grounding test generation in the real API."""
    parts: list[str] = []
    used = 0
    for path, content in impl_map.items():
        rel = posixpath.relpath(path, target_rel)
        block = f"--- {rel} ---\n{content}\n"
        if used + len(block) > limit_bytes:
            break
        used += len(block)
        parts.append(block)
    return "".join(parts)


_RAN_COUNT_RE = re.compile(r"^Ran\s+(\d+)\s+tests?\b", re.IGNORECASE)


def tests_actually_ran(output: str) -> bool:
    """Whether the run contained any tests at all.

    ``python -m unittest discover`` on a project with no test file prints "Ran 0 tests in 0.000s"
    followed by "OK", and the verdict parser below reads that OK as a pass. A live QA build did
    exactly that: the model wrote the module and the README but no test module, and the reply said
    "Tests passed. (Ran 0 tests in 0.000s | OK)". Nothing was verified, and the sentence claiming
    otherwise is the one thing this lane must never produce.
    """
    for line in (ln.strip() for ln in str(output or "").splitlines()):
        match = _RAN_COUNT_RE.match(line)
        if match:
            return int(match.group(1)) > 0
    # No count line at all (a non-unittest runner): fall back to the verdict, which is all there is.
    return True


_VERDICT_COUNTS_RE = re.compile(r"^FAILED\s*\((.*)\)\s*$")


def unittest_failure_counts(output: str) -> tuple[int, int]:
    """``(failures, errors)`` from unittest's verdict line.

    A test that FAILS reached its assertion and the assertion was false -- that is a reproduction of
    something. A test that ERRORS blew up on the way there: a missing fixture, a bad import, a typo
    in the test itself. The subject is not implicated either way.
    """

    failures = 0
    errors = 0
    for line in (ln.strip() for ln in str(output or "").splitlines()):
        match = _VERDICT_COUNTS_RE.match(line)
        if not match:
            continue
        for part in match.group(1).split(","):
            key, _, value = part.strip().partition("=")
            if not value.strip().isdigit():
                continue
            if key.strip() == "failures":
                failures += int(value)
            elif key.strip() == "errors":
                errors += int(value)
    return failures, errors


def unittest_verdict_present(output: str) -> bool:
    """Whether a unittest run got far enough to report a verdict at all.

    ``tests_actually_ran`` treats a MISSING "Ran N tests" line as "some other runner, trust the exit
    code". That fallback is right for ``npm test`` and catastrophic for ``python -m unittest``: a
    test module with a SyntaxError, a bad import or a missing dependency never reaches the runner,
    so it prints a traceback, no "Ran" line and no "FAILED (...)" line, and exits 1. Under the old
    rule that combination scored as a PROOF — non-zero exit, no errors counted, no FAILED line —
    which is a generated test crashing on itself being reported as a reproduced bug in someone
    else's code. Measured while driving acceptance gate C on the frozen fixture.
    """
    return any(_RAN_COUNT_RE.match(ln.strip()) for ln in str(output or "").splitlines())


def proof_holds(*, output: str, returncode: int | None, unittest_runner: bool = False) -> bool:
    """Whether a prove-the-bug run actually reproduced anything.

    The old rule was "the suite is not green, so the bug is proven", which is how the live receipt
    on 2026-08-01 opened with "✅ **Bug reproduced.** The test fails as required" over a run that
    reproduced nothing. Two ways that sentence was false in one turn:

    * The run exited 1 on ``FAILED (errors=2)`` -- one test blew up because its own fixture was
      never created, the other belonged to a password-timing suite an earlier attempt had left in the
      directory. Neither reached an assertion about the subject.
    * The round before it, the command had not executed at all: it was holding at "Manual mode
      requires approval for this exact action". With no returncode the verdict was scraped from the
      approval preview, which contains neither "OK" nor "FAILED", and a non-pass was read as a proof.

    So a proof requires positive evidence: the command ran, tests were collected, and at least one of
    them FAILED. A runner that reports no unittest verdict at all (``npm test``) keeps the older,
    weaker signal -- a non-zero exit -- because that is the only evidence it offers.
    """

    if returncode is None or returncode == 0:
        return False
    if unittest_runner and not unittest_verdict_present(output):
        # A unittest command that produced no verdict line never reached the runner. Whatever
        # exited 1, it was not an assertion about the subject.
        return False
    if not tests_actually_ran(output):
        return False
    failures, errors = unittest_failure_counts(output)
    if failures > 0:
        return True
    return errors == 0 and not any(
        ln.strip().startswith("FAILED") for ln in str(output or "").splitlines()
    )


def _file_confinement_fault(confined_path: str, *, source_context: dict[str, Any] | None) -> None:
    """File the typed fault for a write this gate just refused. Best-effort, never raises.

    The gate owns the confinement mapping: the refusal is decided here, so the record is
    minted here (and the security-event plane opens its observation from the catalog's
    declaration -- no second wiring to forget).
    """
    try:
        from core.faults.recorder import identity_from_context, record_fault
        from core.faults.records import FaultRecord

        turn_key, session_id = identity_from_context(source_context)
        record_fault(
            FaultRecord.for_code(
                "confinement_refusal",
                authority="core.agent_runtime.builder.app_builder",
                turn_key=turn_key,
                session_id=session_id,
                dedupe=f"paths:{confined_path}",
                context={
                    "target_name": confined_path,
                    "status": "refused",
                    "reason": "outside the authorized mutation scope",
                    "decision": "confinement_gate",
                },
            )
        )
    except Exception:
        pass


def _tests_passed_from_output(output: str) -> bool:
    """Whether the unittest run actually PASSED -- parsed from its verdict, not the sandbox's
    "the command executed" flag (a failing `python -m unittest` still exits 'executed')."""
    lines = [ln.strip() for ln in str(output or "").splitlines()]
    if any(ln.startswith("FAILED") for ln in lines):
        return False
    if not tests_actually_ran(output):
        return False
    return any(ln == "OK" or ln.startswith(("OK ", "OK(")) for ln in lines)


# What the operator is actually asking ABOUT, spliced into both generation prompts.
#
# `subject_paths` reached the build report but never reached the MODEL, so passing it changed
# nothing a person could see. Measured 2026-08-01: asked to prove a bug in
# `api/apache/liquefy_apache_repetition_v1.py`, the lane generated an unrelated security project
# about SQL injection and command injection -- twice -- because the only thing the generator was
# ever told was the raw sentence, and "prove the bug you identified" names no file at all.
#
# The source is read verbatim rather than described. A test that is supposed to fail on real code
# cannot be written against a summary of it.
_SUBJECT_HEADER = (
    "\n\nThe code under test is `{paths}`. Write against THAT file -- do not invent an unrelated "
    "example project. Import it by its module name. Its current contents are:\n\n{excerpt}\n"
)
_SUBJECT_EXCERPT_CHARS = 12000


def subject_block(subject_paths: tuple[str, ...], *, workspace_root: str) -> str:
    """The subject's real source, or "" when there is none to name.

    Never raises and never guesses: an unreadable or missing path yields nothing, so the build
    behaves exactly as it did before rather than inventing a subject.
    """

    import os

    paths = [str(p).strip() for p in (subject_paths or ()) if str(p or "").strip()]
    if not paths:
        return ""
    excerpts: list[str] = []
    for rel in paths[:2]:
        candidate = rel if os.path.isabs(rel) else os.path.join(str(workspace_root or ""), rel)
        try:
            with open(candidate, encoding="utf-8", errors="replace") as handle:
                body = handle.read(_SUBJECT_EXCERPT_CHARS)
        except OSError:
            continue
        if body.strip():
            excerpts.append(f"--- {rel} ---\n{body}")
    if not excerpts:
        return ""
    return _SUBJECT_HEADER.format(paths=", ".join(f"`{p}`" for p in paths[:2]), excerpt="\n\n".join(excerpts))


def build_app_from_spec(
    *,
    request: str,
    target_rel: str,
    source_context: dict[str, Any],
    generate_fn: Callable[[str], str],
    run_tool_fn: Callable[..., ToolOutcome],
    expected_test_outcome: str = "unspecified",
    subject_paths: tuple[str, ...] = (),
    scope: MutationScope | None = None,
    announce_plan_fn: Callable[[list[dict[str, Any]]], None] | None = None,
) -> AppBuildReport:
    # The authority this build runs under. `None` is the historical, unbounded behaviour, so every
    # existing caller keeps exactly the lane it had; an EXACT scope names the files the operator
    # asked for and nothing here may write outside them.
    scope = scope or mutation_scope_module.open_scope()
    report = AppBuildReport(
        handled=True,
        target_dir=target_rel,
        expected_test_outcome=str(expected_test_outcome or "unspecified"),
        subject_paths=tuple(subject_paths or ()),
        scope_kind=scope.kind,
        authorized_paths=tuple(scope.paths),
    )
    workspace_root = str(
        (source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or ""
    ).strip()

    subject = subject_block(report.subject_paths, workspace_root=workspace_root)
    if scope.is_exact:
        # The file list is the OPERATOR's, not a proposal. `_LIST_PROMPT` asks a model to "list the
        # files for this project" and instructs it to include a test module and a README -- which is
        # how "create a file called notes.txt" became notes.txt + app.py + README.md + test_app.py.
        # When the request already named its files there is nothing to propose, so the call is not
        # made at all: a generation that can only be discarded is wall clock spent to no end.
        raw_paths = list(scope.paths)
    else:
        raw_paths = extract_path_list(generate_fn(_LIST_PROMPT.format(request=request) + subject))
    seen: set[str] = set()
    safe_paths: list[str] = []
    for raw in raw_paths:
        # An exact target is already workspace-relative and already the destination the operator
        # named. Confining it against `target_rel` would flatten a second folder into the first,
        # which is the silent relocation this lane keeps removing.
        confined = (
            mutation_scope_module.normalise_path(raw)
            if scope.is_exact
            else _confine_relative_path(raw, target_dir=target_rel, workspace_root=workspace_root)
        )
        if not confined:
            continue
        if not scope.authorizes(confined):
            # Reached only if some caller hands an EXACT scope a list it did not author. Recorded
            # rather than silently dropped: an unrequested target the runtime declined is evidence.
            if confined not in report.paths_refused:
                report.paths_refused.append(confined)
            _file_confinement_fault(confined, source_context=source_context)
            continue
        if confined not in seen:
            seen.add(confined)
            safe_paths.append(confined)
        if len(safe_paths) >= MAX_FILES:
            break
    if not safe_paths:
        report.handled = False
        report.error = "the model did not return a usable file list"
        return report

    rel_names = ", ".join((posixpath.relpath(p, target_rel) if target_rel else p) for p in safe_paths)
    # Generate implementation files first, then the test files LAST with the real implementation code
    # as context, so a test's calls match the actual API instead of a guessed one (the main cause of
    # test/impl drift with a small model). README/data files are generated first too.
    test_paths = [p for p in safe_paths if posixpath.basename(p).startswith("test_")]
    ordered = [p for p in safe_paths if p not in test_paths] + test_paths
    file_map: dict[str, str] = {}
    for path in ordered:
        rel = posixpath.relpath(path, target_rel) if target_rel else path
        if scope.is_exact and len(safe_paths) == 1:
            prompt = _EXACT_FILE_PROMPT.format(path=rel, request=request) + subject
        else:
            prompt = _FILE_PROMPT.format(path=rel, paths=rel_names, request=request) + subject
        if path in test_paths:
            impl_context = _render_impl_for_prompt(
                {p: c for p, c in file_map.items() if p.endswith(".py")}, target_rel=target_rel
            )
            if impl_context:
                prompt += (
                    "\nThe implementation is ALREADY written below -- your tests MUST call these exact "
                    "class/function/method names and signatures (do not invent new ones):\n" + impl_context
                )
        content = strip_code_fences(generate_fn(prompt))
        if content and len(content.encode("utf-8", "ignore")) <= MAX_FILE_BYTES:
            file_map[path] = content
    if not file_map:
        report.handled = False
        report.error = "the model did not return usable file contents"
        return report

    # Directories the build is allowed to make: the ones an authorized file needs, and no others.
    # `generated/file-called-notes-txt-b92eec/` was created for a request that named `notes.txt`
    # and nothing else -- a scaffold directory is a mutation like any other.
    directories = _directories_to_create(safe_paths, target_dir=target_rel, scope=scope)
    # The whole workspace plan, byte-exact, BEFORE the first call goes out. In Manual/Review mode
    # each of these is an approval, and the permission controller can only offer one bounded grant
    # for the set if it is told the set exists -- otherwise it sees one write, asks about one write,
    # and a three-file project costs three prompts. Announced here because this is the first moment
    # the contents exist: a batch is fingerprinted on exact arguments, so a plan without bytes in it
    # authorizes nothing.
    if announce_plan_fn is not None:
        announce_plan_fn(
            [
                {"intent": "workspace.ensure_directory", "arguments": {"path": directory}}
                for directory in directories
            ]
            + [
                {"intent": "workspace.write_file", "arguments": {"path": path, "content": content}}
                for path, content in file_map.items()
            ]
        )
    for directory in directories:
        run_tool_fn("workspace.ensure_directory", {"path": directory})
    # No second authority check here, deliberately. `file_map` is built only from `safe_paths`, and
    # `safe_paths` is what the gate above filters -- so a check in this loop can never fire. A guard
    # the code cannot reach reads as a layered defence and is not one; the single reachable gate is
    # the one that must hold, and it is driven adversarially in
    # tests/test_mutation_scope_authority.py::test_a_target_outside_the_authority_is_refused_and_recorded.
    for path, content in file_map.items():
        if run_tool_fn("workspace.write_file", {"path": path, "content": content}).ok:
            report.files_written.append(path)
            # Kept so the reply can state SIZE, not just names. A bare bullet list of paths gives
            # the operator no way to tell a real file from an empty one.
            report.file_lines[path] = len(str(content or "").splitlines())
        else:
            report.files_skipped.append(path)

    # Chosen from the files that EXIST, not the ones that were planned. A live build listed
    # `test_titlecase.py`, failed to generate its contents, and still picked the unittest command
    # off the plan; `discover` then found nothing, printed "Ran 0 tests ... OK", and the reply said
    # "Tests passed." The plan is an intention; only what was written can be run.
    test_command = _pick_test_command(report.files_written, target_dir=target_rel)
    if test_command and not scope.allow_commands:
        # "Create a file called notes.txt containing hello" is consent to write one file. It is not
        # consent to execute anything, and the live trace ran `python -m unittest` twice off the
        # back of it. Recorded, so the receipt can say a command was declined rather than imply
        # none was ever available.
        report.commands_refused.append(test_command)
        test_command = None
    # A test that is SUPPOSED to fail is the deliverable, not a defect to repair. Without this the
    # fix loop below spends up to MAX_FIX_ROUNDS regenerating the code to make that failure go away
    # -- the exact inverse of "prove the bug before you fix it".
    proving_a_bug = report.expected_test_outcome == "fail"
    if test_command:
        report.tests_ran = True
        report.test_command = test_command
        for round_index in range(MAX_FIX_ROUNDS + 1):
            outcome = run_tool_fn(
                "sandbox.run_command",
                {"command": test_command, "cwd": target_rel},
                trusted_local_only=True,
            )
            report.test_output = str(outcome.response_text or "")
            # The runner reports "executed" even for a failing suite, which is why the verdict was
            # scraped from stdout. The real code is in the outcome details and simply went unread;
            # text scraping stays as the fallback when no code is surfaced.
            raw_code = dict(getattr(outcome, "details", None) or {}).get("returncode")
            report.test_returncode = int(raw_code) if isinstance(raw_code, (int, float)) else None
            passed = (
                report.test_returncode == 0
                if report.test_returncode is not None
                else _tests_passed_from_output(report.test_output)
            )
            if proving_a_bug:
                # Asked for a failing test. Passing means the claimed bug was NOT reproduced, so the
                # finding is unproven -- never a success. Neither is a suite that errored before it
                # could assert, nor a command that never ran: "not green" is not evidence of
                # anything. Either way the loop stops: there is nothing to repair.
                report.tests_passed = passed
                proven = proof_holds(output=report.test_output, returncode=report.test_returncode)
                report.proof_failed = not proven
                if proven:
                    report.proof_note = ""
                elif report.test_returncode is None:
                    report.proof_note = "did_not_run"
                elif passed:
                    report.proof_note = "passed"
                elif unittest_failure_counts(report.test_output)[1] > 0:
                    report.proof_note = "errored"
                else:
                    report.proof_note = "passed"
                break
            if passed:
                report.tests_passed = True
                break
            if round_index == MAX_FIX_ROUNDS:
                break
            report.fix_rounds = round_index + 1
            for path in _source_files_to_fix(file_map):
                rel = posixpath.relpath(path, target_rel) if target_rel else path
                fixed = strip_code_fences(
                    generate_fn(
                        _FIX_PROMPT.format(
                            path=rel,
                            paths=rel_names,
                            command=test_command,
                            output=report.test_output[:2000],
                            content=file_map[path][:8000],
                        )
                    )
                )
                if fixed and fixed != file_map[path] and len(fixed.encode("utf-8", "ignore")) <= MAX_FILE_BYTES:
                    if run_tool_fn("workspace.write_file", {"path": path, "content": fixed}).ok:
                        file_map[path] = fixed
                        if path not in report.files_written:
                            report.files_written.append(path)

    return report


_EXCEPTION_LINE_RE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt)\b\s*:")


def summarize_test_output(output: str) -> str:
    """A single-line, sanitizer-safe digest of a unittest run: the result line and any FAIL/ERROR
    headers. Deliberately excludes the raw traceback (`Traceback ...` / `File "..."` lines trip the
    runtime response sanitizer, which would replace the whole answer with a generic error)."""
    lines = [ln.strip() for ln in str(output or "").splitlines() if ln.strip()]
    ran = next((ln for ln in lines if ln.startswith("Ran ")), "")
    verdict = next((ln for ln in lines if ln.startswith(("OK", "FAILED"))), "")
    fails = list(dict.fromkeys(ln for ln in lines if ln.startswith(("FAIL:", "ERROR:"))))[:4]
    parts = [p for p in (ran, verdict) if p] + fails
    digest = " | ".join(parts)
    if not digest:  # not a unittest run (e.g. a sandbox-block message) -- take a safe first line
        # …plus the exception that ended it, when there is one. A test that dies before the runner
        # reports has NO "Ran N tests" line, so the first line alone was all the operator saw:
        # "Command failed in `generated/…`:" and nothing about what went wrong (measured live
        # 2026-08-01). The final `SomeError: message` line is the one fact worth carrying.
        raised = next(
            (ln for ln in reversed(lines) if _EXCEPTION_LINE_RE.match(ln)),
            "",
        )
        head = lines[0] if lines else ""
        digest = " | ".join(part for part in (head, raised) if part)
    # Belt-and-braces: drop anything that could still look like a traceback to the sanitizer.
    digest = digest.replace("Traceback (most recent call last)", "trace").replace('File "', "file ")
    return digest.replace("\n", " ")[:400]


def render_app_build_response(report: AppBuildReport) -> str:
    """A truthful summary of what was built and what the tests actually did.

    Composed HERE, in the runtime, rather than asked of the model. A build reply is a receipt: which
    files exist now, how big they are, what command ran, what it returned. A model that is having a
    bad turn -- and the free models this runs on regularly are -- cannot be relied on to format its
    own receipt, and when the lane failed entirely the operator was shown the raw completion
    (`["security.py", ...]import hmac...`). Deterministic output cannot be skipped.
    """

    if not report.handled:
        return f"I couldn't build that in the workspace: {report.error}."

    lines: list[str] = []
    proving = report.expected_test_outcome == "fail"
    ran_anything = report.tests_ran and tests_actually_ran(report.test_output)

    # Headline first: for a prove-it request the VERDICT is the answer, not the file count. Four
    # distinct states, because collapsing them is what produced "✅ Bug reproduced" over a suite that
    # errored on a missing fixture -- and, one round earlier, over a command that never ran.
    if proving and report.tests_ran:
        if report.proof_note == "did_not_run":
            lines.append(
                "⚠️ **Not proven — the test never ran.** No exit code came back, so nothing was "
                "executed and nothing was reproduced. Approve the command and re-run to get a verdict."
            )
        elif not ran_anything:
            lines.append(
                "⚠️ **Not proven — no tests were collected.** The run reported zero tests, so the "
                "claimed failure was never exercised."
            )
        elif report.proof_note == "errored":
            failures, errors = unittest_failure_counts(report.test_output)
            lines.append(
                f"⚠️ **Not proven — the test errored instead of failing.** {errors} error(s) and "
                f"{failures} failure(s): an error means the test stopped before reaching its "
                "assertion, so this says nothing about the code under test. Fix the test itself, "
                "then re-run."
            )
        elif report.proof_failed:
            lines.append(
                "❌ **The bug is not proven.** The test I wrote passes, so it does not reproduce "
                "the claimed failure. Treat the finding as unverified until a test fails on it."
            )
        else:
            lines.append("✅ **Bug reproduced.** The test fails as required, so the finding holds.")
        lines.append("")

    def _rel(path: str) -> str:
        base = str(report.target_dir or "")
        return posixpath.relpath(path, base) if base and path.startswith(base + "/") else path

    lines.append(f"**Files** — `{report.target_dir or 'the workspace root'}`")
    lines.append("")
    lines.append("| File | Lines | Status |")
    lines.append("|---|---:|---|")
    for path in report.files_written:
        lines.append(f"| `{_rel(path)}` | {report.file_lines.get(path, 0)} | ✅ written |")
    for path in report.files_skipped:
        lines.append(f"| `{_rel(path)}` | — | ❌ not written |")
    for path in report.paths_refused:
        lines.append(f"| `{_rel(path)}` | — | ⛔ not requested — refused |")

    if report.scope_kind == mutation_scope_module.EXACT:
        lines.append("")
        lines.append(
            "**Scope** — you named "
            + ", ".join(f"`{p}`" for p in report.authorized_paths)
            + ", so that is all I wrote. Ask for a project and I'll build one."
        )

    if report.subject_paths:
        lines.append("")
        lines.append("**Subject** — " + ", ".join(f"`{p}`" for p in report.subject_paths))

    lines.append("")
    if not report.tests_ran:
        if report.commands_refused:
            # "No test command was detected" would be false here: one was, and it was declined.
            lines.append(
                "**Tests** — not run. You asked me to write "
                + ("this file" if len(report.authorized_paths) == 1 else "these files")
                + f", not to execute anything, so I did not run `{report.commands_refused[0]}`. "
                "Say the word and I'll run it."
            )
        else:
            lines.append("**Tests** — no test command was detected, so nothing was run.")
        return "\n".join(lines)

    digest = summarize_test_output(report.test_output)
    code = report.test_returncode
    code_text = "unknown" if code is None else str(code)
    lines.append("**Tests**")
    lines.append("")
    lines.append("| Check | Result |")
    lines.append("|---|---|")
    lines.append(f"| Command | `{report.test_command or 'n/a'}` |")
    lines.append(f"| Exit code | {code_text} |")

    if not ran_anything:
        # "Ran 0 tests ... OK" is not a pass, and calling it a failure is not true either.
        lines.append("| Outcome | ⚠️ no tests collected — nothing was verified |")
    elif proving:
        lines.append(
            "| Outcome | " + ("❌ passed (proof failed)" if report.proof_failed else "✅ failed as required") + " |"
        )
    elif report.tests_passed:
        suffix = "" if report.fix_rounds == 0 else f" after {report.fix_rounds} fix round(s)"
        lines.append(f"| Outcome | ✅ passed{suffix} |")
    else:
        lines.append(f"| Outcome | ❌ still failing after {report.fix_rounds} fix round(s) |")
    if digest:
        # `summarize_test_output` joins its parts with " | ", which would close the cell early and
        # break the table renderer. Escape rather than change the digest -- other callers use it.
        safe_digest = digest.replace("|", "\\|")
        lines.append(f"| Detail | {safe_digest} |")

    if not ran_anything:
        lines.append("")
        lines.append("The files were written; their behaviour is unchecked.")
    elif not proving and not report.tests_passed:
        lines.append("")
        lines.append("I won't claim it works.")
    return "\n".join(lines)
