"""An explicit brevity request sets the short-answer contract, and the reader is told so.

Measured on a fresh paid probe: every reader turn ended with "Give a concise final answer.", no
length grammar recognized it, and the reader prompt carried no answer shape beyond the standing
"Keep responses concise but complete." The shipped answers ran a median 42 words around facts
that need one to six, opening with "Based on the records, ..." and restating the question.

The contract after the repair:
  * the grammar of an explicit length request ("give a concise final answer", "short answer
    please", "be brief", "just the answer", "answer in a sentence", ...) sets
    `answer_shape == "short"`, never its topic ("Is the short notebook on the rack?");
  * the reader's system prompt states that shape before the first call, and the one rewrite
    repeats it: answer first, no preamble, no restatement, no source narration;
  * the brevity request outranks the advice-length lift on the same turn;
  * a list or multi-part request keeps every item or part, and a clause that only states the
    length is not counted as a request part of its own;
  * the abstention sentence and any needed safety notice stay.

Every sentence here is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.memory_first_router as mfr
import core.ordinary_chat_response_guard as guard
from core.memory_first_router import MemoryFirstRouter
from core.plain_task_routing import ordinary_plain_request_count

READER_STYLE_TURN = (
    "Answer from the harbor logbook entries I shared earlier. Use only what those entries "
    "support. If they do not support an answer, say you do not know. Give a concise final "
    "answer.\nWhat colour was the kayak Marta rented?"
)

BREVITY_TURNS = [
    READER_STYLE_TURN,
    "What colour was the kayak Marta rented? Give a concise answer.",
    "Which pier does the night ferry use? Short answer please.",
    "Be brief: when does the lighthouse tour start?",
    "Just the answer: which day did the regatta move to?",
    "Keep your answer short. Which harbor did the trawler leave from?",
    "Tell me in a few words why the swing bridge closed.",
    "Answer in a single sentence: who repaired the dock crane?",
    "I'd like a quick summary of the fishing trip.",
    "Where did Ilse moor the sloop? Brief answer only.",
    "Why did the ferry change its route? Explain succinctly.",
    "Please be concise. Which bakery did Tomasz open in the spring?",
    "Could you please give me a short answer: where did the regatta finish?",
    "I would like a brief answer. Who owns the green trawler?",
]

# User-typed forms: no article, no punctuation, shouting, typos, casual tails, bare fragments.
SLOPPY_BREVITY_TURNS = [
    "which pier for the night ferry give concise answer",
    "WHAT COLOUR WAS THE KAYAK. GIVE A CONCISE FINAL ANSWER",
    "when does the lighthouse tour start, short answer pls",
    "who fixed the dock crane?? be breif",
    "give me a consise answer what day did the regatta move",
    "just the answer pls - which harbor did the trawler leave from",
    "keep it short. why did the swing bridge close",
]

NO_LENGTH_REQUEST_TURNS = [
    "What colour was the kayak Marta rented?",
    "Is the short notebook on the rack?",
    "Why did the ferry change its route?",
    "Use 'ephemeral' in a sentence.",
    "I don't want a short answer, explain how the tides work.",
    "No need to be brief, walk me through the harbor history.",
    "Which brief did the lawyer file for the harbor dispute?",
]

# Reads like a length request, but the length words name the TOPIC, not the reply.
NEAR_MISS_TURNS = [
    "What is the short answer to the halting problem?",
    "Did Marta give a brief answer when the harbor master asked about the kayak?",
    "Which short summary did the ferry company publish?",
    "Should the clerk give a short answer to the inspector?",
]


def _policy(text: str, **kwargs) -> dict:
    return guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text=text, **kwargs
    )


def _reader_request(user_text: str, history: list[tuple[str, str]] | None = None):
    """Build the provider request for one turn; `normalize_prompt` is replaced by a fixed base."""
    wire = [
        {"role": "system", "content": "BASE SYSTEM"},
        *({"role": role, "content": content} for role, content in history or []),
        {"role": "system", "content": "<retrieved_context>harbor log</retrieved_context>"},
        {"role": "user", "content": user_text},
    ]
    internal = SimpleNamespace(
        metadata={},
        temperature=0.2,
        max_output_tokens=256,
        context_summary="",
        trace_id="brevity-trace",
        attachments=(),
        messages=[SimpleNamespace(**message) for message in wire],
        system_prompt=lambda: "BASE SYSTEM",
        user_prompt=lambda: user_text,
        as_openai_messages=lambda: [dict(message) for message in wire],
    )
    interpretation = SimpleNamespace(
        raw_text=user_text, normalized_text=user_text, user_text="", understanding_confidence=0.9
    )
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    with mock.patch.object(mfr, "normalize_prompt", return_value=internal):
        return MemoryFirstRouter._build_request(
            router,
            task=None,
            classification={"task_class": "chat"},
            interpretation=interpretation,
            context_result=None,
            persona=None,
            output_mode="plain_text",
            task_kind="conversation",
            surface="openclaw",
            source_context={},
        )


def _system_messages(request) -> list[str]:
    return [str(m["content"]) for m in request.messages if m.get("role") == "system"]


# The answer-format block rides the per-turn system message that follows the history, and the leading
# system message stays byte-stable across turns for provider prompt caching: main 00ba5bd5
# (2026-10-05, "keep the leading provider system message stable across turns"), ported in dc937f7.
_TURN_PREFIX = "Context for this turn:\n"


def _turn_system(request) -> str:
    return next((m for m in _system_messages(request) if m.startswith(_TURN_PREFIX)), "")


# --- recognition -------------------------------------------------------------------------------


@pytest.mark.parametrize("text", BREVITY_TURNS + SLOPPY_BREVITY_TURNS)
def test_an_explicit_brevity_phrasing_sets_the_short_answer_contract(text: str) -> None:
    policy = _policy(text)
    assert policy["answer_shape"] == "short", text
    assert policy["max_words"] == "64"
    assert policy["max_items"] is None


@pytest.mark.parametrize("text", NEAR_MISS_TURNS)
def test_length_words_that_name_the_topic_are_not_a_length_request(text: str) -> None:
    assert _policy(text)["answer_shape"] == ""


@pytest.mark.parametrize("text", NO_LENGTH_REQUEST_TURNS)
def test_a_turn_without_an_explicit_length_request_keeps_todays_policy(text: str) -> None:
    policy = _policy(text)
    assert policy["answer_shape"] == ""
    assert policy["max_words"] == "64"
    assert guard.short_answer_instruction(policy) == ""


def test_todays_advice_and_detail_budgets_are_unchanged_without_a_length_request() -> None:
    advice = _policy("Can you recommend a few upgrades for my camera kit?")
    assert (advice["max_words"], advice["max_items"], advice["answer_shape"]) == ("140", 5, "")
    detail = _policy("Explain in detail how the lock gates fill.")
    assert (detail["max_words"], detail["answer_shape"]) == ("", "")


def test_the_brevity_request_outranks_the_advice_lift_on_the_same_turn() -> None:
    policy = _policy("Any tips for packing light on a sailing trip? Give a concise answer.")
    assert policy["max_words"] == "64"
    assert policy["max_items"] is None
    assert policy["answer_shape"] == "short"


def test_a_non_chat_output_mode_takes_no_answer_shape() -> None:
    policy = guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="json_object", user_text=READER_STYLE_TURN
    )
    assert policy["answer_shape"] == ""


# --- the reader-facing instruction ------------------------------------------------------------


def test_the_reader_system_prompt_carries_the_short_answer_contract() -> None:
    request = _reader_request(READER_STYLE_TURN)
    assert _system_messages(request)[0] == "BASE SYSTEM"  # stable across turns
    turn_block = _turn_system(request)
    assert turn_block.startswith(_TURN_PREFIX + "Answer format for this request:")
    assert "give the answer first" in turn_block
    assert "No preamble" in turn_block
    assert "no restating the question" in turn_block
    assert "no narration of where the answer came from" in turn_block
    # The retrieved evidence block is untouched and appears exactly once.
    assert _system_messages(request)[1] == "<retrieved_context>harbor log</retrieved_context>"
    assert request.metadata["ordinary_chat_output_policy"]["answer_shape"] == "short"
    # The current user turn still reaches the reader verbatim.
    assert request.messages[-1]["content"] == READER_STYLE_TURN


def test_a_reader_turn_without_a_length_request_gets_no_short_answer_contract() -> None:
    # A question over retrieved records still states how records are read (see
    # tests/test_reader_contract_attribution_and_linked_records.py); no length was asked, so the
    # short-answer contract is absent and the base prompt still leads.
    request = _reader_request("What colour was the kayak Marta rented?")
    primary = _system_messages(request)[0]
    assert primary.startswith("BASE SYSTEM")
    assert "give the answer first" not in "\n".join(_system_messages(request))
    assert "No preamble" not in "\n".join(_system_messages(request))
    assert request.metadata["ordinary_chat_output_policy"]["answer_shape"] == ""


def test_an_earlier_turns_length_request_does_not_shape_a_later_turn() -> None:
    """Cross-turn: brevity asked two turns ago is not this turn's contract."""
    request = _reader_request(
        "And which pier did it leave from?",
        history=[
            ("user", "When did the night ferry leave? Give a concise answer."),
            ("assistant", "9:40 pm."),
        ],
    )
    primary = _system_messages(request)[0]
    assert primary.startswith("BASE SYSTEM")
    assert "give the answer first" not in "\n".join(_system_messages(request))
    assert request.metadata["ordinary_chat_output_policy"]["answer_shape"] == ""


