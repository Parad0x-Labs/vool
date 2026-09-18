"""Multiprocess CAS + connection-lifecycle correction tests (release-sensitive).

DEFECT 1 pins: has_existing_signal() owns the storage.db connection it opens — it must
close on EVERY exit path (row found, no row, query raised, seed completing, repeated
seed/GET leaving no persistent holder).

DEFECT 2 pins: cross-process CAS is atomic. Two independent processes read the same
revision, are released simultaneously by a deterministic barrier, and exactly one
mutation may commit — the loser receives the typed stale_revision fault and unrelated
state is not lost. Pinned for the pact state, the provider-choice state, and the
policy setter (two processes updating independent allowed keys — no lost update).
No load-based skips: every race runs three rounds unconditionally.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
import textwrap
from pathlib import Path

import pytest

from core import first_run_pact
from core.first_run_pact import PactFault
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture

REPO = Path(__file__).resolve().parents[1]
ROUNDS = 3


# --- DEFECT 1: the DB census owns and closes its connection ----------------------------------


class _CountingConn:
    def __init__(self, real, registry: list):
        self._real = real
        self._registry = registry

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        if not getattr(self, "_closed", False):
            self._closed = True
            self._registry.append("closed")
            self._real.close()


def _patch_connection_factory(monkeypatch):
    import storage.db as sdb

    created: list = []
    closed: list = []
    original = sdb.get_connection

    def factory(db_path=None):
        real = original(db_path)
        created.append(real)
        return _CountingConn(real, closed)

    monkeypatch.setattr(sdb, "get_connection", factory)
    return created, closed


def test_the_db_census_closes_its_connection_when_a_row_is_found(pact_rig, monkeypatch):
    created, closed = _patch_connection_factory(monkeypatch)
    import storage.db as sdb

    setup = sdb.get_connection()
    try:
        setup.execute("CREATE TABLE IF NOT EXISTS chat_sessions (id INTEGER PRIMARY KEY, placeholder TEXT)")
        setup.execute("INSERT INTO chat_sessions (placeholder) VALUES ('prior use')")
        setup.commit()
    finally:
        setup.close()

    created.clear()
    closed.clear()
    assert first_run_pact.has_existing_signal() is True
    assert len(created) == 1 and len(closed) == 1, "the census connection leaked on the row-found return"


def test_the_db_census_closes_its_connection_when_no_row_exists(pact_rig, monkeypatch):
    created, closed = _patch_connection_factory(monkeypatch)
    created.clear()
    closed.clear()
    first_run_pact.has_existing_signal()
    assert len(created) == 1
    assert len(closed) == 1, "the census connection leaked on the fall-through path"


def test_the_db_census_closes_when_the_table_query_raises(monkeypatch, tmp_path):
    """The execute that RAISES must still leave a closed connection behind."""
    import storage.db as sdb

    opened = []
    closed = []
    original = sdb.get_connection

    def factory(db_path=None):
        conn = original(db_path)
        opened.append(conn)
        real_close = conn.close

        class _RaisingExecute:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, *a, **k):
                raise RuntimeError("boom")

            def close(self):
                closed.append("closed")
                real_close()

        class _Proxy:
            def execute(self, *a, **k):
                raise RuntimeError("boom")

            def close(self):
                closed.append("closed")
                real_close()

        return _Proxy()

    monkeypatch.setattr(sdb, "get_connection", factory)
    first_run_pact.has_existing_signal()  # must not raise and must have closed the conn
    assert len(opened) == len(closed), f"leaked: opened {len(opened)}, closed {len(closed)}"


def test_seed_completes_on_a_real_sqlite_store_and_the_census_closes(pact_rig, monkeypatch):
    created, closed = _patch_connection_factory(monkeypatch)
    import storage.db as sdb

    probe = sdb.get_connection()
    try:
        probe.execute("SELECT 1")
    finally:
        probe.close()
    created.clear()
    closed.clear()
    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.unlink(missing_ok=True)  # force the seed to run the §12 predicate for real
    first_run_pact.seed()
    assert first_run_pact.load_state() is not None
    assert len(created) == len(closed), f"seed leaked connections: {len(created)} opened, {len(closed)} closed"


def test_repeated_seed_and_get_leave_no_persistent_db_holder(pact_rig, monkeypatch):
    created, closed = _patch_connection_factory(monkeypatch)
    import storage.db as sdb

    probe = sdb.get_connection()
    try:
        probe.execute("SELECT 1")
    finally:
        probe.close()
    created.clear()
    closed.clear()
    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.unlink(missing_ok=True)  # the first seed must run the predicate, not early-return
    for _ in range(6):
        first_run_pact.seed()
        first_run_pact.snapshot()
    assert len(created) == len(closed), (
        f"persistent holder: {len(created)} connections opened, {len(closed)} closed"
    )


# --- DEFECT 2: cross-process CAS is atomic ----------------------------------------------------

DRIVER = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path

    sys.path.insert(0, {repo!r})
    tag = sys.argv[1]
    barrier = Path(sys.argv[2])
    target = sys.argv[3]
    mode = sys.argv[4]  # race | stale

    from core import first_run_pact
    from core import first_run as provider

    if mode == "stale":
        # The carrier's revision is supplied by the parent: it is the revision the
        # operator read BEFORE the winner published — the definition of a stale carrier.
        revision = int(sys.argv[5])
    else:
        if target == "pact":
            current = first_run_pact.load_state()
        else:
            current = provider.load()
        revision = int(current["revision"])

    if mode == "race":
        ready = barrier / (tag + ".ready")
        ready.write_text("1", encoding="utf-8")
        other = barrier / (("b" if tag == "a" else "a") + ".ready")
        deadline = time.time() + 60
        while not other.exists():
            if time.time() > deadline:
                print(json.dumps({{"result": "barrier_timeout"}}))
                sys.exit(0)
            time.sleep(0.02)
    else:
        # stale-carrier mode: tag "a" commits FIRST; tag "b" then attempts with the
        # revision it read before the winner published — a deterministic stale carrier.
        if tag == "a":
            pass
        else:
            winner = barrier / "a.done"
            deadline = time.time() + 60
            while not winner.exists():
                if time.time() > deadline:
                    print(json.dumps({{"result": "barrier_timeout"}}))
                    sys.exit(0)
                time.sleep(0.02)

    try:
        if target == "pact":
            data = first_run_pact.advance("naming", expect_revision=revision)
            done = barrier / "a.done"
            done.write_text("1", encoding="utf-8")
            print(json.dumps({{"result": "committed", "revision": data["revision"]}}))
        else:
            data = provider.choose("local_only", expect_revision=revision)
            done = barrier / "a.done"
            done.write_text("1", encoding="utf-8")
            print(json.dumps({{"result": "committed", "revision": data["revision"]}}))
    except Exception as exc:
        code = getattr(exc, "code", "") or type(exc).__name__
        print(json.dumps({{"result": "refused", "code": code}}))
    """
)


