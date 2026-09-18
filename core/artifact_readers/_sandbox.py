"""How a reader is allowed to run an external decoder: confined, bounded, offline -- or not at all.

A document reader is the one place in this runtime where *hostile bytes chosen by someone else*
are handed to a complex C parser. ffmpeg, libarchive, PDFKit and Vision are all excellent and all
have had memory-safety CVEs. So the decoder does not get the machine, and where the machine cannot
enforce that, the decoder does not run:

* **Confinement is a precondition, not a preference.** Without an OS sandbox this module refuses
  ``ReaderUnavailable`` *before* spawning anything. Scrubbing the environment and setting rlimits
  is neither network nor filesystem confinement, and a build that only did those would be running
  untrusted parsers with the operator's whole home directory readable. Every capability report
  derives from :func:`confinement_available`, so a machine without it reports those formats
  BLOCKED rather than quietly reading them unconfined.
* **No network.** The profile denies every outbound socket. A PDF with a remote XObject, a DOCX
  with a linked image, an HLS playlist inside a video container -- none can fetch, because the
  process that would fetch cannot open a socket.
* **Scoped reads.** The operator's home, other volumes, ``/private/etc``, the system keychains,
  other processes' temporary containers and every sibling scratch directory are denied. What is
  left is this extraction's own scratch directory plus the system framework tree the decoder needs
  in order to start. A decoder cannot read the file it was not given.
* **Scoped writes.** One per-extraction scratch directory. Nothing else -- including the scratch
  directories of other extractions running at the same moment.
* **Bounded output, CPU, files and wall clock**, enforced *while* the decoder runs: stdout and
  stderr are drained in chunks against separate caps, and passing either kills the process group
  immediately rather than after this process has already buffered the flood.
* **A kill is a typed error.** Never a partial result presented as a complete one.

Two bounds are deliberately NOT claimed. ``ulimit -v``/``RLIMIT_AS`` is not settable on macOS
(measured 2026-09-03), so there is no address-space ceiling; and the profile allows reading the
system framework tree, because a dynamically linked binary that cannot read its own libraries
aborts before ``main``. Both are stated here rather than implied away.
"""

from __future__ import annotations

import contextlib
import errno
import os
import platform
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from ._limits import (
    EXTRACTION_TIMEOUT_S,
    MAX_SCRATCH_BYTES,
    MAX_SCRATCH_SCAN_ENTRIES,
    SCRATCH_POLL_SECONDS,
    SUBPROCESS_CPU_SECONDS,
    SUBPROCESS_MAX_OPEN_FILES,
    SUBPROCESS_MAX_OUTPUT_BYTES,
    SUBPROCESS_MAX_STDERR_BYTES,
    SUBPROCESS_TIMEOUT_S,
)
from ._types import ReaderFailed, ReaderRefused, ReaderUnavailable

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_SHELL = "/bin/sh"

#: The ONLY paths whose file CONTENT a decoder may read, beyond its own scratch directory and
#: whatever the caller grants explicitly. Measured, not guessed: each entry is here because a real
#: decoder failed without it (2026-09-03), and the set was checked against `/bin/cat`, `bsdtar`,
#: `ffprobe`, `ffmpeg`, PDFKit and Vision.
#:
#: These are executable, library and framework trees -- not user data. `(literal "/")` is the root
#: DIRECTORY entry (an `opendir`, needed for path resolution), never the bytes of any file.
_RUNTIME_DATA_SUBPATHS: tuple[str, ...] = (
    "/usr/lib",                 # dylibs
    "/usr/share",               # ICU, timezone and locale data
    "/System",                  # frameworks, and the dyld cache on current macOS
    "/bin",
    "/usr/bin",
    "/sbin",
    "/private/var/db/dyld",     # dyld cache on older macOS
    "/opt/homebrew/bin",        # ffmpeg / ffprobe themselves
    "/opt/homebrew/lib",        # and the libraries they link
    "/opt/homebrew/Cellar",
    "/opt/homebrew/opt",
)
_RUNTIME_DATA_LITERALS: tuple[str, ...] = (
    "/",                        # the root directory entry, for path resolution
    "/dev/null",
    "/dev/zero",
    "/dev/urandom",
    "/dev/random",
    "/dev/dtracehelper",
)


