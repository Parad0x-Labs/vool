"""Cross-process file-lock order: a recorder for child processes and the checker for its trace.

The lock-graph review plugin follows the locks of ONE process. Two processes share no Python lock,
only files: the forget authority's ledger lock (`<home>/.forget-authority/lock`, `fcntl.flock` or
`msvcrt.locking` through `revocation._flock`) and its admission leases
(`<home>/.forget-authority/lease-*`). The memory and forget paths take no other file lock (SQLite's
own locks, which time out instead of waiting forever, are followed separately below).

`install()` runs inside a child process, before the path under test. It wraps `revocation._flock` /
`_funlock` and the documented module locks, and writes one JSON line per acquisition to the child's
own trace file: what was taken, whether the call could wait, and what the thread already held.
`violations()` reads every child's trace and checks it against the documented order:

    forget fence (`revocation._LOCK` + the ledger flock)
      -> chat_attachments._LOCK -> finalization.A8_TRAVERSAL_LOCK -> entries._MEMORY_ENTRY_LOCK
      -> files._JSONL_LOCK

(A8 before the entry lock: the derivative sweep holds A8 across its store steps and its memory
step takes the entry lock, `_run_derivative_sweep` -> `sweep_governed_memory_rows`; nothing takes
them the other way round.)

with several ledger flocks (homes) taken in sorted path order (`begin_group`,
`_command_erasure_fence`, `revoked_tokens`), and leases private to the process that created them
(arming is a wait on a file created a moment ago with `open('x')`; every other process only tries
them, `LOCK_NB`). It also joins every child's waits into one graph (file locks shared, module locks
per process) and reports any cycle: an order inversion across processes.
"""
from __future__ import annotations

import collections
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
import weakref
from pathlib import Path

#: The documented order. A lock may wait for a lock to its right, never to its left.
ORDER = (
    "fence",
    "chat_attachments._LOCK",
    "finalization.A8_TRAVERSAL_LOCK",
    "entries._MEMORY_ENTRY_LOCK",
    "files._JSONL_LOCK",
)
MODULE_LOCKS = (
    ("core.memory.revocation", "_LOCK", "fence"),
    ("core.chat_attachments", "_LOCK", "chat_attachments._LOCK"),
    ("core.memory.entries", "_MEMORY_ENTRY_LOCK", "entries._MEMORY_ENTRY_LOCK"),
    ("core.finalization", "A8_TRAVERSAL_LOCK", "finalization.A8_TRAVERSAL_LOCK"),
    ("core.memory.files", "_JSONL_LOCK", "files._JSONL_LOCK"),
)

_local = threading.local()
_out = {"fd": None, "pid": 0}
_connections: dict[int, tuple[weakref.ref, int]] = {}
_connections_lock = threading.Lock()


def _held() -> collections.OrderedDict:
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = collections.OrderedDict()
    return held


def file_lock_name(path) -> str:
    path = Path(path)
    if path.parent.name == ".forget-authority":
        if path.name == "lock":
            return "ledger:" + str(path.parent.parent)
        if path.name.startswith("lease-"):
            return "lease:" + str(path)
    return "file:" + str(path)


def _sqlite_write_open() -> bool:
    """Whether this thread has a SQLite transaction open (a write began and is not committed)."""
    me = threading.get_ident()
    with _connections_lock:
        refs = list(_connections.values())
    for ref, owner in refs:
        conn = ref()
        if conn is None or owner != me:
            continue
        try:
            if conn.in_transaction:
                return True
        except sqlite3.ProgrammingError:  # closed
            continue
    return False


def _where() -> list[str]:
    frames = [f for f in traceback.extract_stack()[:-3] if "/core/" in f.filename or "/storage/" in f.filename]
    return [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames[-6:]]


def _emit(lock: str, *, blocking: bool, busy: bool = False) -> None:
    fd = _out["fd"]
    if fd is None:
        return
    held = [name for name in _held() if name != lock]
    if not held and not lock.startswith(("ledger:", "lease:")):
        return
    row = {"pid": _out["pid"], "thread": threading.current_thread().name, "lock": lock, "blocking": blocking,
           "held": held, "sqlite_write_open": _sqlite_write_open(), "where": _where(), "t": time.time()}
    if busy:
        row["busy"] = True
    os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))


def note_waiting(lock: str) -> None:
    """Before a call that can wait: the wait is recorded even when it never ends (a deadlock)."""
    if lock not in _held():
        _emit(lock, blocking=True)


