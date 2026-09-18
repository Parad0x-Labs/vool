"""Ordinary multi-part chat and visible-transcript recall stay whole and authoritative.

The production failures had one shared symptom -- a small conversational request was treated as a
set of unrelated operations.  The conductor sent an explanation and a title into operation
adapters, while the recall fast path only knew how to recover arithmetic.  These tests keep the two
contracts distinct:

* an all-ordinary multi-part turn is one model request with a structural completeness guard;
* a direct recall question reads the namespace-bound visible transcript without a model or tool.

The corpora deliberately contain clean, connector-heavy, lettered, numeric and typo-heavy forms.
The negative controls keep workspace operations and forward-looking questions out of both lanes.
"""

from __future__ import annotations

import json
import re
from unittest import mock

import pytest

from core.agent_runtime.same_chat_recall import (
    TranscriptRecall,
    same_chat_transcript_recall_fast_path,
)
from core.context_history_authority import is_same_chat_history_recall
from core.context_retrieval import update_retrieval_telemetry
from core.execution.planner import should_attempt_tool_intent
from core.memory_first_router import ModelExecutionDecision
from core.ordinary_chat_response_guard import inspect_ordinary_chat_output, ordinary_chat_output_policy
from core.plain_task_routing import (
    is_ordinary_multi_part_plain_task,
    ordinary_multi_part_answer_complete,
    ordinary_plain_request_count,
    ordinary_plain_requests,
    plain_task_kind,
)
from core.remote_fetch_policy import note_remote_fetch_attempt
from core.user_identity_authority import assistant_display_name

REPORTED_MULTIPART = (
    "Do these separately: A) In one plain sentence, why does soap help remove grease? "
    "B) What is 46 × 19? C) Make a 6-word title for a QA note about local routing."
)
REPORTED_IDENTITY_MULTIPART = (
    "what do people call you in this app? also what is 58 × 7?"
)

# Original report, five clean paraphrases, and five user-style variants.  The expected count is
# explicit so adding a permissive "contains and => multipart" shortcut cannot satisfy the family.
MULTIPART_FAMILY = (
    (REPORTED_MULTIPART, 3),
    ("Explain why rain smells earthy; calculate 27 × 14; give a five-word QA heading.", 3),
    ("Why does wool retain warmth? Also, what is 23 × 6? Plus name a four-word note title.", 3),
    ("Tell me how brakes create friction. What is 31 times 9? Provide a brief release title.", 3),
    ("Define photosynthesis and explain evaporation, then compute 18 × 12.", 3),
    ("A) Describe why ice floats. B) Calculate 44 × 3. C) Draft a short test heading.", 3),
    ("do em separate a) why soap lifts oil? b) whats 17 x 8? c) mak a tiny qa titl", 3),
    ("1) explain static pls 2) wht is 12*13 3) gimme 4 word heading", 3),
    ("explain dew; also calc 9x16; plus make me a short titl", 3),
    ("why leaves drop and how magnets pull, plus calculate 8 * 7", 3),
    ("pls do all: a- tell why salt melts ice b- compute 15 x 11 c- name a qa note", 3),
    ("Name Peru's capital; calculate 34 × 6; rewrite 'We launched' in the future tense.", 3),
    ("Give one fact about basalt. What is 13 x 4? Rewrite 'Tests pass' as a question.", 3),
    ("3 lil things pls: say japan capital; calc 7x12; rewrit 'ship now' politely", 3),
    ("Suggest a four-word title in Sentence Case; calculate 4 × 5; explain why tests matter.", 3),
    (REPORTED_IDENTITY_MULTIPART, 2),
    ("What's your app name? also what's 14 × 5?", 2),
)

MULTIPART_NEGATIVE_CONTROLS = (
    "Search the workspace files for retry_timeout and explain each match.",
    "Read config.py; summarize it; give the result a short title.",
    "Why do salt and pepper work well together?",
)

