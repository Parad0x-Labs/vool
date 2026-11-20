"""A save claim is held to what the turn's keep step wrote, not to the reply's wording.

Measured on the v0.8 line (94ce6169, the real local model, a fresh home; the memory and context diagnosis of
2026-11-16): a fact said without a remember lead was answered "I'll keep that safe" and no row was written; a
passcode was refused and the same reply said `Locked in. I'll remember "it for me".`, with the row "it for me."
saved; a question in the middle of a remember list closed the list, so its next item was lost.

Here: the keep step writes what the person states about their own life before any model answers and records it on
the turn's receipt; the claim gate reads the receipt; a list stays open across a question; a capture whose object
only points back keeps nothing.

All names, places, dates and codes are invented for this contract.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from core.action_claim_gate import gate_action_claims
from core.agent_runtime.memory_runtime import keep_what_the_turn_states
from core.agent_runtime.secret_intake import consume_secret_intake
from core.grounding_lifecycle import record_model_authorship, register_required
from core.grounding_lifecycle import reset_for_tests as reset_lifecycles
from core.memory import everyday_remember as everyday
from core.memory import keep_receipt
from core.memory.entries import list_memory_entries
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command
from core.semantic.semantic_admissions import bound_request_context
from core.turn_model_call_ledger import begin_turn, record_tool_execution
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home

CHAT = "keep-receipt-chat"
_ids = itertools.count()


@pytest.fixture(autouse=True)
def _fresh():
    from core.final_answer_authorship import reset_for_tests

    reset_lifecycles()
    reset_for_tests()
    yield
    reset_lifecycles()
    reset_for_tests()


def _facts(chat: str = CHAT) -> list[str]:
    rows = list_memory_entries(access_policy=_policy(chat), limit=200)
    return [str(row.get("fact") or row.get("text") or "") for row in rows]


def _turn(text: str, chat: str = CHAT) -> tuple[bool, str, dict]:
    """One turn as the runtime runs it: the keep step at the turn's start, then the memory lane at its door."""
    context: dict = {}
    keep_what_the_turn_states(text, session_id=chat, source_context=context, access_policy=_policy(chat))
    with keep_receipt.collecting() as lane_receipt:
        handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat))
    keep_receipt.stamp(context, lane_receipt)
    receipt = SimpleNamespace(as_dict=lambda: context[keep_receipt.CONTEXT_KEY])
    append_conversation_event(
        session_id=chat, user_input=text, assistant_output=str(reply or "ok"), persist_now=True,
        access_policy=_policy(chat),
    )
    return handled, str(reply or ""), receipt.as_dict()


def _gate(reply: str, receipt: dict | None, tools=(), request: str = "help me with this") -> tuple[str, dict]:
    rid = f"req:keep-receipt:{next(_ids)}"
    with bound_request_context(rid):
        context = {"session_id": CHAT, "request_id": rid, "turn_id": rid}
        if receipt is not None:
            context[keep_receipt.CONTEXT_KEY] = receipt
        register_required(context, request_text=request, reason_codes=("test",))
        record_model_authorship(context)
        begin_turn(context)
        for tool in tools:
            record_tool_execution(context, tool)
        return gate_action_claims(reply, turn_id=rid, source_context=context, connected=lambda name: True)


NOTHING_KEPT = {"saved": [], "refused": [], "in_passing": False}


# --- the receipt -------------------------------------------------------------------------------------------------


def test_the_writer_records_each_row_on_the_turns_receipt(isolated_home) -> None:
    handled, reply, receipt = _turn("Remember that my chiropodist is Dr Avenmoor.")
    assert handled and reply.startswith("Locked in")
    assert receipt["saved"] == ["my chiropodist is Dr Avenmoor."]
    assert _facts() == ["my chiropodist is Dr Avenmoor."]


def test_a_turn_that_writes_nothing_has_an_empty_receipt(isolated_home) -> None:
    handled, _reply, receipt = _turn("What's a good name for a goldfish?")
    assert not handled
    assert receipt == NOTHING_KEPT
    assert _facts() == []


def test_a_refused_secret_is_on_the_receipt_and_no_row_is_written(isolated_home) -> None:
    with keep_receipt.collecting() as receipt:
        from core.memory.entries import add_memory_fact

        kept = add_memory_fact("my router admin password is zebra-Mallet-31", session_id=CHAT, scope="chat",
                               access_policy=_policy(CHAT))
    assert kept is False
    assert receipt.as_dict()["refused"] == [keep_receipt.REFUSED_SECRET]
    assert _facts() == []


def test_a_receipt_a_caller_wrote_is_stripped_at_the_door() -> None:
    from core.request_trust import strip_reserved_trust_keys

    assert keep_receipt.CONTEXT_KEY not in strip_reserved_trust_keys(
        {keep_receipt.CONTEXT_KEY: {"saved": ["anything"], "refused": [], "in_passing": False}}
    )