def note_acquired(lock: str, *, blocking: bool) -> None:
    held = _held()
    if not blocking and lock not in held:
        _emit(lock, blocking=False)
    if lock.startswith("lease:"):
        return  # a lease is let go by closing it (`_disarm`, `_clean_exit`), never by `_funlock`: not followed
    held[lock] = held.get(lock, 0) + 1


def note_released(lock: str) -> None:
    held = _held()
    if lock in held:
        held[lock] -= 1
        if held[lock] <= 0:
            del held[lock]


class Tracked:
    """A module lock that reports its acquisitions; behaves as the lock it wraps."""

    def __init__(self, inner, name):
        self._inner, self._name = inner, name

    def acquire(self, blocking=True, timeout=-1):
        waits = bool(blocking) and timeout == -1
        if waits:
            note_waiting(self._name)
            # A plain wait goes through the lock's own no-argument acquire: v0.8's A8 traversal gate
            # (finalization._A8TraversalGate, a writer-reentrant read/write gate) takes no arguments,
            # blocks until it is held and returns None.
            ok = self._inner.acquire()
            ok = True if ok is None else ok
        else:
            ok = self._inner.acquire(blocking, timeout)
        if ok:
            note_acquired(self._name, blocking=waits)
        return ok

    def release(self):
        note_released(self._name)
        self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def locked(self):
        probe = getattr(self._inner, "locked", None)
        if probe is not None:
            return probe()
        if self._inner.acquire(False):
            self._inner.release()
            return False
        return True

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _TrackedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with _connections_lock:
            _connections[id(self)] = (weakref.ref(self), threading.get_ident())


