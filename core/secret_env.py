"""Keep keys out of every starting environment VOOL creates.

On macOS any program running as the same user can read another process's STARTING environment and
argv (sysctl KERN_PROCARGS2), and the macOS sandbox cannot stop that read (see
``core/plugin_executor.process_isolation_refusal``). Changing ``os.environ`` after a process has
started does not change what that read returns. So the only protection is that no key is ever in
the environment or argv a process is started with.

Two rules follow, and this module holds both:

* A child VOOL starts never receives a key in its environment: ``child_env()`` drops every
  secret-looking variable from the mapping the child is given. A child that needs a key gets it on
  stdin after start (the container lane and plugin handlers already do).
* VOOL's own daemon never keeps a key in its starting environment. ``shed_starting_secrets()``
  runs first thing in ``apps.vool_api_server``: when the launching environment carried a key, the
  daemon hands those values to itself over a pipe and re-executes with an environment that has none
  of them. ``handoff_spawn_kwargs()`` lets a launcher (the native window's supervisor) send keys the
  same way, so the daemon is never started with them at all.

Inside the running daemon the received values are set in ``os.environ`` again (Python's mapping
only, not the C environment), so the provider code that reads them keeps working; that in-process
copy is not part of the starting environment, a child that inherits the C environment does not get
it, and ``child_env()`` keeps it out of children given a copy of ``os.environ``. This module imports only the standard library so
it can run before anything else in the daemon.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping

HANDOFF_FD_ENV = "VOOL_SECRET_HANDOFF_FD"
_HANDOFF_LIMIT = 60_000  # below the smallest pipe buffer, so writing never blocks before exec

# Names that hold a key, token or password. Paths/modes/URLs that only mention one are kept.
_SECRET_NAME = re.compile(
    r"(API_?KEY|ACCESS_KEY|PRIVATE_KEY|SECRET|PASSWORD|PASSWD|PASSPHRASE|(^|_)TOKEN($|_)|(^|_)PAT$|_KEY$|^KEY$)"
)
_NOT_SECRET_SUFFIX = (
    "_FILE", "_PATH", "_DIR", "_MODE", "_URL", "_SOCK", "_ID", "_NAME", "_PROVIDER", "_STORAGE", "_SYMBOL", "_PER_TOKEN",
    "_LIMIT", "_BUDGET", "_ADDRESS",
)


class SecretHandoffError(RuntimeError):
    """A key could not be moved between processes without an environment. Callers stop."""


def is_secret_env_name(name: str) -> bool:
    upper = str(name or "").upper()
    if not upper or upper == HANDOFF_FD_ENV:
        return False
    if upper.endswith(_NOT_SECRET_SUFFIX):
        return False
    return bool(_SECRET_NAME.search(upper))


def split_secret_env(env: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """``(clean, secrets)``: the mapping without secret-looking names, and those names with values."""
    source = os.environ if env is None else env
    clean: dict[str, str] = {}
    found: dict[str, str] = {}
    for key, value in source.items():
        if is_secret_env_name(key):
            if value:
                found[str(key)] = str(value)
        else:
            clean[str(key)] = str(value)
    return clean, found


def child_env(base: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    """The environment for a child VOOL starts: ``base`` (default ``os.environ``) with no key in it.

    ``extra`` is applied afterwards and is for fixed, non-secret settings only.
    """
    clean, _ = split_secret_env(base)
    clean.update({k: str(v) for k, v in extra.items()})
    return clean


def secret_argv_findings(argv) -> list[str]:
    """Secret-looking ``NAME=value`` / ``--x-api-key`` items in an argv (names only, for checks)."""
    hits: list[str] = []
    for item in [str(a) for a in argv or []]:
        name = item.split("=", 1)[0].lstrip("-").replace("-", "_")
        if ("=" in item or item.startswith("--")) and is_secret_env_name(name):
            hits.append(name)
    return hits


def _write_payload(fd: int, payload: Mapping[str, str]) -> None:
    data = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
    if len(data) > _HANDOFF_LIMIT:
        raise SecretHandoffError("the keys to hand over are larger than the handoff pipe allows")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def handoff_spawn_kwargs(env: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict]:
    """For a launcher: ``(child_env, popen_kwargs)`` that move keys over a pipe instead of the environment.

    The returned environment has no key in it and names the pipe's read end; ``popen_kwargs``
    carries ``pass_fds``. Call ``close_handoff(kwargs)`` after ``Popen`` returns (or fails).
    """
    clean, found = split_secret_env(env)
    if not found:
        return clean, {}
    if os.name != "posix":
        raise SecretHandoffError("key handoff over a pipe is only available on macOS and Linux")
    read_fd, write_fd = os.pipe()
    try:
        _write_payload(write_fd, found)
    except Exception:
        os.close(read_fd)
        raise
    finally:
        os.close(write_fd)
    clean[HANDOFF_FD_ENV] = str(read_fd)
    return clean, {"pass_fds": (read_fd,)}


def close_handoff(kwargs: Mapping) -> None:
    for fd in kwargs.get("pass_fds", ()) or ():
        try:
            os.close(fd)
        except OSError:
            pass


def receive_handoff(environ=None) -> dict[str, str]:
    """In the started process: read keys a launcher sent over the pipe named by ``VOOL_SECRET_HANDOFF_FD``."""
    target = os.environ if environ is None else environ
    raw_fd = target.pop(HANDOFF_FD_ENV, None)
    if raw_fd is None:
        return {}
    try:
        fd = int(raw_fd)
    except ValueError as exc:
        raise SecretHandoffError("the key handoff pipe number is not a number") from exc
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise SecretHandoffError(f"the key handoff pipe could not be read: {exc.strerror or exc}") from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SecretHandoffError("the key handoff pipe did not carry valid data") from exc
    if not isinstance(payload, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items()):
        raise SecretHandoffError("the key handoff pipe did not carry name/value pairs")
    for key, value in payload.items():
        _set_python_only(target, key, value)
    return payload


def _set_python_only(target, key: str, value: str) -> None:
    """Make ``key`` visible to Python code (``os.environ``/``os.getenv``) without ``putenv``.

    A child started with the inherited C environment (``env=None``: git, helpers) then does not
    receive the key; children given a copy of ``os.environ`` must go through ``child_env()``.
    """
    data = getattr(target, "_data", None)
    if target is os.environ and isinstance(data, dict) and hasattr(target, "encodekey"):
        data[target.encodekey(key)] = target.encodevalue(value)
    else:
        target[key] = value


def load_provider_env_secrets(runtime_home: str | None = None) -> list[str]:
    """Read keys saved in ``<VOOL_HOME>/config/provider-env.sh`` into this process only.

    ``Start_VOOL.sh`` sources that file but no longer exports its keys, so the daemon reads them
    here. A name already set in the environment wins, as it did when the shell exported the file.
    Returns the names loaded.
    """
    home = runtime_home or os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME")
    if not home:
        return []
    from core.provider_env import load_provider_env_overrides

    loaded: list[str] = []
    for key, value in load_provider_env_overrides(home).items():
        if value and is_secret_env_name(key) and not str(os.environ.get(key) or "").strip():
            _set_python_only(os.environ, key, value)
            loaded.append(key)
    return loaded


def shed_starting_secrets(argv: list[str] | None = None) -> None:
    """First thing in the daemon: make sure no key stays in this process's starting environment.

    Receives keys a launcher sent over the handoff pipe. If keys arrived in the environment instead
    (a terminal with exported keys, a dev run), re-executes this same command with those names
    removed and the values sent over a pipe. Exits with a plain error if the handoff fails: running
    on with the keys still in the starting environment is the leak this exists to stop.
    """
    if os.name != "posix":
        return
    try:
        received = receive_handoff()
        clean, found = split_secret_env(os.environ)
        if not found:
            return
        found = {k: v for k, v in found.items() if received.get(k) != v}
        if not found:
            # Everything secret in os.environ came over the pipe; the starting environment is clean.
            return
        found.update(received)
        command = list(argv if argv is not None else getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
        exec_env, kwargs = handoff_spawn_kwargs({**clean, **found})
        for fd in kwargs.get("pass_fds", ()):
            os.set_inheritable(fd, True)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execve(sys.executable, [sys.executable, *command[1:]], exec_env)
    except SecretHandoffError as exc:
        sys.stderr.write(f"VOOL stopped: {exc}. No key was left in the starting environment.\n")
        raise SystemExit(78) from None
    except OSError as exc:
        sys.stderr.write(
            f"VOOL stopped: could not restart without the keys in its starting environment ({exc.strerror or exc}).\n"
        )
        raise SystemExit(78) from None
