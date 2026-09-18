"""One normalized provider result for both ModelResponse and CloudModelResponse.

The explicit regression the operator's closure pass asked for: the second call in a multi-tool
batch must survive normalize_model_response AND normalize_cloud_model_response identically. The
free-cloud lane already dropped call #2 once (see the inline comment on
memory_first_router._try_free_cloud_boost's tool_calls= line, and
tests/test_cloud_free_lane_reachability.py's own regression test for that exact incident) because
the two response types were hand-converted independently. Routing everything through ONE tested
function per source type is what makes that class of drift structurally harder to reintroduce.
"""
from __future__ import annotations

import pytest

from adapters.base_adapter import ModelResponse
from core.cloud_provider_contract import CloudModelResponse, CloudToolCall
from core.normalized_provider_result import (
    EmptyProviderResponseError,
    MalformedProviderResponseError,
    ProviderErrorClass,
    classify_error_class,
    is_retryable,
    normalize_cloud_model_response,
    normalize_model_response,
)

CALL_1 = CloudToolCall(call_id="c1", intent="sandbox.run_command", name="sandbox__run_command", arguments={"command": "pwd"})
CALL_2 = CloudToolCall(call_id="c2", intent="web.search", name="web__search", arguments={"query": "vool"})


def test_model_response_multi_call_batch_survives_normalization() -> None:
    response = ModelResponse(
        output_text='{"intent":"sandbox.run_command","arguments":{"command":"pwd"}}',
        usage={"prompt_tokens": 100, "completion_tokens": 20},
        provider_id="local-qwen-http:qwen-local",
        model_name="qwen-local",
        tool_calls=(CALL_1, CALL_2),
    )
    normalized = normalize_model_response(
        response, requested_model="qwen-local", resolved_provider="local-qwen-http:qwen-local", resolved_model="qwen-local"
    )
    assert len(normalized.tool_calls) == 2
    assert [c.intent for c in normalized.tool_calls] == ["sandbox.run_command", "web.search"]


def test_cloud_model_response_multi_call_batch_survives_normalization() -> None:
    """The exact shape of the historical incident: CloudModelResponse, not ModelResponse."""
    response = CloudModelResponse(
        output_text='{"intent":"sandbox.run_command","arguments":{"command":"pwd"}}',
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=(CALL_1, CALL_2),
    )
    normalized = normalize_cloud_model_response(
        response, requested_model="vendor/model:free", resolved_provider="openrouter", resolved_model="vendor/model:free"
    )
    assert len(normalized.tool_calls) == 2
    assert [c.intent for c in normalized.tool_calls] == ["sandbox.run_command", "web.search"]


def test_both_source_types_produce_the_identical_normalized_shape_for_equivalent_input() -> None:
    """Same logical response, two different source dataclasses -- the normalized fields that
    both can express must come out identical, so a downstream reader cannot tell which lane
    answered from the shape of what it received."""
    model_response = ModelResponse(output_text="hello", usage={"prompt_tokens": 5, "completion_tokens": 2}, tool_calls=())
    cloud_response = CloudModelResponse(output_text="hello", usage={"prompt_tokens": 5, "completion_tokens": 2}, tool_calls=())

    a = normalize_model_response(model_response, resolved_provider="p", resolved_model="m")
    b = normalize_cloud_model_response(cloud_response, resolved_provider="p", resolved_model="m", actual_provider="p", actual_model="m")

    assert (
        a.text, a.usage_input, a.usage_output, a.usage_input_reported, a.usage_output_reported, a.tool_calls, a.finish_reason
    ) == (
        b.text, b.usage_input, b.usage_output, b.usage_input_reported, b.usage_output_reported, b.tool_calls, b.finish_reason,
    )


def test_missing_usage_is_distinguishable_from_reported_zero() -> None:
    reported_zero = normalize_model_response(ModelResponse(output_text="x", usage={"prompt_tokens": 0, "completion_tokens": 0}))
    missing = normalize_model_response(ModelResponse(output_text="x", usage={}))
    assert reported_zero.usage_input_reported is True
    assert reported_zero.usage_output_reported is True
    assert reported_zero.usage_input == 0
    assert missing.usage_input_reported is False
    assert missing.usage_output_reported is False
    # same numeric value as reported_zero -- the *_reported flags are what distinguishes them
    assert missing.usage_input == 0


