"""TEST LAUNCHER for the served UsePod drive. Not a runtime entry point, and never shipped.

It installs one labelled monetary TEST DOUBLE, which grants every reservation and journals each
reserve / mark_dispatched / settle / retain_unknown / release_unsent call as a JSON line in the file
named by ``USEPOD_SERVED_DOUBLE_JOURNAL``, then runs the real daemon entry point
(``apps.vool_api_server``) unchanged in this process. Nothing is paid and no wallet exists here; the
label ``test_double:served_journaling_monetary_authority`` is written into every receipt.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


class JournalingMonetaryTestDouble:
    """TEST DOUBLE: grants every reservation and journals each call. Not a monetary authority."""

    label = "test_double:served_journaling_monetary_authority"

    def __init__(self, journal: Path) -> None:
        self._journal = journal
        self._lock = threading.Lock()
        self._issued = 0

    def _write(self, call: str, **fields: Any) -> None:
        line = json.dumps({"call": call, "at": time.time(), **fields}, sort_keys=True, default=str)
        with self._lock, self._journal.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def reserve(self, liability: Any) -> Any:
        from core.usepod.monetary import MonetaryReservation

        with self._lock:
            self._issued += 1
            reservation_id = f"served-test-double-{self._issued}"
        self._write("reserve", reservation_id=reservation_id, liability=liability.as_dict())
        return MonetaryReservation(reservation_id, self.label, liability, time.time())

    def mark_dispatched(self, reservation: Any) -> None:
        self._write("mark_dispatched", reservation_id=reservation.reservation_id)

    def settle(self, reservation: Any, evidence: Any) -> None:
        self._write("settle", reservation_id=reservation.reservation_id, evidence=evidence.as_dict())

    def retain_unknown(self, reservation: Any, evidence: Any) -> None:
        self._write("retain_unknown", reservation_id=reservation.reservation_id, evidence=evidence.as_dict())

    def release_unsent(self, reservation: Any, *, reason: str) -> None:
        self._write("release_unsent", reservation_id=reservation.reservation_id, reason=reason)


def main() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from core.usepod.monetary import install_monetary_authority

    double = JournalingMonetaryTestDouble(Path(os.environ["USEPOD_SERVED_DOUBLE_JOURNAL"]))
    install_monetary_authority(double, label=double.label)
    sys.argv = ["apps.vool_api_server", *sys.argv[1:]]
    runpy.run_module("apps.vool_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
