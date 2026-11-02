"""Privacy review 2026-11-02 of the default-on clean loop: an off switch the code cannot read is off.

THE DEFECT (delivery/clean-loop-08-default-on-20261101 @ 94ee1728)
------------------------------------------------------------------
Default-on was written as "on unless the value is one of 0/false/no/off". Every other value the
owner may write to turn it off ("disabled", "never", "none", "n", "local", "0 # off") read as ON.
For ``VOOL_CLEAN_LOOP_CLOUD`` that sends the owner's file lines to their cloud model after they
tried to switch it off. Unset or empty keeps the default (on), a known yes is on, and anything
else is off.
"""
from __future__ import annotations

from core.clean_loop import brain, route

_NOT_A_YES = ("disabled", "disable", "never", "none", "n", "local", "local-only", "0 # off", "nope", "x")


def test_an_unreadable_cloud_switch_value_keeps_the_cloud_model_out():
    """Fails before the fix: each value below left the owner's cloud model running the loop."""
    for value in _NOT_A_YES:
        assert not brain.cloud_enabled({brain.CLOUD_ENV: value}), value


def test_an_unreadable_loop_switch_value_turns_the_loop_off():
    """Fails before the fix: each value below kept the loop (and, by default, its cloud brain) on."""
    for value in _NOT_A_YES:
        assert not route.flag_on({route.FLAG_ENV: value}), value


def test_the_default_and_each_known_yes_stay_on():
    for check, name in ((brain.cloud_enabled, brain.CLOUD_ENV), (route.flag_on, route.FLAG_ENV)):
        assert check({}) and check({name: ""}) and check({name: "  "})
        for value in ("1", "true", "yes", "on", " TRUE ", "On"):
            assert check({name: value}), (name, value)