def _spawn_driver(python, repo, home, tag, barrier, target, mode, forced_revision=None):
    script = barrier / f"driver_{tag}_{mode}.py"
    script.write_text(DRIVER.format(repo=str(repo)))
    env = dict(os.environ)
    env.update({
        "VOOL_HOME": str(home),
        "VOOL_KEY_STORAGE_MODE": "file",
        "VOOL_CREDENTIAL_STORE": "vault",
    })
    argv = [python, str(script), tag, str(barrier), target, mode]
    if forced_revision is not None:
        argv.append(str(forced_revision))
    return subprocess.Popen(
        argv, cwd=str(repo), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _seed_target(home: Path, target: str) -> dict:
    (home / "data").mkdir(parents=True, exist_ok=True)
    if target == "pact":
        start = {
            "schema_name": "vool.first_run_pact", "schema_version": 1, "state": "welcome",
            "revision": 1, "welcome_hidden": False, "seed_reason": "fresh_install",
            "steps": {}, "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:00Z",
            "provider_step": "delegated",
        }
        marker = home / "data" / "first_run_pact_state.json"
    else:
        start = {
            "version": 1, "state": "card_visible", "revision": 1, "chosen_provider_id": "",
            "skipped_at": "", "connected_at": "", "test_turn_message_id": "",
            "provider_step": "delegated",
        }
        marker = home / "data" / "first_run_state.json"
    marker.write_text(json.dumps(start, indent=2, sort_keys=True) + "\n")
    return start


def _run_race(target: str, rounds: int = ROUNDS):
    _RACE_TMP = Path(tempfile.mkdtemp(prefix="pact-race-"))
    outcomes = []
    for round_index in range(rounds):
        home = _RACE_TMP / f"race-{target}-{round_index}"
        barrier = _RACE_TMP / f"barrier-{target}-{round_index}"
        barrier.mkdir(parents=True, exist_ok=True)
        _seed_target(home, target)

        procs = [
            _spawn_driver(sys.executable, REPO, home, tag, barrier, target, "race")
            for tag in ("a", "b")
        ]
        outs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            if proc.returncode != 0:
                raise AssertionError(f"driver crashed: {err[-400:]}")
            outs.append(json.loads(out.strip().splitlines()[-1]))

        marker = home / "data" / ("first_run_pact_state.json" if target == "pact" else "first_run_state.json")
        after = json.loads(marker.read_text())
        outcomes.append({
            "round": round_index,
            "outs": outs,
            "after_revision": after["revision"],
            "start_revision": 1,
        })

    for entry in outcomes:
        committed = [o for o in entry["outs"] if o["result"] == "committed"]
        refused = [o for o in entry["outs"] if o["result"] == "refused"]
        assert len(committed) == 1, f"round {entry['round']}: expected exactly one winner, got {entry['outs']}"
        assert len(refused) == 1 and refused[0]["code"] in {"stale_revision", "lock_unavailable"}, entry["outs"]
        assert entry["after_revision"] == 2, entry


def _run_stale(target: str):
    _RACE_TMP = Path(tempfile.mkdtemp(prefix="pact-stale-"))
    home = _RACE_TMP / f"stale-{target}"
    barrier = _RACE_TMP / f"barrier-stale-{target}"
    barrier.mkdir(parents=True, exist_ok=True)
    _seed_target(home, target)

    winner = _spawn_driver(sys.executable, REPO, home, "a", barrier, target, "stale", forced_revision=1)
    loser = _spawn_driver(sys.executable, REPO, home, "b", barrier, target, "stale", forced_revision=1)
    out_w, err_w = winner.communicate(timeout=120)
    out_l, err_l = loser.communicate(timeout=120)
    if winner.returncode != 0:
        raise AssertionError(f"winner crashed: {err_w[-400:]}")
    if loser.returncode != 0:
        raise AssertionError(f"loser crashed: {err_l[-400:]}")
    winner_out = json.loads(out_w.strip().splitlines()[-1])
    loser_out = json.loads(out_l.strip().splitlines()[-1])
    assert winner_out["result"] == "committed" and winner_out["revision"] == 2, winner_out
    assert loser_out == {"result": "refused", "code": "stale_revision"}, loser_out
    marker = home / "data" / ("first_run_pact_state.json" if target == "pact" else "first_run_state.json")
    after = json.loads(marker.read_text())
    assert after["revision"] == 2


def test_cross_process_cas_exactly_one_winner_pact_state():
    _run_race("pact")


def test_cross_process_cas_exactly_one_winner_provider_state():
    _run_race("provider")


def test_a_stale_carrier_across_processes_gets_typed_stale_revision_pact():
    _run_stale("pact")


def test_a_stale_carrier_across_processes_gets_typed_stale_revision_provider():
    _run_stale("provider")


LOCK_DRIVER = textwrap.dedent(
    """
    import fcntl, json, sys
    from pathlib import Path

    sys.path.insert(0, {repo!r})
    target = sys.argv[1]
    lock_path = Path(sys.argv[2])

    from core import first_run_pact
    from core import first_run as provider

    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        if target == "pact":
            first_run_pact.advance("naming")
        else:
            provider.choose("local_only")
        print(json.dumps({{"code": "no-fault-mutation-ran"}}))
    except Exception as exc:
        print(json.dumps({{"code": getattr(exc, "code", "") or type(exc).__name__}}))
    """
)


def test_the_mutation_lock_fails_closed_with_a_typed_fault(pact_rig, tmp_path):
    """A mutation that cannot take the publication lock refuses TYPED and fast —
    it must never block indefinitely and never 'yield unlocked' and continue."""
    first_run_pact.seed()
    first_run_pact.begin()
    from core import first_run as provider

    provider.reset()

    for target, lock_name in (("pact", "first_run_pact_state.json.lock"),
                              ("provider", "first_run_state.json.lock")):
        lock_path = pact_rig.home / "data" / lock_name
        script = tmp_path / f"lock_driver_{target}.py"
        script.write_text(LOCK_DRIVER.format(repo=str(REPO)))
        env = dict(os.environ)
        env["VOOL_HOME"] = str(pact_rig.home)
        env["VOOL_KEY_STORAGE_MODE"] = "file"
        env["VOOL_CREDENTIAL_STORE"] = "vault"
        proc = subprocess.Popen(
            [sys.executable, str(script), target, str(lock_path)],
            cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=10)
            raise AssertionError(
                f"{target}: the mutation BLOCKED on a held publication lock — "
                "acquisition must be non-blocking and fail closed with a typed fault"
            )
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["code"] == "lock_unavailable", (target, payload)


POLICY_DRIVER = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path

    sys.path.insert(0, {repo!r})
    tag = sys.argv[1]
    barrier = Path(sys.argv[2])

    from core import policy_engine

    key, value = ("system.local_only_mode", True) if tag == "a" else ("network.outbound_enabled", False)

    ready = barrier / (tag + ".ready")
    ready.write_text("1", encoding="utf-8")
    other = barrier / (("b" if tag == "a" else "a") + ".ready")
    deadline = time.time() + 30
    while not other.exists():
        if time.time() > deadline:
            print(json.dumps({{"result": "barrier_timeout"}}))
            sys.exit(0)
        time.sleep(0.02)

    # Fail-closed contention is answered the way the remediation says: bounded retry.
    # The deadline is generous: under heavy machine load the loser may wait out the
    # winner's whole publication — correctness never depends on winning the race.
    deadline = time.time() + 60
    while True:
        try:
            policy_engine.set_operator_policy_values({{key: value}})
            break
        except RuntimeError as exc:
            # contention messages say "holds the publication lock"; anything else is a
            # real failure. Exhaustion exits NONZERO so a lost update can never pass.
            if "publication lock" not in str(exc) or time.time() > deadline:
                print(json.dumps({{"result": "refused", "code": "lock_unavailable"}}))
                sys.exit(3)
            time.sleep(0.25)
    print(json.dumps({{"result": "written", "key": key}}))
    """
)


def test_policy_setter_two_processes_lose_no_update(pact_rig, tmp_path):
    """Two processes update INDEPENDENT allowed keys simultaneously — both must persist."""
    from core import policy_engine

    for round_index in range(ROUNDS):
        home = tmp_path / f"policy-race-{round_index}"
        home.mkdir(parents=True, exist_ok=True)
        barrier = tmp_path / f"policy-barrier-{round_index}"
        barrier.mkdir(parents=True, exist_ok=True)
        procs = []
        for tag in ("a", "b"):
            script = barrier / f"driver_{tag}.py"
            script.write_text(POLICY_DRIVER.format(repo=str(REPO)))
            env = dict(os.environ)
            env["VOOL_HOME"] = str(home)
            procs.append(subprocess.Popen(
                [sys.executable, str(script), tag, str(barrier)],
                cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            if proc.returncode != 0:
                raise AssertionError(f"policy driver crashed: {err[-400:]}")

        # Read the RACE HOME's policy document directly: both independent keys must survive.
        import yaml

        doc = yaml.safe_load((home / "config" / "default_policy.yaml").read_text(encoding="utf-8"))
        assert doc["system"]["local_only_mode"] is True, f"round {round_index}: local_only_mode lost"
        assert doc["network"]["outbound_enabled"] is False, f"round {round_index}: outbound_enabled lost"
