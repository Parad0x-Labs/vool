from __future__ import annotations

import contextlib
import functools
import logging
import sqlite3
import time
from pathlib import Path

from core.runtime_paths import active_data_dir

logger = logging.getLogger("vool.storage.db")

# Keep the public default path constant import-safe. The real default connection
# target is resolved via active runtime home unless an explicit override is set.
DEFAULT_DB_PATH = str(((Path(__file__).resolve().parents[1] / ".vool_local" / "data") / "vool_web0_v2.db").resolve())
_DEFAULT_DB_PATH_OVERRIDE: str | None = None

# The frozen sentinel, canonicalized ONCE. It is compared against, never materialized: its parent
# is the source root, which is read-only in a packaged .app and must stay byte-identical.
_FROZEN_DEFAULT_DB_PATH = str(Path(DEFAULT_DB_PATH).expanduser().resolve())


@functools.lru_cache(maxsize=256)
def _canonical_db_path_cached(db_path_str: str) -> str:
    """Pure canonicalization (expanduser + resolve) — no filesystem side effects."""
    return str(Path(db_path_str).expanduser().resolve())


@functools.lru_cache(maxsize=256)
def _resolve_db_path_cached(db_path_str: str) -> str:
    """Canonicalize a CONCRETE database path and ensure its parent exists.

    Pure in its input string: the sentinel never reaches here (see _resolve_db_path), so no
    runtime state — active home, default override — is folded into a memo keyed on the string.
    That fold is exactly what made an explicit DEFAULT_DB_PATH stick to the first home resolved
    in a process and ignore the active override (pre-demo candidate blocker, 2026-09-02).
    """
    path = Path(db_path_str).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _resolve_db_path(db_path: str | Path) -> str:
    # get_connection() calls this multiple times per invocation (including on its cached-connection
    # fast path), so on Windows this was issuing a real GetFinalPathNameByHandleW syscall (via
    # Path.resolve()) thousands of times across a full test run - which produced intermittent
    # access-violation crashes under sustained load. The resolution is idempotent for a given
    # input string within a process lifetime, so cache it.
    #
    # Fail loudly on a non-path. Passing a CONNECTION where a path belongs (e.g.
    # `run_migrations(db_path=conn)`) used to stringify the object and silently create a real
    # database named "<storage.db._PooledConnection object at 0x...>". Two such files were committed
    # to the repo, and because angle brackets are illegal on NTFS they broke every native Windows
    # checkout. A wrong type is a caller bug: raise it here instead of materialising garbage on disk.
    if not isinstance(db_path, (str, Path)):
        raise TypeError(
            f"db_path must be a str or Path, got {type(db_path).__name__}. "
            "Passing a connection here silently creates a database named after the object."
        )
    text = str(db_path).strip()
    if not text or text.startswith("<"):
        raise ValueError(f"db_path is not a usable filesystem path: {text[:80]!r}")
    if _canonical_db_path_cached(text) == _FROZEN_DEFAULT_DB_PATH:
        # The frozen import-time sentinel was passed EXPLICITLY (importers hold the constant).
        # It means "the active default database": the configured override first, else the
        # runtime home's file — resolved LIVE on every call, so an explicit sentinel and an
        # implicit default always open the SAME file, and a home switch is never shadowed by a
        # stale memo. The sentinel's own parent is never created.
        text = _DEFAULT_DB_PATH_OVERRIDE or _runtime_default_db_path()
    return _resolve_db_path_cached(text)


def resolve_runtime_db_filename(data_dir: Path) -> Path:
    """Canonical ``vool_web0_v2.db``, or the pre-rename ``nulla_web0_v2.db`` when only that
    file exists in ``data_dir`` (legacy data is reused, never orphaned by a second database)."""
    canonical = data_dir / "vool_web0_v2.db"
    legacy = data_dir / "nulla_web0_v2.db"
    if not canonical.exists() and legacy.exists():
        return legacy.resolve()
    return canonical.resolve()


def _runtime_default_db_path() -> str:
    return str(resolve_runtime_db_filename(active_data_dir()))