def test_a_follow_up_that_asks_for_brevity_gets_the_contract() -> None:
    request = _reader_request(
        "and the pier? short answer pls",
        history=[
            ("user", "When did the night ferry leave? Explain what happened that evening."),
            ("assistant", "It left at 9:40 pm after a delay at the fuel dock."),
        ],
    )
    assert "give the answer first" in _turn_system(request)


def test_the_rewrite_repeats_the_short_answer_contract() -> None:
    short = guard.ordinary_chat_retry_instruction(_policy(READER_STYLE_TURN))
    assert "give the answer first" in short
    assert "at most 64 words" in short
    plain = guard.ordinary_chat_retry_instruction(_policy("What colour was the kayak Marta rented?"))
    assert "give the answer first" not in plain


# --- completeness: every item and part stays ---------------------------------------------------


def test_the_contract_keeps_every_list_item_and_part() -> None:
    instruction = guard.short_answer_instruction(_policy(READER_STYLE_TURN))
    # Wording from c1b97f31 (2026-10-06, "name every supported item"); the rule is unchanged: every
    # item the records name is kept, and every part of a multi-part request is answered.
    assert "name every one of them" in instruction
    assert "answers every part" in instruction


def test_a_terse_complete_list_passes_and_is_not_cut() -> None:
    turn = "List all the stops on the coastal ferry route. Give a concise answer."
    policy = _policy(turn)
    answer = (
        "Northpoint, Gull Rock, Fenwick Quay, Saltmarsh, Old Mill, Heron Bay, Cobble Cove, "
        "Lantern Pier, Eastwick, Driftwood Sands, Marram Point, Southhaven."
    )
    assert guard.inspect_ordinary_chat_output(answer, policy, current_user_text=turn).allowed
    assert guard.constrain_ordinary_chat_output(answer, policy) == answer


