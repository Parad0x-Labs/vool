"""C18 — one typed response-language policy with explicit precedence.

The base rule identified English by counting ASCII words (``[A-Za-z]+`` ≥ 4). That is not
English identification: unaccented Spanish, ASCII-ized Lithuanian and plain German are all
"≥4 ASCII words", so real non-English turns were silently assigned an English expectation and
their answers were pushed back toward English by the anti-drift guard. RED at base 2055d622.

The contract pinned here, in one canonical module (``core/response_language_policy.py``):

  precedence: explicit requested output language
            > applicable scoped operator preference
            > sufficiently evidenced input language
  uncertain input resolves to NO expectation — it is never silently forced to English.
  translation/bilingual requests, code fences and the strict literal-bytes safeguards keep
  their existing exempt behavior, byte for byte.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.response_language_policy import (
    check_response_language,
    response_language_policy_for_text,
    response_language_provider_instruction,
    response_language_retry_instruction,
)
from tests.operator_profile_rig import OWNER, profile_env_generator


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------
# 1. The defect: ASCII-word counting is not English identification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_text",
    (
        # Unaccented Spanish — every word is ASCII, none of it is English.
        "Explicame por favor como funciona un indice de base de datos y porque acelera las consultas",
        # ASCII-ized Lithuanian — the way Lithuanian is very often typed.
        "Prasau paaiskink kas yra duomenu baziu indeksas ir kaip jis veikia",
        # Plain German.
        "Erkläre mir bitte, wie ein Datenbankindex funktioniert und warum er Abfragen beschleunigt",
        # French.
        "Explique-moi comment fonctionne un index de base de données et pourquoi il accélère les recherches",
    ),
)
def test_non_english_latin_turns_are_not_silently_assigned_english(user_text: str) -> None:
    policy = response_language_policy_for_text(user_text)

    assert policy.expected_language != "en", (
        "ASCII-word counting forced an English expectation on a non-English turn"
    )
    assert policy.expected_language == "none"


def test_ambiguous_latin_turn_fails_open_not_to_english() -> None:
    # A short fragment with no sufficient evidence either way.
    assert response_language_policy_for_text("duomenu baziu indeksas").expected_language == "none"


# ---------------------------------------------------------------------------
# 2. Sufficiently evidenced input language
# ---------------------------------------------------------------------------


def test_lithuanian_exclusive_letters_evidence_lithuanian() -> None:
    policy = response_language_policy_for_text(
        "Paaiškink, kaip veikia duomenų bazių indeksas ir kodėl jis pagreitina paiešką."
    )

    assert policy.expected_language == "lt"
    assert policy.reason == "evidenced_input_language"


def test_german_sharp_s_evidences_german() -> None:
    policy = response_language_policy_for_text(
        "Erkläre mir bitte, wie ein Datenbankindex funktioniert und warum er Abfragen "
        "in großen Tabellen beschleunigt."
    )

    assert policy.expected_language == "de"
    assert policy.reason == "evidenced_input_language"


def test_spanish_exclusive_punctuation_evidences_spanish() -> None:
    policy = response_language_policy_for_text(
        "¿Puedes explicarme qué es un índice de base de datos y por qué acelera las consultas?"
    )

    assert policy.expected_language == "es"
    assert policy.reason == "evidenced_input_language"


def test_kana_evidences_japanese_but_han_alone_stays_unresolved() -> None:
    japanese = response_language_policy_for_text(
        "データベースのインデックスはどのように機能し、なぜ検索が速くなるのか説明してください。"
    )
    assert japanese.expected_language == "ja"
    assert japanese.reason == "evidenced_input_language"

    # Pure Han is genuinely ambiguous between Chinese and Japanese — no silent choice, and
    # above all no silent English.
    han_only = response_language_policy_for_text("请解释为什么数据库索引更快。")
    assert han_only.expected_language == "none"


@pytest.mark.parametrize(
    "user_text",
    (
        "Please explain why a library index improves search speed.",
        "What is the currency of North Korea and why would spending it in Seoul fail?",
    ),
)
def test_genuine_english_turns_still_evidence_english(user_text: str) -> None:
    policy = response_language_policy_for_text(user_text)

    assert policy.expected_language == "en"
    assert policy.reason == "english_user_turn"


def test_typo_heavy_english_still_evidences_english() -> None:
    policy = response_language_policy_for_text(
        "wat is teh crrency of north korea and why woud the transacion fail over there"
    )

    assert policy.expected_language == "en"


def test_mixed_kana_and_ascii_turn_evidences_japanese() -> None:
    policy = response_language_policy_for_text(
        "データベースのindexについて、explain briefly お願いします。"
    )

    assert policy.expected_language == "ja"


# ---------------------------------------------------------------------------
# 3. Explicit requested output language (top precedence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_text", "expected"),
    (
        ("Answer in Lithuanian, please: what is a database index?", "lt"),
        ("Atsakyk lietuviškai: kas yra duomenų bazių indeksas?", "lt"),
        ("Please respond in Spanish: what is a database index?", "es"),
        ("Answer in Japanese: what is a database index?", "ja"),
        ("Please answer in German: what is a database index?", "de"),
        ("日本語で答えてください。データベースのインデックスとは何ですか？", "ja"),
        # Explicit beats evidenced input: an English turn that names its output language.
        (
            "Please answer in Japanese even though I am writing in English: what is an index?",
            "ja",
        ),
    ),
)
def test_explicit_output_request_wins(user_text: str, expected: str) -> None:
    policy = response_language_policy_for_text(user_text)

    assert policy.expected_language == expected
    assert policy.reason == "explicit_output_request"


# ---------------------------------------------------------------------------
# 4. Translation / bilingual preservation and the existing exemptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user_text",
    (
        "Translate this answer to Japanese.",
        "Give me a bilingual English and Korean explanation.",
        "Write a multilingual product description.",
        "Respond using Russian for this entire answer.",
        "Explain the result in both English and Chinese.",
        "Translate this into Lithuanian.",
    ),
)
def test_translation_and_bilingual_requests_stay_exempt(user_text: str) -> None:
    assert response_language_policy_for_text(user_text).expected_language == "none"


def test_code_fence_turns_stay_exempt() -> None:
    assert (
        response_language_policy_for_text(
            "Explain this code:\n```python\nprint('duomenu baze')\n```"
        ).expected_language
        == "none"
    )


# ---------------------------------------------------------------------------
# 5. Scoped operator preference (middle precedence)
# ---------------------------------------------------------------------------


def _profile_language(home_principals: bool = True) -> None:
    from core.operator_profile import remember

    return remember


def test_chat_scoped_language_preference_decides(profile_env) -> None:
    from core.operator_profile import remember, reset_table_cache_for_tests

    reset_table_cache_for_tests()
    changed = remember(
        OWNER, "language", "Lithuanian", scope="chat", session_id="chat-lt-1"
    )
    assert changed.persisted

    policy = response_language_policy_for_text(
        "Please explain how a database index works.",  # evidenced English — preference outranks it
        principal=OWNER,
        session_id="chat-lt-1",
    )

    assert policy.expected_language == "lt"
    assert policy.reason == "operator_preference"


def test_chat_scoped_preference_does_not_leak_to_another_chat(profile_env) -> None:
    from core.operator_profile import remember, reset_table_cache_for_tests

    reset_table_cache_for_tests()
    remember(OWNER, "language", "Lithuanian", scope="chat", session_id="chat-lt-1")

    other = response_language_policy_for_text(
        "Please explain how a database index works.",
        principal=OWNER,
        session_id="chat-other",
    )

    assert other.expected_language == "en"
    assert other.reason == "english_user_turn"


def test_global_preference_applies_and_explicit_request_still_wins(profile_env) -> None:
    from core.operator_profile import remember, reset_table_cache_for_tests

    reset_table_cache_for_tests()
    assert remember(OWNER, "language", "lt", scope="global").persisted

    applied = response_language_policy_for_text(
        "What is a database index?",
        principal=OWNER,
        session_id="any-chat",
    )
    assert applied.expected_language == "lt"
    assert applied.reason == "operator_preference"

    explicit = response_language_policy_for_text(
        "Answer in Japanese: what is a database index?",
        principal=OWNER,
        session_id="any-chat",
    )
    assert explicit.expected_language == "ja"
    assert explicit.reason == "explicit_output_request"


def test_unparseable_preference_is_ignored_not_guessed(profile_env) -> None:
    from core.operator_profile import remember, reset_table_cache_for_tests

    reset_table_cache_for_tests()
    remember(OWNER, "language", "Lithuanian and English, depends", scope="global")

    policy = response_language_policy_for_text(
        "What is a database index?",
        principal=OWNER,
        session_id="any-chat",
    )

    assert policy.expected_language == "en"


def test_no_principal_means_no_preference_tier() -> None:
    policy = response_language_policy_for_text("What is a database index?", session_id="chat-x")

    assert policy.expected_language == "en"


# ---------------------------------------------------------------------------
# 6. The typed check extends beyond English (script-level, no new classifier)
# ---------------------------------------------------------------------------


def test_english_answer_to_japanese_turn_violates_japanese_policy() -> None:
    policy = {"expected_language": "ja", "reason": "evidenced_input_language"}

    english_answer = (
        "A database index is a data structure that improves the speed of data retrieval "
        "operations on a table at the cost of additional storage and maintenance."
    )
    assert not check_response_language(english_answer, policy).compliant

    japanese_answer = "データベースのインデックスは検索を速くするデータ構造です（index というラベルは許容）。"
    assert check_response_language(japanese_answer, policy).compliant


def test_cjk_answer_to_lithuanian_turn_violates_latin_policy() -> None:
    policy = {"expected_language": "lt", "reason": "evidenced_input_language"}

    assert not check_response_language("数据库索引是一种加快检索的数据结构。", policy).compliant
    assert check_response_language(
        "Duomenų bazių indeksas yra duomenų struktūra, pagreitinanti paiešką lentelėje.",
        policy,
    ).compliant


def test_expected_none_and_code_blocks_stay_compliant() -> None:
    assert check_response_language("任何回答", None).compliant
    assert check_response_language(
        "```python\nresult = index_scan(данные)\n```",
        {"expected_language": "en", "reason": "english_user_turn"},
    ).compliant


# ---------------------------------------------------------------------------
# 7. Instructions: retry string stays byte-identical for English; typed for others
# ---------------------------------------------------------------------------


def test_english_retry_instruction_is_byte_identical() -> None:
    assert response_language_retry_instruction(
        {"expected_language": "en", "reason": "english_user_turn"}
    ) == "Return the answer in English only, while preserving the answer's meaning."


def test_typed_retry_instruction_names_the_language() -> None:
    assert (
        response_language_retry_instruction(
            {"expected_language": "lt", "reason": "operator_preference"}
        )
        == "Return the answer in Lithuanian only, while preserving the answer's meaning."
    )


def test_provider_instruction_is_typed_and_bounded() -> None:
    assert (
        response_language_provider_instruction(
            {"expected_language": "lt", "reason": "operator_preference"}
        )
        == "Respond in Lithuanian."
    )
    # English keeps its proven post-hoc channel; the dominant lane's prompt bytes do not change.
    assert (
        response_language_provider_instruction(
            {"expected_language": "en", "reason": "english_user_turn"}
        )
        == ""
    )
    assert response_language_provider_instruction(None) == ""
    assert (
        response_language_provider_instruction({"expected_language": "none"})
        == ""
    )


# ---------------------------------------------------------------------------
# 8. The policy reaches the actual provider request (first call, not only retry)
# ---------------------------------------------------------------------------


def _build_router_request(user_text: str, *, source_context=None):
    from core.memory_first_router import MemoryFirstRouter

    router = MemoryFirstRouter(registry=mock.Mock())
    interpretation = SimpleNamespace(raw_text=user_text, normalized_text=user_text)
    return router._build_request(
        task=SimpleNamespace(task_id="language-provider-bound"),
        classification={},
        interpretation=interpretation,
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context=source_context if source_context is not None else {},
    )


def test_lithuanian_turn_carries_typed_policy_and_provider_instruction() -> None:
    request = _build_router_request(
        "Paaiškink, kaip veikia duomenų bazių indeksas ir kodėl jis pagreitina paiešką."
    )

    assert request.metadata["response_language_policy"] == {
        "expected_language": "lt",
        "reason": "evidenced_input_language",
    }
    system_messages = [
        str(message.get("content") or "")
        for message in request.messages
        if str(message.get("role") or "").casefold() == "system"
    ]
    assert any("Respond in Lithuanian." in content for content in system_messages)


def test_english_turn_keeps_its_existing_prompt_shape() -> None:
    request = _build_router_request(
        "Please explain why a library index improves search speed."
    )

    assert request.metadata["response_language_policy"] == {
        "expected_language": "en",
        "reason": "english_user_turn",
    }
    system_messages = [
        str(message.get("content") or "")
        for message in request.messages
        if str(message.get("role") or "").casefold() == "system"
    ]
    assert not any("Respond in" in content for content in system_messages)
