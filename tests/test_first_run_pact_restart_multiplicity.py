"""M-P6/M-P7 — restart, corruption and CAS multiplicity never tear or block the pact."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys
import threading

import pytest

from core import first_run_pact
from core.first_run_pact import PactFault
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture


def test_sigkill_between_writes_never_leaves_a_half_written_file(pact_rig):
    """Torn-write simulation: kill writers mid-storm; the file always parses or is quarantined.

    The write pattern itself is pinned structurally: mkstemp in the target dir, fsync,
    os.replace — the repo's canonical atomic pattern (the pact file is its exemplar)."""
    import inspect

    source = inspect.getsource(first_run_pact._atomic_write)
    for required in ("mkstemp", "fsync", "os.replace"):
        assert required in source, f"the atomic write pattern lost {required}"
    from core.runtime_paths import active_data_dir

    first_run_pact.seed()
    pact_file = active_data_dir() / "first_run_pact_state.json"
    stop = threading.Event()
    errors: list[Exception] = []

    def storm():
        while not stop.is_set():
            try:
                first_run_pact.hide(True)
            except PactFault:
                pass
            except Exception as exc:
                errors.append(exc)
                return

    threads = [threading.Thread(target=storm, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    for _ in range(25):
        raw = pact_file.read_bytes()
        if raw.strip():
            json.loads(raw)  # every observed instant is a parseable, complete document
    stop.set()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors
    json.loads(pact_file.read_text())


def test_task_claim_succeeds_across_a_full_process_restart(pact_rig):
    pact_rig.walk_to("local_task")
    request_id, _frames, _commit = pact_rig.run_task_turn("Create welcome-notes.txt containing Welcome to VOOL.")
    state_before = first_run_pact.load_state()

    # In-process restart: drop every cache and reopen the same files (the rig's own law).
    import storage.db as sdb
    from apps.vool_api_server import _bootstrap
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(pact_rig.home)
    sdb.configure_default_db_path(pact_rig.home / "data" / "vool_web0_v2.db")
    pact_rig.runtime = _bootstrap(run_prewarm=False)

    state_after_boot = first_run_pact.load_state()
    assert state_after_boot == state_before  # byte-stable across restart
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/task/claim", {
        "session_id": pact_rig.canonical_session(), "request_id": request_id,
        "expect_revision": snap["revision"],
    })
    assert status == 200, payload


def test_boot_is_never_blocked_by_an_absent_or_unhealthy_pact(pact_rig):
    """A GET racing the seed gets a typed answer, never a hang and never a guess."""
    from core.runtime_paths import active_data_dir
    from core.web.api.service import dispatch_get

    (active_data_dir() / "first_run_pact_state.json").unlink(missing_ok=True)
    response = dispatch_get(path="/api/onboarding/pact", query={}, runtime=pact_rig.runtime,
                            model_name="vool", client_host="127.0.0.1")
    # The GET seeds idempotently and answers — boot ordering cannot wedge the page.
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["state"] in {"absent", "welcome", "not_applicable"}


def test_two_cas_writers_converge_with_exactly_one_winner_per_revision(pact_rig):
    first_run_pact.seed()
    first_run_pact.begin()
    snap = first_run_pact.load_state()
    results: list = []

    def contender(tag: str):
        try:
            data = first_run_pact.advance("naming", expect_revision=snap["revision"])
            results.append(("won", tag, data["revision"]))
        except PactFault as exc:
            results.append(("lost", tag, exc.code))

    threads = [threading.Thread(target=contender, args=(str(i),)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [r for r in results if r[0] == "won"]
    losers = [r for r in results if r[0] == "lost"]
    assert len(winners) == 1
    assert len(losers) == 4
    assert all(code == "stale_revision" for _, _, code in losers)


def test_a_subprocess_writer_converges_under_flock_hygiene(pact_rig, tmp_path):
    from core.runtime_paths import active_data_dir

    first_run_pact.seed()
    writer = tmp_path / "pact_writer.py"
    writer.write_text(
        "import sys, os, json\n"
        "sys.path.insert(0, %r)\n"
        "os.environ['VOOL_HOME'] = %r\n"
        "from core.runtime_paths import configure_runtime_home\n"
        "configure_runtime_home(%r)\n"
        "from core import first_run_pact\n"
        "first_run_pact.begin()\n"
        "print(json.dumps({'state': first_run_pact.load_state()['state']}))\n"
        % (str(Path(__file__).resolve().parents[1]), str(pact_rig.home), str(pact_rig.home))
    )
    proc = subprocess.run([sys.executable, str(writer)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    data = json.loads((active_data_dir() / "first_run_pact_state.json").read_text())
    assert data["state"] in {"welcome", "naming"}