MULTIPART_ADVERSARIAL_NEAR_MISS = (
    'The fixture says "A) Explain caching. B) Calculate 8 x 9." Critique that fixture wording.'
)
REAL_WORKSPACE_SEARCH = "search this workspace for retry_timeout"


@pytest.mark.parametrize(("prompt", "expected"), MULTIPART_FAMILY)
def test_semantic_family_stays_one_complete_ordinary_model_task(prompt: str, expected: int) -> None:
    assert ordinary_plain_request_count(prompt) == expected
    assert len(ordinary_plain_requests(prompt)) == expected
    assert is_ordinary_multi_part_plain_task(prompt) is True
    assert plain_task_kind(prompt) == "multi_part_qa"
    assert should_attempt_tool_intent(
        prompt,
        task_class="chat_conversation",
        source_context={"surface": "api", "platform": "api"},
    ) is False


@pytest.mark.parametrize("prompt", MULTIPART_NEGATIVE_CONTROLS)
def test_operational_or_single_request_controls_do_not_enter_ordinary_multipart(prompt: str) -> None:
    assert is_ordinary_multi_part_plain_task(prompt) is False


def test_quoted_multi_request_text_is_not_mistaken_for_the_users_requests() -> None:
    assert is_ordinary_multi_part_plain_task(MULTIPART_ADVERSARIAL_NEAR_MISS) is False


def test_real_workspace_search_keeps_the_tool_lane() -> None:
    assert is_ordinary_multi_part_plain_task(REAL_WORKSPACE_SEARCH) is False
    assert should_attempt_tool_intent(
        REAL_WORKSPACE_SEARCH,
        task_class="chat_conversation",
        source_context={"surface": "api", "platform": "api"},
    ) is True


def test_sabotage_removing_operational_exclusion_hijacks_a_real_workspace_request(
    monkeypatch,
) -> None:
    """Load-bearing mutation: workspace intent must invalidate the whole ordinary lane."""
    monkeypatch.setattr("core.plain_task_routing._OPERATIONAL_REQUEST_RE", re.compile(r"(?!)"))
    prompt = "Write a file in the workspace named retry notes; explain what retries mean."

    assert is_ordinary_multi_part_plain_task(prompt) is True
    assert should_attempt_tool_intent(
        prompt,
        task_class="chat_conversation",
        source_context={"surface": "api", "platform": "api"},
    ) is False


@pytest.mark.parametrize("prompt", (REPORTED_MULTIPART, REPORTED_IDENTITY_MULTIPART))
def test_conductor_and_lookup_planner_decline_the_whole_ordinary_family_before_a_planner_call(
    prompt: str,
) -> None:
    from core.agent_runtime.turn_planner import plan_turn
    from core.conductor.planner import plan_conductor_turn

    calls: list[str] = []

    def _record_planner_call(_system: str, planner_prompt: str) -> str:
        calls.append(planner_prompt)
        return "[]"

    assert plan_conductor_turn(prompt, ask_model=_record_planner_call) is None
    assert plan_turn(prompt, ask_model=_record_planner_call) == []
    assert calls == [], "an ordinary multi-part request bought a decomposition call"


def test_semantic_admission_still_blocks_tool_hijack_if_ordinary_decline_is_removed(
    monkeypatch,
) -> None:
    """The ordinary-lane decline is not the only defense against a wrong-domain real tool."""
    from core.conductor.planner import plan_conductor_turn

    monkeypatch.setattr(
        "core.plain_task_routing.is_ordinary_multi_part_plain_task",
        lambda *_args, **_kwargs: False,
    )
    planner_reply = json.dumps(
        [
            {
                "request": "why does soap help remove grease",
                "operation": "factual_explanation",
                "depends_on": [],
            },
            {
                "request": "What is 46 × 19",
                "operation": "calculation",
                "depends_on": [],
            },
            {
                "request": "Make a 6-word title for a QA note about local routing",
                "operation": "workspace_investigation",
                "depends_on": [],
            },
        ]
    )

    plan = plan_conductor_turn(REPORTED_MULTIPART, ask_model=lambda *_args: planner_reply)

    assert plan is not None
    assert "unresolved" in plan.operations
    assert "workspace_investigation" not in plan.operations
    title = next(node for node in plan.nodes if "6-word title" in node.request_text)
    assert "workspace_investigation is unavailable for this request" in title.unresolved_reason
    assert all(node.tool_intent != "workspace.search_text" for node in plan.nodes)
    assert any(node.operation == "factual_explanation" for node in plan.nodes)


