"""A bare output-format token must never co-sign a turn into live web research.

Live defect (2026-08-14, session openclaw:ed890df3 seqs 36-56): "What is the capital of France?
... Constraints: raw JSON key-value" ran a real web retrieval. The classifier mislabelling the
turn "config" is a model-intelligence error and is NOT patched here; the engineering defect was
that the runtime's corroboration check rubber-stamped it -- the single substring "json" from the
OUTPUT-FORMAT clause counted as "specific troubleshooting evidence".

The fix is a conservative linguistic detector over the whole text, keyed to no prompt: artifact/
format names (.env/yaml/json/config/dependency/version) count as troubleshooting evidence only
when the text also carries a problem cue; genuine problem words (traceback/exception/error/...)
keep firing alone. Every test here drives the REAL `_adaptive_research_decision`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import core.curiosity_roamer as curiosity_roamer
from core.curiosity_roamer import _adaptive_research_decision


def _decide(text: str, *, task_class: str = "config") -> dict:
    # `allow_web_fallback` is pinned open so these tests measure the marker logic, not the
    # runtime's web policy: with the policy closed every case would return disabled and the
    # detector under test would never be reached.
    with mock.patch.object(curiosity_roamer.policy_engine, "allow_web_fallback", return_value=True):
        return _adaptive_research_decision(
            user_input=text,
            classification={"task_class": task_class},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context={"surface": "openclaw"},
        )


# ---- the defect class, in NEW wording (never the recorded prompt verbatim) ---------------------

def test_a_yaml_output_constraint_on_a_geography_question_does_not_research() -> None:
    decision = _decide("Answer as YAML only. What is the capital of Spain?")
    assert decision["enabled"] is False, decision


def test_a_json_output_constraint_does_not_research() -> None:
    decision = _decide("What is the capital of France? Constraints: raw JSON key-value")
    assert decision["enabled"] is False, decision


def test_sloppy_adversarial_format_only_phrasing_does_not_research() -> None:
    # Malformed/sloppy typing of the same class -- must fall to the same rule, not a special case.
    decision = _decide("constraints:: rawJSON pls no braces!! capital of spain??")
    assert decision["enabled"] is False, decision


def test_a_bare_version_word_does_not_research() -> None:
    decision = _decide("Give me the short version: who wrote Hamlet?", task_class="chat_conversation")
    assert decision["enabled"] is False, decision


# ---- controls: genuine troubleshooting and lookups KEEP their research --------------------------

def test_a_failing_parse_of_a_yaml_config_still_researches() -> None:
    decision = _decide("the yaml config fails to parse after the upgrade")
    assert decision["enabled"] is True, decision
    assert decision["needs_specific_focus"] is True, decision


def test_a_named_exception_still_researches() -> None:
    decision = _decide("my package.json throws JSONDecodeError on npm install", task_class="debugging")
    assert decision["enabled"] is True, decision


def test_a_problem_word_alone_still_researches() -> None:
    decision = _decide("I keep getting a weird error when the app starts", task_class="debugging")
    assert decision["enabled"] is True, decision


def test_an_explicit_lookup_is_unaffected() -> None:
    decision = _decide("look up the latest node version for me", task_class="chat_conversation")
    assert decision["enabled"] is True, decision


def test_a_dotted_env_file_with_a_problem_cue_still_researches() -> None:
    decision = _decide("my .env is not working after the rename", task_class="config")
    assert decision["enabled"] is True, decision
