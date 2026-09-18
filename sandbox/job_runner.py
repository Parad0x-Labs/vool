from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from sandbox.container_adapter import ExecutionResult
from sandbox.network_guard import command_uses_network
from sandbox.resource_limits import (
    ExecutionPolicy,
    normalize_policy,
    path_args_within_roots,
    path_within_roots,
)

#: Files to look at before deciding a writable root has no hard links. A root with more
#: entries than this is not a sane sandbox target, and answering "probably fine, I stopped
#: looking" is the failure mode this whole lane exists to delete — so the cap refuses.
_HARDLINK_SCAN_FILE_LIMIT = 200_000


class _JobCancelledError(Exception):
    """Internal: the cancel event fired while the job was running."""


class HardLinkedWritableRootError(ValueError):
    """A writable root contains a file with more than one name on the filesystem.

    Path-based kernel confinement — Seatbelt, and bwrap's bind mounts — decides by PATH. A
    hard link is a second path to the same inode, so `workspace/innocent.txt` can be the
    operator's `~/Desktop/keys.txt` and the kernel will allow the write, correctly, because
    the path it was asked about really is inside the workspace. Measured on macOS 26.5.1:
    writing through a pre-planted hard link succeeds under `(allow file-write* (subpath ws))`,
    and adding `(deny file-link)` does not change it — that rule governs CREATING links.

    What the kernel does close is the other half: a confined job cannot create a hard link at
    all (`os.link` fails under the profile), so a link inside a writable root was put there by
    something that was already running unconfined. This check is what notices that, before the
    job starts, from `st_nlink` — an OS fact about an inode, not a guess about a string.
    """


class KernelIsolationUnavailableError(ValueError):
    """No isolation backend on this host can actually enforce no-egress.

    Distinct from the other refusals `run` raises so a caller can tell "this host cannot isolate"
    from "policy forbids this command". Subclasses ValueError deliberately: every existing caller
    catches ValueError and keeps working unchanged.
    """


# Whether a backend actually works on this host, keyed by backend name. A backend being INSTALLED
# and a backend being PERMITTED are different questions, and only the second one matters:
# `unshare` ships in util-linux on every mainstream distro, so `shutil.which` finds it inside
# containers and CI runners that deny the namespace syscall it needs. Probed once per process --
# it is a property of the host and the probe costs a subprocess.
_BACKEND_USABLE: dict[str, bool] = {}


def reset_isolation_backend_probe_cache() -> None:
    """Forget probe results. For tests that fake a backend's availability."""
    _BACKEND_USABLE.clear()


def _backend_usable(name: str, probe_argv: list[str]) -> bool:
    """Whether *probe_argv* -- the wrapper plus a no-op command -- actually completes.

    Presence was the bug this exists to close. On a GitHub Actions runner `unshare` is on PATH and
    `unshare -n -- cat file` exits non-zero with `unshare failed: Operation not permitted`, so the
    wrapper's failure was reaching the user as though THEIR command had failed: `cat present.txt`
    came back `ok=False, status='command_failed'`. Nothing downstream could tell a broken command
    from a sandbox that was never able to run one.
    """
    cached = _BACKEND_USABLE.get(name)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            probe_argv,
            capture_output=True,
            timeout=10,
            shell=False,
            check=False,
        )
        usable = completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        usable = False
    _BACKEND_USABLE[name] = usable
    return usable


def _seatbelt_subpath_literal(path: Path) -> str:
    # Seatbelt string literals are double-quoted; escape embedded quotes/backslashes.
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'(subpath "{escaped}")'


