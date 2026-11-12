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
    import core.runtime_continuity as continuity
    from core.memory import entries

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


# --- saved memory answers only what it covers (battery v8, 2026-11-11) -------------------------


def _save_test_codes(chat: str) -> None:
    for fact in ("Remember that my test hotel booking code is LOTUS-73.",
                 "Remember that my test bike lock code word is HERON-7."):
        handled, reply = _command(chat, fact)
        assert handled and "Locked in" in reply, reply


def _lane_rows(question: str, chat: str):
    from core.agent_runtime.cross_chat_recall import _QUESTION_WORDS, _memory_rows, as_question
    from core.chat_housekeeping import query_terms

    text = as_question(question)
    terms = tuple(t for t in query_terms(text) if t not in _QUESTION_WORDS)
    ContextAccessPolicy.for_request(session_id=chat, source_context=OWNER_LOCAL)
    return _memory_rows(text, terms, chat, OWNER_LOCAL)


def test_one_shared_word_is_not_saved_memory_for_the_cross_chat_lane():
    _save_test_codes("cover-a")
    covering, closest = _lane_rows("Where is my test parking spot at work?", "cover-b")
    assert covering == [] and closest == []
    covering, _ = _lane_rows("whats my test hotel bookin code", "cover-b")
    assert len(covering) == 1 and "LOTUS-73" in covering[0]


def test_after_forget_the_other_code_is_offered_as_closest_never_as_the_answer():
    _save_test_codes("close-a")
    handled, _ = _command("close-a", "Forget my test hotel booking code.")
    assert handled
    covering, closest = _lane_rows("What is my test hotel booking code?", "close-b")
    assert covering == []
    assert len(closest) == 1 and "HERON-7" in closest[0]


def test_the_saved_memory_lane_does_not_list_unrelated_codes_for_another_chat_question():
    _save_test_codes("list-a")
    handled, reply = _command("list-b", "What did I tell you in another chat about my test parking spot?")
    assert not handled, reply  # nothing saved covers it: the cross-chat lane and the model take it
    handled, reply = _command("list-b", "do you remember my test hotel booking code?")
    assert handled and "LOTUS-73" in reply and "HERON-7" not in reply, reply


# --- battery v9 (2026-11-11) -------------------------------------------------------------------


def test_a_fact_said_again_after_it_was_forgotten_can_be_quoted_from_that_chat():
    from datetime import datetime, timedelta, timezone

    from core.agent_runtime.cross_chat_recall import _not_forgotten

    said = "my test parking spot at work is P-17 on level -2."
    handled, reply = _command("again-a", "Remember that " + said)
    assert handled and "Locked in" in reply, reply
    handled, reply = _command("again-a", "Forget my test parking spot.")
    assert handled and "Nothing was removed" not in reply, reply
    before = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    after = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    assert not _not_forgotten(said, before)  # said before the forget: stays forgotten
    assert not _not_forgotten(said, "")  # unknown time: fail closed
    assert _not_forgotten(said, after)  # said again afterwards: the owner's again


def test_one_shared_word_is_never_the_closest_saved_fact():
    _save_test_codes("near-a")
    assert _lane_rows("which level is my test parking on", "near-b") == ([], [])


@pytest.mark.parametrize(
    "text",
    ["remind me which bakery i like most", "what day is test piano", "do u know the test parking spot i mentioned",
     "can you find my test piano lesson time from earlier", "when is my dentist appointment"],
)
def test_a_question_without_a_question_mark_is_not_saved_as_a_fact(text):
    from core.memory.learning import extract_memory_candidates

    assert extract_memory_candidates(text) == []


@pytest.mark.parametrize(
    "text", ["When I travel I take the window seat.", "Whatever the weather, I cycle to work."]
)
def test_a_statement_that_opens_like_a_question_word_is_still_read(text):
    from core.memory.learning import should_skip_memory_sentence

    assert not should_skip_memory_sentence(text)


@pytest.mark.parametrize(
    "text", ["reply to the latest kucoin email saying thanks", "answer kucoin's last email with thanks"]
)
def test_a_reply_to_a_received_message_picked_by_recency_reads_the_mailbox_first(text):
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents(text) == ("email.read",)


def test_a_compose_order_for_a_template_does_not_read_the_mailbox():
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents("Write the last email template for our newsletter") == ()


@pytest.mark.parametrize("text", ["do I have any meetings this week?", "will we have any calls tomorrow"])
def test_any_meetings_in_a_window_is_an_agenda_read(text):
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "show_agenda"


def test_opening_a_named_page_without_a_url_seats_a_web_search():
    from core.tool_demand_signals import resolve_demand_signals

    signals = resolve_demand_signals("open the Wikipedia page for the Daugava river and tell me how long it is")
    assert "web.search" in signals.explicit_intents


# --- battery v10 (2026-11-11) ------------------------------------------------------------------


def test_a_read_drops_a_subject_taken_from_the_replys_own_words():
    from core.email_argument_guard import guard_email_arguments

    verdict = guard_email_arguments(
        "email.read", {"sender": "kucoin", "subject": "Thank You", "unseen_only": True},
        source_context={}, user_text="reply to the latest kucoin email saying thanks",
    )
    assert verdict.arguments.get("sender") == "kucoin"
    assert "subject" not in verdict.arguments and not verdict.arguments.get("unseen_only")


def test_a_read_keeps_the_filters_the_person_asked_for():
    from core.email_argument_guard import guard_email_arguments

    verdict = guard_email_arguments(
        "email.read", {"sender": "paysera", "subject": "fee changes", "unseen_only": True},
        source_context={}, user_text="show my unread paysera mail about the fee changes",
    )
    assert verdict.arguments.get("subject") == "fee changes" and verdict.arguments.get("unseen_only") is True


@pytest.mark.parametrize(
    "reply",
    ["I'm on it, baby! Let me fetch the Wikipedia page for the Daugava River and check its length. Just give me a sec...",
     "Hey there! Let me open the page.",
     "Sure thing, let's check that out. I'll fetch the Wikipedia page for the Daugava River and find the length. One sec..."],
)
def test_an_acknowledged_plan_with_nothing_done_is_incomplete(reply):
    from core.incomplete_answer import _only_a_plan

    assert _only_a_plan(reply)


@pytest.mark.parametrize(
    "reply", ["Sure! Let me check. The Daugava is 1,020 km long.", "On it! The Daugava is 1,020 km long."]
)
def test_an_acknowledged_answer_is_not_a_plan(reply):
    from core.incomplete_answer import _only_a_plan

    assert not _only_a_plan(reply)


def test_the_draft_send_card_shows_what_the_draft_will_send(monkeypatch):
    import core.email_drafts as drafts
    from core.mode_permission_policy import OperatingMode, PermissionAction, _new_approval_request

    stored = {"draft_id": "ed-1", "to": ["no-reply@exchange.example"], "subject": "Re: Deposit Successful",
              "body": "Thanks!", "account_resolved": "me-vooltest"}
    asked: list[tuple[str, str]] = []

    def _get(draft_id="", *, session_id="", principal=None, latest=False):
        asked.append((draft_id, session_id))
        return drafts.DraftResult(True, "ok", "Draft retrieved.", draft=stored)

    monkeypatch.setattr(drafts, "get_draft", _get)
    request = _new_approval_request(
        session_id="card-s", task_id="card-t", intent="email.draft.send", arguments={"draft_id": "ed-1"},
        actions=(PermissionAction.SEND_EXTERNAL_MESSAGES,), mode=OperatingMode.MANUAL, mode_revision=0,
        source_context={"session_id": "card-s"}, fingerprint="fp-card",
    )
    assert asked == [("ed-1", "card-s")]
    assert "no-reply@exchange.example" in request["affected_resources"]
    preview = request["diff_preview"]
    assert "To: no-reply@exchange.example" in preview and "Re: Deposit Successful" in preview and "Thanks!" in preview