def _profile(*, scratch: str, extra_read: Sequence[str], allow_gpu: bool) -> str:
    """Deny by default; keep execution working; allow file CONTENT only where it is needed.

    The distinction that matters, and the one an earlier version of this file got wrong:

    * **Execution, library mapping and path resolution stay broad.** A dynamically linked binary
      that cannot map its own libraries aborts before ``main``, with no diagnostic at all -- an
      allowlist that omits one framework path looks exactly like a crashing decoder.
    * **File CONTENT is denied everywhere and re-granted by allowlist.** ``file-read-data`` is
      revoked across the whole filesystem and returned only for the runtime trees above, this
      extraction's scratch directory, and whatever the caller explicitly asks for.

    The previous shape -- allow everything, then deny a list of familiar private directories --
    was not an extraction-only policy, and a review demonstrated it: a synthetic file in an
    unrelated ``/private/var/tmp`` directory was read in full, because that directory was simply
    not on the list. A blacklist answers "which secrets did we think of"; this answers "what does
    the decoder need".
    """
    lines = [
        "(version 1)",
        "(deny default)",
        "(deny network*)",
        "(allow process-fork)",
        "(allow process-exec*)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
    ]
    if allow_gpu:
        # Vision's recognizer runs on the GPU/Neural Engine and cannot open those devices without
        # this. Measured: without it VNRecognizeTextRequest fails with a generic ObjC error and
        # OCR returns nothing -- which would have read as "the scan had no text". OCR path only.
        lines.append("(allow iokit-open)")
    # Metadata, execution and mapping: broad, because the loader needs them and they expose
    # existence and size, never contents.
    lines.append('(allow file-read* (subpath "/"))')
    # Contents: nothing, anywhere...
    lines.append('(deny file-read-data (subpath "/"))')
    # ...except these. Last-match-wins, so this is the complete content-read set.
    allowed = [f'(literal "{path}")' for path in _RUNTIME_DATA_LITERALS]
    allowed += [f'(subpath "{path}")' for path in _RUNTIME_DATA_SUBPATHS]
    allowed.append(f'(subpath "{scratch}")')
    allowed += [f'(subpath "{path}")' for path in extra_read]
    lines.append("(allow file-read-data " + " ".join(allowed) + ")")
    # WRITES ARE SCRATCH-ONLY, with no caller-supplied widening. There used to be an
    # `extra_write` parameter, and the one decoder that used it (textutil, granted the per-user
    # temp directory) turned out to permit overwriting unrelated files there -- read denial does
    # not prevent mutation, and such writes are outside the scratch quota's scan. That decoder now
    # converts through stdout instead and needs no grant, so the parameter is gone rather than
    # left available for the next caller to reach for.
    lines.append(f'(allow file-write* (subpath "{scratch}"))')
    lines.append('(allow file-write-data (literal "/dev/null"))')
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    #: Always True on a returned result: an unconfined run raises rather than returning one.
    network_confined: bool = True


def confinement_available() -> bool:
    """Whether this machine can actually confine a decoder. Nothing external runs without it."""
    return platform.system() == "Darwin" and os.path.exists(_SANDBOX_EXEC) and os.access(_SHELL, os.X_OK)


def confinement_unavailable_reason() -> str:
    if confinement_available():
        return ""
    if platform.system() != "Darwin":
        return f"process confinement for untrusted decoders is implemented for macOS only; this host is {platform.system()}"
    return "the macOS sandbox (/usr/bin/sandbox-exec) is not available on this machine"


def require_confinement(fmt: str = "") -> None:
    """Raise BEFORE any decoder is spawned when this machine cannot confine it."""
    if confinement_available():
        return
    raise ReaderUnavailable(
        f"Reading this file needs an external decoder, and {confinement_unavailable_reason()}. "
        f"It was not read: this runtime does not run untrusted document parsers unconfined.",
        code="confinement_unavailable",
        remediation="Use a macOS host where the sandbox is available, or attach the content as text.",
        fmt=fmt,
    )


# --- the extraction-wide budget -------------------------------------------------------------------