# --- SWITCHBOARD Repair 5: usage_input_reported and usage_output_reported must be independent --
#
# Before this repair, NormalizedProviderResult carried one combined `usage_reported` field
# (input_reported OR output_reported), and memory_first_router._try_free_cloud_boost recorded
# THAT SAME combined value into both usage_meter columns:
#   prompt_tokens_reported=normalized.usage_reported, output_tokens_reported=normalized.usage_reported
# so e.g. usage={"completion_tokens": 12} (only the output count present) incorrectly recorded
# prompt_tokens_reported=True even though no prompt count was ever supplied -- fabricating a
# "the provider reported this" claim for a field it never touched.


@pytest.mark.parametrize(
    "usage,expected_input,expected_input_reported,expected_output,expected_output_reported",
    [
        pytest.param({}, 0, False, 0, False, id="both_absent"),
        pytest.param({"prompt_tokens": 0, "completion_tokens": 0}, 0, True, 0, True, id="both_present_zero"),
        pytest.param({"prompt_tokens": 7}, 7, True, 0, False, id="only_prompt_present"),
        pytest.param({"completion_tokens": 12}, 0, False, 12, True, id="only_completion_present"),
        pytest.param({"prompt_tokens": "not-a-number", "completion_tokens": 3}, 0, True, 3, True, id="malformed_numeric_prompt"),
        pytest.param({"prompt_tokens": 5, "completion_tokens": "garbage"}, 5, True, 0, True, id="malformed_numeric_completion"),
        pytest.param({"prompt_tokens": 5, "completion_tokens": 9}, 5, True, 9, True, id="both_present_nonzero"),
    ],
)
def test_usage_presence_flags_are_independent_per_field_on_system_a(
    usage, expected_input, expected_input_reported, expected_output, expected_output_reported
) -> None:
    result = normalize_model_response(ModelResponse(output_text="x", usage=usage))
    assert result.usage_input == expected_input
    assert result.usage_input_reported is expected_input_reported
    assert result.usage_output == expected_output
    assert result.usage_output_reported is expected_output_reported


@pytest.mark.parametrize(
    "usage,expected_input,expected_input_reported,expected_output,expected_output_reported",
    [
        pytest.param({}, 0, False, 0, False, id="both_absent"),
        pytest.param({"prompt_tokens": 0, "completion_tokens": 0}, 0, True, 0, True, id="both_present_zero"),
        pytest.param({"prompt_tokens": 7}, 7, True, 0, False, id="only_prompt_present"),
        pytest.param({"completion_tokens": 12}, 0, False, 12, True, id="only_completion_present"),
        pytest.param({"prompt_tokens": "not-a-number", "completion_tokens": 3}, 0, True, 3, True, id="malformed_numeric_prompt"),
        pytest.param({"prompt_tokens": 5, "completion_tokens": "garbage"}, 5, True, 0, True, id="malformed_numeric_completion"),
        pytest.param({"prompt_tokens": 5, "completion_tokens": 9}, 5, True, 9, True, id="both_present_nonzero"),
    ],
)
def test_usage_presence_flags_are_independent_per_field_on_system_b(
    usage, expected_input, expected_input_reported, expected_output, expected_output_reported
) -> None:
    result = normalize_cloud_model_response(CloudModelResponse(output_text="x", usage=usage))
    assert result.usage_input == expected_input
    assert result.usage_input_reported is expected_input_reported
    assert result.usage_output == expected_output
    assert result.usage_output_reported is expected_output_reported


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 0, "completion_tokens": 0},
        {"prompt_tokens": 7},
        {"completion_tokens": 12},
        {"prompt_tokens": "not-a-number", "completion_tokens": 3},
        {"prompt_tokens": 5, "completion_tokens": 9},
    ],
)
def test_system_a_and_system_b_record_identical_usage_presence_flags_for_identical_payloads(usage) -> None:
    """The explicit parity requirement: the same usage payload must produce the same *_reported
    flags regardless of which response type carried it."""
    a = normalize_model_response(ModelResponse(output_text="x", usage=usage))
    b = normalize_cloud_model_response(CloudModelResponse(output_text="x", usage=usage))
    assert (a.usage_input, a.usage_input_reported, a.usage_output, a.usage_output_reported) == (
        b.usage_input, b.usage_input_reported, b.usage_output, b.usage_output_reported,
    )