def _sensitive_read_deny_roots() -> list[Path]:
    """Directories that a sandboxed job must never read, even though reads are otherwise open so
    interpreters can load their stdlib. This closes the exfil target the audit named: an
    allowed interpreter (`python -c "open('~/.ssh/id_rsa').read()"`) reading credentials or
    VOOL's own key home at runtime, which the argv path-guard cannot see. Deny-listing these
    specific secret dirs is far safer than a read allow-list (which would break stdlib loading)."""
    home = Path.home()
    roots = [
        home / ".ssh", home / ".aws", home / ".gnupg", home / ".config" / "solana",
        home / ".config" / "gcloud", home / ".config" / "gh", home / ".config" / "solana-keygen",
        home / ".vool_runtime", home / ".config" / "solana" / "id.json",
    ]
    for env in ("VOOL_HOME", "VOOL_RUNTIME_HOME"):
        val = str(os.environ.get(env) or "").strip()
        if val:
            roots.append(Path(val))
    # De-dup while preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _runtime_protection_clauses(write_roots: tuple[Path, ...]) -> str:
    """Seatbelt clauses that keep this runtime's own state (no read, no write) and its running code (no write) out of a
    job's reach (core.runtime_state_protection). A write root strictly inside the code root that is an exempt workspace
    folder is allowed again; the code root itself never is."""
    try:
        from core.runtime_state_protection import code_write_exemptions, protected_code_roots, protected_state_roots

        state, code, exempt = protected_state_roots(), protected_code_roots(), code_write_exemptions()
    except Exception:
        return ""

    def literals(paths) -> str:
        return "".join(_seatbelt_subpath_literal(variant) for path in paths for variant in _path_variants(path))

    clauses = ""
    if code:
        clauses += f"(deny file-write* {literals(code)})"
        reallowed = [root for root in write_roots if any(root == allowed or allowed in root.parents for allowed in exempt)]
        if reallowed:
            clauses += f"(allow file-write* {literals(reallowed)})"
    if state:
        # file-read-data is named as well as file-read*: Seatbelt lets an earlier (allow file-read-data ...) for a containing
        # root win over a later (deny file-read* ...), measured with sandbox-exec; the explicit form placed last does not.
        clauses += f"(deny file-read* {literals(state)})(deny file-read-data {literals(state)})(deny file-write* {literals(state)})"
    return clauses


def _macos_confined_profile(
    allowed_roots: tuple[Path, ...],
    *,
    deny_network: bool = True,
    read_roots: tuple[Path, ...] = (),
) -> str:
    """Build a Seatbelt profile that confines file *writes* to the allowed workspace roots, and
    (when `deny_network` is true) also denies all network egress.

    Reads stay permitted so interpreters/tooling can load their stdlib and
    dylibs without bespoke allow-lists, EXCEPT for a deny-list of secret dirs
    (SSH/cloud/GPG credentials and VOOL's own key home) that the kernel blocks
    even from an allowed interpreter's runtime open() — the argv path-guard in
    :meth:`JobRunner.run` only sees literal path arguments, not runtime reads, so
    it is a first line, not the read boundary. Writes are denied by the kernel so
    a job cannot tamper with or persist outside its workspace even if the static
    guard is bypassed.

    ANVIL, 2026-08-07: `deny_network=False` exists because network trust and filesystem
    confinement are independent controls, not one boolean. `workspace.run_tests` (etc.) setting
    a trusted-local network relaxation used to skip building this profile AT ALL -- which meant a
    generated test that called `open('/tmp/x', 'w')` wrote there with no denial, no approval
    prompt, verified live (`test_macos_seatbelt_still_confines_writes_when_network_is_relaxed`).
    The write-confinement clauses below must never depend on whether network is also denied.
    """
    write_roots: list[Path] = []
    seen: set[str] = set()
    for root in allowed_roots:
        for variant in _path_variants(root):
            key = str(variant)
            if key not in seen:
                seen.add(key)
                write_roots.append(variant)
    allow_clauses = "".join(_seatbelt_subpath_literal(p) for p in write_roots)
    # /dev is needed for normal stdio (e.g. /dev/null, /dev/urandom).
    allow_clauses += '(subpath "/dev")'
    network_clause = "(deny network*)" if deny_network else ""

    # READ confinement, in three layers, and the ORDER is the policy (Seatbelt is
    # last-match-wins):
    #
    #   1. `(deny file-read-data (subpath $HOME))` — the CONTENTS of the operator's home are
    #      unreadable. Deliberately `file-read-data` and not `file-read*`: the starred form
    #      also denies read-METADATA, and metadata on every ancestor directory is what a path
    #      lookup needs, so denying it makes `execvp()` of an interpreter living under the
    #      home fail with "Operation not permitted" before the job starts. Measured, not
    #      guessed. The cost is honest and stated in the docs: a job can still stat and list
    #      inside the home and learn that a file EXISTS. It cannot read a byte of it.
    #   2. `(allow file-read-data <read_roots + writable roots>)` — the named exceptions, and
    #      the reason `read_roots` exists at all: an interpreter has to load its own stdlib,
    #      and on this machine that stdlib lives under the home being denied.
    #   3. `(deny file-read* <secret roots>)` LAST, so it beats layer 2 even if a credential
    #      directory sits inside a read root. For these, metadata goes too: a job has no
    #      business learning the names of the keys in `~/.ssh` either.
    #
    # Denying the whole home rather than a list of secret directories is the point of the
    # change. A deny-list names the credential paths somebody thought of; browser profiles,
    # other worktrees, the operator's checkout, `~/.npmrc`, `~/.cargo/credentials` and a
    # downloads folder full of exports were on nobody's list, and a build script does not need
    # a path to be listed to read it.
    home_deny = ""
    try:
        home_deny = "".join(_seatbelt_subpath_literal(v) for v in _path_variants(Path.home()))
    except (OSError, RuntimeError):
        home_deny = ""
    home_deny_clause = f"(deny file-read-data {home_deny})" if home_deny else ""
    read_back = "".join(
        _seatbelt_subpath_literal(variant)
        for root in (*read_roots, *allowed_roots)
        for variant in _path_variants(root)
    )
    read_back_clause = f"(allow file-read-data {read_back})" if read_back else ""
    secret_deny = "".join(
        _seatbelt_subpath_literal(variant)
        for root in _sensitive_read_deny_roots()
        for variant in _path_variants(root)
    )
    # Named file-read-data as well: a (deny file-read* ...) alone does NOT beat the earlier (allow file-read-data ...) of a read
    # root that contains a secret directory (measured with sandbox-exec, 2026-09-15); the explicit operation placed last does.
    secret_deny_clause = f"(deny file-read* {secret_deny})(deny file-read-data {secret_deny})" if secret_deny else ""
    # LAST, because Seatbelt is last-match-wins: this runtime's own state and running code stay out of reach even when an
    # allowed or read-back root contains them (core.runtime_state_protection).
    protection_clause = _runtime_protection_clauses(tuple(write_roots))
    return (
        "(version 1)"
        "(allow default)"
        f"{network_clause}"
        "(deny file-write*)"
        f"(allow file-write* {allow_clauses})"
        f"{home_deny_clause}"
        f"{read_back_clause}"
        f"{secret_deny_clause}"
        f"{protection_clause}"
    )


