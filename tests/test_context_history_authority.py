from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from adapters.openai_compatible_adapter import _request_messages_with_memory
from core.bootstrap_context import canonical_runtime_transcript
from core.context_history_authority import (
    assistant_text_is_runtime_failure_notice,
    enforce_history_budget,
    select_history_policy,
)
from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state
from core.human_input_adapter import adapt_user_input
from core.memory_first_router import MemoryFirstRouter
from core.persistent_memory import append_conversation_event
from core.prompt_normalizer import _history_messages_for_chat, normalize_prompt
from core.web.api.runtime import extract_user_message, normalize_chat_history

PRONOUN_FOLLOWUPS = [
    "Why does it matter?",
    "How does that work?",
    "Can you explain it?",
    "What caused that?",
    "Does it have a downside?",
    "Can we improve it?",
    "What should I do with that?",
    "Could you compare it?",
    "Is that safe?",
    "When would I use it?",
    "Who owns that?",
    "Where does it live?",
    "What breaks if we remove it?",
    "Can that be simplified?",
    "Why did you choose that?",
    "Would it scale?",
    "Can you verify it?",
    "What is the risk in that?",
    "How would you test it?",
    "Can it be faster?",
]

ELLIPTICAL_FOLLOWUPS = [
    "Why?",
    "How?",
    "More.",
    "And then?",
    "So?",
    "Really?",
    "Any risks?",
    "What next?",
    "Go deeper.",
    "Keep going.",
    "One example?",
    "In practice?",
    "Shorter.",
    "Be specific.",
    "What else?",
    "And the downside?",
    "Then what?",
    "How exactly?",
    "More detail.",
    "One more thing?",
]

EXPLICIT_HISTORY_FOLLOWUPS = [
    "What was my previous question?",
    "What did I ask before?",
    "Use the previous result.",
    "Use your last answer.",
    "Continue from the previous response.",
    "Return to the point you just made.",
    "Summarize your last answer.",
    "Compare this with what you said before.",
    "Which option did you recommend earlier?",
    "Repeat the reason you gave.",
    "Build on the previous explanation.",
    "Use the result from the last turn.",
    "What was the last constraint I gave?",
    "Continue the answer above.",
    "Refer back to your previous conclusion.",
    "What did we decide in the prior exchange?",
]

TOOL_FOLLOWUPS = [
    "Now edit it.",
    "Run the tests.",
    "Open that file.",
    "Revert that change.",
    "Try again.",
    "Now inspect it.",
    "Apply that patch.",
    "Run the linter.",
    "Read the file back.",
    "Fix that failure.",
    "Execute the same check.",
    "Open the result.",
    "Retry the command.",
    "Verify that change.",
    "Test it end to end.",
    "Undo the last edit.",
]

QUOTED_LITERAL_CASES = [
    'The evidence says "continue that investigation".',
    'Review this JSON: {"instruction":"continue","topic":"database indexes"}.',
    "The witness wrote 'use the previous result' in the report.",
    "Do not execute `run the tests`; explain the sentence.",
    "> Now edit it.\nAssess whether this is a quotation.",
    "```json\n{\"action\": \"try again\"}\n```\nDescribe this payload.",
    "```sh\nopen that file\n```\nExplain the shell sample.",
    "The literal command is `revert that change`.",
    'Compare the strings "go on" and "stop".',
    "The fixture contains {'message': 'what was my previous question?'}.",
    "~~~text\nUse the previous result.\n~~~\nClassify the snippet.",
    'A log line printed "explain database indexes"; summarize the log line.',
]

INDEPENDENT_TOPIC_SHIFTS = [
    "Explain photosynthesis.",
    "Explain why tides change.",
    "Explain how sourdough rises.",
    "Explain the phases of the moon.",
    "Explain why leaves change color.",
    "Explain how violins produce sound.",
    "Use watercolor to paint a lighthouse.",
    "Use rosemary in a potato recipe.",
    "Use a compass to find north.",
    "Use linen for summer clothing.",
    "Use clay to make a small bowl.",
    "Use rhythm to improve the chorus.",
    "Write a poem about winter birds.",
    "Write a toast for a wedding.",
    "Write a bedtime story about a fox.",
    "Write a melody in a minor key.",
    "Write a note thanking a neighbor.",
    "Write a recipe for apple crumble.",
    "Run a marathon training plan.",
    "Run through a breathing exercise.",
    "Run a rehearsal for the school play.",
    "Run intervals for cycling fitness.",
    "Run a checklist for packing luggage.",
    "Run through the rules of chess.",
    "Make a paper airplane.",
    "Make a vegetable soup.",
    "Make a playlist for a road trip.",
    "Make a sketch of a mountain cabin.",
    "Make a plan for learning Italian.",
    "Make a bouquet from wildflowers.",
    "Describe coral reef ecosystems.",
    "Compare jazz and classical harmony.",
    "Why do cats purr?",
    "How do migratory birds navigate?",
    "What causes a rainbow?",
    "When is the best time to plant tulips?",
]

HOSTILE_CONTEXT_CORPUS = [
    *[("pronoun", text) for text in PRONOUN_FOLLOWUPS],
    *[("elliptical", text) for text in ELLIPTICAL_FOLLOWUPS],
    *[("explicit_history", text) for text in EXPLICIT_HISTORY_FOLLOWUPS],
    *[("tool", text) for text in TOOL_FOLLOWUPS],
    *[("quoted_literal", text) for text in QUOTED_LITERAL_CASES],
    *[("independent_shift", text) for text in INDEPENDENT_TOPIC_SHIFTS],
]