# --- the keep step: a fact stated in passing ---------------------------------------------------------------------


def test_a_fact_said_in_passing_is_written_before_any_model_answers(isolated_home) -> None:
    handled, _reply, receipt = _turn("I'm allergic to quince, by the way. What goes well with roast parsnips?")
    assert not handled  # the question is still the model's to answer
    assert receipt == {"saved": ["I'm allergic to quince"], "refused": [], "in_passing": True}
    assert _facts() == ["I'm allergic to quince"]


@pytest.mark.parametrize(
    "text",
    [
        "What's the best way to descale my kettle?",
        "my code is throwing an error on line 12",
        "I have a question about council tax",
        "I'm trying to fix my bike",
        "I'm a bit lost with this spreadsheet",
        "I have a call in ten minutes",
        "I think that my router is broken",
        "Suppose that my budget is 500, what could I buy?",
        "write a poem about my cat",
    ],
)
def test_a_question_a_request_or_talk_about_the_task_states_no_fact(text: str) -> None:
    assert everyday.stated_facts(text) == []


@pytest.mark.parametrize(
    ("text", "facts"),
    [
        ("My plumber's called Mr Ostrander and our bins go out on the 2nd.",
         ["My plumber's called Mr Ostrander and our bins go out on the 2nd."]),
        ("I live in Skerrow and I work at a bakery.", ["I live in Skerrow and I work at a bakery."]),
        ("our allotment is plot 31", ["our allotment is plot 31"]),
        ("I've got a walnut allergy. Any snack ideas?", ["I've got a walnut allergy."]),
    ],
)
def test_a_fact_about_the_persons_own_life_is_read_by_its_shape(text: str, facts: list[str]) -> None:
    assert everyday.stated_facts(text) == facts


def test_an_ask_to_keep_a_fact_is_read_from_its_shape_not_its_verb(isolated_home) -> None:
    handled, reply, receipt = _turn("pop it in your memory that my nephew's birthday is 19 October.")
    assert handled and reply.startswith("Locked in")
    assert receipt["saved"] == ["my nephew's birthday is 19 October."]


def test_a_note_ask_stays_the_notes_lanes(isolated_home) -> None:
    # "jot down that ..." saves a note (core.operator.notes); the memory lane leaves the turn alone.
    assert everyday.everyday_lead("Jot down that my nephew's birthday is 19 October.") is None
    handled, _reply, receipt = _turn("Jot down that my nephew's birthday is 19 October.")
    assert not handled and receipt["saved"] == []


def test_a_list_item_is_written_once_by_the_lane_not_twice(isolated_home) -> None:
    assert _turn("few things to remember for me: my allotment is plot 31")[0]
    handled, reply, receipt = _turn("also our shed key is with Mrs Tolland")
    assert handled and reply.startswith("Locked in")
    assert receipt == {"saved": ["our shed key is with Mrs Tolland"], "refused": [], "in_passing": False}


def test_a_turn_that_may_not_act_keeps_nothing(isolated_home) -> None:
    context = {"action_policy": "forbidden"}
    keep_what_the_turn_states("I'm allergic to quince.", session_id=CHAT, source_context=context,
                              access_policy=_policy(CHAT))
    assert keep_receipt.CONTEXT_KEY not in context and _facts() == []


def test_one_turn_stamped_twice_holds_both_writes() -> None:
    context: dict = {}
    first, second = keep_receipt.KeepReceipt(saved=["a"], in_passing=True), keep_receipt.KeepReceipt(saved=["b"])
    keep_receipt.stamp(context, first)
    keep_receipt.stamp(context, second)
    assert context[keep_receipt.CONTEXT_KEY] == {"saved": ["a", "b"], "refused": [], "in_passing": True}


@pytest.mark.parametrize(
    "text",
    ["I think that my wifi is down", "Pretend that my name is Corbin", "confirm that my order is shipped",
     "Now that my kids are older, what should I do?", "she said that my car is ready"],
)
def test_what_stands_in_front_of_that_without_asking_is_no_keep_ask(text: str) -> None:
    assert everyday.everyday_lead(text) is None


# --- the gate reads the receipt ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "I'll hold on to that for you.",
        "Got it. I'm keeping track of that for you.",
        "Yes, it's saved now.",
        "Your vet's number is now on file.",
        "I've tucked that away for later.",
        "I'll remember that.",
    ],
)
def test_a_save_claim_with_no_write_on_the_receipt_is_corrected(reply: str) -> None:
    published, record = _gate(reply, NOTHING_KEPT)
    assert record["publication"] == "action_claims_corrected", record
    assert "Nothing from this message was saved to memory" in published
    assert reply not in published