def test_reported_multipart_runs_once_and_keeps_every_answer(make_agent, monkeypatch) -> None:
    answer = (
        "A) Soap surrounds grease so water can carry it away.\n"
        "B) 46 × 19 = 874.\n"
        "C) Local Routing QA Guards Every Edge"
    )
    agent = make_agent()
    monkeypatch.setattr(
        agent.memory_router,
        "_invoke_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a conductor/planner helper model ran")
        ),
    )
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="ordinary-multipart-reported",
            provider_id="ollama-local:test",
            model_name="test",
            used_model=True,
            output_text=answer,
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        REPORTED_MULTIPART,
        session_id_override="ordinary-multipart-reported",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == answer
    assert result["route_reason"] == "ordinary_plain_text_task"
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0
    assert agent.memory_router.resolve.call_count == 1


def test_identity_and_math_use_the_repository_identity_and_keep_both_parts(make_agent) -> None:
    name = assistant_display_name()
    answer = f"1. People call me {name} in this app.\n2. 58 × 7 = 406."
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="ordinary-identity-math",
            provider_id="ollama-local:test",
            model_name="test",
            used_model=True,
            output_text=answer,
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        REPORTED_IDENTITY_MULTIPART,
        session_id_override="ordinary-identity-math",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == answer
    assert name in result["response"]
    assert "406" in result["response"]
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0


@pytest.mark.parametrize(
    "answer",
    (
        "I am the local app assistant. 37 + 28 = 65.",
        "Role: local app assistant.\nSum: 37 + 28 = 65.",
        "The phrase final answer is descriptive here; my role is app assistant, and 37 + 28 is 65.",
    ),
)
def test_model_only_accounting_ignores_prior_remote_and_retrieval_state(
    make_agent,
    answer: str,
) -> None:
    """A prior turn's count and unrelated telemetry can never become this turn's web_calls."""

    for _ in range(11):
        note_remote_fetch_attempt()
    update_retrieval_telemetry(
        web_calls=267,
        retrieved_chars=267,
        selected_facts=["unrelated prior event"] * 3,
    )
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="ordinary-accounting-variant",
            provider_id="ollama-local:test",
            model_name="test",
            used_model=True,
            output_text=answer,
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        "Briefly identify your role here and add 37 + 28.",
        session_id_override=f"ordinary-accounting-{abs(hash(answer))}",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == answer
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0


def test_actual_current_turn_remote_attempt_is_counted(make_agent, enable_web, monkeypatch) -> None:
    """`web_calls` is the number of remote attempts the turn ACTUALLY made -- never fewer.

    The turn's own notes search is one attempt. The adaptive research loop behind it then fans out
    over every search engine when the sealed test network answers none of them (21 more attempts
    measured 2026-09-07: duckduckgo and bing renders, yahoo, brave, searxng, twice each), so
    pinning `1` made this a statement about the network, not about the accounting. The ledger is
    judged against what its own `note` saw: every attempt counted, the notes attempt among them.
    """
    del enable_web
    from core import remote_fetch_policy

    ledger_cls = type(remote_fetch_policy._new_remote_fetch_ledger()) if hasattr(
        remote_fetch_policy, "_new_remote_fetch_ledger"
    ) else _remote_fetch_ledger_class(remote_fetch_policy)
    original_note = ledger_cls.note
    seen: list[str] = []

    def counting_note(self, url: str = "", *args, **kwargs):
        seen.append(str(url or kwargs.get("host") or ""))
        return original_note(self, url, *args, **kwargs)

    monkeypatch.setattr(ledger_cls, "note", counting_note)
    agent = make_agent()

    def collect_current_turn_web_notes(*_args, **_kwargs):
        note_remote_fetch_attempt(host="test-notes-search")
        return [
            {
                "title": "Northstar release note",
                "url": "https://example.invalid/northstar",
                "snippet": "A current-turn web result.",
            }
        ]

    agent._live_info_search_notes = mock.Mock(  # type: ignore[method-assign]
        side_effect=collect_current_turn_web_notes
    )

    result = agent.run_once(
        "Find the latest public Northstar protocol update and explain it normally.",
        session_id_override="actual-current-web-accounting",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": True},
    )

    assert agent._live_info_search_notes.call_count == 1
    assert "test-notes-search" in seen, "the turn's own notes attempt must reach the ledger"
    assert result["web_calls"] == len(seen) >= 1


