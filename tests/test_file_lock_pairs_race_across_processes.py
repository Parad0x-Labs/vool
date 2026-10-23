"""Two processes that each take two file locks never deadlock (forget-locks3 review, Low).

The paths that hold one file lock while they wait for another are:

- a forget whose `runtime_home` is not the active home: `_command_erasure_fence` takes both homes'
  ledger flocks, in sorted path order;
- a serving read across two homes: `revocation.revoked_tokens(chat, runtime_home)` takes them in
  the same sorted order;
- any forget: under its ledger flock it arms an admission lease (`prepare`), a second flock, on a
  file it has just created; other processes only try leases (`_check_leases`, `LOCK_NB`), and at
  exit `_clean_exit` takes the ledger flock while it still has its lease.

Each pair runs in two child processes started together, with the first ledger flock of every
nesting held a little longer to widen the window. A child still running at the deadline is killed
and the test fails: a hang cannot wedge the session. The recorded traces must also follow the
order (`tests/_file_lock_order.py`). The control at the end runs a deliberately inverted pair of
test helpers (never production code) and checks that this harness does see that deadlock.
"""
from __future__ import annotations

import pytest

from tests import _file_lock_order as flo

DEADLINE_S = 120.0
HOLD_S = 0.05
ITEMS = 3
READS = 40


def _nested(rows) -> list[dict]:
    return [r for r in rows if r["lock"].startswith("ledger:") and r["blocking"]
            and any(h.startswith("ledger:") for h in r["held"])]


def _run(tmp_path, specs: dict, deadline_s: float):
    children = {name: flo.worker(tmp_path, name, role, items, home=home, runtime_home=runtime_home, **extra)
                for name, (role, items, home, runtime_home, extra) in specs.items()}
    flo.go(tmp_path, children)
    return flo.finish(children, deadline_s)


@pytest.mark.parametrize("pair", ["forget_vs_forget_crossed_homes", "forget_vs_two_home_read",
                                  "forget_vs_forget_one_home"])
def test_two_processes_each_taking_two_file_locks_never_deadlock(tmp_path, pair):
    a, b = str((tmp_path / "home-a").resolve()), str((tmp_path / "home-b").resolve())
    hold = {"hold_first_flock_s": HOLD_S}
    if pair == "forget_vs_forget_crossed_homes":
        first = flo.setup(tmp_path, "setup-1", home=a, runtime_home=b, n=ITEMS, tag="p")
        second = flo.setup(tmp_path, "setup-2", home=b, runtime_home=a, n=ITEMS, tag="q")
        specs = {"one": ("forget", first["forget"], a, b, hold), "two": ("forget", second["forget"], b, a, hold)}
    elif pair == "forget_vs_two_home_read":
        first = flo.setup(tmp_path, "setup-1", home=a, runtime_home=b, n=ITEMS, tag="p")
        reads = [[first["forget"][i % ITEMS][0], a] for i in range(READS)]
        specs = {"one": ("forget", first["forget"], a, b, hold), "two": ("two_home_read", reads, b, b, hold)}
    else:
        first = flo.setup(tmp_path, "setup-1", home=a, runtime_home=a, n=ITEMS, tag="p")
        second = flo.setup(tmp_path, "setup-2", home=a, runtime_home=a, n=ITEMS, tag="q")
        specs = {"one": ("forget", first["forget"], a, a, hold), "two": ("forget", second["forget"], a, a, hold)}

    results, hung, errors = _run(tmp_path, specs, DEADLINE_S)
    if hung:
        pytest.fail(f"{pair}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s (killed): a cross-process "
                    "deadlock between two file locks", pytrace=False)
    # The order first: a child that failed still left the trace of what it took until then.
    order = flo.violations(flo.read_traces([tmp_path / f"{name}.trace" for name in specs]))
    assert not order, "\n".join(order + [f"{name} failed: {error}" for name, error in errors.items()])
    assert not errors, errors
    for name, (role, *_rest) in specs.items():
        if role == "forget":
            assert all(reply.startswith("Forget applied") for reply in results[name]), results[name]
        else:
            assert len(results[name]) == READS, results[name]

    traces = {name: flo.read_traces([tmp_path / f"{name}.trace"]) for name in specs}
    for name, rows in traces.items():
        if pair == "forget_vs_forget_one_home":
            assert any(r["lock"].startswith("lease:") for r in rows), f"{name} never armed a lease"
        else:
            assert _nested(rows), f"{name} never held one ledger flock while it waited for the other"
    spans = [(min(r["t"] for r in rows), max(r["t"] for r in rows)) for rows in traces.values()]
    assert max(start for start, _ in spans) < min(end for _, end in spans), f"{pair}: the two never ran together"
    found = flo.violations([row for rows in traces.values() for row in rows])
    assert not found, "\n".join(found)


def test_the_race_harness_sees_a_deadlock_between_two_inverted_file_locks(tmp_path):
    """Detection: one helper takes the homes in sorted order, the other on purpose the other way round."""
    a, b = str((tmp_path / "home-a").resolve()), str((tmp_path / "home-b").resolve())
    hold = {"hold_first_flock_s": 1.0}
    specs = {"ordered": ("ordered_homes", [[a, b]], a, a, hold), "inverted": ("inverted_homes", [[a, b]], a, a, hold)}
    results, hung, errors = _run(tmp_path, specs, 15.0)
    assert sorted(hung) == ["inverted", "ordered"], (results, hung, errors)
    found = flo.violations(flo.read_traces([tmp_path / f"{name}.trace" for name in specs]))
    text = "\n".join(found)
    assert any(f"took ledger:{a} while holding ledger:{b}: homes out of sorted order" in line for line in found), text
    assert any(line.startswith("cycle across processes:") for line in found), text