def test_an_explicit_web_ask_is_admitted_to_the_tool_loop():
    from core.execution.planner import should_attempt_tool_intent

    text = "open the Wikipedia page for the Daugava river and tell me how long it is"
    assert should_attempt_tool_intent(text, task_class="unknown", source_context={"surface": "api", "platform": "api"})
    assert not should_attempt_tool_intent(
        "Do not search the web. Open the Wikipedia page for the Daugava river.",
        task_class="unknown", source_context={"surface": "api", "platform": "api"},
    )


@pytest.mark.parametrize(
    "text", ["according to wikipedia, when was the eiffel tower finished?",
             "what does wikipedia say about the eiffel tower?"],
)
def test_an_answer_asked_from_wikipedia_is_a_web_lookup(text):
    from core.execution.planner import should_attempt_tool_intent
    from core.tool_demand_signals import resolve_demand_signals

    assert "web.search" in resolve_demand_signals(text).explicit_intents
    assert should_attempt_tool_intent(text, task_class="unknown", source_context={"surface": "api", "platform": "api"})


def test_a_question_about_wikipedia_itself_is_not_a_lookup():
    from core.tool_demand_signals import resolve_demand_signals

    assert resolve_demand_signals("what is wikipedia?").explicit_intents == ()


# --- private-web test (2026-11-11) -------------------------------------------------------------


def test_a_fetched_page_is_evidence_the_grounding_gate_can_read(monkeypatch):
    from types import SimpleNamespace

    from core import grounding_lifecycle
    from core.agent_runtime import fast_paths_web
    from tests.test_a_url_is_not_a_local_file_path import _Agent

    body = '{"IsTor":true,"IP":"5.2.69.86"}'
    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=True, status="ok", response_text="",
            details={"url": arguments["url"], "final_url": arguments["url"], "text": body, "truncated": False,
                     "observation": {"url": arguments["url"], "text": body, "http_status": 200}},
        ),
    )
    context = {"session_id": "fetch-s", "turn_id": "fetch-t", "surface": "api"}
    assert grounding_lifecycle.register_required(
        context, request_text="open it", reason_codes=("current_info_signal:explicit_lookup_request",)
    )
    question = "Open https://check.torproject.org/api/ip and tell me exactly what it says"
    result = fast_paths_web.maybe_handle_url_read(_Agent(), question, session_id="fetch-s", source_context=context)
    assert '"IsTor":true' in str(result["response"])
    record = grounding_lifecycle.lifecycle_for_context(context)
    assert record.retrieval_outcome == "tool_receipt" and record.retrieved_source_count == 1
    rows = record.typed_observations
    assert any("5.2.69.86" in str(row.get("summary")) for row in rows)
    from core.grounding_publication import publication_verdict

    grounding_lifecycle.record_model_authorship(context)
    grounding_lifecycle.seal_turn_grounding(context, str(result["response"]))
    verdict = publication_verdict(grounding_lifecycle.lifecycle_for_context(context), str(result["response"]))
    assert verdict.failed_stage == "" and not verdict.withheld_claims, verdict
    assert verdict.failed_stage != "retrieved", verdict
    assert "5.2.69.86" in verdict.content and "no usable rows" not in verdict.content, verdict


def test_the_not_yet_checked_model_refusal_leads_with_plain_words():
    from tests.test_authorship_refusal_names_attempted_models import _blocked_decision

    text = _blocked_decision().refusal_text()
    plain, remedy, details = text.split("\n\n")
    assert "hasn't passed VOOL's answer check" in plain and "(qwen2.5:7b)" in plain
    for term in ("certified to author", "runtime", "ollama-local:", "`"):
        assert term not in plain and term not in remedy
    assert details.startswith("Details:") and "not certified to author" in details


@pytest.mark.parametrize(
    "question", ["according to wikipedia, when was the eiffel tower finished?",
                 "Who wrote the novel Solaris? Check Wikipedia.", "who built the eiffel tower"],
)
def test_an_ordinary_research_question_does_not_search_developer_docs(question):
    from core.source_reputation import profiles_for_topic, render_query
    from retrieval.web_adapter import _infer_topic_kind

    kind = _infer_topic_kind(question, task_class="research", topic_hints=[])
    assert kind == "general"
    queries = " ".join(render_query(profile, question) for profile in profiles_for_topic(kind, question))
    assert "docs.python.org" not in queries and "github.com" not in queries


def test_a_developer_research_question_still_reads_developer_sources():
    from retrieval.web_adapter import _infer_topic_kind

    assert _infer_topic_kind("how do I parse json in python", task_class="research", topic_hints=[]) == "technical"


# --- plugins run (2026-11-12) ------------------------------------------------------------------


def _follow_up_policy(prior_user: str, current: str):
    from core.ordinary_chat_response_guard import ordinary_chat_output_policy, prior_turn_literal_hashes

    return ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text=current,
        prior_turn_literal_hashes=prior_turn_literal_hashes(
            [{"role": "user", "content": prior_user}, {"role": "assistant", "content": "ORBIT-7731"},
             {"role": "user", "content": current}],
            current_user_text=current,
        ),
    )


@pytest.mark.parametrize(
    "current", ["how many bytes long is that text?", "reverse the string above", "what does the previous answer mean?"]
)
def test_a_follow_up_about_the_previous_turns_text_may_quote_it(current):
    from core.ordinary_chat_response_guard import (
        inspect_ordinary_chat_output,
        remove_unrequested_prior_turn_literals,
    )

    policy = _follow_up_policy("Run the echo plugin with ORBIT-7731.", current)
    answer = "The text ORBIT-7731 is 10 bytes long."
    assert inspect_ordinary_chat_output(answer, policy, current_user_text=current).allowed
    assert remove_unrequested_prior_turn_literals(answer, policy, current_user_text=current) == answer


def test_an_unrelated_later_turn_still_may_not_answer_with_an_old_code():
    from core.ordinary_chat_response_guard import inspect_ordinary_chat_output

    current = "Which element of [3, 1, 2] is largest?"
    policy = _follow_up_policy("Run the echo plugin with ORBIT-7731.", current)
    result = inspect_ordinary_chat_output("ORBIT-7731", policy, current_user_text=current)
    assert not result.allowed and result.reasons == ("unrequested_prior_turn_literal",)


@pytest.mark.parametrize(
    "current", ["now do the same for BRAVO-2", "same for BRAVO-2", "again for BRAVO-2", "and for BRAVO-2?",
                "repeat that for BRAVO-2", "what about BRAVO-2?"],
)
def test_a_repeat_the_task_follow_up_may_carry_the_previous_output(current):
    from core.ordinary_chat_response_guard import inspect_ordinary_chat_output

    policy = _follow_up_policy("Run the echo plugin with ORBIT-7731.", current)
    answer = "ORBIT-7731 became BRAVO-2 in the second run."
    assert inspect_ordinary_chat_output(answer, policy, current_user_text=current).allowed


@pytest.mark.parametrize(
    "text", ["whats in my orders database?", "how many rows r in my orders table", "show me my databse tables",
             "what's in my budget spreadsheet?"],
)
def test_a_question_about_the_owners_data_store_is_not_a_cross_chat_recall(text):
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert _owned_by_another_lane(as_question(text))


