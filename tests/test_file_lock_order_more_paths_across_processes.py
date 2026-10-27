"""More memory and forget paths keep the file-lock order across processes (REVIEW-1, finding 3).

`tests/test_file_lock_order_across_processes.py` drives the forget command, chat delete,
finalization erase, upload and transcript append. This file adds, with the same recorder and
checker (`tests/_file_lock_order.py`) and the roles in `tests/_file_lock_coverage.py`:

- the forget sent as a served chat turn (`VoolAgent.run_once`), whose turn door admits it under
  every home's ledger flock (`_preadmit_forget_turn`) and retires the admission at the seal
  (`_finish_forget_turn_admission`);
- project delete through its web route (`/api/projects/delete`);
- "Remember that ..." (`add_memory_fact`), the forget's `forget_structured_memory`, and the web
  runtime's owner-local recall fence (`_memory_recall_response`);
- the context-compiler shadow's compile and dispatch.

Each path must show in its own trace that it really ran (its functions in the recorded frames,
its ledger flocks), the joined trace must follow the order, and a deliberate inversion around each
path (test code only) must be reported on that path. The last test races two served forget turns
with crossed homes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests import _file_lock_coverage as cov
from tests import _file_lock_order as flo

DEADLINE_S = 180.0
ITEMS = 2


def _homes(tmp_path: Path, layout: str) -> tuple[str, str]:
    active = str((tmp_path / "home-b").resolve())
    other = {"one_home": active, "runtime_home_sorts_first": str((tmp_path / "home-a").resolve()),
             "runtime_home_sorts_last": str((tmp_path / "home-c").resolve())}[layout]
    return active, other


def _with_frame(rows, name: str) -> list[dict]:
    return [r for r in rows if any(w.endswith(":" + name) for w in r.get("where") or [])]


def _check_results(results: dict, n: int) -> None:
    assert all(reply.startswith("Forget applied") for reply in results["served_forget"]), results["served_forget"]
    assert len(results["served_forget"]) == n
    for deleted in results["project_delete"]:
        assert deleted["status"] == 200 and deleted["ok"] is True and deleted["chats"] == 2, deleted
        assert deleted["forgot"] >= 1, deleted
    for remembered, forgot, recall in results["remember_forget_recall"]:
        assert "Locked in" in remembered and forgot.startswith("Forget applied"), (remembered, forgot)
        assert recall == "completed_scoped_erasure", recall
    for shadow in results["shadow_dispatch"]:
        assert shadow["sent"] == 1 and shadow["carries_body"], shadow


@pytest.mark.parametrize("layout", ["one_home", "runtime_home_sorts_first", "runtime_home_sorts_last"])
def test_more_memory_and_forget_paths_keep_the_file_lock_order_across_processes(tmp_path, layout):
    home, runtime_home = _homes(tmp_path, layout)
    data = flo.setup(tmp_path, "setup", home=home, runtime_home=runtime_home, n=ITEMS, tag="m", entry=cov.ENTRY)
    children = {role: flo.worker(tmp_path, role, role, data[role], home=home, runtime_home=runtime_home,
                                 entry=cov.ENTRY) for role in cov.ROLES}
    flo.go(tmp_path, children)
    stderr: dict = {}
    results, hung, errors = flo.finish(children, DEADLINE_S, stderr=stderr)
    if hung:
        pytest.fail(f"{layout}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s (killed): a cross-process "
                    "lock wait that never ended\n" + flo.hang_report(tmp_path, cov.ROLES), pytrace=False)
    order = flo.violations(flo.read_traces([tmp_path / f"{name}.trace" for name in cov.ROLES]))
    assert not order, "\n".join(order + [f"{name} failed: {error}" for name, error in errors.items()])
    assert not errors, errors
    assert not [name for name, err in stderr.items() if "NETWORK REFUSED" in err], stderr
    _check_results(results, ITEMS)

    traces = {role: flo.read_traces([tmp_path / f"{role}.trace"]) for role in cov.ROLES}
    for role, rows in traces.items():
        # Driven: the path waited for the shared file lock, from inside its own functions.
        assert any(r["lock"].startswith("ledger:") and r["blocking"] for r in rows), f"{role} never took a ledger flock"
        missing = set(cov.PATH_FRAMES[role]) - cov.frames(rows)
        assert not missing, f"{role}: the trace never passed through {sorted(missing)}"

    served = traces["served_forget"]
    assert any(r["lock"].startswith("lease:") for r in _with_frame(served, "_preadmit_forget_turn")), \
        "the turn door never armed its admission lease"
    for door in ("_preadmit_forget_turn", "_finish_forget_turn_admission"):
        ledgers = [r for r in _with_frame(served, door) if r["lock"].startswith("ledger:") and r["blocking"]]
        assert ledgers, f"{door} never took a ledger flock"
        if layout != "one_home":
            assert any(any(h.startswith("ledger:") for h in r["held"]) for r in ledgers), \
                f"{door} never held both homes' ledger flocks"
    deleted = traces["project_delete"]
    assert any(r["lock"] == "entries._MEMORY_ENTRY_LOCK" and f"ledger:{home}" in r["held"]
               for r in _with_frame(deleted, "forget_sessions_memory")), "project delete never purged under the fence"
    assert any(r["lock"] == "finalization.A8_TRAVERSAL_LOCK" and f"ledger:{home}" in r["held"]
               for r in _with_frame(deleted, "erase_finalizations_for_session")), "project delete never ran the A8 erase"
    assert any(r["lock"].startswith("ledger:") for r in _with_frame(traces["remember_forget_recall"],
                                                                    "_memory_recall_response")), \
        "the web recall never entered its fence"
    assert any(r["lock"] == "finalization.A8_TRAVERSAL_LOCK" and f"ledger:{home}" in r["held"]
               for r in _with_frame(traces["shadow_dispatch"], "dispatch")), "the shadow never sent under fence and A8"
    found = flo.violations([row for rows in traces.values() for row in rows])
    assert not found, "\n".join(found)


#: What the checker must report for each path's inversion, and in which of the path's functions.
_CAUGHT = {
    "served_forget": ("homes out of sorted order", ("_preadmit_forget_turn",)),
    "project_delete": ("while holding finalization.A8_TRAVERSAL_LOCK",
                       ("forget_sessions_memory", "erase_finalizations_for_session")),
    "remember_forget_recall": ("while holding files._JSONL_LOCK",
                               ("add_memory_fact", "forget_structured_memory", "_memory_recall_response")),
    "shadow_dispatch": ("while holding finalization.A8_TRAVERSAL_LOCK", ("compile", "dispatch")),
}


@pytest.mark.parametrize("role", cov.ROLES)
def test_the_checker_reports_an_inversion_on_each_new_path(tmp_path, role):
    """Detection: a test-only wrapper takes a lock the documented order puts after the path's fence
    (for the served turn, the home that sorts last), then runs the real path inside it."""
    home, other = _homes(tmp_path, "runtime_home_sorts_first")
    data = flo.setup(tmp_path, "setup", home=home, runtime_home=other, n=2, tag="i", entry=cov.ENTRY)
    (tmp_path / "go").write_text("go", encoding="utf-8")
    # The real path and its inverted twin get one item each: the real one consumes its item.
    runs = {"real": (role, data[role][:1]), "inverted": (f"inverted:{role}", data[role][1:])}
    results = {}
    for name, (run, items) in runs.items():  # one after the other: the checker alone must see it
        done, hung, errors = flo.finish({name: flo.worker(tmp_path, name, run, items, home=home, runtime_home=other,
                                                          entry=cov.ENTRY)}, DEADLINE_S)
        assert not hung and not errors, (hung, errors)
        results.update(done)
    real = flo.read_traces([tmp_path / "real.trace"])
    inverted = flo.read_traces([tmp_path / "inverted.trace"])
    assert not flo.violations(real), "\n".join(flo.violations(real))
    assert not set(cov.PATH_FRAMES[role]) - cov.frames(inverted), "the inverted run did not drive the path"
    found = flo.violations(inverted)
    text, (needle, where) = "\n".join(found), _CAUGHT[role]
    for name in where:
        assert any(needle in line and f":{name}" in line for line in found), f"{role}: {needle!r} in {name}:\n{text}"
    if role == "served_forget":
        assert any(f"took ledger:{other} while holding ledger:{home}" in line for line in found), text
        both = flo.violations(real + inverted)
        assert any(line.startswith("cycle across processes:") and f"ledger:{home}" in line
                   and f"ledger:{other}" in line for line in both), "\n".join(both)


def test_two_served_forget_turns_with_crossed_homes_never_deadlock(tmp_path):
    """Turn one: active home A, runtime home B. Turn two: active B, runtime A. The turn door, the
    command fence and the admission retirement each take both ledger flocks, in sorted order."""
    a, b = str((tmp_path / "home-a").resolve()), str((tmp_path / "home-b").resolve())
    first = flo.setup(tmp_path, "setup-1", home=a, runtime_home=b, n=ITEMS, tag="p", entry=cov.ENTRY)
    second = flo.setup(tmp_path, "setup-2", home=b, runtime_home=a, n=ITEMS, tag="q", entry=cov.ENTRY)
    hold = {"hold_first_flock_s": 0.05}
    children = {
        "one": flo.worker(tmp_path, "one", "served_forget", first["served_forget"], home=a, runtime_home=b,
                          entry=cov.ENTRY, **hold),
        "two": flo.worker(tmp_path, "two", "served_forget", second["served_forget"], home=b, runtime_home=a,
                          entry=cov.ENTRY, **hold),
    }
    flo.go(tmp_path, children)
    results, hung, errors = flo.finish(children, DEADLINE_S)
    if hung:
        pytest.fail(f"{', '.join(hung)} still running after {DEADLINE_S:.0f}s (killed): a cross-process deadlock\n"
                    + flo.hang_report(tmp_path, children), pytrace=False)
    order = flo.violations(flo.read_traces([tmp_path / f"{name}.trace" for name in children]))
    assert not order, "\n".join(order + [f"{name} failed: {error}" for name, error in errors.items()])
    assert not errors, errors
    for name in children:
        assert all(reply.startswith("Forget applied") for reply in results[name]), results[name]
    traces = {name: flo.read_traces([tmp_path / f"{name}.trace"]) for name in children}
    for name, rows in traces.items():
        nested = [r for r in _with_frame(rows, "_preadmit_forget_turn") if r["lock"].startswith("ledger:")
                  and r["blocking"] and any(h.startswith("ledger:") for h in r["held"])]
        assert nested, f"{name}: the turn door never held one ledger flock while it waited for the other"
    spans = [(min(r["t"] for r in rows), max(r["t"] for r in rows)) for rows in traces.values()]
    assert max(start for start, _ in spans) < min(end for _, end in spans), "the two turns never ran together"
