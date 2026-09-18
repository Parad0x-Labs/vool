"""FINAL VERIFICATION AMENDMENT — value fidelity + atomic preimages.

Two independently measured defects at d6f4dfe2, pinned here RED before the fix:

1. VALUE-BLIND VERIFICATION. `_state_fingerprint` compared sqlite_master rows and
   per-table COUNT(*) — never cell values. Two databases holding [(1,)] and
   [(999,)] compared EQUAL, and that predicate is what backed
   `rolled_back_verified` and `restored_verified`. Verification must match the
   stated claim: schema plus complete typed content (NULL/BLOB/duplicates
   included), read as a consistent snapshot with bounded work; when a full read
   cannot complete, the answer is explicit unknown — never "verified". Rollback
   EXECUTION status and verification STRENGTH stay separate facts.
2. NON-ATOMIC BACKUP PUBLICATION. `_take_backup` checked `exists(target)` and
   then opened SQLite at the target — a window in which two concurrent writers
   to the same database+backup name BOTH passed the check and BOTH published
   (last writer wins, first preimage silently lost). Exactly one publisher per
   name; no overwrite; no exposed partial snapshot; a failed publication cleans
   up after itself and never deletes an existing winner; automatic names are
   unique by CONSTRUCTION (atomic claim), not by UUID probability.

The predicate tests exec the handler's policy section directly (the same
layer-pin pattern as the authorizer test); the atomicity tests race the REAL
confined executor from two threads at once.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import sqlite3
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, internal_scope, reset_toolchain_state
from tests._vool_database_pack import DB_PLUGIN_ID, databases_root, isolated_db_world


@pytest.fixture()
def db_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    isolated_db_world(tmp_path, monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        from core import plugin_tools

        _loaded, errors = plugin_tools.load_all(tmp_path)
        assert errors == (), errors
        yield tmp_path
    reset_mode_permission_state()
    reset_toolchain_state()


# ---------------------------------------------------------------------------
# The handler's policy core, loaded for predicate-level pins
# ---------------------------------------------------------------------------

_POLICY_MODULE = None


def _policy():
    global _POLICY_MODULE
    if _POLICY_MODULE is not None:
        return _POLICY_MODULE
    import types

    handler = Path("plugins/vool-database/bin/db_handler").read_text(encoding="utf-8")
    cut = handler.index("def tool_connect")
    module = types.ModuleType("db_handler_policy_probe")
    exec(compile(handler[:cut], "plugins/vool-database/bin/db_handler", "exec"), module.__dict__)
    _POLICY_MODULE = module
    return module


def _mem_db_with(*rows_sql: str) -> sqlite3.Connection:
    # Bare columns carry BLOB affinity: SQLite stores each value AS GIVEN, so
    # type distinctions (int vs float vs text vs blob vs NULL) survive storage
    # and the fingerprint is tested on real distinctions, not affinity rules.
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None  # the fingerprint opens its own read transaction
    conn.execute("CREATE TABLE t (a, b, c, d)")
    for sql in rows_sql:
        conn.execute(sql)
    return conn


# ---------------------------------------------------------------------------
# Defect 1: value fidelity
# ---------------------------------------------------------------------------


def test_fingerprint_distinguishes_identical_schema_and_counts_with_different_values() -> None:
    """The exact review counterexample: [(1, ...)] vs [(999, ...)]."""
    left = _mem_db_with("INSERT INTO t VALUES (1, 'x', x'01', 1.5)")
    right = _mem_db_with("INSERT INTO t VALUES (999, 'x', x'01', 1.5)")
    try:
        assert _policy()._content_fingerprint(left) != _policy()._content_fingerprint(right)
    finally:
        left.close()
        right.close()


def test_fingerprint_is_null_blob_type_and_duplicate_faithful() -> None:
    policy = _policy()
    # NULL vs empty string vs zero-length blob are three different values.
    a = _mem_db_with("INSERT INTO t VALUES (NULL, NULL, NULL, NULL)")
    b = _mem_db_with("INSERT INTO t VALUES (0, '', x'', 0.0)")
    # int vs float vs text that print alike are different values.
    c = _mem_db_with("INSERT INTO t VALUES (1, '1', x'31', 1.0)")
    d = _mem_db_with("INSERT INTO t VALUES (1, '1', x'31', 1)")
    # duplicates are content: one row is not two.
    e = _mem_db_with("INSERT INTO t VALUES (7, 's', x'0a', 2.5)")
    f = _mem_db_with("INSERT INTO t VALUES (7, 's', x'0a', 2.5)", "INSERT INTO t VALUES (7, 's', x'0a', 2.5)")
    try:
        assert policy._content_fingerprint(a) != policy._content_fingerprint(b)
        assert policy._content_fingerprint(c) != policy._content_fingerprint(d)
        assert policy._content_fingerprint(e) != policy._content_fingerprint(f)
        # and equal content still compares equal
        g = _mem_db_with("INSERT INTO t VALUES (7, 's', x'0a', 2.5)")
        assert policy._content_fingerprint(e) == policy._content_fingerprint(g)
    finally:
        for conn in (a, b, c, d, e, f):
            conn.close()


def test_fingerprint_treats_row_order_as_layout_not_content() -> None:
    """A rebuild/VACUUM can change physical row order; logical content is equal.

    Amendment 2026-09-03 (value coverage): the fixtures originally left the
    inserts uncommitted, so BOTH fingerprints were None and the equality passed
    vacuously. The fixtures commit first, and both digests must be non-None
    before equality is asserted -- genuine unavailable verification stays
    unknown and never feeds an equality claim.
    """
    left = sqlite3.connect(":memory:")
    right = sqlite3.connect(":memory:")
    for conn in (left, right):
        conn.isolation_level = None  # autocommit: the inserts COMMIT
        conn.execute("CREATE TABLE t (a INTEGER)")
    left.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    right.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)][::-1])
    try:
        policy = _policy()
        left_digest = policy._content_fingerprint(left)
        right_digest = policy._content_fingerprint(right)
        assert left_digest is not None, "committed fixture must be readable"
        assert right_digest is not None, "committed fixture must be readable"
        assert left_digest == right_digest
    finally:
        left.close()
        right.close()


def test_unbounded_or_busy_verification_reports_unknown_not_verified() -> None:
    """A read that cannot finish in budget is an explicit unknown, never a pass."""
    policy = _policy()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (a)")
        # A budget of zero seconds cannot complete any read: unknown.
        assert policy._content_fingerprint(conn, budget_seconds=0) is None
    finally:
        conn.close()


def test_rollback_receipt_separates_execution_from_verification_strength(db_world) -> None:
    """rolled_back=True (the ROLLBACK ran) is a different fact from HOW we know
    the state survived — the receipt names both."""
    from core.mode_permission_policy import resolve_approval
    from core.tool_intent_executor import execute_tool_intent

    def run(intent, arguments, session="f", **ctx):
        return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **ctx))

    made = run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": "fid", "schema_sql": "CREATE TABLE t (n INTEGER)", "seed_sql": "INSERT INTO t VALUES (1)"},
        **internal_scope("fid.create", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",)),
    )
    assert made.ok, made.response_text[:200]
    pending = run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "fid", "statements": ["UPDATE t SET n = 999", "INSERT INTO missing VALUES (1)"]})
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    failed = run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "fid", "statements": ["UPDATE t SET n = 999", "INSERT INTO missing VALUES (1)"]},
        mode_approval_token=token,
    )
    assert failed.status == "apply_failed_rolled_back"
    observation = failed.details["observation"]
    assert observation["rollback_executed"] is True
    assert observation["rollback_verification"] == "verified"
    assert observation["outcome"] == "rolled_back_verified"
    # The VALUE the migration tried to change is what verification saw restored.
    conn = sqlite3.connect(str(databases_root(db_world) / "fid.sqlite"))
    try:
        assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Defect 3 (value coverage): generated columns and projection alignment
# ---------------------------------------------------------------------------


def _generated_db(kind: str, position: str, a: int, z: int) -> sqlite3.Connection:
    """A disposable DB with one generated column at first/middle/last position.

    `kind` is VIRTUAL or STORED. The schema projection (PRAGMA table_info)
    OMITS the generated column while SELECT * INCLUDES it -- the exact split
    that made the fingerprint mislabel or truncate cells.
    """
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None
    column = "g INTEGER GENERATED ALWAYS AS (a*2) " + kind
    if position == "first":
        conn.execute("CREATE TABLE t (" + column + ", a INTEGER, z INTEGER)")
    elif position == "middle":
        conn.execute("CREATE TABLE t (a INTEGER, " + column + ", z INTEGER)")
    else:
        conn.execute("CREATE TABLE t (a INTEGER, z INTEGER, " + column + ")")
    conn.execute("INSERT INTO t (a, z) VALUES (?, ?)", (a, z))
    return conn


@pytest.mark.parametrize("kind", ["VIRTUAL", "STORED"])
@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_generated_column_tables_fingerprint_every_projected_cell(kind, position) -> None:
    """The review's counterexample, at every generated-column position: same
    schema, same generated value, z=7 vs z=999 -- the fingerprints MUST differ,
    with both digests non-None."""
    policy = _policy()
    left = _generated_db(kind, position, a=1, z=7)
    right = _generated_db(kind, position, a=1, z=999)
    try:
        left_digest = policy._content_fingerprint(left)
        right_digest = policy._content_fingerprint(right)
        assert left_digest is not None and right_digest is not None
        assert left_digest != right_digest, (kind, position)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("kind", ["VIRTUAL", "STORED"])
@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_generated_column_equal_content_controls_compare_equal(kind, position) -> None:
    """Equal content (ordinary AND generated cells) compares equal -- generated
    columns are covered, not banned or collapsed to unknown."""
    policy = _policy()
    left = _generated_db(kind, position, a=1, z=7)
    right = _generated_db(kind, position, a=1, z=7)
    try:
        left_digest = policy._content_fingerprint(left)
        right_digest = policy._content_fingerprint(right)
        assert left_digest is not None and right_digest is not None
        assert left_digest == right_digest
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("kind", ["VIRTUAL", "STORED"])
def test_mutation_of_a_generated_dependency_changes_the_fingerprint(kind) -> None:
    """a=1 (g=2) vs a=2 (g=4) with identical z: the ordinary dependency drives
    the generated value; the fingerprint must see the change."""
    policy = _policy()
    left = _generated_db(kind, "middle", a=1, z=7)
    right = _generated_db(kind, "middle", a=2, z=7)
    try:
        left_digest = policy._content_fingerprint(left)
        right_digest = policy._content_fingerprint(right)
        assert left_digest is not None and right_digest is not None
        assert left_digest != right_digest
    finally:
        left.close()
        right.close()


def test_projection_width_disagreement_is_unknown_never_partial() -> None:
    """If cells and projection names ever disagree in width, the answer is
    unknown (None), never a silently truncated read."""
    policy = _policy()
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None
    conn.execute("CREATE TABLE t (a INTEGER, z INTEGER)")
    conn.execute("INSERT INTO t VALUES (1, 7)")

    class _ShortenedProjection:
        """Duck-typed connection whose SELECT * reports a SHORT name list -- the
        exact shape PRAGMA table_info produces for generated-column tables: one
        name short of the cells it is asked to digest."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **k):
            cur = self._inner.execute(sql, *a, **k)
            if sql.startswith('SELECT * FROM') and cur.description:
                inner_cur = cur

                class _Cur:
                    description = inner_cur.description[:1]  # one name, two cells

                    def __iter__(self):
                        return iter(inner_cur)

                return _Cur()
            return cur

        def set_progress_handler(self, *a, **k):
            return self._inner.set_progress_handler(*a, **k)

    try:
        assert policy._content_fingerprint(_ShortenedProjection(conn)) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Defect 2: atomic publication