def install(trace_path, *, flock_hook=None) -> None:
    """Record this process's lock acquisitions to `trace_path`. `flock_hook(name, phase)` runs around
    each ledger/lease flock (`phase` 'before' or 'after'), for tests that widen a window."""
    import importlib

    _out["fd"] = os.open(str(trace_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    _out["pid"] = os.getpid()
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs.setdefault("factory", _TrackedConnection)
        return real_connect(*args, **kwargs)

    sqlite3.connect = connect
    for module, attr, name in MODULE_LOCKS:
        mod = importlib.import_module(module)
        current = getattr(mod, attr)
        if not isinstance(current, Tracked):
            setattr(mod, attr, Tracked(current, name))
    revocation = importlib.import_module("core.memory.revocation")
    real_flock, real_funlock = revocation._flock, revocation._funlock

    def _flock(handle, *, blocking=True):
        name = file_lock_name(handle.name)
        if flock_hook is not None:
            flock_hook(name, "before")
        if blocking:
            note_waiting(name)
        try:
            real_flock(handle, blocking=blocking)
        except BlockingIOError:
            _emit(name, blocking=False, busy=True)  # a try that found it held: no wait
            raise
        note_acquired(name, blocking=blocking)
        if flock_hook is not None:
            flock_hook(name, "after")

    def _funlock(handle):
        note_released(file_lock_name(handle.name))
        real_funlock(handle)

    revocation._flock, revocation._funlock = _flock, _funlock


def read_traces(paths) -> list[dict]:
    rows = []
    for path in paths:
        path = Path(path)
        if path.exists():
            rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _kind(name: str) -> str:
    return name.split(":", 1)[0] if name.startswith(("ledger:", "lease:", "file:")) else "module"


def _rank(name: str) -> int:
    """The fence (its process lock and every ledger flock) is 0; module locks follow in `ORDER`."""
    if name.startswith("file:"):
        return len(ORDER)  # an unknown file lock: anything taken under it is reported
    return 0 if name.startswith("ledger:") else ORDER.index(name)


def violations(rows, *, sqlite_rule: bool = True) -> list[str]:
    """Every wait in `rows` that breaks the documented order, then every cross-process cycle."""
    found: list[str] = []
    lease_owner: dict[str, int] = {}
    for row in rows:
        lock, held, pid = row["lock"], row["held"], row["pid"]
        where = " <- ".join(reversed(row.get("where") or []))
        if not row["blocking"]:
            continue  # a try never waits: it adds no edge
        if lock.startswith("lease:"):
            owner = lease_owner.setdefault(lock, pid)
            if owner != pid:
                found.append(f"pid {pid} waited for another process's lease {lock} ({where})")
            home = "ledger:" + str(Path(lock[len("lease:"):]).parent.parent)
            if home not in held:
                found.append(f"pid {pid} armed {lock} outside its home's fence, holding {held} ({where})")
            continue
        for prior in held:
            if prior.startswith("lease:"):
                continue  # private to this process: nobody else ever waits for it
            if lock.startswith("ledger:") and prior.startswith("ledger:"):
                if not Path(prior[len("ledger:"):]) < Path(lock[len("ledger:"):]):
                    found.append(f"pid {pid} took {lock} while holding {prior}: homes out of sorted order ({where})")
            elif _rank(prior) > _rank(lock) or (_rank(prior) == _rank(lock) == 0 and _kind(prior) == "module"
                                                and _kind(lock) == "module"):
                found.append(f"pid {pid} took {lock} while holding {prior}: against the documented order ({where})")
        if sqlite_rule and lock.startswith("ledger:") and row.get("sqlite_write_open"):
            found.append(f"pid {pid} waited for {lock} with a SQLite write transaction open ({where})")
    found.extend(cycles(rows))
    return found


def cycles(rows) -> list[str]:
    """Cycles in the union of every process's waits. File locks are shared; module locks are per process."""
    def node(name, pid):
        return name if name.startswith(("ledger:", "lease:", "file:")) else f"{name}@{pid}"

    graph: dict[str, set[str]] = collections.defaultdict(set)
    for row in rows:
        if not row["blocking"] or row["lock"].startswith("lease:"):
            continue  # a try never waits; arming waits only for a file just created (`violations` checks that)
        target = node(row["lock"], row["pid"])
        for prior in row["held"]:
            source = node(prior, row["pid"])
            if source != target:
                graph[source].add(target)
    found, seen = [], set()
    for start in sorted(graph):
        stack, path, on_path = [(start, iter(sorted(graph[start])))], [start], {start}
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                on_path.discard(path.pop())
                continue
            if child in on_path:
                loop = [*path[path.index(child):], child]
                key = frozenset(loop)
                if key not in seen:
                    seen.add(key)
                    found.append("cycle across processes: " + " -> ".join(loop))
                continue
            if child not in graph:
                continue
            stack.append((child, iter(sorted(graph[child]))))
            path.append(child)
            on_path.add(child)
    return found


def child_environment(home: str) -> None:
    """The storage a test session's conftest gives each test, rebuilt in a plain child process."""
    import socket

    real_connect = socket.socket.connect

    def sealed(sock, address):  # the session's network seal does not reach a child: refuse here too
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            print(f"NETWORK REFUSED {address!r}", file=sys.stderr, flush=True)
            raise OSError("network is sealed in lock-order child processes")
        return real_connect(sock, address)

    socket.socket.connect = sealed
    os.environ.update(VOOL_HOME=home, VOOL_WORKSPACE_ROOT=os.path.join(home, "workspace"))
    Path(home).mkdir(parents=True, exist_ok=True)
    from core import embedding_service
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    embedding_service._best_embed_model = lambda: None
    db = os.path.join(home, "vool.db")
    configure_default_db_path(db)
    reset_default_connection()
    configure_runtime_continuity_db_path(db)
    run_migrations()


def _png() -> bytes:
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00" * 5)) + chunk(b"IEND", b"")


def _ingest(home: str, chat: str, text: str) -> None:
    from tests.test_question_date_time_leg_20261002 import _ingest as ingest
    from tests.test_question_date_time_leg_20261002 import _ts

    ingest(Path(home), chat, [(_ts("2026-03-04T09:00:00"), text)])


def _forget(home: str, chat: str, value: str) -> str:
    from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command

    return _forget_command(Path(home), chat, f"Forget {value}")[1]


def setup_role(spec: dict) -> dict:
    """Data for every path: chats to forget in, delete and upload to, and finalizations to erase."""
    from core.memory import entries
    from tests.foundation.test_a8_privacy_pass004 import _finalize

    home, n = spec["runtime_home"], spec["n"]
    tag = spec.get("tag", "x")
    data: dict = {"forget": [], "chat_delete": [], "finalization_erase": [], "upload": [], "append": []}
    for i in range(n):
        chat, value = f"openclaw:{tag}forget{i:011d}", f"QZRT{i:02d}-{4100 + i}"
        _ingest(home, chat, f"The locker code is {value}.")
        data["forget"].append([chat, value])
        gone = f"openclaw:{tag}delete{i:011d}"
        _ingest(home, gone, f"The shed key {i} hangs by the door.")
        entries._ensure_memory_files()
        with open(entries.conversation_log_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_id": gone, "request_id": f"req-{tag}-d{i}", "user": "Where is the key?",
                                     "assistant": f"Answer {i} about the shed."}) + "\n")
        data["chat_delete"].append(gone)
        fin = _finalize(f"Answer {i} about the north gate.", request_id=f"req-{tag}-f{i}", turn_id=f"t-{tag}-{i}")
        data["finalization_erase"].append(fin["finalization_id"])
        data["upload"].append([f"openclaw:{tag}upload{i:011d}", f"Shelf list {i}.\nNothing forgotten here.\n"])
        data["append"].append([f"openclaw:{tag}append{i:011d}", f"The boiler {i} is serviced in May."])
    return data