def test_a_personal_fact_question_is_still_the_cross_chat_lanes():
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert not _owned_by_another_lane(as_question("where is my test parking spot at work"))


@pytest.mark.parametrize(
    "reply",
    ["Let's connect to your database and list the tables. Would you like me to proceed?",
     "I'll help you check your orders database. First, let's connect to it and inspect the structure. Would you like me to proceed with that?",
     "Let's connect to your database and check the `orders` table to see how many orders are shipped. Would you like me to proceed?"],
)
def test_a_plan_that_asks_leave_to_start_is_incomplete(reply):
    from core.incomplete_answer import _only_a_plan

    assert _only_a_plan(reply)


@pytest.mark.parametrize(
    "reply",
    ["There are 4 shipped orders. Would you like me to proceed with the cancelled ones too?",
     "Let's see: 46 × 19 = 874. So the answer is 874."],
)
def test_an_answer_that_offers_a_next_step_is_not_a_plan(reply):
    from core.incomplete_answer import _only_a_plan

    assert not _only_a_plan(reply)


# --- 0.7.8 sweep (2026-11-12) ------------------------------------------------------------------


def test_an_unchecked_writer_waits_for_the_boot_check_before_it_is_refused(monkeypatch):
    import threading
    from types import SimpleNamespace

    import core.final_answer_authorship as authorship
    import core.local_model_readiness as readiness

    calls: list[str] = []

    def _decide(**kwargs):
        calls.append("decide")
        eligible = not readiness.boot_readiness_check_running()
        return SimpleNamespace(eligible=eligible, task_class="chat", requested_model="ollama-local:qwen3:4b",
                               certification_state="unchecked", certification_source="none", detail="")

    monkeypatch.setattr(authorship, "resolve_author_role", lambda **_k: authorship.FINAL_ANSWER_ROLE)
    monkeypatch.setattr(authorship, "decide_final_answer_author", _decide)
    monkeypatch.setattr(authorship, "_BOOT_CHECK_WAIT_SECONDS", 5.0)
    readiness.mark_boot_readiness_check(True)
    threading.Timer(0.3, readiness.mark_boot_readiness_check, args=(False,)).start()
    verdict = authorship.precall_author_verdict(manifest=SimpleNamespace(provider_id="ollama-local:qwen3:4b"))
    assert calls == ["decide", "decide"] and verdict.eligible


def test_with_no_boot_check_running_an_unchecked_writer_is_refused_at_once(monkeypatch):
    from types import SimpleNamespace

    import core.final_answer_authorship as authorship
    import core.local_model_readiness as readiness

    readiness.mark_boot_readiness_check(False)
    monkeypatch.setattr(authorship, "resolve_author_role", lambda **_k: authorship.FINAL_ANSWER_ROLE)
    monkeypatch.setattr(authorship, "decide_final_answer_author", lambda **_k: SimpleNamespace(
        eligible=False, task_class="chat", requested_model="m", certification_state="unchecked",
        certification_source="none", detail=""))
    verdict = authorship.precall_author_verdict(manifest=SimpleNamespace(provider_id="m"))
    assert not verdict.eligible and verdict.reason == authorship.REASON_BLOCKED_BEFORE_GENERATION


def _local_truth(model_id: str, vram_gb: float):
    from core.provider_routing import ProviderCapabilityTruth

    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}", model_id=model_id, role_fit="drone", context_window=32768,
        tool_support=(), structured_output_support=True, tokens_per_second=20.0, ram_budget_gb=vram_gb,
        vram_budget_gb=vram_gb, quantization="q4", locality="local", privacy_class="local_private",
        queue_depth=0, max_safe_concurrency=1,
    )


def test_auto_does_not_pick_a_local_model_that_cannot_fit_free_memory():
    from core.local_inference_autopilot import _without_local_models_that_do_not_fit

    big, small = _local_truth("gpt-oss:20b", 13.0), _local_truth("qwen3:4b", 2.6)
    assert _without_local_models_that_do_not_fit([big, small], 4.0, ()) == [small]
    # a resident model costs nothing new; unknown free memory or nothing fitting changes nothing
    assert _without_local_models_that_do_not_fit([big, small], 2.0, ("gpt-oss:20b",)) == [big]
    assert _without_local_models_that_do_not_fit([big, small], None, ()) == [big, small]
    assert _without_local_models_that_do_not_fit([big], 2.0, ()) == [big]


def test_the_plan_s_primary_skips_the_model_that_does_not_fit():
    from core.local_inference_autopilot import build_local_inference_autopilot_plan

    def _primary(free_memory_gb):
        return build_local_inference_autopilot_plan(
            user_text="name the planets closer to the sun than mars", task_kind="chat", output_mode="plain_text",
            provider_role="drone", capability_truth=[_local_truth("gpt-oss:20b", 13.0), _local_truth("qwen3:8b", 5.5)],
            free_memory_gb=free_memory_gb, default_model_tag="gpt-oss:20b",
        ).to_dict()["selected_model"]

    assert _primary(None) == "gpt-oss:20b"  # the scoring's own preference, memory unknown
    assert _primary(8.0) == "qwen3:8b"  # 13 GB cannot load into 8 GB free


# --- database run 5 (2026-11-12): a reply must not claim an action that never ran ---------------


def _gate_with(monkeypatch, executed, variants):
    import core.final_answer_authorship as authorship
    import core.turn_model_call_ledger as ledger

    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": list(executed), "completed_calls": 1})
    monkeypatch.setattr(authorship, "_runtime_tool_names", lambda: {name.replace(".", " ").replace("-", " "): name for name in variants})
    return authorship


def test_a_listed_apply_that_never_ran_is_removed_and_the_change_is_not_claimed(monkeypatch):
    authorship = _gate_with(monkeypatch, ["vool-database.migrate.preview"],
                            ["vool-database.migrate.preview", "vool-database.migrate.apply"])
    reply = ("Adding the 'email' column to the 'customers' table in the 'orders' database.\n"
             "- vool-database.migrate.preview\n- vool-database.migrate.apply")
    text, payload = authorship._unrun_listed_intents(reply, {"session_id": "s"})
    assert "Adding the 'email' column" not in text and "This change has not been made." in text
    assert "- vool-database.migrate.apply" not in text and "- vool-database.migrate.preview" in text
    assert "`vool-database.migrate.apply`, which did not run in this turn" in text
    assert payload["execution_claims"] == ["vool-database.migrate.apply"]


def test_a_reply_whose_listed_intents_all_ran_is_left_alone(monkeypatch):
    authorship = _gate_with(monkeypatch, ["vool-database.migrate.preview"], ["vool-database.migrate.preview"])
    reply = "Here is the preview of the change.\n- vool-database.migrate.preview"
    assert authorship._unrun_listed_intents(reply, {"session_id": "s"}) is None


# --- sweep block 1b (2026-11-12) ---------------------------------------------------------------


def test_a_load_waits_for_unloaded_memory_to_come_back(monkeypatch):
    from types import SimpleNamespace

    import core.resource_governor as governor

    readings = iter([2.1, 2.1, 3.0, 9.5])  # before, right after the unload, settling, settled
    monkeypatch.setattr(governor, "_local_ollama_base", lambda _b=None: "http://127.0.0.1:11434")
    monkeypatch.setattr(governor.sr, "snapshot", lambda **_k: SimpleNamespace(available_ram_gb=next(readings)))
    monkeypatch.setattr(governor, "resident_models", lambda **_k: [{"name": "qwen3:4b"}, {"name": "gpt-oss:20b"}])
    monkeypatch.setattr(governor, "estimated_model_gb", lambda _n, **_k: 5.5)
    monkeypatch.setattr(governor, "_unload_models", lambda names, **_k: list(names))
    monkeypatch.setattr(governor.time, "sleep", lambda _s: None)
    decision = governor.plan_model_load("qwen3:8b")
    assert decision.ok and decision.usable_after_gb == 9.5


