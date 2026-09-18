from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    """What one execution is allowed to do. Two independent axes, never one boolean.

    ``allow_network_egress`` is a NETWORK decision and nothing else. It used to be read, one
    layer down, as permission to skip confinement entirely — the whole defect this policy's
    docstring now exists to prevent. Filesystem confinement is not a policy field at all: it
    is unconditional, and a host that cannot enforce it refuses the job.

    ``read_roots`` are paths outside the task root that the job may READ. They exist because
    an interpreter has to load its own stdlib: denying the operator's home wholesale would
    also deny a Python living in ``~/vool/.venv``. Naming them makes the exception explicit
    and auditable instead of implicit in an ``(allow default)`` profile.
    """

    workspace_root: Path
    writable_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_seconds: int = 120
    max_output_kb: int = 256
    max_memory_mb: int = 512
    allow_network_egress: bool = False
    #: auto | os_enforced | heuristic_only. NETWORK trust only — none of the three can
    #: relax filesystem confinement, and all three refuse on a host with no backend.
    network_isolation_mode: str = "auto"
    backend: str = "subprocess"
    #: Read-only roots outside the writable set. Interpreter/toolchain prefixes only.
    read_roots: tuple[Path, ...] = field(default_factory=tuple)


def _default_read_roots() -> tuple[Path, ...]:
    """The prefixes an interpreter or toolchain must read to start at all.

    Derived from this process, not hard-coded: the Python that will run a child is the one
    running now, and its prefix is where its stdlib lives. A repo `.venv` under the operator's
    home is the normal case here, which is exactly why the home deny-rule needs a named
    exception rather than being dropped.
    """
    import sys
    import sysconfig

    roots: list[Path] = []
    for candidate in (
        sys.prefix,
        sys.base_prefix,
        sysconfig.get_paths().get("stdlib"),
        sysconfig.get_paths().get("purelib"),
        os.path.dirname(sys.executable or ""),
    ):
        text = str(candidate or "").strip()
        if text:
            roots.append(Path(text))
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key not in seen and resolved.exists():
            seen.add(key)
            out.append(resolved)
    return tuple(out)


def default_read_roots() -> tuple[Path, ...]:
    """Public name for the interpreter prefixes every confined child needs to start.

    Callers that declare their own `read_roots` REPLACE the defaults (see `normalize_policy`),
    so anything adding a root must be able to name the defaults it is adding to.
    """

    return _default_read_roots()


def normalize_policy(policy: ExecutionPolicy) -> ExecutionPolicy:
    writable_roots = tuple(Path(path).resolve() for path in (policy.writable_roots or (policy.workspace_root,)))
    declared_reads = tuple(Path(path).resolve() for path in (policy.read_roots or ()))
    return ExecutionPolicy(
        workspace_root=policy.workspace_root.resolve(),
        writable_roots=writable_roots,
        max_seconds=int(policy.max_seconds),
        max_output_kb=int(policy.max_output_kb),
        max_memory_mb=int(policy.max_memory_mb),
        allow_network_egress=bool(policy.allow_network_egress),
        network_isolation_mode=str(policy.network_isolation_mode or "auto"),
        backend=policy.backend,
        read_roots=declared_reads or _default_read_roots(),
    )


def path_within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def _looks_like_path_arg(token: str) -> bool:
    """A token is treated as a filesystem path only when it clearly is one.

    We deliberately avoid flagging flags ("-c", "--prefix"), inline code/URLs,
    or bare relative names so that legitimate commands are not rejected. The
    escape vectors we must catch are (1) absolute paths and (2) relative paths
    that climb out of the cwd via ".." segments.
    """
    value = str(token or "")
    if not value:
        return False
    if value.startswith("-"):
        return False
    if value.startswith(("/", "~", "./", "../", ".\\", "..\\")) or value.startswith("\\"):
        return True
    if value[1:3] == ":\\" or value[1:3] == ":/":  # Windows drive paths, e.g. C:\
        return True
    # A relative token that escapes upward (e.g. "a/../../etc/passwd").
    parts = value.replace("\\", "/").split("/")
    return ".." in parts


def path_args_within_roots(argv: list[str], roots: tuple[Path, ...], *, cwd: Path) -> str | None:
    """Validate that every path-like argument resolves inside ``roots``.

    Returns ``None`` when all path-like arguments stay within the allowed roots,
    otherwise the offending raw token (so the caller can build an error). This is
    an OS-independent backstop in front of (not a replacement for) kernel
    sandboxing: it blocks reads/writes of absolute paths outside the workspace
    even on hosts with no Seatbelt/bwrap enforcement.
    """
    cwd_resolved = Path(cwd).resolve()
    for token in argv[1:]:
        if not _looks_like_path_arg(token):
            continue
        raw = str(token)
        expanded = Path(raw).expanduser()
        if expanded.is_absolute():
            candidate = expanded
        else:
            candidate = cwd_resolved / expanded
        if not path_within_roots(candidate, roots):
            return raw
    return None
