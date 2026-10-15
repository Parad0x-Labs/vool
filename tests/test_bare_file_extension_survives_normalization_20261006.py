"""A bare file extension the user types ("as a .md file") reaches the runtime as one token.

Measured live 2026-10-06: "Create a moving-house checklist and hand it to me as a .md file." was
received as "... as a. md file.", the file order went unrecognized, and the reply claimed a Markdown
file that was never made. Names with a stem ("notes.md") were already protected; a bare extension
had no stem, so "." and "md" were split.
"""
from __future__ import annotations

import pytest

from core.human_input_adapter import adapt_user_input
from core.input_normalizer import normalize_user_text


@pytest.mark.parametrize(
    "text,kept",
    [
        ("Create a moving-house checklist and hand it to me as a .md file.", "as a .md file"),
        ("Could you put that table in a .csv for me", "in a .csv for"),
        ("send the config over as a .yaml please", "as a .yaml please"),
        ("a .txt with the lyrics would be great", "a .txt with"),
        ("bundle everything into a .tar.gz archive", "a .tar.gz archive"),
        ("I'd like the report as a .pdf, thanks", "as a .pdf,"),
    ],
)
def test_a_bare_extension_stays_one_token(text, kept):
    assert kept in normalize_user_text(text).normalized_text
    assert kept in adapt_user_input(text, session_id="openclaw:bareextension0001").normalized_text


@pytest.mark.parametrize(
    "text",
    [
        "save it as notes.md",          # a stemmed name, protected before this change too
        "I paid 3.50 for it.",           # a decimal is not an extension
        "see section 2. md is fine",     # a sentence end followed by a word is still prose
        "release 1.2.3 is out",
    ],
)
def test_names_numbers_and_sentence_ends_are_unchanged(text):
    assert normalize_user_text(text).normalized_text == text


@pytest.mark.parametrize("extension", [".css", ".toml", ".jsonl", ".custom", ".CSS", ".3mf", ".žalia", ".tar.zst"])
def test_unlisted_extensions_are_literal_not_capability_grants(extension):
    text = f"Please save the output as a {extension} file."
    kept = f"as a {extension} file"
    assert kept in normalize_user_text(text).normalized_text
    assert kept in adapt_user_input(text, session_id="openclaw:unlistedextension0001").normalized_text
