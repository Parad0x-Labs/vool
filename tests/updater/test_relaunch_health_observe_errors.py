"""A slow process listing during the health wait is not taken as the new version's verdict."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

from core.updater import relaunch_health


class _Helper:
    def __init__(self):
        self.calls = 0

    def observe_relaunch(self):
        self.calls += 1
        if self.calls <= 3:
            raise subprocess.TimeoutExpired(["ps"], 5)
        return "up"


def test_a_ps_timeout_keeps_waiting_and_a_healthy_version_still_wins() -> None:
    helper = _Helper()
    answers = iter([False, False, False, True])

    def probe(*, timeout, expected_version):
        return SimpleNamespace(ok=next(answers))

    health = relaunch_health.await_relaunch_health(
        probe, helper, health_timeout=30.0, expected_version="0.7.1", start_bound=30.0)

    assert health.ok
    assert helper.calls == 3