def test_asked_whether_it_is_saved_a_reply_that_says_so_is_held_to_the_receipt() -> None:
    reply = 'You\'re welcome! Whatever you meant by "that" is saved, just as you said. Anything else?'
    published, record = _gate(reply, NOTHING_KEPT, request="Thanks, so that is stored now, yes?")
    assert record["publication"] == "action_claims_corrected", record
    assert "is saved" not in published and "Nothing from this message was saved to memory" in published
    # The same sentence under a request that asks nothing about a save is not read by the save words alone.
    assert _gate(reply, NOTHING_KEPT, request="thanks for the recipe")[0] == reply


def test_a_read_or_a_retrieval_does_not_vouch_for_a_save() -> None:
    # No tool ran; the receipt holds no write. (With a tool run the reply may name what that tool kept.)
    published, record = _gate("Here is what I found. I've saved that for you.", NOTHING_KEPT)
    assert record["publication"] == "action_claims_corrected", record
    assert published.startswith("Here is what I found.")


def test_a_tool_run_still_stands_the_check_down() -> None:
    reply = "I've saved that for you."
    published, _record = _gate(reply, NOTHING_KEPT, tools=["notes.create"])
    assert published == reply


def test_a_save_claim_that_matches_the_receipt_stands() -> None:
    reply = "Nice. I'll remember that you're allergic to quince."
    receipt = {"saved": ["I'm allergic to quince"], "refused": [], "in_passing": True}
    published, record = _gate(reply, receipt)
    assert published == reply, record


def test_a_reply_that_never_mentions_the_save_gets_one_line_naming_it() -> None:
    reply = "Roast parsnips go well with thyme and honey."
    receipt = {"saved": ["I'm allergic to quince"], "refused": [], "in_passing": True}
    published, record = _gate(reply, receipt)
    assert published == reply + '\n\nSaved to memory: "I\'m allergic to quince".', record


def test_a_claim_beside_a_refused_write_says_it_was_not_kept() -> None:
    receipt = {"saved": [], "refused": [keep_receipt.REFUSED_SECRET], "in_passing": False}
    published, record = _gate("Got it, I'll keep that safe for you.", receipt)
    assert record["publication"] == "action_claims_corrected", record
    assert published.endswith(
        "I didn't save that. VOOL never keeps PINs, card numbers or passwords in memory, so I won't be able to "
        "tell you later."
    )
    assert "keep that safe" not in published


def test_a_reply_with_no_save_claim_is_untouched_by_an_empty_receipt() -> None:
    reply = "Use a waterproof tin. Got it?"
    published, _record = _gate(reply, NOTHING_KEPT)
    assert published == reply


def test_a_sentence_that_says_vool_looked_is_not_a_save_claim() -> None:
    # Measured on the served cb32d1b6 (the before/after rerun, r6 t29 turn 2): the model repeated the recall lane's
    # own sentence from the turn before; it was cut as a save claim and the nothing-was-saved line put under a reply
    # that claimed no save. A read of memory is judged by the ordinary evidence, as on a turn with no receipt.
    reply = (
        "I don't have that saved, and I looked through your other chats and projects (1 chat) without finding it. "
        "If you tell me, I'll remember it for every chat."
    )
    published, record = _gate(reply, NOTHING_KEPT)
    assert "Nothing from this message was saved to memory" not in published, record
    assert record.get("memory_receipt") != "claims_without_receipt_corrected", record
    assert published == _gate(reply, None)[0]
    # And the ordinary check does not answer it with a line about saving either: it promises to keep nothing.
    # Measured on base 94ce6169 at function level and on the served fb46ab5c (r6 t29 turn 2), the reply came out as
    # "If you tell me, I'll remember it for every chat.\n\nNothing was saved: no tool stored it. ...".
    assert published == reply, record
    # A done-form claim beside it is still held to the receipt.
    published, record = _gate(reply + " I've saved your question for later.", NOTHING_KEPT)
    assert record["memory_receipt"] == "claims_without_receipt_corrected", record
    assert "I've saved your question" not in published and "Nothing from this message was saved to memory" in published


@pytest.mark.parametrize(
    "reply",
    [
        "I looked through what I have saved and there is nothing about a dentist.",
        "I went through everything I remember about the allotment and no plot number is in it.",
        "I checked my memory and your other chats, and the rent day isn't mentioned.",
    ],
)
def test_saying_it_looked_in_memory_promises_to_keep_nothing(reply: str) -> None:
    for receipt in (NOTHING_KEPT, None):
        published, record = _gate(reply, receipt)
        assert published == reply, record


@pytest.mark.parametrize(
    ("reply", "gone"),
    [
        ("I looked and then I saved it for you.", "saved it"),
        ("I checked your calendar and Friday afternoon is free.", "Friday afternoon is free"),
        ("I've stored that for later.", "stored"),
    ],
)
def test_a_save_or_another_sources_read_with_nothing_behind_it_is_still_corrected(reply: str, gone: str) -> None:
    published, record = _gate(reply, NOTHING_KEPT)
    assert published != reply and gone not in published, record