def _boot_manifest(name):
    from types import SimpleNamespace

    return SimpleNamespace(model_name=name, provider_id=f"ollama-local:{name}")


def test_boot_check_skips_a_model_too_big_for_this_computer_and_stops_once_one_is_ready(monkeypatch):
    import core.local_model_readiness as readiness

    monkeypatch.setattr("core.local_model_tool_certification.certification_applies", lambda _m: True)
    checked: list[str] = []

    def _run(manifest):
        checked.append(manifest.model_name)
        return {"state": "degraded"}

    rows = readiness.ready_local_models(
        [_boot_manifest("qwen3.8:27b"), _boot_manifest("qwen2.5:7b"), _boot_manifest("qwen3:4b")],
        primary_model="qwen3.8:27b", status_of=lambda _m: {"state": "unknown"}, run_check=_run,
        fits=lambda m: m.model_name != "qwen3.8:27b", ready_now=lambda: len(checked) >= 1,
    )
    assert checked == ["qwen2.5:7b"]  # 27b never loaded; after the first check a model was ready
    assert rows[0] == {"model": "qwen3.8:27b", "readiness": "too_large_for_this_computer"}


# --- sweep block 1c (2026-11-12): general recall grammar, not the sweep's words ------------------


@pytest.mark.parametrize(
    "said,read",
    [("whats my fave cafe", "what's my favourite cafe?"), ("my gym membership nr pls", "my gym membership number"),
     ("your locker pw?", "what's my locker password?"), ("ur dentist appt?", "what's my dentist appointment?")],
)
def test_chat_shorthand_reads_as_the_full_words(said, read):
    from core.agent_runtime.cross_chat_recall import as_question

    assert as_question(said) == read


@pytest.mark.parametrize("said", ["train seat number?", "storage unit pin?", "dentist appointment time?"])
def test_a_bare_record_name_as_a_first_message_asks_for_the_persons_record(said):
    from core.agent_runtime.cross_chat_recall import as_question, bare_record_ask

    assert bare_record_ask(as_question(said)) == "what's my " + said.rstrip("?") + "?"


@pytest.mark.parametrize("said", ["and pencil?", "plus 50?", "capital of Peru?", "weather in Riga?", "what day is it?"])
def test_continuations_and_world_questions_are_not_record_asks(said):
    from core.agent_runtime.cross_chat_recall import as_question, bare_record_ask

    assert bare_record_ask(as_question(said)) == ""


def test_i_saved_it_somewhere_is_a_recall():
    from core.agent_runtime.cross_chat_recall import _EXPLICIT_RECALL_RE

    assert _EXPLICIT_RECALL_RE.match("i stored a wifi password somewhere for the cabin, what was it")


@pytest.mark.parametrize("reply", ["Got it — we’re one more. Let me adjust that.", "Okay, noted. I'll recalculate the split."])
def test_an_acknowledged_promise_to_redo_the_math_is_incomplete(reply):
    from core.incomplete_answer import _only_a_plan

    assert _only_a_plan(reply)


@pytest.mark.parametrize("reply", ["Got it — 7 people now. That makes 25.71 each.", "Yes, it is Venus."])
def test_an_acknowledged_answer_with_content_is_not_a_plan(reply):
    from core.incomplete_answer import _only_a_plan

    assert not _only_a_plan(reply)


@pytest.mark.parametrize(
    "question",
    ["who lives in Oslo in the customers table?", "which supplier is cheapest in my vendors sheet?",
     "what does Mercury cost in our price list?"],
)
def test_a_question_about_the_persons_own_data_is_never_a_world_ambiguity(question):
    from core.entity_ambiguity import single_plain_know_question

    assert not single_plain_know_question(question)


@pytest.mark.parametrize(
    "said,url",
    [("whats on python.org", "https://python.org"), ("go to wikipedia.org and summarize it", "https://wikipedia.org"),
     ("open the website lrt.lt", "https://lrt.lt"), ("can you read www.gov.uk/browse for me", "https://www.gov.uk/browse")],
)
def test_a_bare_web_address_after_a_read_verb_is_read(said, url):
    from core.agent_runtime.fast_paths_web import url_read_request

    assert url_read_request(said) == {"url": url}


@pytest.mark.parametrize(
    "said", ["open budget.csv", "read house-notes.md", "my site is mysite.com", "write a script to open github.com"],
)
def test_files_and_mentions_are_not_web_reads(said):
    from core.agent_runtime.fast_paths_web import url_read_request

    assert url_read_request(said) is None


def test_a_completed_change_is_not_claimed_when_only_a_preview_ran(monkeypatch):
    authorship = _gate_with(monkeypatch, ["vool-database.migrate.preview", "vool-database.schema"],
                            ["vool-database.migrate.preview", "vool-database.migrate.apply", "vool-database.schema"])
    reply = "The column 'phone' has been successfully added to the 'members' table.\n- Previewed the migration on a copy."
    text, payload = authorship._unrun_listed_intents(reply, {"session_id": "s"})
    assert "successfully added" not in text and "This change has not been made" in text
    assert payload["publication"] == "unrun_completion_claim_corrected"


def test_a_completed_change_stands_when_the_change_ran(monkeypatch):
    authorship = _gate_with(monkeypatch, ["vool-database.migrate.apply"], ["vool-database.migrate.apply"])
    assert authorship._unrun_listed_intents("The column has been added.", {"session_id": "s"}) is None


def test_a_mail_question_with_no_mailbox_gets_the_plain_truth(monkeypatch):
    import core.email_accounts as accounts
    from core.agent_runtime.agent import _no_mailbox_reply

    monkeypatch.setattr(accounts, "configured_accounts", lambda: {})
    assert "No mailbox is connected" in _no_mailbox_reply("any new mail from the bank today?")
    assert _no_mailbox_reply("what is an email header?") is None


def test_a_connected_mailbox_leaves_mail_questions_to_the_email_tools(one_account):
    from core.agent_runtime.agent import _no_mailbox_reply

    assert _no_mailbox_reply("any new mail from the bank today?") is None


@pytest.mark.parametrize("asked", ["have i got any appointments on monday?", "what's on my agenda for the weekend",
                                   "am i busy thursday afternoon?"])
def test_a_schedule_question_with_no_calendar_gets_the_plain_truth(monkeypatch, asked):
    from core.agent_runtime.agent import _no_calendar_reply

    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda **_k: [])
    monkeypatch.delenv("VOOL_CALENDAR_PROVIDER", raising=False)
    assert "No calendar is connected" in _no_calendar_reply(asked)


@pytest.mark.parametrize("asked", ["what is a calendar year?", "schedule a meeting with Ana on friday",
                                   "how do events in javascript work?"])
def test_non_schedule_questions_and_orders_are_left_alone(monkeypatch, asked):
    from core.agent_runtime.agent import _no_calendar_reply

    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda **_k: [])
    assert _no_calendar_reply(asked) is None


def test_a_connected_calendar_leaves_schedule_questions_to_the_calendar(monkeypatch):
    from core.agent_runtime.agent import _no_calendar_reply

    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda **_k: ["one"])
    assert _no_calendar_reply("am i busy thursday afternoon?") is None


@pytest.mark.parametrize("text", ["thanks", "thanks,", "ok thank you!", "according to wikipedia", "per the docs"])
def test_courtesies_and_attributions_are_not_unanswered_demands(text):
    from core.finalization import _is_a_demand

    assert not _is_a_demand(text)