assert len(HOSTILE_CONTEXT_CORPUS) == 120


class _ContextResult:
    report = SimpleNamespace(to_dict=lambda: {})

    @staticmethod
    def assembled_context(*, prompt_profile: str = "default") -> str:
        del prompt_profile
        return ""


def _history(current: str) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "ANCIENT-DATABASE-TOPIC-001"},
        {"role": "assistant", "content": "ANCIENT-DATABASE-ANSWER-001"},
        {"role": "user", "content": "LATEST-USER-QUESTION-882"},
        {"role": "assistant", "content": "LATEST-ASSISTANT-ANSWER-882"},
        {"role": "user", "content": current},
    ]


@pytest.mark.parametrize(
    ("category", "current"),
    HOSTILE_CONTEXT_CORPUS,
    ids=[f"{index + 1:03d}-{category}" for index, (category, _text) in enumerate(HOSTILE_CONTEXT_CORPUS)],
)
def test_120_case_corpus_preserves_adjacency_without_false_broad_expansion(
    category: str,
    current: str,
) -> None:
    history_session_id = f"corpus-history:{uuid4().hex}"
    ensure_chat_namespace(history_session_id)
    messages, _source = _history_messages_for_chat(
        {
            "surface": "api",
            "platform": "api",
            "client_conversation_history": _history(current),
        },
        runtime_session_id=history_session_id,
        current_user_text=current,
        current_user_raw_text=current,
        prompt_profile="chat_minimal",
        # Causal false-negative mutation: every ordinary turn still receives adjacency.
        expansion_hint=False,
    )

    assert [(message.role, message.content) for message in messages] == [
        ("user", "LATEST-USER-QUESTION-882"),
        ("assistant", "LATEST-ASSISTANT-ANSWER-882"),
    ]
    assert all("ANCIENT-DATABASE" not in message.content for message in messages)

    if category in {"quoted_literal", "independent_shift"}:
        session_id = f"corpus:{uuid4().hex}"
        adapt_user_input("Explain database indexes.", session_id=session_id)
        append_conversation_event(
            session_id=session_id,
            user_input="Explain database indexes.",
            assistant_output="Database indexes speed selected lookups.",
            source_context={},
        )
        interpretation = adapt_user_input(current, session_id=session_id)
        assert interpretation.is_continuation is False


@pytest.mark.parametrize(
    "current",
    [
        "Now edit it.",
        "Run the tests.",
        "Open that file.",
        "Revert that change.",
        "Try again.",
    ],
)
def test_scoped_tool_followups_request_bounded_expansion(current: str) -> None:
    session_id = f"tool-followup:{uuid4().hex}"
    adapt_user_input("Inspect the migration failure.", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Inspect the migration failure.",
        assistant_output="The migration failed in accounts.sql.",
        source_context={},
    )

    assert adapt_user_input(current, session_id=session_id).is_continuation is True


def test_scope_authority_and_typed_one_shot_are_explicit_zero_history_cases() -> None:
    denied = select_history_policy(
        scope_allows_transcript=False,
        expansion_hint=True,
        authority_reason="chat_namespace_archived",
    )
    assert denied.transcript_allowed is False
    assert denied.max_messages == 0

    session_id = f"archived-history:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="ARCHIVED-USER-MARKER-421",
        assistant_output="ARCHIVED-ASSISTANT-MARKER-421",
        source_context={},
    )
    set_chat_namespace_state(session_id, "archived")
    transcript, source = canonical_runtime_transcript(
        session_id=session_id,
        source_context={
            "client_conversation_history": [
                {"role": "user", "content": "FORGED-ARCHIVED-USER-421"},
                {"role": "assistant", "content": "FORGED-ARCHIVED-ANSWER-421"},
            ],
        },
        current_user_text="Can this be recalled?",
        expansion_hint=True,
    )
    assert transcript == []
    assert source == "scope_denied"

    messages, source = _history_messages_for_chat(
        {
            "client_conversation_history": _history("Create the requested file."),
        },
        runtime_session_id="",
        current_user_text="Create the requested file.",
        prompt_profile="plain_task_minimal",
        expansion_hint=True,
    )
    assert messages == []
    assert source == "plain_task_no_history"

    stateless, source = canonical_runtime_transcript(
        session_id=None,
        source_context={"client_conversation_history": _history("Stateless turn")},
        current_user_text="Stateless turn",
        expansion_hint=True,
    )
    assert stateless == []
    assert source == "scope_denied"


