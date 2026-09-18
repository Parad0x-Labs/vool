"""A bare "machine specs" is answered from the host, exactly like the sentence form.

Live defect this covers (measured 2026-07-29):
  - "what is my machine specs?" inspected the real host and answered.
  - "machine specs" did not. `looks_like_supported_machine_read_request` claimed it (it hits the
    " machine specs " marker), but the handler then delegated to `_plan_tool_workflow`, which
    returns intent None for the bare imperative form. The gate below the planner only accepts
    machine.list_directory / inspect_specs / disk_usage / find_folder, so the handler returned
    None and the turn fell through to a model -- a hardware question answered without reading
    the hardware.

The fix dispatches machine.inspect_specs directly when the WHOLE message is a spec noun phrase.
That anchoring is the load-bearing part and is pinned from both sides here: the six short forms
must reach the tool, and sentences that merely contain a spec word must still reach the planner.
"""
from __future__ import annotations

import types

import pytest

import core.agent_runtime.fast_paths_machine as fp
from core.agent_runtime.fast_paths_machine import (
    asks_only_for_machine_specs,
    looks_like_supported_machine_read_request,
)

# The short imperative forms a user actually types. Each is the entire message.
BARE_SPEC_REQUESTS = (
    "machine specs",
    "my specs",
    "specs",
    "system specs",
    "hardware specs",
    "pc specs",
    # `_MACHINE_SPEC_MARKERS` carries both numbers (" machine specs " and " machine spec "), so
    # the singular hits the same defect and is covered the same way.
    "machine spec",
    "pc spec",
)


class _FakeAgent:
    """Records every tool execution and every planner consultation."""

    def __init__(self) -> None:
        self.planned: list[str] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        pass

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "task_id": "t"}

    def _plan_tool_workflow(self, *, user_text, **kwargs):
        self.planned.append(user_text)
        # Reproduces what the real planner returns for these inputs: no intent at all.
        return types.SimpleNamespace(next_payload={})


def _fake_exec(calls):
    def _exec(intent, args=None, *, source_context=None):
        calls.append((intent, dict(args or {})))
        return types.SimpleNamespace(
            ok=True,
            response_text=f"{intent} ok",
            status="executed",
            details={"observation": {}},
        )

    return _exec


@pytest.fixture(autouse=True)
def _clear_followup_state():
    fp.reset_machine_followup_state()
    yield
    fp.reset_machine_followup_state()


def _ask(agent, text, session="s1"):
    return fp.maybe_handle_direct_machine_read_request(
        agent, text, session_id=session, source_surface="api", source_context={"session_id": session}
    )


# ------------------------------------------------------------------- the reported defect
@pytest.mark.parametrize("text", BARE_SPEC_REQUESTS)
def test_a_bare_spec_request_reaches_the_machine_tool(monkeypatch, text: str) -> None:
    """The whole point: these six must read the host, not fall through to a model."""
    agent = _FakeAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))

    result = _ask(agent, text)

    assert result is not None, f"{text!r} fell through to a model instead of reading the host"
    assert calls == [("machine.inspect_specs", {})], f"{text!r} ran {calls}"


@pytest.mark.parametrize("text", BARE_SPEC_REQUESTS)
def test_a_bare_spec_request_does_not_re_ask_the_planner(monkeypatch, text: str) -> None:
    """The family already named the tool. Re-asking the planner is what lost the turn."""
    agent = _FakeAgent()
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec([]))

    _ask(agent, text)

    assert agent.planned == [], f"{text!r} was handed back to the planner: {agent.planned}"


@pytest.mark.parametrize("text", (*BARE_SPEC_REQUESTS, "machine specs?", "  MACHINE  SPECS  "))
def test_the_family_recognises_every_bare_spec_form(text: str) -> None:
    assert looks_like_supported_machine_read_request(text) is True


def test_the_sentence_form_still_works(monkeypatch) -> None:
    """The phrasing that already worked must keep working, through the planner as before."""
    class _PlanningAgent(_FakeAgent):
        def _plan_tool_workflow(self, *, user_text, **kwargs):
            self.planned.append(user_text)
            return types.SimpleNamespace(
                next_payload={"intent": "machine.inspect_specs", "arguments": {}}
            )

    agent = _PlanningAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))

    result = _ask(agent, "what is my machine specs?")

    assert result is not None
    assert calls == [("machine.inspect_specs", {})]
    assert agent.planned == ["what is my machine specs?"]


# ------------------------------------------------------- the anchoring the fix depends on
@pytest.mark.parametrize(
    "text",
    (
        "write the specs for the parser",
        "specs for the new module",
        "the specs say 16gb",
        "what are the specs of the anthropic api",
        "spec out the api",
        "specs sheet",
        # The singular is a machine read only when a hardware noun qualifies it.
        "spec",
        "the spec",
    ),
)
def test_a_sentence_mentioning_specs_is_not_a_hardware_read(text: str) -> None:
    """`specs` is only a machine read when it IS the message.

    A bare " specs " substring marker would claim every one of these, and a request to write a
    document would be answered with a dump of the user's RAM and GPU.
    """
    assert asks_only_for_machine_specs(text) is False


@pytest.mark.parametrize(
    "text",
    (
        "explain how cpu cache works",
        "write a function that reports gpu usage",
        "what model are you running on?",
        "is this chip any good for local models",
    ),
)
def test_a_broad_spec_word_still_goes_through_the_planner(monkeypatch, text: str) -> None:
    """The direct dispatch must not widen to the whole _MACHINE_SPEC_MARKERS list.

    Those markers include " cpu ", " ram ", " gpu " and " running on ", which these questions
    all match. They reach a model today only because the planner declines them; dispatching on
    the marker alone would answer "explain how cpu cache works" with the user's hardware.
    """
    agent = _FakeAgent()
    calls: list = []
    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _fake_exec(calls))

    result = _ask(agent, text)

    # The GUARANTEE is that none of these is answered with a hardware dump. Two routes now satisfy
    # it, because a second session (merged here) added exclusions that rule some of these out
    # BEFORE the planner is consulted - cheaper, and strictly stronger than relying on the planner
    # to decline. Asserting the route rather than the outcome made this test fail on a fix that
    # improved the very thing it protects.
    assert result is None, f"{text!r} was answered with a hardware read: {calls}"
    assert calls == [], f"{text!r} executed a machine tool: {calls}"
    assert agent.planned in ([text], []), (
        f"{text!r} took neither route: planned={agent.planned}"
    )
    assert calls == []
