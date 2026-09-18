"""A failure must name its stage. Every one of tonight's dead ends came from one that did not.

Each of these fixtures is a real measured failure from 2026-08-05, reduced to what the runtime
already knew at the time. The point is not that the classifier is clever -- it is that the same
sentence was reaching the user for six different causes, so every investigation started by guessing.
"""
from __future__ import annotations

import pytest

from core.turn_failure_stage import (
    StageObservation,
    TerminalState,
    classify_turn,
    explain,
    observation_from_trace,
)


def test_the_aviation_run_that_received_apple_developer_docs_is_contamination() -> None:
    """Evidence from another turn is the failure most likely to go unnoticed: a contaminated run
    still produces fluent, confident prose."""
    obs = StageObservation(retrieval_attempted=True, retrieval_result_count=4,
                           retrieval_on_topic_count=4, foreign_evidence_ids=["ev-from-other-turn"])
    assert classify_turn(obs) is TerminalState.RETRIEVAL_CONTAMINATED
    assert "ownership" in explain(TerminalState.RETRIEVAL_CONTAMINATED)


def test_contamination_outranks_a_good_looking_answer() -> None:
    """A contaminated turn that renders beautifully must still be reported as contaminated."""
    obs = StageObservation(retrieval_attempted=True, retrieval_result_count=4,
                           retrieval_on_topic_count=4, foreign_evidence_ids=["ev-x"],
                           provider_called=True, raw_content="a polished table",
                           text_before_postprocessing="a polished table", final_text="a polished table")
    assert classify_turn(obs) is TerminalState.RETRIEVAL_CONTAMINATED


def test_the_console_run_that_found_only_wikipedia_orientation_pages_is_irrelevant_not_empty() -> None:
    """Results came back; none proved a console sales figure. That is a query/relevance problem, and
    calling it 'empty' would send the next person to the wrong layer."""
    obs = StageObservation(retrieval_attempted=True, retrieval_result_count=4, retrieval_on_topic_count=0)
    assert classify_turn(obs) is TerminalState.RETRIEVAL_IRRELEVANT


def test_a_search_that_returned_nothing_is_empty() -> None:
    obs = StageObservation(retrieval_attempted=True, retrieval_result_count=0)
    assert classify_turn(obs) is TerminalState.RETRIEVAL_EMPTY


def test_content_in_an_unread_field_is_OUR_failure_not_the_models() -> None:
    """The distinction the review insisted on. openrouter_cloud_provider.py reads only
    message.content; a model returning its answer in reasoning_content looks empty. Reporting that
    as SYNTHESIS_EMPTY blames the model for the runtime's own gap."""
    obs = StageObservation(provider_called=True, raw_content="",
                           raw_alternate_fields={"reasoning_content": "the actual answer"})
    assert classify_turn(obs) is TerminalState.RESPONSE_EXTRACTION_FAILED


def test_a_genuinely_empty_completion_is_synthesis_empty() -> None:
    obs = StageObservation(provider_called=True, raw_content="",
                           raw_alternate_fields={"reasoning_content": "", "output_text": ""})
    assert classify_turn(obs) is TerminalState.SYNTHESIS_EMPTY


def test_the_provider_cascade_behind_the_status_dump_is_a_provider_error() -> None:
    """The measured Q1 trace: cloud + qwen3:14b + qwen3:8b + qwen3:0.6b all failed, then a fast path
    answered with runtime state. Assumed for hours to be an intent-detector false positive."""
    obs = observation_from_trace([
        {"event_type": "model.call_started", "message": "openrouter-byok:nemotron"},
        {"event_type": "model.call_failed", "message": "Model call failed with openrouter-byok:nemotron."},
        {"event_type": "model.call_started", "message": "ollama-local:qwen3:8b"},
        {"event_type": "model.call_failed", "message": "Model call failed with ollama-local:qwen3:8b."},
    ])
    assert classify_turn(obs) is TerminalState.PROVIDER_ERROR


def test_the_hbar_answer_that_lost_its_price_and_source_is_postprocessor_destruction() -> None:
    """Not empty, so nothing flagged it -- yet the price and the source were gone."""
    obs = StageObservation(
        provider_called=True, raw_content="x",
        text_before_postprocessing=("Hedera Hashgraph is $0.0699 USD as of 2026-08-05 11:49 UTC. "
                                    "24h change: -1.41%. Source: [CoinGecko](https://x)."),
        final_text="24h change: -1.41%.")
    assert classify_turn(obs) is TerminalState.POSTPROCESSOR_DESTROYED


def test_an_answer_wiped_to_nothing_by_decoration_is_also_destruction() -> None:
    obs = StageObservation(provider_called=True, raw_content="x",
                           text_before_postprocessing="a real answer", final_text="")
    assert classify_turn(obs) is TerminalState.POSTPROCESSOR_DESTROYED


def test_ordinary_trimming_is_not_reported_as_destruction() -> None:
    """The control. Punctuation and preamble cleanup must not be flagged, or the signal is noise."""
    obs = StageObservation(provider_called=True, raw_content="x",
                           text_before_postprocessing="Sure! Bitcoin is $64,238.00 USD as of today.",
                           final_text="Bitcoin is $64,238.00 USD as of today.")
    assert classify_turn(obs) is TerminalState.SUCCESS


def test_a_guard_refusing_a_real_answer_is_named_as_such() -> None:
    obs = StageObservation(provider_called=True, raw_content="a real answer",
                           validator_rejection="ungrounded",
                           text_before_postprocessing="", final_text="")
    assert classify_turn(obs) is TerminalState.VALIDATOR_REJECTED


def test_a_delivered_answer_is_success() -> None:
    obs = StageObservation(provider_called=True, raw_content="hello",
                           text_before_postprocessing="hello", final_text="hello")
    assert classify_turn(obs) is TerminalState.SUCCESS


def test_an_unmatched_turn_says_the_classifier_has_a_gap_not_the_runtime() -> None:
    """A wrong terminal state is worse than none -- it sends the next person confidently to the
    wrong layer, which is the failure this module exists to end."""
    assert classify_turn(StageObservation()) is TerminalState.UNCLASSIFIED
    assert "classifier has a gap" in explain(TerminalState.UNCLASSIFIED)


@pytest.mark.parametrize("state", list(TerminalState))
def test_every_state_explains_which_layer_to_look_at(state) -> None:
    """A verdict with no next step is another dead end."""
    assert explain(state).strip()


def test_an_unknown_event_contributes_nothing_rather_than_a_guess() -> None:
    obs = observation_from_trace([{"event_type": "something.new"}, {"not": "an event"}, None])
    assert classify_turn(obs) is TerminalState.UNCLASSIFIED