@pytest.mark.parametrize("scope_case", ["archived", "foreign"])
def test_chat_exact_uses_canonical_scope_and_reports_denial(scope_case: str) -> None:
    requested_chat = f"exact-scope:{scope_case}:{uuid4().hex}"
    source_chat = requested_chat
    ensure_chat_namespace(requested_chat, project_id="project-a")
    if scope_case == "archived":
        set_chat_namespace_state(requested_chat, "archived")
    else:
        source_chat = f"exact-foreign:{uuid4().hex}"
        ensure_chat_namespace(source_chat, project_id="project-b")

    current = "Reply with exactly: SCOPE-CHECK-OK and nothing else"
    request = normalize_prompt(
        task=SimpleNamespace(task_id="exact-scope", task_summary=current),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(
            raw_text=current,
            normalized_text=current,
            reconstructed_text=current,
            intent_mode="request",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
            is_continuation=True,
            state_mutation=None,
            turn_id="turn-exact-current",
        ),
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="exact-scope",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": requested_chat,
            "chat_id": source_chat,
            "client_conversation_history": [
                {"role": "user", "content": "UNAUTHORIZED-EXACT-USER-419"},
                {"role": "assistant", "content": "UNAUTHORIZED-EXACT-ANSWER-419"},
            ],
        },
    )

    truth = request.metadata["chat_truth_prompt"]
    assert truth["system_prompt_profile"] == "chat_exact"
    assert truth["history_messages"] == 0
    assert truth["transcript_source"] == "scope_denied"
    assert truth["history_authority"] == "scope_denied"
    assert truth["history_expansion"] == "history_not_admitted"
    assert "UNAUTHORIZED-EXACT" not in "\n".join(
        message.content for message in request.messages
    )


def test_namespaceless_foreign_project_chat_exact_fails_closed() -> None:
    session_id = f"exact-namespaceless:{uuid4().hex}"
    current = "Reply with exactly: SCOPE-CHECK-OK and nothing else"
    request = normalize_prompt(
        task=SimpleNamespace(task_id="exact-namespaceless", task_summary=current),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(
            raw_text=current,
            normalized_text=current,
            reconstructed_text=current,
            intent_mode="request",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
            is_continuation=True,
            state_mutation=None,
            turn_id="turn-exact-namespaceless-current",
        ),
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="exact-namespaceless",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": session_id,
            "chat_id": session_id,
            "_trusted_project_id": "foreign-project",
            "client_conversation_history": [
                {"role": "user", "content": "UNAUTHORIZED-NAMESPACELESS-USER-731"},
                {"role": "assistant", "content": "UNAUTHORIZED-NAMESPACELESS-ANSWER-731"},
            ],
        },
    )

    truth = request.metadata["chat_truth_prompt"]
    assert truth["system_prompt_profile"] == "chat_exact"
    assert truth["history_messages"] == 0
    assert truth["transcript_source"] == "scope_denied"
    assert truth["history_authority"] == "scope_denied"
    assert truth["history_expansion"] == "history_not_admitted"
    assert "UNAUTHORIZED-NAMESPACELESS" not in "\n".join(
        message.content for message in request.messages
    )


def test_final_history_budget_reapplies_after_oversized_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = f"compressed-bound:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    raw_history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"short-{index}",
        }
        for index in range(22)
    ]
    compressed = [
        {"role": "system", "content": "<context_summary>" + ("S" * 6000)},
        *raw_history[-8:],
    ]
    monkeypatch.setattr(
        "core.bootstrap_context.compress_if_needed",
        lambda *args, **kwargs: (compressed, True),
    )

    transcript, _source = canonical_runtime_transcript(
        session_id=session_id,
        source_context={"client_conversation_history": raw_history},
        current_user_text="Continue.",
        expansion_hint=True,
    )

    assert len(transcript) <= 10
    assert sum(len(item["content"]) for item in transcript) <= 5000
    assert [(item["role"], item["content"]) for item in transcript[-2:]] == [
        (raw_history[-2]["role"], raw_history[-2]["content"]),
        (raw_history[-1]["role"], raw_history[-1]["content"]),
    ]
    assert not any("<context_summary>" in item["content"] for item in transcript)


def test_final_history_budget_resolves_message_and_character_conflicts_by_whole_pair() -> None:
    candidates = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"pair-{index}-" + ("x" * 700),
        }
        for index in range(12)
    ]

    selected = enforce_history_budget(candidates, max_messages=10, max_chars=5000)

    assert len(selected) <= 10
    assert sum(len(item["content"]) for item in selected) <= 5000
    assert len(selected) % 2 == 0
    assert selected[-2:] == candidates[-2:]
    assert selected[0]["role"] == "user"
    assert selected[-1]["role"] == "assistant"


def test_final_history_budget_counts_unicode_characters_not_utf8_bytes() -> None:
    pair = [
        {"role": "user", "content": "🧪" * 2000},
        {"role": "assistant", "content": "🌍" * 2000},
    ]

    selected = enforce_history_budget(pair, max_messages=10, max_chars=5000)

    assert selected == pair
    assert sum(len(item["content"]) for item in selected) == 4000
    assert sum(len(item["content"].encode("utf-8")) for item in selected) > 5000


def test_final_history_budget_drops_an_oversized_exchange_without_slicing_it() -> None:
    pair = [
        {"role": "user", "content": "u" * 3000},
        {"role": "assistant", "content": "a" * 3000},
    ]

    assert enforce_history_budget(pair, max_messages=10, max_chars=5000) == []


def test_final_history_budget_clamps_a_larger_request_to_the_default_envelope() -> None:
    # The measured defect (2026-09-17): the prompt normalizer sized the continuation budget to
    # the carried artifact and passed max_chars=40000, but the authority clamped it right back to
    # HISTORY_MAX_CHARS -- the 19,764-char task board was dropped whole and the model truthfully
    # answered six consecutive paid turns with "I don't have access to the previous agent's code".
    items = [
        {"role": "user", "content": "build the board"},
        {"role": "assistant", "content": "<html>" + "b" * 19_757},
        {"role": "user", "content": "continue the previous implementation"},
        {"role": "assistant", "content": "short refusal"},
    ]

    selected = enforce_history_budget(items, max_messages=10, max_chars=40_000)

    assert sum(len(item["content"]) for item in selected) <= 5000
    assert selected == items[-2:]


