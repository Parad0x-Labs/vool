from __future__ import annotations

import pytest

from core.stable_file_format_standard_reference import (
    FILE_FORMAT_STANDARDS,
    stable_file_format_standard_response,
)


def test_registry_has_versioned_primary_source_provenance() -> None:
    assert {item.format_name for item in FILE_FORMAT_STANDARDS} == {
        "PNG",
        "PDF",
        "JPEG",
        "JPEG 2000",
    }
    assert all(item.canonical_id.startswith("ISO") for item in FILE_FORMAT_STANDARDS)
    assert all(item.publication_year and item.edition for item in FILE_FORMAT_STANDARDS)
    assert all(item.source_url.startswith("https://www.iso.org/standard/") for item in FILE_FORMAT_STANDARDS)
    assert all(item.authority.startswith("ISO") for item in FILE_FORMAT_STANDARDS)


def test_frozen_unseen_iso_15948_membership_returns_only_png() -> None:
    prompt = (
        "Which of PNG, TXT, GIF, and PDF is the format defined by ISO/IEC 15948? "
        "List only actual members of that requested standard; do not infer membership merely "
        "because every item is a file format."
    )

    assert stable_file_format_standard_response(prompt) == "PNG — ISO/IEC 15948:2004."


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            "Which of PNG, PDF, and GIF is the document format defined by ISO 32000-2:2020?",
            "PDF — ISO 32000-2:2020.",
        ),
        (
            "which of png; jpg; or txt is the image format specified by iso-iec 10918-1:1994?",
            "JPEG — ISO/IEC 10918-1:1994.",
        ),
        (
            "Which of JPEG, JP2, PNG, and TIFF is the image format standardized by ISO / IEC 15444?",
            "JPEG 2000 — ISO/IEC 15444-1:2024.",
        ),
        (
            "Which of PDF, PNG, or TXT is the format defined by ISO 32000?",
            "PDF — ISO 32000-2:2020.",
        ),
        (
            "Which of Portable Network Graphics, PDF, and GIF is the file format defined by ISO IEC 15948 : 2004?",
            "PNG — ISO/IEC 15948:2004.",
        ),
        (
            "Which of TXT, JPEG 2000, and BMP are defined by ISO/IEC 15444-1:2024?",
            "JPEG 2000 — ISO/IEC 15444-1:2024.",
        ),
    ),
)
def test_sibling_standards_and_punctuation_variants_use_exact_registry(
    prompt: str,
    expected: str,
) -> None:
    assert stable_file_format_standard_response(prompt) == expected


def test_known_standard_with_only_distractors_returns_explicit_empty_membership() -> None:
    answer = stable_file_format_standard_response(
        "Which of GIF, BMP, and TIFF is the image format defined by ISO/IEC 15948?"
    )

    assert answer == "None of the listed formats — ISO/IEC 15948:2004."


@pytest.mark.parametrize(
    "prompt",
    (
        "Which file format is defined by ISO/IEC 15948?",
        "Tell me everything standardized by ISO 32000.",
        "Which of PNG, GIF, and PDF is the format defined by ISO/IEC 99999?",
        "Which of PNG, GIF, and PDF is the format defined by ISO/IEC 15948:2024?",
        "Which of report.pdf, PNG, and GIF is the format defined by ISO/IEC 15948?",
        "Which of PNG/GIF, TXT, and PDF is the format defined by ISO/IEC 15948?",
        "Which of PNG is the format defined by ISO/IEC 15948?",
        "Which of kg, mol, and flarn are recognized SI unit symbols?",
    ),
)
def test_unknown_unbounded_non_atomic_and_out_of_domain_queries_remain_unclaimed(prompt: str) -> None:
    assert stable_file_format_standard_response(prompt) is None
