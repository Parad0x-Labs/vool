"""Atomicity guarantees for the per-node mesh credit ledger.

Pins that a re-settled verified proof credits exactly once (idempotent earn) and that
concurrent spends cannot overdraw the balance below zero (atomic check-and-spend), closing
the settlement double-spend the audit flagged.
"""
from __future__ import annotations

import contextlib
import hashlib
import threading
import uuid

from core.mesh.credit_ledger import CreditLedger
from storage.migrations import run_migrations


def _node() -> str:
    # A fresh node_id isolates each test (the ledger keys every row by node_id).
    return f"node-{uuid.uuid4().hex}"


def _proof(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def test_earn_is_idempotent_on_proof_hash():
    run_migrations()
    ledger = CreditLedger(_node())
    proof = _proof("task-1-result")
    first = ledger.earn("task-1", 5.0, proof)
    second = ledger.earn("task-1", 5.0, proof)  # replay of the SAME verified proof
    assert ledger.balance() == 5.0                # credited once, not twice
    assert second.entry_id == first.entry_id      # returns the canonical existing entry


def test_spend_cannot_overdraw():
    run_migrations()
    ledger = CreditLedger(_node())
    ledger.earn("t", 5.0, _proof("earn-5"))
    ledger.spend("t", 5.0, "peer-x")              # exactly affordable
    assert ledger.balance() == 0.0
    try:
        ledger.spend("t", 1.0, "peer-x")          # nothing left
        raise AssertionError("expected insufficient-credits ValueError")
    except ValueError as exc:
        assert "insufficient" in str(exc)
    assert ledger.balance() == 0.0                # never negative


def test_concurrent_spends_do_not_overdraw():
    run_migrations()
    node = _node()
    CreditLedger(node).earn("t", 5.0, _proof("earn-5-conc"))

    start = threading.Barrier(6)
    successes: list[int] = []
    lock = threading.Lock()

    def _try_spend(i: int) -> None:
        start.wait()
        try:
            CreditLedger(node).spend("t", 5.0, f"peer-{i}")
            with lock:
                successes.append(i)
        except ValueError:
            pass

    threads = [threading.Thread(target=_try_spend, args=(i,)) for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Only one 5-credit spend can succeed against a 5-credit balance, and the balance is
    # never driven negative regardless of the race.
    assert len(successes) == 1, f"expected exactly one winning spend, got {successes}"
    assert CreditLedger(node).balance() == 0.0


def test_concurrent_earns_of_same_proof_credit_once():
    run_migrations()
    node = _node()
    proof = _proof("earn-idem-conc")
    start = threading.Barrier(6)

    def _try_earn() -> None:
        start.wait()
        with contextlib.suppress(Exception):
            CreditLedger(node).earn("t", 5.0, proof)

    threads = [threading.Thread(target=_try_earn) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert CreditLedger(node).balance() == 5.0  # exactly one credit despite the race