def _path_variants(path: Path) -> tuple[Path, ...]:
    # macOS aliases /tmp -> /private/tmp and /var -> /private/var. The policy
    # stores the resolved path; include the unresolved alias too so a job
    # launched with the /tmp form is still allowed by the kernel profile.
    resolved = path.resolve()
    variants = [resolved]
    text = str(resolved)
    if text.startswith("/private/tmp"):
        variants.append(Path(text.replace("/private/tmp", "/tmp", 1)))
    elif text.startswith("/private/var"):
        variants.append(Path(text.replace("/private/var", "/var", 1)))
    return tuple(variants)


def _truncate(text: str, limit_kb: int) -> str:
    raw = text or ""
    encoded = raw.encode("utf-8")
    if len(encoded) <= limit_kb * 1024:
        return raw
    return encoded[-(limit_kb * 1024) :].decode("utf-8", errors="replace")


def _decode_partial(captured: object) -> str:
    # subprocess.TimeoutExpired.stdout/stderr may be None, str (text=True), or
    # bytes depending on how far the read got; normalize to a string.
    if captured is None:
        return ""
    if isinstance(captured, bytes):
        return captured.decode("utf-8", errors="replace")
    return str(captured)


def _hard_linked_entry(roots: tuple[Path, ...]) -> tuple[Path | None, bool]:
    """``(offending_path, scan_complete)`` for the writable roots.

    Cheap in practice: 4,511 files in 0.02s on this repo, measured. `lstat` rather than
    `stat` so a symlink is never followed out of the root and counted for its target.
    """
    scanned = 0
    seen_roots: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen_roots or not root.exists():
            continue
        seen_roots.add(key)
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                scanned += 1
                if scanned > _HARDLINK_SCAN_FILE_LIMIT:
                    return None, False
                try:
                    stat_result = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                if stat_result.st_nlink > 1:
                    return Path(dirpath) / name, True
    return None, True


