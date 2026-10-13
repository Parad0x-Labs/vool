"""The API server's fatal-signal diagnostics arm is VERIFIED, never assumed.

``_arm_fatal_signal_diagnostics`` exists so a native SIGSEGV-class fault dumps every
thread's Python stack instead of vanishing; a silent arming failure would drop that
diagnostic without a trace -- the exact failure mode the function exists to remove. These
controls pin the verification contract in both directions: a real arming reports True with
``faulthandler.is_enabled()`` actually true, and a refused or ineffective arming reports
False instead of raising or claiming success. The entrypoint check keeps main() wired to
the verifying arm rather than a bare ``faulthandler.enable``.
"""
from __future__ import annotations

import faulthandler
import inspect

from apps import vool_api_server


def test_arming_reports_true_only_when_actually_enabled():
    was_enabled = faulthandler.is_enabled()
    try:
        assert vool_api_server._arm_fatal_signal_diagnostics() is True
        assert faulthandler.is_enabled() is True
    finally:
        if not was_enabled:
            faulthandler.disable()


def test_a_refused_arming_is_reported_false_and_never_raises(monkeypatch):
    def refused(*_args, **_kwargs):
        raise RuntimeError("handler installation refused")

    monkeypatch.setattr(faulthandler, "enable", refused)
    assert vool_api_server._arm_fatal_signal_diagnostics() is False


def test_an_ineffective_arming_is_reported_false(monkeypatch):
    monkeypatch.setattr(faulthandler, "enable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    assert vool_api_server._arm_fatal_signal_diagnostics() is False


def test_main_wires_the_verified_arm_before_serving():
    source = inspect.getsource(vool_api_server.main)
    arm_call = source.index("_arm_fatal_signal_diagnostics()")
    assert arm_call < source.index("preflight(")
    assert "faulthandler.enable" not in source
