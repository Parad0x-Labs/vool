"""C18 review corrections — shared-script, loanword, quoting and guard-family counterexamples.

Independent review 2026-09-03 (C18_LANGUAGE_REVIEW_20260903.md) falsified the first typed
policy: Cyrillic was treated as uniquely Russian, Polish ą/ę as uniquely Lithuanian, a
naturalized loanword (ñ) as Spanish, a quoted sentence as an output instruction, and a Russian
answer passed the Japanese-target script guard.

Root requirement pinned here: SHARED SCRIPTS, NAMES AND QUOTED MATERIAL ARE NOT LANGUAGE
AUTHORITY. A language resolves only on evidence its table entry marks exclusive to it
(≥2 distinct exclusive letters, or one unambiguous prose mark like ¿ ¡ ß); shared scripts
(Cyrillic, Arabic, Devanagari, Han) resolve NOTHING on their own. Uncertain stays unresolved.
"""
from __future__ import annotations

import pytest

from core.response_language_policy import (
    check_response_language,
    response_language_policy_for_text,
)

# ---------------------------------------------------------------------------
# Review falsification probes — the decision, not the reply language
# ---------------------------------------------------------------------------


def test_ukrainian_cyrillic_is_not_russian() -> None:
    policy = response_language_policy_for_text("Поясни будь ласка як працює база даних")

    assert policy.expected_language == "uk"
    assert policy.reason == "evidenced_input_language"


def test_cyrillic_without_exclusive_letters_stays_unresolved() -> None:
    # Shared Cyrillic only: Russian "мир", Bulgarian, Serbian latinized shapes — no authority.
    assert response_language_policy_for_text("скажи мне привет мир").expected_language == "none"


def test_russian_exclusive_letters_resolve_russian() -> None:
    policy = response_language_policy_for_text("Расскажи мне, почему мы́ все ещё используем это")

    assert policy.expected_language == "ru"


def test_polish_is_not_lithuanian() -> None:
    policy = response_language_policy_for_text("Wyjaśnij tę funkcję i pokaż książkę")

    assert policy.expected_language == "pl"
    assert policy.reason == "evidenced_input_language"


def test_lithuanian_still_resolves_on_truly_exclusive_letters() -> None:
    policy = response_language_policy_for_text(
        "Paaiškink, kaip veikia duomenų bazių indeksas ir kodėl jis pagreitina paiešką."
    )

    assert policy.expected_language == "lt"


def test_shared_lithuanian_polish_letters_alone_stay_unresolved() -> None:
    # ą/ę exist in BOTH Lithuanian and Polish — no authority, no silent choice.
    assert (
        response_language_policy_for_text("Įrašyk tai į sąrašą").expected_language == "none"
    )


def test_naturalized_loanword_is_not_spanish() -> None:
    policy = response_language_policy_for_text("Please explain why señor is spelled with a tilde.")

    assert policy.expected_language == "en"
    assert policy.reason == "english_user_turn"


def test_inverted_question_mark_still_evidences_spanish() -> None:
    policy = response_language_policy_for_text(
        "¿Puedes explicarme qué es un índice de base de datos y por qué acelera las consultas?"
    )

    assert policy.expected_language == "es"


def test_quoted_sentence_is_not_an_output_instruction() -> None:
    policy = response_language_policy_for_text("Explain the sentence: Answer in Japanese")

    assert policy.expected_language == "none"


def test_quoted_span_is_not_an_output_instruction() -> None:
    # The quoted German phrase is a subject, not a commitment — the turn is an ENGLISH question.
    policy = response_language_policy_for_text('What does "Answer in German" mean here?')

    assert policy.expected_language == "en"
    assert policy.reason == "english_user_turn"


def test_negated_instruction_is_not_an_output_instruction() -> None:
    policy = response_language_policy_for_text(
        "Don't answer in Japanese, stay in English: what is a database index?"
    )

    assert policy.expected_language == "en"


def test_negation_with_a_real_later_instruction_still_resolves() -> None:
    policy = response_language_policy_for_text(
        "Don't answer in Japanese. Answer in Lithuanian: what is a database index?"
    )

    assert policy.expected_language == "lt"
    assert policy.reason == "explicit_output_request"


# ---------------------------------------------------------------------------
# The output guard must know the EXPECTED script family, not merely "not Latin"
# ---------------------------------------------------------------------------


def test_russian_answer_violates_japanese_expectation() -> None:
    policy = {"expected_language": "ja", "reason": "evidenced_input_language"}
    russian = "Русский ответ полностью написан по-русски и подробно объясняет работу индекса."

    result = check_response_language(russian, policy)

    assert not result.compliant
    assert result.violations == ("unexpected_output_language",)


def test_japanese_answer_still_satisfies_japanese_expectation() -> None:
    policy = {"expected_language": "ja", "reason": "evidenced_input_language"}
    japanese = "データベースのインデックスは検索を速くするデータ構造です（index というラベルは許容）。"

    assert check_response_language(japanese, policy).compliant


def test_ukrainian_expectation_rejects_cyrillic_dominated_by_another_family_nothing_and_latin() -> None:
    policy = {"expected_language": "uk", "reason": "operator_preference"}
    latin = "An index is a data structure that speeds up search at the cost of storage."

    assert not check_response_language(latin, policy).compliant