# ---------------------------------------------------------------------------


def _run(intent: str, arguments: dict, session: str = "f", **context):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **context))


def _make(db_world, name: str = "race") -> None:
    out = _run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": name, "schema_sql": "CREATE TABLE t (n INTEGER)", "seed_sql": "INSERT INTO t VALUES (1)"},
        **internal_scope("race.create", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",)),
    )
    assert out.ok, out.response_text[:200]


def test_real_concurrent_named_backup_writers_exactly_one_publishes(db_world) -> None:
    """Two REAL executor calls race the same backup name; one publishes, one is
    refused, and the winner's snapshot is complete and stable."""
    _make(db_world)
    scope = internal_scope("race.backup", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            _run, f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "pinned"}, **scope
        )
        second = pool.submit(
            _run, f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "pinned"}, **scope
        )
        results = [first.result(), second.result()]
    oks = [r for r in results if r.ok]
    refused = [r for r in results if not r.ok]
    assert len(oks) == 1, [(r.status, r.response_text[:80]) for r in results]
    assert len(refused) == 1 and refused[0].status == "backup_exists"
    pinned = Path(oks[0].details["observation"]["path"])
    assert pinned.is_file()
    # The winner is a complete, valid snapshot with the expected content.
    conn = sqlite3.connect(str(pinned))
    try:
        assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]
    finally:
        conn.close()
    # And no partial ever sat at a published name: only the winner file exists
    # besides nothing partial (sweep the backups dir for part files).
    backups_dir = databases_root(db_world) / "backups" / "race"
    parts = [p for p in backups_dir.iterdir() if ".part-" in p.name]
    assert parts == [], parts


