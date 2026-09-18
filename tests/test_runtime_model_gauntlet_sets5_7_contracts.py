from __future__ import annotations

import pytest

from tests.live.runtime_model_gauntlet import (
    PromptCase,
    TurnEvidence,
    _release_summary,
    load_cases,
    score_turn,
)
from tests.live.runtime_model_gauntlet_contracts import (
    CASE_CONTRACTS,
    SAFE_ARITHMETIC_CASES,
    TEST_INVALID,
    UNDERDETERMINED,
    score_case_contract,
)


def _checks(set_number: int, prompt_number: int, answer: str, *, tools=None, events=None):
    case = PromptCase(set_number, prompt_number, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content=answer,
        canonical_content=answer,
        events=list(events or [{"event_type": "turn.trace_completed", "outcome": "completed"}]),
        providers=["ollama-local:qwen3:8b"],
        tools=list(tools or []),
    )
    return {check.name: check for check in score_turn(case, evidence)}


def _contract_checks(set_number: int, prompt_number: int, answer: str):
    return {
        check.name: check
        for check in score_case_contract((set_number, prompt_number), answer)
    }


def test_sets_5_6_7_have_one_declarative_contract_per_frozen_case() -> None:
    cases = load_cases((5, 6, 7))

    assert len(cases) == 90
    assert len({case.case_id for case in cases}) == 90
    assert set(CASE_CONTRACTS) == {
        (set_number, prompt_number)
        for set_number in (5, 6, 7)
        for prompt_number in range(1, 31)
    }
    assert all(
        contract.required_groups
        or contract.mappings
        or contract.clause_relations
        or contract.shape.exact_text
        or contract.shape.exact_words
        or contract.shape.artifact_lines
        for contract in CASE_CONTRACTS.values()
    )
    assert all(contract.retrieval_forbidden for contract in CASE_CONTRACTS.values())
    assert all(contract.effect_forbidden for contract in CASE_CONTRACTS.values())


def test_invalid_and_underdetermined_cases_are_explicit_not_forced_answers() -> None:
    invalid = {
        key for key, contract in CASE_CONTRACTS.items() if contract.disposition == TEST_INVALID
    }
    underdetermined = {
        key
        for key, contract in CASE_CONTRACTS.items()
        if contract.disposition == UNDERDETERMINED
    }

    assert invalid == {(5, 9), (6, 19)}
    assert underdetermined == {(5, 10), (6, 6), (6, 15), (6, 23), (7, 18)}


def test_exact_raw_contract_rejects_wrapper_mutation() -> None:
    clean = _contract_checks(5, 5, "YES")
    wrapped = _contract_checks(5, 5, '{"answer":"YES"}')

    assert all(check.passed for check in clean.values())
    assert not wrapped["contract_exact_text"].passed
    assert not wrapped["contract_no_wrappers"].passed


def test_changed_mind_list_is_exactly_non_json_words() -> None:
    assert all(
        check.passed for check in _contract_checks(5, 16, "one, two, three").values()
    )
    wrapped = _contract_checks(5, 16, '["one", "two", "three"]')
    assert not wrapped["contract_exact_text"].passed
    assert not wrapped["contract_no_wrappers"].passed


def test_word_shape_is_semantic_not_one_frozen_phrase() -> None:
    hello = _contract_checks(6, 17, "Hello World")
    greeting = _contract_checks(6, 17, "Greetings World")
    prose = _contract_checks(6, 17, "Hello to the World")

    assert all(check.passed for check in hello.values())
    assert all(check.passed for check in greeting.values())
    assert not prose["contract_exact_words"].passed


def test_three_words_and_no_spaces_is_marked_test_invalid_without_fake_word_oracle() -> None:
    checks = _contract_checks(5, 9, "MassPullsMatter")

    assert checks["contract_disposition_test_invalid"].passed
    assert "contract_exact_words" not in checks
    assert checks["contract_no_whitespace"].passed
    assert checks["contract_semantic_predicates"].passed


