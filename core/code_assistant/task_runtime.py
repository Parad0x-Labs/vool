"""CodeTaskRuntime -- the one execution authority behind the VOOL coding assistant.

A model, skill or plugin PROPOSES steps as ``code.task.*`` tool calls. Only this runtime
executes them, and it executes them through the same production door every other tool crosses
(``core.runtime_execution_tools.execute_runtime_tool``), so every existing authority applies
unchanged: the tool registry and its contracts, the mode/permission matrix, the effect gateway
and its receipts, workspace confinement, the Blackbox flight recorder, the fault catalog.
Nothing in this module touches a file, a process or a socket on its own.

What this module ADDS on top of those authorities:

* the root-cause workflow as an enforced stage machine
  (reproduce -> identify -> propose -> approve -> mutate -> narrow_test -> cumulative
  -> inspect_diff -> report), so a mutation before a reproduced failure, or before an
  approved preview, is refused by the runtime rather than by the model's good manners;
* approval binding: a mutation must match an approved proposal byte-for-byte (same intent,
  same arguments, same content hash) AND the reviewed base of every canonical target it
  changes -- the content this task read (or itself last wrote) when the repair was proposed.
  The base is checked again under the task's write serialization immediately before the
  physical writer runs, and handed to that writer as its own ``expected_hash`` precondition,
  so neither a caller-supplied hash nor an intervening edit can re-point an approval at content
  nobody reviewed. The runtime re-reads the file after the write to prove the bytes on disk are
  the approved bytes;
* task control: cancellation refuses every later effect; a retried ``step_id`` replays the
  recorded result and never re-executes; an approval is durably claimed by the one step that
  executes it; concurrent writers to one path are serialized;
* evidence: the report is assembled ONLY from steps this runtime executed. Caller-supplied
  results are not in any contract's schema and are rejected at the door as
  ``invalid_arguments`` -- prose cannot become a tool result.

State is journaled as JSON under ``VOOL_CODE_TASK_DIR`` (default: the Blackbox data root),
one file per task, rewritten atomically after every change, so a task survives a restart and
``code.task.report`` answers from the journal, never from memory of intending to run something.
The journal is the authority. A runtime instance's in-memory copy is only a cache, re-read
whenever the journal changed underneath it, and every journal write is a compare-and-swap under
a cross-process lock, so a stale instance can neither decide from nor overwrite a newer journal.
The journal schema, its migrations and the rollback downgrade live in ``journal_schema``.
"""

from __future__ import annotations

import ast
import configparser
import contextlib
import fnmatch
import functools
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import threading
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.code_assistant.journal_schema import (
    CONTENT_HASH_INTENTS,
    PATH_KEYS,
    SCHEMA_VERSION,
    canonical_target,
    mutation_target_paths,
    state_token,
    upgrade_payload,
)
from core.cross_process_lock import LockUnavailable, PublicationLock

AUTHORITY = "code_task_runtime"

STAGES: tuple[str, ...] = (
    "reproduce",
    "identify",
    "propose",
    "approve",
    "mutate",
    "narrow_test",
    "cumulative",
    "inspect_diff",
    "report",
)
STAGE_CANCELLED = "cancelled"
STAGE_ROLLED_BACK = "rolled_back"