def test_deterministic_barrier_race_both_pass_exists_then_publish_twice_at_base(db_world) -> None:
    """The review's exact reproduction: a barrier parks BOTH writers immediately
    after their exists(target) checks return False, then both proceed. At base,
    both publish; under the atomic publisher, exactly one does. Racing the
    handler's own publisher function (whole-script exec, no main) keeps the
    barrier deterministic -- a raw two-process race misses the microsecond window.
    """
    import os
    import threading
    import types

    os.environ["PLUGIN_SCRATCH"] = str(db_world / "scratch" / DB_PLUGIN_ID)
    _make(db_world)
    handler = Path("plugins/vool-database/bin/db_handler").read_text(encoding="utf-8")
    module = types.ModuleType("db_handler_full_probe")
    exec(compile(handler, "plugins/vool-database/bin/db_handler", "exec"), module.__dict__)

    source = databases_root(db_world) / "race.sqlite"
    barrier = threading.Barrier(2, timeout=10)
    real_exists = os.path.exists
    target_name = str(databases_root(db_world) / "backups" / "race" / "det.sqlite")

    def parked_exists(path):
        result = real_exists(path)
        if str(path) == target_name:
            # Park only once BOTH writers have passed their exists check; if the
            # publisher no longer consults exists for the claim, nobody parks.
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=0.5)
        return result

    results = {}
    def writer(tag):
        try:
            os.path.exists = parked_exists
            results[tag] = module._take_backup(str(source), "race", "det")
        except Exception as exc:  # Fail or anything else
            results[tag] = exc
        finally:
            os.path.exists = real_exists

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    os.path.exists = real_exists
    outcomes = list(results.values())
    published = [o for o in outcomes if not isinstance(o, Exception)]
    refused = [o for o in outcomes if isinstance(o, Exception)]
    assert len(published) == 1, outcomes
    assert len(refused) == 1, outcomes
    # The published snapshot is complete and no partial was left behind.
    target = Path(target_name)
    assert target.is_file()
    conn = sqlite3.connect(str(target))
    try:
        assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]
    finally:
        conn.close()
    parts = [p for p in target.parent.iterdir() if ".part-" in p.name]
    assert parts == [], parts


