"""The arbitration gate in the front door: fires only on ambiguity/near-miss, executes the pick
through real read-only tools, and fails open on every other outcome — with each decision logged."""
from __future__ import annotations

from unittest import mock

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths
from core.agent_runtime.turn_frontdoor import (
    _ARBITRATE_ON_AMBIGUITY,
    _ARBITRATE_ON_NEAR_MISS,
    _maybe_arbitrate_intent,
)

AMBIGUOUS_MSG = "right, can you check Token hunter folder on this machine and run audit, but only audit no changes"
NEAR_MISS_MSG = "Ok. please fint the oken hunter folder on desktop and we will analyse it"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    yield
    runtime_paths.configure_runtime_home(None)


def _gate(text: str, agent=None, gate: str = _ARBITRATE_ON_AMBIGUITY):
    """One of the front door's TWO arbitration call sites, named by `gate`.

    They exist separately because the two signals want opposite positions: competing readings must
    be settled before the deterministic lanes run, a near-miss only after they have all declined.
    Passing the gate explicitly is what keeps each test honest about which one it is exercising.
    """
    return _maybe_arbitrate_intent(
        agent or mock.Mock(),
        effective_input=text,
        session_id="openclaw:testtesttesttest0000",
        source_surface="chat",
        source_context={},
        gate=gate,
    )


def test_flag_off_is_a_no_op(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "0")
    with mock.patch("core.intent_arbiter.arbitrate", side_effect=AssertionError("must not run")):
        assert _gate(AMBIGUOUS_MSG) is None


def test_unambiguous_message_never_consults_the_model() -> None:
    with mock.patch("core.intent_arbiter.arbitrate", side_effect=AssertionError("must not run")):
        assert _gate("what are my machine specs?") is None      # single-family claim
        assert _gate("hey how are you today") is None           # no claim, no tool-ish noun


def test_ambiguous_pick_executes_the_real_tool_and_logs() -> None:
    from core.intent_arbiter import ArbiterDecision

    execution = mock.Mock(ok=True, details={}, response_text="Found 1 folder")
    wrapped = {"routed": "find_folder"}
    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("find_folder", "token hunter")),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=execution) as tool,
        mock.patch("core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result", return_value=wrapped),
    ):
        result = _gate(AMBIGUOUS_MSG)
    assert result == wrapped
    assert tool.call_count == 1
    (tool_name, tool_args), tool_kwargs = tool.call_args
    assert (tool_name, tool_args) == ("machine.find_folder", {"name": "token hunter"})
    assert tool_kwargs["trusted_local_only"] is False
    # The execution boundary the runtime crosses carries its own authorization: a manual-mode
    # allow for exactly this bounded action, not a bare context that executed by default.
    permission = dict(tool_kwargs["source_context"]).get("_blackbox_permission") or {}
    assert permission.get("effect") == "allow" and permission.get("actions") == ["list_directories"], permission
    rows = rdl.recent_decisions()
    assert rows and rows[-1]["family"] == "intent_arbiter"
    assert rows[-1]["arbiter"] == "picked:find_folder" and rows[-1]["handled"] is True
    assert "find_folder" in rows[-1]["claims"] and "machine_specs" in rows[-1]["claims"]


def test_chat_pick_falls_open_and_logs_the_decline() -> None:
    from core.intent_arbiter import ArbiterDecision

    with mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("chat")):
        assert _gate(AMBIGUOUS_MSG) is None
    assert rdl.recent_decisions()[-1]["arbiter"] == "declined:chat"


def test_model_failure_falls_open_and_logs() -> None:
    with mock.patch("core.intent_arbiter.arbitrate", return_value=None):
        assert _gate(AMBIGUOUS_MSG) is None
    assert rdl.recent_decisions()[-1]["arbiter"].startswith("failed_open")


def test_pick_without_argument_never_guesses() -> None:
    from core.intent_arbiter import ArbiterDecision

    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("find_folder", "")),
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", side_effect=AssertionError("must not run")),
    ):
        assert _gate("check that folder thing on this machine or whatever") is None


def test_near_miss_typo_reaches_the_arbiter() -> None:
    from core.intent_arbiter import ArbiterDecision

    execution = mock.Mock(ok=True, details={}, response_text="Found it")
    with (
        mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("find_folder", "oken hunter")) as arb,
        mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=execution),
        mock.patch("core.agent_runtime.fast_paths_machine._machine_tool_fast_path_result", return_value={"ok": 1}),
    ):
        result = _gate(NEAR_MISS_MSG, gate=_ARBITRATE_ON_NEAR_MISS)
    assert result == {"ok": 1}
    arb.assert_called_once()


def test_each_signal_belongs_to_exactly_one_call_site() -> None:
    """A message may cost at most ONE arbitration, and only from the gate that owns its signal.

    This is what stops the split from becoming two chances to spend a model call, and what stops a
    near-miss from being arbitrated at the early position again -- the position whose whole cost was
    a call bought ahead of the deterministic lanes that answer these for free.
    """
    with mock.patch("core.intent_arbiter.arbitrate", side_effect=AssertionError("wrong gate ran")):
        assert _gate(NEAR_MISS_MSG, gate=_ARBITRATE_ON_AMBIGUITY) is None
        assert _gate(AMBIGUOUS_MSG, gate=_ARBITRATE_ON_NEAR_MISS) is None


def test_arbiter_receives_server_context_identity() -> None:
    from core.intent_arbiter import ArbiterDecision

    with mock.patch(
        "core.intent_arbiter.arbitrate",
        return_value=ArbiterDecision("chat"),
    ) as arb:
        result = _maybe_arbitrate_intent(
            mock.Mock(),
            effective_input=AMBIGUOUS_MSG,
            session_id="chat-context-100",
            source_surface="chat",
            source_context={
                "project_id": "project-context-200",
                "request_id": "request-context-300",
            },
        )

    assert result is None
    _, kwargs = arb.call_args
    assert kwargs["request_id"] == "request-context-300"
    assert kwargs["chat_id"] == "chat-context-100"
    assert kwargs["project_id"] == "project-context-200"
    assert kwargs["context_manifest"] == {
        "chat_id": "chat-context-100",
        "project_id": "project-context-200",
        "items_included": [],
        "items_excluded": [],
        "capsule_version": "none",
    }


def test_gate_exceptions_never_break_the_turn() -> None:
    with mock.patch("core.intent_arbiter.arbitrate", side_effect=RuntimeError("boom")):
        assert _gate(AMBIGUOUS_MSG) is None