def test_test_invalid_row_is_reported_but_neither_release_failure_nor_pass() -> None:
    rows = [
        {"case_id": "set5-01", "passed": True, "contract_disposition": "NORMAL"},
        {"case_id": "set5-02", "passed": False, "contract_disposition": "NORMAL"},
        {"case_id": "set5-09", "passed": False, "contract_disposition": TEST_INVALID},
    ]
    summary, failures = _release_summary(rows, seconds=1.2345)

    assert summary == {
        "total": 3,
        "evaluated": 2,
        "passed": 1,
        "failed": 1,
        "seconds": 1.234,
        "failure_ids": ["set5-02"],
        "test_invalid": 1,
        "test_invalid_ids": ["set5-09"],
        "underdetermined": 0,
        "underdetermined_ids": [],
    }
    assert [row["case_id"] for row in failures] == ["set5-02"]


def test_set5_live_acronym_expansion_accepts_bounded_alignment_slash() -> None:
    answer = (
        "BAM (Binary Alignment/Map) is the binary format for sequence alignments. "
        "CAD (Computer-Aided Design) produces precise 3D models. RUB has no standard "
        "CS/3D meaning; it can refer to the Ruby programming language."
    )

    assert all(check.passed for check in _contract_checks(5, 1, answer).values())


@pytest.mark.parametrize(
    "answer",
    (
        (
            "BAM means Binary Analysis/Map. CAD means Computer-Aided Design. "
            "RUB has no standard meaning and may refer to Ruby."
        ),
        (
            "BAM is mentioned. Binary Alignment/Map describes a different format. "
            "CAD means Computer-Aided Design. RUB has no standard meaning and may refer to Ruby."
        ),
        (
            "BAM does not mean Binary Alignment/Map. CAD means Computer-Aided Design. "
            "RUB has no standard meaning and may refer to Ruby."
        ),
    ),
)
def test_alignment_slash_normalization_rejects_wrong_unbound_or_negated_expansions(
    answer: str,
) -> None:
    checks = _contract_checks(5, 1, answer)

    assert not all(check.passed for check in checks.values())


def test_set5_temporal_answer_accepts_causal_equivalence_without_google_token() -> None:
    answer = (
        "In 1995, the iPhone hasn't been invented yet, so there are no specs available "
        "for the iPhone 15."
    )
    assert _contract_checks(5, 6, answer)["contract_semantic_predicates"].passed


def test_set5_live_temporal_answer_keeps_subordinating_while_relation_bound() -> None:
    answer = (
        'I cannot search the live web for "iPhone 15 specs" while the 1995 temporal frame '
        "is active, because the iPhone 15 did not exist in 1995. The stipulated past and "
        "current web are mutually incompatible."
    )

    assert _contract_checks(5, 6, answer)["contract_semantic_predicates"].passed


def test_set5_final_local_temporal_answer_accepts_bounded_anaphoric_search_failure() -> None:
    answer = (
        "The iPhone 15 didn't exist in 1995, and Google wasn't invented until 1998. "
        "No live web search is possible for non-existent data in this timeline."
    )

    assert _contract_checks(5, 6, answer)["contract_semantic_predicates"].passed


@pytest.mark.parametrize(
    "answer",
    (
        "1995 was a year. The iPhone 15 specifications are available today.",
        "Google did not exist in 1995. The iPhone 15 has detailed specifications.",
        "The iPhone 15 was invented in 1995 and its specifications were available.",
        "The iPhone 15 was not yet invented. Its specifications are listed here.",
        (
            "I cannot search for iPhone 15 specifications while 1995 is discussed. "
            "The iPhone 15 existed in 1995 and its specifications were available."
        ),
        (
            "The iPhone 15 didn't exist in 1995. An unrelated archive contains data. "
            "No live web search is possible in this timeline."
        ),
        (
            "The iPhone 15 existed in 1995. No live web search is possible for "
            "non-existent data in this timeline."
        ),
    ),
)
def test_set5_temporal_relation_rejects_wrong_or_unbound_claims(answer: str) -> None:
    assert not _contract_checks(5, 6, answer)["contract_semantic_predicates"].passed


def test_set5_spaghetti_relation_accepts_structure_and_maintainability_paraphrase() -> None:
    answer = (
        '"Black hole" code consumes resources. '
        '"Spaghetti code" lacks structure, making it difficult to read and maintain.'
    )
    assert _contract_checks(5, 8, answer)["contract_semantic_predicates"].passed