# Opening N connections at once against a database that is not yet in WAL mode makes every one
# of them attempt the journal-mode conversion. That conversion needs exclusive access to the
# database and — unlike an ordinary write — SQLite answers it with SQLITE_BUSY *immediately*
# rather than routing it through the busy handler, so the `timeout=` passed to connect() does
# not cover it. The loser threads therefore raised OperationalError("database is locked") out
# of get_connection() before any caller had begun a transaction. Measured on a cold database:
# 25 concurrent spend reservations lost 1-2 of them to this, with the spend caps nowhere near
# binding. Retrying the pragma briefly is enough, because the conversion is one-time: once any
# connection has completed it, every later `PRAGMA journal_mode=WAL` is a no-op that cannot
# block.
# K-03 store version gate (canonical contract Pass 001). The main DB opens only
# when PRAGMA application_id + user_version match this binary's contract.
# Downgrade refuse-open: a database stamped by a NEWER contract version is never
# opened by an older binary (no best-effort read path). A 0/0 stamp means a
# fresh or pre-gate legacy database — allowed, so run_migrations() can bootstrap
# and stamp it; every later open sees the real values and enforces them.
# Version OWNERSHIP: the number below is the head of storage.migrations.STORE_VERSION_LEDGER.
# The migration authority asserts the two agree when it imports (VOOL_E_STORE_VERSION_OWNERSHIP);
# a gated migration pass without a ledger entry, or a bump on one side only, refuses to load.
STORE_APPLICATION_ID = 0x4E5A4131  # 'NZA1'
STORE_USER_VERSION = 8


class StoreVersionError(RuntimeError):
    """Refused-open: the database does not match this binary's store contract.

    ``code`` is the stable VOOL error code surfaces should report:
    ``VOOL_E_STORE_TOO_NEW`` for a downgrade refusal, ``VOOL_E_STORE_VERSION`` for an
    unreadable/mismatched stamp.
    """

    def __init__(self, message: str, *, code: str = "VOOL_E_STORE_VERSION") -> None:
        super().__init__(message)
        self.code = code