@pytest.mark.parametrize("text", ["what's 7 plus 8", "how tall is the tower", "thanks for the list, now sort it"])
def test_real_asks_stay_demands(text):
    from core.finalization import _is_a_demand

    assert _is_a_demand(text)


# --- Sweep block 3 (b): version and update questions in plain words ------------------------------

def _version_runtime():
    from types import SimpleNamespace

    return SimpleNamespace(
        runtime_version_stamp={"release_version": "0.7.8", "build_id": "0.7.8+9f3e21", "commit": "9f3e21aa77",
                               "channel_name": "stable"},
        runtime_model_tag="qwen3:8b",
    )


class _Updater:
    def __init__(self, payload):
        self.payload = payload

    def status_payload(self):
        return dict(self.payload)


@pytest.mark.parametrize("asked", ["what version r u", "ur version?", "which vool build is this"])
def test_version_asks_in_chat_shorthand_get_the_version_without_the_hash(asked):
    from core.web.api.service import runtime_version_response

    result = runtime_version_response(asked, _version_runtime())
    assert result is not None
    assert result["response"].startswith("You're on VOOL 0.7.8 (stable channel).")
    assert "9f3e21" not in result["response"]


@pytest.mark.parametrize("asked", ["is there an update?", "r u up to date", "is a newer version of vool out"])
def test_update_asks_say_what_the_updater_knows(monkeypatch, asked):
    import core.updater.runtime as updater
    from core.web.api.service import runtime_version_response

    monkeypatch.setattr(updater, "get_update_subsystem", lambda: _Updater({"configured": True, "online_access": False}))
    off = runtime_version_response(asked, _version_runtime())["response"]
    assert "Update checks are off on this computer, so I can't tell whether a newer version exists." in off
    assert "up to date" not in off.replace("r u up to date", "")

    monkeypatch.setattr(updater, "get_update_subsystem",
                        lambda: _Updater({"configured": True, "online_access": True, "target_version": "0.7.9"}))
    assert "Version 0.7.9 is available." in runtime_version_response(asked, _version_runtime())["response"]

    monkeypatch.setattr(updater, "get_update_subsystem",
                        lambda: _Updater({"configured": True, "online_access": True, "target_version": ""}))
    assert "No newer version has been found." in runtime_version_response(asked, _version_runtime())["response"]


@pytest.mark.parametrize("asked", ["any updates on my order?", "is my passport up to date",
                                   "is there an update on the bakery delivery", "what is the latest version of python",
                                   "can u build me a list"])
def test_other_peoples_updates_and_versions_are_not_answered_with_vools(asked):
    from core.web.api.service import runtime_version_response

    assert runtime_version_response(asked, _version_runtime()) is None


# --- Sweep block 3 (c): a folder is named the way the person knows it --------------------------------

def test_a_project_folder_is_shown_by_its_name_not_vools_storage_path(tmp_path):
    from core.folder_overview import build_folder_overview

    root = tmp_path / "vool-data" / "projects" / "Garden"
    root.mkdir(parents=True)
    (root / "seeds.md").write_text("tomatoes, basil\n", encoding="utf-8")
    text = build_folder_overview(str(root), project_bound=True)
    assert "`Garden`" in text
    assert str(tmp_path) not in text


def test_an_empty_folder_under_home_is_shown_from_home(tmp_path, monkeypatch):
    from core.folder_overview import _empty_guidance

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Documents" / "trip"
    folder.mkdir(parents=True)
    text = _empty_guidance(str(folder), project_bound=True)
    assert "`~/Documents/trip`" in text and str(tmp_path) not in text


# --- Sweep block 3 (d): no setting names in the calendar reply --------------------------------------

@pytest.mark.parametrize("tool", ["check_availability", "list_calendars", "inspect_calendar_event"])
def test_no_calendar_reasons_name_the_settings_page_not_variables(tool):
    from core.operator.registry import _operator_capability_unavailable_reason

    reason = _operator_capability_unavailable_reason(tool)
    assert "Settings under Calendars" in reason
    assert "VOOL_" not in reason and "runtime" not in reason


# A stated address is not a mailbox read (found by the hive transcript-paste suite: "my email is
# x@y" in a pasted chat got the no-mailbox reply instead of the hive lane).
@pytest.mark.parametrize("text", ["my email is researcher@example.test and here is the backstory",
                                  "fyi my email is now ada@mail.lt", "my email: bo@x.io",
                                  "our email address is team@shop.example"])
def test_stating_an_email_address_is_not_a_mailbox_read(text):
    from core.agent_runtime.agent import _no_mailbox_reply
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents(text) == ()
    assert _no_mailbox_reply(text) is None


@pytest.mark.parametrize("text", ["is my email from ada@x.io here yet?", "did my email to ben@x.io bounce",
                                  "read my latest email"])
def test_mail_asks_that_mention_an_address_still_read_mail(text):
    from core.tool_demand_signals import mailbox_retrieval_intents

    assert mailbox_retrieval_intents(text) == ("email.read",)


# Database run 9: after five library-database turns, "how many titles do we have in total" got
# "I don't have that saved" from the recall lane.
@pytest.mark.parametrize("asked", ["how many titles do we have in total", "which member joined most recently?",
                                   "what's the oldest one?"])
def test_a_follow_up_in_a_database_chat_is_the_chats_not_a_recall(monkeypatch, asked):
    import core.agent_runtime.cross_chat_recall as recall

    monkeypatch.setattr(recall, "_owner_local", lambda _ctx: True)
    monkeypatch.setattr(recall, "_private", lambda *_a: False)
    monkeypatch.setattr(recall, "_short_question_the_owner_answered", lambda *_a: True)

    def _no_search(*_a, **_k):
        raise AssertionError("the recall lane searched other chats")

    monkeypatch.setattr("core.chat_housekeeping.search_chats", _no_search)
    ctx = {"conversation_history": [{"role": "user", "content": "what do I have in my library db"},
                                    {"role": "assistant", "content": "books, loans and members"},
                                    {"role": "user", "content": asked}]}
    assert recall.cross_chat_recall(asked, session_id="openclaw:" + "d" * 20, source_context=ctx) is None


def test_saying_saved_still_recalls_inside_a_database_chat():
    from core.agent_runtime.cross_chat_recall import _EXPLICIT_RECALL_RE, _chat_works_on_a_data_store

    assert _chat_works_on_a_data_store({"conversation_history": [{"role": "user", "content": "show me the tasks tabel"}, {"role": "user", "content": "x"}]})
    assert not _chat_works_on_a_data_store({"conversation_history": [{"role": "user", "content": "my gate code is 4471"}, {"role": "user", "content": "x"}]})
    assert _EXPLICIT_RECALL_RE.match("what was the wifi password i saved somewhere")


# --- Sweep block 2c: web pages, digests and web-off answers ----------------------------------------

_IANA_HTML = (
    "<!doctype html>\n<html>\n<head>\n\t<title>Example Domains</title>\n\t<meta charset=\"utf-8\" />\n"
    "<link rel=\"stylesheet\" href=\"/static/css/site.css\"/>\n<script>var x = 1;</script>\n</head>\n<body>\n"
    "<nav>Domains Protocols Numbers</nav>\n<h1>Example Domains</h1>\n"
    "<p>As described in RFC 2606 and RFC 6761, a number of domains such as example.com and example.org are "
    "maintained for documentation purposes.</p>\n</body>\n</html>"
)