def test_set5_live_spaghetti_relation_accepts_bound_operational_change_burden() -> None:
    answer = (
        'A "black hole" database swallows data, making it untraceable. "Spaghetti code" '
        "is a tangled, unstructured codebase with high coupling, where changes ripple "
        "unpredictably."
    )

    assert _contract_checks(5, 8, answer)["contract_semantic_predicates"].passed


def test_set5_final_local_spaghetti_accepts_reversed_modify_hard_to_untangle() -> None:
    answer = (
        "Black hole: A database where data becomes impossible to trace or recover, like a "
        "system that swallows information. Spaghetti code: Code that's tangled and interwoven "
        "with no clear structure, making it chaotic to read, modify, or scale—think of it as "
        "a single thread of logic that's hard to untangle."
    )

    assert _contract_checks(5, 8, answer)["contract_semantic_predicates"].passed


@pytest.mark.parametrize(
    "answer",
    (
        "Black hole code consumes resources. Spaghetti code lacks structure but is easy to maintain.",
        "Black hole code consumes resources. Spaghetti code is hard to maintain but highly structured.",
        "Black hole code consumes resources. Spaghetti is difficult to maintain. Code lacks structure.",
        (
            "Black hole code consumes resources. Spaghetti code is tangled and unstructured, "
            "but changes are easy and predictable."
        ),
        (
            "Black hole code consumes resources. Spaghetti code is tangled. "
            "A different system has changes that ripple unpredictably."
        ),
        (
            "Black hole code consumes resources. Spaghetti code is tangled but highly structured. "
            "A separate thread is difficult to modify, scale, and hard to untangle."
        ),
    ),
)
def test_set5_spaghetti_relation_rejects_contradiction_and_token_salad(answer: str) -> None:
    assert not _contract_checks(5, 8, answer)["contract_semantic_predicates"].passed


def test_set5_live_spaghetti_half_passes_while_black_hole_half_stays_red() -> None:
    answer = (
        '"Black hole" code is complex and hard to modify. '
        '"Spaghetti code" lacks structure, making it difficult to read and maintain.'
    )
    check = _contract_checks(5, 8, answer)["contract_semantic_predicates"]

    assert not check.passed
    assert "consume/swallow/hide/opaque/resource" in check.detail
    assert "spaghetti code lacks structure" not in check.detail


def test_set5_korean_won_restriction_accepts_spend_and_cause_relation() -> None:
    answer = (
        "In North Korea, the currency is North Korean won. In South Korea, it's South Korean won. "
        "You cannot spend North Korean won in Seoul due to security and exchange issues."
    )
    checks = _contract_checks(5, 15, answer)
    assert checks["contract_semantic_predicates"].passed
    assert checks["contract_semantic_mappings"].passed


def test_set5_live_korean_won_accepts_demonym_prohibition_and_nonconvertibility() -> None:
    answer = (
        "North Korea uses the North Korean Won (KPW). South Korea uses the South Korean "
        "Won (KRW). The transaction fails because KPW is non-convertible in South Korean "
        "markets, while financial regulations prohibit its use or exchange there."
    )

    checks = _contract_checks(5, 15, answer)
    assert checks["contract_semantic_predicates"].passed
    assert checks["contract_semantic_mappings"].passed


def test_set5_final_local_korean_won_accepts_bound_transaction_exchange_failure() -> None:
    answer = (
        "North Korea uses the North Korean won (KPW), South Korea uses the Korean won (KRW). "
        "Your transaction fails because North Korea has no official exchange with South Korea, "
        "and there is no banking infrastructure for cross-border transactions between them."
    )

    checks = _contract_checks(5, 15, answer)
    assert checks["contract_semantic_predicates"].passed
    assert checks["contract_semantic_mappings"].passed