def run_role(role: str, items: list, spec: dict) -> list:
    """One path, once per item. Returns what each call answered."""
    from core import chat_attachments as ca
    from core import finalization as fz
    from core.memory import entries, revocation

    home = spec["runtime_home"]
    out = []
    for item in items:
        if role == "forget":
            out.append(_forget(home, item[0], item[1]))
        elif role == "chat_delete":
            out.append(bool(entries.delete_conversation_session(item)))
        elif role == "finalization_erase":
            out.append(bool(fz.erase_finalization_payload(item).get("sweep_complete")))
        elif role == "upload":
            chat, text = item
            doc = ca.stage_document(session_id=chat, data=text.encode(), declared_name="shelf.txt")
            image = ca.stage_attachment(session_id=chat, declared_name="dot.png", declared_type="image/png", data=_png())
            ca.bind_to_turn(session_id=chat, turn_id="turn-1", attachment_ids=[doc["id"], image["id"]])
            try:
                served = ca.model_attachments_for_turn(session_id=chat, turn_id="turn-1", question="what is in it?")
            finally:
                ca.release_turn(session_id=chat, turn_id="turn-1", outcomes=None)
            out.append(len(served))
        elif role == "append":
            _ingest(home, item[0], item[1])
            out.append(True)
        elif role == "inverted_fence":  # the order this checker guards, broken on purpose (test-only)
            with entries._MEMORY_ENTRY_LOCK, revocation.serialized():
                out.append(True)
        elif role == "inverted_sqlite":  # a SQLite write left open while the fence is awaited (test-only)
            conn = sqlite3.connect(os.path.join(spec["home"], "inverted.db"))
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
                conn.commit()
                conn.execute("INSERT INTO t VALUES (1)")
                with revocation.serialized():
                    out.append(conn.in_transaction)
                conn.rollback()
            finally:
                conn.close()
        elif role in {"ordered_homes", "inverted_homes"}:  # the second on purpose out of sorted order (test-only)
            first, second = sorted(item, reverse=role == "inverted_homes")
            with revocation.serialized(first), revocation.serialized(second):
                out.append(True)
        elif role == "two_home_read":  # a serving read under both homes' fences (`revoked_tokens`)
            chat, other_home = item
            out.append(len(revocation.revoked_tokens(chat, other_home)))
        else:
            raise ValueError(role)
    return out


def child_hook(spec: dict):
    """The flock hook a child's spec asks for, or None.

    `hold_first_flock_s` holds the first ledger flock of a nesting that long, to widen the window
    before a second one. `barrier` (names of the children that take part, `barrier_dir` beside them)
    makes each of them, once it holds its first ledger flock, wait until every one of them holds
    its own before it goes on: two inverted helpers then deadlock however late either one started."""
    hold = float(spec.get("hold_first_flock_s") or 0)
    peers, folder = spec.get("barrier") or [], Path(spec.get("barrier_dir") or ".")
    if not hold and not peers:
        return None

    def hook(name, phase):
        if phase != "after" or not name.startswith("ledger:"):
            return
        if [h for h in _held() if h.startswith("ledger:") and h != name]:
            return  # not the first ledger flock of this nesting
        if peers:
            (folder / f"{spec['name']}.first").write_text("held", encoding="utf-8")
            deadline = time.monotonic() + 120
            while not all((folder / f"{peer}.first").exists() for peer in peers) and time.monotonic() < deadline:
                time.sleep(0.01)
        if hold:
            time.sleep(hold)
    return hook