def _terminate_process_group(process: subprocess.Popen, *, only_if_populated: bool = False) -> None:
    """Kill the job's whole process group — children, grandchildren, everything.

    The group id IS the child's pid because `run` starts it with `start_new_session=True`.
    SIGTERM first so a well-behaved tool can flush, then SIGKILL for anything that ignores it.

    `only_if_populated` is the success-path sweep: the direct child is already reaped, so
    signalling the group is a no-op unless something it spawned is still there. `ProcessLookup`
    means the group is empty, which is the outcome this function exists to produce.
    """
    if os.name == "nt":  # pragma: no cover - no process groups of this shape on Windows
        with contextlib.suppress(Exception):
            process.kill()
        return
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return
    if pgid == os.getpgid(0):  # pragma: no cover - defensive: never signal our own group
        return
    if only_if_populated:
        try:
            os.killpg(pgid, 0)
        except (OSError, ProcessLookupError):
            return
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (OSError, ProcessLookupError):
            break
        time.sleep(0.05)
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


class JobRunner:
    def __init__(self, policy: ExecutionPolicy):
        self.policy = normalize_policy(policy)

    #: Variables a child genuinely needs to start. EVERYTHING else the operator's shell is
    #: carrying — API keys, cloud tokens, `SSH_AUTH_SOCK`, `GITHUB_TOKEN`, npm/cargo registry
    #: credentials, the operator's real `HOME` — is dropped. This is an allow-list on purpose:
    #: a deny-list of "secret-looking" names is the same losing game as scanning a command
    #: string, and the environment is the one channel where a build script can find a
    #: credential without ever naming a path.
    _ENV_PASSTHROUGH = (
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TERM",
        "SHELL",
        "USER",
        "LOGNAME",
    )

    def _child_environment(self, cwd_path: Path) -> dict[str, str]:
        """A minimal, sanitized environment whose writable roots are all inside the task root.

        `HOME`, `TMPDIR` and the XDG config/cache/data roots are pointed at directories under
        the job's own writable root. That matters twice over: a tool that writes to `~/.npm`,
        `~/.cargo` or `$TMPDIR` now writes inside the disposable area instead of being denied
        by the kernel and failing confusingly, and a tool that READS `$HOME/.aws/credentials`
        finds an empty directory rather than the operator's.
        """
        source = os.environ
        env = {
            key: str(source[key])
            for key in self._ENV_PASSTHROUGH
            if str(source.get(key) or "").strip()
        }
        task_root = Path(self.policy.workspace_root)
        home = task_root / ".sandbox-home"
        tmp = task_root / ".sandbox-tmp"
        for path in (home, tmp, home / ".cache", home / ".config", home / ".local" / "share"):
            with contextlib.suppress(OSError):
                path.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(tmp),
                "TEMP": str(tmp),
                "TMP": str(tmp),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "PWD": str(cwd_path),
                "VOOL_EXECUTION_BACKEND": self.policy.backend,
                # Proxies are an egress path that survives a network-denying profile on hosts
                # where the profile is not the boundary; refusing them costs nothing here.
                "NO_PROXY": "*",
                "no_proxy": "*",
                # A sandboxed command must not leave bytecode behind in a tree it merely READ.
                # `python -m unittest` importing a subject module writes `__pycache__/<stem>.pyc`
                # next to it, which for a read-only audit is a real, CI-observed mutation of a
                # tree asserted byte-identical (run 31131255470).
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

    #: Returned when the operator cancelled the job. Distinct from 124 (timed out) so a
    #: receipt can say which happened; 125 is unused by the shells this runs.
    CANCELLED_RETURNCODE = 125

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ExecutionResult:
        if not argv:
            raise ValueError("No command provided.")
        if cwd is None:
            cwd = self.policy.workspace_root
        cwd_path = Path(cwd).resolve()
        allowed_roots = (self.policy.workspace_root, *tuple(self.policy.writable_roots))
        if not path_within_roots(cwd_path, allowed_roots):
            raise ValueError("Execution cwd escapes allowed workspace roots.")
        escaping = path_args_within_roots(argv, allowed_roots, cwd=cwd_path)
        if escaping is not None:
            raise ValueError(f"Path argument '{escaping}' escapes allowed workspace roots.")
        if command_uses_network(argv) and not self.policy.allow_network_egress:
            raise ValueError("Network egress is disabled by execution policy.")
        linked, scan_complete = _hard_linked_entry(allowed_roots)
        if linked is not None:
            raise HardLinkedWritableRootError(
                f"'{linked}' has more than one name on this filesystem. A hard link is a second "
                "path to the same file, and path-based kernel confinement cannot tell them "
                "apart, so a write inside the workspace could land outside it."
            )
        if not scan_complete:
            raise HardLinkedWritableRootError(
                f"the writable roots hold more than {_HARDLINK_SCAN_FILE_LIMIT} files, so they "
                "could not be checked for hard links. Refusing rather than reporting a "
                "confinement that was not verified."
            )
        argv = self._with_network_isolation(argv, allowed_roots)

        env = self._child_environment(cwd_path)
        # `start_new_session=True` puts the child in its OWN process group, which is what makes
        # the whole tree killable. `subprocess.run(timeout=…)` kills the direct child and reaps
        # it; anything that child spawned keeps running, reparented to init, with the caller
        # told the job "timed out and was terminated". A build script that backgrounds a daemon
        # or a watcher — exactly what an npm lifecycle hook does — outlived every job here.
        process = subprocess.Popen(
            argv,
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = self._wait(process, cancel_event)
        except _JobCancelledError:
            # The operator withdrew the job. Same teardown as a timeout — the whole process
            # group, children and grandchildren — and a distinct code so the receipt can say
            # "cancelled" rather than "failed".
            _terminate_process_group(process)
            partial_out, partial_err = "", ""
            with contextlib.suppress(Exception):
                partial_out, partial_err = process.communicate(timeout=5)
            note = "Execution was cancelled by the operator and the process group was terminated."
            return ExecutionResult(
                returncode=self.CANCELLED_RETURNCODE,
                stdout=_truncate(partial_out or "", self.policy.max_output_kb),
                stderr=_truncate(f"{partial_err}\n{note}".strip(), self.policy.max_output_kb),
            )
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            partial_stdout = _decode_partial(exc.stdout)
            partial_stderr = _decode_partial(exc.stderr)
            with contextlib.suppress(Exception):
                # After the group is dead the pipes drain without blocking, and whatever the
                # job managed to say before it was killed is the most useful thing in the
                # result. `communicate` on a reaped process returns immediately.
                drained_out, drained_err = process.communicate(timeout=5)
                partial_stdout = partial_stdout or (drained_out or "")
                partial_stderr = partial_stderr or (drained_err or "")
            timeout_note = f"Execution timed out after {self.policy.max_seconds}s and was terminated."
            combined_stderr = f"{partial_stderr}\n{timeout_note}".strip() if partial_stderr else timeout_note
            return ExecutionResult(
                returncode=124,
                stdout=_truncate(partial_stdout, self.policy.max_output_kb),
                stderr=_truncate(combined_stderr, self.policy.max_output_kb),
            )
        except BaseException:
            # Cancellation (KeyboardInterrupt), a caller thread dying, anything at all: the
            # process group does not outlive the call that started it.
            _terminate_process_group(process)
            raise
        finally:
            # A job that returned normally can still have left the group populated — a
            # backgrounded grandchild does not keep the parent alive. Sweeping unconditionally
            # is what makes "no orphan survives" true on the success path too.
            _terminate_process_group(process, only_if_populated=True)
        return ExecutionResult(
            returncode=int(process.returncode),
            stdout=_truncate(stdout, self.policy.max_output_kb),
            stderr=_truncate(stderr, self.policy.max_output_kb),
        )

    def _wait(self, process: subprocess.Popen, cancel_event) -> tuple[str, str]:
        """Wait for the job, honouring cancellation as promptly as the timeout.

        Without a cancel event this is `communicate(timeout=…)` unchanged. With one, the wait
        is sliced so a cancellation lands in well under a second instead of whenever the job
        happens to finish — a cancel that is only noticed at completion is not a cancel.
        """
        if cancel_event is None:
            return process.communicate(timeout=self.policy.max_seconds)
        deadline = time.monotonic() + float(self.policy.max_seconds)
        while True:
            if cancel_event.is_set():
                raise _JobCancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, self.policy.max_seconds)
            try:
                return process.communicate(timeout=min(0.2, remaining))
            except subprocess.TimeoutExpired:
                continue

    def _with_network_isolation(
        self, argv: list[str], allowed_roots: tuple[Path, ...] = ()
    ) -> list[str]:
        """Wrap ``argv`` in this host's kernel confinement. NEVER returns it unwrapped.

        The name is historical and the behaviour is not: this is the only place a confinement
        profile is ever applied, so whatever it returns is the entire boundary around a child
        process. It used to open with

            if self.policy.allow_network_egress:
                return argv

        which made "we trust this command with the network" mean "run it with no profile at
        all" — no write confinement, no read denial, no secret-directory rule. Every base
        command in `SandboxRunner._PACKAGE_INSTALL_COMMANDS` took that path, so `cargo build`
        (build.rs, proc macros), `npm run <script>` and `pip uninstall` executed arbitrary
        third-party code against the operator's home with nothing but a scan of their argv
        tokens in front of them.

        Network egress and filesystem confinement are independent controls. Trusting one has
        never been a reason to drop the other, and a host that cannot apply a profile refuses
        the job (`KernelIsolationUnavailableError`) rather than running it raw and calling it
        sandboxed.
        """
        if not allowed_roots:
            allowed_roots = (self.policy.workspace_root, *tuple(self.policy.writable_roots))
        mode = (self.policy.network_isolation_mode or "auto").strip().lower()
        if mode not in {"auto", "os_enforced", "heuristic_only"}:
            mode = "auto"
        if self.policy.allow_network_egress or mode == "heuristic_only":
            # ANVIL, 2026-08-07: this used to `return argv` unmodified -- skip the wrapper
            # entirely. On macOS and Linux+bwrap, the SAME wrapper that denies network is what
            # denies out-of-workspace file writes, so relaxing network trust for a local
            # pytest/ruff run silently dropped filesystem confinement too: a generated test
            # calling `open('/tmp/x', 'w')` wrote there with no denial and no approval prompt
            # (`sandbox.run_command`, which never gets this relaxation, correctly denied the
            # identical write). Filesystem confinement must hold regardless of network trust, so
            # this still applies whatever real backend can express "confine writes, allow
            # network" independently. It no longer falls back to the bare static argv-guard:
            # that fallback WAS the hole, and a guard that reads argument strings is not a
            # sandbox and must never be reported as one.
            confined = self._filesystem_only_isolation_prefix(argv, allowed_roots)
            if confined is not None:
                return confined
            # No backend that can express "confine writes, permit network" on this host. The
            # previous behaviour here was `return argv` — the static-guard fallback — and that
            # is precisely the shape this lane exists to delete: running raw while the caller
            # and every receipt still say "sandboxed". Refuse instead.
            raise KernelIsolationUnavailableError(self._no_kernel_isolation_message())
        isolated = self._kernel_network_isolation_prefix(argv, allowed_roots)
        if isolated is not None:
            return isolated
        # No OS-enforced backend on this host, in ANY mode. macOS has sandbox-exec and Linux
        # has bwrap/unshare/firejail, so reaching here means no kernel enforcement is possible
        # — and there is no longer a mode that answers that by running the job anyway.
        # `heuristic_only` now relaxes NETWORK trust only; it never relaxes confinement, and on
        # a host with no backend it refuses exactly like the other two.
        raise KernelIsolationUnavailableError(self._no_kernel_isolation_message())

    def _no_kernel_isolation_message(self) -> str:
        base = (
            "OS-level network isolation is required but unavailable "
            "(expected one of: bwrap, unshare, firejail on Linux; sandbox-exec on macOS). "
            "A backend may be installed and still unusable: containers and CI runners commonly "
            "ship util-linux but deny the namespace syscall, so each backend is probed for whether "
            "it actually runs, not merely for whether it is on PATH."
        )
        if os.name == "nt":
            # Native Windows has no kernel network-namespace backend, so a
            # no-network job fails closed here. Name the two real options so the
            # operator can make an informed choice instead of guessing.
            return (
                f"{base} Windows has no kernel confinement backend, so executable tools cannot "
                "run here at all: run VOOL under WSL2/Linux so bwrap/unshare/firejail provide "
                "kernel-enforced confinement. There is deliberately no override — the only thing "
                "an override could buy is running the job with no boundary while still calling "
                "it sandboxed."
            )
        return (
            f"{base} There is no override: `network_isolation_mode='heuristic_only'` relaxes "
            "NETWORK trust only and still requires a real backend for filesystem confinement."
        )

    def _kernel_network_isolation_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...]
    ) -> list[str] | None:
        # Prefer hardened Linux isolation backends when present.
        isolated = self._linux_bwrap_prefix(argv, allowed_roots)
        if isolated is not None:
            return isolated
        isolated = self._linux_unshare_prefix(argv)
        if isolated is not None:
            return isolated
        isolated = self._linux_firejail_prefix(argv)
        if isolated is not None:
            return isolated
        # macOS: real kernel-enforced network denial via Seatbelt (sandbox-exec).
        return self._macos_sandbox_exec_prefix(argv, allowed_roots)

    def _filesystem_only_isolation_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...]
    ) -> list[str] | None:
        """Confine filesystem writes to `allowed_roots` WITHOUT denying network egress.

        `unshare -n` and firejail's `--net=none` only ever express network isolation, so they
        offer nothing here -- only bwrap (`--ro-bind / /` plus selective `--bind`) and macOS
        Seatbelt can express "confine writes, permit network" independently. `None` means neither
        backend exists on this host; the caller falls back to the bare static argv-guard.
        """
        isolated = self._linux_bwrap_prefix(argv, allowed_roots, deny_network=False)
        if isolated is not None:
            return isolated
        return self._macos_sandbox_exec_prefix(argv, allowed_roots, deny_network=False)

    def _macos_sandbox_exec_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...], *, deny_network: bool = True
    ) -> list[str] | None:
        if sys.platform != "darwin":
            return None
        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            return None
        # Minimal root-independent profile: does Seatbelt run here at all. Probing with the real
        # profile would key a cached verdict to one call's roots. Network-independent -- whether
        # sandbox-exec runs at all doesn't depend on which clauses the real profile will carry.
        if not _backend_usable(
            "sandbox-exec",
            [sandbox_exec, "-p", "(version 1)(allow default)(deny network*)", "--", "true"],
        ):
            return None
        profile = _macos_confined_profile(
            allowed_roots, deny_network=deny_network, read_roots=tuple(self.policy.read_roots or ())
        )
        return [sandbox_exec, "-p", profile, "--", *list(argv)]

    def _linux_bwrap_prefix(
        self, argv: list[str], allowed_roots: tuple[Path, ...], *, deny_network: bool = True
    ) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return None
        net_flag = ["--unshare-net"] if deny_network else []
        # Probed without the per-root binds: the question is whether this host grants the
        # namespace at all, not whether one particular root exists. Keyed separately per
        # `deny_network` -- with the flag dropped, this probes plain bwrap sandboxing (no network
        # namespace required), a different capability than the deny_network=True probe.
        if not _backend_usable(
            f"bwrap:deny_network={deny_network}",
            [bwrap, *net_flag, "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        ):
            return None
        # FS isolation in addition to (optionally) the network namespace: mount the whole
        # host read-only, then re-bind only the allowed workspace roots writable,
        # plus a private tmpfs for /tmp. 'unshare -n' alone gives NO filesystem
        # isolation, so bwrap is the preferred backend when present. The `--ro-bind`/`--bind`
        # confinement below applies regardless of `deny_network` -- ANVIL, 2026-08-07: filesystem
        # confinement must hold even when network trust is relaxed for a local test/lint run.
        cmd: list[str] = [bwrap, *net_flag, "--ro-bind", "/", "/", "--tmpfs", "/tmp"]
        for root in allowed_roots:
            resolved = str(root.resolve() if isinstance(root, Path) else Path(root).resolve())
            cmd += ["--bind", resolved, resolved]
        cmd += ["--", *list(argv)]
        return cmd

    def _linux_unshare_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        unshare = shutil.which("unshare")
        if not unshare:
            return None
        if not _backend_usable("unshare", [unshare, "-n", "--", "true"]):
            return None
        return [unshare, "-n", "--", *list(argv)]

    def _linux_firejail_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        firejail = shutil.which("firejail")
        if not firejail:
            return None
        if not _backend_usable("firejail", [firejail, "--net=none", "--quiet", "--", "true"]):
            return None
        return [firejail, "--net=none", "--quiet", "--", *list(argv)]
