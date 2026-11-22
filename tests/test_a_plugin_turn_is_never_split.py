"""A turn that needs an added plugin's tools is never split into steps that cannot offer it.

0.8 release candidate a7b18403 (2026-11-22, real local model, a parcel lookup plugin enabled):
"could you check on shipment PX-3065-D for me, has it arrived anywhere yet" required the plugin
toolset, was planned by the conductor into two explanation steps, and was answered "I currently do
not have access to real-time tracking information" without the plugin being offered. The
conductor, the demand seam and the turn planner all ask `_skill_keeps_turn_whole` first.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.agent_runtime.turn_planner as turn_planner
import core.execution_requirements as requirements
import core.tool_offer_assembly as offers
from core.agent_runtime import agent

ASK = "could you check on shipment PX-3065-D for me, has it arrived anywhere yet"


@pytest.fixture
def several(monkeypatch):
    monkeypatch.setattr(turn_planner, "turn_may_hold_several_requests", lambda _text: True)
    monkeypatch.setattr(offers, "skill_picks_turn", lambda _text, source_context=None: False)


def _toolsets(monkeypatch, toolsets):
    monkeypatch.setattr(
        requirements, "_compute_requirements",
        lambda _text, source_context=None: SimpleNamespace(allowed_toolsets=tuple(toolsets)),
    )


def test_a_turn_the_requirements_send_to_a_plugin_stays_whole(several, monkeypatch):
    _toolsets(monkeypatch, ["plugin"])
    assert agent._skill_keeps_turn_whole(ASK, {}) is True


@pytest.mark.parametrize("toolsets", [[], ["web"], ["plugin", "web"], ["files"]])
def test_other_turns_are_left_to_the_planner(several, monkeypatch, toolsets):
    _toolsets(monkeypatch, toolsets)
    assert agent._skill_keeps_turn_whole(ASK, {}) is False


def test_a_failed_skill_reading_does_not_hide_a_plugin_turn(several, monkeypatch):
    def broken(_text, source_context=None):
        raise RuntimeError("skill index unreadable")

    monkeypatch.setattr(offers, "skill_picks_turn", broken)
    _toolsets(monkeypatch, ["plugin"])
    assert agent._skill_keeps_turn_whole(ASK, {}) is True


def test_a_failed_requirement_reading_leaves_the_turn_to_its_lanes(several, monkeypatch):
    def broken(_text, source_context=None):
        raise RuntimeError("classifier failed")

    monkeypatch.setattr(requirements, "_compute_requirements", broken)
    assert agent._skill_keeps_turn_whole(ASK, {}) is False


def test_a_one_request_turn_pays_nothing(monkeypatch):
    monkeypatch.setattr(turn_planner, "turn_may_hold_several_requests", lambda _text: False)

    def never(*_args, **_kwargs):
        raise AssertionError("read for a one-request turn")

    monkeypatch.setattr(requirements, "_compute_requirements", never)
    monkeypatch.setattr(offers, "skill_picks_turn", never)
    assert agent._skill_keeps_turn_whole("hi", {}) is False
