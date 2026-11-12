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

The wrapper promises that the step's OWN exception survives, and a diagnostic that fails
must not break that promise. Before the evidence note went through ``attach_evidence_note``,
a capture that raised (reading the process state or the log tail) or a note the exception
refused to take escaped from the ``except`` arm and REPLACED the step's error with the
diagnostic's own. The controls below pin the law: the original exception object, type and
traceback always propagate; a failed capture or attachment reports itself in the note (or on
standard error) and never replaces the step's error. Injected diagnostic faults are labeled
CONTROL.
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


def test_a_failed_evidence_capture_never_replaces_the_step_error(tmp_path, monkeypatch):
    """CONTROL: the capture itself fails; the step's OWN error must still survive.

    ``await_failure_log_tail`` raises an injected fault while the daemon is alive.
    The wrapper's whole value is evidence on failure, so its capture failing is the
    worst case: the note must report the capture failure (measured state still
    included, no fabricated log evidence), and the original exception object, type
    and traceback must propagate unchanged.
    """

    def injected_capture_fault(*_args, **_kwargs):
        raise RuntimeError("CONTROL injected diagnostic-capture fault")

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            monkeypatch.setattr(daemon, "await_failure_log_tail", injected_capture_fault)
            original = URLError("controlled transport failure")
            with pytest.raises(URLError) as caught:
                with daemon.evidence_on_failure("capture-failure control"):
                    raise original
            assert caught.value is original
            assert type(caught.value) is URLError
            note = _notes(caught.value)
            assert "capture failed" in note, note
            assert "RuntimeError" in note, note
            assert "process alive" in note, note
            assert "vool_api.log" not in note and "daemon.log" not in note, note
            assert str(tmp_path) not in note
            assert "/Users/" not in note
            # No retry, no state change: the daemon was never the thing that failed.
            with urlopen(daemon.base_url + "/healthz", timeout=3) as response:
                assert response.status == 200
        finally:
            daemon.stop()


def test_a_failed_note_attachment_never_replaces_the_step_error(tmp_path, capsys):
    """CONTROL: the note cannot be attached; the step's OWN error must still survive.

    The marker refuses ``add_note``. The evidence then rides the fallback seam
    (standard error, redacted like every public artifact), and the marker's object
    identity, type and traceback propagate unchanged.
    """

    class AttachmentRefused(URLError):
        def add_note(self, *_args, **_kwargs):
            raise AttributeError("'AttachmentRefused' object refuses add_note")

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            original = AttachmentRefused("controlled attachment fault")
            with pytest.raises(URLError) as caught:
                with daemon.evidence_on_failure("attachment-failure control"):
                    raise original
            assert caught.value is original
            assert type(caught.value) is AttachmentRefused
            # The evidence stayed visible on the fallback seam, still redacted.
            fallback = capsys.readouterr().err
            assert "[served-daemon]" in fallback, fallback
            assert "process alive" in fallback, fallback
            assert str(tmp_path) not in fallback
            assert "/Users/" not in fallback
            # The attachment failure changed nothing else: the daemon still answers.
            with urlopen(daemon.base_url + "/healthz", timeout=3) as response:
                assert response.status == 200
        finally:
            daemon.stop()


def test_a_healthy_wrapped_step_emits_no_evidence(tmp_path, capsys):
    """The wrapper is silent on success: no exception, no note, no output."""
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=60)
            with daemon.evidence_on_failure("healthy step control"):
                with urlopen(daemon.base_url + "/healthz", timeout=3) as response:
                    assert response.status == 200
            assert capsys.readouterr().err == ""
        finally:
            daemon.stop()
