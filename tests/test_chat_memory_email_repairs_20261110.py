"""Repairs for the 0.7.8 chat, memory and email defects measured 2026-11-10 on the chat page.

Each test pins one owning cause found by driving the real chat page against an isolated
profile and a real local TLS mail service (see the module docstrings each repair cites).
Contributor: sls_0x.
"""
from __future__ import annotations

import imaplib
import json

import pytest

from core.context_scope import ContextAccessPolicy
from core.memory import entries as memory_entries
from core.memory.files import memory_entries_path
from core.persistent_memory import maybe_handle_memory_command

OWNER_LOCAL = {"surface": "cli", "platform": "cli"}


# --- clock fast path ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["what date do i fly back", "what date is my flight", "what date does the conference start",
     "what date is the orchard inspection", "what's the date of the launch"],
)
def test_a_date_question_about_an_event_is_not_a_clock_read(text):
    from core.agent_runtime.fast_paths_utility import _ASKS_DATE_RE

    assert not _ASKS_DATE_RE.search(text)


@pytest.mark.parametrize(
    "text",
    ["what is the date today", "whats the date", "what date is it", "what's the date in tokyo",
     "what is the date? output exactly one word", "what date", "what's the date and time"],
)
def test_a_clock_date_question_still_reads_the_clock(text):
    from core.agent_runtime.fast_paths_utility import _ASKS_DATE_RE

    assert _ASKS_DATE_RE.search(text)


# --- memory across the owner's chats -----------------------------------------------------------


def _command(chat: str, text: str) -> tuple[bool, str]:
    ContextAccessPolicy.for_request(session_id=chat, source_context=OWNER_LOCAL)
    return maybe_handle_memory_command(text, session_id=chat, source_context=OWNER_LOCAL)


def _rows() -> list[dict]:
    return [json.loads(line) for line in memory_entries_path().read_text(encoding="utf-8").splitlines() if line.strip()]


def _visible(chat: str) -> str:
    reader = ContextAccessPolicy.for_request(session_id=chat, source_context=OWNER_LOCAL)
    return json.dumps(memory_entries.list_memory_entries(access_policy=reader, limit=50))


def test_a_remembered_fact_is_recalled_in_another_owner_chat_and_forgotten_from_there():
    handled, reply = _command("mot-a", "Remember that my car's MOT inspection is due on 22 January at AutoRimi.")
    assert handled and "Locked in" in reply
    assert [row.get("scope") for row in _rows() if "AutoRimi" in str(row.get("text"))] == ["user_profile"]
    assert "AutoRimi" in _visible("mot-b")

    handled, reply = _command("mot-b", "Forget what I told you about the MOT.")
    assert handled and "Nothing was removed" not in reply, reply
    assert "AutoRimi" not in _visible("mot-b")
    assert "AutoRimi" not in _visible("mot-c")


def test_a_fact_the_owner_keeps_to_this_chat_stays_in_it():
    _command("keep-a", "Remember that the release marker is ZULU-9 for this chat.")
    assert "ZULU-9" in _visible("keep-a")
    assert "ZULU-9" not in _visible("keep-b")


# --- email: routing ----------------------------------------------------------------------------


def test_the_conductor_leaves_a_mailbox_request_to_the_email_tools():
    from core.conductor.planner import plan_conductor_turn

    asked: list[str] = []

    def _planner(_system, text, *_args, **_kwargs):
        asked.append(text)
        return '[{"request": "Do I have any unread emails?", "operation": "machine_observation"}, ' \
               '{"request": "List every one with sender and subject.", "operation": "factual_explanation"}]'

    plan = plan_conductor_turn(
        "Do I have any unread emails? List every one with sender and subject.", ask_model=_planner
    )
    # The planner exceptions are swallowed by design, so the proof is that it was never asked.
    assert plan is None and asked == []


def test_a_question_about_mail_from_a_sender_is_not_an_entity_to_disambiguate():
    from core.entity_ambiguity import single_plain_know_question

    assert not single_plain_know_question("What did KuCoin email me about?")


@pytest.mark.parametrize("text", ["Show me Ana's last email", "Reply to Ana's dinner email and say yes"])
def test_a_possessive_sender_seats_the_mailbox_read(text):
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents(text) == ("email.read",)


def test_a_compose_request_does_not_seat_a_mailbox_read():
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents("Write Ana a nice email") == ()


