"""Start `apps.vool_api_server` with the dispatch tracer installed. TEST INFRASTRUCTURE ONLY.

    python -m tests.trace_daemon_launch --port 11455 --jsonl /path/to/trace.jsonl

Run it with `-m` from the worktree root, never as a bare script path. Two separate traps make that
mandatory, and both were reproduced on this machine rather than assumed:

**Trap 1 -- `sys.path[0]` is the SCRIPT's directory, not the cwd.** The runner venv is
editable-installed against a DIFFERENT checkout, so a launcher stored anywhere other than the
worktree root leaves the editable finder to resolve `core`, and it resolves to that other checkout
at a different SHA. Measured: the same file run from a scratchpad directory imported
`vool-convergence-c1/core/__init__.py`; run as `-m` from the worktree it imports
`wt-obligation-floor/core/__init__.py`. `core/retrieval_constraints.py` genuinely differs between
them, so the wrong one silently traces different code. `_assert_frozen_worktree` below turns that
into a loud, immediate exit instead of a plausible-looking trace of the wrong SHA.

**Trap 2 -- `python -m apps.vool_api_server` would make the tracer record nothing.** `-m` executes
the target as `__main__`, so `sys.modules["__main__"]` and `sys.modules["apps.vool_api_server"]`
are two distinct module objects with two distinct copies of every top-level import. The tracer
patches `apps.vool_api_server.dispatch_post` (that module holds its own module-top-level copy, so
patching `core.web.api.service` alone is blind to it) -- and the `__main__` copy, which is the one
that actually serves, would keep the unwrapped binding. Wrappers would install "successfully",
report zero failures, and log nothing. Importing the server as an ordinary module and calling
`main()` from here means there is exactly ONE `apps.vool_api_server`, so what is patched is what
runs.

Ordering matters too: the tracer is installed AFTER `apps.vool_api_server` is imported, so every
module that holds a load-time-bound copy already exists and gets its copy wrapped.
"""
from __future__ import annotations

import argparse
import os
import sys

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assert_frozen_worktree() -> None:
    """Fail loudly, before anything else runs, if `core`/`apps` came from another checkout."""
    if sys.path[0] != WORKTREE:
        sys.path.insert(0, WORKTREE)
    import apps
    import core

    for module in (core, apps):
        path = getattr(module, "__file__", "") or ""
        if not path.startswith(WORKTREE + os.sep):
            raise SystemExit(
                f"FATAL: {module.__name__} resolved to {path!r}, not the frozen worktree "
                f"{WORKTREE!r}. Refusing to trace the wrong checkout."
            )
    print(f"[trace-daemon] core.__file__ = {core.__file__}", flush=True)
    print(f"[trace-daemon] apps.__file__ = {apps.__file__}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="trace-daemon-launch")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--jsonl", required=True)
    args = parser.parse_args()

    _assert_frozen_worktree()

    # Import the server as an ordinary module FIRST (see Trap 2), then wrap.
    import apps.vool_api_server as server
    from tests.claim_dispatch_tracer import DispatchTracer

    tracer = DispatchTracer(jsonl_path=args.jsonl)
    tracer.install()
    if tracer.install_failures:
        for entry in tracer.install_failures:
            print(f"[trace-daemon] INSTALL FAILURE {entry}", flush=True)
        raise SystemExit("FATAL: tracer failed to install on at least one registry target.")
    print(
        f"[trace-daemon] tracer installed on {len(tracer._restores)} bindings "
        f"across {len(tracer.registry)} registry symbols -> {args.jsonl}",
        flush=True,
    )
    # Proof the wrapper is on the object the SERVED path uses, not on a second copy.
    print(
        f"[trace-daemon] apps.vool_api_server.dispatch_post wrapped: "
        f"{getattr(server.dispatch_post, '__name__', '') == 'wrapper'}",
        flush=True,
    )

    sys.argv = [
        "vool-api-server",
        "--port",
        str(args.port),
        "--bind",
        str(args.bind),
    ]
    try:
        return int(server.main() or 0)
    finally:
        tracer.uninstall()


if __name__ == "__main__":
    raise SystemExit(main())
