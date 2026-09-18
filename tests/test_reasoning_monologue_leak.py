"""A thinking model's monologue is not an answer, and must never be the reply.

Measured live 2026-08-01. A user asked a hard question and the ENTIRE visible reply was raw internal
monologue -- "Let me look at...", "But wait...", "Actually..." -- cut off mid-sentence at 7,786
tokens. No answer at all, and two explicit instructions in the prompt were never addressed.

Three layers of the failure:

1. `think:false` can switch off Ollama's parser rather than reasoning, putting the monologue in
   `message.content`. A later live falsification also proved `/no_think` ineffective on installed
   qwen3. Ordinary Auto therefore ranks a proven non-thinking daily model ahead of this family;
   the adapter keeps its typed wire contract but it is no longer mistaken for reliability proof.
2. The lead-pattern guard was too narrow to fire: `.search()` with no `re.MULTILINE` on a `^\\s*(...)`
   pattern, with `let me` closed to check/inspect/look/think. Every phrase the live failure used
   escaped it.
3. A reasoning block truncated before its closing tag carries no marker at all, and nothing outside
   the builder stripped `<think>` in the first place.

The decision this pins: a reply that is ONLY reasoning is a FAILED LANE. It escalates to the next
ranked model down the same soft-failure path an empty completion takes. Daily ranking is the
cause-side protection; this detector is the terminal backstop.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import (
    OpenAICompatibleAdapter,
    _extract_ollama_chat_text,
)
from core.agent_runtime.response import suppress_internal_reasoning_leak
from core.memory_first_router import _soft_failure_details
from core.model_output_guard import (
    is_reasoning_only,
    reasoning_only_markers,
    strip_reasoning_block,
)

# --------------------------------------------------------------------------------------
# The four shapes the fix is defined by
# --------------------------------------------------------------------------------------

# The live failure: monologue end to end, stopped by the token budget mid-sentence, no closing tag.
TRUNCATED_MONOLOGUE = (
    "Let me look at what the user is actually asking here. They want the routing table and they "
    "also asked about the fallback order. Hmm, the routing table lives in the router, but wait, "
    "there is also a ranking step before it. Let me re-read the question. Okay, so the user wants "
    "both the table and the order. Actually, I should check whether the free cloud boost runs "
    "before or after the ranked loop, because if it runs before then the order I was about to give "
    "is"
)

# The same failure in the phrasing the model reaches for most often.
OKAY_SO_THE_USER = (
    "Okay, so the user wants me to explain how the memory-first router picks a model. Let me think "
    "about what I know about this. There is a ranking step and then a candidate loop. Hmm, but "
    "wait, I should check whether the mux path is separate. Let me re-read their message once more "
    "to be sure I have the"
)

# Self-correction INSIDE a real answer. The phrase is identical; the reply is not.
ANSWER_THAT_CORRECTS_ITSELF = (
    "Yes, you can cache the manifest between turns. The simplest approach is a module-level dict "
    "keyed by provider id. But wait, actually there is a subtlety worth naming: the manifest "
    "carries a runtime_config that the resource governor mutates in place, so cache the parsed copy "
    "rather than the object itself. That keeps the hot path allocation-free while the governor's "
    "edits stay visible to the next turn."
)

PLAIN_ANSWER = (
    "The router ranks candidates by cost class, then locality, then measured tokens per second. The "
    "first candidate that returns a valid contract wins; anything that fails validation escalates "
    "to the next one."
)

# Captured verbatim from live qwen3:8b on 2026-08-01 with the reasoning parser off and a 200-token
# budget -- `done_reason: "length"`, so it is cut off mid-sentence with no closing tag, exactly as
# the reported failure was. Kept as the model wrote it: hand-written monologue can accidentally be
# written to match the detector, and this cannot.
LIVE_CAPTURE = (
    "Okay, let's start by understanding what the user is asking. They want to know how a routing "
    "layer should decide between a local and a cloud model, and also what it logs. \n\nFirst, the "
    "routing layer's decision between local and cloud models. I know that in distributed systems, "
    "especially with machine learning models, there's often a choice between running inference "
    "locally on a device or using a cloud-based model. The routing layer is responsible for "
    "directing traffic to the appropriate model. So, the decision factors would include things "
    "like latency, bandwidth, data sensitivity, model performance, and maybe even cost.\n\nWait, "
    "but how exactly does the routing layer make that decision? Maybe it uses some kind of policy "
    "or algorithm. For example, if the request is time-sensitive, it might prioritize the local "
    "model to reduce latency. If the data is sensitive, it might route to the local model to avoid "
    "sending it over the network. On the other hand, if the cloud model has better accuracy or "
    "more resources,"
)

# Also captured live, same model, same 200-token budget, same `done_reason: "length"` -- but the
# parser-off turn produced a real ANSWER rather than a monologue. Truncation alone must never be
# enough to condemn a reply, and this is the control that holds that line.
LIVE_TRUNCATED_ANSWER = (
    "In a **per-session write-ahead log (WAL)** used by a **single-writer local runtime**, the "
    "choice between **optimistic** and **pessimistic locking** depends on the **failure mode** you "
    "are guarding against.\n\n### Pessimistic Locking\n- **Mechanism**: Assumes that conflicts are "
    "likely. It **acquires a lock** before performing"
)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(TRUNCATED_MONOLOGUE, id="cut_off_mid_sentence_no_closing_tag"),
        pytest.param(OKAY_SO_THE_USER, id="okay_so_the_user_wants_me_to"),
    ],
)
def test_a_reply_that_is_only_reasoning_is_detected(reply: str) -> None:
    assert is_reasoning_only(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(ANSWER_THAT_CORRECTS_ITSELF, id="but_wait_actually_inside_a_real_answer"),
        pytest.param(PLAIN_ANSWER, id="ordinary_answer"),
    ],
)
def test_a_real_answer_survives(reply: str) -> None:
    assert reasoning_only_markers(reply) == []


def test_the_detection_names_what_it_saw() -> None:
    """A failed lane that cannot say why is a failed lane nobody can debug."""

    markers = reasoning_only_markers(TRUNCATED_MONOLOGUE)
    assert "cut_off_mid_sentence" in markers
    assert "but wait" in markers


def test_the_live_capture_is_caught_end_to_end() -> None:
    """Model-authored prose, not a shape written to match the detector."""

    assert is_reasoning_only(LIVE_CAPTURE) is True
    assert "cut_off_mid_sentence" in reasoning_only_markers(LIVE_CAPTURE)
    assert _soft_failure_details(LIVE_CAPTURE)["reasoning_only_completion"] is True
    assert suppress_internal_reasoning_leak(LIVE_CAPTURE) == NO_CLEAN_ANSWER


def test_a_live_answer_cut_off_by_the_same_budget_survives() -> None:
    """Same model, same `done_reason: "length"`, but an ANSWER. Truncation is a signal, not a
    verdict -- reading it as one would throw away every reply that ran out of budget."""

    assert reasoning_only_markers(LIVE_TRUNCATED_ANSWER) == []
    assert _soft_failure_details(LIVE_TRUNCATED_ANSWER) is None
    assert suppress_internal_reasoning_leak(LIVE_TRUNCATED_ANSWER) is None


def test_an_answer_that_stops_reasoning_and_answers_survives_its_own_preamble() -> None:
    """The reply must still be reasoning in its SECOND HALF. Counting monologue phrases alone
    condemns this one -- four of them, all in the preamble -- and throws away a real answer."""

    reply = (
        "Let me check. Hmm, the user wants two things here. But wait, no, that is right. The "
        "router ranks by cost class, then locality, then measured throughput, and the first "
        "candidate that returns a valid contract wins the turn."
    )
    assert len(reasoning_only_markers(reply)) == 0


# --------------------------------------------------------------------------------------
# `<think>`: truncated, closed, and the code file that must not be destroyed
# --------------------------------------------------------------------------------------


def test_a_truncated_think_block_is_a_failed_lane() -> None:
    """The shape `strip_reasoning_monologue` admits it cannot see: no closing tag, so no answer."""

    reply = (
        "<think>\nOkay the user wants the routing table. Let me look at the ranking step first, and "
        "then I need to check whether the"
    )
    assert reasoning_only_markers(reply) == ["think_block_unterminated"]
    # Nothing to recover: the block never closed, so stripping cannot find an answer.
    assert strip_reasoning_block(reply) == reply


def test_a_closed_think_block_gives_up_the_answer_after_it() -> None:
    reply = "<think>Let me check the ranking step.</think>\n\nThe router ranks by cost class first."
    assert strip_reasoning_block(reply) == "The router ranks by cost class first."
    assert is_reasoning_only(reply) is False


def test_a_think_block_with_nothing_after_it_is_a_failed_lane() -> None:
    assert reasoning_only_markers("<think>Let me check. Hmm, but wait.</think>") == [
        "think_block_only"
    ]


def test_the_template_closer_alone_is_still_stripped() -> None:
    """Ollama's chat template opens the tag itself, so the reasoning arrives with only a closer."""

    reply = "Okay, let me work out what they want. Hmm.</think>\n\nThe router ranks by cost class."
    assert strip_reasoning_block(reply) == "The router ranks by cost class."