def test_a_malformed_numeric_usage_value_does_not_crash_normalization() -> None:
    """SWITCHBOARD repair: _usage_tokens used to call `int(...)` unguarded -- a provider sending
    a non-numeric usage field (e.g. a string where a count is expected) raised ValueError out of
    a function whose job is normalizing a response that already arrived successfully, turning a
    malformed metering field into a crash of the entire response path."""
    result = normalize_model_response(ModelResponse(output_text="x", usage={"prompt_tokens": "NaN", "completion_tokens": None}))
    assert result.usage_input == 0
    assert result.usage_input_reported is True  # the field WAS present, just unparseable
    assert result.usage_output == 0
    assert result.usage_output_reported is False  # completion_tokens was genuinely None/absent


def test_substitution_authorized_defaults_true_and_is_carried_through() -> None:
    normalized = normalize_model_response(ModelResponse(output_text="x"), substitution_authorized=False)
    assert normalized.substitution_authorized is False
    normalized2 = normalize_model_response(ModelResponse(output_text="x"))
    assert normalized2.substitution_authorized is True


def test_provenance_fields_are_all_independently_readable() -> None:
    normalized = normalize_model_response(
        ModelResponse(output_text="x", provider_id="actual-p", model_name="actual-m"),
        requested_provider="req-p", requested_model="req-m",
        resolved_provider="res-p", resolved_model="res-m",
    )
    assert normalized.requested_provider == "req-p"
    assert normalized.requested_model == "req-m"
    assert normalized.resolved_provider == "res-p"
    assert normalized.resolved_model == "res-m"
    assert normalized.actual_provider == "actual-p"
    assert normalized.actual_model == "actual-m"


def test_error_free_response_has_no_error_class_and_is_not_retryable() -> None:
    normalized = normalize_model_response(ModelResponse(output_text="ok"))
    assert normalized.error_class is None
    assert normalized.retryable is False


def test_bounded_diagnostic_is_actually_bounded() -> None:
    huge = "x" * 5000
    normalized = normalize_model_response(ModelResponse(output_text=huge))
    assert len(normalized.bounded_diagnostic) <= 501  # 500 + ellipsis


# --- error classification -------------------------------------------------------------------


def test_required_tools_not_offered_classifies_correctly_and_is_not_retryable() -> None:
    error_class = classify_error_class("required_tools_not_offered:REQUIRED_TOOLS_NOT_OFFERED: offered=0")
    assert error_class == ProviderErrorClass.REQUIRED_TOOLS_NOT_OFFERED
    assert is_retryable(error_class) is False


def test_malformed_tool_call_variants_all_classify_to_the_same_class() -> None:
    for message in ("unknown_tool_name:...", "malformed_tool_arguments:...", "duplicate_tool_call:..."):
        assert classify_error_class(message) == ProviderErrorClass.MALFORMED_TOOL_CALL


def test_timeout_classifies_as_retryable() -> None:
    error_class = classify_error_class("Read timed out (read timeout=59.99)")
    assert error_class == ProviderErrorClass.PROVIDER_TIMEOUT
    assert is_retryable(error_class) is True


def test_rate_limit_classifies_as_retryable() -> None:
    assert is_retryable(classify_error_class("provider rate or quota limit reached")) is True


def test_context_overflow_classifies_as_not_retryable() -> None:
    error_class = classify_error_class("prompt_budget_exceeded:too many tokens")
    assert error_class == ProviderErrorClass.CONTEXT_LENGTH_EXCEEDED
    assert is_retryable(error_class) is False


# The exception type, not one memorized English sentence, is the adapter-to-policy contract.  This
# matrix deliberately includes the reported diagnostic, clean rewordings, and operator-log-grade
# typo/slang variants so a wording cleanup cannot silently disable recovery.
EMPTY_RESPONSE_DIAGNOSTICS = [
    pytest.param("OpenAI-compatible response did not include choices.", id="reported_wording"),
    pytest.param("Provider returned no candidate choices.", id="clean_no_candidates"),
    pytest.param("The completion envelope contained an empty choice list.", id="clean_empty_list"),
    pytest.param("No answer candidate was present in the provider payload.", id="clean_absent_candidate"),
    pytest.param("The provider response was structurally valid but carried no answer.", id="clean_structural_empty"),
    pytest.param("Response JSON arrived without a usable completion choice.", id="clean_no_completion"),
    pytest.param("no choises in resp", id="sloppy_typo_choices"),
    pytest.param("got 200 but nada in candidates", id="sloppy_nada"),
    pytest.param("resp empty-ish; zero answer slots", id="sloppy_emptyish"),
    pytest.param("provider gave [] lol", id="sloppy_brackets"),
    pytest.param("no answr candidate came back", id="sloppy_answer_typo"),
]


