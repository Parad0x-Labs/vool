"""A live-data (price/weather) request gets an explicit, inspectable LIVE_DATA classification.

Reproduces the exact benchmark spec from the handover:

    answer_mode = LIVE_DATA
    tools_required = true
    current_information_required = true
    multipart = true
    parallel_preferred = true
    inference_allowed = false
    allowed_toolsets = market_prices + weather

`_live_data_classification` reuses the SAME recognizers the fast live-info lanes and the
multipart planner's own servability check already use -- never a new keyword list -- so this can
never disagree with what actually answers the request.
"""

from __future__ import annotations

from unittest import mock

from core.execution_requirements import ExecutionRequirements, requirements_for

BENCHMARK = (
    "Give me the current price and 24-hour change for gold, silver, Bitcoin, and BNB, "
    "and the weather in Kaunas, Tallinn, and Warsaw."
)


def test_the_exact_benchmark_matches_the_full_specified_contract() -> None:
    requirements = requirements_for(BENCHMARK)
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.tools_required is True
    assert requirements.current_information_required is True
    assert requirements.multipart is True
    assert requirements.parallel_preferred is True
    assert requirements.inference_allowed is False
    assert set(requirements.allowed_toolsets) == {"market_prices", "weather"}


def test_a_single_asset_live_data_request_is_not_multipart() -> None:
    requirements = requirements_for("what is the current price of bitcoin")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.multipart is False
    assert requirements.parallel_preferred is False
    assert requirements.allowed_toolsets == ("market_prices",)


def test_a_single_city_weather_request_is_not_multipart() -> None:
    requirements = requirements_for("what is the current weather in London")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.multipart is False
    assert requirements.allowed_toolsets == ("weather",)


def test_a_multi_city_weather_request_alone_is_multipart() -> None:
    # "current" matters here: answer_mode_for() classifies a bare "weather in X" (no temporal
    # marker) as DIRECT, not GROUNDED -- a pre-existing gap in that function, unrelated to this
    # module, out of scope for this change (recorded, not fixed, in the delivery report).
    requirements = requirements_for("what is the current weather in Kaunas, Tallinn, and Warsaw")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.multipart is True
    assert requirements.parallel_preferred is True
    assert requirements.allowed_toolsets == ("weather",)


def test_non_live_data_grounded_requests_are_unaffected() -> None:
    """A general current-events question must stay plain GROUNDED, not be swept into LIVE_DATA."""
    requirements = requirements_for("what's the latest news on the EU semiconductor act")
    assert requirements.answer_mode == "GROUNDED"
    assert requirements.multipart is False
    assert requirements.allowed_toolsets == ("web_search", "web_fetch")


def test_direct_and_audit_requests_default_multipart_and_parallel_preferred_to_false() -> None:
    assert requirements_for("what is the capital of France").multipart is False
    assert requirements_for("audit this repository for security issues").multipart is False


def test_live_data_never_permits_inference_even_when_the_message_would_otherwise_allow_it() -> None:
    """A live-data answer must always come from a real fetch, never a guessed number -- unlike
    plain GROUNDED, which only forbids inference when the user explicitly said not to guess."""
    requirements = requirements_for("what is the current price of bitcoin, just estimate if unsure")
    assert requirements.answer_mode == "LIVE_DATA"
    assert requirements.inference_allowed is False


def test_no_consumer_may_be_more_restrictive_than_live_data_tools_required() -> None:
    """The existing corpus invariant (tests/test_execution_requirements.py) generalizes: a
    LIVE_DATA classification must still satisfy tools_required=True, so nothing downstream that
    already trusts tools_required can regress by this classification existing."""
    requirements = requirements_for(BENCHMARK)
    assert requirements.forbids_toolless_lane() is True


def test_sabotage_removing_the_live_data_branch_reverts_the_benchmark_to_plain_grounded() -> None:
    """Proves the branch is load-bearing: patch it out entirely and the benchmark's answer_mode
    and multipart/parallel_preferred fields silently lose their meaning."""
    with mock.patch("core.execution_requirements._live_data_classification", return_value=None):
        requirements = requirements_for(BENCHMARK)
    assert requirements.answer_mode == "GROUNDED"
    assert requirements.multipart is False
    assert requirements.parallel_preferred is False
    assert requirements.allowed_toolsets == ("web_search", "web_fetch")


def test_execution_requirements_still_accepts_positional_free_construction() -> None:
    """The two new fields default to False, so every pre-existing call site that constructs
    ExecutionRequirements without naming them keeps working unchanged."""
    requirements = ExecutionRequirements(
        answer_mode="DIRECT",
        external_evidence_required=False,
        current_information_required=False,
        user_material_supplied=False,
        tools_required=False,
    )
    assert requirements.multipart is False
    assert requirements.parallel_preferred is False
