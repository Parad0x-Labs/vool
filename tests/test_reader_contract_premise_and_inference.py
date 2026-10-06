"""The reader contract on a recall question: premise rule, stipulated frame, and direct inference.

Measured on archived dev misses (no provider call in this file):
  * the stipulated-frame rule ("answer within that frame ... without replacing named people")
    was appended to every reader prompt, although no ordinary recall question stipulates a frame.
    On a multiple-choice question whose premise names the wrong person it told the reader to
    answer inside that premise. It now goes out only when this turn, or a user turn in the
    history sent with it, stipulates a frame;
  * a multiple-choice question that offers a "not mentioned" choice was answered with a factual
    option the records state about someone else. When such an option is offered, the reader is
    told that a factual option needs a record about the exact person and thing the question
    names; otherwise it is the not-mentioned option;
  * the capsule header "Answer from these exact facts only." read as a ban on combining records
    and on a direct inference, so the reader said "not recorded" with the answer's records
    present. The header keeps the records as the only source, allows combining them and a
    marked direct inference, and still forbids a fact no record states.

Every sentence here is synthetic.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
import core.ordinary_chat_response_guard as guard
import core.prompt_normalizer as prompt_normalizer
from core.internal_message_schema import InternalMessage
from tests.test_an_explicit_brevity_request_sets_the_short_answer_contract import (
    _reader_request,
    _system_messages,
)
from tests.test_prompt_assembly_profiles import _build_request

READER_PREFIX = (
    "Answer from the harbor logbook entries I shared earlier. Use only what those entries "
    "support. If they do not support an answer, say you do not know. Give a concise final "
    "answer.\n"
)

NOT_MENTIONED_OPTION_TURNS = [
    "Why did Mira repaint her kayak? Select the correct answer: (a) Not mentioned in the "
    "conversation (b) to match her team colours.",
    "Which pier did Oren pick? (a) the north pier (b) Not stated in the records",
    "Pick one: a) cedar b) maple c) cannot be determined",
    "What did Lena bake for the fair?\n(A) rye bread\n(B) No information given",
    "Who fixed the crane? 1) Tomasz 2) Ilse 3) It is not mentioned",
    "Which harbor did the trawler leave from? (a) Unknown (b) the east harbor",
]

NO_NOT_MENTIONED_OPTION_TURNS = [
    "Why did Mira repaint her kayak?",
    "Which colour did Mira pick: (a) red (b) blue",
    "Was the repair cost not mentioned in the logbook?",
    "Explain why some facts are not mentioned in a summary.",
    "He told me a) it was not mentioned",
    "Answer: (a) unknown (c) blue",
    "Plan A. Not stated yet.",
]

PREMISE_RULE_MARKERS = (
    "One offered option says the information is not mentioned.",
    "about the exact person and the exact thing the question names",
    "attribute it to someone else",
    "choose the not-mentioned option",
)


def _policy(text: str, *, output_mode: str = "plain_text") -> dict:
    return guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode=output_mode, user_text=text
    )


# --- F5b: the premise rule for an offered not-mentioned option -------------------------------


@pytest.mark.parametrize("text", NOT_MENTIONED_OPTION_TURNS)
def test_an_offered_not_mentioned_option_sets_the_premise_rule(text: str) -> None:
    policy = _policy(READER_PREFIX + text)
    assert policy["not_mentioned_option_offered"] is True, text
    instruction = guard.not_mentioned_option_instruction(policy)
    for marker in PREMISE_RULE_MARKERS:
        assert marker in instruction, (marker, text)


@pytest.mark.parametrize("text", NO_NOT_MENTIONED_OPTION_TURNS)
def test_without_an_offered_not_mentioned_option_there_is_no_premise_rule(text: str) -> None:
    policy = _policy(READER_PREFIX + text)
    assert policy["not_mentioned_option_offered"] is False, text
    assert guard.not_mentioned_option_instruction(policy) == ""


def test_a_non_chat_output_mode_takes_no_premise_rule() -> None:
    policy = _policy(READER_PREFIX + NOT_MENTIONED_OPTION_TURNS[0], output_mode="json_object")
    assert policy["not_mentioned_option_offered"] is False


def test_the_reader_system_prompt_states_the_premise_rule_before_the_first_call() -> None:
    turn = READER_PREFIX + NOT_MENTIONED_OPTION_TURNS[0]
    request = _reader_request(turn)
    primary = _system_messages(request)[0]
    assert primary.startswith("BASE SYSTEM\n\nAnswer format for this request:")
    for marker in PREMISE_RULE_MARKERS:
        assert marker in primary, marker
    # The short-answer contract the same turn asks for is still there, and still leads.
    assert "give the answer first" in primary
    assert primary.index("give the answer first") < primary.index(PREMISE_RULE_MARKERS[0])
    assert request.metadata["ordinary_chat_output_policy"]["not_mentioned_option_offered"] is True
    # Evidence and the user's turn are untouched.
    assert _system_messages(request)[1] == "<retrieved_context>harbor log</retrieved_context>"
    assert request.messages[-1]["content"] == turn


@pytest.mark.parametrize(
    "text",
    [READER_PREFIX + "Which colour did Mira pick: (a) red (b) blue", "Why did Mira repaint her kayak?"],
)
def test_a_reader_turn_without_the_option_carries_no_premise_rule(text: str) -> None:
    request = _reader_request(text)
    assert PREMISE_RULE_MARKERS[0] not in "\n".join(_system_messages(request))


def test_the_one_rewrite_repeats_the_premise_rule() -> None:
    offered = guard.ordinary_chat_retry_instruction(_policy(READER_PREFIX + NOT_MENTIONED_OPTION_TURNS[1]))
    plain = guard.ordinary_chat_retry_instruction(_policy(READER_PREFIX + "Which pier did Oren pick?"))
    assert PREMISE_RULE_MARKERS[0] in offered
    assert PREMISE_RULE_MARKERS[0] not in plain


def test_the_premise_rule_carries_no_answer_text() -> None:
    instruction = guard.not_mentioned_option_instruction({"not_mentioned_option_offered": True})
    # General contract wording only: no option label, no name, no quoted option text.
    assert "(a)" not in instruction and "(b)" not in instruction
    assert "Not mentioned in the conversation" not in instruction
    # It also names the two shapes measured with the earlier rule in the prompt (an option whose
    # words appear only in a record that asks about it; a factual option qualified as someone
    # else's); the bound keeps it one compact rule.
    assert len(instruction) < 520


# --- F5a: the stipulated-frame rule only when a frame is stipulated ---------------------------


STIPULATED_MARKER = "User-stipulated assumptions"


def _system_text(request) -> str:
    return request.as_openai_messages()[0]["content"]


def _segment_names(request) -> list[str]:
    return [
        str(item.get("name"))
        for item in list(request.metadata.get("prompt_payload_segments") or [])
        if isinstance(item, dict)
    ]


@pytest.mark.parametrize(
    "prompt",
    [
        "What colour was the kayak Marta rented?",
        "Why did Mira repaint her kayak? Select the correct answer: (a) Not mentioned in the "
        "conversation (b) to match her team colours.",
        "How many days passed between the regatta and the harbor festival?",
    ],
)
def test_an_ordinary_question_carries_no_stipulated_frame_rule(prompt: str) -> None:
    request, _ = _build_request(
        prompt, task_class="research", task_kind="summarization", output_mode="plain_text"
    )
    assert STIPULATED_MARKER not in _system_text(request)
    assert "replacing named people" not in _system_text(request)
    assert "stipulated_frame_guidance" not in _segment_names(request)


def test_a_stipulating_turn_still_carries_the_frame_rule_and_its_ledger_entry() -> None:
    request, _ = _build_request(
        "Assume it is 2040. President Buster is selling me a watch in Washington, DC.",
        task_class="research",
        task_kind="summarization",
        output_mode="plain_text",
    )
    system = _system_text(request)
    assert STIPULATED_MARKER in system
    assert "Keep those stipulated facts active in follow-up answers" in system
    assert "stipulated_frame_guidance" in _segment_names(request)


def test_a_follow_up_to_a_stipulating_turn_keeps_the_frame_rule(monkeypatch) -> None:
    history = [
        {"role": "user", "content": "Assume it is 2040. President Buster is selling me a watch in Washington, DC."},
        {"role": "assistant", "content": "Understood: in 2040 President Buster is selling you a watch."},
    ]
    monkeypatch.setattr(
        prompt_normalizer,
        "canonical_runtime_transcript",
        lambda **_kwargs: (list(history), "client_conversation_history"),
    )
    request, _ = _build_request(
        "Who am I meeting?",
        task_class="research",
        task_kind="summarization",
        output_mode="plain_text",
    )
    assert STIPULATED_MARKER in _system_text(request)
    assert "stipulated_frame_guidance" in _segment_names(request)


def test_the_frame_check_reads_user_turns_only() -> None:
    stipulation = "Assume it is 2040. President Buster is selling me a watch in Washington, DC."
    in_play = prompt_normalizer._stipulated_frame_in_play
    assert in_play(stipulation, "", [])
    assert in_play("Who am I meeting?", "", [InternalMessage(role="user", content=stipulation)])
    assert not in_play("Who am I meeting?", "", [InternalMessage(role="assistant", content=stipulation)])
    assert not in_play("What colour was the kayak Marta rented?", "", [])


# --- F8: the capsule header allows combining records and a marked direct inference -------------


def test_the_capsule_header_allows_a_marked_direct_inference_and_no_invented_fact() -> None:
    header = cr._CAPSULE_FACTS_HEADER
    assert "\n" not in header
    # Output guards and the packer key on this first sentence.
    assert header.startswith("Distilled local facts.")
    assert "exact facts only" not in header
    assert "combine" in header
    assert "direct inference" in header
    assert "marked as inferred" in header
    assert "never add a fact the records do not state" in header


def test_a_delivered_capsule_carries_the_new_header(tmp_path, monkeypatch) -> None:
    from tests.test_overnight_source_structure import _recall, _store

    home = tmp_path / "header-world"
    home.mkdir()
    from core import runtime_paths
    from storage.migrations import run_migrations
    import core.embedding_service as embeddings

    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    try:
        runtime_paths.configure_runtime_home(home)
        monkeypatch.setattr(embeddings, "_best_embed_model", lambda: None)
        run_migrations()
        chat = "capsule-header-inference"
        _store(home, chat, "I moored the green sloop at the north pier last Tuesday.", "Noted.")
        capsule = _recall(home, chat, "Where did I moor the green sloop?")
    finally:
        runtime_paths.configure_runtime_home(previous)
    assert "north pier" in capsule, capsule
    assert cr._CAPSULE_FACTS_HEADER in capsule
    assert "Answer from these exact facts only." not in capsule
