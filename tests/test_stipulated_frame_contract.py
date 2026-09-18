from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.agent_runtime.fast_live_info_runtime_preflight import prepare_live_info_request
from core.curiosity_gate import should_skip_curiosity_for_local_answer
from core.execution.planner import should_attempt_tool_intent
from core.execution_requirements import requirements_for
from core.stipulated_frame import (
    explicitly_compares_frame_to_reality,
    has_stipulated_frame,
    stipulated_frame_active,
    stipulated_frame_forbids_retrieval,
)
from core.task_router import (
    classify,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
)


class _Agent:
    @staticmethod
    def _looks_like_builder_request(_text: str) -> bool:
        return False

    @staticmethod
    def _wants_fresh_info(_text: str, *, interpretation: object) -> bool:
        return True

    @staticmethod
    def _live_info_mode(user_input: str, *, interpretation: object) -> str:
        return live_info_mode(_Agent(), user_input, interpretation=interpretation)

    @staticmethod
    def _recover_price_lookup_query(
        _user_input: str,
        *,
        source_context: dict[str, object] | None,
    ) -> str:
        return ""

    @staticmethod
    def _normalize_live_info_query(user_input: str, *, mode: str) -> str:
        return user_input

    @staticmethod
    def _requires_ultra_fresh_insufficient_evidence(_user_input: str) -> bool:
        return False


REAL_LOOKUPS_WITH_WEAK_SETUP = (
    "Suppose I migrate soon. Look up the latest Postgres release notes online.",
    "Assume I deploy Postgres next month. Search the latest Postgres release notes online.",
    "Imagine I move to Tokyo. Check online for today's JPY/USD exchange rate.",
    "Suppose we switch cloud providers. Browse the newest AWS pricing online.",
    "Assume I'm visiting London. Look up the current weather in London.",
    "Suppose I rebalance next week. Search online for the current BTC price.",
    "Assume I upgrade soon. Look up the latest Node release and summarize it.",
)

FICTIONAL_LOOKUP_TARGETS = (
    "In this fictional future, Buster is president. Search online and tell me who the president is.",
    "In this future, Buster is president. Search the current president online.",
    "Pretend Buster runs the Moon. Search the latest news about Buster online.",
    "Suppose Buster is president. Search the current president online.",
    "Imagine Luna rules Mars. Look up the latest information about Mars online.",
    "In a fictional timeline Acme launches Zeta. Search the latest Zeta release notes.",
    "Assume the currency is Baguettes. Check the current Baguette exchange rate online.",
    "Let's say Orion is Neptune's capital. Browse the latest Orion travel alerts online.",
)

EXPLANATION_ONLY_SEARCH_MISMATCHES = (
    "Assume today is Jan 1 2035. Based only on that premise, explain why a web search would fail to verify it.",
    "Suppose Buster is president. Why can't an online lookup confirm that premise?",
    "Imagine Luna governs Mars. Clarify why internet search cannot establish this scenario.",
    "Pretend Zeta shipped in 2040. Describe why search tools would not prove that claim.",
    "Suppose Buster is president. Explain why that premise cannot be verified online.",
)


@pytest.mark.parametrize(
    "prompt",
    [
        "Assume today is Jan 1 2035 and I am meeting President Buster. Who am I meeting?",
        "Imagine it is 2040 and President Buster sells me a watch in Washington, DC. What currency is likely?",
        "In this fictional future, Buster is president. Search online and tell me who the president is.",
        "Under this stipulated timeline, the sale happens in DC. Give the likely currency.",
    ],
)
def test_stipulated_frames_hard_disable_all_retrieval_gates(prompt: str) -> None:
    interpretation = SimpleNamespace(topic_hints=["web", "news"])

    assert has_stipulated_frame(prompt)
    assert stipulated_frame_forbids_retrieval(prompt)
    assert live_info_mode(_Agent(), prompt, interpretation=interpretation) == ""
    assert should_skip_curiosity_for_local_answer(prompt)
    assert not looks_like_explicit_lookup_request(prompt)
    assert not looks_like_public_entity_lookup_request(prompt)

    requirements = requirements_for(prompt, task_class="research")
    assert requirements.answer_mode == "DIRECT"
    assert requirements.tools_required is False
    assert requirements.external_evidence_required is False
    assert requirements.reason_codes == ("user_stipulated_frame",)


@pytest.mark.parametrize("prompt", REAL_LOOKUPS_WITH_WEAK_SETUP)
def test_independent_fresh_lookup_wins_over_a_weak_personal_setup(prompt: str) -> None:
    assert has_stipulated_frame(prompt), "the setup remains available to prompt grounding"
    assert not stipulated_frame_forbids_retrieval(prompt)
    assert live_info_mode(_Agent(), prompt, interpretation=SimpleNamespace(topic_hints=[]))
    assert classify(prompt, {"chat_surface": True})["task_class"] == "research"

    requirements = requirements_for(prompt, task_class="research")
    assert requirements.reason_codes != ("user_stipulated_frame",)
    assert should_attempt_tool_intent(
        prompt,
        task_class="research",
        source_context={"surface": "api", "platform": "api"},
    )


