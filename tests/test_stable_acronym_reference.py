from __future__ import annotations

from core.stable_acronym_reference import stable_acronym_reference_response


def test_computing_acronym_set_is_resolved_locally() -> None:
    answer = stable_acronym_reference_response(
        '"The PHP server in BSD is throwing a SOS." What do PHP, BSD, and SOS stand for in computing and networking?'
    )

    assert answer is not None
    assert "Hypertext Preprocessor" in answer
    assert "Berkeley Software Distribution" in answer
    assert "not a standard computing acronym" in answer


def test_mixed_engineering_and_currency_reference_is_resolved_locally() -> None:
    answer = stable_acronym_reference_response(
        '"I need to CAD a design for a new RUB." What do CAD and RUB typically mean in engineering software and global finance?'
    )

    assert answer is not None
    assert "computer-aided design" in answer
    assert "Russian ruble" in answer


def test_plain_mentions_and_single_acronyms_stay_model_owned() -> None:
    assert stable_acronym_reference_response("The PHP server runs BSD.") is None
    assert stable_acronym_reference_response("What does PHP stand for in computing?") is None
