"""A daemon that dies mid-test must fail AS a dead daemon, carrying its own evidence.

The 2026-10-02 corrected combined gate lost the claim-window test's daemon between
certification and page.goto: the page answered ``net::ERR_CONNECTION_REFUSED`` and nothing
else existed -- the server routes its logging into the disposable home's file, that home died
with the test, and no exit status was read by anyone. The failure was effectively
unattributable, which is what let "transient" stand in for a cause.

``ServedDaemon.assert_running`` is the repaired contract: at the boundary where a test is
about to depend on the daemon, a dead process raises with its exit status plus the rig's
redacted server-log tail -- the same capture law ``certify()`` already applies to transport
loss. This file pins that law from both sides: an alive daemon passes silently, and a
SIGKILLed one (the silent-death signature: no cleanup, no shutdown log) fails naming the
daemon, the rc, and redacted evidence without leaking the machine's home path.
"""
from __future__ import annotations

import signal

import pytest

from tests import _reader_served_rig as rig


def test_an_alive_daemon_passes_the_boundary_check(tmp_path):
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            assert daemon.assert_running(context="boundary check") is None
        finally:
            daemon.stop()


def test_a_sigkilled_daemon_fails_as_daemon_death_with_evidence(tmp_path):
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            # The observed 2026-10-02 signature: the process vanishes without cleanup,
            # without a shutdown log, holding a port the browser is about to need.
            daemon.process.kill()
            daemon.process.wait(timeout=20)
            with pytest.raises(AssertionError) as caught:
                daemon.assert_running(context="initial browser navigation")
            message = str(caught.value)
            assert "rc=-9" in message, message
            assert "daemon death" in message, message
            assert "initial browser navigation" in message, message
            # The evidence rides along, redacted for a public artifact: no machine path.
            assert "vool_api.log" in message or "daemon.log" in message, message
            assert str(tmp_path) not in message
            assert "/Users/" not in message
        finally:
            daemon.stop()