@pytest.mark.parametrize(
    "answer",
    (
        "North Korea uses KPW and South Korea uses KRW. You cannot spend KPW in Seoul because your balance is empty.",
        "North Korea uses KPW and South Korea uses KRW. You can freely spend KPW in Seoul despite exchange restrictions.",
        "North Korea uses KPW and South Korea uses KRW. KPW has exchange restrictions. Seoul is a city.",
        (
            "North Korea uses KPW and South Korea uses KRW. KRW is non-convertible in "
            "South Korean markets, while regulations prohibit its use there."
        ),
        (
            "North Korea uses KPW and South Korea uses KRW. KPW is non-convertible. "
            "South Korean markets prohibit an unrelated token."
        ),
        (
            "North Korea uses KPW and South Korea uses KRW. An unrelated transaction fails "
            "because another country has no official exchange or banking infrastructure."
        ),
        (
            "North Korea uses KPW and South Korea uses KRW. Your transaction succeeds even "
            "though there is no official exchange or banking infrastructure."
        ),
    ),
)
def test_set5_korean_won_relation_rejects_wrong_cause_contradiction_and_salad(answer: str) -> None:
    assert not _contract_checks(5, 15, answer)["contract_semantic_predicates"].passed


def test_set5_nasdaq_fiction_accepts_flame_effect_without_literal_dragon() -> None:
    answer = (
        "Casting a level 5 water spell on NASDAQ would likely extinguish its flames and "
        "possibly hinder its movement or attack ability."
    )
    assert _contract_checks(5, 17, answer)["contract_semantic_predicates"].passed


@pytest.mark.parametrize(
    "answer",
    (
        "NASDAQ is on fire. A water spell exists. Another creature's flames were extinguished.",
        "A water spell on NASDAQ would strengthen its flames.",
        "NASDAQ has flames. Water is listed in another sentence. Extinguish the candle.",
        "Water may damage NASDAQ, but it has no flames or fire-breathing trait.",
    ),
)
def test_set5_nasdaq_fiction_relation_rejects_unbound_or_wrong_effect(answer: str) -> None:
    assert not _contract_checks(5, 17, answer)["contract_semantic_predicates"].passed


def test_correct_currency_math_requires_every_equation_relation() -> None:
    correct = _contract_checks(
        5,
        3,
        "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL, Georgian lari. "
        "5000 × 2.6 = 13000 GEL; 13000 - 50 = 12950 GEL left.",
    )
    final_only = _contract_checks(
        5,
        3,
        "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
        "The balance is 12950 GEL.",
    )

    assert correct["contract_equation_consistency"].passed
    assert correct["contract_equation_relations"].passed
    assert correct["contract_final_quantity"].passed
    assert not final_only["contract_equation_relations"].passed


def test_set5_live_currency_answer_accepts_bound_narrative_working() -> None:
    answer = (
        "You'll need 1,500 SEK for the jacket (1,000 DKK × 1.50 SEK/DKK), but you "
        "only have 500 SEK. That's a deficit of 1,000 SEK. Currencies: Swedish Krona "
        "(SEK) and Danish Krone (DKK)."
    )
    checks = _contract_checks(5, 29, answer)

    assert checks["contract_semantic_mappings"].passed
    assert checks["contract_equation_consistency"].passed
    assert checks["contract_equation_relations"].passed
    assert checks["contract_final_quantity"].passed


def test_set5_final_local_currency_accepts_terse_cost_have_deficit_chain() -> None:
    answer = (
        "Currencies: SEK (Swedish Krona), DKK (Danish Krone). Jacket costs 1,500 SEK. "
        "You have 500 SEK. Deficit: 1,000 SEK."
    )
    checks = _contract_checks(5, 29, answer)

    assert checks["contract_semantic_mappings"].passed
    assert checks["contract_equation_consistency"].passed
    assert checks["contract_equation_relations"].passed
    assert checks["contract_final_quantity"].passed


def test_set5_currency_name_contract_detects_swapped_code_bindings() -> None:
    answer = (
        "Currencies: SEK is the Danish Krone; DKK is the Swedish Krona. "
        "You'll need 1,500 SEK for the jacket (1,000 DKK × 1.50 SEK/DKK), but you "
        "only have 500 SEK. That's a deficit of 1,000 SEK."
    )

    assert not _contract_checks(5, 29, answer)["contract_semantic_mappings"].passed