class _HtmlResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self._body, self.headers = body, {"content-type": content_type}

    def geturl(self):
        return "https://www.iana.org/help/example-domains"

    def read(self, _n):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _fetch_with(monkeypatch, body: bytes, content_type: str):
    import core.runtime_execution_tools as rt

    monkeypatch.setattr(rt, "_remote_fetch_allowed", lambda _ctx: True)
    monkeypatch.setattr("core.remote_fetch_policy.open_remote", lambda _req, timeout=0, **_kw: _HtmlResponse(body, content_type))
    return rt._web_fetch({"url": "https://www.iana.org/help/example-domains"}, source_context={})


def test_a_fetched_web_page_is_its_words_not_its_markup(monkeypatch):
    result = _fetch_with(monkeypatch, _IANA_HTML.encode(), "text/html; charset=utf-8")
    text = result.details["text"]
    assert result.ok and text.startswith("Example Domains")
    assert "RFC 2606" in text
    for markup in ("<!doctype", "<meta", "stylesheet", "var x", "<p>"):
        assert markup not in text


def test_a_plain_text_fetch_is_left_as_it_came(monkeypatch):
    body = b'{"slideshow": {"title": "Sample <b>Slide</b> Show"}}'
    result = _fetch_with(monkeypatch, body, "application/json")
    assert result.details["text"] == body.decode()


class _DigestAgent:
    persona_id = "default"

    class ResponseClass:
        UTILITY_ANSWER = "utility"

    def __init__(self) -> None:
        self.model_inputs: list[str] = []

    def _chat_surface_live_info_model_input(self, *, user_input, query, mode, notes, runtime_note=""):
        from core.agent_runtime.chat_surface import live_info_model_input

        return live_info_model_input(self, user_input=user_input, query=query, mode=mode, notes=notes,
                                     runtime_note=runtime_note)

    def _chat_surface_model_wording_result(self, **kwargs):
        self.model_inputs.append(kwargs["model_input"])
        return {"response": "MODEL-SUMMARY", "reason": kwargs["reason"]}

    def _fast_path_result(self, *, response: str, reason: str, **_: object):
        return {"response": response, "reason": reason}

    def _emit_runtime_event(self, *_a, **_k):
        return None


@pytest.mark.parametrize("asked", ["summarize https://www.iana.org/help/example-domains for me",
                                   "can you check https://www.iana.org/help/example-domains and tell me the gist",
                                   "open https://www.iana.org/help/example-domains and explain it in two lines"])
def test_a_summary_of_a_page_is_written_by_a_model_from_the_fetched_text(monkeypatch, asked):
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_web

    page = "Example Domains\nAs described in RFC 2606 and RFC 6761, example.com is kept for documentation."
    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=True, status="ok", response_text="Fetched.",
            details={"text": page, "final_url": "https://www.iana.org/help/example-domains"}),
    )
    agent = _DigestAgent()
    result = fast_paths_web.maybe_handle_url_read(agent, asked, session_id="t", source_context={},
                                                  interpretation=SimpleNamespace(raw_text=asked))
    assert result == {"response": "MODEL-SUMMARY", "reason": "web_fetch_digest"}
    assert "RFC 2606" in agent.model_inputs[0] and "Answer ONLY using" in agent.model_inputs[0]


def test_reading_a_page_out_still_shows_the_page(monkeypatch):
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_web

    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=True, status="ok", response_text="Fetched.",
            details={"text": "Example Domains\nRFC 2606 text", "final_url": "https://example.org"}),
    )
    agent = _DigestAgent()
    result = fast_paths_web.maybe_handle_url_read(agent, "open https://example.org and tell me what the page says",
                                                  session_id="t", source_context={},
                                                  interpretation=SimpleNamespace(raw_text="x"))
    assert "RFC 2606 text" in str(result["response"]) and not agent.model_inputs


@pytest.mark.parametrize("cause, sentence", [
    ("Web lookups are off. You can turn them on in Settings, Privacy & Permissions.", "Web lookups are off, so I didn't open example.org."),
    ("Web fetch for `https://example.org` failed: <urlopen error timed out>", "I couldn't open example.org: <urlopen error timed out>."),
])
def test_a_failed_page_read_is_said_plainly(monkeypatch, cause, sentence):
    from core.agent_runtime.fast_paths_web import _failure_text

    monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda: False)
    monkeypatch.setattr("core.remote_fetch_policy.web_unavailable_reason",
                        lambda _ctx=None: __import__("core.remote_fetch_policy", fromlist=["x"]).WEB_OFF_TEXT)
    reply = _failure_text("unsupported" if "off" in cause else "error", reason=cause, url="https://example.org")
    assert reply.startswith(sentence)
    assert "`" not in reply and ".." not in reply and "runtime" not in reply


# Sweep block 2c (7): every routing plan named an uninstalled qwen3.5:9b as its selected model.
def test_a_routing_plan_never_selects_a_local_model_that_is_not_installed():
    from core import turn_routing
    from tests.test_a9_turn_routing_authority import _manifest

    missing, present = _manifest("ollama-local", "qwen3.5:9b"), _manifest("ollama-local", "qwen3:8b")
    plan = turn_routing.mint_turn_routing_plan(
        turn_id="t1", session_id="s1", manifests=[missing, present],
        not_installed_provider_ids=frozenset({missing.provider_id}),
    )
    assert plan.selected_model == "qwen3:8b"
    assert {row.model_id: row.reason for row in plan.eligibility}["qwen3.5:9b"] == "not_installed"
    assert missing.provider_id not in plan.fallback_ladder


def test_a_pin_on_a_model_that_is_not_installed_does_not_silently_widen():
    from core import turn_routing
    from tests.test_a9_turn_routing_authority import _manifest

    missing = _manifest("ollama-local", "qwen3.5:9b")
    plan = turn_routing.mint_turn_routing_plan(
        turn_id="t3", session_id="s1", requested_model="qwen3.5:9b",
        manifests=[missing, _manifest("ollama-local", "qwen3:8b")],
        not_installed_provider_ids=frozenset({missing.provider_id}),
    )
    assert plan.selected_model == "" and plan.reason == "pin_unresolved"


def test_each_local_model_is_judged_by_its_own_servers_inventory(monkeypatch):
    import core.memory_first_router as router
    from tests.test_a9_turn_routing_authority import _manifest

    inventories = {"http://127.0.0.1:11434": frozenset({"qwen3:8b"}), "http://127.0.0.1:5999": frozenset()}
    monkeypatch.setattr(router, "_installed_model_names_at", lambda base: inventories.get(base, frozenset()))
    assert router._not_installed_local(_manifest("ollama-local", "qwen3.5:9b"))
    assert not router._not_installed_local(_manifest("ollama-local", "qwen3:8b"))
    # A provider on another server whose inventory is unknown (a stub, a second runtime) is never "missing".
    assert not router._not_installed_local(_manifest("ollama-local", "stub:1b", base_url="http://127.0.0.1:5999"))


@pytest.mark.parametrize("asked", ["go to example.net and give me the gist", "go to https://example.org",
                                   "please go to the website bbc.co.uk and read it"])
def test_going_to_a_web_address_is_not_travel(asked):
    from core.action_availability import unavailable_action_report

    assert unavailable_action_report(asked) is None


@pytest.mark.parametrize("asked", ["go to the kitchen and bring me water", "fly to rome"])
def test_going_to_a_place_is_still_travel(asked):
    from core.action_availability import unavailable_action_report

    assert unavailable_action_report(asked) is not None


@pytest.mark.parametrize("asked", ["how warm is it in tallinn this minute", "how cold is it in oslo right now",
                                   "how hot is it out in phoenix"])
