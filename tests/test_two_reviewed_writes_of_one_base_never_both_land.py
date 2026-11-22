"""Two writes approved against the same reviewed bytes never both land.

CI 44970482057 (shard 8), `test_two_processes_racing_two_approvals_of_one_base_land_exactly_one`:
both racing writers read the reviewed bytes, both passed the check, and both renamed their file
into place; the one that renamed first was then reported as "bytes_diverged" although its write
had already happened. The check and the rename now run under one lock on the pinned folder.
Here the window between the check and the rename is held open on purpose, so the outcome does not
depend on timing luck.
"""
from __future__ import annotations

import hashlib
import threading

from core.execution import artifacts
from core.execution.artifacts import ReviewedBaseChangedError, pinned_atomic_write_text


def test_the_second_writer_sees_the_first_ones_bytes_and_is_refused(tmp_path, monkeypatch):
    target = tmp_path / "inventory.py"
    target.write_text("stock = 1\n", encoding="utf-8")
    reviewed = hashlib.sha256(target.read_bytes()).hexdigest()
    both_checked = threading.Barrier(2)
    real = artifacts._pinned_entry_sha256

    def check_then_hold(name, fd):
        value = real(name, fd)
        try:
            # Without the lock both writers stand here together, each having passed the check.
            both_checked.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        return value

    monkeypatch.setattr(artifacts, "_pinned_entry_sha256", check_then_hold)
    outcomes: dict[str, str] = {}

    def write(label: str, content: str) -> None:
        try:
            pinned_atomic_write_text(target, content, expected_prior_sha256=reviewed)
            outcomes[label] = "written"
        except ReviewedBaseChangedError:
            outcomes[label] = "stale_base"

    threads = [threading.Thread(target=write, args=(label, f"stock = {n}\n")) for label, n in (("a", 2), ("b", 3))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(outcomes.values()) == ["stale_base", "written"], outcomes
    winner = next(label for label, outcome in outcomes.items() if outcome == "written")
    assert target.read_text(encoding="utf-8") == {"a": "stock = 2\n", "b": "stock = 3\n"}[winner]