# ---------------------------------------------------------------------------
# Expanded language packs: resolve on exclusivity, otherwise stay unresolved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_text", "expected"),
    (
        ("Varbūt tu vari man paskaidrot, kā darbojas datubāžu indekss un kāpēc ātrāk", "lv"),
        ("Wyjaśnij mi proszę, jak działa indeks bazy danych i dlaczego przyspiesza wyszukiwanie", "pl"),
        ("Spiega però perché un indice sì accelera le ricerche del database per favore", "it"),
        ("Explique o índice de banco de dados e por que ele acelera as buscas nas informações; as consultas ficariam ainda mais rápidas amanhã", "pt"),
        ("Поясни будь ласка як працює база даних", "uk"),
        ("このデータベースのインデックスが検索を速くする理由を説明してください。", "ja"),
    ),
)
def test_expanded_packs_resolve_on_exclusive_evidence(user_text: str, expected: str) -> None:
    policy = response_language_policy_for_text(user_text)

    assert policy.expected_language == expected
    assert policy.reason == "evidenced_input_language"


@pytest.mark.parametrize(
    "user_text",
    (
        # No orthographic exclusivity: Dutch, Estonian, French, Swahili, Zulu, Swedish resolve
        # nothing from letters alone — uncertain stays unresolved, never forced.
        "Kun je uitleggen hoe een database index werkt en waarom het zoeken versnelt",
        "Kas sa saad mulle seletada, kuidas andmebaasi indeks töötab",
        "Explique-moi comment fonctionne un index de base de données",
        "Tafadhali nisaidie kueleza jinsi faharasa ya database inavyofanya kazi",
        "Ngicela ungichazela ngendlela isiqukathi somdwebo esisebenza ngayo",
        "Kan du förklara hur ett databasindex fungerar och varför det snabbar upp sökningar",
    ),
)
def test_languages_without_exclusive_evidence_stay_unresolved(user_text: str) -> None:
    policy = response_language_policy_for_text(user_text)

    assert policy.expected_language == "none"
    assert policy.expected_language != "en"


def test_explicit_requests_resolve_for_expanded_language_names() -> None:
    for name, code in (
        ("Latvian", "lv"),
        ("Polish", "pl"),
        ("Italian", "it"),
        ("French", "fr"),
        ("Dutch", "nl"),
        ("Portuguese", "pt"),
        ("Estonian", "et"),
        ("Swahili", "sw"),
        ("Thai", "th"),
    ):
        policy = response_language_policy_for_text(f"Answer in {name}: what is a database index?")

        assert policy.expected_language == code, name
        assert policy.reason == "explicit_output_request", name


def test_south_and_southeast_asian_scripts_keep_their_unique_authority() -> None:
    thai = response_language_policy_for_text(
        "กรุณาอธิบายว่าดัชนีฐานข้อมูลทำงานอย่างไรและทำไมการค้นหาจึงเร็วขึ้น"
    )
    assert thai.expected_language == "th"

    amharic = response_language_policy_for_text(
        "እባክህ የመረጃ ቋሚ ማውጫ እንዴት እንደሚሰራ አስረዳኝ እና ለምን ፈጣን ፍለጋ እንደሚያደርግ"
    )
    assert amharic.expected_language == "am"


# ---------------------------------------------------------------------------
# Task routing must be language-fair at its named seams (measured 2026-09-03):
# equivalent repair demands in non-English languages never reached the debugging/code lane
# because the triage keywords and the repo-repair predicate are English-only, and the turn
# planner dropped non-ASCII words from plan verification.
# ---------------------------------------------------------------------------


def test_command_anchor_is_context_never_routing_or_seating_authority() -> None:
    """SUPERSEDED (C18 final correction): 943de55a routed and seated the code-task plane on a
    pasted command alone. A command artifact is code CONTEXT, never action AUTHORITY — the
    corrected law now lives in tests/test_c18_context_is_not_authority.py; this test pins that
    the anchor no longer routes debugging or seats code.task.open in either seam."""
    from core.task_router import classify
    from core.tool_demand_signals import resolve_demand_signals

    lt_demand = (
        "Išsiaiškink, kodėl šis testas nepavyksta (python -m pytest -q), "
        "pataisyk pagrindinę priežastį ir parodyk skirtumą."
    )
    assert classify(lt_demand).get("task_class") != "debugging"
    assert "code.task.open" not in resolve_demand_signals(lt_demand).explicit_intents


def test_untranslated_verb_only_demands_fail_closed_recorded_for_c12() -> None:
    """Measured PARTIAL (C12 owner): without the (rejected) anchor shortcut there is no typed
    execution-demand authority for translated verbs alone — the turn must fail closed, never
    be authorized by artifacts."""
    from core.task_router import classify
    from core.tool_demand_signals import resolve_demand_signals

    lt = "Išsiaiškink, kodėl šis testas nepavyksta, pataisyk pagrindinę priežastį ir parodyk skirtumą."
    result = classify(lt)
    assert result.get("task_class") != "debugging"
    assert "code.task.open" not in resolve_demand_signals(lt).explicit_intents


def test_turn_planner_keeps_non_ascii_words_in_plan_verification() -> None:
    from core.agent_runtime.turn_planner import PlannedTask, _content_words, verify_plan

    lt = "Išsiaiškink, kodėl šis testas nepavyksta ir parodyk skirtumą."
    words = _content_words(lt)

    assert "išsiaiškink" in words and "nepavyksta" in words and "skirtumą" in words
    assert verify_plan([PlannedTask(index=0, request=lt)], lt) is True