def _remote_fetch_ledger_class(module):
    for value in vars(module).values():
        if isinstance(value, type) and callable(getattr(value, "note", None)):
            return value
    raise AssertionError("no remote-fetch ledger class exposes `note`")


@pytest.mark.parametrize(
    "answer",
    (
        "1. Soap surrounds grease.\n2. 874.\n3. Local Routing QA Guards Every Edge",
        "A) Soap surrounds grease.\nB) 874.\nC) Local Routing QA Guards Every Edge",
    ),
)
def test_completeness_accepts_sequential_numeric_or_lettered_answers(answer: str) -> None:
    assert ordinary_multi_part_answer_complete(REPORTED_MULTIPART, answer) is True


def test_final_display_backstop_keeps_lettered_complete_answers_without_request_text() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="plain_task_minimal",
        output_mode="plain_text",
        user_text=REPORTED_MULTIPART,
    )
    answer = "A) Soap surrounds grease.\nB) 874.\nC) Local Routing QA Guards Every Edge"

    assert inspect_ordinary_chat_output(answer, policy).allowed is True


def test_completeness_guard_rejects_a_fluent_partial_answer() -> None:
    assert ordinary_multi_part_answer_complete(
        REPORTED_MULTIPART,
        "Soap surrounds grease so water can carry it away. 46 × 19 = 874.",
    ) is False


def test_sabotage_removing_completeness_guard_recreates_the_partial_final_answer(
    make_agent,
    monkeypatch,
) -> None:
    """Load-bearing mutation: remove the guard and the original omission becomes shippable."""
    prompt = "Explain why soap removes grease. Calculate 46 × 19. Give a short QA title."
    partial = "1. Soap surrounds grease so water can carry it away."
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="ordinary-multipart-sabotage",
            provider_id="ollama-local:test",
            model_name="test",
            used_model=True,
            output_text=partial,
            confidence=0.9,
            trust_score=0.9,
        )
    )
    intact = agent.run_once(
        prompt,
        session_id_override="ordinary-multipart-intact",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )
    assert intact["response"] != partial
    assert "complete answer to every part" in intact["response"].lower()

    monkeypatch.setattr(
        "core.plain_task_routing.ordinary_multi_part_answer_complete",
        lambda *_args, **_kwargs: True,
    )
    sabotaged = agent.run_once(
        prompt,
        session_id_override="ordinary-multipart-sabotaged",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )
    assert sabotaged["response"] == partial


BASE_HISTORY = [
    {"role": "user", "content": REPORTED_MULTIPART},
    {
        "role": "assistant",
        "content": (
            "A) Soap surrounds grease so water can carry it away.\n"
            "B) 46 × 19 = 874.\n"
            "C) Local Routing QA: Edge Case Testing"
        ),
    },
    {"role": "user", "content": "what number did you get for the multiply thing just now?"},
    {"role": "assistant", "content": "874"},
]

