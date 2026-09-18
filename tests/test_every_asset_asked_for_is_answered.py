"""Asking for two prices and getting one, with no mention of the other, is a wrong answer.

Measured live 2026-08-03:

    "btc price now? and sol price please"   -> Bitcoin only
    "what about other price i asked?"       -> Bitcoin again
    "sol price now?"                        -> Solana, correctly

The lookup could do both. `first_live_quote` returned on the first valid payload and the rest were
discarded, and `_extract_price_asset_alias` returned the first alias and stopped, so nothing
downstream even knew a second asset had been named.

This is the same defect as the second filename dropped from a read, the second file dropped from a
batch, the eight deferred tools, and the tool results starved out of the prompt window. Fourth
lane, one shape: the first match wins and the remainder disappears in silence.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_price import (
    price_assets_named,
    price_request_leaves_unresolved_content,
    recover_price_lookup_query,
)
from core.agent_runtime.fast_live_info_quote_rendering import all_live_quotes


def test_the_measured_query_names_both_assets() -> None:
    assert price_assets_named("btc price now? and sol price please") == ["btc", "sol"]


def test_assets_are_returned_in_the_order_they_were_asked_for() -> None:
    """The reply reads as an answer to the question, not as a set lookup."""

    assert price_assets_named("sol and btc please") == ["sol", "btc"]
    assert price_assets_named("btc and sol please") == ["btc", "sol"]


def test_three_assets_all_survive() -> None:
    assert price_assets_named("btc and eth and sol prices") == ["btc", "eth", "sol"]


def test_a_longer_alias_is_not_double_counted() -> None:
    """`bitcoin` contains no `btc`, but the guard matters for any alias pair that overlaps.

    Without longest-first claiming, one spelled-out asset could be reported twice and the reply
    would answer a question nobody asked.
    """

    assert price_assets_named("bitcoin price") == ["bitcoin"]


def test_a_single_asset_is_unchanged() -> None:
    """The control. This phrasing already worked and must keep working."""

    assert price_assets_named("sol price now?") == ["sol"]


@pytest.mark.parametrize("query", ["what is the weather", "how are you", ""])
def test_a_query_naming_no_asset_returns_nothing(query: str) -> None:
    assert price_assets_named(query) == []


# --------------------------------------------------------------------------------------
# The quote side: every valid quote in the notes, not the first one.
# --------------------------------------------------------------------------------------


def _note(asset: str, symbol: str, price: float) -> dict:
    """The real `live_quote_contract` shape — a fixture the validator actually accepts.

    The first draft of this file invented field names and every quote was silently rejected, so
    three tests failed against a working fix. Worth keeping: a fixture the contract refuses proves
    nothing about the code.
    """

    return {
        "live_quote": {
            "asset_name": asset,
            "symbol": symbol,
            "value": price,
            "currency": "USD",
            "as_of": "2026-08-03T13:42:00Z",
            "source_label": "CoinGecko",
            "source_url": "https://www.coingecko.com/",
        }
    }


def test_every_valid_quote_is_returned() -> None:
    quotes = all_live_quotes([_note("Bitcoin", "BTC", 62835.0), _note("Solana", "SOL", 72.73)])

    assert len(quotes) == 2


def test_a_duplicate_quote_appears_once() -> None:
    quotes = all_live_quotes([_note("Bitcoin", "BTC", 62835.0), _note("Bitcoin", "BTC", 62835.0)])

    assert len(quotes) == 1


def test_notes_without_quotes_are_skipped_not_fatal() -> None:
    quotes = all_live_quotes(
        [{"summary": "some article"}, _note("Solana", "SOL", 72.73), {"live_quote": "not a dict"}]
    )

    assert len(quotes) == 1


def test_no_quotes_at_all_is_empty_not_an_error() -> None:
    assert all_live_quotes([]) == []
    assert all_live_quotes([{"summary": "nothing here"}]) == []


# --------------------------------------------------------------------------------------
# The note must not contradict the line above it.
# --------------------------------------------------------------------------------------


def _render(query: str, notes: list[dict]) -> str:
    from core.agent_runtime.fast_live_info_generic_rendering import render_live_info_response

    return render_live_info_response(query=query, notes=notes, mode="fresh_lookup")


QUERY = "btc price now? and sol price please"


def test_an_answered_asset_is_never_reported_missing() -> None:
    """Driven live 2026-08-03 and WRONG in the first version of this fix.

    The check compared the operator's alias against the rendered prose - `"btc" in "Bitcoin is
    $63,791..."` - and `btc` is not a substring of `bitcoin`. So the reply showed the Bitcoin price
    and, two lines later, said no quote came back for `btc`. A note that contradicts the line above
    it is worse than no note at all.
    """

    out = _render(QUERY, [_note("Bitcoin", "BTC", 63791.0)])

    assert "Bitcoin is" in out
    assert "`btc`" not in out, "an asset that WAS answered is being reported missing"
    assert "`sol`" in out, "the asset that genuinely did not come back must still be named"


def test_no_note_at_all_when_everything_asked_for_came_back() -> None:
    """Both assets answered means no missing-asset note.

    Asserted on the names and the NUMBERS rather than on the sentence form: two or more quotes now
    render as a comparison table (`quotes_as_table`), so "Bitcoin is ..." is no longer the shape.
    The claim under test was never about prose -- it is that both assets are present and nothing is
    reported missing -- and checking the prices makes it stricter than the string it replaces.
    """

    out = _render(QUERY, [_note("Bitcoin", "BTC", 63791.0), _note("Solana", "SOL", 72.7)])

    assert "Bitcoin" in out and "63,791" in out
    assert "Solana" in out and "72.70" in out
    assert "No live quote" not in out


def test_a_single_asset_request_never_gets_a_note() -> None:
    """The control: one asset asked, one answered, nothing to report."""

    out = _render("sol price now?", [_note("Solana", "SOL", 72.7)])

    assert "Solana is" in out
    assert "No live quote" not in out


def test_a_symbol_only_quote_still_matches_its_alias() -> None:
    """Some sources label a quote by symbol rather than by name."""

    out = _render(QUERY, [_note("BTC", "BTC", 63791.0), _note("SOL", "SOL", 72.7)])

    assert "No live quote" not in out


# --------------------------------------------------------------------------------------
# Live incident, 2026-08-03T22:05 UTC, session openclaw:c2588e75f94349eace3b:
# "ok proce for BNB and ARB please?" reached the model with no tools offered at all, and the
# model's free-formed, never-executed `web.search` call shape was rendered verbatim as the final
# answer. Root cause: `recover_price_lookup_query` used the first-match-only
# `_extract_price_asset_alias`, so "BNB and ARB" recovered to "bnb price now" and ARB's name never
# reached anything downstream -- not the search, not the render, not a "couldn't find it" note.
# `ARB` is not in `_PRICE_ASSET_ALIASES` at all, which is a second, independent gap: an
# unrecognized asset must still be accounted for, not just a second recognized one.
#
# Two follow-up fix attempts tried to CLOSE that gap by guessing which leftover word in the
# message was the missing ticker -- first a hand-written stopword blocklist ("BTC PRICE?" ->
# phantom `PRICE` asset, because no blocklist enumerates every ordinary word a person might type
# in caps), then a list-connector structural test ("price for bnb and arb please?" lowercase ->
# `arb` vanished again, because the candidate regex was `[A-Z]{2,6}` only; "TELL ME THE PRICE FOR
# BNB ARB AND SUI" -> `sui` vanished too, because there is no connector between `bnb` and `arb`).
# Three real, independently confirmed regressions from the same shape of mechanism.
#
# The fix here does not try a fourth guess. It reverts the extraction to ONLY the deterministic,
# already-reliable alias table (`price_assets_named`) and, when a price-shaped message plausibly
# names something beyond what that table resolves, DECLINES the fast path outright instead of
# answering part of the request. The decline is flagged so the turn is routed to a lane where a
# real tool is actually offered -- see `prepare_live_info_request`,
# `should_keep_ai_first_chat_lane`, and `should_attempt_tool_intent`.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "price for BNB and ARB please?",
        "price for bnb and arb please?",
        "ok price for BNB and ARB please?",
        "TELL ME THE PRICE FOR BNB ARB AND SUI",
        "WHAT IS THE PRICE OF BTC AND MORE",
    ],
)
def test_a_price_request_naming_unresolved_content_is_flagged(text: str) -> None:
    assert price_request_leaves_unresolved_content(text)


@pytest.mark.parametrize(
    "text",
    [
        "BTC PRICE?",
        "WHAT IS THE PRICE OF BTC",
        "SOL PRICE PLEASE",
        "sol price now?",
        "btc price now? and sol price please",
        "how much is bitcoin",
        "ok price for BNB please, OK?",
        "please explain ARB rollups",
        "what is the weather",
    ],
)
def test_a_fully_resolvable_price_request_is_not_flagged(text: str) -> None:
    """The control set: every one of these must keep answering fast and deterministically. A
    request with no recognized asset at all (weather, "explain ARB rollups") is not price-shaped
    in the first place and must never be flagged either."""

    assert not price_request_leaves_unresolved_content(text)


def test_recover_price_lookup_query_still_keeps_every_recognized_asset() -> None:
    """The a02dd8b fix this all sits on top of: multiple RECOGNIZED aliases all get answered, not
    just the first. This must not regress now that the bare-ticker guess is gone."""

    recovered = recover_price_lookup_query("btc price now? and sol price please", source_context=None)

    assert recovered == "btc, sol price now"


def test_recover_price_lookup_query_single_asset_is_unchanged() -> None:
    """The control: this exact string is asserted verbatim by other tests in this repo and must
    not gain a stray comma or wrapper now that multi-asset recovery exists."""

    assert recover_price_lookup_query("how much is bitcoin", source_context=None) == "bitcoin price now"


@pytest.mark.parametrize(
    "text",
    [
        "price for BNB and ARB please?",
        "price for bnb and arb please?",
        "TELL ME THE PRICE FOR BNB ARB AND SUI",
        "WHAT IS THE PRICE OF BTC AND MORE",
    ],
)
def test_recover_price_lookup_query_declines_rather_than_drop_the_extra_name(text: str) -> None:
    """A regex guess at the missing name has failed three times running (see the incident note
    above). Declining cleanly -- returning "" so the caller routes to a real tool -- is the
    contract now, not a best-effort string that quietly omits ARB/SUI/MORE."""

    assert recover_price_lookup_query(text, source_context=None) == ""


def test_the_fast_path_declines_and_flags_the_turn_for_a_real_tool() -> None:
    """`_maybe_handle_live_info_fast_path` must not answer part of a request it cannot fully
    resolve. It must decline (return None) so the turn is NOT hijacked by a deterministic partial
    answer, and it must flag `source_context` so the tool-loop gates (`should_keep_ai_first_chat_lane`,
    `should_attempt_tool_intent`) route the decline to a lane with a real tool instead of the
    tools-less "unknown" chat lane that produced the original incident.
    """

    from types import SimpleNamespace

    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    for text in (
        "price for BNB and ARB please?",
        "price for bnb and arb please?",
        "TELL ME THE PRICE FOR BNB ARB AND SUI",
        "WHAT IS THE PRICE OF BTC AND MORE",
    ):
        source_context: dict[str, object] = {"surface": "openclaw", "platform": "openclaw"}
        result = agent._maybe_handle_live_info_fast_path(
            text,
            session_id="test-session-partial",
            source_context=source_context,
            interpretation=SimpleNamespace(topic_hints=[]),
        )
        assert result is None, f"{text!r} should decline the deterministic fast path, got {result!r}"
        assert source_context.get("live_info_partial_unresolved") is True, text


def test_a_fully_resolvable_request_still_answers_on_the_fast_path(enable_web) -> None:
    """The control: a request the alias table can fully resolve must keep answering immediately,
    with no flag and no detour through a model or a tool."""

    from types import SimpleNamespace
    from unittest import mock

    from apps.vool_agent import VoolAgent
    from core.live_quote_contract import LiveQuoteResult

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def fake_lookup_live_quote(query: str, timeout_s: float = 8.0):
        return LiveQuoteResult(
            asset_key="bitcoin",
            asset_name="Bitcoin",
            symbol="BTC",
            value=63791.0,
            currency="USD",
            as_of="2026-08-03T22:05:00Z",
            source_label="CoinGecko",
            source_url="https://coingecko.com",
            kind="crypto",
        )

    source_context: dict[str, object] = {"surface": "openclaw", "platform": "openclaw"}
    with mock.patch("tools.web.web_research.lookup_live_quote", side_effect=fake_lookup_live_quote):
        result = agent._maybe_handle_live_info_fast_path(
            "BTC PRICE?",
            session_id="test-session-btc-only",
            source_context=source_context,
            interpretation=SimpleNamespace(topic_hints=[]),
        )

    assert result is not None
    assert "63,791" in str(result.get("response") or "")
    assert result.get("route") == "deterministic:live_info_fast_path"
    assert "live_info_partial_unresolved" not in source_context


# --------------------------------------------------------------------------------------
# The routing gates a decline must actually reach. Unit-level (not full-agent) so the exact
# condition each gate checks is pinned down independent of the rest of the turn pipeline --
# mirrors the pattern in tests/test_code_location_question_reaches_the_tools.py.
# --------------------------------------------------------------------------------------


class _LaneAgent:
    def _is_chat_truth_surface(self, source_context) -> bool:
        return True

    def _looks_like_explicit_resume_request(self, user_input) -> bool:
        return False

    def __getattr__(self, name):
        return lambda *args, **kwargs: False


def test_a_flagged_turn_leaves_the_tools_less_chat_lane() -> None:
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane

    keep = should_keep_ai_first_chat_lane(
        _LaneAgent(),
        user_input="TELL ME THE PRICE FOR BNB ARB AND SUI",
        classification={"task_class": "unknown"},
        interpretation=None,
        source_context={"live_info_partial_unresolved": True},
        checkpoint_state=None,
    )

    assert keep is False, "a flagged decline must not be kept in the tools-less AI-first chat lane"


def test_an_unflagged_unknown_turn_still_keeps_the_chat_lane() -> None:
    """The control: this function's existing behaviour for an ordinary unknown/chat turn must not
    change for anything that never touched the price fast path."""

    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane

    keep = should_keep_ai_first_chat_lane(
        _LaneAgent(),
        user_input="tell me a joke",
        classification={"task_class": "unknown"},
        interpretation=None,
        source_context={},
        checkpoint_state=None,
    )

    assert keep is True


def test_a_flagged_turn_is_offered_a_real_tool() -> None:
    from core.execution.planner import should_attempt_tool_intent

    for text in (
        "price for bnb and arb please?",
        "TELL ME THE PRICE FOR BNB ARB AND SUI",
        "WHAT IS THE PRICE OF BTC AND MORE",
    ):
        assert should_attempt_tool_intent(
            text,
            task_class="unknown",
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "live_info_partial_unresolved": True,
            },
        ), text


def test_without_the_flag_these_inputs_still_reach_a_tool_via_live_data_classification() -> None:
    """When this test was first written, `live_info_partial_unresolved` was the ONLY gate that
    could route these three confirmed-failure phrasings to a real tool -- absent the flag, neither
    gate recognized them, and a turn that merely declined the deterministic fast path would fall
    through to the tools-less lane and free-form a response, exactly what happened live.

    That is no longer true, and this is an improvement rather than a regression: the LIVE_DATA
    classification fix (`core.execution_requirements._live_data_classification`, made unconditional
    of the DIRECT/GROUNDED split so genuine multi-asset price/weather requests are never silently
    routed to plain model prose -- see the fabricated-Bitcoin-price incident) recognizes
    "price for X and Y" phrasing as a multipart live-data request on its own, independent of
    whether the fast path ever ran or set the flag. The flag-based path
    (`test_a_flagged_turn_is_offered_a_real_tool`) still exists and still works; this test now
    proves there is a SECOND, independent mechanism that reaches the same safe outcome, so a future
    regression in the flag-setting logic alone would no longer reproduce the original incident for
    this class of phrasing."""

    from core.execution.planner import should_attempt_tool_intent

    for text in (
        "price for bnb and arb please?",
        "TELL ME THE PRICE FOR BNB ARB AND SUI",
        "WHAT IS THE PRICE OF BTC AND MORE",
    ):
        assert should_attempt_tool_intent(
            text,
            task_class="unknown",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        ), text