def test_a_turn_the_keep_step_never_saw_is_judged_as_before() -> None:
    reply = "I'm keeping track of that for you."
    published, _record = _gate(reply, None)
    assert published == reply  # the wider wording is read only against a receipt


# --- a list stays open across a question; a named list keeps its items under its name ---------------------------


def test_a_question_in_the_middle_does_not_close_the_list(isolated_home) -> None:
    assert _turn("Remember these for my canoe weekend: a bivvy bag and a tarp.")[0]
    handled, _reply, receipt = _turn("What's a good way to keep a lighter dry?")
    assert not handled and receipt["saved"] == []
    handled, reply, receipt = _turn("And a spare head torch.")
    assert handled and reply.startswith("Locked in")
    assert receipt["saved"] == ["for my canoe weekend: a spare head torch."]
    assert sorted(_facts()) == [
        "for my canoe weekend: a bivvy bag and a tarp.",
        "for my canoe weekend: a spare head torch.",
    ]


def test_a_closing_still_closes_the_list(isolated_home) -> None:
    assert _turn("Remember these for my canoe weekend: a bivvy bag and a tarp.")[0]
    _turn("ok that's all, thanks")
    handled, _reply, receipt = _turn("And a spare head torch.")
    assert not handled and receipt["saved"] == []


# --- a capture that only points back keeps nothing ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["keep it for me.", "save that for later", "store this somewhere safe please"])
def test_a_capture_whose_object_only_points_back_keeps_nothing(isolated_home, text: str) -> None:
    handled, _reply, receipt = _turn(text)
    assert not handled
    assert receipt["saved"] == [] and _facts() == []


@pytest.mark.parametrize(
    "text",
    [
        "My tablet unlock passcode is 730915, keep it for me.",
        "My streaming account password is otter-Lantern-06, hold on to it for me.",
        "My streaming account password is otter-Lantern-06, please stash that somewhere safe.",
    ],
)
def test_an_ask_that_only_points_back_at_a_refused_secret_leaves_nothing_to_answer(text: str) -> None:
    intake = consume_secret_intake(text, owner_local=True)
    assert intake is not None and intake.reply.startswith("I didn't save your")
    assert intake.remainder == ""


def test_a_question_after_a_refused_secret_is_still_asked() -> None:
    intake = consume_secret_intake("My tablet unlock passcode is 730915, is it long enough?", owner_local=True)
    assert intake is not None and intake.remainder == "is it long enough?"


# --- the receipt is read through the serving gate -------------------------------------------------------------------


def test_the_receipt_is_served_through_the_one_gate(isolated_home, monkeypatch) -> None:
    from core.memory import revocation

    context = {"session_id": CHAT, keep_receipt.CONTEXT_KEY: {
        "saved": ["my tram stop is Calder Row", "my bus pass ends in March"], "refused": [], "in_passing": True}}
    asked: list = []

    def gate(payload, chat_id=None, runtime_home=None, **_):
        asked.append((payload, chat_id))
        return "tram" not in payload

    monkeypatch.setattr(revocation, "authorize_payload", gate)
    served = keep_receipt.receipt_for(context)
    assert served["saved"] == ["my bus pass ends in March"] and served["withheld"] == 1
    assert asked == [("my tram stop is Calder Row", CHAT), ("my bus pass ends in March", CHAT)]
    assert context[keep_receipt.CONTEXT_KEY]["saved"] == ["my tram stop is Calder Row", "my bus pass ends in March"]


def test_a_receipt_whose_words_are_withheld_neither_backs_nor_denies_a_save(isolated_home, monkeypatch) -> None:
    from core.memory import revocation

    # A real write: it is what sets up the forget authority the serving gate reads.
    _handled, _reply, receipt = _turn("My tram stop is Calder Row, by the way. What goes well with rye bread?")
    assert receipt["in_passing"] and receipt["saved"] == ["My tram stop is Calder Row"]
    reply = "Good to know. Anything else?"
    published, _record = _gate(reply, dict(receipt))
    assert published.endswith('Saved to memory: "My tram stop is Calder Row".'), published
    monkeypatch.setattr(revocation, "authorize_payload", lambda *a, **k: False)
    published, _record = _gate(reply, dict(receipt))
    assert published == reply  # no kept line with the withheld words, and no "nothing was saved" either
    published, _record = _gate("Got it, I'll keep that safe for you.", dict(receipt))
    assert "Nothing from this message was saved" not in published
    assert "Calder Row" not in published


def test_the_memory_reader_registry_has_every_module_and_public_function() -> None:
    from pathlib import Path

    from core.memory.reader_registry import _audit

    assert _audit(Path(__file__).resolve().parents[1]) == []
