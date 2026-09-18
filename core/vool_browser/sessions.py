"""Session management: isolated disposable profiles, one owner thread per session.

A session owns:
- a fresh profile directory under this lane's scratch root (deleted on close),
- a staging directory for bounded uploads,
- a receipts file (append-only, hash-chained),
- a budget record (ops + download bytes),
- its per-origin grants,
- ONE owner thread that runs every browser-driver operation for that session.

The owner-thread design is deliberate: the browser driver's synchronous API is
single-threaded per instance, the daemon is not, and a session that died with
the daemon must still be inspectable from the registry file. Ops post a closure
to the owner thread and wait, bounded, for the result.

Restart law: the registry and receipts live on disk. After a process restart
the registry is still readable, receipts persist, and a session whose engine
process is gone reports `stale` instead of pretending to be alive.
"""
from __future__ import annotations

import contextlib
import getpass
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.vool_browser.permissions import GrantStore
from core.vool_browser.receipts import append_receipt, build_receipt

SESSION_NAME_MAX = 64
DEFAULT_OP_BUDGET = 200
DEFAULT_DOWNLOAD_BUDGET_BYTES = 100 * 1024 * 1024
_OP_WAIT_SECONDS = 90

_LOCK = threading.RLock()
_HANDLES: dict[str, SessionHandle] = {}


def scratch_root() -> Path:
    override = str(os.getenv("VOOL_BROWSER_SCRATCH_ROOT") or "").strip()
    base = Path(override) if override else Path(tempfile.gettempdir()) / f"vool-browser-{getpass.getuser()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _registry_path() -> Path:
    return scratch_root() / "registry.json"


def _load_registry() -> dict[str, dict[str, Any]]:
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_registry(registry: dict[str, dict[str, Any]]) -> None:
    path = _registry_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def valid_session_name(name: str) -> bool:
    text = str(name or "")
    return bool(text) and len(text) <= SESSION_NAME_MAX and text.rstrip("/\\").strip() == text


class OpTimeout(Exception):  # noqa: N818 - typed browser OUTCOME, house convention (see ReaderUnavailable)
    pass


class OpCancelled(Exception):  # noqa: N818 - typed browser OUTCOME, house convention (see ReaderUnavailable)
    pass


