"""A2 identity isolation + uniqueness regression.

Load-bearing for TWO repairs:

Pass 001 (context isolation): the admission ContextVar's default was ONE
shared mutable _AdmissionState; every thread that never called .set() shared
it. Repair: default=None, per-context lazy state via _get_admission_state.

Pass 001 continuation (identity uniqueness): per-turn attempt counters
re-minted identical sr:<turn_id>:<seq> ids across reused turn_ids and
"turn-unknown" fallbacks, and rejected duplicates exposed a contradictory
fresh candidate id in-band. Repair: process-global monotonic identity
sequence owned by A2; reset_admission() installs a fresh state object
instead of mutating an inherited one; duplicate stubs carry the FIRST
admitted id as the authoritative semantic identity.

Removing any of these protections makes specific tests below FAIL.
"""
from __future__ import annotations

import asyncio
import threading
from contextvars import copy_context

from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    current_admission,
    has_admitted,
    reset_admission,
)


def _admit(response: str, turn_id: str = "") -> dict:
    kwargs = {"route_reason": "model_lane"}
    if turn_id:
        return admit_semantic_result(
            {"response": response, **kwargs}, turn_id=turn_id
        )
    return admit_semantic_result({"response": response, **kwargs})


def _stub(result: dict) -> dict:
    return result["_semantic_admission"]


# ---------------------------------------------------------------------------
# C1: thread isolation — B cannot see or clear A's admission; duplicates in
# one logical turn stay rejected with the FIRST id authoritative.
# ---------------------------------------------------------------------------

def test_c1_thread_b_cannot_see_or_clear_thread_a_admission():
    out: dict = {}

    def ctx_a() -> None:
        reset_admission()
        _admit("ALPHA-CONTENT", turn_id="turnA")
        out["a_record"] = current_admission()
        out["a_event"].set()
        out["b_event"].wait()  # B performs its own turn-start reset mid-turn
        dup = _admit("BRAVO-DIFFERENT-BYTES", turn_id="turnA")
        out["dup_stub"] = dict(_stub(dup))
        out["a_record_after"] = current_admission()

    def ctx_b() -> None:
        out["a_event"].wait()
        out["b_sees_a_record"] = has_admitted()
        reset_admission()  # ordinary turn-start call (apps/vool_agent.py)
        out["b_after_own_reset"] = has_admitted()
        out["b_event"].set()

    out["a_event"] = threading.Event()
    out["b_event"] = threading.Event()
    ta = threading.Thread(target=ctx_a)
    tb = threading.Thread(target=ctx_b)
    ta.start()
    tb.start()
    ta.join()
    tb.join()

    assert out["a_record"] is not None
    first_id = out["a_record"].semantic_result_id
    assert out["b_sees_a_record"] is False, (
        "thread B observed thread A's admitted record — state is shared"
    )
    assert out["b_after_own_reset"] is False

    rec = out["a_record_after"]
    assert rec is not None
    assert rec.semantic_result_id == first_id
    assert rec.content == "ALPHA-CONTENT", (
        "admitted record content changed under the same semantic_result_id"
    )
    # Duplicate stub carries the AUTHORITATIVE first id, accepted=False.
    assert out["dup_stub"]["semantic_result_id"] == first_id
    assert out["dup_stub"]["accepted"] is False
    assert out["dup_stub"]["duplicate"] is True


# ---------------------------------------------------------------------------
# C2: asyncio task isolation — concurrent tasks neither collide nor
# falsely reject each other.
# ---------------------------------------------------------------------------

def test_c2_asyncio_tasks_are_isolated():
    async def main() -> dict:
        reset_admission()
        results: dict = {}

        async def task(name: str) -> None:
            await asyncio.sleep(0)  # yield to interleave
            reset_admission()
            r = _admit(f"CONTENT-{name}", turn_id=f"async-{name}")
            await asyncio.sleep(0)
            results[name] = {
                "stub": dict(_stub(r)),
                "record": current_admission(),
            }

        await asyncio.gather(task("P"), task("Q"))
        return results

    results = asyncio.run(main())
    for name in ("P", "Q"):
        assert results[name]["stub"]["accepted"] is True, (
            f"task {name} was falsely rejected by the other task"
        )
        assert results[name]["record"] is not None
        assert results[name]["record"].content == f"CONTENT-{name}"
    ids = {results["P"]["stub"]["semantic_result_id"],
           results["Q"]["stub"]["semantic_result_id"]}
    assert len(ids) == 2, "two independent tasks minted the same id"

    # A plain reset in one task must not clear another live admission.
    seen: dict = {}

    async def reset_kills_nothing() -> None:
        async def owner() -> None:
            reset_admission()
            _admit("OWNER", turn_id="owner-1")
            await asyncio.sleep(0.05)
            seen["record"] = current_admission()

        async def interferer() -> None:
            await asyncio.sleep(0.01)
            reset_admission()

        await asyncio.gather(owner(), interferer())

    asyncio.run(reset_kills_nothing())
    assert seen["record"] is not None, "another task's reset cleared this task's admission"