def child_main(run=None, setup_fn=None) -> None:
    """`python -c 'from tests._file_lock_order import child_main; child_main()' SPEC.json`

    `run(role, items, spec)` and `setup_fn(spec)` replace `run_role` / `setup_role` for a module
    that adds paths of its own (`tests/_file_lock_coverage.py`)."""
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    child_environment(spec["home"])
    hook = child_hook(spec)
    if spec["role"] != "setup":
        install(spec["trace"], flock_hook=hook)
    if spec.get("ready"):
        Path(spec["ready"]).write_text("ready", encoding="utf-8")
    if spec.get("go"):
        deadline = time.monotonic() + 120
        while not Path(spec["go"]).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    if spec["role"] == "setup":
        result = (setup_fn or setup_role)(spec)
    else:
        result = (run or run_role)(spec["role"], spec["items"], spec)
    print("RESULT " + json.dumps(result), flush=True)


ROOT = Path(__file__).resolve().parents[1]


def spawn(work: Path, name: str, spec: dict, *, entry: str = "tests._file_lock_order"):
    """One child process running `spec`; its trace is `<work>/<name>.trace`. `entry` is the module
    whose `child_main()` the child runs."""
    import subprocess

    spec = {"trace": str(work / f"{name}.trace"), "name": name, **spec}
    path = work / f"{name}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen([sys.executable, "-c", f"from {entry} import child_main; child_main()",
                             str(path)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(children: dict, deadline_s: float, *, stderr: dict | None = None) -> tuple[dict, list[str], dict]:
    """Wait for every child against one deadline. A child still running then is killed (it is ours) and
    named in `hung`. Returns (results by name, hung names, stderr tails). `stderr`, when given, gets
    every finished child's whole stderr."""
    import subprocess

    end = time.monotonic() + deadline_s
    results, hung, errors = {}, [], {}
    for name, child in children.items():
        try:
            out, err = child.communicate(timeout=max(0.1, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            hung.append(name)
            continue
        if stderr is not None:
            stderr[name] = err
        line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
        if line is None:
            errors[name] = f"exit {child.returncode}: {err[-3000:]}"
        else:
            results[name] = json.loads(line[len("RESULT "):])
    for name in hung:
        children[name].kill()
        children[name].communicate()
    return results, hung, errors


def hang_report(work: Path, names) -> str:
    """For a hang: what the hung children's traces say, so the failure names the locks, not only the
    children. A wait that never ended was written before it started, so it is in the trace."""
    rows = read_traces([work / f"{name}.trace" for name in names])
    found = violations(rows)
    last = {}
    for row in rows:
        if row["blocking"]:
            last[row["pid"]] = row
    waits = [f"pid {row['pid']} last waited for {row['lock']} holding {row['held']} "
             f"({' <- '.join(reversed(row.get('where') or []))})" for row in last.values()]
    return "\n".join(["order checker on the traces:", *(found or ["(no violation recorded)"]),
                      "last recorded wait of each child:", *(waits or ["(none)"])])


def await_nested_waits(work: Path, names, timeout_s: float) -> bool:
    """Until every named child's trace shows it waiting for a ledger flock while it holds another:
    True once they all do, False at the timeout."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if all(any(r["blocking"] and r["lock"].startswith("ledger:") and any(h.startswith("ledger:") for h in r["held"])
                   for r in read_traces([work / f"{name}.trace"])) for name in names):
            return True
        time.sleep(0.05)
    return False


def go(work: Path, children: dict, timeout_s: float = 120.0) -> None:
    """Release every child at once, after each has set up its storage."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if all((work / f"{name}.ready").exists() or child.poll() is not None for name, child in children.items()):
            break
        time.sleep(0.02)
    (work / "go").write_text("go", encoding="utf-8")


def setup(work: Path, name: str, *, home: str, runtime_home: str, n: int, tag: str, deadline_s: float = 300.0,
          entry: str = "tests._file_lock_order") -> dict:
    results, hung, errors = finish({name: spawn(work, name, {"role": "setup", "home": home, "runtime_home": runtime_home,
                                                            "n": n, "tag": tag}, entry=entry)}, deadline_s)
    assert not hung and not errors, (hung, errors)
    return results[name]


def worker(work: Path, name: str, role: str, items: list, *, home: str, runtime_home: str,
           entry: str = "tests._file_lock_order", **extra):
    return spawn(work, name, {"role": role, "items": items, "home": home, "runtime_home": runtime_home,
                              "ready": str(work / f"{name}.ready"), "go": str(work / "go"), **extra}, entry=entry)


if __name__ == "__main__":  # pragma: no cover - debugging aid: print a trace's violations
    print("\n".join(violations(read_traces(sys.argv[1:]))) or "clean")