COMMAND_INTENTS = frozenset(
    {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter", "sandbox.run_command"}
)
TEST_INTENTS = frozenset({"workspace.run_tests"})
DIFF_INTENTS = frozenset({"workspace.git_diff", "workspace.git_status"})

#: Stages where a shell/validation command is a lawful act. Inherited from the retired in-process
#: workflow's family/stage table: commands are EVIDENCE acts (reproduce, diagnose, narrow, cumulative),
#: never interstitial work between an approval and its mutation. Reads are lawful at every stage.
COMMAND_STAGES = frozenset({"reproduce", "identify", "narrow_test", "cumulative"})

_PATH_KEYS = PATH_KEYS

#: Wall clock, not budget: a journal section is read -> decide -> atomic rewrite (milliseconds) and is
#: never held across a command run or a file write, so ten seconds only bounds waiting on another
#: process's section before the call fails closed as ``journal_unavailable``.
_JOURNAL_LOCK_WAIT_SECONDS = 10.0
#: Wall clock, not budget: a claimed approval's workspace write completes in milliseconds. A claim
#: held by ANOTHER runtime instance for this long belongs to a process that stopped mid-step; it is
#: released, and the reviewed-base precondition -- not the claim -- decides whether the write may run.
_RESERVATION_ABANDON_SECONDS = 15 * 60
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

#: The refusal status an invalidated approval answers with, by invalidation reason.
_INVALIDATION_STATUS = {
    "legacy_approval_without_reviewed_base": "approval_requires_review",
    "legacy_destination_resolves_elsewhere": "approval_requires_review",
    "bytes_diverged": "bytes_diverged",
    "destination_changed": "destination_changed",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_line(text: str, limit: int) -> str:
    """One line, at most ``limit`` characters, for titles assembled from journal text."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit].rsplit(" ", 1)[0] if " " in clean[:limit] else clean[:limit]
    return cut.rstrip(" ,;:")


def _sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


#: Interpreter basenames whose argv this module parses with CPython's documented rules.
_PYTHON_RUNNER_RE = re.compile(r"^(?:python(?:\d+(?:\.\d+)?)?|pypy3?)$")
#: CPython's short-option grammar (``python -h``; measured on the installed 3.12.13): flags cluster
#: (``-BO`` is ``-B -O``); ``c``/``m``/``W``/``X`` take a value ATTACHED (``-c'code'``,
#: ``-mjson.tool``, ``-Werror``) or as the next token; ``-c``/``-m`` end the option list. ``-J`` is
#: reserved and every other character is a usage error: nothing runs.
_PY_SHORT_FLAGS = frozenset("bBdEiIOPqRsStuvx")
_PY_SHORT_VALUES = frozenset("cmWX")
#: Options after which CPython prints and EXITS without executing any entry, wherever they sit in
#: the option list (``-BV`` and ``-VB`` both only print the version, measured). A script named after
#: them never runs: version/help are diagnostics, never execution of a check (revision-3 review R1).
_PY_TERMINATING_SHORT = frozenset("Vh?")
_PY_TERMINATING_LONG = frozenset({"--version", "--help", "--help-env", "--help-xoptions", "--help-all"})
#: The one long option taking a value -- as the NEXT token only (``--check-hash-based-pycs=x`` is a
#: usage error, measured).
_PY_LONG_VALUES = frozenset({"--check-hash-based-pycs"})
#: Interpreter options that change neither which code executes nor whether its assertions run:
#: bytecode writing, buffering, verbosity, and the accepted no-ops ``-R``/``-t``.
_PY_SAFE_INTERP = frozenset({"-B", "-u", "-v", "-q", "-d", "-R", "-t"})
#: ``-O`` strips ``assert`` statements (``-OO`` is two of them); more of it than the obligation ran
#: under can only weaken an assertion-based acceptance condition.
_PY_OPTIMIZE = "-O"
#: ``-i`` turns a failing program's exit status into 0 once standard input closes (measured:
#: ``python -i failing.py </dev/null`` exits 0) -- a green exit under it proves nothing.
_PY_INSPECT = "-i"

#: pytest's short-option grammar (argparse; ``pytest --help`` on the installed 9.1.0, plus the
#: documented xdist ``-n``): flags cluster (``-qx``); ``k``/``m``/``r``/``W``/``c``/``p``/``o``/``n``
#: take a value attached (``-kalpha``, ``-k=alpha``) or as the next token.
_PYTEST_SHORT_FLAGS = frozenset("xsvqlVh")
_PYTEST_SHORT_VALUES = frozenset("kmrWcpon")
#: Long pytest options that consume the next token as their value (``--name=value`` always binds).
_PYTEST_LONG_VALUES = frozenset({
    "--deselect", "--ignore", "--ignore-glob", "--rootdir", "--config-file", "--override-ini",
    "--confcutdir", "--basetemp", "--junitxml", "--junit-xml", "--junit-prefix", "--html",
    "--pythonwarnings", "--capture", "--color", "--tb", "--report-chars", "--maxfail", "--durations",
    "--durations-min", "--import-mode", "--assert", "--numprocesses", "--dist", "--pastebin", "--debug",
    "--last-failed-no-failures", "--lfnf", "--doctest-glob", "--log-level", "--log-file", "--log-cli-level",
    "--show-capture", "--verbosity", "--code-highlight",
})
#: Modes that run no test at all -- collection, planning, fixture setup, help, version -- yet can
#: exit 0 (measured: ``--setup-only`` and ``--version`` are green without executing one test).
_PYTEST_INERT = frozenset({
    "--co", "--collect-only", "--setup-plan", "--setup-only", "--fixtures", "--funcargs",
    "--fixtures-per-test", "--markers", "--version", "-V", "--help", "-h",
})
#: Selection filters argparse STORES: the LAST occurrence decides (``-k a -k b`` runs ``b``, measured).
_PYTEST_STORE_FILTERS = frozenset({"-k", "-m"})
#: Selection filters argparse APPENDS: every occurrence excludes; their order does not change the set.
_PYTEST_APPEND_FILTERS = frozenset({"--deselect", "--ignore", "--ignore-glob"})
#: Filters decided by pytest's cache of earlier runs: never provably the obligation's selection.
_PYTEST_CACHE_FILTERS = frozenset({
    "--lf", "--last-failed", "--sw", "--stepwise", "--sw-skip", "--stepwise-skip", "--lfnf",
    "--last-failed-no-failures",
})
#: Options that change WHERE collection looks or HOW modules import: config file, ini overrides,
#: rootdir, conftest cut-off and loading, import mode, plugin loading (``-p no:cacheprovider`` alone
#: is cosmetic), module-name operands, duplicate handling, doctest collection and collection-error
#: tolerance. Identical on both sides, or coverage is unknown.
_PYTEST_SCOPE = frozenset({
    "-c", "--config-file", "-o", "--override-ini", "--rootdir", "--confcutdir", "--noconftest",
    "--import-mode", "-p", "--pyargs", "--continue-on-collection-errors", "--keep-duplicates",
    "--doctest-modules", "--doctest-glob",
})
#: Options that can only turn a green run red.
_PYTEST_STRICTER = frozenset({"--strict-markers", "--strict-config", "--strict", "--runxfail"})
#: Warning filters: the LAST matching filter wins (measured: ``-W error -W ignore`` is green), so only
#: the obligation's own list, optionally followed by more ``error`` filters, keeps its bar.
_PYTEST_WARNING_FILTERS = frozenset({"-W", "--pythonwarnings"})
#: Reporting, output, stop-after-failure (a green run under them still executed everything), test
#: ordering and execution parallelism. ``--assert`` alone still executes every assertion (measured);
#: composed with interpreter ``-O`` it is judged by the weakening rule. Unknown options are NEVER
#: guessed into this class.
_PYTEST_NEUTRAL = frozenset({
    "-q", "--quiet", "-v", "--verbose", "-s", "--capture", "-x", "--exitfirst", "--maxfail", "--color",
    "--tb", "-r", "--report-chars", "-l", "--showlocals", "--durations", "--durations-min", "--junitxml",
    "--junit-xml", "--html", "--self-contained-html", "--basetemp", "--no-header", "--no-summary",
    "--disable-warnings", "--disable-pytest-warnings", "--setup-show", "--full-trace", "-n",
    "--numprocesses", "--dist", "--ff", "--failed-first", "--nf", "--new-first", "--assert",
})
_PYTEST_RUNNERS = frozenset({"pytest", "py.test"})
#: pytest's collection defaults (the ``python_files``/``norecursedirs`` ini defaults of 9.1.0).
_PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
_PYTEST_DEFAULT_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}")
#: The configuration files pytest searches, in its own precedence order (``locate_config``, 9.1.0).
_PYTEST_CONFIG_NAMES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini",
                        "setup.cfg")
#: Conftest names that customize collection of the paths beneath their directory, and conftest hooks
#: that act on EVERY collected item of the session. Conftest code is Python this contract cannot
#: evaluate: where one of these could change whether pytest reaches the obligation, coverage is unknown.
_CONFTEST_PATH_NAMES = frozenset({
    "collect_ignore", "collect_ignore_glob", "pytest_ignore_collect", "pytest_collect_file",
    "pytest_collect_directory", "pytest_pycollect_makemodule", "pytest_pycollect_makeitem",
})
_CONFTEST_SESSION_NAMES = frozenset({"pytest_collection_modifyitems", "pytest_collection", "pytest_plugins"})
#: Directory budget for classifying one recursive collection scope; beyond it coverage is unknown.
_COLLECTION_WALK_BUDGET = 2000


@dataclass(frozen=True)
class Invocation:
    """What a command EFFECTIVELY executes, as its runner's documented argv rules say.

    The executed program, the executed entry, the interpreter modes, each option's BOUND VALUE (a
    whole string, never its first character), the positional operands and the program arguments stay
    apart and IN COMMAND ORDER, so coverage and repeat identity compare what actually runs. It is
    still a DECLARED identity -- parsed argv, never proven execution semantics.
    """

    program: str = ""           # the executable as the runner resolves it (see ``_interpreter_identity``)
    runner: str = ""            # effective runner: script interpreter, ``-m`` module, or basename
    family: str = "generic"     # python-script | python-inline | python-module | python-diagnostic | pytest | generic
    entry: str = ""             # executed entry as written: script path, module name, inline code, ""
    interpreter: tuple[tuple[str, tuple[str, ...]], ...] = ()  # ordered interpreter options w/ bound values
    options: tuple[tuple[str, str | None], ...] = ()           # ordered program options with their whole values
    operands: tuple[str, ...] = ()                              # ordered positional operands
    args: tuple[str, ...] = ()                                  # the program's argv after its entry, verbatim
    raw: str = ""

    def identity(self) -> tuple[Any, ...]:
        """The repeat identity. Argument order, option occurrence order and whole values are all part
        of it: a script's argv is ordered, a repeated ``-k`` means its LAST value, a generic runner's
        arguments mean whatever that program says. The one documented cosmetic normalization is
        pytest's argparse interleaving -- ``pytest -q t.py`` and ``pytest t.py -q`` parse to the same
        ordered options and the same ordered operands -- together with argparse's attached-value
        spellings (``-kalpha``/``-k=alpha``/``-k alpha``) and the runtime's own interpreter resolution.
        A conservative false "different" only costs an extra run."""
        if self.family == "pytest":
            return (self.program, self.runner, self.family, self.entry, self.interpreter, self.options, self.operands)
        return (self.program, self.runner, self.family, self.entry, self.interpreter, self.args)

    def option_values(self, flag: str) -> tuple[str | None, ...]:
        """Every occurrence of ``flag``'s WHOLE bound value, in command order (``None`` = valueless)."""
        return tuple(value for name, value in self.options if name == flag)

    def last_value(self, flag: str) -> str | None:
        """The value an argparse ``store`` option ends up with: its last occurrence (``None`` if absent)."""
        values = self.option_values(flag)
        return values[-1] if values else None

    def declared_operands(self) -> tuple[str, ...]:
        """The operands the command DECLARES it selects on: program operands, plus for a script
        the executed script itself first. Interpreter options, their values, flags and program
        arguments that are options are never operands."""
        if self.family == "python-script":
            return (self.entry, *(token for token in self.args if not token.startswith("-")))
        return self.operands


def _split_argv(command: str) -> list[str]:
    """The argv the sandbox executes: POSIX shell-word splitting, nothing dropped. The runner execs
    argv directly (no shell), so a leading ``NAME=value`` is the program itself -- refused by the
    sandbox allowlist -- and a later ``mode=strict`` is an ordinary program argument whose VALUE is
    part of the check (the revision-3 parser dropped such tokens anywhere in argv)."""
    text = str(command or "").strip()
    try:
        return shlex.split(text)
    except ValueError:
        return [text] if text else []


@functools.lru_cache(maxsize=256)
def _resolved_executable(program: str, search_path: str) -> str:
    located = program if os.sep in program else shutil.which(program, path=search_path or None)
    if not located or not os.path.exists(located):
        return program
    absolute = os.path.abspath(located)
    return f"{os.path.dirname(absolute)}{os.pathsep}{os.path.realpath(absolute)}"


def _interpreter_identity(argv: list[str], *, validation: bool) -> str:
    """The executable a command runs. The validation runner executes ``pytest`` and ``python3 -m
    pytest`` as THIS interpreter (``runtime_validation_command``); a sandbox command runs its program
    verbatim. Either is identified by the PATH-resolved directory plus real file, so ``venv/bin/python``
    and ``venv/bin/python3`` are one interpreter, two virtualenvs of one base interpreter stay two, and
    an unresolvable program is its literal token."""
    program = str(argv[0])
    if validation:
        from core.execution.validation_tools import runtime_validation_command

        rewritten = _split_argv(runtime_validation_command(shlex.join(argv)))
        program = str(rewritten[0]) if rewritten else program
    return _resolved_executable(program, str(os.environ.get("PATH") or ""))


def _parse_interpreter_options(tokens: list[str]) -> tuple[list[tuple[str, tuple[str, ...]]], str, int, str]:
    """Scan a python interpreter's argv the way CPython does, up to the executed entry.

    Returns (ordered interpreter options with bound values, kind, index just past the entry, entry).
    ``script``/``inline``/``module`` execute ``entry``; ``stdin`` reads the program from standard
    input; ``terminating`` prints version/help and exits; ``invalid`` is a usage error; ``none`` names
    no entry. Only the first three execute a workspace program -- the distinction the revision-3
    scanner lost for ``-V``/``--help`` and for attached ``-c``/``-m`` values."""
    options: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(tokens):
        token = str(tokens[index])
        if token == "--":
            if index + 1 < len(tokens):
                return options, "script", index + 2, str(tokens[index + 1])
            return options, "none", index + 1, ""
        if token == "-":
            return options, "stdin", index + 1, ""
        if token.startswith("--"):
            if token in _PY_TERMINATING_LONG:
                options.append((token, ()))
                return options, "terminating", index + 1, ""
            if token in _PY_LONG_VALUES and index + 1 < len(tokens):
                options.append((token, (str(tokens[index + 1]),)))
                index += 2
                continue
            options.append((token, ()))
            return options, "invalid", index + 1, ""
        if token.startswith("-"):
            body = token[1:]
            consumed = 1
            for position, char in enumerate(body):
                if char in _PY_TERMINATING_SHORT:
                    options.append(("-" + char, ()))
                    return options, "terminating", index + 1, ""
                if char in _PY_SHORT_VALUES:
                    value = body[position + 1:]
                    if not value:
                        if index + 1 >= len(tokens):
                            options.append(("-" + char, ()))
                            return options, "invalid", index + 1, ""
                        value = str(tokens[index + 1])
                        consumed = 2
                    if char in {"c", "m"}:
                        return options, ("inline" if char == "c" else "module"), index + consumed, value
                    options.append(("-" + char, (value,)))
                    break
                if char in _PY_SHORT_FLAGS:
                    options.append(("-" + char, ()))
                    continue
                options.append(("-" + char, ()))
                return options, "invalid", index + 1, ""
            index += consumed
            continue
        return options, "script", index + 1, token
    return options, "none", index, ""


def _parse_pytest_arguments(tokens: tuple[str, ...] | list[str]) \
        -> tuple[tuple[tuple[str, str | None], ...], tuple[str, ...]]:
    """(ordered options with their WHOLE bound value, ordered positional operands) of pytest's argv,
    by its argparse grammar: ``--name=value`` binds; a known long value option consumes the next
    token; short flags cluster and a short value option takes the rest of its token (``-kalpha``,
    ``-k=alpha``) or the next token; ``--`` ends options. An unknown option is kept with no value --
    never guessed into a known class."""
    options: list[tuple[str, str | None]] = []
    operands: list[str] = []
    index = 0
    while index < len(tokens):
        token = str(tokens[index])
        if token == "--":
            operands.extend(str(item) for item in tokens[index + 1:])
            break
        if token.startswith("--"):
            head, sep, inline = token.partition("=")
            if sep:
                options.append((head, inline))
            elif head in _PYTEST_LONG_VALUES and index + 1 < len(tokens):
                options.append((head, str(tokens[index + 1])))
                index += 2
                continue
            else:
                options.append((head, None))
            index += 1
            continue
        if token.startswith("-") and token != "-":
            body = token[1:]
            consumed = 1
            for position, char in enumerate(body):
                if char in _PYTEST_SHORT_VALUES:
                    value = body[position + 1:]
                    if value.startswith("="):
                        value = value[1:]
                    elif not value and index + 1 < len(tokens):
                        value = str(tokens[index + 1])
                        consumed = 2
                    options.append(("-" + char, value))
                    break
                if char in _PYTEST_SHORT_FLAGS:
                    options.append(("-" + char, None))
                    continue
                options.append(("-" + body[position:], None))
                break
            index += consumed
            continue
        operands.append(token)
        index += 1
    return tuple(options), tuple(operands)


def _effective_invocation(command: str, *, validation: bool = True) -> Invocation:
    """The command's effective invocation under its runner's documented argv rules. ``validation``
    resolves the executable the way the validation runner runs it; a sandbox command runs verbatim."""
    raw = str(command or "").strip()
    argv = _split_argv(raw)
    if not argv:
        return Invocation(raw=raw)
    program = _interpreter_identity(argv, validation=validation)
    runner = Path(str(argv[0])).name.lower()
    rest = [str(token) for token in argv[1:]]
    if _PYTHON_RUNNER_RE.match(runner):
        interpreter, kind, past, entry = _parse_interpreter_options(rest)
        modes = tuple(interpreter)
        tail = tuple(rest[past:])
        if kind == "inline":
            return Invocation(program=program, runner=runner, family="python-inline", entry=entry,
                              interpreter=modes, args=tail, raw=raw)
        if kind == "module":
            if entry.lower() in _PYTEST_RUNNERS:
                options, operands = _parse_pytest_arguments(tail)
                return Invocation(program=program, runner="pytest", family="pytest", entry="pytest",
                                  interpreter=modes, options=options, operands=operands, args=tail, raw=raw)
            return Invocation(program=program, runner=entry, family="python-module", entry=entry, interpreter=modes,
                              operands=tuple(token for token in tail if not token.startswith("-")), args=tail,
                              raw=raw)
        if kind == "script":
            return Invocation(program=program, runner=runner, family="python-script", entry=entry,
                              interpreter=modes, args=tail, raw=raw)
        # Version/help, a usage error, stdin or no entry at all: no workspace program executes.
        return Invocation(program=program, runner=runner, family="python-diagnostic", interpreter=modes,
                          args=tail, raw=raw)
    if runner in _PYTEST_RUNNERS:
        options, operands = _parse_pytest_arguments(rest)
        return Invocation(program=program, runner="pytest", family="pytest", entry="pytest", options=options,
                          operands=operands, args=tuple(rest), raw=raw)
    return Invocation(program=program, runner=runner, family="generic",
                      operands=tuple(token for token in rest if not token.startswith("-")), args=tuple(rest), raw=raw)


def _command_selection(command: str) -> tuple[Any, ...]:
    """The selection IDENTITY of a validation command: what a repeat comparison compares (see
    :class:`Invocation`). Order and whole values are preserved; only documented cosmetic spellings
    normalize."""
    return _effective_invocation(command).identity()


@dataclass(frozen=True)
class _CollectionArgument:
    """One pytest collection argument as ``resolve_collection_argument`` (9.1.0) resolves it: an
    absolute path, the ``::`` parts inside it, and the ``[...]`` parametrization of the last part."""

    path: Path
    parts: tuple[str, ...] = ()
    parametrization: str | None = None


@dataclass(frozen=True)
class _CollectionScope:
    """What a pytest invocation collects from: its normalized collection arguments, where they came
    from, and the governing configuration's collection rules -- or why that is unknown."""

    arguments: tuple[_CollectionArgument, ...] = ()
    origin: str = "arguments"   # "arguments" | "testpaths" | "invocation directory"
    config: str = ""
    python_files: tuple[str, ...] = _PYTEST_DEFAULT_PYTHON_FILES
    norecursedirs: tuple[str, ...] = _PYTEST_DEFAULT_NORECURSEDIRS
    unknown: str = ""


def _collection_argument(base: Path, token: str) -> _CollectionArgument | None:
    """``token`` resolved under the invocation directory the way pytest resolves it: the
    parametrization splits at the first ``[``, then ``::`` parts; ``None`` when pytest would refuse
    the argument (a missing path, a parametrized path, parts on a directory)."""
    head, bracket, rest = str(token).partition("[")
    path_text, *parts = head.split("::")
    if bracket and not parts:
        return None
    candidate = Path(path_text)
    try:
        path = (candidate if candidate.is_absolute() else base / candidate).resolve()
    except (OSError, RuntimeError):
        return None
    if not path.exists() or (parts and path.is_dir()):
        return None
    return _CollectionArgument(path=path, parts=tuple(parts), parametrization=f"{bracket}{rest}" if bracket else None)


def _argument_subsumes(by: _CollectionArgument, argument: _CollectionArgument) -> bool:
    """pytest's ``is_collection_argument_subsumed_by`` (9.1.0): a directory without parts subsumes
    every path beneath it; within one path, a parts prefix subsumes the longer selection, and a
    selection without parametrization subsumes every parametrization of the same parts."""
    if by.path != argument.path:
        return not by.parts and argument.path.is_relative_to(by.path)
    if len(by.parts) > len(argument.parts) or argument.parts[:len(by.parts)] != by.parts:
        return False
    return by.parametrization is None or by.parametrization == argument.parametrization


def _normalized_collection(arguments: list[_CollectionArgument]) -> tuple[_CollectionArgument, ...]:
    """pytest's ``normalize_collection_arguments`` (9.1.0): duplicates and subsumed arguments drop,
    keeping the broader one -- which is why ``pytest checks checks/x_check.py`` collects nothing
    from ``x_check.py`` when the name does not match ``python_files`` (measured)."""
    ordered = sorted(enumerate(arguments),
                     key=lambda item: (item[1].path.parts, item[1].parts, item[1].parametrization or ""))
    kept: list[tuple[int, _CollectionArgument]] = []
    for index, argument in ordered:
        if not kept or not _argument_subsumes(kept[-1][1], argument):
            kept.append((index, argument))
    return tuple(argument for _index, argument in sorted(kept, key=lambda item: item[0]))


def _ini_args(settings: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = settings.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return tuple(shlex.split(value))
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return default


def _load_pytest_settings(path: Path) -> dict[str, Any] | None:
    """pytest's ``load_config_dict_from_file`` (9.1.0): the pytest settings a file carries, or
    ``None`` when the file carries none (``pytest.ini``/``pytest.toml`` always count)."""
    if path.suffix in {".ini", ".cfg"}:
        parser = configparser.RawConfigParser()
        parser.read(path, encoding="utf-8")
        section = "pytest" if path.suffix == ".ini" else "tool:pytest"
        if parser.has_section(section):
            return dict(parser.items(section))
        return {} if path.name in {"pytest.ini", ".pytest.ini"} else None
    if path.suffix == ".toml":
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if path.name in {"pytest.toml", ".pytest.toml"}:
            return dict(data.get("pytest") or {})
        tool = dict((data.get("tool") or {}).get("pytest") or {})
        native = {key: value for key, value in tool.items() if key != "ini_options"}
        if native:
            return native
        ini = tool.get("ini_options")
        return dict(ini) if isinstance(ini, dict) else None
    return None


def _pytest_configuration(base: Path, arguments: tuple[_CollectionArgument, ...]) \
        -> tuple[Path | None, dict[str, Any], str]:
    """(governing config file, its settings, error) as ``determine_setup``/``locate_config`` find them
    (9.1.0): the common ancestor of the argument directories -- the invocation directory without
    arguments -- and its parents, first file carrying pytest configuration; a ``pyproject.toml``
    without it still anchors the rootdir."""
    directories = [str(argument.path if argument.path.is_dir() else argument.path.parent) for argument in arguments]
    ancestor = Path(os.path.commonpath(directories)) if directories else base
    pyproject: Path | None = None
    for directory in (ancestor, *ancestor.parents):
        for name in _PYTEST_CONFIG_NAMES:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                settings = _load_pytest_settings(candidate)
            except (OSError, UnicodeDecodeError, ValueError, configparser.Error) as exc:
                return candidate, {}, f"`{candidate}` could not be read as pytest configuration ({exc})"
            if settings is not None:
                return candidate, settings, ""
            if name == "pyproject.toml" and pyproject is None:
                pyproject = candidate
    return pyproject, {}, ""


def _fnmatch_ex(pattern: str, path: Path) -> bool:
    """pytest's ``fnmatch_ex`` (9.1.0): a pattern without a separator matches the basename; one with
    a separator matches the whole path, a relative pattern anchored anywhere beneath the root."""
    if os.sep not in pattern:
        return fnmatch.fnmatch(path.name, pattern)
    if path.is_absolute() and not os.path.isabs(pattern):
        pattern = f"*{os.sep}{pattern}"
    return fnmatch.fnmatch(str(path), pattern)


def _conftest_names(path: Path) -> frozenset[str] | None:
    """Every name a conftest binds (definitions, assignments, imports), or ``None`` when it cannot be
    parsed; an absent conftest binds nothing."""
    if not path.is_file():
        return frozenset()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return frozenset(names)


def node_file(operand: str) -> str:
    """The FILE a selector names when the runner's selector syntax addresses inside files
    (pytest ``path/to/test.py::node``): the path part, verbatim otherwise. A node id selects
    inside its file; it never names a second input, and it is never silently promoted into
    complete byte evidence for a directory or a truncated path."""
    text = str(operand or "")
    return text.split("::", 1)[0] if "::" in text else text


def _cwd_identity(raw: Any) -> str:
    """A working directory as the journal compares selections: the workspace root itself is ``""``."""
    text = str(raw or "").strip()
    return "" if text in {".", "./"} else text


def _short(token: str) -> str:
    return f"`{token[5:17]}…`" if token.startswith("file:") else f"`{token}`"


def task_dir() -> Path:
    override = str(os.environ.get("VOOL_CODE_TASK_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    from core.blackbox.store import store_root

    return store_root() / "code_tasks"


class JournalConflictError(RuntimeError):
    """The journal on disk is not the version this runtime decided from."""

    def __init__(self, task_id: str, on_disk: int, expected: int) -> None:
        super().__init__(
            f"code task `{task_id}` journal is at version {on_disk}, not the version {expected} this "
            "runtime decided from; nothing was overwritten"
        )


def _disk_state(target: Path | None) -> dict[str, str]:
    """What is at a resolved target right now, in the identity the writer's precondition compares
    (``content_sha256`` of the decoded text, exactly as ``read_file`` reports ``hash``)."""
    if target is None:
        return {"kind": "unresolvable", "sha256": ""}
    try:
        if not target.exists():
            return {"kind": "absent", "sha256": ""}
        if target.is_dir():
            return {"kind": "directory", "sha256": ""}
        if not target.is_file():
            return {"kind": "other", "sha256": ""}
        from core.execution.artifacts import content_sha256

        return {"kind": "file", "sha256": content_sha256(target.read_bytes().decode("utf-8", errors="replace"))}
    except OSError:
        return {"kind": "unreadable", "sha256": ""}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class _CompositeCancelEvent:
    """Cancelled when ANY underlying signal is set. A command step runs under two masters -- the
    turn it was proposed in (the operator's stop button) and the task itself (``code.task.cancel``)
    -- and neither may be dropped on the floor because the other exists."""

    def __init__(self, events: list[Any]) -> None:
        self._events = [event for event in events if event is not None]

    def is_set(self) -> bool:
        for event in self._events:
            checker = getattr(event, "is_set", None)
            try:
                if callable(checker):
                    if checker():
                        return True
                elif callable(event):
                    if event():
                        return True
                elif bool(event):
                    return True
            except Exception:
                return True  # a signal that cannot be read is not permission to keep running
        return False


@dataclass
class StepRecord:
    step_id: str
    intent: str
    arguments: dict[str, Any]
    stage_at: str
    started_at: str
    completed_at: str = ""
    executed: bool = False
    ok: bool = False
    status: str = "pending"
    reason: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    bytes_match_approved_patch: bool | None = None
    proposal_id: str = ""
    fault_id: str = ""
    #: The task's workspace revision when this step was admitted (see ``CodeTask.revision``).
    revision_at: int = 0
    #: For an evidence command admitted at a verification stage: the repaired files' on-disk identity
    #: at admission. A check is current only if those bytes did not move while it ran.
    bytes_at: dict[str, str] = field(default_factory=dict)
    #: The repair unit this step changed (a mutation) or validated (a check at a checkpoint).
    unit: str = ""
    #: A declared purpose for RE-RUNNING a check whose equivalent outcome is already journaled
    #: (investigating flakiness, an operator request, changed fixtures outside the journal's
    #: byte binding). A claim by the proposer, journaled as declared -- never as verified fact.
    rerun_reason: str = ""


@dataclass
class Proposal:
    proposal_id: str
    intent: str
    arguments: dict[str, Any]
    preview: dict[str, Any]
    rationale: str = ""
    approved: bool = False
    consumed_by: str = ""
    created_at: str = ""
    approved_at: str = ""
    #: Canonical workspace-relative targets, resolved by the physical writer's own resolver.
    targets: list[str] = field(default_factory=list)
    #: The REVIEWED BASE of each target: what this task read (or itself last wrote) when the
    #: repair was proposed. The approval binds it; the mutation's precondition enforces it.
    base: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The durable claim of the one step executing this approval, and which runtime instance holds it.
    reserved_by: str = ""
    reserved_instance: str = ""
    reserved_at: str = ""
    #: Why this approval can never execute (its reviewed base changed, it was superseded, ...).
    invalidated: dict[str, Any] = field(default_factory=dict)
    #: The repair unit this proposal belongs to: its own id unless it declared a shared unit. A unit
    #: lands whole and is validated after all of its changes; separate units are validated between.
    unit: str = ""


@dataclass
class CodeTask:
    task_id: str
    objective: str
    workspace_root: str
    session_id: str
    turn_id: str
    turn_key: str
    stage: str = "reproduce"
    created_at: str = ""
    updated_at: str = ""
    cancelled_reason: str = ""
    defect: dict[str, Any] = field(default_factory=dict)
    #: Workspace paths this task has READ through the boundary, as executed read steps landed.
    #: The owner-read proposal gate reads this set, never the model's word that it looked.
    read_paths: list[str] = field(default_factory=list)
    steps: dict[str, StepRecord] = field(default_factory=dict)
    step_order: list[str] = field(default_factory=list)
    proposals: dict[str, Proposal] = field(default_factory=dict)
    reproduced_failure: dict[str, Any] | None = None
    narrow: dict[str, Any] | None = None
    cumulative: dict[str, Any] | None = None
    git_diff_paths: list[str] = field(default_factory=list)
    rollback: dict[str, Any] = field(default_factory=lambda: {"restored": False, "restored_paths": []})
    #: The PR description prepared from this task's journal, persisted when it is prepared so
    #: a served surface (or a restarted daemon) can retrieve the full body after the chat
    #: reply itself has published only its summary line.
    pr_description: dict[str, Any] = field(default_factory=dict)
    pr_description_history: list[dict[str, Any]] = field(default_factory=list)
    fault_ids: list[str] = field(default_factory=list)
    #: Blackbox turn ids the task's mutations were journaled under. Served, the flight recorder
    #: keys effects by the CHAT turn's identity, not by this task's synthetic turn id, so rollback
    #: must name what was recorded, never what the task assumed.
    blackbox_turn_ids: list[str] = field(default_factory=list)
    #: Every recorded diagnosis, oldest first. `defect` is the CURRENT one; a failed verification
    #: lawfully reopens diagnosis, and superseded diagnoses are evidence, never erased.
    diagnoses: list[dict[str, Any]] = field(default_factory=list)
    #: Every narrow/cumulative verification outcome, oldest first, each tagged with its stage.
    #: `narrow`/`cumulative` hold the LATEST outcome; this list preserves the failed ones a
    #: recovery superseded, because a wrong first fix is evidence the report must keep.
    verifications: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    #: Incremented by every journal write; a write lands only over the version it was decided from.
    journal_version: int = 0
    #: The workspace revision: how many task mutations have landed bytes.
    revision: int = 0
    #: The task's own view of each canonical path: the hash its latest read returned or its latest
    #: write left, with the step and revision that recorded it. Reviewed bases are taken from here.
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema_migrations: list[dict[str, Any]] = field(default_factory=list)
    #: The repair unit whose landed changes the validation stages are checking, and at which revision.
    checkpoint: dict[str, Any] = field(default_factory=dict)

    def plan(self) -> list[dict[str, Any]]:
        done_index = STAGES.index(self.stage) if self.stage in STAGES else len(STAGES)
        return [
            {"name": name, "state": "done" if i < done_index else ("current" if i == done_index else "pending")}
            for i, name in enumerate(STAGES)
        ]

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CodeTask:
        data = upgrade_payload(payload)
        steps = {k: StepRecord(**v) for k, v in dict(data.get("steps") or {}).items()}
        proposals = {k: Proposal(**v) for k, v in dict(data.get("proposals") or {}).items()}
        task = cls(**{**data, "steps": steps, "proposals": proposals})
        if payload.get("schema_version") != SCHEMA_VERSION:
            # Older runtimes kept a part-applied batch of approvals at `mutate`. Place a migrated task
            # where this stage machine puts it, from its recorded state alone.
            _settle_stage(task)
        return task


def _ordered_proposals(task: CodeTask) -> list[Proposal]:
    return sorted(task.proposals.values(), key=lambda p: (p.created_at, p.proposal_id))


def _live(proposal: Proposal) -> bool:
    """Approved, not yet executed, and still applicable."""
    return bool(proposal.approved and not proposal.consumed_by and not proposal.invalidated)


def _unit_of(proposal: Proposal) -> str:
    return proposal.unit or proposal.proposal_id


def _pending_proposals(task: CodeTask) -> list[Proposal]:
    """Approved repairs still waiting to run, oldest first."""
    return [p for p in _ordered_proposals(task) if _live(p)]


def _unit_members(task: CodeTask, unit: str) -> list[Proposal]:
    return [p for p in _ordered_proposals(task) if _unit_of(p) == unit]


def _unit_in_progress(task: CodeTask) -> str:
    """The repair unit whose changes have partly landed and that still has applicable changes, or ""."""
    seen: set[str] = set()
    for proposal in _ordered_proposals(task):
        unit = _unit_of(proposal)
        if unit in seen:
            continue
        seen.add(unit)
        members = _unit_members(task, unit)
        if any(m.consumed_by for m in members) and any(not m.consumed_by and not m.invalidated for m in members):
            return unit
    return ""


def _proposal_state(proposal: Proposal) -> str:
    """Where a recorded proposal stands, in the words a replay reports."""
    if proposal.consumed_by:
        return "consumed"
    if proposal.invalidated:
        return "invalidated"
    if proposal.reserved_by:
        return "in_flight"
    return "approved" if proposal.approved else "pending_approval"


def _request_differences(proposal: Proposal, *, intent: str, arguments: dict[str, Any], targets: list[str],
                         unit: str) -> list[str]:
    """What a request under a recorded proposal_id changes, field by field.

    Compared: the intent, the canonical target paths, every other argument, an explicit ``expected_hash``
    and the repair unit. The rationale is prose about the change, not part of it, and may be reworded on a
    retry. An explicit ``expected_hash`` differs only when it names content other than the base the
    proposal recorded, so a retry that merely spells out the recorded base is still the same request."""
    differences: list[str] = []
    if proposal.intent != intent:
        differences.append("intent")
    if list(proposal.targets) != list(targets):
        differences.append("path")
    recorded = {k: v for k, v in proposal.arguments.items() if k not in _PATH_KEYS and k != "expected_hash"}
    requested = {k: v for k, v in arguments.items() if k not in _PATH_KEYS and k != "expected_hash"}
    changed = [key for key in sorted(set(recorded) | set(requested))
               if _canonical(recorded.get(key)) != _canonical(requested.get(key))]
    differences.extend(changed)
    explicit = str(arguments.get("expected_hash") or "").strip().lower()
    if explicit:
        if intent in CONTENT_HASH_INTENTS and len(proposal.targets) == 1:
            named = str((proposal.base.get(proposal.targets[0]) or {}).get("sha256") or "")
        else:
            named = str(proposal.arguments.get("expected_hash") or "").strip().lower()
        if explicit != named:
            differences.append("expected_hash")
    if _unit_of(proposal) != unit:
        differences.append("unit")
    return differences


def _free_proposal_id(task: CodeTask, proposal_id: str) -> str:
    """An unused proposal_id derived from a conflicting one, offered as the lawful next id."""
    stem = re.sub(r"-\d+$", "", proposal_id) or proposal_id
    number = 2
    while f"{stem}-{number}" in task.proposals:
        number += 1
    return f"{stem}-{number}"


def _invalidation_refusal(proposal: Proposal) -> tuple[str, str]:
    reason = str(proposal.invalidated.get("reason") or "")
    status = _INVALIDATION_STATUS.get(reason, "stale_base")
    path = str(proposal.invalidated.get("path") or (proposal.targets[0] if proposal.targets else ""))
    where = f" at `{path}`" if path else ""
    return status, (
        f"approval `{proposal.proposal_id}` no longer applies ({reason.replace('_', ' ')}{where}); nothing was "
        "written. Re-read the file through `code.task.step` with intent `workspace.read_file`, propose the repair "
        "against its current content under a new proposal_id, and approve that proposal."
    )


def _current_outcome(task: CodeTask, scope: str) -> dict[str, Any] | None:
    """The narrow/cumulative outcome of the CURRENT workspace revision, or None.

    A landed mutation retires the current outcome (it stays in ``verifications``). An outcome
    recorded for any other revision -- by an older runtime, or a hand-edited journal -- is never
    current, so it can neither reopen review nor complete the task."""
    outcome = task.narrow if scope == "narrow" else task.cumulative
    if not isinstance(outcome, dict) or not outcome:
        return None
    if outcome.get("revision") != task.revision:
        return None
    return outcome


def failed_verification_reopens_review(task: CodeTask) -> bool:
    """Whether a recorded failed verification lawfully reopens diagnosis for THIS task.

    The stage machine's evidence stages are one-way while verification PASSES; a FAILED narrow
    or cumulative check proved the task's current repair wrong, and the one honest continuation
    is another reviewed repair cycle in the SAME durable task: re-diagnose, re-propose, obtain a
    fresh approval for the changed bytes, mutate, and re-verify (the mutation returns the task
    to narrow_test, so downstream green evidence can never be stale). Measured on the base: the
    refusal texts told the model to rerun until passing while `identify`/`propose` were refused
    at exactly these stages -- a dead end no instruction could unlock. Reopened review never
    rewrites history: the failing outcomes stay journaled (see `verifications`).

    Only a failure of the CURRENT bytes reopens review. Measured on revision 2: after a corrected
    repair landed, the first repair's failed check still reopened diagnosis -- even across a
    restart -- so the corrected bytes were never verified before the next cycle began.
    """
    if task.stage == "narrow_test":
        outcome = _current_outcome(task, "narrow")
    elif task.stage == "cumulative":
        outcome = _current_outcome(task, "cumulative")
    else:
        return False
    return bool(outcome) and outcome.get("success") is False


def _completion_verdict(task: CodeTask) -> str:
    """The journal's completion law, shared by step receipts and the evidence report."""
    if task.stage == STAGE_CANCELLED:
        return "cancelled"
    if task.rollback.get("restored"):
        return "rolled_back"
    failed = any(task.steps[key].executed and not task.steps[key].ok
                 for key in task.step_order if key in task.steps)
    narrow = _current_outcome(task, "narrow")
    cumulative = _current_outcome(task, "cumulative")
    if (task.stage == "report" and narrow and narrow.get("success")
            and cumulative and cumulative.get("success") and not failed):
        obligation = code_task_runtime()._obligation_state(
            task, str(cumulative.get("intent") or "workspace.run_tests"),
            {"command": cumulative.get("command"), "cwd": cumulative.get("cwd")})
        if obligation.get("covers_obligation") is not True:
            # The green cumulative never covered the retained acceptance obligation (or its scope
            # is unknowable): unknown coverage is preserved as unresolved, never completed.
            return "unresolved"
        return "completed"
    return "unresolved"


def _coverage_owed(task: CodeTask) -> dict[str, Any] | None:
    """The retained check still owed at the full-check checkpoint, or None.

    The CURRENT full check passed and was a genuine check of the task, yet it does not provably
    cover the retained acceptance obligation (a provable miss, or an unknown scope). The checkpoint
    holds on it -- commands stay lawful here and nowhere after -- and this names the lawful next
    action instead of stranding a repaired task past the last stage where it could still run."""
    if task.stage != "cumulative" or not isinstance(task.reproduced_failure, dict):
        return None
    cumulative = _current_outcome(task, "cumulative")
    if not cumulative or not cumulative.get("success"):
        return None
    state = code_task_runtime()._obligation_state(
        task, str(cumulative.get("intent") or "workspace.run_tests"),
        {"command": cumulative.get("command"), "cwd": cumulative.get("cwd")})
    if not state.get("genuine") or state.get("covers_obligation") is True:
        return None
    return {
        "retained_command": str(task.reproduced_failure.get("command") or ""),
        "retained_cwd": str(task.reproduced_failure.get("cwd") or ""),
        "retained_intent": str(task.reproduced_failure.get("intent") or "workspace.run_tests"),
        "covers_obligation": state.get("covers_obligation"),
        "reason": str(state.get("reason") or ""),
    }


def _checkpoint_validated(task: CodeTask) -> bool:
    """Whether the changes that landed since the last validation have been validated at THIS revision:
    a current focused check and, when it passed, a current full check. A red result validates the
    checkpoint just as a green one does -- the failure is recorded, and recovery or the next
    approved repair may proceed. A check that never ran validates nothing."""
    if task.revision == 0:
        return True
    narrow = _current_outcome(task, "narrow")
    if narrow is None:
        return False
    if narrow.get("success") is False:
        return True
    return _current_outcome(task, "cumulative") is not None


def _last_landed_unit(task: CodeTask) -> str:
    order = {step_id: index for index, step_id in enumerate(task.step_order)}
    landed = [p for p in task.proposals.values() if p.consumed_by]
    if not landed:
        return ""
    return _unit_of(max(landed, key=lambda p: order.get(p.consumed_by, -1)))


def _retire_pr_description(task: CodeTask, reason: str) -> None:
    """A prepared PR description describes the bytes one revision verified. Once those bytes are gone it is
    history, never the task's current description: a surface that drafts a pull request from the journal
    must not find it there."""
    if task.pr_description:
        task.pr_description_history.append({**task.pr_description, "retired_at": _utcnow(), "stale_reason": reason})
        task.pr_description = {}


def _settle_stage(task: CodeTask, *, landed_unit: str = "") -> None:
    """Place a mid-repair task after its approvals or bytes changed: a landed or refused mutation, an
    invalidated approval, a migrated journal. Only a task between `mutate` and `cumulative` moves.

    A unit with changes still to land keeps the task at `mutate`. Once the changes that landed since
    the last checkpoint form no unfinished unit, the task stops at a checkpoint for them (`narrow_test`),
    however many other approved repairs are waiting: a batch of approvals is not one change.

    A task already validated at this revision stays where it is. At `mutate` that matters: when the
    approval it was waiting on stops applying (its file changed), the recovery the refusal names -- re-read,
    propose again, approve -- is lawful only at `mutate`. Measured on this candidate before the rule: the
    task moved on to `inspect_diff`, refused that re-proposal and every check, and its report then said
    `completed` although the approved repair never ran."""
    if task.stage not in {"mutate", "narrow_test", "cumulative"}:
        return
    if _unit_in_progress(task):
        task.stage = "mutate"
        return
    if task.revision and int(task.checkpoint.get("revision") or 0) != task.revision:
        unit = landed_unit or _last_landed_unit(task)
        task.checkpoint = {
            "unit": unit,
            "revision": task.revision,
            "members": [p.proposal_id for p in _unit_members(task, unit)] if unit else [],
            "opened_at": _utcnow(),
        }
        task.stage = "narrow_test"


def stage_next_actions(
    stage: str,
    *,
    verification_failed: bool = False,
    pending_repairs: list[str] | tuple[str, ...] = (),
    unit_in_progress: str = "",
    coverage_owed: dict[str, Any] | None = None,
) -> list[str]:
    """The lawful next actions for a stage, derived from the stage machine itself.

    These are the controller's continuation hints handed back to the model with the bounded
    early-response correction: the stage names exist in the ``code.task.*`` contracts, but a
    model reading a correction mid-turn has no map from ``reproduce`` to the concrete call that
    advances it, and measured native runs burned the whole correction budget narrating or
    re-reading the catalog instead. The actions name existing contracted tools only -- nothing
    here is fixture-specific or a scripted success path.

    ``verification_failed`` switches the two evidence stages to their RECOVERY actions: a
    failed check reopens reviewed repair in the same task (see
    ``failed_verification_reopens_review``), and the guidance must say so -- telling the model
    only to rerun until passing, while review is what actually unlocks continuation, is the
    measured dead end this recovery exists to remove.

    ``coverage_owed`` (see ``_coverage_owed``) switches a PASSED full check that does not cover the
    retained acceptance obligation to its lawful continuation: run the retained check, or a broader
    check whose selection provably includes it -- never "rerun the full suite", which cannot change it.
    """
    stage = str(stage or "").strip()
    pending = [str(item) for item in (pending_repairs or ()) if str(item).strip()]
    pending_text = ", ".join(f"`{item}`" for item in pending)
    continue_pending = (
        [f"or execute the remaining approved repair {pending_text} through `code.task.step`; the task "
         "validates again after it lands"]
        if pending else []
    )
    if stage == "reproduce":
        return [
            "run the failing suite through `code.task.step` with intent `workspace.run_tests` "
            "(or `sandbox.run_command`) and the test command; the recorded failure advances the task"
        ]
    if stage == "identify":
        return [
            "read the owning file through `code.task.step` with intent `workspace.read_file`",
            "record the defect with `code.task.identify` (path, line, reason)",
        ]
    if stage == "propose":
        return [
            "read the owning file through `code.task.step` with intent `workspace.read_file` "
            "if the task has not read it yet",
            "preview the repair with `code.task.propose`: intent `workspace.write_file` or "
            "`workspace.replace_in_file`, its arguments, and a rationale naming the owner and root cause",
        ]
    if stage == "approve":
        return ["approve the previewed proposal with `code.task.approve`"]
    if stage == "mutate":
        if unit_in_progress:
            return [
                f"execute the remaining approved change(s) of repair unit `{unit_in_progress}` through "
                "`code.task.step`; the unit is validated after all of its changes land"
            ]
        return [
            "execute the approved mutation byte-for-byte through `code.task.step` with the same "
            "intent and arguments as the approved proposal"
            + (f" (next approved repair: {pending_text})" if pending else "")
        ]
    if stage == "narrow_test":
        if verification_failed:
            return [
                "the focused suite still fails: re-diagnose with `code.task.identify` -- the failing "
                "output stays journaled as evidence",
                "read the owner and preview the corrected repair with `code.task.propose`; changed "
                "bytes need a fresh approval",
                "after the approved mutation the task returns here to re-verify",
                *continue_pending,
            ]
        after_checks = (
            [f"after this repair's focused and full checks are recorded, the remaining approved repair "
             f"{pending_text} may run"]
            if pending else []
        )
        return [
            "run the focused suite through `code.task.step` with intent `workspace.run_tests`; "
            "a passing run advances the task",
            *after_checks,
        ]
    if stage == "cumulative":
        if verification_failed:
            return [
                "the full suite still fails: re-diagnose with `code.task.identify`, then preview the "
                "corrected repair with `code.task.propose`; changed bytes need a fresh approval",
                "after the approved mutation the task re-verifies at narrow_test before returning here",
                *continue_pending,
            ]
        after_full = [f"after it is recorded, the remaining approved repair {pending_text} may run"] if pending else []
        if coverage_owed:
            retained = str(coverage_owed.get("retained_command") or "")
            cwd = str(coverage_owed.get("retained_cwd") or "")
            intent = str(coverage_owed.get("retained_intent") or "workspace.run_tests")
            reason = str(coverage_owed.get("reason") or "").strip()
            return [
                "the full check passed but does not cover the retained acceptance obligation"
                + (f" ({reason})" if reason else "") + "; it retires nothing",
                f"run the retained acceptance check `{retained}`" + (f" with cwd `{cwd}`" if cwd else "")
                + f" through `code.task.step` with intent `{intent}`, or a broader check whose selection "
                "provably includes it; a covering pass advances the task",
                *after_full,
            ]
        return [
            "run the full suite through `code.task.step` with intent `workspace.run_tests`; "
            "a passing run advances the task",
            *after_full,
        ]
    if stage == "inspect_diff":
        return ["inspect the changed files through `code.task.step` with intent `workspace.git_diff`"]
    if stage == "report":
        return ["publish the evidence report with `code.task.report`"]
    return []


def enforce_code_task_completion(result: dict[str, Any], executed_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep a coding work turn's terminal claim bound to its latest runtime receipts.

    Status-only queries remain answers; only a turn that attempted coding work acquires this
    completion requirement. Earlier failures cannot override a later verified completion.
    """
    work_intents = {"code.task.open", "code.task.step", "code.task.identify", "code.task.propose", "code.task.approve"}
    work_ids: set[str] = set()
    latest: dict[str, dict[str, Any]] = {}
    for step in executed_steps:
        intent = str(step.get("tool_name") or "")
        if not intent.startswith("code.task.") or step.get("correction"):
            continue
        details = dict(step.get("details") or {})
        task_id = str(details.get("task_id") or dict(step.get("arguments") or {}).get("task_id") or "")
        if not task_id:
            continue
        if intent in work_intents:
            work_ids.add(task_id)
        # A preflight refusal has no new journal state. Keep the last actual
        # stage/verdict instead of erasing it with a stateless argument error.
        latest[task_id] = {**latest.get(task_id, {}), **details}
    unfinished = [
        {
            "task_id": task_id,
            "stage": str(latest[task_id].get("stage") or "unknown"),
            "verification_failed": bool(latest[task_id].get("verification_failed")),
            "next": stage_next_actions(
                str(latest[task_id].get("stage") or "unknown"),
                verification_failed=bool(latest[task_id].get("verification_failed")),
                pending_repairs=list(latest[task_id].get("pending_repairs") or []),
                unit_in_progress=str(latest[task_id].get("unit_in_progress") or ""),
                coverage_owed=latest[task_id].get("coverage_owed") or None,
            ),
        }
        for task_id in sorted(work_ids)
        if str(latest[task_id].get("code_task_verdict") or latest[task_id].get("verdict") or "")
        not in {"completed"}
    ]
    if not unfinished:
        return result
    states = "; ".join(f"{row['task_id']} is still at {row['stage']}" for row in unfinished)
    return {
        **result,
        "response": f"Coding task incomplete: {states}. The repair has not passed its completion checks.\n\n" + str(result.get("response") or ""),
        "success": False,
        "status": "code_task_incomplete",
        "mode": "tool_failed",
        "task_outcome": "partially_fulfilled",
        "fulfillment_outcome": {
            "fulfillment_status": "partially_fulfilled",
            "failure_stage": "code_task_execution",
            "failure_codes": ["code_task_incomplete"],
            "retryable": True,
        },
        "details": {**dict(result.get("details") or {}), "unfinished_code_tasks": unfinished},
    }


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


class CodeTaskRuntime:
    """Process-wide authority; one instance, journaled to disk. See the module docstring."""

    def __init__(self) -> None:
        self._tasks: dict[str, CodeTask] = {}
        #: The (mtime_ns, size) of each journal file as this instance last read or wrote it. A
        #: different stat on disk means another instance wrote: the cached task is re-read.
        self._stats: dict[str, tuple[int, int]] = {}
        self._lock = threading.RLock()
        self._path_locks: dict[tuple[str, str], threading.Lock] = {}
        self._task_mutation_locks: dict[str, threading.Lock] = {}
        self._journal_lock_depth: dict[str, int] = {}
        self._inflight: set[tuple[str, str]] = set()
        #: Runtime-side cancel signals, one per task, created on demand. A ``code.task.cancel``
        #: that arrives WHILE a command step is in flight sets the task's event, and the running
        #: job observes it between the runner's output polls -- cancellation reaches INTO the
        #: command, not merely between commands. Not persisted: a cancelled task is terminal in
        #: the journal, so a restart never needs to re-signal.
        self._cancel_events: dict[str, threading.Event] = {}
        #: Identifies the claims this instance holds; a restart is a new instance.
        self._instance = uuid.uuid4().hex

    def _task_cancel_event(self, task_id: str) -> threading.Event:
        with self._lock:
            event = self._cancel_events.get(task_id)
            if event is None:
                event = self._cancel_events[task_id] = threading.Event()
            return event

    # -- journal ------------------------------------------------------------

    @staticmethod
    def _journal_stat(task_id: str) -> tuple[int, int] | None:
        try:
            stat = (task_dir() / f"{task_id}.json").stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    @contextlib.contextmanager
    def _journal_lock(self, task_id: str) -> Iterator[None]:
        """The cross-process lock over one task's journal, re-entrant within this instance.

        Always taken while holding ``self._lock``, so at most one thread of this process is inside
        it; another process waits (bounded) and fails closed as ``LockUnavailable``."""
        with self._lock:
            depth = self._journal_lock_depth.get(task_id, 0)
            if depth:
                self._journal_lock_depth[task_id] = depth + 1
                try:
                    yield
                finally:
                    self._journal_lock_depth[task_id] -= 1
                return
            root = task_dir()
            root.mkdir(parents=True, exist_ok=True)
            lock = PublicationLock(root / f".{task_id}.lock")
            import time as _time

            deadline = _time.monotonic() + _JOURNAL_LOCK_WAIT_SECONDS
            while True:
                try:
                    lock.__enter__()
                    break
                except LockUnavailable:
                    if _time.monotonic() >= deadline:
                        raise
                    _time.sleep(0.005)
            self._journal_lock_depth[task_id] = 1
            try:
                yield
            finally:
                self._journal_lock_depth.pop(task_id, None)
                lock.__exit__(None, None, None)

    def _persist(self, task: CodeTask) -> None:
        """Write the journal atomically as the next version of what this instance decided from.

        Refuses with ``JournalConflictError`` -- never overwrites -- when the journal on disk is a
        version this instance did not read (a writer that bypassed the journal lock)."""
        root = task_dir()
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{task.task_id}.json"
        with self._lock, self._journal_lock(task.task_id):
            current = self._journal_stat(task.task_id)
            if current is not None and current != self._stats.get(task.task_id):
                try:
                    on_disk = int(json.loads(target.read_text(encoding="utf-8")).get("journal_version") or 0)
                except (OSError, ValueError, AttributeError):
                    on_disk = -1
                if on_disk != task.journal_version:
                    raise JournalConflictError(task.task_id, on_disk, task.journal_version)
            task.journal_version += 1
            task.updated_at = _utcnow()
            tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(task.to_json(), sort_keys=True, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, target)
            self._stats[task.task_id] = self._journal_stat(task.task_id) or (0, 0)
            self._tasks[task.task_id] = task

    def _load(self, task_id: str) -> CodeTask | None:
        """The task as its JOURNAL holds it. The cached copy is used only while the journal file is
        exactly the one this instance last read or wrote."""
        clean = str(task_id or "").strip()
        if not _TASK_ID_RE.match(clean):
            return None
        with self._lock:
            stat = self._journal_stat(clean)
            if stat is None:
                self._tasks.pop(clean, None)
                self._stats.pop(clean, None)
                return None
            cached = self._tasks.get(clean)
            if cached is not None and self._stats.get(clean) == stat:
                return cached
            try:
                task = CodeTask.from_json(json.loads((task_dir() / f"{clean}.json").read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                return None
            self._tasks[clean] = task
            self._stats[clean] = stat
            return task

    @contextlib.contextmanager
    def _transaction(self, task_id: str) -> Iterator[CodeTask | None]:
        """Decide from the journal's current version and write back over exactly that version."""
        clean = str(task_id or "").strip()
        if not _TASK_ID_RE.match(clean):
            yield None
            return
        with self._lock, self._journal_lock(clean):
            yield self._load(clean)

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._stats.clear()
            self._path_locks.clear()
            self._task_mutation_locks.clear()
            self._inflight.clear()
            self._cancel_events.clear()
            self._instance = uuid.uuid4().hex

    # -- identity -------------------------------------------------------------

    @staticmethod
    def _context_session(source_context: dict[str, Any] | None) -> str:
        context = source_context if isinstance(source_context, dict) else {}
        return str(context.get("session_id") or context.get("runtime_session_id") or "").strip()

    def _require_owned(self, task: CodeTask, source_context: dict[str, Any] | None):
        """Concurrent sessions are ISOLATED: a task belongs to the session that opened it, and a
        control-plane call from any other session is refused before it is read as evidence. A
        caller that names no session at all is refused too -- the journal's owner is typed, so an
        anonymous caller is nobody this task knows."""
        caller = self._context_session(source_context)
        if caller and caller == task.session_id:
            return None
        return self._result(
            task,
            ok=False,
            status="not_your_task",
            text=(
                f"Code task `{task.task_id}` belongs to another session; open a task of your own "
                "with `code.task.open`."
            ),
        )

    def prepared_pr_description(self, task_id: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
        """The PR description this task's journal has already prepared, or an empty dict.

        A pure read for surfaces that draft a reviewable pull request from verified evidence:
        it returns only what `code.task.pr_description` persisted (title, body, task_stage,
        prepared_at) and confers no authority. Ownership is enforced exactly as on every other
        control-plane read, so one session cannot mine another session's task journal."""

        task, err = self._require(task_id)
        if err:
            return {}
        if self._require_owned(task, source_context):
            return {}
        with self._lock:
            return dict(task.pr_description or {})

    def _task_context(self, task: CodeTask, source_context: dict[str, Any] | None) -> dict[str, Any]:
        """The context every inner tool call runs under: the caller's, pinned to the task's
        turn identity so Blackbox journals all of the task's mutations under ONE turn and the
        fault rows join to it. The task's own cancel event rides along beside the turn's, so a
        cancellation signalled by either route reaches the running command."""
        context = _pin_task_roots(dict(source_context or {}), task.workspace_root)
        context.setdefault("session_id", task.session_id)
        context["turn_id"] = task.turn_id
        context["_blackbox_task_id"] = task.task_id
        # PB01 — ONE task-class authority for learning: the reuse ranker ranks by the ROUTER's
        # class for the turn's own words, so the lesson learned from this task's validated run
        # must be keyed the same way (the executor-context default would forever miss it). The
        # routing envelope's class wins when the caller carried it; otherwise the router's own
        # classifier derives it from the task's objective — the same text the opening turn was
        # routed on. Fail-soft: a classification failure must not cost the task its context.
        if not str(context.get("task_class") or "").strip():
            routed = str(
                dict(context.get("task_envelope") or {}).get("inputs", {}).get("task_class") or ""
            ).strip()
            if not routed:
                try:
                    from core.task_router import build_task_envelope_for_request

                    routed = str(
                        build_task_envelope_for_request(
                            task.objective, context={"session_id": task.session_id}
                        ).inputs.get("task_class")
                        or ""
                    ).strip()
                except Exception:
                    routed = ""
            if routed:
                context["task_class"] = routed
        events = [self._task_cancel_event(task.task_id)]
        turn_event = context.get("cancel_event") or context.get("cancellation_token")
        if turn_event is not None:
            events.insert(0, turn_event)
        context["cancel_event"] = _CompositeCancelEvent(events)
        return context

    def _file_fault(self, task: CodeTask, code: str, *, dedupe: str, context: dict[str, str]) -> str:
        try:
            from core.faults.recorder import record_fault
            from core.faults.records import FaultRecord

            record = FaultRecord.for_code(
                code,
                authority=AUTHORITY,
                turn_key=task.turn_key,
                session_id=task.session_id,
                dedupe=dedupe,
                context=context,
            )
            fault_id = record_fault(record)
        except Exception:
            return ""
        task.fault_ids.append(fault_id)
        return fault_id

    # -- workspace identity ---------------------------------------------------

    @staticmethod
    def _target_path(task: CodeTask, target: str) -> Path | None:
        from core.execution.workspace_tools import resolve_workspace_path

        try:
            return resolve_workspace_path(target, workspace_root=Path(task.workspace_root))
        except (ValueError, OSError):
            return None

    @staticmethod
    def _canonical_targets(task: CodeTask, intent: str, arguments: dict[str, Any]) -> tuple[list[str], str]:
        """(canonical targets, the first raw path that escapes the workspace or "")."""
        targets: list[str] = []
        for raw in mutation_target_paths(intent, arguments):
            resolved = canonical_target(task.workspace_root, raw)
            if resolved is None:
                return [], raw
            if resolved not in targets:
                targets.append(resolved)
        return targets, ""

    # -- results --------------------------------------------------------------

    @staticmethod
    def _result(task: CodeTask | None, *, ok: bool, status: str, text: str, **details: Any):
        from core.runtime_execution_tools import RuntimeExecutionResult

        payload: dict[str, Any] = {"executed": False}
        if task is not None:
            pending = [p.proposal_id for p in _pending_proposals(task)]
            in_progress = _unit_in_progress(task)
            payload.update({"task_id": task.task_id, "stage": task.stage, "plan": task.plan(),
                            "code_task_verdict": _completion_verdict(task), "revision": task.revision,
                            # Always present, so a view merged from several results never keeps a stale fact.
                            "verification_failed": False, "pending_repairs": pending,
                            "unit_in_progress": in_progress, "coverage_owed": None})
            reopened = failed_verification_reopens_review(task)
            owed = None if reopened else _coverage_owed(task)
            if reopened:
                # Task-level fact, carried on every result while true, so the completion
                # feedback can tell a failed verification from a not-yet-run one. The next
                # actions ride WITH it: the model reading a failed verification's observation
                # sees the recovery path in the same payload, not only on a later correction.
                payload["verification_failed"] = True
                payload["next"] = stage_next_actions(task.stage, verification_failed=True, pending_repairs=pending)
            elif owed:
                # Task-level fact while true: the full check passed without covering the retained
                # acceptance obligation, the checkpoint holds, and the next action names that check.
                payload["coverage_owed"] = owed
                payload["next"] = stage_next_actions(task.stage, pending_repairs=pending, unit_in_progress=in_progress,
                                                     coverage_owed=owed)
            elif pending or in_progress:
                payload["next"] = stage_next_actions(task.stage, pending_repairs=pending, unit_in_progress=in_progress)
        payload.update(details)
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text, details=payload)

    def _step_result(self, task: CodeTask, step: StepRecord, *, replayed: bool = False):
        text = step.reason or f"`{step.intent}` {step.status}"
        verification = next((v for v in reversed(task.verifications) if v.get("step_id") == step.step_id), None)
        extra: dict[str, Any] = {}
        if verification is not None:
            extra["verification"] = {key: verification.get(key)
                                     for key in ("stage", "success", "revision", "current", "stale_reason",
                                                 "obligation_match", "covers_obligation", "coverage_kind",
                                                 "input_coverage")}
        return self._result(
            task,
            **extra,
            ok=step.ok,
            status=step.status,
            text=text,
            executed=False if replayed else step.executed,
            replayed=replayed,
            step_id=step.step_id,
            intent=step.intent,
            tool_result=step.result,
            receipts=step.receipts,
            paths=step.paths,
            bytes_match_approved_patch=step.bytes_match_approved_patch,
            reason=step.reason,
        )

    def _unrecorded_effect(self, task: CodeTask, step_id: str, intent: str, outcome: Any, error: Exception | str):
        """An inner tool ran but its outcome could not be journaled: say exactly that."""
        from core.runtime_execution_tools import RuntimeExecutionResult

        details = dict(getattr(outcome, "details", {}) or {}) if outcome is not None else {}
        details.pop("observation", None)
        self._file_fault(task, "unknown", dedupe=f"{task.task_id}:{step_id}:unrecorded",
                         context={"intent": intent, "status": "journal_unavailable"})
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="journal_unavailable",
            response_text=(
                f"`{intent}` ran as step `{step_id}` but its outcome could not be recorded in the task "
                f"journal ({error}); inspect the workspace before relying on this step."
            ),
            details={"executed": outcome is not None, "effect_recorded": False, "task_id": task.task_id,
                     "step_id": step_id, "intent": intent,
                     "tool_result": json.loads(json.dumps(details, default=str))},
        )

    # -- open / identify / propose / approve / cancel -------------------------

    def open(self, arguments: dict[str, Any], *, workspace_root: Path, source_context: dict[str, Any] | None):
        objective = str(arguments.get("objective") or "").strip()
        if not objective:
            return self._result(
                None, ok=False, status="invalid_arguments", text="`code.task.open` needs a non-empty objective."
            )
        context = dict(source_context or {})
        session_id = (
            str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
            or f"code-task:{uuid.uuid4().hex[:8]}"
        )
        task_id = f"ct-{uuid.uuid4().hex[:12]}"
        turn_id = f"code-task:{task_id}"
        try:
            from core.faults.recorder import identity_from_context as fault_identity

            turn_key, _ = fault_identity({**context, "turn_id": turn_id, "session_id": session_id})
        except Exception:
            turn_key = turn_id
        task = CodeTask(
            task_id=task_id,
            objective=objective,
            workspace_root=str(Path(workspace_root).resolve()),
            session_id=session_id,
            turn_id=turn_id,
            turn_key=turn_key or turn_id,
            created_at=_utcnow(),
        )
        with self._lock:
            self._tasks[task_id] = task
            self._persist(task)
        return self._result(
            task, ok=True, status="ok", text=f"Opened code task {task_id}: {objective}", demand=objective
        )

    def _require(self, task_id: str):
        task = self._load(str(task_id or "").strip())
        if task is None:
            return None, self._result(
                None, ok=False, status="unknown_task", text=f"No code task `{task_id}` exists in the journal."
            )
        return task, None

    def _vanished(self, task_id: str):
        return self._result(
            None, ok=False, status="unknown_task", text=f"No code task `{task_id}` exists in the journal."
        )

    def identify(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            if task.stage == STAGE_CANCELLED:
                return self._result(
                    task, ok=False, status="cancelled", text="The task is cancelled; nothing further executes."
                )
            reopened = failed_verification_reopens_review(task)
            if task.stage != "identify" and not reopened:
                if task.stage in {"narrow_test", "cumulative"}:
                    scope = "focused" if task.stage == "narrow_test" else "full"
                    text = (
                        f"Cannot re-diagnose at stage `{task.stage}`: the current repair (revision {task.revision}) has "
                        f"not been verified. Run the {scope} check through `code.task.step` with intent "
                        "`workspace.run_tests` first; failed checks of earlier bytes stay in the history but cannot "
                        "reopen review."
                    )
                else:
                    text = f"Cannot identify the defect at stage `{task.stage}`: the failure must be reproduced first."
                return self._result(task, ok=False, status="stage_violation", text=text)
            path = str(arguments.get("path") or "").strip()
            if not path:
                return self._result(
                    task, ok=False, status="invalid_arguments", text="`code.task.identify` needs the owning path."
                )
            if task.defect:
                task.diagnoses.append(task.defect)
            task.defect = {
                "path": path,
                "line": int(arguments.get("line") or 0),
                "reason": str(arguments.get("reason") or "").strip(),
                "at": _utcnow(),
            }
            task.stage = "propose"
            self._persist(task)
            return self._result(
                task,
                ok=True,
                status="ok",
                text=f"Owning defect recorded at {path}:{task.defect['line']}.",
                defect=task.defect,
            )

    def _preview(self, task: CodeTask, intent: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        contract = runtime_tool_contract_map().get(intent)
        if contract is None or contract.read_only or intent in COMMAND_INTENTS:
            return None
        paths = [str(arguments.get(k) or "").strip() for k in _PATH_KEYS if str(arguments.get(k) or "").strip()]
        content = arguments.get("content")
        if content is None:
            content = arguments.get("patch") if arguments.get("patch") is not None else arguments.get("replacement", "")
        return {
            "intent": intent,
            "paths": paths,
            "side_effect_class": contract.side_effect_class,
            "content_sha256": _sha(str(content or "")),
            "arguments_sha256": _sha(_canonical(self._bind_key(arguments))),
        }

    @staticmethod
    def _bind_key(arguments: dict[str, Any]) -> dict[str, Any]:
        """The replacement's identity. ``expected_hash`` is not part of it: the reviewed base is
        bound separately (``Proposal.base``) from what the task itself reviewed, so a caller can
        neither omit its way past the precondition nor re-point it by supplying a new hash."""
        return {k: v for k, v in arguments.items() if k != "expected_hash"}

    @staticmethod
    def _refuse_unconfined_paths(arguments: dict[str, Any]) -> str:
        """The contract's confinement pre-check, at proposal time: an absolute or `..`-escaping
        path in a proposed mutation is refused before a preview exists, so nothing unconfined is
        ever offered for approval. Returns "" or the offending path."""
        for key in _PATH_KEYS:
            value = str(arguments.get(key) or "").strip()
            if not value:
                continue
            if value.startswith(("/", "~")) or value.startswith("\\\\"):
                return value
            if any(part == ".." for part in value.replace("\\", "/").split("/")):
                return value
        return ""

    def _resolve_reviewed_base(
        self, task: CodeTask, intent: str, arguments: dict[str, Any], targets: list[str]
    ) -> tuple[dict[str, dict[str, Any]], tuple[str, str] | None]:
        """The content each target was reviewed against, taken ONLY from the task's own evidence.

        An explicit ``expected_hash`` must be exactly what the task read (or last wrote); an
        omitted one resolves to that same snapshot. An existing file the task never read has no
        reviewed content, so nothing about it can be approved; a target that does not exist is
        reviewed as absent, and must still be absent when the repair runs."""
        explicit = str(arguments.get("expected_hash") or "").strip().lower()
        single = intent in CONTENT_HASH_INTENTS and len(targets) == 1
        base: dict[str, dict[str, Any]] = {}
        for target in targets:
            snap = task.snapshots.get(target)
            if explicit and single:
                if snap is None:
                    disk = _disk_state(self._target_path(task, target))
                    if disk["kind"] not in {"absent", "unresolvable"}:
                        return {}, (
                            "base_not_reviewed",
                            f"`{target}` exists but this task has not read it; read it through `code.task.step` "
                            "with intent `workspace.read_file`, then propose the repair against the content you "
                            "read -- an approval binds reviewed content, never unseen bytes.",
                        )
                    return {}, (
                        "stale_base",
                        f"`expected_hash` names existing content for `{target}`, but `{target}` does not exist; "
                        "re-read the workspace and propose against what is there.",
                    )
                if state_token(snap) != "file:" + explicit:
                    return {}, (
                        "stale_base",
                        f"`expected_hash` `{explicit[:12]}…` is not the content this task reviewed for `{target}` "
                        f"({_short(state_token(snap))}, recorded by step `{snap.get('step_id')}`); re-read `{target}` "
                        "through `code.task.step` and propose against what that read returns.",
                    )
                base[target] = {"kind": "file", "sha256": explicit, "source": snap.get("source", ""),
                                "step_id": snap.get("step_id", "")}
                continue
            if snap is not None:
                base[target] = {"kind": snap.get("kind", ""), "sha256": snap.get("sha256", ""),
                                "source": snap.get("source", ""), "step_id": snap.get("step_id", "")}
                continue
            disk = _disk_state(self._target_path(task, target))
            if disk["kind"] in {"absent", "directory"}:
                base[target] = {**disk, "source": f"{disk['kind']}_at_proposal", "step_id": ""}
                continue
            return {}, (
                "base_not_reviewed",
                f"`{target}` exists but this task has not read it; read it through `code.task.step` with intent "
                "`workspace.read_file`, then propose the repair against the content you read -- an approval binds "
                "reviewed content, never unseen bytes.",
            )
        return base, None

    def _binding_digest(self, task: CodeTask, intent: str, arguments: dict[str, Any], targets: list[str],
                        base: dict[str, dict[str, Any]]) -> str:
        """The complete action identity an approval stands for, self-describing for any verifier."""
        return _sha(_canonical({
            "task_id": task.task_id,
            "session_id": task.session_id,
            "workspace_root": task.workspace_root,
            "intent": intent,
            "arguments": self._bind_key(arguments),
            "targets": list(targets),
            "base": {target: state_token(base.get(target)) for target in targets},
        }))

    def _replay_or_conflict(self, task: CodeTask, proposal: Proposal, *, intent: str, inner: dict[str, Any],
                            targets: list[str], unit: str):
        """A request under a recorded proposal_id. Exactly the recorded request replays idempotently and says
        where the proposal stands; anything else is a typed conflict naming what differs and a free id. The
        recorded proposal is never rewritten: an approval covers only the request it approved."""
        pid = proposal.proposal_id
        state = _proposal_state(proposal)
        differences = _request_differences(proposal, intent=intent, arguments=inner, targets=targets, unit=unit)
        refusal: tuple[str, str] | None = None
        if state != "consumed" and "path" not in differences:
            # What this request would bind now. An executed proposal's base is history; any other proposal's
            # base is compared, so a retry after its file changed is a different request, not a replay.
            base, refusal = self._resolve_reviewed_base(task, intent, inner, targets)
            now = {target: state_token(base.get(target)) for target in targets}
            if refusal is None and now != {target: state_token(proposal.base.get(target)) for target in proposal.targets}:
                differences.append("base")
        if differences:
            suggested = _free_proposal_id(task, pid)
            return self._result(
                task, ok=False, status="proposal_id_conflict", proposal_id=pid, proposal_state=state,
                differences=differences, suggested_proposal_id=suggested,
                text=(
                    f"proposal `{pid}` already records a different request (it differs in: {', '.join(differences)}); "
                    "a recorded proposal is never rewritten, and its approval covers only what it recorded. Preview "
                    f"the revised repair with `code.task.propose` under a new proposal_id such as `{suggested}`, then "
                    "approve that proposal."
                ),
            )
        if state == "invalidated":
            status, text = _invalidation_refusal(proposal)
            return self._result(task, ok=False, status=status, text=text, proposal_id=pid, proposal_state=state)
        if refusal is not None:
            status, text = refusal
            return self._result(task, ok=False, status=status, text=text, proposal_id=pid, proposal_state=state)
        next_action = {
            "pending_approval": "approve it with `code.task.approve`",
            "approved": "execute it through `code.task.step` with the same intent and arguments",
            "in_flight": "its approved step is running now; wait for that step's result",
            "consumed": f"it already ran as step `{proposal.consumed_by}`",
        }[state]
        return self._result(
            task, ok=True, status="ok", proposal_id=pid, preview=proposal.preview, replayed=True,
            proposal_state=state,
            text=f"Proposal `{pid}` is already recorded with exactly this request; nothing was changed: {next_action}.",
        )

    def propose(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            if task.stage == STAGE_CANCELLED:
                return self._result(
                    task, ok=False, status="cancelled", text="The task is cancelled; nothing further executes."
                )
            if task.stage not in {"propose", "approve", "mutate"}:
                return self._result(
                    task, ok=False, status="stage_violation", text=f"Cannot propose a repair at stage `{task.stage}`."
                )
            proposal_id = str(arguments.get("proposal_id") or "").strip() or f"p-{uuid.uuid4().hex[:8]}"
            intent = str(arguments.get("intent") or "").strip()
            inner = dict(arguments.get("arguments") or {})
            rationale = str(arguments.get("rationale") or "").strip()
            # The coding contract decides what may be PROPOSED (`core.code_assistant.contract`): the
            # model dialects validate at mint, and this door validates too, so a direct call cannot
            # put a tool outside the contract -- a machine write or move, a message -- behind a
            # code-task approval.
            from core.code_assistant.contract import CodeAssistantProposal, ContractRefused, validate_proposal

            try:
                validate_proposal(CodeAssistantProposal(intent=intent, arguments=inner, rationale=rationale))
            except ContractRefused as refused:
                return self._result(
                    task,
                    ok=False,
                    status=refused.reason,
                    text=str(refused.detail or refused.reason),
                    proposal_id=proposal_id,
                )
            # Inherited from the retired in-process workflow (REASON_FIX_WITHOUT_RATIONALE): a fix
            # proposal must name the owner and the root cause in its rationale. A mutation nobody
            # explained cannot be approved, because an approval the operator cannot judge is not
            # an approval.
            if not rationale:
                return self._result(
                    task,
                    ok=False,
                    status="invalid_arguments",
                    text="A repair proposal must carry a `rationale` naming the owner and the root cause.",
                    proposal_id=proposal_id,
                )
            preview = self._preview(task, intent, inner)
            if preview is None:
                return self._result(
                    task,
                    ok=False,
                    status="not_a_mutation",
                    text=f"`{intent}` is not a mutating tool; propose only what needs approval.",
                )
            unconfined = self._refuse_unconfined_paths(inner)
            targets, escaped = ([], unconfined) if unconfined else self._canonical_targets(task, intent, inner)
            if escaped:
                return self._result(
                    task,
                    ok=False,
                    status="unconfined_path",
                    text=(
                        f"`{escaped}` is absolute or escapes the workspace; coding-assistant paths "
                        "must be workspace-relative, and nothing unconfined is offered for approval."
                    ),
                    proposal_id=proposal_id,
                )
            # Inherited from the retired in-process workflow (REASON_FIX_WITHOUT_OWNER): the owning
            # file must have been READ through the boundary before a repair is proposed. The defect
            # names a path; a proposal for a path this task never read is a guess wearing approval's
            # clothes.
            defect_path = str((task.defect or {}).get("path") or "").strip()
            if defect_path and defect_path not in task.read_paths:
                return self._result(
                    task,
                    ok=False,
                    status="owner_not_read",
                    text=(
                        f"`{defect_path}` has not been read through the task plane yet; read it as a "
                        "`code.task.step` with intent `workspace.read_file` first, then propose its repair."
                    ),
                    proposal_id=proposal_id,
                )
            if proposal_id in task.proposals:
                existing = task.proposals[proposal_id]
                # The mission's replay contract: byte-identical retries replay the recorded proposal
                # and name the lawful next action; a different request under a recorded id is a typed
                # conflict (never a silent replay of the obsolete proposal). A destination-less
                # (legacy) record additionally gets its preview UPGRADED with the destinations this
                # request binds, so nothing is ever approved against a destination nobody saw -- and
                # the approve-side drift check still refuses any change between preview and approval.
                unit_id = str(arguments.get("unit") or "").strip() or proposal_id
                replayed = self._replay_or_conflict(
                    task, existing, intent=intent, inner=inner, targets=targets, unit=unit_id)
                if replayed.details.get("replayed") and existing.intent in _DESTINATION_INTENTS \
                        and not existing.consumed_by and not (existing.preview or {}).get("destinations"):
                    destinations, _unresolvable = _destination_records(task, existing.intent, existing.arguments)
                    if destinations:
                        existing.preview = {**(existing.preview or {}), "destinations": destinations}
                        self._persist(task)
                return replayed
            # The repair-unit law and the reviewed base (the coding mission's binding machinery):
            # a unit is declared in full before its first change, changes each file once, and the
            # proposal binds the exact reviewed base state its writer must still find.
            unit_id = str(arguments.get("unit") or "").strip() or proposal_id
            members = _unit_members(task, unit_id)
            started = [m.proposal_id for m in members if m.consumed_by or m.reserved_by]
            if started:
                return self._result(
                    task, ok=False, status="unit_closed", proposal_id=proposal_id,
                    text=(
                        f"repair unit `{unit_id}` has already started landing (`{started[0]}`); a unit is declared in "
                        "full before its first change. Propose this change as its own repair (omit `unit`) and it is "
                        "validated on its own."
                    ),
                )
            overlap = sorted({t_ for m in members if not m.invalidated for t_ in m.targets} & set(targets))
            if overlap:
                return self._result(
                    task, ok=False, status="unit_duplicate_target", proposal_id=proposal_id,
                    text=(
                        f"repair unit `{unit_id}` already changes `{overlap[0]}`; a unit changes each file once -- fold "
                        "both edits into one proposal."
                    ),
                )
            base, refusal = self._resolve_reviewed_base(task, intent, inner, targets)
            if refusal is not None:
                status, text = refusal
                return self._result(task, ok=False, status=status, text=text, proposal_id=proposal_id)
            preview = {
                **preview,
                "targets": list(targets),
                "base": {target: state_token(base[target]) for target in targets},
                "binding_sha256": self._binding_digest(task, intent, inner, targets, base),
                "unit": unit_id,
            }
            if intent in _DESTINATION_INTENTS:
                destinations, unresolvable = _destination_records(task, intent, inner)
                if unresolvable:
                    return self._result(
                        task,
                        ok=False,
                        status="unconfined_path",
                        text=(
                            f"`{unresolvable}` does not resolve to something its writer may change inside the "
                            "workspace; nothing unresolvable is offered for approval."
                        ),
                        proposal_id=proposal_id,
                    )
                preview = {**preview, "destinations": destinations}
            task.proposals[proposal_id] = Proposal(
                proposal_id=proposal_id,
                intent=intent,
                arguments=inner,
                preview=preview,
                rationale=rationale,
                created_at=_utcnow(),
                targets=list(targets),
                base=base,
                unit=unit_id,
            )
            if task.stage == "propose":
                task.stage = "approve"
            self._persist(task)
            return self._result(
                task,
                ok=True,
                status="ok",
                text=f"Repair proposal `{proposal_id}` previewed; nothing was written.",
                proposal_id=proposal_id,
                preview=preview,
            )

    def approve(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            if task.stage == STAGE_CANCELLED:
                return self._result(
                    task, ok=False, status="cancelled", text="The task is cancelled; nothing further executes."
                )
            proposal = task.proposals.get(str(arguments.get("proposal_id") or "").strip())
            if proposal is None:
                return self._result(task, ok=False, status="unknown_proposal", text="No such proposal to approve.")
            if task.stage not in {"approve", "mutate"}:
                return self._result(
                    task, ok=False, status="stage_violation", text=f"Cannot approve at stage `{task.stage}`."
                )
            if not proposal.consumed_by:
                # Approval binds the destinations the preview recorded, as they are now: a link
                # retargeted, or bytes changed, since the preview means the operator would approve
                # something nobody reviewed.
                drift, drift_path = _destination_drift(task, proposal)
                if drift:
                    return self._result(
                        task,
                        ok=False,
                        status=_INVALIDATION_STATUS.get(drift, drift),
                        text=_DESTINATION_REFUSALS[drift].format(path=drift_path),
                        proposal_id=proposal.proposal_id,
                    )
            proposal.approved = True
            proposal.approved_at = _utcnow()
            task.stage = "mutate"
            self._persist(task)
            return self._result(
                task,
                ok=True,
                status="ok",
                text=f"Proposal `{proposal.proposal_id}` approved; the matching mutation may now execute.",
                proposal_id=proposal.proposal_id,
                preview=proposal.preview,
            )

    def cancel(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            task.cancelled_reason = str(arguments.get("reason") or "cancelled").strip()
            task.stage = STAGE_CANCELLED
            # Reach INTO any command this task currently has in flight: the event is the same
            # one the inner sandbox run was handed, so a running job is killed, not merely told
            # to be the last effect.
            self._task_cancel_event(task.task_id).set()
            self._file_fault(
                task, "cancelled", dedupe=f"{task.task_id}:cancel", context={"reason": task.cancelled_reason}
            )
            self._persist(task)
            return self._result(
                task,
                ok=True,
                status="ok",
                text="Task cancelled: no further effect will execute.",
                reason=task.cancelled_reason,
            )

    # -- step: the only place a proposal becomes an effect ----------------------

    def _classify(self, intent: str) -> tuple[str, Any]:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        contract = runtime_tool_contract_map().get(intent)
        if contract is None:
            return "unknown", None
        if intent.startswith("code.task."):
            return "self", contract
        if intent in COMMAND_INTENTS:
            return "command", contract
        if contract.read_only:
            return "read", contract
        return "mutation", contract

    def _abandoned_claim(self, task: CodeTask, proposal: Proposal) -> bool:
        """Release a claim held by another runtime instance that stopped mid-step (see
        ``_RESERVATION_ABANDON_SECONDS``). The released step is recorded as interrupted."""
        if not proposal.reserved_by or proposal.reserved_instance == self._instance:
            return False
        try:
            claimed = datetime.fromisoformat(proposal.reserved_at)
        except ValueError:
            return False
        age = (datetime.now(timezone.utc) - claimed).total_seconds()
        if age < _RESERVATION_ABANDON_SECONDS:
            return False
        holder = task.steps.get(proposal.reserved_by)
        if holder is not None and holder.status == "in_flight":
            holder.status = "interrupted"
            holder.completed_at = _utcnow()
            holder.reason = (
                f"the runtime instance that claimed approval `{proposal.proposal_id}` stopped before recording "
                f"this step; its claim was released after {int(age)}s and the reviewed-base precondition decides "
                "any later execution"
            )
        proposal.reserved_by = proposal.reserved_instance = proposal.reserved_at = ""
        return True

    def _admit(self, task: CodeTask, step: StepRecord, kind: str) -> tuple[str, str, Proposal | None]:
        """Decide, from the journal alone, whether this step may execute at this stage."""
        if task.stage == STAGE_CANCELLED:
            return "cancelled", f"the task was cancelled ({task.cancelled_reason}); nothing further executes", None
        if kind == "unknown":
            return "unknown_intent", f"`{step.intent}` is not a contracted tool", None
        if kind == "self":
            return "invalid_arguments", "a code task step cannot nest another code task call", None
        if kind == "command" and task.stage not in COMMAND_STAGES:
            # Inherited from the retired in-process workflow's family/stage table: a command is an
            # evidence act (reproduce, diagnose, narrow, cumulative), so a command at any other
            # stage -- in particular between an approval and its mutation -- is order disobedience,
            # refused by the runtime rather than by the model's good manners.
            return (
                "stage_violation",
                f"`{step.intent}` may not run at stage `{task.stage}`: commands are lawful only at "
                + ", ".join(sorted(COMMAND_STAGES)),
                None,
            )
        if kind in {"read", "command"}:
            return "", "", None
        if task.stage != "mutate" and not (task.stage in {"narrow_test", "cumulative"} and _pending_proposals(task)):
            return (
                "stage_violation",
                f"a mutation is refused at stage `{task.stage}`: reproduce the failure, identify the defect, propose and approve the repair first",
                None,
            )
        key = _sha(_canonical(self._bind_key(step.arguments)))
        candidates = [
            p for p in _ordered_proposals(task)
            if p.intent == step.intent and p.preview.get("arguments_sha256") == key
        ]
        live = [p for p in candidates if _live(p)]
        for proposal in live:
            self._abandoned_claim(task, proposal)
            if proposal.reserved_by:
                continue
            # Only a live, unclaimed approval may be selected. Invalidated predecessors
            # can have identical replacement arguments after a fresh review, and a claim
            # held by another process must not be overwritten by this step.
            drift, drift_path = _destination_drift(task, proposal, reviewed_bytes=False)
            if drift:
                proposal.invalidated = {"reason": drift, "path": drift_path, "at": _utcnow()}
                return (
                    _INVALIDATION_STATUS.get(drift, drift),
                    _DESTINATION_REFUSALS[drift].format(path=drift_path),
                    None,
                )
            return "", "", proposal
        if live:
            holder = live[0]
            return (
                "approval_in_flight",
                f"approval `{holder.proposal_id}` is already executing as step `{holder.reserved_by}`; wait for that "
                "step's result instead of executing the approval again",
                None,
            )
        consumed = [p for p in candidates if p.consumed_by]
        if consumed:
            return (
                "approval_consumed",
                f"approval `{consumed[-1].proposal_id}` was already executed by step `{consumed[-1].consumed_by}`; an "
                "approval executes once -- propose and approve a new repair for any further change",
                None,
            )
        invalid = [p for p in candidates if p.approved and p.invalidated]
        if invalid:
            status, reason = _invalidation_refusal(invalid[-1])
            return status, reason, None
        return (
            "approval_mismatch",
            "the mutation does not match any approved proposal byte-for-byte; propose and approve exactly what will be written",
            None,
        )

    def _mutation_precondition(self, task: CodeTask, step: StepRecord, proposal: Proposal | None) -> tuple[str, str]:
        """Decided under the task's write serialization, immediately before the physical writer."""
        if task.stage == STAGE_CANCELLED:
            return "cancelled", f"the task was cancelled ({task.cancelled_reason}); nothing further executes"
        if proposal is None:
            return "approval_mismatch", "the approved proposal this step matched is no longer in the journal"
        if proposal.consumed_by and proposal.consumed_by != step.step_id:
            return (
                "approval_consumed",
                f"approval `{proposal.proposal_id}` was already executed by step `{proposal.consumed_by}`; an approval "
                "executes once -- propose and approve a new repair for any further change",
            )
        if proposal.invalidated:
            return _invalidation_refusal(proposal)
        if proposal.reserved_by != step.step_id:
            return (
                "approval_in_flight",
                f"approval `{proposal.proposal_id}` is claimed by step `{proposal.reserved_by or '(none)'}`, not by this step",
            )
        single = step.intent in CONTENT_HASH_INTENTS and len(proposal.targets) == 1
        supplied = str(step.arguments.get("expected_hash") or "").strip().lower()
        if supplied and single:
            # An explicit hash that is not the reviewed base never reaches the writer: a caller naming
            # different prior content is not fresh consent, whether or not the file really changed.
            target = proposal.targets[0]
            reviewed = state_token(proposal.base.get(target))
            if "file:" + supplied != reviewed:
                actual = state_token(_disk_state(self._target_path(task, target)))
                if actual != reviewed:
                    proposal.invalidated = {"reason": "reviewed_base_changed", "path": target, "reviewed": reviewed,
                                            "observed": actual, "step_id": step.step_id, "at": _utcnow()}
                    return (
                        "stale_base",
                        f"`{target}` changed after this repair was reviewed (reviewed {_short(reviewed)}, now "
                        f"{_short(actual)}), and the supplied `expected_hash` names that new content -- a new hash is "
                        f"not fresh consent. Approval `{proposal.proposal_id}` no longer applies and nothing was "
                        f"written. Re-read `{target}` through `code.task.step` with intent `workspace.read_file`, "
                        "propose the repair against its current content, and approve that proposal.",
                    )
                return (
                    "approval_mismatch",
                    f"approval `{proposal.proposal_id}` binds the reviewed base {_short(reviewed)} of `{target}`; the "
                    f"supplied `expected_hash` `{supplied[:12]}…` is not what was reviewed, and a new hash is not fresh "
                    "consent. Execute the approved arguments as approved, or re-read, propose against the content "
                    "you read, and approve that proposal.",
                )
        for target in proposal.targets:
            reviewed_state = proposal.base.get(target) or {}
            if single and reviewed_state.get("kind") == "file":
                # The physical writer enforces exactly this base as its own `expected_hash` precondition
                # (see `_dispatch_arguments`), so its refusal crosses the door and the flight recorder
                # keeps the attempted effect. Checking here too would hide that record.
                continue
            # Everything the writer cannot enforce -- a file that must still be absent, a directory,
            # a multi-target change -- is checked here, under the same serialization.
            reviewed = state_token(reviewed_state)
            actual = state_token(_disk_state(self._target_path(task, target)))
            if reviewed != actual:
                proposal.invalidated = {"reason": "reviewed_base_changed", "path": target, "reviewed": reviewed,
                                        "observed": actual, "step_id": step.step_id, "at": _utcnow()}
                return (
                    "stale_base",
                    f"`{target}` changed after this repair was reviewed (reviewed {_short(reviewed)}, now "
                    f"{_short(actual)}); approval `{proposal.proposal_id}` no longer applies and nothing was written. "
                    f"Re-read `{target}` through `code.task.step` with intent `workspace.read_file`, propose the repair "
                    "against its current content, and approve that proposal.",
                )
        # The unit law: a part-applied unit finishes first; a unit starts only whole-approved; and a
        # new unit never runs over changes that have not been validated at this revision.
        unit = _unit_of(proposal)
        in_progress = _unit_in_progress(task)
        if in_progress and in_progress != unit:
            remaining = [m.proposal_id for m in _unit_members(task, in_progress) if not m.consumed_by and not m.invalidated]
            return (
                "unit_in_progress",
                f"repair unit `{in_progress}` is part-applied: execute its remaining approved change(s) "
                + ", ".join(f"`{item}`" for item in remaining)
                + f" through `code.task.step` before `{proposal.proposal_id}` starts; a unit is validated after all "
                "of its changes land",
            )
        if not in_progress:
            waiting = [m.proposal_id for m in _unit_members(task, unit) if not m.approved or m.invalidated]
            if waiting:
                return (
                    "unit_not_approved",
                    f"repair unit `{unit}` starts only when every change in it is approved and applicable, and "
                    + ", ".join(f"`{item}`" for item in waiting)
                    + " is not; a declared unit never lands half-way",
                )
            if not _checkpoint_validated(task):
                owed = "the focused check" if _current_outcome(task, "narrow") is None else "the full check"
                landed = str(task.checkpoint.get("unit") or "the last repair")
                return (
                    "checkpoint_required",
                    f"`{landed}` landed at revision {task.revision} and has not been validated: run {owed} through "
                    f"`code.task.step` with intent `workspace.run_tests` before the next repair (`{proposal.proposal_id}`) "
                    "runs; its approval stays valid meanwhile",
                )
        return "", ""

    @staticmethod
    def _dispatch_arguments(step: StepRecord, proposal: Proposal) -> dict[str, Any]:
        """The arguments the physical writer receives: the approved ones, with the reviewed base as
        the writer's own ``expected_hash`` precondition whenever that writer enforces one."""
        arguments = dict(step.arguments)
        if step.intent in CONTENT_HASH_INTENTS and len(proposal.targets) == 1:
            reviewed = proposal.base.get(proposal.targets[0]) or {}
            if reviewed.get("kind") == "file" and not str(arguments.get("expected_hash") or "").strip():
                arguments["expected_hash"] = str(reviewed.get("sha256") or "")
        return arguments

    def _record_read_snapshot(self, task: CodeTask, step: StepRecord, *, absent: bool = False) -> None:
        """The task's view of a file is what its latest read returned -- unless a task mutation
        landed while that read ran, in which case the read may predate the write and is ignored."""
        if step.revision_at != task.revision:
            return
        raw = str(step.result.get("path") or (step.paths[0] if step.paths else ""))
        target = canonical_target(task.workspace_root, raw)
        if not target:
            return
        if absent:
            state = {"kind": "absent", "sha256": ""}
        else:
            digest = str(step.result.get("hash") or "")
            if not digest:
                return
            state = {"kind": "file", "sha256": digest}
        now = _utcnow()
        task.snapshots[target] = {**state, "source": "read", "step_id": step.step_id, "revision": task.revision, "at": now}
        token = state_token(state)
        superseded = False
        for proposal in _ordered_proposals(task):
            if proposal.consumed_by or proposal.invalidated or proposal.reserved_by:
                continue
            if target in proposal.targets and state_token(proposal.base.get(target)) != token:
                proposal.invalidated = {"reason": "reviewed_base_superseded", "path": target, "observed": token,
                                        "step_id": step.step_id, "at": now}
                superseded = True
        if superseded:
            _settle_stage(task)

    def _repair_fingerprint(self, task: CodeTask) -> dict[str, str]:
        """The on-disk identity of every file this task's executed repairs changed."""
        paths = sorted({target for p in task.proposals.values() if p.consumed_by for target in p.targets})
        return {path: state_token(_disk_state(self._target_path(task, path))) for path in paths}

    @staticmethod
    def _invocation_of(intent: str, arguments: dict[str, Any]) -> Invocation:
        """The effective invocation of an evidence command, its default resolved the way the
        validation tool resolves it."""
        from core.execution.validation_tools import validation_command

        try:
            command = str(arguments.get("command") or "").strip() or validation_command(intent, arguments)
        except ValueError:
            command = str(arguments.get("command") or "").strip()
        # The validation runner executes `pytest`/`python3 -m pytest` as this interpreter; a sandbox
        # command runs its program verbatim.
        return _effective_invocation(command, validation=intent in TEST_INTENTS)

    @staticmethod
    def _invocation_record(task: CodeTask, intent: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The versioned persisted identity of an evidence command's effective invocation.

        The RAW command and cwd stay the journal's record (they always were); this additive
        ``v``-keyed record is the structured identity the coverage/repeat decisions were made
        under, so an auditor can see WHICH contract certified a verification. Old journals have
        no record: their proof cannot be recovered under this contract, and completion is
        recomputed live rather than trusted from stale coverage fields."""
        invocation = CodeTaskRuntime._invocation_of(intent, arguments)
        return {
            # v2 (revision 4): ordered argv, whole option values, the resolved executable.
            "v": 2,
            "program": invocation.program,
            "runner": invocation.runner,
            "family": invocation.family,
            "entry": invocation.entry,
            "interpreter": [[flag, list(values)] for flag, values in invocation.interpreter],
            "options": [[flag, value] for flag, value in invocation.options],
            "operands": list(invocation.operands),
            "args": list(invocation.args),
            "cwd": _cwd_identity(arguments.get("cwd")),
        }

    def _resolved_base_dir(self, task: CodeTask, arguments: dict[str, Any]) -> Path:
        """The directory a check's command runs in, resolved the way EXECUTION resolves it: the
        declared cwd against the workspace root, a file cwd resolved to its parent. This is the
        only honest base for binding named inputs -- resolving selectors at the workspace root
        bound an unrelated same-name root file while the executed check under `checks/` moved
        freely (measured by the independent review)."""
        from core.execution.workspace_tools import resolve_workspace_path

        raw = _cwd_identity(arguments.get("cwd"))
        if not raw:
            return Path(task.workspace_root).resolve()
        try:
            base = resolve_workspace_path(raw, workspace_root=Path(task.workspace_root))
        except (ValueError, OSError):
            return Path(task.workspace_root).resolve()
        if base.is_file():
            base = base.parent
        return base.resolve()

    def _resolved_operand(self, task: CodeTask, invocation: Invocation, arguments: dict[str, Any],
                          token: str) -> tuple[str, Path | None]:
        """(canonical workspace-relative identity, resolved path) of one operand token under the
        command's resolved working directory, or ("", None) when it escapes or cannot resolve.

        Known node-id selectors (pytest ``path::node`` forms) bind their REAL FILE: the ``::``
        suffix selects inside the file, it does not name another input. Directory and opaque
        operands resolve as themselves -- never silently promoted into byte evidence."""
        base = self._resolved_base_dir(task, arguments)
        root = Path(task.workspace_root)
        candidate = Path(token)
        target = candidate if candidate.is_absolute() else base / candidate
        try:
            resolved = target.resolve()
            relative = resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            return "", None
        if str(relative) == ".":
            return "", resolved
        return str(relative), resolved

    def _input_fingerprint(self, task: CodeTask, arguments: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        """(identity of the workspace files a check's command names as operands, coverage meta).

        The inputs are the operands the command DECLARES it selects on -- the executed script
        itself first for a script invocation, then its non-option arguments and the runner's
        positional operands -- resolved under the command's RESOLVED working directory, exactly
        as execution resolves them, canonically workspace-relative so the same identity can be
        recomputed at report time or after a restart from the recorded command/cwd alone. The
        binding is bounded to eight named inputs and says so: `truncated` marks a command that
        named more than the binding covers, and the meta never claims complete evidence for
        inputs it did not bind. No whole-tree dependency scanning -- only what the check itself
        declared it executes."""
        intent = str(arguments.get("intent") or "workspace.run_tests")
        invocation = self._invocation_of(intent, arguments)
        declared = invocation.declared_operands()
        named = sorted({node_file(token) if invocation.family == "pytest" else token for token in declared})
        inputs: dict[str, str] = {}
        for selector in named[:8]:
            if selector in task.snapshots and task.snapshots[selector].get("source") == "mutation":
                continue  # repaired files are the repair fingerprint's business, not inputs
            relative, resolved = self._resolved_operand(task, invocation, arguments, selector)
            if resolved is None:
                continue  # escapes the workspace or unresolvable: not a bindable input
            kind = _disk_state(resolved)
            if kind["kind"] not in {"file", "directory"}:
                continue
            token = state_token(kind)
            if token:
                inputs[relative or str(resolved)] = token
        meta = {
            "named": len(named),
            "bound": len(inputs),
            "truncated": len(named) > 8,
            "coverage": "declared",
        }
        return inputs, meta

    def _evidence_fingerprint(self, task: CodeTask, arguments: dict[str, Any]) -> dict[str, str]:
        """What a verification's bytes binding covers: the repaired files AND the check's own
        named inputs, resolved under the check's declared working directory. A green outcome is
        evidence about exactly these bytes; when either side changes, the evidence describes a
        workspace that no longer exists."""
        inputs, _meta = self._input_fingerprint(task, arguments)
        return {**self._repair_fingerprint(task), **inputs}

    def _obligation_invocation(self, task: CodeTask) -> Invocation | None:
        """The task's RETAINED acceptance obligation: the effective invocation of the command
        whose failure the user asked repaired -- exactly as it was recorded at reproduce time,
        with its own working directory, so it survives revised repairs, restarts and report
        time."""
        record = task.reproduced_failure if isinstance(task.reproduced_failure, dict) else None
        if not record or not str(record.get("command") or "").strip():
            return None
        return self._invocation_of(str(record.get("intent") or "workspace.run_tests"),
                                   {"command": record.get("command"), "cwd": record.get("cwd")})

    def _entry_identity(self, task: CodeTask, invocation: Invocation, arguments: dict[str, Any]) -> str:
        """The canonical identity of the file (or inline code) a command EXECUTES, resolved
        against ITS OWN working directory. Two same-basename scripts under different cwds are
        different entries; a cwd alias naming the same directory resolves to the same entry."""
        if invocation.family == "python-inline":
            return "inline:" + _sha(invocation.entry)
        if invocation.family in {"python-script", "python-module"} and invocation.entry:
            if invocation.family == "python-script":
                relative, _resolved = self._resolved_operand(task, invocation, arguments, invocation.entry)
                return "script:" + (relative or invocation.entry)
            return "module:" + invocation.entry
        if invocation.family == "pytest":
            return "pytest"
        if invocation.family == "python-diagnostic":
            return "diagnostic:" + invocation.runner
        return "generic:" + invocation.runner

    def _default_suite_invocation(self, intent: str) -> Invocation | None:
        """The validation tool's own full-suite invocation, however it is spelled."""
        if intent not in TEST_INTENTS:
            return None
        from core.execution.validation_tools import runtime_validation_command, validation_command

        try:
            return _effective_invocation(runtime_validation_command(validation_command(intent, {})))
        except ValueError:
            return None

    def _obligation_state(self, task: CodeTask, intent: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Whether an evidence command is a GENUINE check of this task's repair, and whether its
        effective invocation COVERS the retained acceptance obligation. Two different questions:

        * genuine -- the command exercises the workspace through the SAME effective runner that
          expressed the acceptance condition (the reproduction's own runner), with an executed
          entry that exists under its resolved working directory -- a script file for the python
          family, a known verification runner for pytest, an operand file for an unknown runner;
          or it is the validation tool's own default suite form (`workspace.run_tests` with no
          explicit command). A tool name, an interpreter match or a filename appearing anywhere
          in argv is not proof of the required behavior (measured by the independent review:
          `cat calc.py`, `python3 -c "print(123)"`, `echo ok`, an argument-only `check.py` and a
          same-basename decoy under another cwd all advanced a known wrong repair).
        * covers -- the verification's effective invocation represents the retained acceptance
          check, or a well-defined broader verification that includes it: the same resolved
          executed entry under its own cwd for script checks; the pytest adapter's documented
          selection semantics for suite checks; the retained invocation itself for unknown
          runners. Interpreter modes that weaken the acceptance condition (`-O` strips the
          assertions the obligation's check asserts under) never cover it. This is DECLARED
          coverage, not proven coverage: unknown scope stays unknown, and completion is refused
          rather than invented."""
        obligation = self._obligation_invocation(task)
        invocation = self._invocation_of(intent, arguments)
        state = {"genuine": False, "covers_obligation": None, "coverage_kind": "declared", "reason": ""}
        if obligation is None:
            # No derivable obligation (a hand-edited journal). Genuine falls back to the
            # workspace-validation-surface test; coverage stays UNKNOWN and completion is
            # refused rather than invented.
            state["genuine"] = self._genuine_surface(task, invocation, intent, arguments)
            state["reason"] = "the journal records no reproduced acceptance obligation to measure coverage against"
            return state
        default_suite = self._default_suite_invocation(intent)
        is_default = bool(default_suite) and invocation.identity() == default_suite.identity()
        if not self._genuine_against(task, invocation, obligation, intent, arguments, is_default):
            state["reason"] = (
                f"the command is not the acceptance obligation's own check (`{obligation.runner}` executing "
                f"`{obligation.declared_operands()[0] if obligation.declared_operands() else obligation.entry}` "
                "under its working directory); it may run as a diagnostic, but a green exit proves nothing "
                "about the obligation"
            )
            return state
        state["genuine"] = True
        covered, reason = self._coverage_of(task, invocation, obligation, arguments)
        state["covers_obligation"] = covered
        state["reason"] = reason
        return state

    def _genuine_surface(self, task: CodeTask, invocation: Invocation, intent: str,
                         arguments: dict[str, Any]) -> bool:
        """Whether a command exercises a workspace validation surface at all (no obligation to
        compare against): the validation tool's own suite form, a script that exists under its
        cwd, a known verification runner, or an operand file of an unknown runner."""
        if intent in TEST_INTENTS and self._default_suite_invocation(intent) is not None:
            return True
        if invocation.family == "python-script":
            _relative, resolved = self._resolved_operand(task, invocation, arguments, invocation.entry)
            return resolved is not None and resolved.is_file()
        if invocation.family == "pytest":
            return True
        resolved = [self._resolved_operand(task, invocation, arguments, token)[1]
                    for token in invocation.declared_operands()]
        return any(target is not None and target.is_file() for target in resolved)

    def _genuine_against(self, task: CodeTask, invocation: Invocation, obligation: Invocation, intent: str,
                         arguments: dict[str, Any], is_default: bool) -> bool:
        """Whether the command is a genuine check THROUGH THE OBLIGATION'S OWN effective runner:
        same runner identity plus an executed surface (an entry file that exists, a known
        verification runner, or -- for an unknown runner -- an operand that exists under the
        command's resolved cwd)."""
        if invocation.runner != obligation.runner:
            return False
        if is_default:
            return True
        if invocation.family == "python-script":
            _relative, resolved = self._resolved_operand(task, invocation, arguments, invocation.entry)
            return resolved is not None and resolved.is_file()
        if invocation.family == "pytest":
            return True
        return any(
            self._resolved_operand(task, invocation, arguments, token)[1] is not None
            and self._resolved_operand(task, invocation, arguments, token)[1].is_file()
            for token in invocation.declared_operands()
        )

    def _coverage_of(self, task: CodeTask, invocation: Invocation, obligation: Invocation,
                     arguments: dict[str, Any]) -> tuple[bool | None, str]:
        """(covers, reason) of a GENUINE verification against the retained obligation, by the
        runner family's documented adapter. ``True`` only for provable inclusion; ``False`` for
        a provable miss; ``None`` for a scope that cannot be proven -- which holds the full-check
        checkpoint with the retained check as the lawful next action, and never completes."""
        retained = f"the obligation was reproduced by `{task.reproduced_failure.get('command')}`" \
            + (f" in `{task.reproduced_failure.get('cwd')}`" if str(task.reproduced_failure.get("cwd") or "") else "")
        verification_arguments = arguments
        obligation_arguments = {"command": task.reproduced_failure.get("command"),
                                "cwd": task.reproduced_failure.get("cwd"),
                                "intent": str(task.reproduced_failure.get("intent") or "workspace.run_tests")}
        same_directory = (self._resolved_base_dir(task, verification_arguments)
                          == self._resolved_base_dir(task, obligation_arguments))
        if invocation.family in {"python-script", "python-inline"} \
                and obligation.family in {"python-script", "python-inline"}:
            if invocation.family != obligation.family:
                return False, (
                    "the retained acceptance obligation is a script the runner executes, while this check "
                    f"executes inline code -- `{invocation.raw}` runs no workspace acceptance file; {retained}"
                )
            verification_entry = self._entry_identity(task, invocation, verification_arguments)
            obligation_entry = self._entry_identity(task, obligation, obligation_arguments)
            if verification_entry != obligation_entry:
                where = ""
                if invocation.family == "python-script":
                    _v_rel, v_resolved = self._resolved_operand(task, invocation, verification_arguments,
                                                                invocation.entry)
                    _o_rel, o_resolved = self._resolved_operand(task, obligation, obligation_arguments,
                                                                obligation.entry)
                    if v_resolved is not None and o_resolved is not None:
                        where = f" (`{v_resolved}` is not `{o_resolved}`)"
                return False, (
                    "the executed check is not the retained acceptance obligation's own entry point"
                    f"{where}: identical basenames under different working directories are different "
                    f"obligations; {retained}"
                )
            weak, weak_reason = self._weakening_modes(invocation, obligation)
            if weak is False:
                return False, weak_reason
            if invocation.program != obligation.program:
                return None, self._program_reason(invocation, obligation, retained)
            if weak is None:
                return None, weak_reason
            if invocation.args != obligation.args:
                return None, (
                    "the executed script received different program arguments, in order (`"
                    + " ".join(invocation.args) + "` vs `" + " ".join(obligation.args) + "`); a script's argv is "
                    "ordered and its values are part of the check, so the journal cannot prove the same "
                    "acceptance condition ran; " + retained
                )
            if not same_directory:
                return None, (
                    "the retained script ran under a different working directory; paths it resolves relative "
                    "to its cwd may name other files, so coverage is unknown rather than assumed; " + retained
                )
            return True, ""
        if invocation.family == "pytest" and obligation.family == "pytest":
            return self._pytest_covers(task, invocation, obligation, verification_arguments,
                                       obligation_arguments, retained)
        if invocation.identity() == obligation.identity() and same_directory:
            # An unknown runner's own retained check, re-run verbatim under the same resolved
            # working directory: it IS the obligation. Nothing broader is provable about a
            # wrapper this contract does not document.
            weak, weak_reason = self._weakening_modes(invocation, obligation)
            if weak is False:
                return False, weak_reason
            return weak, weak_reason or ""
        return False, (
            f"the command does not execute the retained acceptance obligation's own check and no documented "
            f"verification adapter covers `{obligation.raw or obligation.runner}`; re-run the retained check "
            f"`{obligation.raw}` (cwd `{obligation_arguments.get('cwd') or '.'}`) or a check a known runner "
            f"defines as broader; {retained}"
        )

    @staticmethod
    def _program_reason(invocation: Invocation, obligation: Invocation, retained: str) -> str:
        return (
            f"the check runs under a different executable (`{invocation.program.split(os.pathsep)[-1]}`) than the "
            f"retained acceptance obligation (`{obligation.program.split(os.pathsep)[-1]}`); coverage is unknown "
            f"rather than assumed; {retained}"
        )

    def _weakening_modes(self, invocation: Invocation, obligation: Invocation) -> tuple[bool | None, str]:
        """(not-weakened, reason) for the verification's INTERPRETER modes against the obligation's.

        ``False``: a mode that can only weaken the acceptance condition. More ``-O`` than the obligation
        ran under strips ``assert`` statements -- under pytest from every module pytest does not rewrite
        (helpers, the code under test), and with ``--assert=plain`` from the tests themselves, where pytest
        itself warns that ASSERTIONS ARE NOT EXECUTED (measured on 9.1.0). ``-i`` exits 0 after a failing
        program once stdin closes. ``None``: a mode this contract cannot classify, on either side.
        ``True``: the same acceptance-relevant modes (safe modes and fewer ``-O`` never weaken)."""
        def optimize(target: Invocation) -> int:
            return sum(1 for flag, _values in target.interpreter if flag == _PY_OPTIMIZE)

        if optimize(invocation) > optimize(obligation):
            if invocation.family == "pytest":
                plain = invocation.last_value("--assert") == "plain"
                return False, (
                    "the check runs pytest under interpreter mode `-O`, which strips `assert` statements from every "
                    "module pytest does not rewrite (helpers, the code under test)"
                    + (" and, with `--assert=plain`, from the tests themselves -- pytest warns that ASSERTIONS ARE "
                       "NOT EXECUTED" if plain else "")
                    + "; a green exit proves nothing about the retained acceptance condition"
                )
            return False, (
                "the check runs under interpreter mode `-O`, which sets `__debug__` false and strips `assert` "
                "statements -- a green exit proves nothing about the retained acceptance condition the "
                "obligation's check asserts under"
            )
        remaining = [item for item in obligation.interpreter if item[0] != _PY_OPTIMIZE]
        for item in (item for item in invocation.interpreter if item[0] != _PY_OPTIMIZE):
            if item in remaining:
                remaining.remove(item)
                continue
            flag = item[0]
            if flag == _PY_INSPECT:
                return False, (
                    "the check runs under interpreter mode `-i`, which exits 0 after a failing program once "
                    "standard input closes -- a green exit proves nothing about the retained acceptance condition"
                )
            if flag in _PY_SAFE_INTERP:
                continue
            return None, (
                f"the check runs under interpreter mode `{flag}`, whose effect on the executed program "
                "this contract cannot classify; coverage is unknown rather than assumed"
            )
        for flag, _values in remaining:
            if flag in _PY_SAFE_INTERP:
                continue
            return None, (
                f"the retained acceptance check ran under interpreter mode `{flag}` this check drops; the "
                "mode may carry acceptance semantics, so coverage is unknown rather than assumed"
            )
        return True, ""

    def _pytest_covers(self, task: CodeTask, invocation: Invocation, obligation: Invocation,
                       verification_arguments: dict[str, Any], obligation_arguments: dict[str, Any],
                       retained: str) -> tuple[bool | None, str]:
        """The pytest adapter: whether a green run PROVABLY executed the retained selection.

        * Interpreter modes compose with pytest's (``_weakening_modes``); a different executable is unknown.
        * The retained invocation itself, re-run under the same resolved working directory, covers.
        * Options (``_pytest_option_bars``): modes that run no test never cover; ``-k``/``-m`` compare by
          their EFFECTIVE last value; appended filters as canonical sets; cache filters must be the
          obligation's own; scope-changing options and warning filters must keep the obligation's; an
          unclassified option is unknown. Obligation-side filters may drop: the unfiltered run is a superset.
        * Selection (``_pytest_selection_covers``): both sides resolve to canonical collection arguments
          under their OWN working directory and configuration -- node ids with parametrization, files,
          directories and the no-argument form's real scope -- and every retained argument must be included
          by the check's: the same file with an enclosing node, or a directory whose recursion pytest would
          actually follow to it. This is selection inclusion only; source-byte binding stays
          ``_input_fingerprint``'s."""
        weak, weak_reason = self._weakening_modes(invocation, obligation)
        if weak is False:
            return False, f"{weak_reason}; {retained}"
        if invocation.program != obligation.program:
            return None, self._program_reason(invocation, obligation, retained)
        if weak is None:
            return None, f"{weak_reason}; {retained}"
        verification_base = self._resolved_base_dir(task, verification_arguments)
        obligation_base = self._resolved_base_dir(task, obligation_arguments)
        if invocation.identity() == obligation.identity() and verification_base == obligation_base:
            return True, ""
        inert = next((flag for flag, _value in invocation.options if flag in _PYTEST_INERT), "")
        if inert:
            return False, (
                f"the check runs pytest with `{inert}`, which only collects, plans, sets up or reports and does "
                f"not execute the tests; {retained}"
            )
        verdict, reason = self._pytest_option_bars(invocation, obligation, verification_base, obligation_base)
        if verdict is not True:
            return verdict, f"{reason}; {retained}"
        return self._pytest_selection_covers(task, invocation, obligation, verification_base, obligation_base,
                                             retained)

    @classmethod
    def _pytest_option_bars(cls, invocation: Invocation, obligation: Invocation, verification_base: Path,
                            obligation_base: Path) -> tuple[bool | None, str]:
        """(bars kept, reason): whether the check's pytest options keep every bar the retained
        obligation's options set, by the option classes documented at module level."""
        def occurrences(target: Invocation, flag: str) -> list[tuple[str, str | None]]:
            return [(name, value) for name, value in target.options if name == flag]

        cache_filtered = any(flag in _PYTEST_CACHE_FILTERS
                             for flag, _value in (*invocation.options, *obligation.options))

        def cosmetic(flag: str, value: str | None) -> bool:
            return flag == "-p" and value == "no:cacheprovider" and not cache_filtered

        checked = [value for flag, value in invocation.options if flag in _PYTEST_WARNING_FILTERS]
        required = [value for flag, value in obligation.options if flag in _PYTEST_WARNING_FILTERS]
        if checked != required and (checked[:len(required)] != required or not all(
                str(value or "").startswith("error") for value in checked[len(required):])):
            return None, (
                f"the check's warning filters ({checked or 'none'}) do not keep the retained acceptance "
                f"obligation's ({required or 'none'}); the last matching filter wins, so a changed list may hide "
                "the failure"
            )
        for flag, value in invocation.options:
            if flag in _PYTEST_NEUTRAL or flag in _PYTEST_STRICTER or flag in _PYTEST_WARNING_FILTERS \
                    or cosmetic(flag, value):
                continue
            if flag in _PYTEST_STORE_FILTERS:
                mine, theirs = invocation.last_value(flag), obligation.last_value(flag)
                if mine != theirs:
                    return False, (
                        f"the check filters the suite (`{flag} {mine}`; the last `{flag}` decides) differently than "
                        "the retained acceptance obligation ("
                        + (f"`{flag} {theirs}`" if theirs is not None else "unfiltered")
                        + "); its green exit does not prove the obligation's selection ran"
                    )
                continue
            if flag in _PYTEST_APPEND_FILTERS:
                if cls._pytest_filter_set(invocation, flag, verification_base) \
                        != cls._pytest_filter_set(obligation, flag, obligation_base):
                    return False, (
                        f"the check filters the suite with `{flag}` {list(invocation.option_values(flag))} "
                        "differently than the retained acceptance obligation "
                        f"({list(obligation.option_values(flag)) or 'unfiltered'}); its green exit does not prove "
                        "the obligation's selection ran"
                    )
                continue
            if flag in _PYTEST_CACHE_FILTERS:
                if not occurrences(obligation, flag):
                    return False, (
                        f"the check filters the suite by pytest's cache of earlier runs (`{flag}`), not by the "
                        "retained acceptance obligation's selection"
                    )
                continue
            if flag in _PYTEST_SCOPE:
                if occurrences(invocation, flag) != occurrences(obligation, flag) \
                        or verification_base != obligation_base:
                    return None, (
                        f"the check changes where pytest collects or how it imports (`{flag}`) differently than "
                        "the retained acceptance obligation; coverage is unknown rather than assumed"
                    )
                continue
            return None, (
                f"the check passes pytest option `{flag}` whose selection semantics this contract has not "
                "classified; coverage is unknown rather than assumed"
            )
        for flag, value in obligation.options:
            if flag in _PYTEST_NEUTRAL or flag in _PYTEST_WARNING_FILTERS or cosmetic(flag, value):
                continue
            if flag in _PYTEST_STORE_FILTERS or flag in _PYTEST_APPEND_FILTERS or flag in _PYTEST_CACHE_FILTERS:
                continue  # the obligation's own filter, dropped by a superset run
            if (flag in _PYTEST_STRICTER or flag in _PYTEST_SCOPE) \
                    and occurrences(invocation, flag) == occurrences(obligation, flag):
                continue
            return None, (
                f"the retained acceptance check passed pytest option `{flag}` this check drops or changes; the "
                "bar it raises (or whose effect is unclassified) may be exactly what is still failing"
            )
        return True, ""

    @staticmethod
    def _pytest_filter_set(invocation: Invocation, flag: str, base: Path) -> frozenset[str]:
        """A path-valued filter's values as pytest applies them: ``--ignore``/``--ignore-glob`` joined to
        the invocation directory (pytest's ``absolutepath``), ``--deselect`` prefixes bound to it."""
        values = [str(value or "") for value in invocation.option_values(flag)]
        if flag == "--deselect":
            return frozenset(f"{base}::{value}" for value in values)
        return frozenset(value if os.path.isabs(value) else os.path.normpath(str(base / value)) for value in values)

    @staticmethod
    def _shown(task: CodeTask, path: Path) -> str:
        try:
            return str(path.relative_to(Path(task.workspace_root).resolve())) or "."
        except ValueError:
            return str(path)

    def _pytest_scope(self, invocation: Invocation, base: Path) -> _CollectionScope:
        """What a pytest invocation run in ``base`` collects from (see :class:`_CollectionScope`): its
        collection arguments resolved and normalized as pytest does, or -- with none -- ini ``testpaths``
        when run from the rootdir, else the invocation directory itself; and the collection rules
        (``python_files``, ``norecursedirs``) of the configuration that governs it."""
        for flag in ("-c", "--config-file", "-o", "--override-ini", "--rootdir", "--pyargs"):
            if invocation.option_values(flag):
                return _CollectionScope(unknown=f"`{flag}` changes how pytest resolves its configuration or its "
                                                "collection arguments, which this contract does not model")
        arguments: list[_CollectionArgument] = []
        for token in invocation.operands:
            argument = _collection_argument(base, token)
            if argument is None:
                return _CollectionScope(unknown=f"the collection argument `{token}` does not resolve under `{base}`")
            arguments.append(argument)
        config, settings, error = _pytest_configuration(base, tuple(arguments))
        if error:
            return _CollectionScope(unknown=error)
        added_options, added_operands = _parse_pytest_arguments(_ini_args(settings, "addopts", ()))
        if added_operands or any(flag not in _PYTEST_NEUTRAL and flag not in _PYTEST_STRICTER
                                 for flag, _value in added_options):
            return _CollectionScope(unknown=f"the `addopts` of `{config}` add collection arguments or selection "
                                            "options this contract does not model")
        origin = "the collection arguments"
        if not arguments:
            testpaths = _ini_args(settings, "testpaths", ())
            if config is not None and testpaths and base == config.parent.resolve():
                for pattern in testpaths:
                    for found in sorted(glob.iglob(pattern if os.path.isabs(pattern) else str(base / pattern),
                                                   recursive=True)):
                        argument = _collection_argument(base, found)
                        if argument is not None:
                            arguments.append(argument)
                if arguments:
                    origin = f"ini `testpaths` of `{config.name}`"
            if not arguments:
                arguments = [_CollectionArgument(path=base)]
                origin = "the invocation directory"
        if not invocation.option_values("--keep-duplicates"):
            arguments = list(_normalized_collection(arguments))
        return _CollectionScope(arguments=tuple(arguments), origin=origin, config=str(config or ""),
                                python_files=_ini_args(settings, "python_files", _PYTEST_DEFAULT_PYTHON_FILES),
                                norecursedirs=_ini_args(settings, "norecursedirs", _PYTEST_DEFAULT_NORECURSEDIRS))

    def _pytest_selection_covers(self, task: CodeTask, invocation: Invocation, obligation: Invocation,
                                 verification_base: Path, obligation_base: Path,
                                 retained: str) -> tuple[bool | None, str]:
        """Whether the check's collection scope includes every retained collection argument."""
        checked = self._pytest_scope(invocation, verification_base)
        if checked.unknown:
            return None, f"the check's collection scope is unknown ({checked.unknown}); {retained}"
        required = self._pytest_scope(obligation, obligation_base)
        if required.unknown:
            return None, f"the retained acceptance obligation's collection scope is unknown ({required.unknown}); {retained}"
        if checked.config != required.config:
            return None, (
                f"a different pytest configuration governs the check (`{checked.config or 'none'}`) than the "
                f"retained acceptance obligation (`{required.config or 'none'}`); coverage is unknown rather than "
                f"assumed; {retained}"
            )
        unknown = ""
        for argument in required.arguments:
            included, reason = self._collection_includes(task, checked, argument, invocation, verification_base)
            if included is False:
                return False, f"{reason}; {retained}"
            if included is None and not unknown:
                unknown = reason
        if unknown:
            return None, f"{unknown}; {retained}"
        return True, ""

    def _collection_includes(self, task: CodeTask, scope: _CollectionScope, argument: _CollectionArgument,
                             invocation: Invocation, base: Path) -> tuple[bool | None, str]:
        """Whether one collection scope includes one retained collection argument: a subsuming argument
        on the same path (an enclosing node, the whole file, the same directory), or a subsuming directory
        whose recursion reaches it."""
        def shown(item: _CollectionArgument) -> str:
            return "::".join((self._shown(task, item.path), *item.parts)) + (item.parametrization or "")

        miss = (
            f"the retained acceptance obligation selects `{shown(argument)}`, which this check's collection ("
            + ", ".join(f"`{shown(item)}`" for item in scope.arguments)
            + f", from {scope.origin}) does not include; run a check that covers it"
        )
        unknown = ""
        for candidate in scope.arguments:
            if not _argument_subsumes(candidate, argument):
                continue
            if candidate.path == argument.path:
                return True, ""
            reached, reason = self._recursion_reaches(task, scope, candidate.path, argument.path, invocation, base)
            if reached is True:
                return True, ""
            if reached is None:
                unknown = unknown or reason
            else:
                miss = reason
        if unknown:
            return None, unknown
        return False, miss

    def _recursion_reaches(self, task: CodeTask, scope: _CollectionScope, top: Path, target: Path,
                           invocation: Invocation, base: Path) -> tuple[bool | None, str]:
        """Whether pytest's recursion from the directory ``top`` collects ``target`` (9.1.0 ``Dir.collect``,
        ``pytest_ignore_collect``, ``pytest_collect_file``): each directory on the way must survive
        ``__pycache__``, ``norecursedirs``, virtualenv detection and ``--ignore``/``--ignore-glob``; a file must
        be a ``.py`` module that is not ignored and matches ``python_files``. Conftest code that could change
        that leaves it unknown (``_conftest_customization``)."""
        ignored = {Path(value) for value in self._pytest_filter_set(invocation, "--ignore", base)}
        globs = sorted(self._pytest_filter_set(invocation, "--ignore-glob", base))
        steps = target.relative_to(top).parts
        depth = len(steps) if target.is_dir() else len(steps) - 1
        for count in range(1, depth + 1):
            directory = top.joinpath(*steps[:count])
            if directory.name == "__pycache__":
                return False, f"pytest never recurses into `{self._shown(task, directory)}`"
            pattern = next((item for item in scope.norecursedirs if _fnmatch_ex(item, directory)), "")
            if pattern:
                return False, (
                    f"pytest does not recurse from `{self._shown(task, top)}` into `{self._shown(task, directory)}`: "
                    f"it matches the `norecursedirs` pattern `{pattern}`, so the retained selection is collected "
                    "only when named explicitly"
                )
            if (directory / "pyvenv.cfg").is_file() or (directory / "conda-meta" / "history").is_file():
                return False, f"pytest does not recurse into the virtualenv `{self._shown(task, directory)}`"
            if directory in ignored or any(fnmatch.fnmatch(str(directory), item) for item in globs):
                return False, f"the check ignores `{self._shown(task, directory)}` (`--ignore`/`--ignore-glob`)"
        if target.is_file():
            if target in ignored or any(fnmatch.fnmatch(str(target), item) for item in globs):
                return False, f"the check ignores `{self._shown(task, target)}` (`--ignore`/`--ignore-glob`)"
            if target.suffix != ".py" or not any(_fnmatch_ex(item, target) for item in scope.python_files):
                return False, (
                    f"`{self._shown(task, target)}` does not match `python_files` ({', '.join(scope.python_files)}): "
                    f"pytest collects it only when it is named explicitly, never through "
                    f"`{self._shown(task, top)}`"
                )
        customization = self._conftest_customization(task, scope, top, target)
        if customization:
            return None, customization
        return True, ""

    def _conftest_customization(self, task: CodeTask, scope: _CollectionScope, top: Path, target: Path) -> str:
        """Why conftest code could change whether recursion from ``top`` collects ``target``, or ``""``.

        The conftests governing ``target``'s own path (its directory up to the workspace root) may ignore
        or re-collect it; conftests the recursion loads elsewhere under ``top`` may run session-wide hooks
        over every collected item. A fixture-only conftest changes nothing. The walk follows pytest's own
        pruning and is bounded by ``_COLLECTION_WALK_BUDGET``."""
        root = Path(task.workspace_root).resolve()
        anchor = target if target.is_dir() else target.parent
        governing = [directory for directory in (anchor, *anchor.parents) if directory.is_relative_to(root)]
        for directory in governing:
            names = _conftest_names(directory / "conftest.py")
            found = ["unparseable"] if names is None else sorted(names & _CONFTEST_PATH_NAMES)
            if found:
                return (
                    f"the conftest `{self._shown(task, directory / 'conftest.py')}` customizes collection "
                    f"({', '.join(found)}); pytest may not collect the retained selection through "
                    f"`{self._shown(task, top)}`, and this contract cannot evaluate conftest code -- run the retained "
                    "check, or name its selection explicitly"
                )
        governing_set = set(governing)
        pending = [top]
        visited = 0
        while pending:
            directory = pending.pop()
            visited += 1
            if visited > _COLLECTION_WALK_BUDGET:
                return (
                    f"the check recurses through more than {_COLLECTION_WALK_BUDGET} directories under "
                    f"`{self._shown(task, top)}`; this contract does not classify a collection that large -- run "
                    "the retained check, or name its selection explicitly"
                )
            if directory not in governing_set:
                names = _conftest_names(directory / "conftest.py")
                session = ["unparseable"] if names is None else sorted(names & _CONFTEST_SESSION_NAMES)
                if session:
                    return (
                        f"the conftest `{self._shown(task, directory / 'conftest.py')}` defines session-wide "
                        f"collection hooks ({', '.join(session)}) that act on every collected test, and this "
                        "contract cannot evaluate conftest code -- run the retained check, or name its selection "
                        "explicitly"
                    )
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                child = Path(entry.path)
                if child.name == "__pycache__" or any(_fnmatch_ex(item, child) for item in scope.norecursedirs) \
                        or (child / "pyvenv.cfg").is_file() or (child / "conda-meta" / "history").is_file():
                    continue
                pending.append(child)
        return ""

    def _equivalent_prior_verification(self, task: CodeTask, step: StepRecord) -> dict[str, Any] | None:
        """The CURRENT verification outcome this step would repeat exactly, or None.

        Equivalence is the whole recorded decision context, never a command string alone: the
        stage, the workspace revision, the repaired files' on-disk bytes, the command's
        effective invocation identity (runner, executed entry, interpreter modes, option/value
        pairs, operands, program arguments) and the working directory. Anything that changed --
        new bytes, a different selection, a swapped ``-k``/``-m`` value, a moved cwd -- is not
        a repeat and runs. The comparison is also honest about its own limits: files the
        journal never bound (fixtures outside the repaired set) are outside it, which is why a
        declared ``rerun_reason`` is the lawful channel for a purposeful rerun."""
        outcome = task.narrow if task.stage == "narrow_test" else task.cumulative
        if not isinstance(outcome, dict) or not outcome or outcome.get("revision") != task.revision:
            return None
        repaired = self._repair_fingerprint(task)
        recorded = dict(outcome.get("bytes") or {})
        if repaired != {path: token for path, token in recorded.items() if path in repaired}:
            return None
        # A changed check input (fixture) is changed evidence too: the outcome's recorded input
        # bytes no longer match disk, so the rerun is not the repeat of an equivalent context.
        inputs, _meta = self._input_fingerprint(task, step.arguments)
        if inputs != {path: token for path, token in recorded.items() if path not in repaired}:
            return None
        prior_intent = str(outcome.get("intent") or "")
        prior = (
            prior_intent,
            self._invocation_of(prior_intent or step.intent, {"command": str(outcome.get("command") or "")}).identity(),
            str(outcome.get("cwd") or ""),
        )
        if prior != (step.intent, self._invocation_of(step.intent, step.arguments).identity(),
                     _cwd_identity(step.arguments.get("cwd"))):
            return None
        return dict(outcome)

    @staticmethod
    def _verification_currency(task: CodeTask, step: StepRecord, observed: dict[str, str]) -> tuple[bool, str]:
        """Whether a finished check verified the task's CURRENT repair bytes, and if not, why."""
        if task.stage != step.stage_at:
            return False, f"the task moved from `{step.stage_at}` to `{task.stage}` while this check ran"
        if task.revision != step.revision_at:
            return False, (
                f"a repair landed while this check ran (revision {step.revision_at} -> {task.revision}); "
                "it verified superseded bytes"
            )
        if step.bytes_at != observed:
            return False, "the repaired files changed while this check ran"
        for path, token in observed.items():
            snapshot = task.snapshots.get(path)
            if snapshot is not None and state_token(snapshot) != token:
                return False, (
                    f"`{path}` on disk differs from the bytes this task last wrote or read; re-read it through "
                    "`code.task.step` with intent `workspace.read_file` before verifying"
                )
        return True, ""

    def _verified_bytes_state(self, task: CodeTask) -> dict[str, Any]:
        """Whether the bytes the current cumulative verification checked are still on disk: the
        repaired files AND the check inputs its command named. Either side changing means the
        green evidence describes a workspace that no longer exists."""
        cumulative = _current_outcome(task, "cumulative")
        if not cumulative or "bytes" not in cumulative:
            return {"current": None, "changed": []}
        changed_paths: set[str] = set()
        # Narrow and cumulative checks can name different inputs and working
        # directories. Completion depends on both still describing current bytes.
        for outcome in (_current_outcome(task, "narrow"), cumulative):
            if not outcome:
                continue
            verified = dict(outcome.get("bytes") or {})
            now = self._evidence_fingerprint(
                task, {"command": str(outcome.get("command") or ""),
                       "cwd": outcome.get("cwd"), "intent": str(outcome.get("intent") or "workspace.run_tests")})
            changed_paths.update(path for path in set(verified) | set(now) if verified.get(path) != now.get(path))
        changed = sorted(changed_paths)
        return {"current": not changed, "changed": changed}

    def _advance(self, task: CodeTask, step: StepRecord, kind: str) -> None:
        payload = step.result
        if kind == "command" and str(payload.get("status") or "") == "cancelled" and task.stage != STAGE_CANCELLED:
            # Terminal truth for a command the cancellation signal killed MID-RUN: the task is
            # cancelled -- not "between commands", but while its command was live -- and the
            # journal says so with the interrupted command attached. No later effect may run.
            task.cancelled_reason = (
                str(task.cancelled_reason or "").strip()
                or f"cancelled while `{step.intent}` was running: {payload.get('command') or ''}".strip()
            )
            task.stage = STAGE_CANCELLED
            step.fault_id = self._file_fault(
                task,
                "cancelled",
                dedupe=f"{task.task_id}:{step.step_id}",
                context={"intent": step.intent, "status": "cancelled_mid_command"},
            )
            return
        if kind == "read" and step.executed and step.ok:
            for path in step.paths:
                if path and path not in task.read_paths:
                    task.read_paths.append(path)
            if step.intent == "workspace.read_file":
                self._record_read_snapshot(task, step)
        elif kind == "read" and step.intent == "workspace.read_file" and step.status == "not_found":
            self._record_read_snapshot(task, step, absent=True)
        # Evidence-bearing commands: the validation runner AND the sandbox command (a shell
        # repository's failing test IS a sandbox command -- the losing workflow's shell_command
        # family always counted as reproduction evidence). Only a COMPLETED run is evidence: an
        # executed command that timed out or was cancelled mid-run left no verdict about the code.
        evidence_command = kind == "command" and step.executed and step.ok and (
            step.intent in TEST_INTENTS or step.intent == "sandbox.run_command"
        )
        if evidence_command:
            outcome = {
                "step_id": step.step_id,
                "command": str(payload.get("command") or step.arguments.get("command") or ""),
                "success": bool(payload.get("success", False)),
                "returncode": payload.get("returncode"),
                "intent": step.intent,
                # The working directory the check ran in, as the journal compares it: the declared
                # one when there was one, else the runner's reported directory, normalized so the
                # workspace root itself is `""` on both sides of the comparison.
                "cwd": _cwd_identity(step.arguments.get("cwd") or payload.get("cwd")),
            }
            if step.rerun_reason:
                outcome["rerun_reason"] = step.rerun_reason
            if step.stage_at == "reproduce" and task.stage == "reproduce" and outcome["success"] is False:
                task.reproduced_failure = outcome
                task.stage = "identify"
            elif step.stage_at in {"narrow_test", "cumulative"}:
                # A verification belongs to the bytes it checked: the revision it was admitted at and
                # the repaired files' identity. It is always recorded; only a CURRENT one decides.
                obligation = self._obligation_state(task, step.intent, step.arguments)
                inputs, input_meta = self._input_fingerprint(task, step.arguments)
                verified = {**outcome, "revision": step.revision_at,
                            "bytes": {**self._repair_fingerprint(task), **inputs},
                            "unit": step.unit, "obligation_match": obligation["genuine"],
                            "covers_obligation": obligation["covers_obligation"],
                            "coverage_kind": obligation["coverage_kind"], "input_coverage": input_meta,
                            "invocation": self._invocation_record(task, step.intent, step.arguments)}
                current, stale_reason = self._verification_currency(task, step, verified["bytes"])
                if not obligation["genuine"]:
                    # A diagnostic, not a check of the obligation: it runs, it is journaled, and it
                    # retires nothing -- the unexercised obligation stays exactly where it was.
                    stale_reason = obligation["reason"]
                elif current and outcome["success"] and obligation["covers_obligation"] is not True:
                    # Green but not provably over the obligation -- a provable miss or an unknown
                    # scope, at either evidence stage: recorded as evidence with the reason naming the
                    # check still owed. The stage still advances at narrow; the full-check checkpoint
                    # holds on it (see below).
                    stale_reason = obligation["reason"] or "the retained acceptance obligation is not covered"
                task.verifications.append({**verified, "stage": step.stage_at, "current": current,
                                           "stale_reason": stale_reason, "at": _utcnow()})
                if current and obligation["genuine"] and step.stage_at == "narrow_test":
                    task.narrow = verified
                    if outcome["success"]:
                        task.stage = "cumulative"
                elif current and obligation["genuine"]:
                    task.cumulative = verified
                    if outcome["success"] and obligation["covers_obligation"] is True:
                        # The checkpoint passed: the next approved unit may run, or the diff is next.
                        # Only PROVEN coverage passes it. Unknown coverage used to advance here while
                        # completion refused it; past `cumulative` no command is lawful, so a genuinely
                        # repaired task was stranded at an unresolved report with no way to run the
                        # retained check (measured, revision 4). The checkpoint now holds, and
                        # `_coverage_owed` names the retained check as the lawful next action.
                        task.stage = "mutate" if _pending_proposals(task) else "inspect_diff"
        elif (
            kind == "read"
            and step.intent in DIFF_INTENTS
            and step.executed
            and step.ok
            and task.stage == "inspect_diff"
            and step.stage_at == "inspect_diff"
            and step.revision_at == task.revision
        ):
            task.git_diff_paths = self._diff_paths(payload)
            task.stage = "report"

    def _land_mutation(self, task: CodeTask, step: StepRecord, proposal: Proposal | None) -> None:
        """Record what the physical writer did to the task's approvals, revision and file view."""
        if proposal is None:
            return
        now = _utcnow()
        if proposal.reserved_by == step.step_id:
            proposal.reserved_by = proposal.reserved_instance = proposal.reserved_at = ""
        if step.ok:
            proposal.consumed_by = step.step_id
        elif step.status == "stale_base":
            proposal.invalidated = {"reason": "writer_refused_stale_base", "path": proposal.targets[0] if proposal.targets else "",
                                    "observed": str(step.result.get("current_hash") or ""), "step_id": step.step_id, "at": now}
        elif step.status == "bytes_diverged":
            proposal.invalidated = {"reason": "bytes_diverged", "step_id": step.step_id, "at": now}
        if not step.executed:
            _settle_stage(task)
            return
        task.revision += 1
        # The evidence of the previous revision verified bytes that no longer exist. It stays in
        # `verifications`; nothing CURRENT -- narrow, cumulative, the inspected diff -- survives.
        task.narrow = None
        task.cumulative = None
        task.git_diff_paths = []
        for target in proposal.targets:
            task.snapshots[target] = {**_disk_state(self._target_path(task, target)), "source": "mutation",
                                      "step_id": step.step_id, "revision": task.revision, "at": now}
        for other in _ordered_proposals(task):
            if other is proposal or other.consumed_by or other.invalidated:
                continue
            superseded = [
                target for target in other.targets
                if target in proposal.targets and state_token(other.base.get(target)) != state_token(task.snapshots.get(target))
            ]
            if superseded:
                # Its reviewed base is gone: that approval can never describe the file again.
                other.invalidated = {"reason": "reviewed_base_superseded", "path": superseded[0],
                                     "observed": state_token(task.snapshots.get(superseded[0])),
                                     "step_id": step.step_id, "at": now}
        _settle_stage(task, landed_unit=_unit_of(proposal))

    @staticmethod
    def _diff_paths(payload: dict[str, Any]) -> list[str]:
        for key in ("paths", "changed_paths", "files"):
            values = payload.get(key)
            if isinstance(values, list) and values:
                return sorted(
                    {str(v.get("path") if isinstance(v, dict) else v).strip() for v in values if str(v).strip()}
                )
        text = str(payload.get("stdout") or payload.get("diff") or "")
        found = {m.group(1) for m in re.finditer(r"^\+\+\+ b/(.+)$", text, flags=re.MULTILINE)}
        found |= {m.group(1) for m in re.finditer(r"^diff --git a/(\S+) b/", text, flags=re.MULTILINE)}
        return sorted(found)

    def _verify_bytes(self, task: CodeTask, step: StepRecord, proposal: Proposal) -> bool | None:
        if step.intent != "workspace.write_file" or not step.paths:
            return None
        try:
            recorded = [
                row for row in list((proposal.preview or {}).get("destinations") or [])
                if isinstance(row, dict) and row.get("path") == step.paths[0]
            ]
            if recorded:
                # Verify the reviewed destination itself, not whatever the path resolves to by now.
                target = Path(str(recorded[0].get("target") or ""))
            else:
                from core.execution.workspace_tools import resolve_workspace_path

                target = resolve_workspace_path(step.paths[0], workspace_root=Path(task.workspace_root))
            return hashlib.sha256(target.read_bytes()).hexdigest() == proposal.preview.get("content_sha256")
        except (OSError, ValueError):
            return False

    def _path_lock(self, workspace_root: str, target: str) -> threading.Lock:
        key = (workspace_root, target)
        with self._lock:
            lock = self._path_locks.get(key)
            if lock is None:
                lock = self._path_locks[key] = threading.Lock()
            return lock

    def _task_mutation_lock(self, task_id: str) -> threading.Lock:
        with self._lock:
            lock = self._task_mutation_locks.get(task_id)
            if lock is None:
                lock = self._task_mutation_locks[task_id] = threading.Lock()
            return lock

    def step(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        step_id = str(arguments.get("step_id") or "").strip() or f"s-{uuid.uuid4().hex[:8]}"
        intent = str(arguments.get("intent") or "").strip()
        #: Declared purpose for re-running a check whose equivalent outcome is journaled (see
        #: ``StepRecord.rerun_reason``). Not part of the inner tool's arguments: it describes the
        #: CALLER's reason for the step, not the command.
        rerun_reason = str(arguments.get("rerun_reason") or "").strip()
        from core.code_assistant.step_validation import step_argument_error

        argument_error = step_argument_error(arguments)
        if argument_error:
            return self._result(task, ok=False, status="invalid_arguments", text=argument_error, step_id=step_id)
        inner = dict(arguments.get("arguments") or {})
        if any(str(k).startswith("_") for k in inner):
            return self._result(
                task,
                ok=False,
                status="invalid_arguments",
                text="internal trust flags cannot be supplied by a proposal",
                step_id=step_id,
            )
        kind, _contract = self._classify(intent)
        paths = [str(inner.get(k) or "").strip() for k in _PATH_KEYS if str(inner.get(k) or "").strip()]

        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            existing = task.steps.get(step_id)
            if existing is not None:
                if existing.intent != intent or _canonical(existing.arguments) != _canonical(inner):
                    return self._result(
                        task,
                        ok=False,
                        status="step_id_conflict",
                        text=f"step `{step_id}` belongs to a different action; use a new step_id for this action",
                        step_id=step_id,
                    )
                if existing.status == "in_flight":
                    return self._result(
                        task,
                        ok=False,
                        status="in_flight",
                        text=f"step `{step_id}` is still executing; wait for its result instead of re-issuing it",
                        step_id=step_id,
                    )
                return self._step_result(task, existing, replayed=True)
            step = StepRecord(
                step_id=step_id,
                intent=intent,
                arguments=inner,
                stage_at=task.stage,
                started_at=_utcnow(),
                paths=paths,
                status="in_flight",
                revision_at=task.revision,
                rerun_reason=rerun_reason,
            )
            task.steps[step_id] = step
            task.step_order.append(step_id)
            refusal, reason, proposal = self._admit(task, step, kind)
            if refusal:
                step.status, step.reason, step.completed_at = refusal, reason, _utcnow()
                code = "cancelled" if refusal == "cancelled" else "permission_denied"
                step.fault_id = self._file_fault(
                    task,
                    code,
                    dedupe=f"{task.task_id}:{step_id}",
                    context={"intent": intent, "stage": task.stage, "status": refusal},
                )
                self._persist(task)
                return self._step_result(task, step)
            if kind == "command" and task.stage in {"narrow_test", "cumulative"} and not rerun_reason:
                prior = self._equivalent_prior_verification(task, step)
                if prior is not None:
                    verdict = "passed" if prior.get("success") else "failed"
                    failed = bool(prior.get("success")) is False
                    owed = None if failed else _coverage_owed(task)
                    if failed:
                        consequence = "The failing check reopens diagnosis, not another identical run. "
                    elif owed:
                        where = f" with cwd `{owed['retained_cwd']}`" if owed["retained_cwd"] else ""
                        consequence = (
                            f"It passed, but it does not cover the retained acceptance obligation ({owed['reason']}); "
                            "repeating it cannot change that. Run the retained check "
                            f"`{owed['retained_command']}`{where} or a broader check whose selection provably "
                            "includes it. "
                        )
                    else:
                        consequence = "A passed check has already discharged this stage's obligation for these bytes. "
                    text = (
                        f"An equivalent verification of this task is already journaled: `{prior.get('command')}` at "
                        f"stage `{task.stage}`, revision {task.revision}, the same repaired bytes, selection and "
                        f"working directory -- it {verdict} (exit {prior.get('returncode')}, step "
                        f"`{prior.get('step_id')}`). As far as the journal binds evidence, running it again answers "
                        "nothing it does not already say. "
                        + consequence
                        + "Re-run it purposefully by declaring `rerun_reason`: the investigative plan, the operator "
                        "request, or what changed outside the journal's byte binding (fixtures, environment)."
                    )
                    step.status, step.reason, step.completed_at = "repeated_verification", text, _utcnow()
                    self._persist(task)
                    return self._result(
                        task,
                        ok=False,
                        status="repeated_verification",
                        text=text,
                        executed=False,
                        step_id=step_id,
                        intent=intent,
                        prior_outcome={
                            key: prior.get(key)
                            for key in ("step_id", "command", "success", "returncode", "revision", "unit", "at")
                        } | {"stage": task.stage},
                        next=stage_next_actions(
                            task.stage,
                            verification_failed=failed_verification_reopens_review(task),
                            pending_repairs=[p.proposal_id for p in _pending_proposals(task)],
                            unit_in_progress=_unit_in_progress(task),
                            coverage_owed=owed,
                        ),
                    )
            if kind == "command" and task.stage in {"narrow_test", "cumulative"}:
                step.bytes_at = self._evidence_fingerprint(task, step.arguments)
                step.unit = str(task.checkpoint.get("unit") or "")
            proposal_id = ""
            if proposal is not None:
                # The durable claim: from here on, no other step or runtime instance can execute
                # this approval, and a restart knows who holds it.
                proposal.reserved_by, proposal.reserved_instance, proposal.reserved_at = step_id, self._instance, _utcnow()
                step.proposal_id = proposal_id = proposal.proposal_id
                step.unit = _unit_of(proposal)
            self._persist(task)

        if kind == "mutation":
            return self._run_mutation(task, step_id, intent, proposal_id, source_context)
        return self._run_step(task, step_id, intent, inner, kind, source_context)

    def _dispatch(self, intent: str, arguments: dict[str, Any], context: dict[str, Any]) -> tuple[Any, str, list[dict[str, Any]]]:
        from core.effect_gateway import current_effect_ledger
        from core.runtime_execution_tools import execute_runtime_tool

        ledger = current_effect_ledger()
        before = len(ledger.entries()) if ledger is not None else 0
        try:
            outcome = execute_runtime_tool(intent, dict(arguments), source_context=context)
        except Exception as exc:  # the door's own failure is still a journaled step
            outcome = None
            failure = f"{type(exc).__name__}: {exc}"
        else:
            failure = ""
        receipts = [dict(e) for e in list(ledger.entries())[before:]] if ledger is not None else []
        return outcome, failure, receipts

    def _run_step(self, task: CodeTask, step_id: str, intent: str, inner: dict[str, Any], kind: str,
                  source_context: dict[str, Any] | None):
        outcome, failure, receipts = self._dispatch(intent, inner, self._task_context(task, source_context))
        try:
            with self._transaction(task.task_id) as fresh:
                if fresh is None or step_id not in fresh.steps:
                    return self._unrecorded_effect(task, step_id, intent, outcome, "the task journal is gone")
                step = fresh.steps[step_id]
                self._record_outcome(fresh, step, kind, None, outcome, failure, receipts)
                self._advance(fresh, step, kind)
                self._persist(fresh)
                return self._step_result(fresh, step)
        except (JournalConflictError, LockUnavailable) as exc:
            return self._unrecorded_effect(task, step_id, intent, outcome, exc)

    def _run_mutation(self, task: CodeTask, step_id: str, intent: str, proposal_id: str,
                      source_context: dict[str, Any] | None):
        with self._lock:
            claimed = task.proposals.get(proposal_id)
            targets = sorted(set(claimed.targets)) if claimed is not None else []
        # Serialization boundary: one mutation of this task at a time, and one writer per path
        # across every task in this process. Taken in a fixed order (task, then sorted paths).
        locks = [self._task_mutation_lock(task.task_id)] + [self._path_lock(task.workspace_root, t) for t in targets]
        for lock in locks:
            lock.acquire()
        try:
            with self._transaction(task.task_id) as fresh:
                if fresh is None or step_id not in fresh.steps:
                    return self._vanished(task.task_id)
                step = fresh.steps[step_id]
                proposal = fresh.proposals.get(proposal_id)
                refusal, reason = self._mutation_precondition(fresh, step, proposal)
                if refusal:
                    step.status, step.reason, step.completed_at = refusal, reason, _utcnow()
                    if proposal is not None and proposal.reserved_by == step_id:
                        proposal.reserved_by = proposal.reserved_instance = proposal.reserved_at = ""
                    code = ("cancelled" if refusal == "cancelled"
                            else "unsupported_claim" if refusal == "stale_base" else "permission_denied")
                    step.fault_id = self._file_fault(
                        fresh, code, dedupe=f"{fresh.task_id}:{step_id}",
                        context={"intent": intent, "stage": fresh.stage, "status": refusal},
                    )
                    _settle_stage(fresh)
                    self._persist(fresh)
                    return self._step_result(fresh, step)
                dispatched = self._dispatch_arguments(step, proposal)
            mutation_context = self._task_context(task, source_context)
            recorded = list((proposal.preview or {}).get("destinations") or []) if proposal is not None else []
            if recorded:
                # The writer holds the approved destinations too: it refuses a target that is not the
                # reviewed one and writes inside the pinned reviewed directory (the protected-write
                # carry from the integrated runtime; recomputed here on the lane's dispatch shape).
                from core.runtime_execution_tools import APPROVED_DESTINATIONS_CONTEXT_KEY

                mutation_context[APPROVED_DESTINATIONS_CONTEXT_KEY] = [dict(row) for row in recorded]
            outcome, failure, receipts = self._dispatch(intent, dispatched, mutation_context)
            try:
                with self._transaction(task.task_id) as fresh:
                    if fresh is None or step_id not in fresh.steps:
                        return self._unrecorded_effect(task, step_id, intent, outcome, "the task journal is gone")
                    step = fresh.steps[step_id]
                    proposal = fresh.proposals.get(proposal_id)
                    self._record_outcome(fresh, step, "mutation", proposal, outcome, failure, receipts)
                    self._land_mutation(fresh, step, proposal)
                    self._persist(fresh)
                    return self._step_result(fresh, step)
            except (JournalConflictError, LockUnavailable) as exc:
                return self._unrecorded_effect(task, step_id, intent, outcome, exc)
        finally:
            for lock in reversed(locks):
                lock.release()

    def _record_outcome(
        self,
        task: CodeTask,
        step: StepRecord,
        kind: str,
        proposal: Proposal | None,
        outcome: Any,
        failure: str,
        receipts: list[dict[str, Any]],
    ) -> None:
        step.completed_at = _utcnow()
        step.receipts = receipts
        if outcome is None:
            step.executed, step.ok, step.status = False, False, "tool_error" if failure else "unhandled"
            step.reason = failure or f"`{step.intent}` was not handled by the runtime door"
            step.fault_id = self._file_fault(
                task,
                "tool_unavailable",
                dedupe=f"{task.task_id}:{step.step_id}",
                context={"intent": step.intent, "status": step.status},
            )
            return
        details = dict(getattr(outcome, "details", {}) or {})
        # The inner tool's own bounded observation is kept with its result: it is the tool's evidence summary
        # for a model (for a check, the failing message, the location it names and the symbol to look up).
        # Nested under the step's `tool_result`, it can never stand in for the task's own observation.
        step.result = json.loads(json.dumps(details, default=str))
        # A refusal decided below the door (confinement, permission, stale base) is not an
        # execution: the tool ran no effect. Only a result the underlying tool marks as having
        # done its work counts as executed. A COMMAND counts as executed only when it reached a
        # terminal outcome of its own (`executed`, `command_failed`); a command that timed out or
        # was cancelled mid-run DID start -- it is executed, with real receipts -- but it did not
        # complete, so it is never `ok` and never verification evidence. The presence of a
        # `returncode` key alone is NOT the signal: measured live, a refused out-of-workspace
        # `cwd` was rendered with `returncode: 0` and journaled as a failed verification of a
        # check that never ran. Permission refusal, unavailable tooling, timeout and an executed
        # failing test are four different facts.
        outcome_status = str(outcome.status or "")
        ran_completed = kind == "command" and outcome_status in {"executed", "command_failed"} \
            and "returncode" in details
        ran_incomplete = kind == "command" and outcome_status in {"timed_out", "cancelled"} \
            and "returncode" in details
        step.executed = bool(outcome.ok) or ran_completed or ran_incomplete
        step.ok = bool(outcome.ok) or ran_completed
        step.status = str(outcome.status or ("ok" if outcome.ok else "error"))
        step.reason = "" if step.ok else str(outcome.response_text or "")
        # A command the cancellation signal killed mid-run DID run -- it is executed, and
        # its receipts are real -- but it did not complete, so it is never `ok`: the
        # terminal truth is `cancelled`, not a false green.
        if step.status == "cancelled":
            step.ok = False
        if kind == "mutation" and step.ok:
            _bb = step.result.get("blackbox") if isinstance(step.result.get("blackbox"), dict) else {}
            _bb_turn = str(_bb.get("turn_id") or "")
            if _bb_turn and _bb_turn not in task.blackbox_turn_ids:
                task.blackbox_turn_ids.append(_bb_turn)
        if kind == "mutation" and proposal is not None and step.ok:
            step.bytes_match_approved_patch = self._verify_bytes(task, step, proposal)
            if step.bytes_match_approved_patch is False:
                step.ok, step.status, step.reason = (
                    False,
                    "bytes_diverged",
                    "the bytes on disk do not match the approved patch",
                )
                step.fault_id = self._file_fault(
                    task,
                    "integrity_verification_failure",
                    dedupe=f"{task.task_id}:{step.step_id}",
                    context={"intent": step.intent},
                )
        if not step.ok and not step.fault_id and step.status not in {"ok", "cancelled"}:
            step.fault_id = self._file_fault(
                task,
                "confinement_refusal"
                if step.status in {"scope_violation", "blocked", "denied", "confinement_refusal"}
                else "unsupported_claim"
                if step.status in {"stale_base", "bytes_diverged"}
                else "permission_denied"
                if step.status == "permission_denied"
                else "unknown",
                dedupe=f"{task.task_id}:{step.step_id}",
                context={"intent": step.intent, "status": step.status},
            )

    # -- rollback / report ------------------------------------------------------

    def rollback(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        from core.blackbox.operator import rollback_turn
        from core.mode_permission_policy import PermissionAction, grant_internal_authority, revoke_internal_authority

        turn_ids = list(reversed(task.blackbox_turn_ids)) or [task.turn_id]
        restored: list[str] = []
        outcome = None
        # The inner Blackbox effect is a delete-class act the mode matrix prompts for. Once the
        # OUTER code.task.rollback call has been admitted (mode matrix, and an operator approval
        # wherever Auto prompts), the task mints its own BOUNDED internal authority for the inner
        # effect -- the same scope the operator CLI mints, bound to this task, this session, this
        # workspace, this one intent, expiring in 120s and revoked on return. Without it the two
        # gates alternate prompts forever: one operator approval can never satisfy both layers.
        inner_scope_token = grant_internal_authority(
            label=f"code-task-rollback:{task.task_id}",
            actions=(
                PermissionAction.DELETE_FILES,
                PermissionAction.OVERWRITE_EXISTING_FILES,
                PermissionAction.MODIFY_FILES,
                PermissionAction.CREATE_FILES,
            ),
            intents=("workspace.rollback_last_change",),
            session_id=task.session_id,
            task_id=task.task_id,
            workspace_root=task.workspace_root,
            duration_seconds=120,
        )
        try:
            for bb_turn in turn_ids:  # most recent mutation first; stop at the first refusal
                outcome = rollback_turn(
                    bb_turn,
                    workspace_root=task.workspace_root,
                    session_id=task.session_id,
                    operator=AUTHORITY,
                    source_context={
                        **self._task_context(task, source_context),
                        "internal_authority_token": inner_scope_token,
                    },
                    task_id=task.task_id,
                )
                details = dict(getattr(outcome, "details", {}) or {})
                restored.extend(str(p).strip() for p in list(details.get("restored_paths") or []) if str(p).strip())
                if not outcome.ok:
                    break
        finally:
            revoke_internal_authority(inner_scope_token)
        restored = sorted(set(restored))
        with self._transaction(task.task_id) as fresh:
            if fresh is None:
                return self._vanished(task.task_id)
            fresh.rollback = {
                "restored": bool(outcome.ok),
                "restored_paths": restored,
                "status": str(outcome.status),
                "at": _utcnow(),
                "turn_id": fresh.turn_id,
                "blackbox_turn_ids": list(turn_ids),
            }
            if outcome.ok:
                fresh.stage = STAGE_ROLLED_BACK
            if outcome.ok or restored:
                _retire_pr_description(fresh, "the task's mutations were rolled back")
            self._persist(fresh)
            return self._result(
                fresh,
                ok=bool(outcome.ok),
                status=str(outcome.status),
                text=str(outcome.response_text or ""),
                executed=bool(outcome.ok),
                restored_paths=sorted(restored),
                rollback=fresh.rollback,
            )

    def pr_description(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`code.task.pr_description` -- a reviewable PR title and body assembled ONLY from
        the task journal: the identified defect, the inspected diff paths, and the executed
        commands with their outcomes. Nothing here is model prose and nothing is invented;
        a task whose evidence is incomplete refuses rather than laundering unverified work
        into a reviewable-looking description. Preparing one opens no pull request.
        """
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            if task.stage == STAGE_CANCELLED:
                return self._result(
                    task, ok=False, status="cancelled", text="The task is cancelled; nothing further executes."
                )
            if task.rollback.get("restored"):
                return self._result(
                    task,
                    ok=False,
                    status="rolled_back",
                    text="The task's mutations were rolled back; there is no repair to describe.",
                )
            missing: list[str] = []
            if task.stage not in {"inspect_diff", "report"}:
                missing.append(f"the task is at stage `{task.stage}`; the repaired diff has not been inspected")
            if not (_current_outcome(task, "narrow") or {}).get("success"):
                missing.append("no successful narrow test run is journaled")
            if not (_current_outcome(task, "cumulative") or {}).get("success"):
                missing.append("no successful cumulative test run is journaled")
            if not task.git_diff_paths:
                missing.append("no diff paths are journaled")
            bytes_state = self._verified_bytes_state(task)
            if bytes_state["current"] is False:
                missing.append(
                    "the verified bytes changed since the cumulative check ("
                    + ", ".join(f"`{path}`" for path in bytes_state["changed"]) + ")"
                )
            if missing:
                return self._result(
                    task,
                    ok=False,
                    status="insufficient_evidence",
                    text=(
                        "A PR description is prepared only from complete task evidence, and this "
                        "task does not have it: " + "; ".join(missing) + "."
                    ),
                    missing=missing,
                )

            steps = [task.steps[s] for s in task.step_order if s in task.steps]
            refused = [s for s in steps if not s.executed and s.status not in {"in_flight", "pending"}]
            failed = [s for s in steps if s.executed and not s.ok]

            defect = dict(task.defect or {})
            defect_path = str(defect.get("path") or "").strip()
            defect_reason = str(defect.get("reason") or "").strip()
            if defect_path and defect_reason:
                title = f"Repair {defect_path}: {_first_line(defect_reason, 64)}"
            else:
                title = _first_line(task.objective, 72)

            lines: list[str] = []
            lines.append("## What changed")
            for path in sorted(set(task.git_diff_paths)):
                lines.append(f"- `{path}`")
            lines.append("")
            lines.append("## Why")
            if defect_path:
                location = f"{defect_path}:{defect.get('line') or '?'}"
                lines.append(f"Owning defect identified at `{location}`: {defect_reason or 'reason not recorded'}")
            else:
                lines.append(f"Task objective: {task.objective}")
            lines.append("")
            lines.append("## Evidence")
            for label, outcome in (
                ("Reproduced the failure", task.reproduced_failure),
                ("Narrow test", task.narrow),
                ("Cumulative test", task.cumulative),
            ):
                if not outcome:
                    lines.append(f"- {label}: not run")
                    continue
                verdict = "passed" if outcome.get("success") else "failed"
                code = outcome.get("returncode")
                suffix = f" (exit {code})" if code is not None else ""
                lines.append(f"- {label}: `{outcome.get('command') or '(no command)'}` → {verdict}{suffix}")
            lines.append("")
            lines.append("## State")
            lines.append(f"- Task {task.task_id} at stage `{task.stage}`; narrow and cumulative tests green.")
            if failed:
                lines.append(f"- The full journal records {len(failed)} failed executed step(s); see the task report for their order.")
            if refused:
                lines.append(f"- {len(refused)} step(s) were refused by the boundary; see the task report.")
            if not failed and not refused:
                lines.append("- No failed or refused steps are recorded in the task journal.")
            lines.append("")
            lines.append("---")
            lines.append(
                "No pull request was opened."
            )
            body = "\n".join(lines)
            publish = self._redact_publish({"title": title, "body": body})
            title, body = publish["title"], publish["body"]

            task.pr_description = {
                "title": title,
                "body": body,
                "prepared_at": _utcnow(),
                "task_stage": task.stage,
                # What it describes, so any reader can tell whether it is still current.
                "revision": task.revision,
                "bytes": dict((_current_outcome(task, "cumulative") or {}).get("bytes") or {}),
            }
            self._persist(task)

            result = self._result(
                task,
                ok=True,
                status="ok",
                text=f"{title}\n\n{body}",
                title=title,
                body=body,
                evidence={
                    "defect": defect,
                    "git_diff_paths": list(task.git_diff_paths),
                    "reproduction": task.reproduced_failure,
                    "narrow": task.narrow,
                    "cumulative": task.cumulative,
                    "failed_steps": len(failed),
                    "refused_steps": len(refused),
                    "revision": task.revision,
                    "verified_bytes": dict((task.cumulative or {}).get("bytes") or {}),
                },
            )
        result.details = self._redact_publish(dict(result.details))
        return result

    @staticmethod
    def _units_summary(task: CodeTask) -> list[dict[str, Any]]:
        """Every repair unit, its changes and where it stands -- read from the journal."""
        units: dict[str, list[Proposal]] = {}
        for proposal in _ordered_proposals(task):
            units.setdefault(_unit_of(proposal), []).append(proposal)
        rows = []
        for unit, members in units.items():
            landed = [m.proposal_id for m in members if m.consumed_by]
            open_members = [m.proposal_id for m in members if not m.consumed_by and not m.invalidated]
            state = ("landed" if landed and not open_members else "part_applied" if landed
                     else "invalidated" if not open_members else "pending")
            rows.append({
                "unit": unit,
                "members": [m.proposal_id for m in members],
                "targets": sorted({t for m in members for t in m.targets}),
                "declared_together": len(members) > 1,
                "state": state,
                "landed": landed,
                "open": open_members,
            })
        return rows

    def _obligation_report(self, task: CodeTask) -> dict[str, Any]:
        """The retained acceptance obligation and how the current evidence stands against it:
        which command expressed it, whether the current cumulative's effective invocation
        covered it, and what input binding its bytes evidence carries. Declared, not proven --
        the journal binds what commands named and the runner adapters document, never proven
        execution semantics."""
        obligation = self._obligation_invocation(task)
        cumulative = _current_outcome(task, "cumulative")
        covered = None
        reason = ""
        if cumulative is not None:
            state = self._obligation_state(
                task, str(cumulative.get("intent") or "workspace.run_tests"),
                {"command": cumulative.get("command"), "cwd": cumulative.get("cwd")})
            covered = state.get("covers_obligation")
            reason = "" if covered is True else str(state.get("reason") or "")
        record = task.reproduced_failure if isinstance(task.reproduced_failure, dict) else {}
        return {
            "reproduced_command": str(record.get("command") or ""),
            "reproduced_cwd": str(record.get("cwd") or ""),
            "runner_family": obligation.family if obligation is not None else "",
            "covered_by_current_cumulative": covered,
            "coverage_reason": reason,
            "coverage_kind": "declared",
            "input_coverage": dict(cumulative.get("input_coverage") or {}) if cumulative else {},
        }

    @staticmethod
    def _redact_publish(payload: dict[str, Any]) -> dict[str, Any]:
        """Scrub every string the REPORT publishes through THE redaction authority and record
        the count. High-precision by design (vendor shapes + the exact-value registry); an
        unknown-format key is caught only if credential intake registered its exact value.
        The report is this lane's publish surface -- commits belong to the KAS/RepoOps lane."""
        from core.secret_redaction import redact_secrets

        changed = 0

        def walk(value: Any) -> Any:
            nonlocal changed
            if isinstance(value, str):
                cleaned = redact_secrets(value)
                if cleaned != value:
                    changed += 1
                return cleaned
            if isinstance(value, dict):
                return {key: walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item) for item in value]
            return value

        cleaned = {key: walk(item) for key, item in payload.items()}
        cleaned["redactions"] = changed
        return cleaned

    def report(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        task, err = self._require(arguments.get("task_id"))
        if err:
            return err
        err = self._require_owned(task, source_context)
        if err:
            return err
        with self._transaction(task.task_id) as task:
            if task is None:
                return self._vanished(arguments.get("task_id"))
            steps = [task.steps[s] for s in task.step_order if s in task.steps]
            completed = [s.step_id for s in steps if s.executed and s.ok]
            refused = [
                {"step_id": s.step_id, "intent": s.intent, "status": s.status, "reason": s.reason}
                for s in steps
                if not s.executed and s.status not in {"in_flight", "pending"}
            ]
            failed = [
                {"step_id": s.step_id, "intent": s.intent, "status": s.status, "reason": s.reason}
                for s in steps
                if s.executed and not s.ok
            ]
            files_changed = sorted(
                {p for s in steps if s.executed and s.ok and self._classify(s.intent)[0] == "mutation" for p in s.paths}
            )
            commands = [
                {
                    "step_id": s.step_id,
                    "intent": s.intent,
                    "command": str(s.result.get("command") or s.arguments.get("command") or ""),
                    "returncode": s.result.get("returncode"),
                    "success": s.result.get("success"),
                }
                for s in steps
                if s.intent in COMMAND_INTENTS and s.executed
            ]
            receipts = [r for s in steps for r in s.receipts]
            try:
                from core.faults.recorder import faults_for_turn

                faults = [
                    {"fault_id": f.fault_id, "code": f.code, "category": f.category, "user_message": f.user_message}
                    for f in faults_for_turn(task.turn_key, session_id=task.session_id)
                ]
            except Exception:
                faults = []
            try:
                from core.blackbox.operator import list_turns

                turns = [
                    t for t in list_turns(workspace_root=task.workspace_root) if str(t.get("turn_id")) == task.turn_id
                ]
            except Exception:
                turns = []
            mutated = any(s.executed and s.ok and self._classify(s.intent)[0] == "mutation" for s in steps)
            verdict = _completion_verdict(task)
            unresolved = list(STAGES[STAGES.index(task.stage) :]) if task.stage in STAGES else []
            if task.stage == "report" and verdict != "completed":
                unresolved = [
                    name
                    for name in ("narrow_test", "cumulative")
                    if not (_current_outcome(task, "narrow" if name == "narrow_test" else "cumulative") or {}).get("success")
                ]
            bytes_state = self._verified_bytes_state(task)
            if verdict == "completed" and bytes_state["current"] is False:
                # The verified bytes are no longer on disk: the green evidence describes a workspace
                # that no longer exists, so the report cannot publish completion.
                verdict = "unresolved"
                unresolved = ["verified_bytes_changed"]
            self._persist(task)
            text = f"Code task {task.task_id}: {verdict}. {len(completed)} step(s) executed, {len(refused)} refused, {len(failed)} failed."
            result = self._result(
                task,
                ok=True,
                status="ok",
                text=text,
                verdict=verdict,
                demand=task.objective,
                defect=task.defect,
                files_changed=files_changed,
                git_diff_paths=list(task.git_diff_paths),
                commands_run=commands,
                tests={
                    "reproduced_failure": task.reproduced_failure is not None,
                    "reproduction": task.reproduced_failure,
                    "narrow": task.narrow,
                    "cumulative": task.cumulative,
                },
                receipts={"count": len(receipts), "entries": receipts},
                faults=faults,
                rollback={
                    "available": bool(turns) or mutated,
                    "turns": len(turns),
                    "restored": bool(task.rollback.get("restored")),
                    "restored_paths": list(task.rollback.get("restored_paths") or []),
                    "turn_id": task.turn_id,
                },
                completed_step_ids=completed,
                refused=refused,
                failed=failed,
                unresolved=unresolved,
                proposals=[asdict(p) for p in task.proposals.values()],
                code_task_verdict=verdict,
                verifications=list(task.verifications),
                diagnoses=list(task.diagnoses),
                units=self._units_summary(task),
                checkpoint=dict(task.checkpoint),
                bytes_current=bytes_state["current"],
                bytes_changed=bytes_state["changed"],
                obligation=self._obligation_report(task),
            )
        result.details = self._redact_publish(dict(result.details))
        return result


STAGE_CONTROL_INTENTS: dict[str, tuple[str, ...]] = {
    "reproduce": ("code.task.step", "code.task.report", "code.task.cancel"),
    "identify": ("code.task.identify", "code.task.step", "code.task.report", "code.task.cancel"),
    "propose": ("code.task.propose", "code.task.step", "code.task.report", "code.task.cancel"),
    "approve": ("code.task.approve", "code.task.propose", "code.task.report", "code.task.cancel"),
    "mutate": ("code.task.step", "code.task.report", "code.task.cancel", "code.task.rollback"),
    "narrow_test": ("code.task.step", "code.task.report", "code.task.cancel", "code.task.rollback"),
    "cumulative": ("code.task.step", "code.task.report", "code.task.cancel", "code.task.rollback"),
    "inspect_diff": ("code.task.step", "code.task.report", "code.task.cancel", "code.task.rollback"),
    "report": ("code.task.report", "code.task.pr_description", "code.task.rollback", "code.task.cancel"),
}
_ACTIVE_TASK_TTL_SECONDS = 6 * 60 * 60


_TASK_ID_SHAPE = re.compile(r"ct-[0-9a-f]{12}")
_REPLACEMENT_INTENTS = frozenset({"workspace.write_file", "machine.write_file"})


#: The coding contract's path-bearing mutations (`core.code_assistant.contract.FAMILY_INTENTS`), the
#: only file-changing tools a proposal may carry. A proposal records each destination when it is
#: previewed -- the canonical target the writer's own resolver produces under the task's workspace,
#: its kind, and the sha256 of the bytes there -- and approval, the permission gate, the step's
#: admission and the writer all compare against that record rather than re-resolving the path.
#: A patch's destinations are the files it names, parsed by the patch writer's own parser.
_DESTINATION_INTENTS = frozenset(
    {"workspace.write_file", "workspace.replace_in_file", "workspace.apply_unified_diff", "workspace.ensure_directory"}
)
_DESTINATION_RECORD_VERSION = 1
_DESTINATION_REFUSALS = {
    "destination_unrecorded": (
        "This proposal was recorded before its destinations were, so there is no reviewed file to hold "
        "it to: re-propose it and approve what it will change. Nothing was approved or written."
    ),
    "legacy_approval_without_reviewed_base": (
        "This legacy approval carries no reviewed base the journal evidence can bind, so nothing it "
        "names may execute: re-read the file through `code.task.step`, propose the repair against "
        "its current content, and approve that proposal. Nothing was approved or written."
    ),
    "destination_changed": (
        "`{path}` no longer resolves to what was reviewed. Nothing was approved or written; re-propose "
        "against the files as they are now."
    ),
    "stale_base": (
        "`{path}` no longer holds what was reviewed. Nothing was approved or written; re-read it and "
        "re-propose."
    ),
}


def _destination_paths(intent: str, arguments: dict[str, Any]) -> list[str]:
    if intent == "workspace.apply_unified_diff":
        from core.execution.workspace_tools import _extract_patch_paths

        return _extract_patch_paths(str(arguments.get("patch") or arguments.get("diff") or ""))
    return [str(arguments.get(key) or "").strip() for key in _PATH_KEYS if str(arguments.get(key) or "").strip()]


def _destination_record(intent: str, path: str, workspace_root: str) -> dict[str, Any] | None:
    """One reviewed destination, resolved NOW by the writer's authority under the task's workspace,
    or None when the writer would refuse the path."""
    from core.runtime_execution_tools import resolve_write_target

    target = resolve_write_target(intent, path, _pin_task_roots({}, workspace_root))
    if target is None:
        return None
    try:
        kind = "file" if target.is_file() else "directory" if target.is_dir() else "absent"
        prior = hashlib.sha256(target.read_bytes()).hexdigest() if kind == "file" else ""
    except OSError:
        return None
    return {
        "version": _DESTINATION_RECORD_VERSION,
        "intent": intent,
        "path": path,
        "target": str(target),
        "kind": kind,
        "prior_sha256": prior,
    }


def _destination_records(task: CodeTask, intent: str, arguments: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Every destination a mutation names, recorded at preview; or ([], path) for the first path its
    writer would refuse -- nothing unresolvable is offered for approval."""
    records: list[dict[str, Any]] = []
    for path in _destination_paths(intent, arguments):
        record = _destination_record(intent, path, task.workspace_root)
        if record is None:
            return [], path
        records.append(record)
    return records, ""


def _destination_drift(task: CodeTask, proposal: Proposal, *, reviewed_bytes: bool = True) -> tuple[str, str]:
    """Whether the destinations a proposal recorded still hold now: ("", "") when they do, else the
    refusal status and the path. A proposal recorded without destinations is `destination_unrecorded`
    -- a record is never rebuilt from today's filesystem, because that would approve a file nobody
    reviewed. ``reviewed_bytes=False`` compares only where each path resolves and leaves the reviewed
    kind and bytes to the writer, which checks them inside the flight recorder and again inside the
    pinned directory immediately before the rename."""
    if proposal.intent not in _DESTINATION_INTENTS:
        return "", ""
    recorded = [row for row in list((proposal.preview or {}).get("destinations") or []) if isinstance(row, dict)]
    if not recorded:
        # A MIGRATED approval predates preview destination records, and the journal migration
        # bound its targets and reviewed base from the journal's own evidence (source
        # ``legacy_*``) after checking the recorded path still resolves where it did. That
        # binding IS the review record for it; refusing `destination_unrecorded` here would
        # deadlock the checkpoint law -- the write is owed `checkpoint_required`, not a
        # destination refusal -- while the unbound legacy shapes stay withdrawn upstream.
        legacy_bound = bool(proposal.targets) and all(
            str((proposal.base.get(target) or {}).get("source") or "").startswith("legacy_")
            for target in proposal.targets
        )
        if not legacy_bound:
            # Fails closed the way a withdrawn legacy approval does (the migration invalidates
            # this shape at load; the filesystem can change after load): the refusal is a
            # demand for review, not a destination complaint.
            return "legacy_approval_without_reviewed_base", ""
        return "", ""
    for record in recorded:
        path = str(record.get("path") or "")
        live = _destination_record(proposal.intent, path, task.workspace_root)
        if live is None or live["target"] != str(record.get("target") or ""):
            return "destination_changed", path
        if reviewed_bytes and (
            live["kind"] != str(record.get("kind") or "")
            or live["prior_sha256"] != str(record.get("prior_sha256") or "")
        ):
            return "stale_base", path
    return "", ""


def _pin_task_roots(context: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    """Pin BOTH context roots to the task's workspace. Every inner call a task step makes runs
    there (`CodeTaskRuntime._task_context`), whatever roots the calling context carried, and the
    permission gate classifies a step under exactly this context (`task_execution_context`)."""
    context["workspace"] = workspace_root
    context["workspace_root"] = workspace_root
    return context


def task_execution_context(source_context: dict[str, Any] | None, task_id: str) -> dict[str, Any] | None:
    """The context the inner call of a `code.task.step` naming ``task_id`` ACTUALLY runs under, for
    the permission gate to classify: the caller's context with both roots pinned to that task's
    workspace. None when the id is malformed, the journal is unreadable, or the task belongs to
    another session -- the step refuses those before any inner call runs. Read from the journal,
    never a cache, so a restarted daemon answers the same."""
    context = source_context if isinstance(source_context, dict) else {}
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    clean_id = str(task_id or "").strip()
    if not session or not _TASK_ID_SHAPE.fullmatch(clean_id):
        return None
    try:
        payload = json.loads((task_dir() / f"{clean_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or str(payload.get("session_id") or "") != session:
        return None
    workspace_root = str(payload.get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    return _pin_task_roots(dict(context), workspace_root)


def _approval_owner_payload(session: str, task_id: str) -> dict[str, Any] | None:
    """The code-task journal that may own an approval for this caller: the task a
    `code.task.step` names, or else the session's most recently UPDATED live task (task ids are
    random, so id order says nothing about recency). Owned by the calling session, not
    cancelled or rolled back, within the live-task TTL; read from the journal, never a cache."""
    now = datetime.now(timezone.utc)

    def is_live_task(payload: dict[str, Any]) -> bool:
        if str(payload.get("session_id") or "") != session:
            return False
        if str(payload.get("stage") or "") in {STAGE_CANCELLED, STAGE_ROLLED_BACK}:
            return False
        if bool(dict(payload.get("rollback") or {}).get("restored")):
            return False
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            return False
        return (now - updated).total_seconds() <= _ACTIVE_TASK_TTL_SECONDS

    root = task_dir()
    if task_id:
        if not _TASK_ID_SHAPE.fullmatch(task_id):
            return None
        try:
            payload = json.loads((root / f"{task_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) and is_live_task(payload) else None
    best: dict[str, Any] | None = None
    for task_path in root.glob("ct-*.json"):
        try:
            payload = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict) or not is_live_task(payload):
            continue
        if best is None or str(payload.get("updated_at") or "") > str(best.get("updated_at") or ""):
            best = payload
    return best


def approved_replacement_destination(
    source_context: dict[str, Any] | None,
    *,
    intent: str,
    path: str,
    content: str,
    expected_hash: str,
    task_id: str = "",
    reviewed_bytes: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """The recorded destination through which an OPERATOR-APPROVED, unconsumed code proposal binds
    EXACTLY this full-file replacement, as ``(record, "")``; otherwise ``(None, reason)``. A binding is
    the replacement's complete action identity, not a relative path that happens to repeat:

    * the same TOOL: an approved `workspace.write_file` never authorizes `machine.write_file`
      (or the reverse) -- they write different files;
    * the same DESTINATION: the call's target, resolved by the writer's own authority
      (`resolve_write_target`), IS the canonical target the proposal RECORDED when it was
      previewed, holding the bytes recorded then; for a workspace write the call's workspace is the
      owning task's workspace and the relative path is the one the operator saw. The proposal's
      path is never re-resolved and trusted: a link retargeted after review resolves it elsewhere,
      and a proposal recorded before destinations were binds nothing until it is reviewed again;
    * the same prior BYTES and replacement CONTENT: the file at that destination hashes to the
      proposal's expected hash, and the content is the approved content;
    * the same OWNER: a live task of the calling session -- the task a `code.task.step` names,
      or else the session's most recently updated live task -- at its `mutate` stage (where the
      task runtime itself admits the mutation), with the proposal approved and not yet consumed
      by an executed step.

    The reason names the nearest miss in the statuses the task runtime gives the same conditions:
    `destination_changed` (an approval for this exact replacement recorded another target, or the
    path no longer resolves for its writer), `destination_unrecorded` (that approval was recorded
    before destinations were), `stale_base` (the destination no longer holds the reviewed bytes),
    and otherwise `approval_mismatch` (no approved, unconsumed proposal of a live owning task binds
    this call). ``reviewed_bytes=False`` skips only the check of the LIVE bytes, for a door the full
    check has already allowed: it hands the record to the writer, which re-checks those bytes inside
    the flight recorder (so a stale base is journaled as a refused effect) and again inside the
    pinned directory immediately before the rename.

    This is the permission authority's evidence hook for classifying a full-file write over an
    existing file as a verified replace rather than a blind overwrite. Possession of the current
    hash is concurrency evidence only. Read from the journal, never the conversation, so a
    restarted daemon answers the same."""
    context = source_context if isinstance(source_context, dict) else {}
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    clean_intent = str(intent or "").strip()
    clean_path = str(path or "").strip()
    clean_content = str(content or "")
    clean_hash = str(expected_hash or "").strip().lower()
    unbound: tuple[dict[str, Any] | None, str] = (None, "approval_mismatch")
    if clean_intent not in _REPLACEMENT_INTENTS or not session or not clean_path or not clean_hash:
        return unbound
    # The writer's own root precedence (`_workspace_root`): `workspace`, then `workspace_root`.
    call_root = str(context.get("workspace") or context.get("workspace_root") or "").strip()
    if clean_intent == "workspace.write_file" and not call_root:
        return unbound
    from core.runtime_execution_tools import resolve_write_target

    target = resolve_write_target(clean_intent, clean_path, context)
    owner = _approval_owner_payload(session, str(task_id or "").strip())
    if owner is None or str(owner.get("stage") or "") != "mutate":
        return unbound
    task_root = str(owner.get("workspace_root") or "").strip()
    if clean_intent == "workspace.write_file":
        try:
            if Path(call_root).expanduser().resolve() != Path(task_root).expanduser().resolve():
                return unbound
        except (OSError, RuntimeError, ValueError):
            return unbound
    reason = "approval_mismatch"
    for proposal in dict(owner.get("proposals") or {}).values():
        row = dict(proposal or {})
        if not bool(row.get("approved")) or str(row.get("consumed_by") or "").strip():
            continue  # never approved, or an executed step already used this approval
        if str(row.get("intent") or "") != clean_intent:
            continue
        arguments = dict(row.get("arguments") or {})
        if str(arguments.get("path") or "").strip() != clean_path:
            continue
        if str(arguments.get("content") or "") != clean_content:
            continue
        if str(arguments.get("expected_hash") or "").strip().lower() != clean_hash:
            continue
        recorded = [
            record for record in list(dict(row.get("preview") or {}).get("destinations") or [])
            if isinstance(record, dict)
            and str(record.get("intent") or "") == clean_intent
            and str(record.get("path") or "") == clean_path
        ]
        if not recorded:
            reason = "destination_unrecorded"
            continue  # recorded before destinations were: reviewed again, never rebuilt from today's target
        if target is None or str(recorded[0].get("target") or "") != str(target):
            reason = "destination_changed"
            continue  # the call lands on a different physical file than the one reviewed
        if str(recorded[0].get("prior_sha256") or "") != clean_hash:
            reason = "stale_base"
            continue  # the bytes being replaced are not the bytes that were reviewed
        if reviewed_bytes:
            try:
                live = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
            except OSError:
                live = ""
            if live != clean_hash:
                reason = "stale_base"
                continue  # the reviewed destination no longer holds the reviewed bytes
        return dict(recorded[0]), ""
    return None, reason


def approved_replacement_matches(
    source_context: dict[str, Any] | None,
    *,
    intent: str,
    path: str,
    content: str,
    expected_hash: str,
    task_id: str = "",
    reviewed_bytes: bool = True,
) -> bool:
    """Whether an operator-approved, unconsumed code proposal binds exactly this full-file replacement -- the
    permission gate's hook (`approved_replacement_destination` states the binding, and also returns the recorded
    destination or the reason nothing binds). ``reviewed_bytes=False`` leaves the live bytes to a writer that
    re-checks them, as a task step's writer does."""
    record, _reason = approved_replacement_destination(
        source_context,
        intent=intent,
        path=path,
        content=content,
        expected_hash=expected_hash,
        task_id=task_id,
        reviewed_bytes=reviewed_bytes,
    )
    return record is not None


def active_task_control_intents(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Offered by task capability: the control-plane tools the session's open code task needs at
    its CURRENT stage. Read from the journal, never from the conversation, so a restarted daemon
    seats the same tools. Nothing is seated for a session with no open task, or once the task is
    cancelled or rolled back. Bounded to one task (the most recently updated) and five seats including a new opening after completion."""
    context = source_context if isinstance(source_context, dict) else {}
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    if not session:
        return ()
    root = task_dir()
    if not root.is_dir():
        return ()
    best: CodeTask | None = None
    now = datetime.now(timezone.utc)
    for path in root.glob("ct-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("session_id") or "") != session:
            continue
        stage = str(payload.get("stage") or "")
        if stage not in STAGE_CONTROL_INTENTS:
            continue
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            continue
        if (now - updated).total_seconds() > _ACTIVE_TASK_TTL_SECONDS:
            continue
        if best is None or str(payload.get("updated_at") or "") > best.updated_at:
            try:
                best = CodeTask.from_json(payload)
            except (TypeError, ValueError):
                continue
    if best is None:
        return ()
    seats = STAGE_CONTROL_INTENTS.get(best.stage, ())[:4]
    if failed_verification_reopens_review(best):
        # A failed verification lawfully reopens diagnosis: seat the review door the
        # recovery needs, so the model is OFFERED the act the journal permits.
        seats = ("code.task.identify", "code.task.step", "code.task.report", "code.task.cancel")
    elif best.stage == "mutate":
        waiting = [p for p in best.proposals.values() if not p.approved and not p.invalidated and not p.consumed_by]
        if waiting:
            # A proposal at `mutate` still needs its own approval: the revision that replaces an approval
            # a changed file invalidated, or the rest of a declared unit that lands only whole-approved.
            seats = ("code.task.approve", "code.task.step", "code.task.propose", "code.task.cancel")
        elif not _pending_proposals(best):
            # Every approval here already ran or no longer applies (its reviewed base changed), so the
            # lawful next act is a fresh proposal over what the task reads now. Measured in the served
            # stale-edit journey: seated only with `step`, the model's re-proposal was rejected as
            # unoffered and the turn ended at the exact recovery the refusal named.
            seats = ("code.task.propose", "code.task.step", "code.task.report", "code.task.cancel")
    if _completion_verdict(best) == "completed":
        return (*seats, "code.task.open")
    return seats


_RUNTIME = CodeTaskRuntime()


def code_task_runtime() -> CodeTaskRuntime:
    return _RUNTIME


def active_task_mutation_refusal(intent: str, source_context: dict[str, Any] | None) -> str:
    """The typed refusal for a call that bypasses an open task's control plane, or "".

    While a session has an active code task (read from the journal: staged, not terminal, fresh),
    that session may not change a byte through any other door -- not through a workspace mutation
    and not through a plugin tool that declares a mutating side-effect class. The coding lane's
    mutations are proposed as contracted ``code.task.*`` calls, approved, and executed as steps;
    that is what makes every one of them approval-bound, receipted and rollback-able. Refusals are
    decided from TYPED state (the task journal's stage, the contract's side_effect_class), never
    from what the request text looks like.

    Evidence commands (``validation_command``/``sandbox_command``) are held to the SAME law for a
    different reason: a test or shell command run beside the task plane executes for real but its
    outcome never enters the task journal, so the stage machine cannot advance on it. Measured on
    the pinned base: a model that ran the failing suite through the top-level ``workspace.run_tests``
    seat received the real failing output while the journal stayed at ``reproduce`` with no steps --
    work the model honestly believed it had done, silently disconnected from the task. The refusal
    for that class names the evidence step, and the refusal for mutations names the proposal path;
    each class gets the action that is actually lawful for it.
    """
    clean = str(intent or "").strip()
    if not clean or clean.startswith("code.task."):
        return ""
    context = source_context if isinstance(source_context, dict) else {}
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    if not session:
        return ""
    for task in _active_tasks_for_session(session):
        try:
            from core.tool_argument_aliases import side_effect_class_for_intent

            effect_class = side_effect_class_for_intent(clean)
        except Exception:
            effect_class = ""
        if effect_class in _COMMAND_EFFECT_CLASSES or clean in COMMAND_INTENTS:
            if task.stage in COMMAND_STAGES:
                return (
                    f"code task `{task.task_id}` is open at stage `{task.stage}` and collects its "
                    f"evidence through the task plane: re-issue this as `code.task.step` with intent "
                    f"`{clean}` and the same arguments, so its outcome is journaled and advances the task."
                )
            return (
                f"code task `{task.task_id}` is open at stage `{task.stage}`: command steps are lawful "
                "only at " + ", ".join(sorted(COMMAND_STAGES)) + ", through `code.task.step`."
            )
        if effect_class in _MUTATING_EFFECT_CLASSES:
            return (
                f"code task `{task.task_id}` is open at stage `{task.stage}`: mutations must be "
                "proposed through `code.task.propose` and executed as approved `code.task.step` calls."
            )
    return ""


_MUTATING_EFFECT_CLASSES = frozenset(
    {
        # The classes that change the workspace or run local code: exactly the acts the task
        # control plane exists to propose, approve and receipt. Network, media and spending
        # classes keep their own authorities -- a web lookup beside a code task is not a
        # workspace mutation and is none of the task's business.
        "workspace_write",
        "builder_state",
        "sandbox_command",
    }
)

#: Evidence-command classes: lawful acts INSIDE a task (they are how reproduce/narrow/cumulative
#: advance), silently disconnected OUTSIDE it -- a run beside the plane executes for real but the
#: journal never sees its outcome. Held to the step door while a task is open.
_COMMAND_EFFECT_CLASSES = frozenset({"validation_command"})


def _active_tasks_for_session(session: str) -> list[CodeTask]:
    """The session's open tasks from the journal (same typed predicate the offer seats read)."""
    root = task_dir()
    if not root.is_dir():
        return []
    now = datetime.now(timezone.utc)
    tasks: list[CodeTask] = []
    for path in root.glob("ct-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("session_id") or "") != session:
            continue
        if str(payload.get("stage") or "") not in STAGE_CONTROL_INTENTS:
            continue
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            continue
        if (now - updated).total_seconds() > _ACTIVE_TASK_TTL_SECONDS:
            continue
        try:
            tasks.append(CodeTask.from_json(payload))
        except (TypeError, ValueError):
            continue
    return tasks


def dispatch_code_task_intent(
    intent: str, arguments: dict[str, Any], *, source_context: dict[str, Any] | None, workspace_root: Path
):
    """The dispatch seam ``core.runtime_execution_tools`` forwards every ``code.task.*`` call to."""
    runtime = code_task_runtime()
    arguments = dict(arguments or {})
    try:
        if intent == "code.task.open":
            return runtime.open(arguments, workspace_root=workspace_root, source_context=source_context)
        if intent == "code.task.step":
            return runtime.step(arguments, source_context=source_context)
        if intent == "code.task.identify":
            return runtime.identify(arguments, source_context=source_context)
        if intent == "code.task.propose":
            return runtime.propose(arguments, source_context=source_context)
        if intent == "code.task.approve":
            return runtime.approve(arguments, source_context=source_context)
        if intent == "code.task.cancel":
            return runtime.cancel(arguments, source_context=source_context)
        if intent == "code.task.rollback":
            return runtime.rollback(arguments, source_context=source_context)
        if intent == "code.task.report":
            return runtime.report(arguments, source_context=source_context)
        if intent == "code.task.pr_description":
            return runtime.pr_description(arguments, source_context=source_context)
    except (JournalConflictError, LockUnavailable) as exc:
        # Nothing was decided on a journal this runtime could not hold: no effect ran.
        return CodeTaskRuntime._result(
            None,
            ok=False,
            status="journal_unavailable",
            text=f"The code task journal could not be held safely ({exc}); nothing was executed. Retry the call.",
            reason=str(exc),
        )
    return None