def test_how_it_feels_at_a_place_is_a_weather_ask(asked):
    from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request

    assert _looks_like_live_weather_request(asked)


@pytest.mark.parametrize("asked", ["how hot is it in the oven for bread", "how hot is the sun", "is it rainbow colored"])
def test_how_hot_without_a_named_place_is_not_a_weather_ask(asked):
    from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request

    assert not _looks_like_live_weather_request(asked)


@pytest.mark.parametrize("asked", ["Answer using the imported prior conversations. How much does James pay?",
                                   "based on what I told you, when is my flight?",
                                   "how many tonnes does the yard hold according to my notes?"])
def test_an_ask_to_answer_from_ones_own_records_is_recognized(asked):
    from core.agent_runtime.turn_frontdoor import _ASKS_FROM_OWN_RECORDS_RE

    assert _ASKS_FROM_OWN_RECORDS_RE.search(asked)


@pytest.mark.parametrize("asked", ["according to wikipedia, how long is the danube", "search the web for the population of portugal"])
def test_a_web_source_is_not_ones_own_records(asked):
    from core.agent_runtime.turn_frontdoor import _ASKS_FROM_OWN_RECORDS_RE

    assert not _ASKS_FROM_OWN_RECORDS_RE.search(asked)


@pytest.mark.parametrize("asked", ["wikipedia: what year did the titanic sink", "check wiki for how tall kilimanjaro is",
                                   "what does the wiki say about the eifel tower", "wiki: capital of mongolia",
                                   "according to the wiki how old is rome"])
def test_wiki_as_people_write_it_is_a_web_lookup(asked):
    from core.tool_demand_signals import resolve_demand_signals

    assert "web" in resolve_demand_signals(asked).required_families


@pytest.mark.parametrize("asked", ["whats a wiki", "how do I set up a wiki for my team", "search our wiki for the onboarding doc",
                                   "put it on the wiki for my team"])
def test_a_wiki_of_ones_own_is_not_wikipedia(asked):
    from core.tool_demand_signals import resolve_demand_signals

    assert "web" not in resolve_demand_signals(asked).required_families


_DANUBE = [{"summary": "The Danube is Europe's second-longest river, at about 2,850 km (1,770 mi) long.",
            "result_url": "https://en.wikipedia.org/wiki/Danube", "origin_domain": "en.wikipedia.org"}]


@pytest.mark.parametrize("answer", ["The Danube is about 2,850 miles long.",
                                    "The Danube spans approximately 2,850 miles (4,580 kilometers)."])
def test_a_number_in_the_wrong_unit_is_not_supported(answer):
    from core.claim_support import match_claims

    claims = match_claims(answer=answer, notes=_DANUBE, request_text="per wikipedia, how long is the danube?").claims
    assert claims and all(claim.status != "supported" for claim in claims)


@pytest.mark.parametrize("answer", ["The Danube is about 2,850 km long.", "The Danube is about 1,770 miles long.",
                                    "The Danube is about 2,850 kilometres (1,770 miles) long."])
def test_the_same_number_in_the_same_unit_is_supported(answer):
    from core.claim_support import match_claims

    claims = match_claims(answer=answer, notes=_DANUBE, request_text="per wikipedia, how long is the danube?").claims
    assert claims and all(claim.status == "supported" for claim in claims)


@pytest.mark.parametrize("asked", ["add sunscreen and a torch and make the pdf again", "pls add sunscreen + torch, new pdf",
                                   "update it with sunscreen and a flashlight and give me the pdf again",
                                   "swap the tent for a hammock and send me an updated pdf", "add bug spray, fresh pdf please"])
def test_an_edit_that_orders_the_file_again_orders_a_file(asked):
    from core.tool_demand_signals import asks_for_a_file, resolve_demand_signals

    assert "file.deliver" in resolve_demand_signals(asked).explicit_intents
    assert asks_for_a_file(asked)


@pytest.mark.parametrize("asked", ["what is a pdf again?", "is the new pdf standard better?"])
def test_a_question_about_pdfs_orders_nothing(asked):
    from core.tool_demand_signals import asks_for_a_file

    assert not asks_for_a_file(asked)


@pytest.mark.parametrize("asked", ["Is anything said in the attached video?", "what's in this screenshot?",
                                   "summarize the uploaded pdf"])
def test_a_question_about_an_attachment_is_not_a_recall(asked):
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert _owned_by_another_lane(as_question(asked))


def test_a_refusal_after_the_checked_model_failed_names_that_model_first():
    from core.final_answer_authorship import _plain_refusal_sentence

    sentence = _plain_refusal_sentence("", "ollama-local:qwen3:4b", ("ollama-local:qwen3:8b",))
    assert sentence.startswith("The checked model (qwen3:8b) couldn't finish this answer")
    assert "qwen3:4b" not in sentence


@pytest.fixture
def _refused_listing(tmp_path):
    """The chat's record: 'list the pics in my downloads' was refused (v3 asserts behaviour, not the old wrapper text)."""
    from core import conversation_state
    from core import runtime_continuity as rc

    rc.configure_runtime_continuity_db_path(tmp_path / "state.db")
    conversation_state.record_sealed_turn(
        "retry-chat", "list the pics in my downloads",
        {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
         "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": False, "status": "blocked_by_mode"}}]},
    )
    yield
    rc.configure_runtime_continuity_db_path(None)


@pytest.mark.parametrize("asked, change", [("Try again, I fixed permissions", None), ("ok tools are on now, go", None),
                                           ("retry pls", None), ("try again but only jpgs", "only jpgs")])
def test_a_bare_retry_redoes_the_last_request(asked, change, _refused_listing):
    from core import conversation_state

    got = conversation_state.resolve("retry-chat", asked)
    if change is None:
        # the refused request runs again, as the user's own words
        assert (got.kind, got.request) == ("retry", "list the pics in my downloads")
    else:
        # a change said with the retry is kept: the message is not swallowed into a blind rerun
        assert got.kind == "retry_changed" and change in got.text
        assert "list the pics in my downloads" in got.note


def test_a_question_about_retrying_is_not_a_retry(_refused_listing):
    from core import conversation_state

    assert conversation_state.resolve("retry-chat", "how do I try again?").kind == "none"


@pytest.mark.parametrize("asked", ["Just fetch the latest 10 pics that you can find in that folder", "find in that folder the tax files"])
def test_that_folder_is_never_a_disk_search_for_a_name(asked):
    from core.execution.constants import machine_folder_search_intent

    assert machine_folder_search_intent(asked) is None


def test_a_multi_part_web_task_stays_with_the_conductor():
    import re

    text = ("Search the live web for the current price of BTC and gold. Once you have both, calculate the ratio.")
    sentences = [p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if p.strip()]
    assert len(sentences) == 2


def test_a_refused_turn_seals_as_refused_with_its_request():
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    payload = {"response": "Web lookups are off, so I can't look this up online.", "success": False,
               "status": "web_off", "task_outcome": "refused",
               "refused_request": {"reason": "web_off_lookup_refusal", "request": "look up the olympics news"}}
    outcome = terminal_fulfillment_outcome(payload)
    as_dict = outcome if isinstance(outcome, dict) else getattr(outcome, "__dict__", {}) or outcome._asdict()
    text = str(as_dict)
    assert "blocked" in text and "web_off_lookup_refusal" in text, text


