"""Run a plugin's tool as a subprocess: argument checked, environment withheld, process confined.

Wire protocol `json_stdio_v1`: one JSON object in on stdin, one JSON object out on stdout.

    in   {"intent": "...", "arguments": {...}, "context": {"scratch_dir": "...", "secrets": [...]}}
    out  {"ok": true,  "text": "...", "observation": {...}, "resolved_target": "..."}
     or  {"ok": false, "status": "invalid_arguments", "error": "..."}

An `ok:false` is fed back to the model as an observation, exactly like a built-in failure, so a
plugin can say "that project key does not exist" and the model can correct itself.

**Environment is an allowlist, not a filter.** `core/mcp_client.py` used to build a child
environment as `{**os.environ, **self.env}` — the whole environment, including whatever API keys
and tokens happen to be exported. This machine holds live keypairs, and a plugin is code a user
downloaded. Here a plugin receives only the variables its manifest names, plus the minimum needed
to start a process at all.

**Confinement is unconditional.** A minimal environment stops a child *inheriting* a secret; it
does nothing about a child *reading* one from disk, or writing wherever it likes. Every plugin
and MCP child therefore requires an isolated runtime. Immutable packages use the Linux
namespace wrapper when available. On macOS they may use a separately provisioned local Linux
container runtime with a pinned, already installed image. Package mounts are read-only, scratch
is private, process metadata is isolated, and executable network access stays off. No runtime
or image is installed automatically. If this boundary is unavailable the child is refused
(status ``confinement_unavailable``). The legacy ``heuristic_only`` option is explicit and
recorded; immutable plugin execution never uses it.

**Arguments are validated before dispatch, not after.** This is the pre-execution shape guard: a
value bound to a parameter declared `x-vool-kind: "name"` that parses as a filesystem path is
refused before the handler runs. `machine.find_folder` is the cautionary case — by the time an
answer exists to inspect, a whole-disk scan has already happened. Type-valid is not shape-valid.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import json_schema_lite

DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024

# Without these a subprocess frequently cannot start or resolve anything. Deliberately minimal:
# no HOME, no SSH_AUTH_SOCK, no cloud credential variables, nothing a keypair could hide in.
_BASE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR")

# What a `x-vool-kind` means about a value's SHAPE, as opposed to its JSON type. A path and a bare
# name are both `"type": "string"`; only this separates them.
_PATH_LIKE_PREFIXES = ("~", "/", "./", "../")

CONFINEMENT_MODES = frozenset({"auto", "heuristic_only"})


class ConfinementUnavailableError(RuntimeError):
    """This host cannot confine a child process and the caller did not opt into running bare."""


def process_isolation_refusal(*, private_environment: bool = False) -> str:
    """Why this device cannot run a plugin's own code, or "" when it can.

    On macOS any program of the same user can read another process's starting
    environment (KERN_PROCARGS2), and the macOS sandbox cannot stop it. A plugin
    whose service key would travel in its environment (``private_environment``)
    therefore runs only in the optional isolated runtime. Plugin handlers get their
    keys over their private input instead, so they need only the macOS sandbox.
    """
    if sys.platform == "darwin":
        if private_environment:
            from core.plugin_container import available

            if not available():
                return ("This plugin's server receives its service key in a way this Mac cannot keep private "
                        "from other programs, so it stays off here. Plugins that need no key, and provider and "
                        "scanner plugins, still work.")
        elif not kernel_confinement_available():
            return ("This Mac cannot run plugin code inside the macOS sandbox, so plugins that run their own "
                    "code stay off. Provider and scanner plugins still work.")
    return ""


def plugin_isolation_refusal(plugin) -> str:
    """The refusal for one loaded plugin's own code on this device, or ""."""
    servers = (plugin.vool or {}).get("mcpServers", {}) or {}
    if not plugin.contracts and not servers:
        return ""
    keyed = any(isinstance(spec, dict) and spec.get("env") for spec in servers.values())
    return process_isolation_refusal(private_environment=keyed)


@dataclass(frozen=True)
class PluginToolResult:
    ok: bool
    status: str = "executed"
    text: str = ""
    observation: dict[str, Any] = field(default_factory=dict)
    resolved_target: str = ""
    error: str = ""
    #: How the child was confined: ``kernel:<backend>`` or ``heuristic_only`` (explicit opt-in).
    #: Empty when the child never started.
    confinement: str = ""

    def as_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"observation": dict(self.observation)}
        if self.resolved_target:
            details["resolved_target"] = self.resolved_target
        if self.error:
            details["error"] = self.error
        if self.confinement:
            details["confinement"] = self.confinement
        return details