# --- email: argument guard ---------------------------------------------------------------------


@pytest.fixture
def one_account(monkeypatch):
    from types import SimpleNamespace

    import core.email_accounts as accounts

    record = SimpleNamespace(address="me@vooltest.example", read=True, send=True)
    monkeypatch.setattr(accounts, "configured_accounts", lambda: {"me-vooltest": record})


def test_a_placeholder_account_is_dropped_and_a_named_sender_becomes_the_filter(one_account):
    from core.email_argument_guard import guard_email_arguments

    verdict = guard_email_arguments(
        "email.read", {"account": "KuCoin", "folder": "Inbox"}, source_context={},
        user_text="What did KuCoin email me about?",
    )
    assert not verdict.refused
    assert "account" not in verdict.arguments and verdict.arguments["sender"] == "KuCoin"

    placeholder = guard_email_arguments(
        "email.read", {"account": "primary"}, source_context={}, user_text="check my email",
    )
    assert "account" not in placeholder.arguments and "sender" not in placeholder.arguments

    by_address = guard_email_arguments("email.send", {"account": "me@vooltest.example"}, source_context={})
    assert by_address.arguments["account"] == "me-vooltest"


def test_an_invented_recipient_is_refused_before_any_draft_or_card(one_account):
    from core.email_argument_guard import guard_email_arguments

    verdict = guard_email_arguments(
        "email.draft.save", {"to": ["ana@example.com"], "body": "see you"}, source_context={},
        user_text="Reply to Ana's dinner email and tell her Friday works",
    )
    assert verdict.refused and "ana@example.com" in verdict.message


def test_a_recipient_shown_by_a_read_or_written_by_the_person_is_allowed(one_account):
    from core.email_argument_guard import guard_email_arguments

    read = {"runtime_tool_observations": [{"intent": "email.read", "ok": True, "messages": [
        {"from": "Ana Petraite <ana@vooltest.example>", "subject": "Dinner on Friday?"}]}]}
    assert not guard_email_arguments(
        "email.reply", {"to": "ana@vooltest.example"}, source_context=read, user_text="reply to her",
    ).refused
    assert not guard_email_arguments(
        "email.send", {"to": "rita@homes.example"}, source_context={},
        user_text="email rita@homes.example that Thursday is fine",
    ).refused
    # The model's own draft never grounds the address it introduced.
    drafted = {"runtime_tool_observations": [{"intent": "email.draft.save", "ok": True,
                                              "draft": {"to": ["ana@example.com"]}}]}
    assert guard_email_arguments(
        "email.send", {"to": "ana@example.com"}, source_context=drafted, user_text="send it",
    ).refused


def test_the_send_card_names_recipient_and_message():
    from core.mode_permission_policy import _affected_resources, _edit_preview

    args = {"to": ["ana@vooltest.example"], "subject": "Re: Dinner", "body": "Friday works."}
    assert "ana@vooltest.example" in _affected_resources(args)
    preview = _edit_preview("email.send", args, {})
    assert "To: ana@vooltest.example" in preview and "Friday works." in preview


# --- tool loop: a prose answer after a tool ran ----------------------------------------------