REPORTED_TEXT_RECALL = "and what was part C title u gave?"
TEXT_RECALL_FAMILY = (
    REPORTED_TEXT_RECALL,
    "What title did you give for part C?",
    "Remind me of the QA title from earlier.",
    "Which heading appeared in the third part above?",
    "What was the title you wrote before?",
    "Tell me the part C title from this chat.",
    "wht was the titl u gave for c",
    "wat heading did u use earlier",
    "remind me qa titl frm above pls",
    "part c titl u wrote??",
    "which headng was in 3rd part b4",
)


@pytest.mark.parametrize("prompt", TEXT_RECALL_FAMILY)
def test_text_recall_family_reads_the_authoritative_visible_transcript(prompt: str) -> None:
    assert is_same_chat_history_recall(prompt) is True
    recalled = same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": [*BASE_HISTORY, {"role": "user", "content": prompt}]},
    )

    assert recalled == TranscriptRecall(
        response="Local Routing QA: Edge Case Testing",
        recall_kind="title",
        found=True,
        route_reason="authoritative_visible_text_history",
    )


@pytest.mark.parametrize(
    ("prompt", "history", "expected", "kind"),
    (
        (
            "what number did you get for the multiply thing just now?",
            BASE_HISTORY[:2],
            "46 × 19 = 874",
            "number",
        ),
        (
            "which word did you use earlier?",
            [
                {"role": "user", "content": "Give one word for a calm launch."},
                {"role": "assistant", "content": "Steady"},
            ],
            "Steady",
            "word",
        ),
        (
            "repeat the short phrase you gave before",
            [
                {"role": "user", "content": "Give me a short phrase for release readiness."},
                {"role": "assistant", "content": "Proof before promotion"},
            ],
            "Proof before promotion",
            "phrase",
        ),
        (
            "what sentence did you write above?",
            [
                {"role": "user", "content": "Write one sentence about local trust."},
                {"role": "assistant", "content": "Local proof makes trust inspectable."},
            ],
            "Local proof makes trust inspectable.",
            "sentence",
        ),
        (
            "what name did you suggest earlier?",
            [
                {"role": "user", "content": "Suggest one name for the test harness."},
                {"role": "assistant", "content": "Boundary Lantern"},
            ],
            "Boundary Lantern",
            "name",
        ),
        (
            "what was list item 2 you gave?",
            [
                {"role": "user", "content": "List three release checks."},
                {"role": "assistant", "content": "- lint\n- regression\n- smoke"},
            ],
            "regression",
            "item",
        ),
    ),
)
def test_recall_covers_numbers_words_phrases_sentences_names_and_list_items(
    prompt: str,
    history: list[dict[str, str]],
    expected: str,
    kind: str,
) -> None:
    recalled = same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": [*history, {"role": "user", "content": prompt}]},
    )

    assert recalled is not None
    assert recalled.response == expected
    assert recalled.recall_kind == kind
    assert recalled.found is True


@pytest.mark.parametrize(
    "prompt",
    (
        "what title did you give earlier?",
        "which name did you suggest above?",
        "what was list item 4 you gave?",
    ),
)
def test_missing_transcript_content_gets_an_authoritative_absence_not_a_guess(prompt: str) -> None:
    history = [
        {"role": "user", "content": "Explain why leaves change color."},
        {"role": "assistant", "content": "Chlorophyll fades and reveals other pigments."},
        {"role": "user", "content": prompt},
    ]
    recalled = same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": history},
    )

    assert recalled is not None
    assert recalled.found is False
    assert "does not appear" in recalled.response
    assert "visible history" in recalled.response


@pytest.mark.parametrize(
    "prompt",
    (
        "What title should I give this QA note?",
        'The fixture says "what title did you give earlier?" Classify that sentence.',
        "What title did Toni Morrison give her novel?",
    ),
)
def test_non_recall_negative_controls_do_not_gain_transcript_authority(prompt: str) -> None:
    assert is_same_chat_history_recall(prompt) is False
    assert same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": BASE_HISTORY},
    ) is None