def test_final_history_budget_carries_a_referenced_artifact_on_a_continuation_turn() -> None:
    items = [
        {"role": "user", "content": "build the board"},
        {"role": "assistant", "content": "<html>" + "b" * 19_757},
        {"role": "user", "content": "continue the previous implementation"},
        {"role": "assistant", "content": "short refusal"},
    ]

    selected = enforce_history_budget(
        items, max_messages=10, max_chars=24_000, continuation_carry=True
    )

    assert selected == items


def test_continuation_carry_ceiling_stays_bounded() -> None:
    items = [
        {"role": "user", "content": "build the board"},
        {"role": "assistant", "content": "<html>" + "b" * 44_970},
        {"role": "user", "content": "continue the previous implementation"},
        {"role": "assistant", "content": "short refusal"},
    ]

    selected = enforce_history_budget(
        items, max_messages=10, max_chars=10_000_000, continuation_carry=True
    )

    # The exception lifts the envelope for a carried artifact, never to an unbounded prompt:
    # a single unit larger than the 40,000-char carry ceiling still drops whole, exactly as
    # an over-sized unit drops whole under the default envelope.
    assert selected == items[-2:]
    assert sum(len(item["content"]) for item in selected) <= 40_000


def test_a_carried_continuation_artifact_names_itself_as_earlier_output() -> None:
    # Measured live (2026-09-17): with the artifact riding as a bare assistant row, the
    # answering model read it as its own prior message and truthfully answered that "the
    # previous agent's version" was missing -- the cross-provider handoff had no readable
    # referent. The carried artifact must name itself as the earlier output this turn
    # continues.
    session_id = f"continuation-provenance:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    from storage.dialogue_memory import record_dialogue_turn

    artifact = "```html\n" + ("b" * 6_600) + "\n```"
    record_dialogue_turn(
        session_id,
        raw_input="build me the task board",
        normalized_input="build me the task board",
        reconstructed_input="build me the task board",
        speaker_role="user",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )
    record_dialogue_turn(
        session_id,
        raw_input=artifact,
        normalized_input=artifact,
        reconstructed_input=artifact,
        speaker_role="assistant",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )
    current = "Continue from the version the previous agent just produced. Review it for bugs."

    messages, source = _history_messages_for_chat(
        {"surface": "api", "platform": "api"},
        runtime_session_id=session_id,
        current_user_text=current,
        current_user_raw_text=current,
        prompt_profile="chat_minimal",
        expansion_hint=False,
    )

    assert source.endswith("+continuation_artifact")
    carried = [message for message in messages if "```html" in message.content]
    assert carried, "the referenced artifact must ride the history"
    assert carried[-1].role == "assistant"
    assert carried[-1].content.startswith(
        "Earlier assistant output from this chat, carried whole because this turn continues it:"
    )
    assert artifact in carried[-1].content


@pytest.mark.parametrize(
    ("source_type", "marker"),
    [
        ("tool_observation", "CAUSALLY-BOUND-TOOL-RECEIPT"),
        ("session_summary", "RELEVANT-SESSION-SUMMARY"),
    ],
)
def test_final_provider_history_stays_bounded_after_prior_turn_context_insertion(
    source_type: str,
    marker: str,
) -> None:
    session_id = f"tool-context-bound:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    current = "Run the tests."
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"history-{index}-" + ("h" * 430),
        }
        for index in range(10)
    ]

    context_result = SimpleNamespace(
        report=SimpleNamespace(to_dict=lambda: {}),
        bootstrap_items=[],
        relevant_items=[SimpleNamespace(source_type=source_type)],
        cold_items=[],
        assembled_context=lambda **_kwargs: marker + "\n" + ("r" * 1900),
    )

    request = normalize_prompt(
        task=SimpleNamespace(task_id="tool-bound", task_summary=current),
        classification={"task_class": "debugging", "risk_flags": []},
        interpretation=SimpleNamespace(
            raw_text=current,
            normalized_text=current,
            reconstructed_text=current,
            intent_mode="request",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
            is_continuation=True,
            state_mutation=None,
            turn_id="turn-tool-bound-current",
        ),
        context_result=context_result,
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="tool-bound",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": session_id,
            "chat_id": session_id,
            "client_conversation_history": [
                *history,
                {
                    "role": "user",
                    "content": current,
                    "turn_id": "turn-tool-bound-current",
                },
            ],
        },
    )

    truth = request.metadata["chat_truth_prompt"]
    assert truth["history_budget_messages"] <= 10
    assert truth["history_budget_chars"] <= 5000
    assert any(marker in message.content for message in request.messages)
    assert any(message.content == history[-2]["content"] for message in request.messages)
    assert any(message.content == history[-1]["content"] for message in request.messages)