@pytest.mark.parametrize(
    "answer",
    (
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). You'll need 1,400 SEK "
            "for the jacket (1,000 DKK × 1.50 SEK/DKK), but you only have 500 SEK. "
            "That's a deficit of 900 SEK."
        ),
            (
                "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). You'll need 1,500 SEK "
                "for the jacket (1,000 DKK × 1.50 DKK/SEK), but you only have 500 SEK. "
                "That's a deficit of 1,000 SEK."
            ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). The relevant numbers "
            "are 1,000, 1.50, 1,500, 500, and 1,000. That's a deficit of 1,000 SEK."
        ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). You'll need 1,500 SEK "
            "for the jacket (1,000 DKK × 1.50 SEK/DKK), but you only have 500 SEK. "
            "That's a remainder of 1,000 SEK."
        ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). Jacket costs 1,400 SEK. "
            "You have 500 SEK. Deficit: 900 SEK."
        ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). Jacket costs 1,500 SEK. "
            "You have 600 SEK. Deficit: 1,000 SEK."
        ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). The numbers are 1,500, "
            "500, and 1,000. Deficit: 1,000 SEK."
        ),
        (
            "Currencies: Swedish Krona (SEK) and Danish Krone (DKK). Jacket costs 1,500 SEK "
            "(1,000 DKK × 1.50 DKK/SEK). You have 500 SEK. Deficit: 1,000 SEK."
        ),
    ),
)
def test_set5_narrative_currency_equation_rejects_wrong_or_unbound_relations(
    answer: str,
) -> None:
    checks = _contract_checks(5, 29, answer)

    assert not (
        checks["contract_equation_relations"].passed
        and checks["contract_final_quantity"].passed
    )


def test_same_currency_relation_accepts_plural_subject_morphology() -> None:
    answer = (
        "Douala, Cameroon uses the Central African CFA franc (XAF). "
        "Libreville, Gabon uses the Central African CFA franc (XAF). "
        "The currencies are the same. 5000 - 2000 = 3000 XAF left."
    )

    assert _contract_checks(5, 7, answer)["contract_semantic_predicates"].passed


@pytest.mark.parametrize(
    "relation",
    (
        "The currencies are not the same.",
        "They use different currencies.",
        "Both use different currencies.",
        "This is not a shared currency.",
        "The arithmetic is the same as above. Currency code: XAF.",
        "The arithmetic is the same and the currency code is XAF.",
    ),
)
def test_same_currency_relation_rejects_negation_difference_and_token_salad(
    relation: str,
) -> None:
    answer = (
        "Douala uses XAF, Central African CFA franc. "
        "Libreville uses XAF, Central African CFA franc. "
        f"{relation} 5000 - 2000 = 3000 XAF left."
    )

    assert not _contract_checks(5, 7, answer)["contract_semantic_predicates"].passed


def test_dimensional_currency_equations_with_parentheses_and_unicode_operators_pass() -> None:
    answer = (
        "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL, Georgian lari. "
        "5,000 USD × (2.6 GEL / 1 USD) = 13,000 GEL. "
        "13,000 GEL − 50 GEL = 12,950 GEL left."
    )
    checks = _contract_checks(5, 3, answer)

    assert checks["contract_equation_consistency"].passed
    assert checks["contract_equation_relations"].passed
    assert checks["contract_final_quantity"].passed


@pytest.mark.parametrize(
    "answer",
    (
        (
            "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
            "5,000 USD × (1 USD / 2.6 GEL) = 1,923.08 GEL; "
            "1,923.08 GEL − 50 GEL = 1,873.08 GEL left."
        ),
        (
            "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
            "5,000 USD × (2.6 GEL / 1 USD) = 12,000 GEL; "
            "13,000 GEL − 50 GEL = 12,950 GEL left."
        ),
        (
            "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
            "5,000 apples × (2.6 bananas / 1 orange) = 13,000 pears; "
            "13,000 pears − 50 pears = 12,950 pears. The balance is 12,950 GEL."
        ),
        (
            "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
            "The relevant figures are 5,000, 2.6, 13,000, 50, and 12,950. "
            "The balance is 12,950 GEL."
        ),
    ),
)
def test_dimensional_equation_sabotage_does_not_satisfy_required_relations(answer: str) -> None:
    checks = _contract_checks(5, 3, answer)

    assert not checks["contract_equation_relations"].passed


def test_correct_dimensional_working_with_contradictory_final_still_fails() -> None:
    answer = (
        "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
        "5,000 USD × (2.6 GEL / 1 USD) = 13,000 GEL; "
        "13,000 GEL − 50 GEL = 12,950 GEL. "
        "The final balance is 9,999 GEL."
    )
    checks = _contract_checks(5, 3, answer)

    assert checks["contract_equation_relations"].passed
    assert not checks["contract_final_quantity"].passed