@dataclass
class ExtractionBudget:
    """One attachment's whole extraction: a deadline and a scratch quota, shared by every call.

    Per-call timeouts alone cannot bound a video that decodes 24 frames, each inside its own
    90-second limit. This deadline is what makes ``EXTRACTION_TIMEOUT_S`` a real ceiling rather
    than a declaration: every ``run_confined`` shortens its own timeout to fit inside what is left.
    """

    deadline: float
    scratch: Path | None = None
    quota_bytes: int = MAX_SCRATCH_BYTES

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def over_quota(self) -> bool:
        """Cheap enough to ask on every watchdog tick; safe enough to ask while a decoder runs."""
        if self.scratch is None:
            return False
        return _directory_bytes(self.scratch) > self.quota_bytes

    def quota_refusal(self, fmt: str = "") -> ReaderRefused:
        shown = (
            f"{self.quota_bytes // (1024 * 1024)} MB"
            if self.quota_bytes >= 1024 * 1024
            else f"{max(1, self.quota_bytes // 1024)} KB"
        )
        return ReaderRefused(
            f"Reading this {fmt or 'file'} produced more than {shown} of intermediate data; it was stopped.",
            code="scratch_quota_exceeded",
            remediation="The file expands to far more than its size suggests.",
            fmt=fmt,
        )

    def check(self, fmt: str = "") -> None:
        if self.remaining() <= 0:
            raise ReaderFailed(
                f"Reading this {fmt or 'file'} passed the {EXTRACTION_TIMEOUT_S}s limit for one attachment; it was not read.",
                code="extraction_timeout",
                remediation="The file is larger or more complex than this reader's time budget allows.",
                fmt=fmt,
            )
        if self.over_quota():
            raise self.quota_refusal(fmt)


_BUDGET: ContextVar[ExtractionBudget | None] = ContextVar("vool_reader_budget", default=None)


@contextlib.contextmanager
def extraction_budget(*, scratch: Path | None = None, seconds: int = EXTRACTION_TIMEOUT_S) -> Iterator[ExtractionBudget]:
    """Open one attachment's budget. A nested open REUSES the outer one, so a targeted re-read
    inside an extraction cannot quietly grant itself a fresh deadline."""
    existing = _BUDGET.get()
    if existing is not None:
        if scratch is not None and existing.scratch is None:
            existing.scratch = scratch
        yield existing
        return
    budget = ExtractionBudget(deadline=time.monotonic() + max(1, int(seconds)), scratch=scratch)
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)


def current_budget() -> ExtractionBudget | None:
    return _BUDGET.get()


def _resolved(paths: Sequence[str]) -> list[str]:
    """Real paths only, de-duplicated, existing directories only. A scope entry that does not
    resolve to a real directory is dropped rather than written into the profile as dead text."""
    out: list[str] = []
    for entry in paths:
        try:
            real = str(Path(entry).resolve())
        except (OSError, ValueError):
            continue
        if real not in out and os.path.isdir(real):
            out.append(real)
    return out


def _directory_bytes(root: Path, *, max_entries: int = MAX_SCRATCH_SCAN_ENTRIES) -> int:
    """Bytes under `root`, measured safely enough to run repeatedly while a decoder is alive.

    Three properties this needs and a naive ``os.walk`` does not have:

    * **Symlinks are never followed** -- neither for size (``lstat``, so a link counts as a link,
      not as its target) nor for traversal (a link to ``/`` would otherwise walk the filesystem).
    * **The scan is bounded.** A decoder that creates a million files must not turn the watchdog
      into the denial of service it is supposed to prevent. Hitting the entry cap returns
      ``inf``: an unmeasurable scratch is over budget by definition, which fails closed.
    * **It touches nothing outside `root`.** No unrelated directory is read to enforce a quota.
    """
    total = 0
    seen = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > max_entries:
                        return float("inf")  # type: ignore[return-value]
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    total += int(info.st_size)
        except OSError:
            continue
    return total


class Scratch:
    """A private directory for one extraction. Everything in it goes when the block ends."""

    def __init__(self, prefix: str = "vool-reader-") -> None:
        self._prefix = prefix
        self.path = Path("")

    def __enter__(self) -> Scratch:
        self.path = Path(tempfile.mkdtemp(prefix=self._prefix)).resolve()
        os.chmod(self.path, 0o700)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.path:
            shutil.rmtree(self.path, ignore_errors=True)

    def bytes_used(self) -> int:
        return _directory_bytes(self.path)


# --- running one decoder --------------------------------------------------------------------------