def enforce_store_version_gate(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute("PRAGMA application_id;").fetchone()
        app_id = int(row[0]) if row is not None else 0
        row = conn.execute("PRAGMA user_version;").fetchone()
        user_version = int(row[0]) if row is not None else 0
    except sqlite3.DatabaseError as exc:
        raise StoreVersionError(f"store version unreadable: {exc}") from exc
    if app_id == 0 and user_version == 0:
        return  # fresh or pre-gate legacy DB; run_migrations() stamps it
    if app_id != STORE_APPLICATION_ID:
        raise StoreVersionError(
            f"refusing to open database: application_id {app_id:#x} != expected "
            f"{STORE_APPLICATION_ID:#x}",
            code="VOOL_E_STORE_VERSION",
        )
    if user_version > STORE_USER_VERSION:
        raise StoreVersionError(
            f"downgrade refused: database user_version {user_version} > binary's "
            f"{STORE_USER_VERSION}",
            code="VOOL_E_STORE_TOO_NEW",
        )


_WAL_RETRIES = 12
_WAL_BACKOFF_SECONDS = 0.005
_WAL_BACKOFF_CAP_SECONDS = 0.05


def _enable_wal(conn: sqlite3.Connection) -> bool:
    """Put the connection's database into WAL mode, tolerating a concurrent converter.

    Returns True once the database reports WAL. Fail-soft by design: a database that is still
    in its old journal mode is slower under concurrency but not less correct — transaction
    semantics (including BEGIN IMMEDIATE, which is what the spend ledger serializes on) hold in
    every journal mode. Refusing to hand back a working connection would turn a transient
    startup race into a hard failure for the caller, which is what this exists to stop.
    """
    delay = _WAL_BACKOFF_SECONDS
    for attempt in range(_WAL_RETRIES):
        try:
            # Read first: this never needs the exclusive lock, so on an already-converted
            # database (the overwhelmingly common case) it settles without a write attempt.
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            if row is not None and str(row[0]).lower() == "wal":
                return True
            row = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            if row is not None and str(row[0]).lower() == "wal":
                return True
        except sqlite3.OperationalError as exc:
            # Only contention is worth waiting out. Anything else (a read-only or unreadable
            # database, an I/O error) will not resolve itself, so surface it now rather than
            # spending the whole retry budget first.
            if not any(word in str(exc).lower() for word in ("lock", "busy")):
                logger.warning("could not switch SQLite to WAL mode (%s); continuing", exc)
                return False
        if attempt < _WAL_RETRIES - 1:
            time.sleep(delay)
            delay = min(delay * 2, _WAL_BACKOFF_CAP_SECONDS)
    logger.warning("could not switch SQLite to WAL mode; continuing in the existing journal mode")
    return False


def _make_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a fresh SQLite connection with WAL mode and safe defaults.

    A failed open closes its own connection before raising: a leaked half-open connection
    would keep fds (and the store generation it probed) alive for the caller's traceback
    lifetime, which is how one poisoned open makes the next one fail too.
    """
    conn = sqlite3.connect(_resolve_db_path(db_path), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        _enable_wal(conn)
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        enforce_store_version_gate(conn)
    except BaseException:
        with contextlib.suppress(Exception):
            conn.close()
        raise
    return conn


# --- Per-use connection contract (P0 chat-lifecycle repair, 2026-09-04) -----------------------
# This module hands out a FRESH connection per get_connection() call and closes it when the
# caller closes it. It deliberately does NOT cache connections across calls, and that is the
# load-bearing decision, not an omission. Measured failure it prevents (checkpoint D,
# 2026-09-03; reproduced end-to-end on this composition): the previous thread-local pool kept
# WAL-mode connections attached across operations, while short-lived helper processes (seed
# scripts, CLI tools, test rigs) legitimately checkpoint + DELETE the -wal/-shm at their own
# last close. SQLite's guard for that is per-process fcntl state, which a multithreaded
# churning daemon cannot keep intact (every sibling fd close erodes the process's shm locks),
# so helpers pulled live WAL generations out from under the daemon's idle pooled connections:
# committed bytes landed in an unlinked WAL no other process could read, and every fresh open
# then failed process-wide with sqlite3.OperationalError("disk I/O error") — surfacing at
# PRAGMA synchronous=NORMAL in _make_connection because _enable_wal's deliberate fail-soft
# swallows the first hit — until the process restarted.
#
# With per-use connections there is no idle attached state to expose: SQLite only deletes the
# sidecars when it can prove it is the system-wide last connection (checkpointing first, so
# committed writes survive into the .db file), and while THIS process is mid-operation its
# connection's locks are live and block that proof. A deletion between operations lands in a
# healthy state and the next open recovers cleanly; a deleted-under-you connection cannot
# exist. Committed writes stay externally visible at every moment.


def reset_default_connection() -> None:
    """No-op retained for API compatibility.

    Connections are per-use since the 2026-09-04 storage contract repair: nothing is cached,
    so there is no default connection to drop. Every call site that used this to shed cached
    state (isolation fixtures, fact_extractor) is simply done.
    """
    return


def configure_default_db_path(db_path: str | Path | None) -> None:
    global _DEFAULT_DB_PATH_OVERRIDE
    if db_path is not None and _canonical_db_path_cached(str(db_path).strip()) == _FROZEN_DEFAULT_DB_PATH:
        db_path = None  # the sentinel names "the default": configuring it IS clearing the override
    _DEFAULT_DB_PATH_OVERRIDE = None if db_path is None else _resolve_db_path(db_path)


def active_default_db_path() -> str:
    return _DEFAULT_DB_PATH_OVERRIDE or _resolve_db_path(_runtime_default_db_path())


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Return a fresh SQLite connection to the store at ``db_path``.

    ``db_path=None`` (the default) resolves at CALL time via active_default_db_path() — never
    the import-time DEFAULT_DB_PATH constant: a Python default binds that source-root path at
    DEFINITION time, and materializing it crashed read-only .app boots and put the database
    inside the bundle on writable installs.

    The caller owns the returned connection's lifecycle (``close()`` really closes) and its
    transactions (commit before the writes must be visible to anyone else).
    """
    if db_path is None:
        db_path = active_default_db_path()
    return _make_connection(db_path)


def execute_query(query: str, params: tuple = (), db_path: str | Path | None = None) -> list:
    """Run a parameterized query and close the connection immediately after use."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if query.strip().upper().startswith("SELECT"):
            return [dict(row) for row in cursor.fetchall()]
        conn.commit()
        return [{"status": "success", "lastrowid": cursor.lastrowid}]
    finally:
        conn.close()


def init_schema(db_path: str | Path | None = None):
    """
    Initialize the exact V2 SQLite schema defined in the Reference Architecture.
    """
    from storage.migrations import SCHEMA_SQL

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        conn.commit()
        return
    finally:
        conn.close()


def healthcheck(db_path: str | Path | None = None) -> bool:
    try:
        conn = get_connection(db_path)
        try:
            conn.execute("SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        return False