def test_a_file_the_user_asked_for_that_contains_think_tags_is_left_whole() -> None:
    """`<think>` in the middle is content ABOUT the tags, not reasoning. Destroying it is the
    false positive this guard is most likely to make."""

    reply = (
        "Here is the parser you asked for:\n\n"
        '```python\nOPEN = "<think>"\nCLOSE = "</think>"\n\n'
        "def strip(body):\n"
        "    i = body.find(CLOSE)\n"
        "    return body[i + len(CLOSE):] if i != -1 else body\n"
        "```\n\n"
        "It drops everything up to the first closing tag."
    )
    assert strip_reasoning_block(reply) == reply
    assert is_reasoning_only(reply) is False


# --------------------------------------------------------------------------------------
# Replies that legitimately DISCUSS reasoning
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(
            'Let me check what "but wait" means in model output. It is a self-correction marker: '
            "the model has noticed a contradiction in its own chain of thought and is backing up. "
            "Seeing it in a user-visible reply means the reasoning parser was off, not that the "
            "model failed.",
            id="explains_what_a_monologue_marker_means",
        ),
        pytest.param(
            "Let me walk you through it. First the router ranks the candidates, then it calls the "
            "top one. Actually, the interesting part is the failover: a contract failure is soft, "
            "so the next candidate runs instead of the turn dying.",
            id="teaching_walkthrough_that_corrects_itself",
        ),
        pytest.param(
            "Okay, so the user wants the port. Let me check the config. Hmm, but wait, there are "
            "two.\n\nFinal answer: the relay listens on 8787.",
            id="thinks_out_loud_then_actually_answers",
        ),
    ],
)
def test_reasoning_that_is_discussed_or_followed_by_an_answer_survives(reply: str) -> None:
    assert is_reasoning_only(reply) is False


