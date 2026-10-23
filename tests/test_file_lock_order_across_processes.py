"""The memory and forget paths keep one file-lock order across processes (forget-locks3 review, Low).

The lock-graph review plugin follows the locks of one process. Processes share only file locks:
the forget ledger's flock (`<home>/.forget-authority/lock`, through `revocation._flock`) and the
admission leases beside it. Here the forget, a chat delete, a finalization erase, an upload (with
its serve) and a transcript append run at the same time, each in its own child process over one
home, with every lock acquisition recorded (`tests/_file_lock_order.py`). The joined trace must
follow the documented order: the fence (its process lock and every ledger flock, several homes in
sorted path order) before `chat_attachments._LOCK`, `finalization.A8_TRAVERSAL_LOCK`,
`entries._MEMORY_ENTRY_LOCK` and `files._JSONL_LOCK`; leases private to the process that armed
them; no wait for a ledger flock with a SQLite write transaction open; and no cycle in the waits
of all processes together.

Runs with one home, and with the forget's `runtime_home` a second home that sorts before or after
the active one, so the forget takes two ledger flocks. The last test runs deliberately inverted
helpers (test code only) and checks the same checker reports them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests import _file_lock_order as flo

DEADLINE_S = 180.0
ROLES = ("forget", "chat_delete", "finalization_erase", "upload", "append")
ITEMS = 3


def _homes(tmp_path: Path, layout: str) -> tuple[str, str]:
    active = str((tmp_path / "home-b").resolve())
    other = {"one_home": active, "runtime_home_sorts_first": str((tmp_path / "home-a").resolve()),
             "runtime_home_sorts_last": str((tmp_path / "home-c").resolve())}[layout]
    return active, other


@pytest.mark.parametrize("layout", ["one_home", "runtime_home_sorts_first", "runtime_home_sorts_last"])
def test_every_memory_and_forget_path_keeps_the_file_lock_order_across_processes(tmp_path, layout):
    home, runtime_home = _homes(tmp_path, layout)
    data = flo.setup(tmp_path, "setup", home=home, runtime_home=runtime_home, n=ITEMS, tag="o")
    children = {role: flo.worker(tmp_path, role, role, data[role], home=home, runtime_home=runtime_home)
                for role in ROLES}
    flo.go(tmp_path, children)
    results, hung, errors = flo.finish(children, DEADLINE_S)
    if hung:
        pytest.fail(f"{layout}: {', '.join(hung)} still running after {DEADLINE_S:.0f}s (killed): a cross-process "
                    "lock wait that never ended", pytrace=False)
    assert not errors, errors

    assert all(reply.startswith("Forget applied") for reply in results["forget"]), results["forget"]
    assert results["chat_delete"] == [True] * ITEMS, results
    assert results["finalization_erase"] == [True] * ITEMS, results
    assert results["upload"] == [2] * ITEMS, results
    assert results["append"] == [True] * ITEMS, results

    traces = {role: flo.read_traces([tmp_path / f"{role}.trace"]) for role in ROLES}
    for role, rows in traces.items():
        # Each path really waited for the shared file lock, or the order below says nothing about it.
        assert any(r["lock"].startswith("ledger:") and r["blocking"] for r in rows), f"{role} never took a ledger flock"
    assert any(r["lock"].startswith("lease:") for r in traces["forget"]), "the forget never armed its lease"
    if layout != "one_home":
        nested = [r for r in traces["forget"] if r["lock"].startswith("ledger:")
                  and any(h.startswith("ledger:") for h in r["held"])]
        assert nested, "the forget never held both homes' ledger flocks"
    found = flo.violations([row for rows in traces.values() for row in rows])
    assert not found, "\n".join(found)


def test_the_checker_reports_an_inverted_file_lock_order(tmp_path):
    """Detection: helpers in this test (never production code) take the locks the wrong way round."""
    home, other = _homes(tmp_path, "runtime_home_sorts_first")
    data = flo.setup(tmp_path, "setup", home=home, runtime_home=other, n=1, tag="i")
    (tmp_path / "go").write_text("go", encoding="utf-8")
    runs = {
        "forget": ("forget", data["forget"], other),
        "inverted_fence": ("inverted_fence", [None], home),
        "inverted_homes": ("inverted_homes", [[home, other]], home),
        "inverted_sqlite": ("inverted_sqlite", [None], home),
    }
    # One after another: run together, the inverted pair of homes could really deadlock against the
    # forget, which is the race test's subject. Here the checker alone must see the inversion.
    results = {}
    for name, (role, items, runtime_home) in runs.items():
        done, hung, errors = flo.finish({name: flo.worker(tmp_path, name, role, items, home=home,
                                                          runtime_home=runtime_home)}, DEADLINE_S)
        assert not hung and not errors, (hung, errors)
        results.update(done)
    assert results["forget"][0].startswith("Forget applied"), results

    clean = flo.violations(flo.read_traces([tmp_path / "forget.trace"]))
    assert not clean, "\n".join(clean)
    found = flo.violations(flo.read_traces([tmp_path / f"{name}.trace" for name in runs]))
    text = "\n".join(found)
    assert any(f"took ledger:{home} while holding entries._MEMORY_ENTRY_LOCK" in line for line in found), text
    assert any(f"took ledger:{other} while holding ledger:{home}: homes out of sorted order" in line
               for line in found), text
    assert any(f"waited for ledger:{home} with a SQLite write transaction open" in line for line in found), text
    assert any(line.startswith("cycle across processes:") and f"ledger:{home}" in line and f"ledger:{other}" in line
               for line in found), text