def test_concurrent_automatic_backups_publish_two_distinct_complete_snapshots(db_world) -> None:
    """Automatic names are unique by ATOMIC CLAIM, not UUID luck: two racing
    default-name backups both publish, under distinct names, both complete."""
    _make(db_world)
    scope = internal_scope("race.backup2", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_run, f"{DB_PLUGIN_ID}.backup", {"name": "race"}, **scope)
        second = pool.submit(_run, f"{DB_PLUGIN_ID}.backup", {"name": "race"}, **scope)
        results = [first.result(), second.result()]
    assert all(r.ok for r in results), [(r.status, r.response_text[:80]) for r in results]
    paths = [Path(r.details["observation"]["path"]) for r in results]
    assert paths[0] != paths[1]
    for path in paths:
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]
        finally:
            conn.close()


def test_failed_publication_cleans_up_and_never_deletes_the_winner(db_world) -> None:
    """A pre-existing orphan part file is not a winner and must not block a new
    publication; the existing WINNER is never removed or rewritten."""
    _make(db_world)
    scope = internal_scope("race.backup3", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",))
    first = _run(f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "pin"}, **scope)
    assert first.ok
    pinned = Path(first.details["observation"]["path"])
    winner_bytes = pinned.read_bytes()
    # An orphaned partial from an interrupted publication sits beside it.
    orphan = pinned.parent / (pinned.name + ".part-orphan")
    orphan.write_bytes(b"garbage-partial")
    # A second publication under the same name is refused, winner untouched.
    again = _run(f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "pin"}, **scope)
    assert not again.ok and again.status == "backup_exists"
    assert pinned.read_bytes() == winner_bytes
    # A DIFFERENT name still publishes fine with the orphan present, and the
    # orphan is not silently promoted to a backup.
    fresh = _run(f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "pin2"}, **scope)
    assert fresh.ok
    assert Path(fresh.details["observation"]["path"]).is_file()
    conn = sqlite3.connect(str(fresh.details["observation"]["path"]))
    try:
        assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]
    finally:
        conn.close()