def _limited_argv(argv: Sequence[str], *, cpu_seconds: int) -> list[str]:
    """Wrap the decoder in a shell that sets its own rlimits, then execs it.

    Deliberately NOT ``preexec_fn``: the served runtime is threaded, and running Python between
    ``fork`` and ``exec`` in a threaded process can deadlock on a lock another thread holds. The
    shell does the same work with no Python in the child. ``ulimit -v`` is omitted because macOS
    refuses to set it -- see ``_limits.SUBPROCESS_CPU_SECONDS``.
    """
    # NO `ulimit -u`. RLIMIT_NPROC counts every process owned by the real UID, not the ones this
    # decoder starts, so any value low enough to bound a fork bomb is already far below the
    # operator's existing process count -- and the first fork fails with EAGAIN. Measured
    # 2026-09-03: at 64 it made every decoder that spawns a helper exit 128 before doing any work.
    # Runaway children are bounded by the process-group kill in `_kill_group` instead, which is
    # what actually reaps them.
    script = (
        f"ulimit -c 0; ulimit -t {int(cpu_seconds)}; ulimit -n {int(SUBPROCESS_MAX_OPEN_FILES)}; "
        f'ulimit -f {int(MAX_SCRATCH_BYTES // 512)}; exec "$0" "$@"'
    )
    return [_SHELL, "-c", script, *argv]


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the decoder AND anything it spawned: a decoder that forked must not outlive its turn."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError) as exc:
        if getattr(exc, "errno", None) not in (errno.ESRCH, errno.EPERM, None):
            raise
    except OSError:
        pass
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _supervise(
    process: subprocess.Popen,
    *,
    deadline: float,
    max_stdout: int,
    max_stderr: int,
    fmt: str,
    budget: ExtractionBudget | None = None,
) -> tuple[bytes, bytes]:
    """Supervise one decoder for the lifetime of the JOB, not the lifetime of its pipes.

    The distinction is load-bearing, and an earlier version got it wrong. Monitoring used to run
    ``while selector.get_map()`` -- that is, only while stdout or stderr were still open. A child
    that closes both (``exec 1>&- 2>&-``) reaches EOF immediately, the loop exits, and the
    subsequent ``wait()`` lets it run on unwatched: measured, a decoder closed its pipes, wrote
    8 KB against a 4 KB quota, slept, and still wrote its completion marker. The quota was
    reported afterwards, which is a complaint, not a limit.

    So the loop ends when the OWNED JOB ends -- pipes drained *and* the process exited -- and the
    quota and deadline are checked on their own cadence throughout, whether or not anything is
    still being written. There is deliberately no requirement that a decoder keep a pipe open or
    produce output: a silent child that closes its pipes and does legitimate work runs to
    completion exactly as before. Output caps still apply for as long as the pipes are open.
    """
    chunks: dict[int, list[bytes]] = {1: [], 2: []}
    sizes = {1: 0, 2: 0}
    caps = {1: max_stdout, 2: max_stderr}
    selector = selectors.DefaultSelector()
    for key, stream in ((1, process.stdout), (2, process.stderr)):
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, key)
    overflowed = 0
    quota_blown = False
    next_quota_check = time.monotonic() + SCRATCH_POLL_SECONDS
    try:
        while True:
            pipes_open = bool(selector.get_map())
            if not pipes_open and process.poll() is not None:
                break  # the job is over: nothing left to read, nothing left running
            now = time.monotonic()
            if now >= deadline:
                _kill_group(process)
                raise ReaderFailed(
                    f"The {fmt or 'file'} decoder was stopped at its time limit without finishing; the file was not read.",
                    code="decoder_timeout",
                    remediation="The file is larger or more complex than this reader's time budget allows.",
                    fmt=fmt,
                )
            if budget is not None and now >= next_quota_check:
                next_quota_check = now + SCRATCH_POLL_SECONDS
                if budget.over_quota():
                    quota_blown = True
                    break
            wait_for = max(0.0, min(deadline - now, SCRATCH_POLL_SECONDS))
            if not pipes_open:
                # Pipes closed, child still alive. Keep supervising on the poll cadence rather
                # than handing the child an unwatched run.
                time.sleep(wait_for)
                continue
            for event, _mask in selector.select(timeout=wait_for):
                key = event.data
                try:
                    data = event.fileobj.read(65536)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    data = b""
                if not data:
                    selector.unregister(event.fileobj)
                    continue
                sizes[key] += len(data)
                if sizes[key] > caps[key]:
                    overflowed = key
                    break
                chunks[key].append(data)
            if overflowed:
                break
    finally:
        selector.close()
    if quota_blown:
        _kill_group(process)
        if budget is not None:
            raise budget.quota_refusal(fmt)
        raise ReaderRefused("The extraction exceeded its scratch quota.", code="scratch_quota_exceeded", fmt=fmt)
    if overflowed:
        _kill_group(process)
        which = "output" if overflowed == 1 else "diagnostics"
        raise ReaderRefused(
            f"The {fmt or 'file'} decoder produced more {which} than the "
            f"{max(1, caps[overflowed] // (1024 * 1024))} MB limit allows and was stopped; the file was not read.",
            code="decoder_output_too_large",
            remediation="The file appears designed to exhaust the reader rather than to be read.",
            fmt=fmt,
        )
    return b"".join(chunks[1]), b"".join(chunks[2])