def _respond_definition():
    from core.cloud_provider_contract import CloudToolDefinition

    return CloudToolDefinition(
        intent="respond.direct", name="respond_direct", description="answer",
        parameters={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    )


def test_a_prose_reply_after_an_executed_tool_closes_the_loop():
    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import _prose_closes_tool_loop
    from core.memory_first_router import _stamp_tool_loop_continuation

    request = ModelRequest(task_kind="tool_intent", prompt="p")
    _stamp_tool_loop_continuation(request, {"runtime_tool_observations": [
        {"intent": "email.read", "ok": True, "mode": "tool_executed"}]})
    calls = _prose_closes_tool_loop(request, "You have 8 unread messages: ...", (_respond_definition(),))
    assert [(call.intent, call.arguments["message"][:8]) for call in calls] == [("respond.direct", "You have")]


def test_a_first_round_prose_reply_is_not_taken_as_the_answer():
    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import _prose_closes_tool_loop
    from core.memory_first_router import _stamp_tool_loop_continuation

    request = ModelRequest(task_kind="tool_intent", prompt="p")
    _stamp_tool_loop_continuation(request, {"runtime_tool_observations": []})
    assert _prose_closes_tool_loop(request, "Searching your inbox...", (_respond_definition(),)) == ()


def test_the_mail_a_read_returned_is_shown_when_no_model_writes_the_answer():
    from core.agent_runtime.orchestrator import tool_loop_final_message

    steps = [{"tool_name": "email.read", "ok": True, "observation": {"count": 2, "messages": [
        {"from": "Mama <mama@family.example>", "subject": "Sunday lunch"},
        {"from": "KuCoin <no-reply@kucoin.example>", "subject": "New login"}]}}]
    message = tool_loop_final_message(None, steps)
    assert "Sunday lunch" in message and "KuCoin" in message and "2 message(s)" in message


# --- IMAP folder selection ---------------------------------------------------------------------


def test_inbox_is_case_insensitive_and_a_missing_folder_is_named(tmp_path):
    from core.email_tools import _FolderNotSelectable, _select_readonly
    from tests.local_mail_service import LocalMailService

    with LocalMailService(smtp_port=0, imap_port=0) as service:
        service.store.add_user("me@vooltest.example", "pw-test", {"INBOX": []})
        imap = imaplib.IMAP4("127.0.0.1", service.imap_port)
        try:
            imap.login("me@vooltest.example", "pw-test")
            assert _select_readonly(imap, "Inbox") == "INBOX"
            with pytest.raises(_FolderNotSelectable):
                _select_readonly(imap, "KuCoin")
        finally:
            imap.logout()


def test_a_sender_filter_matches_whole_words():
    from core.email_tools import _sender_matches

    assert _sender_matches("ana", "Ana Petraite <ana@vooltest.example>")
    assert not _sender_matches("ana", "Ryanair <noreply@ryanair.example>")
    assert _sender_matches("kucoin", "KuCoin <no-reply@kucoin.example>")


def test_a_question_about_the_speakers_own_facts_is_not_an_entity_to_disambiguate():
    from core.entity_ambiguity import single_plain_know_question

    assert not single_plain_know_question("Which bakery is my favourite?")


def test_an_exact_value_recall_keeps_the_owners_profile_memory_from_another_chat():
    from core.tiered_context_loader import ContextItem, _current_session_scoped_items

    def item(scope: str, chat: str) -> ContextItem:
        return ContextItem(
            item_id=f"m-{scope}-{chat}", layer="relevant", source_type="runtime_memory", title="t",
            content="my bike lock code word is HERON-7.", priority=0.5, confidence=0.9,
            include_reason="relevant_persistent_memory", metadata={"scope": scope, "origin_chat_id": chat},
        )

    kept = _current_session_scoped_items(
        [item("user_profile", "chat-a"), item("chat", "chat-a"), item("chat", "chat-b")], session_id="chat-b",
    )
    assert [(i.metadata["scope"], i.metadata["origin_chat_id"]) for i in kept] == [
        ("user_profile", "chat-a"), ("chat", "chat-b")]


@pytest.mark.parametrize(
    "said,expected",
    [
        ("whats my gym lockr number", "what's my gym lockr number?"),
        ("do you remember my gym locker number?", "what's my gym locker number?"),
        ("remind me my bike lok code word", "what's my bike lok code word?"),
        ("remind me to buy milk", "remind me to buy milk"),
    ],
)
def test_an_own_fact_ask_is_read_as_the_question_it_is(said, expected):
    from core.agent_runtime.cross_chat_recall import as_question

    assert as_question(said) == expected


def test_remind_me_my_fact_is_not_a_reminder_to_schedule():
    from core.operator.parser import parse_operator_action_intent

    assert parse_operator_action_intent("remind me my bike lok code word") is None
    assert parse_operator_action_intent("remind me tomorrow at 9 to call the dentist").kind == "schedule_reminder"


def test_a_cut_off_first_reply_keeps_its_question_as_the_chat_title(monkeypatch):
    from core.memory import entries
    import core.runtime_continuity as continuity

    monkeypatch.setattr(continuity, "interrupted_chat_requests", lambda: {
        "chat-cut": {"request_preview": "Write an essay about Trakai Castle.", "updated_at": "2026-11-11T01:00:00+00:00"},
    })
    by_session = {"chat-cut": {"session_id": "chat-cut", "title": "New chat", "updated_at": "", "turn_count": 0}}
    entries._overlay_interrupted_turns(by_session, namespaces={}, meta={})
    assert by_session["chat-cut"]["title"] == "Write an essay about Trakai Castle."
    assert by_session["chat-cut"]["interrupted"] is True


def test_the_entity_check_ask_back_has_no_bookkeeping_words():
    from core.entity_ambiguity import render_unresolved_clarification

    text = render_unresolved_clarification("What is the population of Springfield?", attempts=2)
    assert "well-known answer" not in text and "tried 2x" not in text and "Springfield" in text


@pytest.mark.parametrize("text", ["did paysera send me anything?", "did I get anything from paysera?", "did my bank write to me?"])
def test_a_sender_asked_about_by_name_seats_the_mailbox_read(text):
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents(text) == ("email.read",)


def test_money_or_a_question_to_vool_is_not_a_mailbox_read():
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents("did mom send me money?") == ()
    assert mailbox_retrieval_intents("did you send me the file?") == ()


@pytest.mark.parametrize("text", ["pdf please", "make it a pdf", "as excel"])
def test_a_bare_format_follow_up_orders_the_file(text):
    from core.tool_demand_signals import asks_for_a_file

    assert asks_for_a_file(text)


def test_a_question_about_a_format_orders_nothing():
    from core.tool_demand_signals import asks_for_a_file

    assert not asks_for_a_file("what is a pdf")


def test_a_bare_name_recipient_resolves_to_the_sender_read_here(one_account):
    from core.email_argument_guard import guard_email_arguments

    ctx = {"runtime_tool_observations": [{"intent": "email.read", "ok": True, "messages": [
        {"from": "KuCoin <no-reply@kucoin.example>", "subject": "Deposit"}]}]}
    verdict = guard_email_arguments("email.send", {"to": "kucoin", "subject": "Thanks"}, source_context=ctx,
                                    user_text="reply to the latest kucoin email saying thanks")
    assert not verdict.refused and verdict.arguments["to"] == "no-reply@kucoin.example"
    unknown = guard_email_arguments("email.send", {"to": "bob"}, source_context={}, user_text="email bob")
    assert unknown.refused


def test_an_own_situation_question_about_the_calendar_is_not_a_memory_recall():
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane

    assert _owned_by_another_lane("do I have any meetings this week?")


def test_a_one_off_order_to_produce_a_thing_is_not_a_standing_instruction():
    from core.standing_instructions import read_turn

    assert read_turn("Write a short packing list for a weekend in Lisbon with emojis") == ([], [])
    assert read_turn("Use emojis.")[0] == ["Use emojis."]


def test_a_question_worked_out_from_this_chat_stays_with_the_conversation():
    from core.agent_runtime.cross_chat_recall import _current_chat_holds_it

    history = {"conversation_history": [{"role": "user", "content": "I fly out on 14 October and I'm staying 6 nights"}]}
    assert _current_chat_holds_it(("date", "fly", "back"), history)


@pytest.mark.parametrize("text", ["remind me the code for the hotel booking", "remind me which bakery i like most"])
def test_remind_me_about_a_known_thing_is_a_recall_not_a_reminder(text):
    from core.operator.parser import parse_operator_action_intent

    assert parse_operator_action_intent(text) is None


def test_a_chat_pointer_word_is_this_chats_only_when_this_chat_has_statements():
    from core.agent_runtime.cross_chat_recall import _chat_has_owner_statements

    assert not _chat_has_owner_statements({"conversation_history": [{"role": "user", "content": "do u know the code i mentioned"}]})
    assert _chat_has_owner_statements({"conversation_history": [
        {"role": "user", "content": "She loves ceramics and jazz."},
        {"role": "user", "content": "which hobby did I mention second?"}]})


def test_a_reply_that_only_announces_its_own_next_steps_is_not_a_finished_answer():
    from core.incomplete_answer import inspect_answer_completeness

    plan = ("I'll open the link to the Daugava article and find its length.\n"
            "Step 1: Navigate to the Daugava page.\nStep 2: Locate the length in the infobox.\nStep 3: Report it.")
    assert inspect_answer_completeness(plan).reasons == ("plan_not_carried_out",)
    assert not inspect_answer_completeness("To change it: Step 1: Open Settings. Step 2: Click Accounts.").incomplete
    assert not inspect_answer_completeness("The Daugava is 1,020 km long. I'll check the source if you want.").incomplete