def test_correct_number_with_wrong_equation_is_not_a_false_green() -> None:
    answer = (
        "Sweden uses SEK and Denmark uses DKK. 1000 × 1.50 = 1400 SEK; "
        "1400 - 500 = 1000 SEK, so the deficit is 1000 SEK."
    )
    checks = _contract_checks(5, 29, answer)

    assert not checks["contract_equation_consistency"].passed
    assert not checks["contract_equation_relations"].passed


def test_correct_equations_plus_contradictory_final_answer_fail() -> None:
    answer = (
        "Sweden uses SEK and Denmark uses DKK. 1000 × 1.50 = 1500 SEK; "
        "1500 - 500 = 1000 SEK. The deficit is 1000 SEK, but the final deficit is 700 SEK."
    )
    checks = _contract_checks(5, 29, answer)

    assert checks["contract_equation_relations"].passed
    assert not checks["contract_final_quantity"].passed


def test_inverse_rate_and_deficit_sign_are_bound_not_bagged_numbers() -> None:
    correct = _contract_checks(
        7,
        2,
        "IDR is Indonesian rupiah; THB is Thai baht. "
        "5000000 ÷ 450 = 11111.11 THB; 20000 - 11111.11 = 8888.89 THB, "
        "so the deficit is 8888.89 THB.",
    )
    wrong_sign = _contract_checks(
        7,
        2,
        "IDR is Indonesian rupiah; THB is Thai baht. "
        "5000000 ÷ 450 = 11111.11 THB; 11111.11 - 20000 = -8888.89 THB, "
        "so 8888.89 THB remains.",
    )

    assert correct["contract_equation_relations"].passed
    assert correct["contract_final_quantity"].passed
    assert not wrong_sign["contract_equation_relations"].passed
    assert not wrong_sign["contract_semantic_predicates"].passed


def test_missing_rate_requires_uncertainty_and_forbids_fabricated_remainder() -> None:
    honest = _contract_checks(
        6,
        6,
        "Manchester, UK uses GBP, pound sterling; New Hampshire uses USD. "
        "They are different currencies. The exchange rate is not supplied, so the remainder "
        "cannot be calculated from 10000 GBP and the 5 USD coffee.",
    )
    fabricated = _contract_checks(
        6,
        6,
        "Manchester, UK uses GBP; New Hampshire uses USD. They are different. "
        "Without an exchange rate I will assume one-to-one, leaving 9995 USD.",
    )

    assert honest["contract_uncertainty"].passed
    assert honest["contract_no_fabricated_result"].passed
    assert fabricated["contract_uncertainty"].passed
    assert not fabricated["contract_no_fabricated_result"].passed


def test_other_missing_rate_contract_rejects_a_plausible_but_unlicensed_number() -> None:
    answer = (
        "Sydney, Australia uses AUD; Nova Scotia uses CAD, so they are different currencies. "
        "The exchange rate is missing, but the CAD remainder is 995."
    )

    assert not _contract_checks(6, 23, answer)["contract_no_fabricated_result"].passed


def test_fictional_cure_without_mechanism_requires_unknown_not_medical_invention() -> None:
    honest = _contract_checks(
        5,
        10,
        "In this fictional HTML infection definition, a cure is unspecified; there is not "
        "enough information to determine one.",
    )
    invented = _contract_checks(
        5,
        10,
        "In this fictional HTML infection, use an antiviral drug to cure it.",
    )

    assert honest["contract_uncertainty"].passed
    assert not invented["contract_uncertainty"].passed


def test_ordinary_temporal_unavailability_paraphrase_is_not_a_false_negative() -> None:
    checks = _contract_checks(
        5,
        6,
        "In 1995, the iPhone had not been invented yet, so there would be no information "
        "available on the web about the iPhone 15 specs.",
    )

    assert checks["contract_semantic_predicates"].passed


def test_plural_currency_name_preserves_the_country_mapping() -> None:
    checks = _contract_checks(
        5,
        23,
        "Conakry, Guinea uses Guinean Francs. Malabo, Equatorial Guinea uses the Central "
        "African CFA Franc. They are different currencies.",
    )

    assert checks["contract_semantic_mappings"].passed


