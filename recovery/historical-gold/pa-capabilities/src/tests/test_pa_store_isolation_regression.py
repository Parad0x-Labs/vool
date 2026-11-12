"""PA store-isolation regression (incident 2026-09-09 ~23:33–23:41).

A PA integration-test fixture temporarily called
``configure_runtime_continuity_db_path(None)``, clearing the conftest's
continuity binding. The investigation established that every write still landed
in disposable stores (the frozen-sentinel re-route in ``storage.db`` funnels
the default path through the conftest's per-process test DB under pytest, and
through the ``NULLA_HOME``-derived data dir outside it); the real user DB was
unreachable and untouched. The fixture was fixed before commit ``fb28047f``.

This regression pins BOTH halves so the class cannot silently return:
dynamic — the stores a PA test actually writes resolve to disposable paths and
an emitted PA event lands in exactly that disposable DB; static — no PA test
module clears the continuity binding, and every PA fixture/e2e binds
``NULLA_HOME`` to a disposable home.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import runtime_paths

PA_TEST_MODULES = (
    "tests/test_pa_tasks_reminders.py",
    "tests/test_pa_journeys.py",
    "tests/test_pa_reminder_runtime_integration.py",
    "tests/test_pa_reminder_daemon_e2e.py",
    "tests/test_email_send_ledger.py",
    "tests/test_email_approval_gate.py",
    "tests/test_calendar_timezone.py",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _real_user_store_candidates() -> list[Path]:
    """The installed-runtime user home (read-only discovery; nothing is opened)."""
    try:
        from core.runtime_paths import active_nulla_home

        saved_override = runtime_paths._NULLA_HOME_OVERRIDE
        saved_env = dict(__import__("os").environ)
        try:
            runtime_paths._NULLA_HOME_OVERRIDE = None
            __import__("os").environ.pop("NULLA_HOME", None)
            home = active_nulla_home()
        finally:
            runtime_paths._NULLA_HOME_OVERRIDE = saved_override
            __import__("os").environ.clear()
            __import__("os").environ.update(saved_env)
        return [home, (home / "data" / "nulla_web0_v2.db")]
    except Exception:
        return []


# -- dynamic: the stores a PA test writes are disposable, and writes land there --

def test_active_stores_resolve_to_disposable_paths(tmp_path, monkeypatch) -> None:
    """The exact PA fixture shape (NULLA_HOME -> tmp) plus the conftest bindings
    in force during any test: every store the PA lane writes — the default
    runtime DB and the runtime-continuity events DB — must resolve under a
    disposable root, never the real user home or the repo tree."""
    import os

    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)

    import storage.db as storage_db
    from core import runtime_continuity

    def _resolved(path: str) -> str:
        return str(storage_db._resolve_db_path(path))

    # What the conftest binds for every test (the per-process disposable test DB).
    default_db = _resolved(storage_db.DEFAULT_DB_PATH)
    continuity_raw = runtime_continuity._runtime_db_path()
    continuity_db = _resolved(str(continuity_raw))

    temp_root = Path(str(Path(os.environ.get("NULLA_HOME") or tmp_path))).anchor
    del temp_root  # anchor is just "/"; explicit checks below are stronger.
    real_root = Path.home() / "Library" / "Application Support" / "NULLA"
    repo_root = _repo_root()

    for label, db in (("default", default_db), ("continuity", continuity_db)):
        db_path = Path(db)
        assert real_root not in db_path.parents, (
            f"{label} store resolves into the REAL user home: {db}"
        )
        assert repo_root not in db_path.parents or ".nulla_local" not in db, (
            f"{label} store resolves into the repo tree: {db}"
        )
        # Disposable means: the OS temp root or the test's own NULLA_HOME. Resolve
        # the temp root: on macOS gettempdir() is the symlinked /var/folders/...
        # while resolved DB paths carry the /private prefix.
        import tempfile

        disposable_roots = [Path(tempfile.gettempdir()).resolve(), Path(str(tmp_path)).resolve()]
        assert any(root in db_path.parents for root in disposable_roots), (
            f"{label} store is not under a disposable root: {db}"
        )


def test_emitted_pa_event_lands_in_the_disposable_store(tmp_path, monkeypatch) -> None:
    """Bind-then-write proof: emit one PA notification through the production
    notify path and assert the row exists in the SAME disposable DB the
    resolver named (and nowhere else it could have gone)."""
    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)

    import sqlite3

    import storage.db as storage_db
    from core import pa_reminder_runtime, runtime_continuity

    db = str(storage_db._resolve_db_path(str(runtime_continuity._runtime_db_path())))
    pa_reminder_runtime.notify_runtime_event({
        "reminder_id": "isolation-regression-1",
        "task_id": "",
        "message": "store isolation regression probe",
        "due_at_utc": "2026-09-10T00:00:00+00:00",
        "status": "",
        "possible_duplicate": False,
        "delivery_lineage": "initial",
    })
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM runtime_session_events "
            "WHERE session_id = ? AND details_json LIKE ?",
            (pa_reminder_runtime.REMINDER_SESSION_ID, "%isolation-regression-1%"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, f"PA event did not land in the resolved disposable store ({db})"


# -- static: the incident's shape cannot return through PA test modules ----------

def test_no_pa_test_module_clears_the_continuity_binding() -> None:
    """The incident's exact call — configure_runtime_continuity_db_path(None) —
    must not appear in any PA test module. The root conftest owns that binding."""
    for rel in PA_TEST_MODULES:
        src = (_repo_root() / rel).read_text(encoding="utf-8")
        assert "configure_runtime_continuity_db_path(None)" not in src, (
            f"{rel} clears the continuity store binding (isolation incident class)"
        )
        assert "configure_runtime_continuity_db_path(" not in re.sub(
            r"#.*", "", src
        ), f"{rel} reconfigures the continuity binding outside comments — conftest owns it"


#: Modules that write runtime stores (directly or through the engine/runtime).
_STORE_TOUCHING = ("pa_reminders", "pa_tasks", "pa_reminder_runtime",
                   "storage.db", "get_connection", "email_tools", "runtime_continuity")
#: Imports that would make a module store-touching even without the names above.
_STORE_IMPORTS = ("from storage", "import storage", "from core.pa_", "core.email_tools")


@pytest.mark.parametrize("rel", PA_TEST_MODULES)
def test_every_pa_test_module_binds_a_disposable_home(rel: str) -> None:
    """Every STORE-WRITING PA test module must set NULLA_HOME (monkeypatch/env)
    so runtime state lands in a disposable home before any write; a pure module
    (e.g. the calendar parser suite) must stay store-free instead."""
    src = (_repo_root() / rel).read_text(encoding="utf-8")
    store_touching = any(marker in src for marker in _STORE_TOUCHING + _STORE_IMPORTS)
    binds_home = 'NULLA_HOME"' in src or "'NULLA_HOME'" in src
    if store_touching:
        assert binds_home, (
            f"{rel} writes runtime stores but never binds NULLA_HOME — "
            "its stores are not provably disposable"
        )
    else:
        assert not binds_home or True  # a pure module MAY still bind a home; no requirement
        assert "configure_runtime_continuity_db_path" not in src


def test_daemon_e2e_boots_the_real_server_in_its_own_home() -> None:
    """The daemon e2e must pass NULLA_HOME of a tmp home to the real server
    process, so even the daemon's own writes are disposable."""
    src = (_repo_root() / "tests/test_pa_reminder_daemon_e2e.py").read_text(encoding="utf-8")
    assert '"NULLA_HOME": str(home)' in src
    assert 'tmp_path / "daemon-home"' in src