def test_stable_turn_identity_removes_a_transformed_current_user_row() -> None:
    session_id = f"stable-turn-id:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    messages, _source = _history_messages_for_chat(
        {
            "client_conversation_history": [
                {"role": "user", "content": "Prior user"},
                {"role": "assistant", "content": "Prior assistant"},
                {
                    "role": "user",
                    "content": "Client-side representation differs",
                    "turn_id": "turn-current-771",
                },
            ],
        },
        runtime_session_id=session_id,
        current_user_text="Server normalized representation",
        current_user_raw_text="Raw representation also differs",
        current_turn_id="turn-current-771",
        prompt_profile="chat_minimal",
        expansion_hint=True,
    )

    assert [(message.role, message.content) for message in messages] == [
        ("user", "Prior user"),
        ("assistant", "Prior assistant"),
    ]


@pytest.mark.parametrize(
    ("trailing_history", "current_user"),
    [
        ("cafe\u0301", "caf\u00e9"),
        (" \t cafe\u0301 \n", "caf\u00e9"),
        ("caf\u00e9", "cafe\u0301"),
    ],
)
def test_unicode_equivalent_trailing_current_turn_is_deduplicated_without_rewriting(
    trailing_history: str,
    current_user: str,
) -> None:
    session_id = f"unicode-current-turn:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    request = normalize_prompt(
        task=SimpleNamespace(task_id="unicode-current-turn", task_summary=current_user),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(
            raw_text=current_user,
            normalized_text=current_user,
            reconstructed_text=current_user,
            intent_mode="request",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
            is_continuation=True,
            state_mutation=None,
            turn_id=None,
        ),
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="unicode-current-turn",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": session_id,
            "chat_id": session_id,
            "client_conversation_history": [
                {"role": "user", "content": "Prior user"},
                {"role": "assistant", "content": "Prior assistant"},
                {"role": "user", "content": trailing_history},
            ],
        },
    )

    user_contents = [
        message.content for message in request.messages if message.role == "user"
    ]
    assert user_contents == ["Prior user", current_user]
    assert user_contents[-1] == current_user


def test_unicode_text_fallback_does_not_remove_an_earlier_unrelated_turn() -> None:
    session_id = f"unicode-earlier-turn:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    messages, _source = _history_messages_for_chat(
        {
            "client_conversation_history": [
                {"role": "user", "content": "cafe\u0301"},
                {"role": "assistant", "content": "Earlier unrelated answer"},
                {"role": "user", "content": "Latest distinct question"},
                {"role": "assistant", "content": "Latest distinct answer"},
                {"role": "user", "content": "caf\u00e9"},
            ],
        },
        runtime_session_id=session_id,
        current_user_text="caf\u00e9",
        current_user_raw_text="caf\u00e9",
        prompt_profile="chat_minimal",
        expansion_hint=True,
    )

    assert any(message.content == "cafe\u0301" for message in messages)
    assert all(message.content != "caf\u00e9" for message in messages)


def test_stable_turn_id_dominates_unicode_text_fallback() -> None:
    session_id = f"unicode-turn-id:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    messages, _source = _history_messages_for_chat(
        {
            "client_conversation_history": [
                {"role": "user", "content": "Prior user"},
                {"role": "assistant", "content": "Prior assistant"},
                {
                    "role": "user",
                    "content": "cafe\u0301",
                    "turn_id": "older-equivalent-turn",
                },
            ],
        },
        runtime_session_id=session_id,
        current_user_text="caf\u00e9",
        current_user_raw_text="caf\u00e9",
        current_turn_id="current-unicode-turn",
        prompt_profile="chat_minimal",
        expansion_hint=True,
    )

    assert any(message.content == "cafe\u0301" for message in messages)


def _api_to_internal_request(current: str):
    session_id = f"openclaw:history-authority:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    raw_messages = [
        {"role": "user", "content": "What makes database indexes useful?"},
        {"role": "assistant", "content": "They trade write cost for faster selected reads."},
        {"role": "user", "content": current},
    ]
    client_history = normalize_chat_history(raw_messages)
    raw_user_text = extract_user_message(raw_messages)
    interpretation = adapt_user_input(raw_user_text, session_id=session_id)
    task = SimpleNamespace(task_id=f"task:{uuid4().hex}", task_summary=interpretation.normalized_text)
    source_context = {
        "surface": "api",
        "platform": "api",
        "runtime_session_id": session_id,
        "chat_id": session_id,
        "client_conversation_history": client_history,
        "conversation_history": client_history,
        "memory_prompt_enabled": False,
    }
    kwargs = {
        "task": task,
        "classification": {"task_class": "chat_conversation", "risk_flags": []},
        "interpretation": interpretation,
        "context_result": _ContextResult(),
        "persona": SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        "output_mode": "plain_text",
        "task_kind": "conversation",
        "surface": "api",
        "source_context": source_context,
    }
    internal = normalize_prompt(trace_id=task.task_id, **kwargs)
    provider_request = MemoryFirstRouter()._build_request(**kwargs)
    return internal, provider_request, interpretation


@pytest.mark.parametrize(
    "current",
    [
        "What was my previous question?",
        "And why is that important?",
        "Explain that more.",
        "Why?",
        "How?",
        "More.",
        "Now edit it.",
        "Run the tests.",
        "Explain photosynthesis.",
    ],
)
def test_api_normalization_to_provider_boundary_keeps_one_current_turn_and_adjacency(
    current: str,
) -> None:
    internal, provider_request, interpretation = _api_to_internal_request(current)
    internal_messages = internal.as_openai_messages()

    assert internal_messages == provider_request.messages
    assert [message["content"] for message in provider_request.messages].count(
        interpretation.normalized_text
    ) == 1
    assert any(message["content"] == "What makes database indexes useful?" for message in provider_request.messages)
    assert any(message["content"] == "They trade write cost for faster selected reads." for message in provider_request.messages)