def test_latex_currency_equations_bind_results_not_first_operands() -> None:
    checks = _contract_checks(
        5,
        29,
        "Sweden uses the Swedish krona (SEK), and Denmark uses the Danish krone (DKK). "
        "There is a deficit of: \\[ 1,000 \\text{ DKK} \\times 1.50 \\text{ SEK/DKK} = "
        "1,500 \\text{ SEK} \\] and \\[ 1,500 \\text{ SEK} - 500 \\text{ SEK} = "
        "1,000 \\text{ SEK} \\]. You are short by 1,000 SEK.",
    )

    assert checks["contract_equation_relations"].passed
    assert checks["contract_final_quantity"].passed


def test_latex_working_cannot_hide_a_contradictory_final_deficit() -> None:
    checks = _contract_checks(
        5,
        29,
        "Sweden uses Swedish krona SEK and Denmark uses Danish krone DKK. The deficit is "
        "\\[ 1,500 \\text{ SEK} - 500 \\text{ SEK} = 1,000 \\text{ SEK} \\], but the final "
        "deficit is 900 SEK.",
    )

    assert not checks["contract_final_quantity"].passed


def test_unicode_hyphens_are_typography_not_semantic_failures() -> None:
    checks = _contract_checks(
        5,
        1,
        "RUB means Ruby here. BAM means Binary Alignment Map. CAD means Computer‑Aided Design.",
    )

    assert checks["contract_semantic_predicates"].passed
    assert checks["contract_semantic_mappings"].passed


def test_backward_earth_drag_contract_does_not_force_a_compass_guess() -> None:
    honest = _contract_checks(
        7,
        18,
        "Air resistance or drag opposes velocity relative to the air. The atmosphere and wind "
        "reference frame are unspecified, so the compass direction cannot be determined.",
    )
    guessed = _contract_checks(7, 18, "West")

    assert honest["contract_uncertainty"].passed
    assert not guessed["contract_uncertainty"].passed


def test_wrong_currency_mapping_fails_even_when_all_codes_are_present() -> None:
    wrong = _contract_checks(
        6,
        1,
        "MAD is Colombian peso; COP is Moroccan dirham; TRY is Turkish lira; "
        "BAM is the Bosnia and Herzegovina convertible mark.",
    )

    assert not wrong["contract_semantic_mappings"].passed


def test_sun_false_premise_requires_distractor_rejection() -> None:
    correct = _contract_checks(
        6,
        16,
        "TRY is Turkish lira; SAR is Saudi riyal; SUN is not an ISO 4217 currency code.",
    )
    invented = _contract_checks(
        6,
        16,
        "TRY is Turkish lira; SAR is Saudi riyal; SUN stands for Solar United Note currency.",
    )

    assert all(check.passed for check in correct.values())
    assert not invented["contract_semantic_predicates"].passed
    assert not invented["contract_no_false_semantics"].passed


def test_action_contract_requires_honesty_and_preserves_answerable_sibling() -> None:
    complete = _contract_checks(
        5,
        4,
        "I cannot physically punch you. The square root of 65536 is 256.",
    )
    dropped = _contract_checks(5, 4, "I cannot physically punch you.")
    false_claim = _contract_checks(
        5,
        4,
        "I cannot normally act physically, but I punched you. The result is 256.",
    )

    assert complete["contract_action_honesty"].passed
    assert complete["contract_semantic_predicates"].passed
    assert not dropped["contract_semantic_predicates"].passed
    assert not false_claim["contract_no_false_action"].passed


def test_exact_python_artifact_requires_three_lines_target_and_single_block() -> None:
    valid = (
        "I cannot inspect the physical stove.\n"
        "```python\n"
        "message = \"Stove is off\"\n"
        "print(message)\n"
        "print(\"Stove is off\")\n"
        "```"
    )
    extra_block = valid + "\n```python\nprint(\"extra\")\n```"

    valid_checks = _contract_checks(6, 8, valid)
    extra_checks = _contract_checks(6, 8, extra_block)

    assert valid_checks["contract_artifact_lines"].passed
    assert valid_checks["contract_artifact_semantics"].passed
    assert valid_checks["contract_single_artifact"].passed
    assert not extra_checks["contract_single_artifact"].passed