class OpFailed(Exception):  # noqa: N818 - typed browser OUTCOME, house convention (see ReaderUnavailable)
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SessionHandle:
    """One live session: its engine thread, grants, budget and receipts."""

    def __init__(self, *, session: str, engine_binary: str, headless: bool = True,
                 op_budget: int = DEFAULT_OP_BUDGET,
                 download_budget_bytes: int = DEFAULT_DOWNLOAD_BUDGET_BYTES) -> None:
        self.session = session
        self.engine_binary = engine_binary
        self.headless = headless
        self.dir = scratch_root() / "sessions" / f"{session}-{uuid.uuid4().hex[:8]}"
        self.profile_dir = self.dir / "profile"
        self.staging_dir = self.dir / "staging"
        self.receipts_path = self.dir / "receipts.jsonl"
        self.grants = GrantStore()
        self.op_budget = max(1, min(int(op_budget), 5000))
        self.ops_used = 0
        self.download_budget_bytes = max(1, int(download_budget_bytes))
        self.download_bytes_used = 0
        self.state = "opening"
        self.cancelled = False
        self.primary_origin = ""
        self.current_url = ""
        self.created_ts = time.time()
        self.recently_blocked: list[str] = []
        self._route_guard_installed = False

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue[tuple[Callable[[Any], Any], dict] | None] = queue.Queue()
        self._done = threading.Event()
        self._result: Any = None
        self._error: BaseException | None = None
        self._engine_error: str = ""
        self._pid = 0
        self._thread = threading.Thread(
            target=self._own_engine, name=f"vool-browser-{session}", daemon=True)
        self._thread.start()

    # -- owner thread -------------------------------------------------------

    def _own_engine(self) -> None:
        """The ONLY thread that touches the browser driver for this session."""

        from playwright.sync_api import sync_playwright

        from core.vool_browser.engine import isolation_launch_flags

        try:
            with sync_playwright() as driver:
                # Playwright owns the user-data-dir flag (it refuses the raw
                # argument); the profile itself is still OURS — the disposable
                # scratch dir passed as the kwarg, and the guard contract
                # (`isolation_launch_flags`) still names it.
                flags = [
                    flag for flag in isolation_launch_flags(profile_dir=str(self.profile_dir))
                    if not flag.startswith("--user-data-dir=")
                ]
                context = driver.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=self.headless,
                    executable_path=self.engine_binary,
                    args=flags,
                    accept_downloads=True,
                )
                page = context.new_page()
                self._pid = self._engine_pid(str(self.profile_dir))
                self.state = "ready"
                while True:
                    item = self._queue.get()
                    if item is None:
                        break
                    func, box = item
                    if self.cancelled and box.get("cancellable"):
                        box["error"] = OpCancelled("session cancelled")
                        box["done"].set()
                        continue
                    try:
                        box["result"] = func({"context": context, "page": page, "handle": self})
                        box["ok"] = True
                    except OpCancelled as exc:
                        box["error"] = exc
                    except OpTimeout as exc:
                        box["error"] = exc
                    except OpFailed as exc:
                        box["error"] = exc
                    except Exception as exc:  # driver or page failure
                        box["error"] = OpFailed("op_failed", str(exc)[:500])
                    box["done"].set()
                with contextlib.suppress(Exception):
                    context.close()
        except Exception as exc:
            self._engine_error = str(exc)[:400]
            self.state = "engine_failed"
            # fail every waiter so nothing blocks on a dead engine
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    _func, box = item
                    box["error"] = OpFailed("engine_missing", self._engine_error)
                    box["done"].set()

    @staticmethod
    def _engine_pid(profile_dir: str) -> int:
        """The engine process id, found by its unique profile argument.

        Deliberately not the driver's private internals: `ps` sees the real
        command line, and the profile directory is unique per session, so a
        matching pid is this session's engine and nothing else.
        """

        try:
            out = subprocess.run(
                ["/bin/ps", "-axo", "pid=,command="] if sys.platform == "darwin"
                else ["ps", "-axo", "pid=,command="],
                capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return 0
        for line in out.splitlines():
            if f"--user-data-dir={profile_dir}" in line:
                try:
                    return int(line.strip().split()[0])
                except (ValueError, IndexError):
                    return 0
        return 0

    # -- op submission ------------------------------------------------------

    def submit(self, func: Callable[[Any], Any], *, cancellable: bool = False,
               wait_seconds: float = _OP_WAIT_SECONDS) -> Any:
        box: dict[str, Any] = {"done": threading.Event(), "ok": False,
                               "result": None, "error": None, "cancellable": cancellable}
        self._queue.put((func, box))
        if not box["done"].wait(wait_seconds):
            raise OpTimeout(f"operation did not finish within {wait_seconds:.0f}s")
        if box["error"] is not None:
            raise box["error"]
        return box["result"]

    def spend_op(self) -> None:
        with _LOCK:
            if self.ops_used >= self.op_budget:
                raise OpFailed(
                    "budget_exhausted",
                    f"session op budget exhausted ({self.op_budget} operations); "
                    "close the session or open a new one with a higher explicit budget")
            self.ops_used += 1

    def spend_download(self, size: int) -> None:
        with _LOCK:
            if self.download_bytes_used + size > self.download_budget_bytes:
                raise OpFailed(
                    "budget_exhausted",
                    f"download budget exhausted ({self.download_budget_bytes} bytes per session)")
            self.download_bytes_used += size

    def touch(self, url: str = "") -> None:
        if url:
            from core.vool_browser.permissions import origin_of

            self.current_url = url
            origin = origin_of(url)
            if origin:
                self.primary_origin = self.primary_origin or origin

    # -- lifecycle ----------------------------------------------------------

    def close(self, *, cancelled: bool = False) -> None:
        self.cancelled = self.cancelled or cancelled
        if not cancelled:
            self.state = "closing"
        if cancelled and self._pid > 0:
            # Cancellation must be REAL: stop the engine process so in-flight
            # navigation fails immediately instead of waiting out its timeout.
            import signal

            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.kill(self._pid, sig)
                except OSError:
                    break
                try:
                    if self._thread.is_alive():
                        self._thread.join(timeout=3)
                except RuntimeError:
                    break
                if self._thread is not threading.current_thread() and not self._thread.is_alive():
                    break
        if self._thread is not threading.current_thread():
            self._queue.put(None)
            self._thread.join(timeout=20)
        if cancelled:
            self.state = "cancelled"
        else:
            self.state = "closed"
        _purge_profile(self.profile_dir)

    def pid(self) -> int:
        return self._pid

    def describe(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "state": self.state,
            "profile_dir": str(self.profile_dir),
            "staging_dir": str(self.staging_dir),
            "receipts_path": str(self.receipts_path),
            "engine": self.engine_binary,
            "headless": self.headless,
            "pid": self._pid,
            "grants": self.grants.snapshot(),
            "budget": {
                "op_budget": self.op_budget,
                "ops_used": self.ops_used,
                "download_budget_bytes": self.download_budget_bytes,
                "download_bytes_used": self.download_bytes_used,
            },
            "primary_origin": self.primary_origin,
            "current_url": self.current_url,
            "created_ts": self.created_ts,
        }

    def registry_record(self) -> dict[str, Any]:
        record = self.describe()
        record["engine_error"] = self._engine_error
        return record


def _purge_profile(profile_dir: Path) -> None:
    import shutil

    try:
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Registry-facing API (the only surface ops.py and api.py need)
# ---------------------------------------------------------------------------


def open_session(*, session: str, start_url: str, headless: bool = True,
                 op_budget: int = DEFAULT_OP_BUDGET,
                 download_budget_bytes: int = DEFAULT_DOWNLOAD_BUDGET_BYTES) -> SessionHandle:
    from core.vool_browser.engine import find_engine_binary

    if not valid_session_name(session):
        raise OpFailed("invalid_arguments", f"session must be a bare name (<= {SESSION_NAME_MAX} chars)")
    with _LOCK:
        if session in _HANDLES and _HANDLES[session].state not in {"closed", "cancelled", "engine_failed"}:
            raise OpFailed("session_exists", f"session {session!r} is already open")
        engine = find_engine_binary()
        if not engine:
            raise OpFailed(
                "engine_missing",
                "no Chromium-family browser is available on this machine; "
                "the browser lane installs nothing")
        handle = SessionHandle(
            session=session, engine_binary=engine, headless=headless,
            op_budget=op_budget, download_budget_bytes=download_budget_bytes)
        _HANDLES[session] = handle
        _sync_registry_entry(handle)
        return handle


def get_handle(session: str) -> SessionHandle | None:
    with _LOCK:
        return _HANDLES.get(session)


def record_receipt(handle: SessionHandle, **receipt_kwargs: Any) -> dict[str, Any]:
    receipt = build_receipt(session=handle.session, **receipt_kwargs)
    append_receipt(handle.receipts_path, receipt)
    _sync_registry_entry(handle)
    return receipt


def _sync_registry_entry(handle: SessionHandle) -> None:
    with _LOCK:
        registry = _load_registry()
        registry[handle.session] = handle.registry_record()
        _save_registry(registry)


def registry_status(session: str) -> dict[str, Any] | None:
    """Registry truth for a session, honest about engine death after a restart.

    A CLOSED session is gone: it answers unknown_session, because a disposable
    profile that no longer exists must not be describable as a live thing.
    """

    handle = get_handle(session)
    if handle is not None:
        if handle.state in {"closed", "closing"}:
            return None
        return handle.registry_record()
    registry = _load_registry()
    record = registry.get(session)
    if not record:
        return None
    state = record.get("state")
    if state in {"closed"}:
        return None
    if state in {"open", "ready", "opening", "navigated"}:
        pid = int(record.get("pid") or 0)
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        record = dict(record)
        record["state"] = "stale" if not alive else "recoverable"
    return record


def close_session(session: str, *, cancelled: bool = False) -> bool:
    handle = get_handle(session)
    if handle is not None:
        handle.close(cancelled=cancelled)
        _sync_registry_entry(handle)
        return True
    # No live handle (e.g. after a process restart): purge what the registry
    # still names, but only if it lives under THIS lane's scratch root.
    registry = _load_registry()
    record = registry.get(session)
    if not record:
        return False
    profile = Path(str(record.get("profile_dir") or ""))
    scratch = scratch_root().resolve()
    try:
        if profile.exists() and scratch in profile.resolve().parents:
            _purge_profile(profile)
    except OSError:
        pass
    record = dict(record)
    record["state"] = "cancelled" if cancelled else "closed"
    registry[session] = record
    _save_registry(registry)
    return True


def close_all_for_test() -> None:
    with _LOCK:
        for handle in list(_HANDLES.values()):
            with contextlib.suppress(Exception):
                handle.close()
        _HANDLES.clear()