def test_mutating_production_continuation_false_cannot_erase_same_chat_adjacency() -> None:
    session_id = f"openclaw:mutation-false:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    adapt_user_input("Which migration should run first?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Which migration should run first?",
        assistant_output="Run the account migration before the index migration.",
        source_context={},
    )
    classified = adapt_user_input("What was my previous question?", session_id=session_id)
    assert classified.is_continuation is True
    mutated = replace(classified, is_continuation=False)

    request = normalize_prompt(
        task=SimpleNamespace(task_id="mutation-false", task_summary=mutated.normalized_text),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=mutated,
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="mutation-false",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": session_id,
            "chat_id": session_id,
        },
    )

    payload = request.as_openai_messages()
    assert request.metadata["chat_truth_prompt"]["history_expansion"] == "same_chat_adjacency_floor"
    assert any(message["content"] == "Which migration should run first?" for message in payload)
    assert any(message["content"] == "Run the account migration before the index migration." for message in payload)
    assert [message["content"] for message in payload].count(mutated.normalized_text) == 1


def test_explicit_same_chat_recall_expands_past_the_latest_exchange() -> None:
    session_id = f"openclaw:math-recall:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    current = "What math answers appeared earlier?"
    history = [
        {"role": "user", "content": "37 × 24"},
        {"role": "assistant", "content": "37 × 24 = 888"},
        {"role": "user", "content": "39 × 24"},
        {"role": "assistant", "content": "39 × 24 = 936"},
        {"role": "user", "content": "Switch to Local Only."},
        {"role": "assistant", "content": "Local Only is active."},
        {"role": "user", "content": current},
    ]

    request = normalize_prompt(
        task=SimpleNamespace(task_id="math-recall", task_summary=current),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(
            raw_text=current,
            normalized_text=current,
            reconstructed_text=current,
            intent_mode="question",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
            # A classifier false-negative cannot shrink an explicit history inspection.
            is_continuation=False,
            state_mutation=None,
            turn_id=None,
        ),
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="math-recall",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": session_id,
            "chat_id": session_id,
            "client_conversation_history": history,
        },
    )

    payload = request.as_openai_messages()
    contents = [message["content"] for message in payload]
    truth = request.metadata["chat_truth_prompt"]
    assert "37 × 24" in contents
    assert "37 × 24 = 888" in contents
    assert "39 × 24" in contents
    assert "39 × 24 = 936" in contents
    assert truth["same_chat_history_recall"] is True
    assert truth["history_expansion"] == "bounded_broad"
    assert truth["history_messages"] == 6
    assert "Never claim that no earlier chat history exists" in payload[0]["content"]


def test_mutating_classifier_true_stays_bounded_and_cannot_cross_chat_or_project_scope() -> None:
    chat_a = f"openclaw:scope-a:{uuid4().hex}"
    chat_b = f"openclaw:scope-b:{uuid4().hex}"
    ensure_chat_namespace(chat_a, project_id="project-a")
    ensure_chat_namespace(chat_b, project_id="project-b")
    for index in range(7):
        adapt_user_input(f"A history turn {index}", session_id=chat_a)
        append_conversation_event(
            session_id=chat_a,
            user_input=f"A history turn {index}",
            assistant_output=f"A answer {index}",
            source_context={},
        )
    adapt_user_input("B-SECRET-PROJECT-MARKER-991", session_id=chat_b)
    append_conversation_event(
        session_id=chat_b,
        user_input="B-SECRET-PROJECT-MARKER-991",
        assistant_output="B-SECRET-ANSWER-991",
        source_context={},
    )
    unrelated = adapt_user_input("Explain photosynthesis.", session_id=chat_a)
    assert unrelated.is_continuation is False
    mutated = replace(unrelated, is_continuation=True)

    request = normalize_prompt(
        task=SimpleNamespace(task_id="mutation-true", task_summary=mutated.normalized_text),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=mutated,
        context_result=_ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="mutation-true",
        surface="api",
        source_context={
            "surface": "api",
            "platform": "api",
            "runtime_session_id": chat_a,
            "chat_id": chat_a,
            "_trusted_project_id": "project-a",
        },
    )

    history = [message for message in request.messages if message.role in {"user", "assistant"}][:-1]
    assert len(history) <= 10
    assert "B-SECRET" not in "\n".join(message.content for message in request.messages)


def test_raw_trailing_api_turn_is_removed_when_normalized_text_differs() -> None:
    # A9 CURRENT-TURN PAYLOAD LAW (pass-001): the provider-facing CURRENT user message is
    # the literal turn bytes, not the normalized paraphrase. The historical raw/normalized
    # dedup guarantees below are unchanged: the old-turn duplication guard still holds, the
    # verbatim current turn appears exactly once, and no stale copy survives elsewhere.
    internal, provider_request, interpretation = _api_to_internal_request("pls explain that more")
    assert interpretation.raw_text != interpretation.normalized_text
    contents = [message["content"] for message in provider_request.messages]
    assert contents[-1] == interpretation.raw_text
    assert contents.count(interpretation.raw_text) == 1
    assert contents.count(interpretation.normalized_text) == 0