@pytest.mark.parametrize("prompt", FICTIONAL_LOOKUP_TARGETS)
def test_fictional_lookup_target_stays_disabled_across_every_consumer(prompt: str) -> None:
    assert has_stipulated_frame(prompt)
    assert stipulated_frame_forbids_retrieval(prompt)
    assert live_info_mode(_Agent(), prompt, interpretation=SimpleNamespace(topic_hints=["web"])) == ""
    assert classify(prompt, {"chat_surface": True})["task_class"] != "research"
    assert not should_attempt_tool_intent(
        prompt,
        task_class="research",
        source_context={"surface": "api", "platform": "api"},
    )

    live_mode, query, result = prepare_live_info_request(
        _Agent(),
        prompt,
        session_id="fictional-target",
        source_context={},
        interpretation=SimpleNamespace(topic_hints=["web"]),
    )
    assert (live_mode, query, result) == ("", "", None)


@pytest.mark.parametrize("prompt", EXPLANATION_ONLY_SEARCH_MISMATCHES)
def test_explaining_search_mismatch_is_not_a_retrieval_request(prompt: str) -> None:
    assert stipulated_frame_forbids_retrieval(prompt)
    assert classify(prompt, {"chat_surface": True})["task_class"] != "research"
    assert prepare_live_info_request(
        _Agent(),
        prompt,
        session_id="explanation-only",
        source_context={},
        interpretation=SimpleNamespace(topic_hints=["web"]),
    ) == ("", "", None)


@pytest.mark.parametrize(
    "prompt",
    [
        "Search online for the latest Postgres release notes.",
        "What is the current BTC price right now?",
        "Browse the newest AWS pricing online.",
        "Check online for today's EUR/USD exchange rate.",
    ],
)
def test_normal_live_lookup_still_runs_without_a_frame(prompt: str) -> None:
    assert not has_stipulated_frame(prompt)
    assert not stipulated_frame_forbids_retrieval(prompt)
    assert classify(prompt, {"chat_surface": True})["task_class"] == "research"
    assert should_attempt_tool_intent(
        prompt,
        task_class="research",
        source_context={"surface": "api", "platform": "api"},
    )


def test_negated_explanation_near_miss_keeps_the_actual_lookup() -> None:
    prompt = "Do not explain what a search engine is; search latest Postgres release notes"

    assert not has_stipulated_frame(prompt)
    assert not stipulated_frame_forbids_retrieval(prompt)
    assert looks_like_explicit_lookup_request(prompt)
    assert classify(prompt, {"chat_surface": True})["task_class"] == "research"
    assert should_attempt_tool_intent(
        prompt,
        task_class="research",
        source_context={"surface": "api", "platform": "api"},
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "SUPPOSE I MIGRATE SOON; LOOK UP THE LATEST POSTGRES RELEASE NOTES ONLINE",
        "Suppose I migrate soon,\nlook up the latest Postgres release notes online.",
        "In this FICTIONAL future, Buster is president; SEARCH ONLINE and tell me who the president is.",
        "In this fictional future, Buster is president.\nSearch online and tell me who the president is.",
    ],
)
def test_retrieval_scope_survives_case_and_clause_boundary_mutations(prompt: str) -> None:
    forbidden = stipulated_frame_forbids_retrieval(prompt)
    assert forbidden is ("buster" in prompt.lower())


def test_explicit_reality_comparison_is_the_only_retrieval_escape_hatch() -> None:
    prompt = (
        "Assume it is 2040 and Buster is president. Compare that fictional scenario "
        "with current real-world facts and cite sources."
    )

    assert explicitly_compares_frame_to_reality(prompt)
    assert not stipulated_frame_forbids_retrieval(prompt)
    requirements = requirements_for(prompt, task_class="research")
    assert requirements.tools_required is True
    assert requirements.external_evidence_required is True


def test_immediate_followup_keeps_the_stipulated_buster_frame_without_search() -> None:
    history = [
        {
            "role": "user",
            "content": "Assume it is 2040 and the president is Buster. I have a meeting with him.",
        },
        {"role": "assistant", "content": "Understood."},
    ]
    prompt = "Who am I meeting?"
    source_context = {"conversation_history": history}

    assert stipulated_frame_active(prompt, source_context=source_context)
    requirements = requirements_for(
        prompt,
        task_class="research",
        source_context=source_context,
    )
    assert requirements.answer_mode == "DIRECT"
    assert requirements.tools_required is False

    live_mode, query, result = prepare_live_info_request(
        _Agent(),
        prompt,
        session_id="stipulated-followup",
        source_context=source_context,
        interpretation=SimpleNamespace(topic_hints=["web"]),
    )
    assert (live_mode, query, result) == ("", "", None)


def test_frame_does_not_leak_past_a_newer_unrelated_user_turn() -> None:
    source_context = {
        "conversation_history": [
            {"role": "user", "content": "Assume it is 2040 and Buster is president."},
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": "Write a haiku about rain."},
            {"role": "assistant", "content": "Soft rain taps the glass."},
        ]
    }

    assert not stipulated_frame_active(
        "Who is the current president?",
        source_context=source_context,
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Who is the current president?",
        "Search online for the current USD exchange rate.",
        "Compare USD and EUR inside this hypothetical economy.",
    ],
)
def test_non_stipulated_and_in_frame_comparisons_do_not_open_the_escape_hatch(prompt: str) -> None:
    if has_stipulated_frame(prompt):
        assert stipulated_frame_forbids_retrieval(prompt)
        assert not explicitly_compares_frame_to_reality(prompt)
    else:
        assert not stipulated_frame_forbids_retrieval(prompt)