@pytest.mark.parametrize("diagnostic", EMPTY_RESPONSE_DIAGNOSTICS)
def test_typed_empty_response_classification_is_wording_independent(diagnostic: str) -> None:
    error_class = classify_error_class(EmptyProviderResponseError(diagnostic))
    assert error_class == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE
    assert is_retryable(error_class) is True


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(
            MalformedProviderResponseError("choices was an object, not a list"),
            id="negative_wrong_container",
        ),
        pytest.param(RuntimeError("choice zero contained a scalar"), id="negative_unknown_runtime"),
        pytest.param(ValueError("the message object has an invalid field type"), id="negative_value_error"),
    ],
)
def test_nonempty_structural_defects_do_not_become_retryable_empty_errors(error: Exception) -> None:
    error_class = classify_error_class(error)
    assert error_class != ProviderErrorClass.EMPTY_PROVIDER_RESPONSE
    assert is_retryable(error_class) is False


def test_adversarial_near_miss_named_choices_but_wrong_typed_is_malformed() -> None:
    error = MalformedProviderResponseError(
        "response includes choices, but choices is a mapping rather than an empty list"
    )
    error_class = classify_error_class(error)
    assert error_class == ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE
    assert is_retryable(error_class) is False


def test_empty_message_and_none_classify_to_nothing() -> None:
    assert classify_error_class(None) is None
    assert classify_error_class("") is None
    assert classify_error_class("   ") is None


def test_a_genuinely_unrecognized_message_classifies_to_none_not_internal_error() -> None:
    """SWITCHBOARD-repaired: this test used to assert the BUG (unmatched -> PROVIDER_INTERNAL_ERROR,
    which made an arbitrary unknown exception -- a local bug, an exception type never seen before --
    silently become a RETRYABLE provider failure). classify_error_class's own docstring already
    promised None; the implementation contradicted it. None is a claim of ignorance; guessing
    PROVIDER_INTERNAL_ERROR is a false, specific, actionable claim that is_retryable() then acts on."""
    error_class = classify_error_class("something completely unprecedented happened")
    assert error_class is None
    assert is_retryable(error_class) is False


@pytest.mark.parametrize(
    "message",
    [
        # The exact strings raised by adapters/openrouter_cloud_provider.py,
        # adapters/cloudflare_workers_ai_provider.py, adapters/generic_openai_cloud_provider.py.
        # Every one of them contains BOTH the generic "malformed provider response" prefix AND a
        # specific empty-content marker. SWITCHBOARD repair: _ERROR_CLASS_MARKERS used to check the
        # generic marker first, so all three misclassified as MALFORMED_PROVIDER_RESPONSE instead
        # of the more specific, more actionable EMPTY_PROVIDER_RESPONSE -- the wire format was fine,
        # the provider just sent nothing back.
        "malformed provider response: no content in any choice and no tool call",
        "malformed provider response: no content in result",
        "malformed provider response: no content in any choice",
    ],
)
def test_compound_empty_content_messages_classify_as_empty_not_generic_malformed(message: str) -> None:
    error_class = classify_error_class(message)
    assert error_class == ProviderErrorClass.EMPTY_PROVIDER_RESPONSE, (
        f"{message!r} classified as {error_class}, expected EMPTY_PROVIDER_RESPONSE -- the specific "
        "marker must win over the generic 'malformed provider response' prefix shared by all three."
    )
    # EMPTY_PROVIDER_RESPONSE is in RETRYABLE_ERROR_CLASSES by design (a genuinely empty HTTP 200
    # is often transient); MALFORMED_PROVIDER_RESPONSE is not. Retryability itself is proof the two
    # classes were told apart, not just that some markers matched.
    assert is_retryable(error_class) is True