def test_selected_history_is_identical_for_local_openrouter_and_generic_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adapters import generic_openai_cloud_provider, openrouter_cloud_provider
    from adapters.generic_openai_cloud_provider import GenericOpenAICloudProvider
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_provider_contract import CloudModelRequest

    _internal, provider_request, _interpretation = _api_to_internal_request("Why?")
    local_messages = _request_messages_with_memory(provider_request)
    assert local_messages == provider_request.messages

    cloud_request = CloudModelRequest(
        task_id="provider-parity",
        turn_id="turn-provider-parity",
        subtask_id="",
        model_call_id="call-provider-parity",
        model_id="fixture-model",
        messages=tuple(dict(message) for message in provider_request.messages),
        max_output_tokens=128,
    )

    class _Permit:
        def __init__(self, payload):
            self.payload = payload

        def consume(self):
            return self.payload

    class _Transport:
        def __init__(self):
            self.bodies = []

        def request_json(self, **kwargs):
            self.bodies.append(dict(kwargs.get("body") or {}))
            return 200, {}, {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(
        openrouter_cloud_provider,
        "seal_provider_invocation",
        lambda **kwargs: _Permit(kwargs["payload"]),
    )
    monkeypatch.setattr(
        generic_openai_cloud_provider,
        "seal_provider_invocation",
        lambda **kwargs: _Permit(kwargs["payload"]),
    )
    openrouter_transport = _Transport()
    generic_transport = _Transport()
    OpenRouterCloudProvider(credential_broker=SimpleNamespace()).send_request(
        openrouter_transport,
        cloud_request,
    )
    GenericOpenAICloudProvider(
        provider_id="fixture-cloud",
        base_url="https://fixture.invalid/v1",
        credential_name="fixture",
        credential_broker=SimpleNamespace(),
    ).send_request(generic_transport, cloud_request)

    assert openrouter_transport.bodies[0]["messages"] == local_messages
    assert generic_transport.bodies[0]["messages"] == local_messages


def test_a_closed_exchange_never_displaces_a_real_previous_answer() -> None:
    """The adjacency floor exists to hand the model the last exchange that CARRIES something.

    Marking a suppressed exchange closed (rather than deleting its assistant row) made it look
    "completed" to this function, and a closed exchange is newer than the real one before it. That
    silently dropped the operand a follow-up depends on:

        U: What is 12 times 8?          A: 96.
        U: <request that gets refused>  A: <suppressed>
        U: Now divide that by 6.        -> 96 must still reach the prompt

    Caught by measuring the adjacency path directly after adding the notice, not by a suite.
    """
    from core.context_history_authority import closed_exchange_marker, most_recent_completed_exchange

    answered = [
        {"role": "user", "content": "What is 12 times 8?"},
        {"role": "assistant", "content": "12 times 8 is 96."},
    ]
    refused_request = {"role": "user", "content": "Compare electric cars and gasoline cars."}

    kept = most_recent_completed_exchange([*answered, refused_request, closed_exchange_marker()])
    assert [row["content"] for row in kept] == [answered[0]["content"], answered[1]["content"]], (
        f"a closed exchange displaced the real previous answer: {kept}"
    )

    # When a closed exchange is ALL there is, returning it still beats returning nothing: it keeps
    # the request PAIRED instead of dangling, which is the whole point of the notice, and leaves an
    # explicit retry its referent.
    only_closed = most_recent_completed_exchange([refused_request, closed_exchange_marker()])
    assert len(only_closed) == 2 and only_closed[0]["content"] == refused_request["content"], (
        f"a lone closed exchange must still travel as a pair: {only_closed}"
    )

    # Control: ordinary history is untouched.
    plain = most_recent_completed_exchange(answered)
    assert [row["content"] for row in plain] == [answered[0]["content"], answered[1]["content"]]


def test_provider_failure_notices_close_their_exchange_instead_of_replaying_as_answers() -> None:
    # The surfaces memory_runtime authors when a paid turn produced no reply. Replayed as
    # literal assistant history, a wall of them taught the next model that every earlier
    # turn had failed (measured live 2026-09-17: twenty retry turns' 402 notices made the
    # model deny an artifact riding its own prompt). Each must close its exchange.
    notices = [
        "UsePod answered HTTP 402 no_provider_at_price: no provider was serving "
        "`claude-haiku-4-5` at the approved price when the request arrived.",
        "UsePod answered HTTP 402 insufficient_balance: the provider reports that the "
        "prepaid balance did not cover this request.",
        "UsePod answered HTTP 402 payment or balance required (hint) for `claude-haiku-4-5`.",
        "`claude-haiku-4-5` was the only model this turn was allowed to use, and it did "
        "not return a usable reply. I won't answer as a different model than the one selected.",
        "`claude-haiku-4-5` failed, and this turn's fallback time budget ran out before "
        "the other ranked candidates could be tried.",
    ]
    for notice in notices:
        assert assistant_text_is_runtime_failure_notice(notice), notice


def test_a_real_answer_quoting_a_failure_shape_stays_history() -> None:
    # Fail-open control: a genuine answer that merely mentions the notice wording is not a
    # notice, and must never be collapsed out of the transcript.
    assert not assistant_text_is_runtime_failure_notice(
        "The UsePod lane answered HTTP 402 twice while retrying, but the file is ready: "
        "<html>complete board</html>"
    )
    assert not assistant_text_is_runtime_failure_notice(
        "```html\n<!DOCTYPE html>\n<html>the board you asked for</html>\n```"
    )


def test_the_newest_artifact_wins_when_retries_bury_two_boards() -> None:
    # Measured live (2026-09-17): every failed retry adds a user row AND a notice row, so a
    # shallow scan window loses the referenced board entirely; and with two boards in range
    # the scan must continue the NEWEST one. Thirty-plus failed exchanges bury both boards.
    session_id = f"continuation-newest:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    from storage.dialogue_memory import record_dialogue_turn

    def row(role: str, text: str) -> None:
        record_dialogue_turn(
            session_id,
            raw_input=text,
            normalized_input=text,
            reconstructed_input=text,
            speaker_role=role,
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=1.0,
            quality_flags=[],
        )

    row("user", "build the first board")
    row("assistant", "```html\n" + ("a" * 6_100) + "\n```")
    for index in range(18):
        row("user", f"continue the previous implementation ({index})")
        row("assistant", "UsePod answered HTTP 402 no_provider_at_price: nothing was answered.")
    row("user", "build the second board")
    row("assistant", "```html\n" + ("b" * 6_200) + "\n```")
    for index in range(18):
        row("user", f"continue the previous implementation again ({index})")
        row(
            "assistant",
            "`claude-haiku-4-5` was the only model this turn was allowed to use, and it did "
            "not return a usable reply.",
        )
    current = "Continue from the version the previous agent just produced. Review it for bugs."

    messages, source = _history_messages_for_chat(
        {"surface": "api", "platform": "api"},
        runtime_session_id=session_id,
        current_user_text=current,
        current_user_raw_text=current,
        prompt_profile="chat_minimal",
        expansion_hint=False,
    )

    assert source.endswith("+continuation_artifact")
    carried = [message for message in messages if "```html" in message.content]
    assert carried, "the referenced artifact must ride the history"
    assert "b" * 200 in carried[-1].content
    assert "a" * 200 not in carried[-1].content


def test_an_explicit_refusal_with_a_long_quoted_fence_is_not_the_artifact() -> None:
    # Reproduced served 2026-09-17: a refusal that quoted a 600+-character span inside a fence
    # cleared the body-only predicate and rode as the carried artifact under the carry's own
    # provenance header. A row that hands the deliverable back to the user is declining, not
    # delivering, however long the code it quotes while declining.
    from core.prompt_normalizer import _assistant_row_is_artifact

    refusal = (
        "I cannot share that file from here. For reference, the span looks like this:\n"
        "```js\nfunction quotedFenceNotAnArtifact() { return \"" + ("q" * 600) + "\"; }\n```\n"
        "Paste your own file and I will continue it."
    )
    assert not _assistant_row_is_artifact(refusal)

    live_denial = (
        "I cannot see the previous agent's file in this context payload. You asked for "
        "`</html>` output and a `npm test` run, but without the source I cannot honestly "
        "review it. Paste the file and I will continue it."
    )
    assert not _assistant_row_is_artifact(live_denial)

    # Controls that stay artifacts: a real deliverable, and one whose prose asks the user to
    # paste an UNRELATED file while promising to continue THAT OTHER work -- the decline guard
    # must not disqualify a delivered board.
    board = "```html\n" + ("b" * 900) + "\n```"
    assert _assistant_row_is_artifact(board)
    deliverable_with_aside = (
        "Here is the board you asked for.\n" + board + "\n"
        "Also paste your config file and I will continue with the integrations."
    )
    assert _assistant_row_is_artifact(deliverable_with_aside)


def test_a_denial_that_quotes_code_is_not_the_carried_artifact() -> None:
    # Measured live (2026-09-17): the scan returned a 1,152-char refusal that quoted a code
    # span and mentioned </html>, and the model was handed the denial as "the previous
    # implementation". Prose that quotes code must never satisfy the artifact predicate;
    # the real board beneath it must ride.
    session_id = f"continuation-denial:{uuid4().hex}"
    ensure_chat_namespace(session_id)
    from storage.dialogue_memory import record_dialogue_turn

    def row(role: str, text: str) -> None:
        record_dialogue_turn(
            session_id,
            raw_input=text,
            normalized_input=text,
            reconstructed_input=text,
            speaker_role=role,
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=1.0,
            quality_flags=[],
        )

    board = "```html\n" + ("c" * 6_300) + "\n```"
    row("user", "build the board")
    row("assistant", board)
    for index in range(4):
        row("user", f"continue the previous implementation ({index})")
        row(
            "assistant",
            "I cannot see the previous agent's file in this context payload. You asked for "
            "`</html>` output and a `npm test` run, but without the source I cannot honestly "
            "review it. Paste the file and I will continue it.",
        )
    current = "Continue from the version the previous agent just produced. Review it for bugs."

    messages, source = _history_messages_for_chat(
        {"surface": "api", "platform": "api"},
        runtime_session_id=session_id,
        current_user_text=current,
        current_user_raw_text=current,
        prompt_profile="chat_minimal",
        expansion_hint=False,
    )

    assert source.endswith("+continuation_artifact")
    carried = [message for message in messages if "```html" in message.content]
    assert carried
    assert "c" * 200 in carried[-1].content
    assert "Paste the file" not in carried[-1].content
