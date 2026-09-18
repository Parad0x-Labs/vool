"""The Effect Broker — PLUGIN PROVIDES IMPLEMENTATION; PLATFORM PROVIDES AUTHORITY.

Geometry chosen after comparing four executions (see DESIGN Part 9):

- In-process scoped SDK handles are SECURITY THEATER: ``import socket`` bypasses any
  Python object. Falsified live in the demo.
- The smallest honest architecture is BROKERED EFFECTS: adapter code runs where it
  has NO handles at all — a child process with a cleared environment whose ONLY
  capability channel is a line-typed IPC protocol to the trusted broker. Every real
  I/O happens INSIDE the broker, which alone:
    * validates the call against the handle's declared scope,
    * binds authorization to canonical resource identity,
    * writes the effect receipt into Law 4's journal,
    * decides SUCCESS / FAILED / UNKNOWN — the adapter never authors truth.
- Handles embody authority: an ``HttpHandle('api.github.com', {'GET'},
  '/repos/acme/*')`` cannot DELETE /repos/A, cannot POST /repos/B, cannot reach
  evil.example.com — the broker refuses before touching the network.
- Revocation is immediate: a revoked handle refuses even already-granted shapes.

HONEST LIMIT (stated once, tested twice): the CHILD process is ordinary Python;
without an OS containment layer (macOS sandbox-extensions / seatbelt — broken
``sandbox-exec`` on this host — Linux namespaces+seccomp, Windows AppContainer) a
hostile adapter can still use ambient language authority (raw ``open()``,
``socket``). This module therefore guarantees the BROKERED surface completely and
reports ambient authority as PARTIAL, with the exact remaining trust named: the
process isolation primitive below the broker.
"""
from __future__ import annotations

import fnmatch
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from core.kernel.effects import (
    EffectJournal,
    EffectOutcomeUnknown,
    EffectRunner,
)

__all__ = [
    "FileReadHandle",
    "HandleRevoked",
    "HttpHandle",
    "OutsideHandleScope",
    "EffectBroker",
]


class OutsideHandleScope(RuntimeError):
    """A call outside what the HANDLE itself grants — refused before any I/O."""


class HandleRevoked(RuntimeError):
    """This handle was revoked; even previously legal shapes refuse now."""


@dataclass(frozen=True)
class FileReadHandle:
    """Read-only file access bounded by fnmatch patterns over REAL paths."""

    patterns: tuple[str, ...]
    kind: str = "file.read"

    def check(self, args: Mapping[str, object]) -> str:
        path = str(args.get("path") or "")
        if not any(fnmatch.fnmatch(path, pat) for pat in self.patterns):
            raise OutsideHandleScope(
                f"path {path!r} outside handle scope {list(self.patterns)}")
        return f"file:{path}"

    def perform(self, args: Mapping[str, object]) -> str:
        with open(str(args["path"]), encoding="utf-8", errors="replace") as handle:
            return handle.read()


@dataclass(frozen=True)
class HttpHandle:
    """Network access bounded to ONE origin, explicit methods, and a path scope."""

    origin: str                      # e.g. 'https://api.github.com'
    methods: frozenset[str]
    path_scope: tuple[str, ...]      # fnmatch over the URL path
    kind: str = "http"

    def check(self, args: Mapping[str, object]) -> str:
        url = str(args.get("url") or "")
        method = str(args.get("method") or "GET").upper()
        if not url.startswith(self.origin + "/"):
            raise OutsideHandleScope(
                f"origin {url.split('/')[2] if '://' in url else url!r} is not "
                f"{self.origin}")
        if method not in self.methods:
            raise OutsideHandleScope(
                f"method {method} not granted ({sorted(self.methods)})")
        path = "/" + url[len(self.origin):].lstrip("/")
        if not any(fnmatch.fnmatch(path.split("?")[0], pat) for pat in self.path_scope):
            raise OutsideHandleScope(f"path {path} outside scope {list(self.path_scope)}")
        return f"net:{self.origin}{path.split('?')[0]}#{method}"

    def perform(self, args: Mapping[str, object]):
        request = urllib.request.Request(
            str(args["url"]), method=str(args.get("method") or "GET").upper())
        with urllib.request.urlopen(request, timeout=10) as response:
            return {"status": response.status,
                    "body": response.read(4096).decode("utf-8", errors="replace")}