# --------------------------------------------------------------------------------------
# Root cause 1: the general chat adapter sends `think: true` and reads content only
# --------------------------------------------------------------------------------------


def _ollama_adapter(model_name: str = "qwen3:8b", **runtime_config) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=f"ollama:{model_name}",
            provider_name="ollama",
            model_name=model_name,
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434", **runtime_config},
        )
    )


def test_general_chat_auto_obeys_the_deep_reasoning_preference(monkeypatch: pytest.MonkeyPatch) -> None:

    import core.reasoning_mode as reasoning_mode

    monkeypatch.setattr(reasoning_mode, "deep_reasoning_enabled", lambda: False)
    assert _ollama_adapter()._ollama_think_flag() is False


def test_a_manifest_written_with_think_false_is_respected() -> None:

    assert _ollama_adapter("qwen3:8b", think=False)._ollama_think_flag() is False


def test_a_non_thinking_model_still_omits_the_key() -> None:
    """qwen2.5:7b answers HTTP 400 when `think` is present at all."""

    assert _ollama_adapter("qwen2.5:7b")._ollama_think_flag() is None


def test_the_auto_flag_reaches_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")
    adapter = _ollama_adapter()
    request = ModelRequest(task_kind="chat", prompt="Explain the routing table.", output_mode="plain_text")
    payload = adapter._build_ollama_payload(request, force_json=False, stream=False)
    assert payload["think"] is False