@pytest.mark.parametrize("directive", [
    "Give a short answer.",
    "Keep it short.",
    "Be brief.",
    "Short answer please.",
])
def test_a_length_directive_is_not_a_request_part(directive: str) -> None:
    turn = "What time does the night ferry leave? Which pier does it use? " + directive
    assert ordinary_plain_request_count(turn) == 2
    policy = _policy(turn)
    assert policy["independent_request_parts"] == 2
    assert policy["answer_shape"] == "short"
    complete = "1. 9:40 pm.\n2. Pier 3."
    missing = "1. 9:40 pm."
    assert guard.inspect_ordinary_chat_output(complete, policy, current_user_text=turn).allowed
    verdict = guard.inspect_ordinary_chat_output(missing, policy, current_user_text=turn)
    assert verdict.reasons == ("missing_requested_parts",)


@pytest.mark.parametrize("clause", [
    "Explain the tides briefly",
    "Summarize it in one sentence",
    "What colour was the kayak? Give a concise answer",
])
def test_a_clause_carrying_its_own_request_is_not_a_length_directive(clause: str) -> None:
    assert not guard.is_answer_length_directive(clause)


# --- abstention and safety notices stay --------------------------------------------------------


def test_the_contract_keeps_the_abstention_sentence_and_safety_notice() -> None:
    instruction = guard.short_answer_instruction(_policy(READER_STYLE_TURN))
    assert "say so in one short sentence" in instruction
    assert "Keep any safety notice the answer needs" in instruction


@pytest.mark.parametrize("answer", [
    "I don't know; the logbook entries don't say.",
    "I can't verify that from the entries, and I'm not going to state a colour I can't support.",
])
def test_a_short_abstention_or_notice_passes_under_the_contract(answer: str) -> None:
    policy = _policy(READER_STYLE_TURN)
    assert guard.inspect_ordinary_chat_output(
        answer, policy, current_user_text=READER_STYLE_TURN
    ).allowed
    assert guard.constrain_ordinary_chat_output(answer, policy) == answer


def test_a_withdrawn_part_still_counts_as_declined_under_the_contract() -> None:
    turn = "What time does the night ferry leave? Which pier does it use? Keep it short."
    answer = (
        "1. 9:40 pm.\n"
        "I don't have the pier in the entries, and I'm not going to state one I can't support."
    )
    assert guard.inspect_ordinary_chat_output(answer, _policy(turn), current_user_text=turn).allowed