# ---------------------------------------------------------------------------
# C3 / C5: fresh-turn state and sequential non-inheritance.
# ---------------------------------------------------------------------------

def test_c3_reset_gives_fresh_state_and_rejects_carried_duplicate():
    reset_admission()
    _admit("TURN ONE", turn_id="t-c3")
    assert has_admitted()
    reset_admission()
    assert has_admitted() is False
    assert current_admission() is None
    # New turn admits cleanly (no false duplicate of the previous turn).
    r = _admit("TURN TWO", turn_id="t-c3")
    assert _stub(r)["accepted"] is True


def test_c5_sequential_turns_do_not_inherit_previous_state():
    ids: list = []
    for content in ("FIRST", "SECOND"):
        reset_admission()
        r = _admit(content, turn_id="shared-turn")
        ids.append(_stub(r)["semantic_result_id"])
        assert _stub(r)["accepted"] is True
        assert current_admission().content == content
    assert ids[0] != ids[1], "reused turn_id re-minted the same id"


def test_c5b_copy_context_child_reset_leaves_parent_intact():
    reset_admission()
    _admit("PARENT", turn_id="parent-1")
    parent_before = current_admission().semantic_result_id
    seen: dict = {}

    def child() -> None:
        # Child inherited the parent's state via context copy.
        seen["inherited"] = has_admitted()
        reset_admission()
        seen["after_reset"] = has_admitted()

    copy_context().run(child)
    assert seen["inherited"] is True
    assert seen["after_reset"] is False, "child reset did not give fresh state"
    rec = current_admission()
    assert rec is not None
    assert rec.semantic_result_id == parent_before, (
        "child's reset cleared the parent context's admitted record"
    )


# ---------------------------------------------------------------------------
# C6: uniqueness across reused turn_id / missing identity / concurrency.
# ---------------------------------------------------------------------------

def test_c6_reused_turn_id_mints_distinct_ids():
    ids = set()
    for i in range(3):
        reset_admission()
        r = _admit(f"CONTENT-{i}", turn_id="recycled-turn")
        assert _stub(r)["accepted"] is True
        ids.add(_stub(r)["semantic_result_id"])
    assert len(ids) == 3, "same id minted for different accepted content"


def test_c6_missing_identity_never_collapses_to_one_id():
    # Missing identity may keep the descriptive "turn-unknown" turn
    # component, but the A2 global sequence must make every full id unique.
    ids = set()
    for i in range(3):
        reset_admission()
        r = _admit(f"NO-IDENTITY-CONTENT-{i}")
        assert _stub(r)["accepted"] is True
        ids.add(_stub(r)["semantic_result_id"])
    assert len(ids) == 3, "unknown identity collapsed unrelated admissions onto one id"


def test_c6_concurrent_independent_turns_all_unique_and_accepted():
    n = 8
    barrier = threading.Barrier(n)
    out: dict[int, dict] = {}

    def worker(i: int) -> None:
        reset_admission()
        barrier.wait()
        r = _admit(f"W-{i}", turn_id=f"conc-{i % 2}")  # even ids deliberately reused
        out[i] = {"stub": dict(_stub(r)), "record": current_admission()}

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = set()
    for i in range(n):
        assert out[i]["stub"]["accepted"] is True, f"worker {i} falsely rejected"
        assert out[i]["record"].content == f"W-{i}"
        ids.add(out[i]["stub"]["semantic_result_id"])
    assert len(ids) == n, "concurrent independent turns produced colliding ids"


# ---------------------------------------------------------------------------
# C4 / E: same-turn duplicate rejection; authoritative identity stays FIRST;
# no contradictory in-band id.
# ---------------------------------------------------------------------------

def test_c4_duplicate_rejected_first_identity_authoritative():
    reset_admission()
    r1 = _admit("FIRST", turn_id="t-dup")
    first_id = _stub(r1)["semantic_result_id"]
    r2 = _admit("SECOND-DIFFERENT", turn_id="t-dup")
    s2 = _stub(r2)
    assert s2["accepted"] is False
    assert s2["duplicate"] is True
    assert s2["semantic_result_id"] == first_id, (
        "rejected duplicate exposed a contradictory in-band semantic_result_id"
    )
    rec = current_admission()
    assert rec is not None and rec.semantic_result_id == first_id
    assert rec.content == "FIRST"


