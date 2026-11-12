"""An answer that lists the person's mail is never shipped cut off mid-list.

Measured 2026-11-06 on the operator's machine (0.7.8 work line, ZCODE handover, open item 2): the
unread-mail answer ended

    "... Here's who they're from (newest first):"

with nothing under it. The model had spent the chat table's few hundred tokens, and the reply
shipped as a finished answer, because the completeness inspector read a final colon as a sentence
end. Three repairs, each pinned here:

* an answer whose last line ends on a colon promised what comes next, and is incomplete;
* in the tool loop, a cut answer about mail is replaced by the read's own rows, all of them (they
  are already on this machine; nothing is sent anywhere to list them);
* the answer about a mail read gets room for every message it lists, instead of the chat table's
  short budget.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.orchestrator import tool_loop_final_message
from core.agent_runtime.research_tool_loop_facade import _mail_listing_cut_short
from core.incomplete_answer import inspect_answer_completeness
from core.prompt_normalizer import _build_conversational_request

MEASURED_TAIL = "You have 8 unread emails. Here's who they're from (newest first):"


def _mail_read(count: int) -> dict:
    messages = [
        {"from": f"Sender {index} <s{index}@example.com>", "subject": f"Subject {index}", "date": "2026-11-06"}
        for index in range(count)
    ]
    return {
        "tool_name": "email.read",
        "ok": True,
        "summary": f"{count} message(s) matched in INBOX",
        "observation": {"intent": "email.read", "ok": True, "count": count, "messages": messages},
    }


def test_the_measured_tail_is_an_unfinished_answer() -> None:
    verdict = inspect_answer_completeness(MEASURED_TAIL)

    assert verdict.incomplete
    assert "lead_in_without_content" in verdict.reasons
    assert verdict.has_content


@pytest.mark.parametrize(
    "finished",
    (
        "Here's the list:\n\n1. Anna\n2. Bob",
        "Note: nothing new today.",
        "To change it: Step 1: Open Settings. Step 2: Click Accounts.",
        "The meeting is at 10:30",
    ),
)
def test_a_colon_inside_a_finished_answer_is_not_a_cut(finished: str) -> None:
    assert not inspect_answer_completeness(finished).incomplete


def test_a_cut_mail_answer_is_replaced_by_every_row_the_read_returned() -> None:
    steps = [_mail_read(8)]

    assert _mail_listing_cut_short(MEASURED_TAIL, steps)
    listing = tool_loop_final_message(None, steps)
    assert sum(1 for line in listing.splitlines() if line.startswith("- Sender ")) == 8
    assert "Subject 7" in listing


def test_a_finished_mail_answer_stands() -> None:
    finished = "You have 2 unread emails:\n1. Sender 0 — Subject 0\n2. Sender 1 — Subject 1"

    assert not _mail_listing_cut_short(finished, [_mail_read(2)])


def test_a_cut_answer_without_a_mail_read_is_left_to_the_general_notice() -> None:
    web_step = {"tool_name": "web.research", "ok": True, "summary": "3 sources"}

    assert not _mail_listing_cut_short(MEASURED_TAIL, [web_step])


def _request(observations: list[dict]):
    persona = SimpleNamespace(display_name="VOOL", tone="calm", persona_id="default")
    context_result = SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}))
    return _build_conversational_request(
        user_text="check my unread emails and tell me who they're from",
        persona=persona,
        classification={"task_class": "chat_conversation"},
        context_result=context_result,
        task_kind="conversation",
        output_mode="conversational",
        trace_id="trace-mail",
        ambiguity=0.8,
        source_context={"surface": "openclaw", "platform": "openclaw", "runtime_tool_observations": observations},
    )


def test_the_answer_gets_room_for_every_message_it_lists() -> None:
    plain = _request([])
    mail = _request([_mail_read(12)["observation"]])

    assert plain.max_output_tokens < 1000
    assert mail.max_output_tokens >= 320 + 12 * 110
    assert mail.metadata["generation_profile"]["output_budget_intent"]["reason"] == "mailbox_listing_room"


def test_the_room_has_a_ceiling() -> None:
    assert _request([_mail_read(40)["observation"]]).max_output_tokens == 2048


def test_a_failed_read_gets_no_extra_room() -> None:
    failed = {**_mail_read(12)["observation"], "ok": False}

    assert _request([failed]).max_output_tokens < 1000
