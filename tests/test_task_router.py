from __future__ import annotations

from unittest import mock

import pytest

from core.task_router import (
    _classify_via_model,
    build_task_envelope_for_request,
    classify,
    evaluate_direct_math_request,
    evaluate_word_math_request,
    looks_like_canonical_project_question,
    looks_like_current_chat_recall,
    looks_like_live_recency_lookup,
    looks_like_semantic_hive_request,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("What is 17 * 23?", "17 * 23 = 391."),
        ("what is 17 times 23?", "17 * 23 = 391."),
        ("4821 multiplied by 37", "4821 * 37 = 178377."),
        ("144 divided by 12", "144 / 12 = 12."),
        ("17 plus 23 minus 5", "17 + 23 - 5 = 35."),
        ("50 times 3 please", "50 * 3 = 150."),
        ("100 / 4", "100 / 4 = 25."),
    ],
)
def test_evaluate_direct_math_request_accepts_only_narrow_arithmetic_wrappers(
    prompt: str,
    expected: str,
) -> None:
    assert evaluate_direct_math_request(prompt) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "A box has 3 red balls and 2 blue balls. What is the total?",
        "What is 17 * 23 and delete temp.txt?",
        "Which plan is 100 / 4 better for?",
    ],
)
def test_evaluate_direct_math_request_rejects_word_problems_and_mixed_intent(
    prompt: str,
) -> None:
    assert evaluate_direct_math_request(prompt) is None


def test_word_math_request_classifies_as_chat_conversation() -> None:
    result = classify(
        "I have 3 tasks. Task A takes 17 minutes, Task B takes twice Task A minus 4 minutes, Task C takes 11 minutes. What is the total? Show the steps."
    )

    assert result["task_class"] == "chat_conversation"


def test_evaluate_word_math_request_solves_task_duration_prompt() -> None:
    response = evaluate_word_math_request(
        "I have 3 tasks. Task A takes 17 minutes, Task B takes twice Task A minus 4 minutes, Task C takes 11 minutes. What is the total? Show the steps."
    )

    assert response is not None
    assert "Task B = 2 * 17 - 4 = 30." in response
    assert response.endswith("= 58.")


def test_live_recency_lookup_classifies_as_research() -> None:
    prompt = "What happened five minutes ago in global markets?"

    assert looks_like_live_recency_lookup(prompt) is True
    result = classify(prompt)
    assert result["task_class"] == "research"


@pytest.mark.parametrize(
    "prompt",
    [
        "What is my current preference?",
        "What is our current focus?",
        "What do I prefer now?",
    ],
)
def test_current_chat_recall_does_not_fall_into_research(prompt: str) -> None:
    assert looks_like_current_chat_recall(prompt) is True
    assert classify(prompt)["task_class"] == "chat_conversation"


@pytest.mark.parametrize(
    "prompt",
    [
        "What is one gentle way to make a morning feel less rushed?",
        "Why might a familiar song feel comforting?",
        "Tell me about why small routines can feel reassuring.",
    ],
)
def test_ordinary_conceptual_questions_do_not_fall_into_research(prompt: str) -> None:
    assert classify(prompt)["task_class"] == "chat_conversation"


@pytest.mark.parametrize(
    "prompt",
    [
        "In one sentence, what is VOOL and who builds it?",
        "Who built VOOL?",
        "Explain how VOOL relates to VOOL.",
        "Tell me about Parad0x Labs and VOOL.",
    ],
)
def test_canonical_project_questions_use_grounded_conversation(prompt: str) -> None:
    assert looks_like_canonical_project_question(prompt) is True
    assert classify(prompt)["task_class"] == "chat_conversation"


@pytest.mark.parametrize(
    "prompt",
    [
        "Search online for current OpenRouter news.",
        "What is VOOL saying on X right now?",
    ],
)
def test_explicit_live_canonical_lookup_still_uses_research(prompt: str) -> None:
    assert looks_like_canonical_project_question(prompt) is False
    assert classify(prompt)["task_class"] == "research"


def test_model_classifier_is_disabled_under_pytest(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_task_router.py::test_model_classifier_is_disabled_under_pytest")

    with mock.patch("requests.post", side_effect=AssertionError("model classifier should not hit network under pytest")):
        assert _classify_via_model("do you think boredom is useful?") == ""


def test_patch_and_pytest_prompt_classifies_as_debugging() -> None:
    result = classify(
        "apply this patch, then run `python3 -m pytest -q test_app.py`\n"
        "```diff\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def answer():\n"
        "-    return 41\n"
        "+    return 42\n"
        "```\n"
    )

    assert result["task_class"] == "debugging"


def test_replace_and_ruff_format_check_prompt_is_not_risky_and_classifies_as_debugging() -> None:
    result = classify(
        "replace `foo( )` with `foo()` in app.py, then run `ruff format --check app.py`"
    )

    assert result["task_class"] == "debugging"
    assert result["risk_flags"] == []


def test_absolute_workspace_path_under_vool_hive_mind_is_not_misclassified_as_hive_request() -> None:
    prompt = (
        "Create a file named vool_test_01.txt in "
        "/Users/test/vool-hive-mind/artifacts/acceptance_runs/2026-03-27-fresh-proof/workspace/main "
        "with exactly this content: ALPHA-LOCAL-FILE-01"
    )

    assert looks_like_semantic_hive_request(prompt) is False
    result = classify(prompt)
    assert result["task_class"] != "integration_orchestration"


def test_model_classifier_returns_empty_when_ollama_port_is_unreachable(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")

    with mock.patch("requests.post", side_effect=AssertionError("post should not run when socket preflight fails")):
        assert _classify_via_model("do you think boredom is useful?") == ""


def test_chat_truth_surface_skips_model_classifier_for_unknown_prompt() -> None:
    prompt = "Reply with exactly GREENLOOP-WARMUP-4 and nothing else."

    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("chat surfaces should not model-classify unknown prompts")):
        result = classify(
            prompt,
            context={
                "source_surface": "openclaw",
                "source_platform": "openclaw",
            },
        )

    assert result["task_class"] == "unknown"


def test_chat_surface_envelope_routes_unknown_prompt_without_model_classifier() -> None:
    prompt = "Reply with exactly GREENLOOP-WARMUP-5 and nothing else."

    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("chat surface envelope should not invoke model classifier")):
        envelope = build_task_envelope_for_request(
            prompt,
            context={},
            chat_surface=True,
        )

    assert envelope.inputs["task_class"] == "chat_conversation"
    assert envelope.inputs["routing_profile"]["output_mode"] == "plain_text"