def test_only_content_is_read_never_thinking() -> None:
    """With the parser on, the monologue is quarantined in `message.thinking`. Reading it would
    put the leak straight back."""

    data = {
        "message": {
            "thinking": TRUNCATED_MONOLOGUE,
            "content": "The router ranks by cost class first.",
        }
    }
    assert _extract_ollama_chat_text(data) == "The router ranks by cost class first."


def test_a_reasoning_only_turn_leaves_the_adapter_empty() -> None:
    """Quarantined reasoning plus no content is an EMPTY completion -- the shape the router already
    escalates on."""

    data = {"message": {"thinking": TRUNCATED_MONOLOGUE, "content": ""}}
    assert strip_reasoning_block(_extract_ollama_chat_text(data)) == ""


# --------------------------------------------------------------------------------------
# Root cause 2: the lead-pattern guard, at the last hop before the user
# --------------------------------------------------------------------------------------

NO_CLEAN_ANSWER = (
    "I couldn't produce a clean final answer from that model response. No result is being claimed."
)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(TRUNCATED_MONOLOGUE, id="cut_off_mid_sentence"),
        pytest.param(OKAY_SO_THE_USER, id="okay_so_the_user_wants_me_to"),
    ],
)
def test_a_monologue_is_never_shown_to_the_user(reply: str) -> None:
    assert suppress_internal_reasoning_leak(reply) == NO_CLEAN_ANSWER


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(ANSWER_THAT_CORRECTS_ITSELF, id="but_wait_actually_inside_a_real_answer"),
        pytest.param(PLAIN_ANSWER, id="ordinary_answer"),
    ],
)
def test_the_user_facing_guard_leaves_a_real_answer_untouched(reply: str) -> None:
    assert suppress_internal_reasoning_leak(reply) is None


def test_the_phrasings_the_live_failure_used_now_reach_the_answer_recovery() -> None:
    """`let me` was closed to check/inspect/look/think and there was no throat-clearing prefix, so
    "Hmm, let me start by" matched nothing -- and a reply that matches nothing never reaches the
    `final answer:` extraction below it either. The answer was sitting there and was not taken."""

    reply = (
        "Hmm, let me start by re-reading the request. They want the relay port and the fallback "
        "order. Final answer: the relay listens on 8787 and falls back to the ranked list in order."
    )
    assert suppress_internal_reasoning_leak(reply) == (
        "the relay listens on 8787 and falls back to the ranked list in order."
    )


def test_a_mid_reply_handoff_is_now_found_and_the_answer_kept() -> None:
    """Without `re.MULTILINE` the lead was only ever matched at the very first token, so a reply
    that opened innocuously took neither the suppression nor the `final answer:` recovery."""

    reply = (
        "Sure thing.\n\n"
        "Okay, so the user wants the relay port. Let me check the config. Hmm.\n\n"
        "Final answer: the relay listens on 8787."
    )
    assert suppress_internal_reasoning_leak(reply) == "the relay listens on 8787."


# --------------------------------------------------------------------------------------
# The escalation: a reasoning-only reply takes the empty-completion path
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(TRUNCATED_MONOLOGUE, id="cut_off_mid_sentence"),
        pytest.param(OKAY_SO_THE_USER, id="okay_so_the_user_wants_me_to"),
        pytest.param("<think>Let me check the ranking.</think>", id="think_block_only"),
    ],
)
def test_the_router_treats_a_monologue_as_a_failed_lane(reply: str) -> None:
    details = _soft_failure_details(reply)
    assert details is not None
    assert details["reasoning_only_completion"] is True
    assert details["reasoning_markers"]


def test_the_monologue_path_is_the_empty_completion_path() -> None:
    """Same classifier, same escalation -- not a second mechanism that can drift."""

    assert _soft_failure_details("")["empty_completion"] is True
    assert _soft_failure_details("[]")["empty_completion"] is True


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(ANSWER_THAT_CORRECTS_ITSELF, id="but_wait_actually_inside_a_real_answer"),
        pytest.param(PLAIN_ANSWER, id="ordinary_answer"),
        pytest.param('{"answer": "cost class first"}', id="structured_answer"),
        pytest.param("8787", id="one_token_answer"),
    ],
)
def test_a_real_answer_is_not_escalated(reply: str) -> None:
    assert _soft_failure_details(reply) is None