def _looks_like_a_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.startswith(_PATH_LIKE_PREFIXES) or (os.sep in text and " " not in text.strip())


def check_argument_shapes(arguments: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Refuse values whose SHAPE contradicts what the parameter is declared to mean.

    Runs before the handler. A path arriving where a bare name was declared is the whole
    `machine.find_folder` failure — the value is a perfectly valid string, so schema validation
    passes it, and the cost is paid before anything can inspect the answer.
    """

    problems: list[str] = []
    properties = (schema or {}).get("properties")
    if not isinstance(properties, dict):
        return problems
    for name in properties:
        if name not in arguments:
            continue
        kind = str(json_schema_lite.annotations(schema, name).get("kind") or "")
        value = arguments[name]
        if not isinstance(value, str) or not kind:
            continue
        if kind == "name" and _looks_like_a_path(value):
            problems.append(
                f"{name}: expected a bare name but got what looks like a path ({value!r}). "
                "If you meant that location, pass it to a tool that takes a path."
            )
        elif kind == "path" and not _looks_like_a_path(value):
            problems.append(f"{name}: expected a path but got {value!r}")
        elif kind == "url" and "://" not in value:
            problems.append(f"{name}: expected a URL but got {value!r}")
    return problems


def build_child_env(
    *,
    env_allowlist: tuple[str, ...] = (),
    secrets: dict[str, str] | None = None,
    scratch_dir: str = "",
) -> dict[str, str]:
    """Exactly what the child may see. Everything not named here is withheld."""

    child: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            child[key] = value
    child.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    for key in env_allowlist:
        value = os.environ.get(str(key))
        if value:
            child[str(key)] = value
    for key, value in (secrets or {}).items():
        if str(key) and value:
            child[str(key)] = str(value)
    if scratch_dir:
        child["PLUGIN_SCRATCH"] = scratch_dir
    return child


# ---------------------------------------------------------------------------
# Confinement
# ---------------------------------------------------------------------------


def _program_read_roots(argv: list[str]) -> tuple[Path, ...]:
    """The program a confined child was told to run, so that child can read it.

    The Seatbelt profile denies reading the CONTENTS of the operator's home and names its
    exceptions explicitly. The interpreter's own prefixes are named for it; the script or binary
    the caller chose is not. An MCP server or plugin entrypoint living anywhere else under the
    home therefore died with "Operation not permitted" before it executed a line -- the child
    could not read its own program. Only paths that already resolve are named, and never a root
    as broad as the home directory itself, so this widens nothing beyond the argv the caller
    already chose to execute.
    """

    out: list[Path] = []
    seen: set[str] = set()
    try:
        home: Path | None = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    for token in argv:
        text = str(token or "").strip()
        if not text or text.startswith("-"):
            continue
        try:
            candidate = Path(text)
            if not candidate.exists():
                located = shutil.which(text)
                if not located:
                    continue
                candidate = Path(located)
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if str(resolved) == resolved.anchor or (home is not None and resolved == home):
            continue
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return tuple(out)


def _kernel_confinement_prefix(
    argv: list[str],
    *,
    writable_roots: tuple[Path, ...],
    allow_network: bool = False,
    working_directory: Path | None = None,
    readable_roots: tuple[Path, ...] = (),
    proxy_port: int = 0,
    isolate_process_tree: bool = False,
    private_environment: bool = False,
    argument_reads: bool = True,
) -> list[str] | None:
    """Wrap *argv* in the host's kernel confinement, or None when no backend can enforce it.

    macOS uses the sandbox (Seatbelt), or the optional isolated Linux container when the
    child's environment carries a service key; Linux uses the sandbox runner's backend.
    Every backend must actually run. Reads and writes follow the captured package and
    scratch grants.
    """

    if sys.platform == "darwin" and private_environment:
        from core.plugin_container import command

        wrapped, _label = command(argv, working_directory=working_directory or Path.cwd(),
                                  writable_roots=writable_roots, readable_roots=readable_roots,
                                  allow_network=allow_network, proxy_port=proxy_port,
                                  isolate_process_tree=isolate_process_tree)
        return wrapped
    from sandbox.job_runner import JobRunner
    from sandbox.resource_limits import ExecutionPolicy, default_read_roots

    roots = tuple(Path(p).resolve() for p in writable_roots if str(p))
    # READ-ONLY CONFINEMENT (the 2026-09-02 amendment's own intent): zero writable roots is a
    # VALID policy -- a child confined to write nowhere -- not "no backend". The former
    # `return None` made every read_only plugin child refuse with confinement_unavailable on
    # hosts that can confine, so the read-only claim was never mechanically enforced. The
    # Read the actual working directory as well as the program: a handler under bin/
    # still needs its plugin's assets and directory identity. This adds no write root;
    # the kernel still denies every write for a read-only child.
    anchor = (Path(working_directory).resolve() if working_directory is not None else
              roots[0] if roots else Path(str(argv[0]) if argv else tempfile.gettempdir()).resolve().parent)
    runner = JobRunner(
        ExecutionPolicy(
            workspace_root=anchor,
            writable_roots=roots,
            allow_network_egress=False,
            network_isolation_mode="auto",
            read_roots=(*default_read_roots(), *_program_read_roots(list(argv) if argument_reads else list(argv)[:1]),
                        anchor, *readable_roots),
        )
    )
    if allow_network:
        return runner._filesystem_only_isolation_prefix(list(argv), roots)
    return runner._kernel_network_isolation_prefix(list(argv), roots)


def _backend_label(wrapped: list[str], bare: list[str]) -> str:
    if not wrapped or wrapped == bare:
        return ""
    return f"kernel:{os.path.basename(str(wrapped[0]))}"


def confined_argv(
    argv: list[str],
    *,
    writable_roots: tuple[Path, ...],
    allow_network: bool = False,
    mode: str = "auto",
    working_directory: Path | None = None,
    readable_roots: tuple[Path, ...] = (),
    proxy_port: int = 0,
    isolate_process_tree: bool = False,
    private_environment: bool = False,
    argument_reads: bool = True,
) -> tuple[list[str], str]:
    """The argv to launch and a label naming how it is confined.

    ``mode="auto"`` (default) raises `ConfinementUnavailableError` when the host cannot enforce
    confinement; ``mode="heuristic_only"`` is the explicit opt-in that runs the bare argv in that
    case and labels the result so nothing downstream can mistake it for a confined run.

    ``argument_reads=False`` is for arguments a package chose itself: only the program becomes
    readable, so a path in a manifest's ``args`` never reopens a folder the sandbox hides.
    """

    clean_mode = str(mode or "auto").strip().lower()
    if clean_mode not in CONFINEMENT_MODES:
        clean_mode = "auto"
    bare = [str(a) for a in argv]
    if private_environment and (refusal := process_isolation_refusal(private_environment=True)):
        # Seatbelt does not mediate KERN_PROCARGS2: any same-user process can read a
        # child's starting environment. A key placed there is not private, so such a
        # child starts only inside the isolated runtime.
        raise ConfinementUnavailableError(refusal)
    wrapped = _kernel_confinement_prefix(
        bare, writable_roots=writable_roots, allow_network=allow_network and not proxy_port,
        working_directory=working_directory, readable_roots=readable_roots,
        proxy_port=proxy_port, isolate_process_tree=isolate_process_tree,
        private_environment=private_environment, argument_reads=argument_reads,
    )
    from core.plugin_container import ContainerCommand

    if isinstance(wrapped, ContainerCommand):
        return wrapped, "kernel:container"
    if wrapped is not None:
        wrapped = list(wrapped)
        backend = os.path.basename(wrapped[0])
        if backend == "bwrap":
            # A PID namespace without its own /proc still exposes host environ
            # files through the read-only root mount. Install /proc last.
            wrapped[1:1] = ["--unshare-pid", "--die-with-parent", "--new-session"]
            boundary = wrapped.index("--")
            wrapped[boundary:boundary] = ["--proc", "/proc"]
        if isolate_process_tree:
            if backend == "sandbox-exec" and wrapped[1] == "-p":
                wrapped[2] += "(deny process-fork)"
            elif backend != "bwrap":
                raise ConfinementUnavailableError("This device cannot contain the plugin's child processes.")
        if proxy_port:
            if (not 1 <= proxy_port <= 65535 or len(wrapped) < 4
                    or os.path.basename(wrapped[0]) != "sandbox-exec" or wrapped[1] != "-p"):
                raise ConfinementUnavailableError("This device cannot restrict plugin network access to its private proxy.")
            wrapped = list(wrapped)
            wrapped[2] += f'(allow network-outbound (remote tcp "localhost:{proxy_port}"))'
        return list(wrapped), _backend_label(list(wrapped), bare) or "kernel:unknown"
    if clean_mode == "heuristic_only":
        return bare, "heuristic_only"
    raise ConfinementUnavailableError(
        "this host has no usable kernel confinement backend (sandbox-exec on macOS; bwrap "
        "on Linux), so the child was not started. Set confinement to 'heuristic_only' "
        "only as an explicit, informed local override."
    )


def kernel_confinement_available() -> bool:
    """Whether this host can confine a child at all (probed, cached by the sandbox backend)."""

    try:
        probe_root = Path(tempfile.gettempdir()).resolve()
        return _kernel_confinement_prefix(["true"], writable_roots=(probe_root,)) is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_plugin_tool(
    *,
    intent: str,
    arguments: dict[str, Any],
    handler: dict[str, Any],
    schema: dict[str, Any],
    plugin_root: Path,
    env_allowlist: tuple[str, ...] = (),
    secrets: dict[str, str] | None = None,
    scratch_dir: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_network: bool = False,
    confinement: str = "auto",
    read_only: bool = False,
    immutable_package: bool = False,
    package_dependencies: dict[str, Path] | None = None,
    before_start: Callable[[], None] | None = None,
    call=None,
    proxy_port: int = 0,
    isolate_process_tree: bool = False,
    extra_env: dict[str, str] | None = None,
) -> PluginToolResult:
    """Validate, then run one plugin tool. Never raises; every failure is a structured result."""

    from core.plugin_processes import InvocationStopped, OutputTooLarge

    filled = json_schema_lite.apply_defaults(arguments, schema)
    errors = json_schema_lite.validate(filled, schema)
    if errors:
        return PluginToolResult(
            ok=False, status="invalid_arguments", error="; ".join(errors), observation={"intent": intent}
        )
    shape_problems = check_argument_shapes(filled, schema)
    if shape_problems:
        return PluginToolResult(
            ok=False,
            status="invalid_argument_shape",
            error="; ".join(shape_problems),
            observation={"intent": intent},
        )

    entry = str((handler or {}).get("entry") or "").strip()
    if not entry:
        return PluginToolResult(ok=False, status="handler_missing", error="handler declares no entry")
    root = Path(plugin_root).resolve()
    executable = (root / entry).resolve()
    # A handler path must stay inside its own plugin: `entry: "../../../bin/sh"` would otherwise
    # run anything on the machine under the plugin's declared permissions.
    if not str(executable).startswith(str(root) + os.sep):
        return PluginToolResult(
            ok=False, status="handler_escaped", error=f"handler entry {entry!r} resolves outside the plugin"
        )
    if not executable.is_file():
        return PluginToolResult(ok=False, status="handler_missing", error=f"no handler at {entry}")

    # READ-ONLY CONFINEMENT (2026-09-02 amendment): a contract that claims read_only gets NO
    # writable root at all -- the kernel confinement makes the claim mechanical, and a
    # misdeclared plugin that tries to mutate local state fails inside the child instead of
    # publishing success. The executor additionally scan-checks the workspace afterwards.
    writable: list[Path] = [] if read_only or immutable_package else [root]
    if scratch_dir and not read_only:
        try:
            Path(scratch_dir).mkdir(parents=True, exist_ok=True)
            writable.append(Path(scratch_dir).resolve())
        except OSError:
            pass
    bare_argv = [str(executable), *[str(a) for a in (handler.get("args") or ())]]
    try:
        argv, confinement_label = confined_argv(
            bare_argv,
            writable_roots=tuple(writable),
            allow_network=bool(allow_network),
            mode=str(confinement or "auto"),
            working_directory=root,
            readable_roots=(*(Path(p).resolve() for p in (package_dependencies or {}).values()),
                            *((Path(scratch_dir).resolve(),) if scratch_dir else ())),
            proxy_port=proxy_port,
            isolate_process_tree=isolate_process_tree,
            argument_reads=False,
        )
    except ConfinementUnavailableError as exc:
        return PluginToolResult(
            ok=False,
            status="confinement_unavailable",
            error=str(exc),
            observation={"intent": intent},
        )

    # Keys always travel over the handler's private input. They are also placed in its
    # environment except on macOS, where any same-user program can read a starting
    # environment; there the private input is the only route.
    payload = {
        "intent": intent,
        "arguments": filled,
        "context": {"scratch_dir": scratch_dir, "secrets": sorted((secrets or {}).keys()),
                    "credentials": dict(secrets or {})},
    }
    child_env = build_child_env(env_allowlist=env_allowlist,
                                secrets={**({} if sys.platform == "darwin" else (secrets or {})), **(extra_env or {})},
                                scratch_dir=scratch_dir)
    if immutable_package:
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        child_env["VOOL_PLUGIN_DEPENDENCIES"] = json.dumps(
            {pid: str(path) for pid, path in (package_dependencies or {}).items()}, sort_keys=True
        )
    try:
        if before_start is not None:
            before_start()
        launch = dict(
            cwd=str(root), env=child_env, shell=False,
        )
        duration = float(timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        if immutable_package:
            if not math.isfinite(duration):
                duration = DEFAULT_TIMEOUT_SECONDS
            duration = min(300.0, max(1.0, duration))
            # The copy's lifetime owns the child's process group. Do not delete
            # its executable tree while descendants continue using that tree.
            from core.plugin_processes import communicate, terminate_owned

            with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  start_new_session=True, **launch) as child:
                try:
                    stdout, stderr = communicate(child, json.dumps(payload).encode(), timeout=duration, call=call)
                    completed = subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
                finally:
                    terminate_owned(child)
                    child.wait()
        else:
            completed = subprocess.run(
            argv,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=max(1.0, duration),
            **launch,
            )
    except (InvocationStopped, OutputTooLarge) as exc:
        return PluginToolResult(ok=False, status="plugin_stopped" if isinstance(exc, InvocationStopped) else "response_too_large",
                                error=str(exc), confinement=confinement_label)
    except subprocess.TimeoutExpired:
        return PluginToolResult(
            ok=False,
            status="timeout",
            error=f"handler exceeded {timeout_seconds}s",
            confinement=confinement_label,
        )
    except OSError as exc:
        return PluginToolResult(ok=False, status="handler_failed", error=str(exc), confinement=confinement_label)
    finally:
        if close := getattr(argv, "close", None):
            close()

    stdout = (completed.stdout or "")[:_MAX_OUTPUT_BYTES].strip()
    if completed.returncode != 0 and not stdout:
        stderr = (completed.stderr or "").strip()[:500]
        return PluginToolResult(
            ok=False,
            status="handler_failed",
            error=f"exit {completed.returncode}: {stderr}",
            observation={"intent": intent},
            confinement=confinement_label,
        )
    try:
        parsed = json.loads(stdout) if stdout else {}
    except ValueError:
        return PluginToolResult(
            ok=False,
            status="handler_protocol_error",
            error=f"handler did not emit one JSON object on stdout: {stdout[:200]!r}",
            confinement=confinement_label,
        )
    if not isinstance(parsed, dict):
        return PluginToolResult(
            ok=False,
            status="handler_protocol_error",
            error="handler emitted JSON that is not an object",
            confinement=confinement_label,
        )

    observation = parsed.get("observation")
    observation = dict(observation) if isinstance(observation, dict) else {}
    observation.setdefault("intent", intent)
    ok = bool(parsed.get("ok", True))
    return PluginToolResult(
        ok=ok,
        status=str(parsed.get("status") or ("executed" if ok else "failed")),
        text=str(parsed.get("text") or ""),
        observation=observation,
        resolved_target=str(parsed.get("resolved_target") or ""),
        error=str(parsed.get("error") or ""),
        confinement=confinement_label,
    )


__all__ = [
    "CONFINEMENT_MODES",
    "DEFAULT_TIMEOUT_SECONDS",
    "ConfinementUnavailableError",
    "PluginToolResult",
    "build_child_env",
    "check_argument_shapes",
    "confined_argv",
    "kernel_confinement_available",
    "run_plugin_tool",
]
