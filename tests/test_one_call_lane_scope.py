"""The one-call lane's scope and decisions (core.one_call_lane), in process.

The served behaviour is driven in tests/test_one_call_owner_local_lane.py. Here: who gets the lane
(the owner's own local turn on a local model, flag on; never a remote or phone session, a cloud
model or a tools-forbidden turn), what a prose round is, and which seats a not-connected source
loses.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import one_call_lane as lane
from core.runtime_flags import override

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-one", "turn_id": "t-one"}
REMOTE = {**OWNER, "_owner_local": False}
LOCAL = SimpleNamespace(provider_name="ollama-local", model_name="qwen3:8b", adapter_type="openai_compatible",
                        source_type="http", runtime_config={"base_url": "http://127.0.0.1:11434"}, metadata={})
CLOUD = SimpleNamespace(provider_name="openrouter", model_name="some/cloud-model", adapter_type="openai_compatible",
                        source_type="http", runtime_config={"base_url": "https://openrouter.ai/api/v1"},
                        metadata={"cost_class": "paid_cloud"})
ASK = "wie voll ist eigentlich meine Platte?"


@pytest.fixture
def registry(monkeypatch):
    def _set(*manifests):
        monkeypatch.setattr("core.model_registry.ModelRegistry.list_manifests",
                            lambda self, enabled_only=False, limit=256: list(manifests))
        monkeypatch.setattr("core.auto_local_only_mode.turn_is_local_only", lambda _ctx: False)
    return _set


def test_the_owner_local_turn_on_a_local_model_gets_the_lane(registry) -> None:
    registry(LOCAL)
    with override(lane.FLAG, True):
        assert lane.one_call_lane_applies(ASK, dict(OWNER))


def test_flag_off_no_lane(registry) -> None:
    registry(LOCAL)
    with override(lane.FLAG, False):
        assert not lane.one_call_lane_applies(ASK, dict(OWNER))


def test_a_remote_or_phone_turn_never_gets_the_lane(registry) -> None:
    registry(LOCAL)
    with override(lane.FLAG, True):
        assert not lane.one_call_lane_applies(ASK, dict(REMOTE))
        assert not lane.one_call_lane_applies(ASK, {"surface": "mobile_companion", "_http_ingress": True})


def test_a_cloud_model_never_gets_the_lane(registry) -> None:
    registry(LOCAL, CLOUD)
    with override(lane.FLAG, True):
        assert not lane.one_call_lane_applies(ASK, dict(OWNER))


def test_a_tools_forbidden_turn_never_gets_the_lane(registry) -> None:
    registry(LOCAL)
    with override(lane.FLAG, True):
        assert not lane.one_call_lane_applies(ASK, {**OWNER, "action_policy": "forbidden"})


@pytest.mark.parametrize(
    ("text", "finish", "retried", "evidence", "expected"),
    [
        ("Water boils at 100 °C.", "stop", False, False, "answer"),
        ("Leaves fall and", "length", False, False, "cut"),
        ("", "length", False, False, "empty"),
        ("", "stop", False, False, "empty"),
        ("I checked your notes: the plumber comes Tuesday.", "stop", False, False, "retry"),
        # The turn's one retry is spent: the claim gate corrects what is left.
        ("I checked your notes: the plumber comes Tuesday.", "stop", True, False, "answer"),
        # A tool already ran this turn: the prose answers from its evidence.
        ("Done. I checked your notes.", "stop", False, True, "answer"),
    ],
)
def test_a_prose_round_outcome(text: str, finish: str, retried: bool, evidence: bool, expected: str) -> None:
    assert lane.prose_round_outcome(text, finish_reason=finish, retried=retried, tool_evidence=evidence) == expected


def test_tool_evidence_is_read_from_the_round_messages() -> None:
    assert not lane.round_carries_tool_evidence([{"role": "user", "content": "hi"}])
    assert lane.round_carries_tool_evidence([{"role": "tool", "content": "{}"}])
    assert lane.round_carries_tool_evidence([{"role": "user", "content": '{"schema": "tool_observation_v1"}'}])


def test_a_not_connected_source_loses_its_seats_and_an_unknown_one_keeps_them() -> None:
    context = {"_one_call_lane_connected": {"calendar": False, "mail": None}}
    assert not lane.seat_is_runnable("operator.show_agenda", context)
    assert lane.seat_is_runnable("email.read", context)  # unknown: the tool reports its own state
    assert lane.seat_is_runnable("machine.disk_usage", context)
    assert lane.not_connected_sources(context) == ("calendar",)
    assert "calendar" in lane.not_connected_directive(context)
    assert lane.not_connected_reply("operator.show_agenda", "what's on tomorrow?")


def test_a_one_call_round_is_marked_think_off_and_shares_one_retry_per_turn(registry) -> None:
    from core.model_request_policy import REASONING_DISABLED

    registry(LOCAL)
    context = dict(OWNER)
    first = SimpleNamespace(metadata={}, reasoning_mode="auto")
    second = SimpleNamespace(metadata={}, reasoning_mode="auto")
    with override(lane.FLAG, True):
        assert lane.mark_one_call_round(first, ASK, context)
        assert lane.mark_one_call_round(second, ASK, context)
    assert first.reasoning_mode == REASONING_DISABLED
    assert lane.request_is_one_call_round(first)
    assert not lane.retry_spent(second)
    lane.spend_retry(first)
    assert lane.retry_spent(second)


def test_off_the_lane_a_round_is_not_marked(registry) -> None:
    registry(LOCAL)
    request = SimpleNamespace(metadata={}, reasoning_mode="auto")
    with override(lane.FLAG, False):
        assert not lane.mark_one_call_round(request, ASK, dict(OWNER))
    assert request.metadata == {} and request.reasoning_mode == "auto"
