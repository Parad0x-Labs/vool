"""pa_beta_gate — private/personal-knowledge questions.

"What is my sister's middle name?" must never trigger a web/research loop; it is answered
from VOOL's memory if the fact was told to it, and otherwise plainly admitted. Ordinary
possessive work questions ("my repo", "my company website") are unaffected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.curiosity_roamer import _adaptive_research_decision
from core.personal_knowledge import (
    PERSONAL_KNOWLEDGE_ADMISSION,
    is_personal_knowledge_question,
    memory_has_personal_answer,
)

pytestmark = [pytest.mark.pa_beta]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}


@pytest.mark.parametrize("text,expected", [
    ("What is my sister's middle name?", True),
    ("What is my brother's middle name?", True),
    ("my mother's maiden name", True),
    ("what is my wife's birthday?", True),
    ("what is my home address?", True),
    ("what is my dad's phone number?", True),
    # ordinary possessive work questions must NOT be treated as personal-knowledge
    ("What is my repo name?", False),
    ("What is my company website?", False),
    ("what is my project deadline?", False),
    ("kill my child process", False),
    ("what is a mutex?", False),
    ("latest news on Python mutexes", False),
])
def test_personal_knowledge_detector(text, expected):
    assert is_personal_knowledge_question(text) is expected


def test_memory_presence_requires_the_specific_relation():
    facts = [{"text": "my sister's middle name is Rose."}]
    search = lambda query, limit=5: facts  # noqa: E731
    assert memory_has_personal_answer("what is my sister's middle name", search) is True
    # a brother question must NOT match a stored sister fact even though both share "middle name"
    assert memory_has_personal_answer("what is my brother's middle name", search) is False


def test_memory_presence_false_when_empty():
    assert memory_has_personal_answer("what is my sister's middle name", lambda query, limit=5: []) is False


def _decision(query, source_context=None):
    return _adaptive_research_decision(
        user_input=query,
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(topic_hints=[]),
        source_context=source_context or dict(_OPENCLAW),
    )


def test_unknown_personal_question_admits_and_never_web_searches():
    decision = _decision("what is my sister's middle name?")
    assert decision["enabled"] is False
    assert decision["reason"] == "personal_knowledge_not_researchable"
    assert decision["admitted_uncertainty"] is True
    assert "don't have" in decision["tool_gap_note"]
    assert decision["tool_gap_note"] == PERSONAL_KNOWLEDGE_ADMISSION


def test_known_personal_question_answers_from_memory():
    from core.context_scope import ContextAccessPolicy
    from core.persistent_memory import maybe_handle_memory_command

    session_id = "pk-recall"
    source_context = {
        "surface": "desktop",
        "platform": "local",
        "_owner_local": True,
    }
    policy = ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context=source_context,
    )
    handled, _ = maybe_handle_memory_command(
        "Remember my sister's middle name is Rose.",
        session_id=session_id,
        access_policy=policy,
    )
    assert handled  # the memory feature stored the fact
    decision = _decision(
        "what is my sister's middle name?",
        {**source_context, "chat_id": session_id},
    )
    assert decision["enabled"] is False
    assert decision["reason"] == "personal_knowledge_answer_from_memory"
    assert not decision.get("admitted_uncertainty")  # not forced to admit — memory has it


def test_ordinary_work_question_is_not_personal_short_circuited():
    # a repo / project / company question must not hit the personal-knowledge admission path
    for query in ("what is my repo name?", "what is my company website?"):
        decision = _decision(query)
        assert decision["reason"] != "personal_knowledge_not_researchable"
        assert decision["reason"] != "personal_knowledge_answer_from_memory"
