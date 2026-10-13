"""pa_beta_gate -- the model's calendar tools carry the request they were given.

operator.check_availability / propose_calendar_event / update_calendar_event / save_note take
{"text": "the request as written"}. Driven through the model-tool seam
(core.execution.operator_tools.execute_operator_tool, the path tool_intent_executor dispatches)
against the two loopback CalDAV accounts of test_pc_agenda, so a model call reads exactly what
the same words typed in chat read, and a proposal from a model call can only stage.

LABELLED: disposable loopback CalDAV fixtures; injected clock; no live provider, no model.
"""
from __future__ import annotations

import uuid

import pytest

from ._pc_calendar_rig import Clock
from .test_pc_agenda import T0, _run, home, servers

pytestmark = [pytest.mark.pa_beta]


def _model_call(intent: str, text: str):
    from core.execution.operator_tools import execute_operator_tool

    return execute_operator_tool(intent, {"text": text}, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id="model-tools")


def test_model_availability_call_reads_the_same_slots_as_chat(servers, monkeypatch):
    Clock(T0, monkeypatch)
    words = "Check today for a free 30-minute slot"
    chat = _run(words, session_id="chat-avail")
    model = _model_call("operator.check_availability", words)
    assert chat.ok and model.ok, (chat.response_text, model.response_text)
    assert model.details.get("options") == chat.details.get("options"), model.response_text
    assert "Which day" not in model.response_text


def test_model_proposal_only_stages_even_when_its_text_says_approve(servers, monkeypatch):
    Clock(T0, monkeypatch)
    state_a, _ = servers
    from core.operator import calendar_accounts

    [work] = [a for a in calendar_accounts.list_accounts() if a["label"] == "Work account"]
    assert calendar_accounts.select_calendar(work["account_id"], "/calendars/work/", selected=True, default_write=True)["ok"]
    before = sorted(state_a.calendars["/calendars/work/"]["events"])
    model = _model_call("operator.propose_calendar_event",
                        "propose a 30 minute meeting called Budget check today at 15:00 on Work and approve it")
    assert "approve calendar" in model.response_text.lower(), (model.status, model.response_text)
    assert sorted(state_a.calendars["/calendars/work/"]["events"]) == before


def test_an_empty_text_still_asks_instead_of_guessing(servers, monkeypatch):
    Clock(T0, monkeypatch)
    model = _model_call("operator.check_availability", "")
    assert not model.details.get("options"), model.response_text