def run_confined(
    argv: Sequence[str],
    *,
    scratch: Path,
    timeout: int = SUBPROCESS_TIMEOUT_S,
    fmt: str = "",
    allow_gpu: bool = False,
    extra_read: Sequence[str] = (),
    max_stdout: int = SUBPROCESS_MAX_OUTPUT_BYTES,
    max_stderr: int = SUBPROCESS_MAX_STDERR_BYTES,
) -> SandboxResult:
    """Run one decoder under confinement. Refuses BEFORE spawning if it cannot be confined."""
    require_confinement(fmt)
    executable = str(argv[0])
    if not os.path.isabs(executable) or not os.access(executable, os.X_OK):
        raise ReaderFailed(
            f"The decoder {executable!r} is not an executable absolute path.",
            code="decoder_not_executable",
            fmt=fmt,
        )
    scratch_path = str(Path(scratch).resolve())
    budget = current_budget()
    if budget is not None:
        budget.check(fmt)
        timeout = max(1, min(int(timeout), int(budget.remaining())))
    # Every scope path is RESOLVED before it reaches the profile. `/tmp` is a symlink to
    # `/private/tmp` on macOS, and an SBPL `subpath` matches the real path only -- an unresolved
    # entry silently grants nothing, which shows up as the decoder dying on SIGSEGV before `main`
    # rather than as a permission error. Measured 2026-09-03 on the compiled OCR helper.
    profile = _profile(scratch=scratch_path, extra_read=_resolved(extra_read), allow_gpu=allow_gpu)
    command = [_SANDBOX_EXEC, "-p", profile, *_limited_argv(argv, cpu_seconds=min(SUBPROCESS_CPU_SECONDS, timeout))]
    # An empty environment plus only what a decoder needs to find its own libraries. No proxy
    # variables, no credentials, no real HOME: a decoder that wanted a token could not find one,
    # and one that wanted a proxy could not reach it even if the sandbox were somehow absent.
    env = {
        "PATH": "/usr/bin:/bin",
        "TMPDIR": scratch_path,
        "LC_ALL": "C.UTF-8",
        "HOME": scratch_path,
    }
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=scratch_path,
            start_new_session=True,  # its own process group, so the whole tree can be killed
        )
    except OSError as exc:
        raise ReaderFailed(f"The {fmt or 'file'} decoder could not be started ({exc}).", code="decoder_start_failed", fmt=fmt) from exc

    deadline = time.monotonic() + timeout
    try:
        stdout, stderr = _supervise(process, deadline=deadline, max_stdout=max_stdout, max_stderr=max_stderr, fmt=fmt, budget=budget)
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill_group(process)
            raise ReaderFailed(
                f"The {fmt or 'file'} decoder did not exit within its time limit; the file was not read.",
                code="decoder_timeout",
                fmt=fmt,
            ) from None
    finally:
        # Whatever happened, nothing the decoder started is left running or holding a pipe.
        if process.poll() is None:
            _kill_group(process)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    if budget is not None:
        budget.check(fmt)
    if process.returncode is not None and process.returncode < 0:
        raise ReaderFailed(
            f"The {fmt or 'file'} decoder was killed by signal {-process.returncode}; the file was not read.",
            code="decoder_killed",
            remediation="The file exceeded the decoder's CPU or file-size ceiling, or the decoder faulted on it.",
            fmt=fmt,
        )
    return SandboxResult(int(process.returncode or 0), stdout, stderr, True)


__all__ = [
    "ExtractionBudget",
    "SandboxResult",
    "Scratch",
    "confinement_available",
    "confinement_unavailable_reason",
    "current_budget",
    "extraction_budget",
    "require_confinement",
    "run_confined",
]