def test_c7_exception_hygiene_no_leak_into_next_turn():
    reset_admission()
    _admit("BEFORE CRASH", turn_id="crashy")
    # Simulate a mid-turn crash: next unrelated turn on this context resets
    # first (the production entry point always does) and gets clean state.
    reset_admission()
    assert current_admission() is None
    r = _admit("AFTER CRASH", turn_id="next-turn")
    assert _stub(r)["accepted"] is True
    assert current_admission().content == "AFTER CRASH"
    # Empty response never creates an admission.
    before = current_admission().semantic_result_id
    empty = admit_semantic_result({"response": "", "route_reason": "model_lane"})
    assert "_semantic_admission" not in empty
    assert current_admission().semantic_result_id == before


# ---------------------------------------------------------------------------
# C8: single identity authority — only this seam mints sr: ids.
# ---------------------------------------------------------------------------

def test_c8_only_the_seam_mints_sr_ids():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hits = subprocess.run(
        [sys.executable, "-c",
         "import pathlib;"
         "print('\\n'.join(str(p) for p in pathlib.Path('.').rglob('*.py')"
         " if ('f\\\"sr:' in p.read_text(errors='ignore')"
         " or '\\\"sr:' in p.read_text(errors='ignore'))"
         " and 'test' not in str(p) and '.venv' not in str(p)))"],
        cwd=root, capture_output=True, text=True,
    ).stdout.split()
    offenders = [
        h for h in hits
        if Path(h).resolve() != (root / "core/semantic/semantic_result_seam.py").resolve()
    ]
    assert not offenders, f"sr: identity minting found outside the seam: {offenders}"


# ---------------------------------------------------------------------------
# R1-R3: process-lifetime uniqueness — fresh interpreters must never replay
# the id space onto reused/missing turn identity (restart collision class).
# ---------------------------------------------------------------------------

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_FRESH_PROBE = f"""
import sys
sys.path.insert(0, {str(_ROOT)!r})
from storage.migrations import run_migrations
run_migrations()  # Fresh processes bootstrap their durable admission store before use.
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
turn_id, content = sys.argv[1], sys.argv[2]
reset_admission()
kwargs = {{"response": content, "route_reason": "model_lane"}}
result = (admit_semantic_result(kwargs, turn_id=turn_id)
          if turn_id != "-" else admit_semantic_result(kwargs))
stub = result["_semantic_admission"]
print(stub["semantic_result_id"], stub["accepted"])
"""


def _fresh_process_admit(turn_id: str, content: str) -> tuple[str, bool]:
    """Admit through the real seam in a FRESH interpreter process."""
    proc = subprocess.run(
        [sys.executable, "-c", _FRESH_PROBE, turn_id, content],
        cwd=str(_ROOT), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
        timeout=120,
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr}"
    sr_id, accepted = proc.stdout.strip().rsplit(" ", 1)
    return sr_id, accepted == "True"


def test_r1_fresh_processes_reused_turn_id_never_collide():
    id_a, ok_a = _fresh_process_admit("recycled-turn", "PROCESS-A-CONTENT")
    id_b, ok_b = _fresh_process_admit("recycled-turn", "PROCESS-B-DIFFERENT")
    assert ok_a and ok_b
    assert id_a != id_b, (
        f"fresh processes re-minted identical id {id_a} for different accepted "
        "content — restart collision"
    )


def test_r2_fresh_processes_missing_identity_never_collide():
    id_a, ok_a = _fresh_process_admit("-", "NOID-A-CONTENT")
    id_b, ok_b = _fresh_process_admit("-", "NOID-B-DIFFERENT")
    assert ok_a and ok_b
    assert id_a != id_b, f"missing-identity restart collision: {id_a}"


def test_r3_simultaneous_independent_processes_no_collision():
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _FRESH_PROBE, "shared-turn", content],
            cwd=str(_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "PYTHONPATH": str(_ROOT)},
        )
        for content in ("SIMUL-A", "SIMUL-B", "SIMUL-C")
    ]
    ids = set()
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        sr_id, accepted = out.strip().rsplit(" ", 1)
        assert accepted == "True"
        ids.add(sr_id)
    assert len(ids) == len(procs), "concurrent independent processes collided"
