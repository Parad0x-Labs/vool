"""A ratings read or change asked while another one holds the store's lock waits for it instead of failing.

CI shard 7 on 571e05675: on the settings page a project's rating ("Keep it exact.") never appeared. The store's lock never
waited, so a list asked while another request held it answered "store unreadable" at once and the page drew a failure.
"""
from __future__ import annotations

import fcntl
import threading
import time

from tests._credential_intelligence_support import isolated_home, vault_home


def _hold_the_lock(seconds: float) -> threading.Event:
    from core import x_feedback
    from core.runtime_paths import active_vool_home

    path = (active_vool_home() / x_feedback.STORE_FILENAME).with_suffix('.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    held = threading.Event()

    def hold() -> None:
        with path.open('w') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            held.set()
            time.sleep(seconds)

    threading.Thread(target=hold, daemon=True).start()
    assert held.wait(5)
    return held


def test_a_list_asked_while_the_lock_is_held_waits_and_reads(vault_home):
    from core import x_feedback

    assert x_feedback.save('project:pear', 7, 'A pear note.', comment='Short and plain.')['ok']
    _hold_the_lock(0.4)
    listed = x_feedback.inspect('project:pear')
    assert listed['ok'] and [row['comment'] for row in listed['entries']] == ['Short and plain.']


def test_a_save_asked_while_the_lock_is_held_waits_and_saves(vault_home):
    from core import x_feedback

    _hold_the_lock(0.4)
    assert x_feedback.save('global', 8, 'A plum note.')['ok']
    assert len(x_feedback.inspect('global')['entries']) == 1


def test_a_lock_held_past_the_wait_still_fails_closed(vault_home, monkeypatch):
    from core import x_feedback

    assert x_feedback.save('global', 6, 'A fig note.')['ok']
    monkeypatch.setattr(x_feedback, '_LOCK_WAIT_S', 0.1)
    _hold_the_lock(1.0)
    assert x_feedback.inspect('global') == {'ok': False, 'status': 'store_unreadable', 'entries': []}