class EffectBroker:
    """Trusted side. Owns every real I/O, the journal, and the truth."""

    def __init__(self) -> None:
        self._handles: dict[str, object] = {}
        self._revoked: set[str] = set()
        self.receipts: list[dict[str, object]] = []
        self._runner = EffectRunner(mode="record")

    @property
    def journal(self) -> EffectJournal:
        return self._runner.journal

    def grant(self, handle_id: str, handle: FileReadHandle | HttpHandle) -> None:
        self._handles[handle_id] = handle

    def revoke(self, handle_id: str) -> None:
        self._revoked.add(handle_id)

    def invoke(self, handle_id: str, args: Mapping[str, object]) -> object:
        if handle_id in self._revoked:
            self.receipts.append({"handle": handle_id, "status": "denied",
                                  "reason": "revoked"})
            raise HandleRevoked(handle_id)
        handle = self._handles.get(handle_id)
        if handle is None:
            self.receipts.append({"handle": handle_id, "status": "denied",
                                  "reason": "unknown handle"})
            raise OutsideHandleScope(f"unknown handle {handle_id!r}")
        try:
            resource = handle.check(args)      # scope BEFORE any I/O
        except OutsideHandleScope as exc:
            self.receipts.append({"handle": handle_id,
                                  "resource": f"<scope-denied:{exc}>",
                                  "status": "denied"})
            raise
        try:
            result = self._runner.run(f"{handle.kind}@{resource}", handle.perform,
                                      dict(args))
        except EffectOutcomeUnknown as exc:
            self.receipts.append({"handle": handle_id, "resource": resource,
                                  "status": "attempted_unknown", "reason": exc.reason})
            raise                              # the broker NEVER invents an outcome
        except Exception as exc:
            self.receipts.append({"handle": handle_id, "resource": resource,
                                  "status": "failed", "error": type(exc).__name__})
            raise
        # THE RECEIPT IS OURS, NOT THE ADAPTER'S:
        self.receipts.append({"handle": handle_id, "resource": resource,
                              "status": "success"})
        return result


# ---------------------------------------------------------------- child-side proxy



ADAPTER_RUNTIME = r'''
"""Child-side runtime: the adapter's ONLY authority is this proxy."""
import json, sys

def make_proxy(send, recv):
    """The adapter's ONLY authority-bearing object. The raw transport functions are
    held in a closure, never stored on the object: there is no attribute, method,
    or message kind through which an adapter may set an outcome or touch the
    protocol directly. (A determined adapter can still introspect `call.__closure__`
    -- that is AMBIENT language authority, reported honestly in the demo.)"""
    def call(handle_id: str, **args):
        send({"op": "invoke", "handle": handle_id, "args": args})
        reply = recv()
        if not reply.get("ok"):
            raise RuntimeError(f"broker refused: {reply.get('error')}")
        return reply["result"]
    return _FrozenProxy(call)


class _FrozenProxy:
    """Attribute-add lock over the adapter's authority surface."""

    __slots__ = ("call",)

    def __init__(self, call):
        object.__setattr__(self, "call", call)

    def __setattr__(self, name, value):
        raise AttributeError("the adapter context is frozen")


def _main():
    lines_in = sys.stdin
    _protocol_out = sys.stdout            # the ONLY writer on the protocol channel
    sys.stdout = sys.stderr               # adapter prints can never corrupt IPC
    def send(obj): _protocol_out.write(json.dumps(obj) + "\n"); _protocol_out.flush()
    def recv():
        line = lines_in.readline()
        if not line:
            sys.exit(0)
        return json.loads(line)
    proxy = make_proxy(send, recv)
    send({"op": "ready"})
    while True:
        msg = recv()
        if msg.get("op") != "run_adapter":
            continue
        namespace: dict = {}
        exec(compile(msg["source"], "<adapter>", "exec"), namespace)
        try:
            namespace["run"](proxy)
            send({"op": "adapter_done"})
        except SystemExit:
            raise
        except Exception as exc:
            send({"op": "adapter_error", "error": f"{type(exc).__name__}: {exc}"})

if __name__ == "__main__":
    _main()
'''