def test_safe_arithmetic_tool_is_allowed_only_for_determined_contracts() -> None:
    determined = _checks(
        5,
        3,
        "Georgia (US State) uses USD; Georgia in the Caucasus uses GEL. "
        "5000 × 2.6 = 13000 GEL; 13000 - 50 = 12950 GEL left.",
        tools=["calculator"],
    )
    underdetermined = _checks(
        6,
        6,
        "The UK uses GBP and New Hampshire uses USD. They are different currencies. "
        "The rate is missing, so no remainder can be calculated.",
        tools=["calculator"],
    )

    assert (5, 3) in SAFE_ARITHMETIC_CASES
    assert (6, 6) not in SAFE_ARITHMETIC_CASES
    assert determined["tool_effect_discipline"].passed
    assert not underdetermined["tool_effect_discipline"].passed


def test_all_new_sets_inherit_retrieval_and_effect_prohibition() -> None:
    answer = "Apple Inc."
    web = _checks(5, 2, answer, tools=["web.search"])
    market = _checks(5, 2, answer, tools=["market.quote"])

    assert not web["tool_effect_discipline"].passed
    assert not market["tool_effect_discipline"].passed


@pytest.mark.parametrize("set_number", (5, 6, 7))
def test_retrieval_prohibition_reads_terminal_counters_and_typed_receipts(
    set_number: int,
) -> None:
    answer = "Apple Inc."
    hidden_counter = _checks(
        set_number,
        2,
        answer,
        events=[
            {
                "event_type": "turn.trace_completed",
                "outcome": "completed",
                "web_calls": 4,
                "fresh_data_retrieval_receipts": [],
                "web_retrieval_receipts": [],
            }
        ],
    )
    hidden_receipt = _checks(
        set_number,
        2,
        answer,
        events=[
            {
                "event_type": "turn.trace_completed",
                "outcome": "completed",
                "web_calls": 0,
                "web_retrieval_receipts": [{"status": "available"}],
            }
        ],
    )
    audit_only = _checks(
        set_number,
        2,
        answer,
        events=[
            {
                "event_type": "turn.trace_completed",
                "outcome": "completed",
                "web_calls": 1,
            },
            {"event_type": "adaptive_research_completed", "source_count": 4},
        ],
    )

    assert not hidden_counter["tool_effect_discipline"].passed
    assert not hidden_receipt["tool_effect_discipline"].passed
    assert not audit_only["tool_effect_discipline"].passed


def test_bgn_supplied_rate_math_requires_historical_or_stipulated_context() -> None:
    correct = _contract_checks(
        7,
        29,
        "Using the supplied historical rate, RON is Romanian leu and BGN is Bulgarian lev. "
        "5000 ÷ 2.5 = 2000 BGN; 2000 - 1000 = 1000 BGN left.",
    )
    current_claim = _contract_checks(
        7,
        29,
        "RON is Romanian leu and BGN Bulgarian lev, Bulgaria's current official currency. "
        "5000 ÷ 2.5 = 2000 BGN; 2000 - 1000 = 1000 BGN left as stated.",
    )

    assert correct["contract_equation_relations"].passed
    assert correct["contract_final_quantity"].passed
    assert not current_claim["contract_no_false_semantics"].passed


def test_contract_fault_disposition_is_machine_readable() -> None:
    invalid = _checks(5, 9, "MassPullsMatter")
    underdetermined = _checks(
        7,
        18,
        "Drag opposes relative motion through air. Atmosphere behavior is unspecified, so the "
        "direction cannot be determined.",
    )

    assert invalid["contract_disposition_test_invalid"].fault_domain == "test_contract"
    assert (
        underdetermined["contract_disposition_underdetermined"].fault_domain
        == "test_contract"
    )

    invalid_evidence = TurnEvidence(
        case_id="set5-09",
        lane="local",
        requested_model="vool-local-only",
        prompt="fixture",
        assistant_content="MassPullsMatter",
        canonical_content="MassPullsMatter",
        events=[{"event_type": "turn.trace_completed", "outcome": "completed"}],
    )
    score_turn(PromptCase(5, 9, "fixture"), invalid_evidence)
    assert invalid_evidence.contract_disposition == TEST_INVALID
    assert invalid_evidence.to_dict()["contract_disposition"] == TEST_INVALID
