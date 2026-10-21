"""The forget fence's place in the lock order is a public revocation API (REVIEW-1 of forget-locks, N1).

`chat_attachments._ledger_first` held the private `revocation._LOCK` directly, so the order
"fence, then a module lock" silently depended on revocation's internals. `revocation.fence_first()`
states it: it holds the very lock `serialized()` takes, and keeps holding it when the ledger's file
lock cannot be taken (the fenced read or write inside reports that itself, fail closed).
"""
from __future__ import annotations

import inspect
import threading

import pytest

from core import chat_attachments as ca
from core.memory import revocation
from tests.test_question_date_time_leg_20261002 import _profile


def _blocked_while(context, contender) -> bool:
    """Whether `contender`, run in another thread, waits while `context` is held, then finishes."""
    done = threading.Event()
    errors: list[BaseException] = []

    def run():
        try:
            contender()
        except BaseException as exc:  # noqa: BLE001 -- an unopenable ledger raises once let through
            errors.append(exc)
        done.set()

    with context:
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        waited = not done.wait(0.5)
    thread.join(10)
    assert not thread.is_alive(), "the contender never finished after the fence was released"
    return waited


def test_fence_first_holds_the_lock_serialized_takes(tmp_path):
    _profile(tmp_path)

    def contender():
        with revocation.serialized():
            pass

    assert _blocked_while(revocation.fence_first(), contender)


def test_fence_first_keeps_the_order_when_the_ledger_cannot_be_locked(tmp_path, monkeypatch):
    _profile(tmp_path)

    def broken(handle, *, blocking=True):
        raise OSError("the ledger lock cannot be taken")

    monkeypatch.setattr(revocation, "_flock", broken)

    def contender():
        with revocation.serialized():
            pass

    # Entered without raising; another fenced caller still waits for it, then fails closed itself.
    assert _blocked_while(revocation.fence_first(), contender)
    with pytest.raises(OSError):
        with revocation.serialized():
            pass


def test_the_attachment_store_uses_the_public_order_not_revocations_private_lock():
    assert "revocation._LOCK" not in inspect.getsource(ca)
