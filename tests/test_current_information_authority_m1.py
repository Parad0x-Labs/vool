"""M1 -- ONE authority owns current-information requirements.

The audit (2026-09-01, base a2308a26) measured five independent freshness vocabularies
disagreeing, and retrieval running while the canonical ``current_information_required``
read False -- which disarms every grounding guard that consults it
(``core.unsourced_current_claim`` exits early on ``requires_current=False``).  These tests
pin the repaired contract:

  * all 22 audit paraphrases classify current-required; the 13 measured misses flip;
  * a reason code names the signal that escalated each former miss;
  * timeless/ordinary controls stay DIRECT;
  * whenever a production retrieval scheduler fires, the canonical requirement is True
    (structurally for every text the fast lane claims, and end-to-end through the real
    fast-path seam);
  * a lane freshness decision ESCALATES the canonical requirement (typed, monotone,
    reason-coded) instead of deciding alone;
  * once synthesis begins the frozen decision can neither widen nor flip;
  * restoring a scheduler's private authority (sabotage S1) reds the invariant.

The sabotage test proves the invariant catches the defect class this milestone exists to
remove: it patches the canonical door into a no-op (a scheduler that decides freshness
alone again) and points the lane's private vocabulary at a control text the authority
does not know.  Retrieval runs, the canonical requirement stays False, and the same
invariant helper the honest test uses goes red.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.execution_requirements import (
    begin_synthesis_freeze,
    current_requirement_record,
    escalate_current_requirement,
    require_current_information_for_retrieval,
    requirements_for,
)
from core.unsourced_current_claim import inspect_unsourced_current_claim

# --- the audit set: 22 paraphrases, 13 of which the base (a2308a26) measured as misses ----

AUDIT_PARAPHRASES = (
    # prices (4) -- 2 baseline misses
    "what does bitcoin cost right now",          # hit on base (LIVE_DATA)
    "how much is gold worth today",              # hit on base (LIVE_DATA)
    "show me tesla stock price",                 # MISS on base (fast lane fired regardless)
    "how is tesla stock doing",                  # MISS on base
    # weather (4) -- 1 baseline miss
    "is it raining in vilnius right now",        # hit on base (GROUNDED)
    "weather forecast for tomorrow in berlin",   # hit on base (LIVE_DATA)
    "do i need an umbrella in riga",             # MISS on base
    "will it snow in oslo this week",            # hit on base (LIVE_DATA)
    # news (4) -- 3 baseline misses
    "show me recent news coverage about Rust",   # MISS on base (the live-proof phrase)
    "any headlines about the election today",    # MISS on base (fast lane fired regardless)
    "what happened today in ukraine",            # MISS on base (fast lane fired regardless)
    "latest on the fed rate decision",           # hit on base (GROUNDED)
    # versions (4) -- 3 baseline misses
    "what's the newest version of python",       # MISS on base (fast lane fired regardless)
    "current version of node js",                # hit on base (GROUNDED)
    "has rust released a new version recently",  # MISS on base
    "has django 6 come out yet",                 # MISS on base
    # schedules (4) -- 4 baseline misses
    "what time is the liverpool match today",    # MISS on base
    "when does the next train to warsaw leave",  # MISS on base
    "what's on tv tonight",                      # MISS on base
    "is my flight on time",                      # MISS on base
    # currency / markets (2) -- 2 baseline hits
    "what's the exchange rate from eur to usd",  # hit on base (GROUNDED)
    "how are markets doing right now",           # hit on base (GROUNDED)
)

#: The 13 rows the audit measured as misses on a2308a26, with the signal that must own
#: the escalation.  A test name, not a second classifier: the authority appends
#: ``current_info_signal:<name>`` to the frozen decision's reason codes.
BASELINE_MISSES: dict[str, str] = {
    "show me tesla stock price": "fresh_lookup_markers",
    "how is tesla stock doing": "market_status",
    "do i need an umbrella in riga": "weather_preparedness",
    "show me recent news coverage about Rust": "news_request",
    "any headlines about the election today": "news_request",
    "what happened today in ukraine": "news_request",
    "what's the newest version of python": "fresh_lookup_markers",  # or release_freshness
    "has rust released a new version recently": "release_freshness",
    "has django 6 come out yet": "release_freshness",
    "what time is the liverpool match today": "schedule_lookup",
    "when does the next train to warsaw leave": "schedule_lookup",
    "what's on tv tonight": "schedule_lookup",
    "is my flight on time": "schedule_lookup",
}

#: Signals that legitimately own the "newest version" row: the fast lane's own
#: ``latest|newest|recent x domain`` tail or the release-freshness recognizer.
_VERSION_SIGNALS = {"current_info_signal:fresh_lookup_markers", "current_info_signal:release_freshness"}

TIMELESS_AND_ORDINARY_CONTROLS = (
    "water freezes at standard pressure",
    "what is 17 times 23",
    "explain how photosynthesis works",
    "who wrote pride and prejudice",
    "hi, how are you doing today",
    "summarize the file I attached",
)


@pytest.mark.parametrize("text", AUDIT_PARAPHRASES)
def test_all_22_audit_paraphrases_require_current_information(text: str) -> None:
    requirements = requirements_for(text)

    assert requirements.current_information_required, (
        f"audit paraphrase classified as not-current: {text!r} -> {requirements.reason_codes}"
    )


@pytest.mark.parametrize("text,signal", sorted(BASELINE_MISSES.items()))
def test_every_former_miss_names_the_signal_that_escalated_it(text: str, signal: str) -> None:
    requirements = requirements_for(text)

    assert requirements.current_information_required
    if signal == "fresh_lookup_markers" and text == "what's the newest version of python":
        assert _VERSION_SIGNALS & set(requirements.reason_codes), requirements.reason_codes
    else:
        assert f"current_info_signal:{signal}" in requirements.reason_codes, requirements.reason_codes


@pytest.mark.parametrize("text", TIMELESS_AND_ORDINARY_CONTROLS)
def test_timeless_and_ordinary_controls_remain_direct(text: str) -> None:
    requirements = requirements_for(text)

    assert requirements.answer_mode == "DIRECT"
    assert requirements.current_information_required is False
    assert not [code for code in requirements.reason_codes if code.startswith("current_info_signal:")]


# --- invariant: retrieval scheduled => canonical current_information_required -------------


def _fast_lane_corpus() -> tuple[str, ...]:
    """Texts each fast-lane vocabulary claims, plus every audit paraphrase.

    Every recognizer road into ``live_info_mode`` is represented: the news markers, the
    weather recognizer, the fresh-lookup markers, the recency x domain table, the
    latest/newest x domain tail, and the explicit/public-entity lookup requests.
    """

    return (
        *AUDIT_PARAPHRASES,
        "latest news about the election",
        "breaking news in france",
        "latest telegram bot api updates",
        "release notes for postgres 17",
        "current price of tea in china",
        "eur/usd right now",
        "btc today",
        "what happened in the markets minutes ago",
        "look up the melting point of tungsten",
        "who is greta thunberg",
        "weather only for berlin and copenhagen",
        "what's the latest on the telegram api",
    )


def test_every_text_the_fast_lane_claims_is_current_required() -> None:
    """Structural form of the invariant: the authority knows every vocabulary the
    scheduler knows, so no scheduler claim can exist with a False canonical reading."""

    unclaimed = []
    violated = []
    for text in _fast_lane_corpus():
        mode = live_info_mode(None, text, interpretation=None)
        requirements = requirements_for(text)
        if mode and not requirements.current_information_required:
            violated.append((text, mode, requirements.reason_codes))
        if not mode and requirements.current_information_required:
            unclaimed.append((text, requirements.reason_codes))
    assert not violated, f"scheduler claims without canonical current requirement: {violated}"
    # Cross-check only: current-required does NOT imply this lane claims it (other lanes
    # may serve it, or the turn may honestly refuse) -- but a claimed-but-not-current
    # row is exactly the disarmed-guard defect.


def _drive_fast_path_and_assert_invariant(agent, text: str, *, interpretation) -> None:
    """Drive the REAL fast-path seam with retrieval stubbed, then enforce the invariant.

    The invariant is checked against the canonical record IN THE TURN'S OWN CONTEXT --
    the same object downstream guards read -- not a fresh reclassification.
    """

    source_context: dict[str, object] = {"session_id": "m1-invariant", "cancel_turn_id": "m1-inv-1"}
    with (
        mock.patch(
            "core.agent_runtime.fast_live_info_runtime_flow.live_info_search_notes_with_fallback",
            return_value=[{"title": "stub", "url": "https://stub.example/x", "summary": "stub note"}],
        ) as search,
        mock.patch(
            "core.agent_runtime.fast_live_info_runtime_flow.build_live_info_response_result",
            return_value={"response": "STUB_LIVE_INFO", "response_class": "utility_answer", "confidence": 0.9},
        ),
    ):
        agent._maybe_handle_live_info_fast_path(
            text,
            session_id="m1-invariant",
            source_context=source_context,
            interpretation=interpretation,
        )
        retrieval_ran = search.call_count > 0
    if not retrieval_ran:
        return
    record = current_requirement_record(source_context)
    assert record is not None, "retrieval ran but no canonical requirement record exists"
    assert record.requirements.current_information_required, (
        f"retrieval ran while canonical current_information_required=False for {text!r} "
        f"(reasons={record.requirements.reason_codes})"
    )


@pytest.mark.parametrize(
    "text",
    (
        "show me tesla stock price",               # fresh-lookup road, canonical-blind on base
        "any headlines about the election today",  # news road, canonical-blind on base
        "latest news about the election",          # news road the authority must know
    ),
)
def test_retrieval_never_runs_with_canonical_current_false(make_agent, enable_web, text: str) -> None:
    agent = make_agent()

    _drive_fast_path_and_assert_invariant(agent, text, interpretation=SimpleNamespace(topic_hints=[]))


def test_a_hint_driven_claim_escalates_the_canonical_requirement(make_agent, enable_web) -> None:
    """The hints road is the one shape the text-only authority cannot see coming: the
    lane decides from ``topic_hints`` that retrieval is wanted.  The invariant still
    holds -- because the lane's decision ESCALATES the canonical requirement through the
    door rather than deciding alone."""

    agent = make_agent()

    _drive_fast_path_and_assert_invariant(
        agent, "find good coverage of the roman republic", interpretation=SimpleNamespace(topic_hints=["web"])
    )


def test_sabotage_s1_restored_private_authority_reds_the_invariant(make_agent, enable_web) -> None:
    """SABOTAGE: restore one scheduler's private authority and the invariant must red.

    The sabotage does two things a future regression would do together: it neuters the
    canonical door (the scheduler 'decides' alone again -- the lambda returns True
    without escalating anything) and gives the lane a private vocabulary the authority
    does not know (the canary control text).  Retrieval runs, the canonical record stays
    current=False, and the same invariant helper the honest tests use raises."""

    agent = make_agent()
    canary = "who wrote pride and prejudice"  # the authority must never know this as current

    def _restored_private_authority(ctx, text, *, lane):
        # Consults the authority's record, then ignores it: retrieval proceeds on the
        # scheduler's own say-so -- no escalation, no reason code.  This is the shape of
        # the world before M1: the lane decided freshness alone.
        requirements_for(text, source_context=ctx)
        return True

    with (
        mock.patch(
            "core.execution_requirements.require_current_information_for_retrieval",
            _restored_private_authority,
        ),
        mock.patch.object(
            agent,
            "_live_info_mode",
            lambda text, interpretation: "news",  # a private vocabulary, unknown to the authority
        ),
    ):
        with pytest.raises(AssertionError, match="current_information_required=False"):
            _drive_fast_path_and_assert_invariant(
                agent, canary, interpretation=SimpleNamespace(topic_hints=[])
            )


# --- the frozen decision -------------------------------------------------------------------


def test_lane_signal_escalation_is_typed_monotone_and_recorded() -> None:
    context: dict[str, object] = {}
    baseline = requirements_for("find good coverage of the roman republic", source_context=context)
    assert baseline.current_information_required is False

    escalated = escalate_current_requirement(
        context,
        text="find good coverage of the roman republic",
        source="live_info_fast_path",
        reason_code="lane:live_info_fast_path",
    )
    assert escalated is True

    record = current_requirement_record(context)
    assert record is not None and record.requirements.current_information_required
    assert "current_info_signal:lane:live_info_fast_path" in record.requirements.reason_codes
    # The turn now READS its own frozen, escalated requirement -- every consumer sees True.
    assert requirements_for(
        "find good coverage of the roman republic", source_context=context
    ).current_information_required is True

    # Monotone: a second signal refines attribution; nothing can lower the requirement.
    assert (
        escalate_current_requirement(
            context,
            text="find good coverage of the roman republic",
            source="live_info_fast_path",
            reason_code="topic_hints:web",
        )
        is True
    )
    record = current_requirement_record(context)
    assert record is not None and record.requirements.current_information_required


def test_frozen_decision_cannot_flip_after_synthesis_begins() -> None:
    text = "thanks, that helps a lot"
    context: dict[str, object] = {}
    assert requirements_for(text, source_context=context).current_information_required is False

    # Late context arrives after the decision was read (a later stage adds hints).
    context["topic_hints"] = ["news", "web"]
    late = requirements_for(text, source_context=context)
    assert late.current_information_required is False, "late context re-opened the frozen decision"

    begin_synthesis_freeze(context, text)

    # Widening is refused after synthesis begins ...
    assert (
        escalate_current_requirement(
            context, text=text, source="late_lane", reason_code="lane:late_lane"
        )
        is False
    )
    # ... and the requirement still reads exactly as it did at decision time.
    frozen = requirements_for(text, source_context=context)
    assert frozen.current_information_required is False
    assert "current_info_signal:lane:late_lane" not in frozen.reason_codes


def test_frozen_decision_cannot_flip_down_either_after_synthesis(make_agent) -> None:
    context: dict[str, object] = {}
    text = "any headlines about the election today"
    assert requirements_for(text, source_context=context).current_information_required is True

    begin_synthesis_freeze(context, text)

    assert requirements_for(text, source_context=context).current_information_required is True


# --- the guard reads the same frozen requirement -------------------------------------------


def test_output_guard_is_armed_by_the_canonical_requirement() -> None:
    """The defect's user-visible half: on the audit's live-proof phrase, a memory-only
    answer carrying a current claim must be withdrawn because the canonical requirement
    (not the reply's wording) says the turn needed current information."""

    requirements = requirements_for("show me recent news coverage about Rust")
    verdict = inspect_unsourced_current_claim(
        answer="Rust 1.85 shipped last week; coverage at techcrunch.com says the borrow checker changed.",
        requires_current=requirements.current_information_required,
        notes=[],
        session_id="",
        turn_id="",
        source_context=None,
    )
    assert requirements.current_information_required
    assert verdict.requires_current
    assert verdict.unsupported, "memory-only current claim passed with the requirement disarmed"


def test_require_current_information_for_retrieval_is_fail_closed_after_freeze() -> None:
    context: dict[str, object] = {}
    text = "thanks, that helps a lot"
    requirements_for(text, source_context=context)
    begin_synthesis_freeze(context, text)

    assert (
        require_current_information_for_retrieval(context, text, lane="live_info_fast_path") is False
    ), "retrieval was permitted post-synthesis on a turn the authority never required current information for"
