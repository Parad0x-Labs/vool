"""A served-rig step that fails must carry the daemon's measured state and evidence.

A corrected-gate claim-window run failed at page.goto with ``ERR_CONNECTION_REFUSED`` and
no daemon-side evidence existed with which to classify the refusal: the server routes its
logging into the disposable home's file, that home dies with the test, and nothing read
the process's exit status. The cause of that refusal remains unclassified; what was
missing was the ability to classify it.

``ServedDaemon`` therefore owns two diagnostic seams, pinned here from both sides:
``assert_running`` (a boundary precheck: an exited daemon fails as its exit status with
redacted server evidence) and ``evidence_on_failure`` (a wrapper that attaches the
daemon's process status and redacted server log tail to a failed navigation or request
step's OWN exception, preserving its type and traceback). The controls are deterministic:
process state is set by direct signal, and the failing step is a loopback request.
Nothing here classifies a failure's cause; the contract is accurate reporting.
"""
from __future__ import annotations

from urllib.error import URLError
from urllib.request import urlopen

import pytest

from tests import _reader_served_rig as rig


def _notes(exc: BaseException) -> str:
    return "\n".join(str(note) for note in getattr(exc, "__notes__", ()))


def test_an_alive_daemon_passes_the_boundary_check(tmp_path):
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            assert daemon.assert_running(context="boundary check") is None
        finally:
            daemon.stop()


def test_an_exited_daemon_fails_the_boundary_check_with_evidence(tmp_path):
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            # A hard kill leaves no cleanup and no shutdown log: the state to report is
            # the exit status plus whatever the server's own log still holds.
            daemon.process.kill()
            daemon.process.wait(timeout=20)
            with pytest.raises(AssertionError) as caught:
                daemon.assert_running(context="initial browser navigation")
            message = str(caught.value)
            assert "exited rc=-9" in message, message
            assert "initial browser navigation" in message, message
            # Measured state only -- the failure does not pre-classify the cause.
            assert "not a" not in message, message
            # The evidence rides along, redacted for a public artifact: no machine path.
            assert "vool_api.log" in message or "daemon.log" in message, message
            assert str(tmp_path) not in message
            assert "/Users/" not in message
        finally:
            daemon.stop()


def test_a_daemon_exiting_after_the_precheck_fails_the_step_with_its_evidence(tmp_path):
    """The precheck passed, the process exited before the step, the step then failed: the
    step's OWN exception type survives, and its note names the exit status and evidence."""
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            assert daemon.assert_running(context="post-turn queue read") is None
            daemon.process.kill()
            daemon.process.wait(timeout=20)
            with pytest.raises(URLError) as caught:
                with daemon.evidence_on_failure("post-turn queue read"):
                    with urlopen(daemon.base_url + "/healthz", timeout=3):
                        pass
            assert type(caught.value) is URLError
            note = _notes(caught.value)
            assert "exited rc=-9" in note, note
            assert "post-turn queue read" in note, note
            assert "vool_api.log" in note or "daemon.log" in note, note
            assert str(tmp_path) not in note
            assert "/Users/" not in note
        finally:
            daemon.stop()


def test_a_transport_failure_while_the_daemon_lives_reports_it_alive(tmp_path):
    """A refused transport with the daemon's process still alive must be reported as
    alive -- never as an exit -- with the live daemon's own evidence attached."""
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            assert daemon.assert_running(context="transport-failure control") is None
            unbound = rig.free_port()
            with pytest.raises(URLError) as caught:
                with daemon.evidence_on_failure("transport-failure control"):
                    with urlopen(f"http://127.0.0.1:{unbound}/healthz", timeout=3):
                        pass
            assert type(caught.value) is URLError
            note = _notes(caught.value)
            assert "process alive" in note, note
            assert "exited rc=" not in note, note
            assert "vool_api.log" in note or "daemon.log" in note, note
            assert str(tmp_path) not in note
            assert "/Users/" not in note
            # The daemon was not the thing that failed here: it still answers.
            with urlopen(daemon.base_url + "/healthz", timeout=3) as response:
                assert response.status == 200
        finally:
            daemon.stop()