def test_ambiguous_earlier_reference_to_an_outside_person_stays_out_of_direct_recall() -> None:
    prompt = "What title did Toni Morrison give her novel earlier?"

    # The broader history-expansion hint may admit context for model reasoning, but the
    # deterministic value copier must not replace an outside-world question with stale chat text.
    assert is_same_chat_history_recall(prompt) is True
    assert same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": BASE_HISTORY},
    ) is None


def test_sabotage_removing_recall_intent_gate_turns_a_new_title_request_into_stale_recall(
    monkeypatch,
) -> None:
    """Load-bearing mutation: prior text must not hijack a forward-looking writing request."""
    monkeypatch.setattr(
        "core.agent_runtime.same_chat_recall.is_same_chat_history_recall",
        lambda *_args, **_kwargs: True,
    )

    recalled = same_chat_transcript_recall_fast_path(
        "What title should I give this QA note?",
        source_surface="api",
        source_context={"conversation_history": BASE_HISTORY},
    )

    assert recalled is not None
    assert recalled.response == "Local Routing QA: Edge Case Testing"


def test_adversarial_part_reference_without_chat_recall_does_not_extract_an_answer() -> None:
    prompt = "What was part C about in the novel?"

    assert is_same_chat_history_recall(prompt) is False
    assert same_chat_transcript_recall_fast_path(
        prompt,
        source_surface="api",
        source_context={"conversation_history": BASE_HISTORY},
    ) is None


def test_text_recall_frontdoor_skips_models_memory_search_and_tools(make_agent) -> None:
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("direct transcript recall loaded context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        side_effect=AssertionError("direct transcript recall reached a provider")
    )

    result = agent.run_once(
        REPORTED_TEXT_RECALL,
        session_id_override="ordinary-text-recall",
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "conversation_history": [
                *BASE_HISTORY,
                {"role": "user", "content": REPORTED_TEXT_RECALL},
            ],
        },
    )

    assert result["response"] == "Local Routing QA: Edge Case Testing"
    assert result["route"] == "same_chat_history"
    assert result["route_reason"] == "authoritative_visible_text_history"
    assert result["route_skips"] == ["model", "capsule", "web", "tool_loop"]
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0


def test_missing_title_frontdoor_returns_visible_absence_without_model_guess(make_agent) -> None:
    prompt = "what title did you give earlier?"
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("absence recall loaded context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        side_effect=AssertionError("absence recall asked a model to invent transcript content")
    )

    result = agent.run_once(
        prompt,
        session_id_override="ordinary-missing-title-recall",
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "conversation_history": [
                {"role": "user", "content": "Explain why leaves change color."},
                {
                    "role": "assistant",
                    "content": "Chlorophyll fades and reveals other pigments.",
                },
                {"role": "user", "content": prompt},
            ],
        },
    )

    assert result["response"] == "That title does not appear in the visible history of this chat."
    assert result["route"] == "same_chat_history"
    assert result["route_reason"] == "authoritative_visible_history_absence"
    assert result["recall_found"] is False
    assert result["model_calls"] == 0


@pytest.mark.parametrize(
    "prompt",
    (
        "what number did you get for the multiply thing just now?",
        REPORTED_TEXT_RECALL,
    ),
)
def test_sabotage_removing_generic_transcript_recall_breaks_numeric_and_text_cases(
    make_agent,
    monkeypatch,
    prompt: str,
) -> None:
    """Load-bearing mutation: without the generic guard both cases fall into model/context work."""
    agent = make_agent()
    monkeypatch.setattr(agent, "_same_chat_transcript_recall_fast_path", lambda *_a, **_k: None)
    agent.context_loader.load.side_effect = AssertionError("recall guard removed")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("recall guard removed"))  # type: ignore[assignment]

    with pytest.raises(AssertionError, match="recall guard removed"):
        agent.run_once(
            prompt,
            session_id_override=f"ordinary-recall-sabotage-{len(prompt)}",
            source_context={
                "surface": "api",
                "platform": "api",
                "allow_remote_fetch": False,
                "conversation_history": [*BASE_HISTORY, {"role": "user", "content": prompt}],
            },
        )