def test_restore_verification_is_value_faithful_from_a_fresh_connection(db_world) -> None:
    """restored_verified means the restored CELLS equal the backup's cells,
    verified from a fresh connection — not just schema and counts."""
    from core.mode_permission_policy import resolve_approval

    _make(db_world)
    scope = internal_scope("fid.backup", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",))
    saved = _run(f"{DB_PLUGIN_ID}.backup", {"name": "race", "backup_name": "values"}, **scope)
    assert saved.ok
    # Mutate VALUES (same schema, same counts) after the backup.
    pending = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "race", "statements": ["UPDATE t SET n = 424242"]})
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    applied = _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "race", "statements": ["UPDATE t SET n = 424242"]},
        mode_approval_token=token,
    )
    assert applied.ok
    pending_restore = _run(f"{DB_PLUGIN_ID}.restore", {"name": "race", "backup_name": "values"})
    token = str(pending_restore.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    restored = _run(
        f"{DB_PLUGIN_ID}.restore",
        {"name": "race", "backup_name": "values"},
        mode_approval_token=token,
    )
    assert restored.ok, restored.response_text[:200]
    observation = restored.details["observation"]
    assert observation["outcome"] == "restored_verified"
    assert observation["verified_from"] == "fresh_connection"
    conn = sqlite3.connect(str(databases_root(db_world) / "race.sqlite"))
    try:
        assert conn.execute("SELECT n FROM t").fetchall() == [(1,)]  # the VALUE came back
    finally:
        conn.close()
