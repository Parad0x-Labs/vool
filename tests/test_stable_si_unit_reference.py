from __future__ import annotations

import pytest

from core.stable_si_unit_reference import (
    SI_NAMED_UNITS,
    SI_REFERENCE,
    stable_si_unit_reference_response,
)


def test_reference_pack_is_the_complete_versioned_named_si_core() -> None:
    assert SI_REFERENCE.authority == "Bureau International des Poids et Mesures (BIPM)"
    assert SI_REFERENCE.edition == "9th edition"
    assert SI_REFERENCE.version == "4.01"
    assert SI_REFERENCE.publication_year == 2026
    assert len(SI_NAMED_UNITS) == 29
    assert {unit.kind for unit in SI_NAMED_UNITS.values()} == {"base", "derived-special-name"}
    assert {"s", "m", "kg", "A", "K", "mol", "cd"}.issubset(SI_NAMED_UNITS)
    assert {"Hz", "Pa", "Ω", "°C", "Bq", "kat"}.issubset(SI_NAMED_UNITS)


def test_frozen_unseen_membership_question_uses_reference_data_not_guessing() -> None:
    prompt = (
        "Which of kg, zorp, mol, and flarn are recognized SI unit symbols? "
        "Treat invented abbreviations as distractors and do not search the web."
    )

    answer = stable_si_unit_reference_response(prompt)

    assert answer is not None
    assert "kg (kilogram)" in answer
    assert "mol (mole)" in answer
    assert "zorp" in answer and "flarn" in answer
    assert "not SI symbols" in answer
    assert "version 4.01 (2026)" in answer


@pytest.mark.parametrize(
    ("prompt", "admitted", "rejected"),
    (
        (
            "Which of A, splat, K, cd, Hz, and bogus are SI unit symbols?",
            ("A (ampere)", "K (kelvin)", "cd (candela)", "Hz (hertz)"),
            ("splat", "bogus"),
        ),
        (
            "Which of kPa, MHz, mmol, µm, and nope are recognized SI symbols?",
            ("kPa (prefixed pascal)", "MHz (prefixed hertz)", "mmol (prefixed mole)", "µm (prefixed metre)"),
            ("nope",),
        ),
        (
            "Which of Hz, hz, Pa, and pa are recognized SI unit symbols?",
            ("Hz (hertz)", "Pa (pascal)"),
            ("hz", "pa"),
        ),
    ),
)
def test_membership_is_generic_prefix_aware_and_case_sensitive(
    prompt: str,
    admitted: tuple[str, ...],
    rejected: tuple[str, ...],
) -> None:
    answer = stable_si_unit_reference_response(prompt)

    assert answer is not None
    for item in admitted:
        assert item in answer
    for item in rejected:
        assert item in answer


@pytest.mark.parametrize(
    "prompt",
    (
        "I measured 2 kg and 3 mol in the lab.",
        "What is the complete list of SI unit symbols?",
        "Which of m/s and kg/m3 are SI unit symbols?",
        "Which of PNG, TXT, GIF, and PDF is defined by ISO/IEC 15948?",
    ),
)
def test_non_membership_or_non_atomic_questions_remain_unclaimed(prompt: str) -> None:
    assert stable_si_unit_reference_response(prompt) is None