def test_a_data_question_in_a_database_chat_gets_no_records_capsule(monkeypatch):
    from core import bootstrap_context

    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")

    def _never(*_a, **_k):
        raise AssertionError("the records capsule was built for a database question")

    monkeypatch.setattr("core.context_retrieval.inject_retrieved", _never)
    transcript = [{"role": "user", "content": "what do I have in my library db"},
                  {"role": "assistant", "content": "books, members, loans"}]
    out = bootstrap_context._with_semantic_memory_capsule(
        transcript, session_id="openclaw:" + "e" * 20, current_user_text="how many titles do we have in total")
    assert out == transcript


def test_asking_what_was_said_in_a_database_chat_still_reads_the_records():
    from core import bootstrap_context

    assert bootstrap_context._asks_for_what_was_said("what did I say about the members table earlier?")


@pytest.mark.parametrize("asked", ["which pdfs are in my documents", "find my tax folder", "how many photos on my desktop"])
def test_a_files_question_is_never_a_memory_recall(asked):
    from core.agent_runtime.cross_chat_recall import _owned_by_another_lane, as_question

    assert _owned_by_another_lane(as_question(asked))


@pytest.mark.parametrize("asked, expect", [
    ("just the 2 newest there", {"path": "~/Documents", "kind": "pdf", "limit": 2, "newest_first": True}),
    ("how many are in there", {"path": "~/Documents", "kind": "pdf", "count_only": True}),
    ("same for photos in that folder", {"path": "~/Documents", "kind": "image", "limit": 200, "newest_first": False}),
    ("same for videos", {"path": "~/Documents", "kind": "video", "limit": 200, "newest_first": False}),
])
def test_a_follow_up_on_a_listing_keeps_its_folder_and_kind(asked, expect):
    from core.execution.constants import machine_kind_listing_intent

    assert machine_kind_listing_intent(asked, folder_hint="~/Documents", kind_hint="pdf") == expect


@pytest.mark.parametrize("asked", ["how many planets are there", "I love photos", "same for videos"])
def test_no_listing_without_a_folder_in_view(asked):
    from core.execution.constants import machine_kind_listing_intent

    hint = {} if asked == "same for videos" else {"folder_hint": "~/Documents", "kind_hint": "pdf"}
    assert machine_kind_listing_intent(asked, **hint) is None


@pytest.mark.parametrize("text", ["just the count", "only the number please", "keep it short", "count only"])
def test_how_to_answer_is_not_a_demand(text):
    from core.finalization import _is_a_demand

    assert not _is_a_demand(text)


def test_a_stated_count_is_not_an_announced_list():
    from core.incomplete_answer import _announced_item_count

    assert _announced_item_count("There are 3 PDF files in your Documents folder.") == 0
    assert _announced_item_count("There are 3 options:\n- a\n- b") == 3
    assert _announced_item_count("Here are 3 options") == 3


@pytest.mark.parametrize("asked", ["what time does the sun set in oslo today", "when does it get dark in riga", "sunrise tomorrow in tokyo"])
def test_a_sky_event_is_not_a_clock_read(asked):
    from core.agent_runtime.fast_paths_utility import _SKY_EVENT_RE

    assert _SKY_EVENT_RE.search(asked)


@pytest.mark.parametrize("text", ["i turned on web", "web is on now", "i fixed permissions", "ok i've switched on the tools"])
def test_a_status_remark_is_not_a_demand(text):
    from core.finalization import _is_a_demand

    assert not _is_a_demand(text)


def test_screenshots_are_their_own_kind():
    from core.execution.constants import machine_kind_listing_intent

    assert machine_kind_listing_intent("list the screenshots in my downloads pls")["kind"] == "screenshot"


@pytest.mark.parametrize("reply, claim", [
    ("Visible entries under ~/Desktop:\n- in progress/\n- notes.txt", False),
    ("The weather is fine. One moment of sun around noon.", False),
    ("One moment, let me check.", True),
    ("Audit in progress — one moment.", True),
])
def test_only_vools_own_activity_is_a_progress_claim(reply, claim):
    from core.agent_runtime.action_honesty_validator import _FALSE_PROGRESS_CLAIM_RE

    assert bool(_FALSE_PROGRESS_CLAIM_RE.search(reply)) is claim


# --- database run 13: "The numeric-authority rule applies here." and "Could not be answered: sorry" ---


def _explain(reply: str, request: str = "why do older books cost more?"):
    from core.conductor.node import ConductorNode
    from core.conductor.operations import _explanation_run
    from core.conductor.registry import NodeContext

    seen: dict = {}

    def generate(system_prompt: str, prompt: str) -> str:
        seen["system"], seen["briefing"] = system_prompt, prompt
        return reply

    node = ConductorNode(node_id="n1", operation="general.explanation", request_text=request,
                         arguments={"clause": request})
    return _explanation_run(node, NodeContext(run_generation=generate)), seen


def test_the_explanation_prompt_names_no_rule_for_the_model_to_repeat():
    _result, seen = _explain("Rarity and condition drive the price of older books.")
    for text in (seen["system"], seen["briefing"]):
        assert "numeric-authority" not in text.lower(), text


def test_a_sentence_about_the_rules_is_dropped_from_the_explanation():
    result, _seen = _explain(
        "The numeric-authority rule applies here. Older books are rarer, so collectors pay more for them."
    )
    assert result["text"] == "Older books are rarer, so collectors pay more for them.", result["text"]


@pytest.mark.parametrize("said", ["sorry", "oops", "my bad", "sorry,", "oh and", "um"])
def test_an_apology_or_filler_is_not_listed_as_an_unanswered_part(said):
    from core.finalization import _is_a_demand

    assert not _is_a_demand(said)


@pytest.mark.parametrize("asked", ["sorry, what was the oldest book again", "oops which one was cheapest"])
def test_an_apology_does_not_swallow_the_question_after_it(asked):
    from core.finalization import _is_a_demand

    assert _is_a_demand(asked)


def test_a_real_word_is_not_spell_corrected_into_a_tool_word():
    # Red team R3-02: "how much does a pint of beer cost" ran (and was searched) as "a print of beer".
    from core.input_normalizer import normalize_user_text

    for typed in ("how much does a pint of beer cost in london", "i need a pint glass", "whats a pint in ml"):
        assert "pint" in normalize_user_text(typed).normalized_text.split(), typed


@pytest.mark.parametrize("asked, path", [
    ("count the pdfs in documents/Invoices for me", "~/Documents/Invoices"),
    ("list the pics in downloads/trip-2026", "~/Downloads/trip-2026"),
    ("how many pdfs in my documents", "~/Documents"),
])
def test_a_subfolder_typed_after_a_known_folder_is_the_folder_asked_about(asked, path):
    # Red team R3-07: "count the pdfs in documents/Invoices" counted ~/Documents.
    from core.execution.constants import machine_kind_listing_intent

    assert machine_kind_listing_intent(asked)["path"] == path


def test_the_largest_file_scan_never_lists_voolS_own_home(tmp_path, monkeypatch):
    # Red team R3-04: a largest-file scan of the home folder listed vool_web0_v2.db and vool_api.log.
    from core import machine_diagnostics, runtime_paths

    vool = tmp_path / "vool"
    (vool / "data").mkdir(parents=True)
    (vool / "data" / "vool_web0_v2.db").write_bytes(b"x" * 9000)
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "report.pdf").write_bytes(b"x" * 500)
    monkeypatch.setenv("VOOL_HOME", str(vool))
    runtime_paths.configure_runtime_home(vool)
    try:
        files = [row["name"] for row in machine_diagnostics.largest_files(str(tmp_path))["entries"]]
        folders = [row["name"] for row in machine_diagnostics.largest_entries(str(tmp_path))["entries"]]
    finally:
        runtime_paths.configure_runtime_home(None)
    assert files == ["report.pdf"], files
    assert "vool" not in folders, folders
