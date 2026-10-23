"""Bound unauthenticated work before persistence; retain counted refusal evidence."""
from __future__ import annotations

import threading
import time


class CompanionIngress:
    """Coalesce unauthenticated refusals into counted receipts.

    Nothing here admits or refuses a request: a cap charged before authentication
    lets junk from other addresses (or one relay room) lock the paired phone out.
    Writes stay bounded because refusal kinds are fixed and counted, not appended.
    """

    WINDOW = 60
    RECEIPTS_PER_WINDOW = 8

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.writes = 0
        self.emitted = set()
        self.pending = {}

    def refused(self, kind, *, reason):
        # Call sites choose fixed kinds/reasons. Attacker-supplied device IDs,
        # payloads and addresses never become durable keys or receipt fields.
        key = (kind, reason)
        with self.lock:
            now = time.monotonic()
            if now - self.started >= self.WINDOW:
                self.started, self.writes = now, 0
                self.emitted.clear()
            self.pending[key] = self.pending.get(key, 0) + 1
            if self.writes < self.RECEIPTS_PER_WINDOW and (key not in self.emitted or self.pending[key] >= 64):
                self._emit(key)

    def _emit(self, key):
        count = self.pending.pop(key, 0)
        if count:
            self.store.append_receipt(key[0], detail={"reason": key[1], "count": count})
            self.writes += 1
            self.emitted.add(key)

    def flush(self):
        """Owner receipt reads flush the bounded pending counters, never a LAN request."""
        with self.lock:
            for key in list(self.pending):
                self._emit(key)
