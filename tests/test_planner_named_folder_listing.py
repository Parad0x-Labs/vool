"""The listing planner must not collapse "my X folder on the desktop" to the whole Desktop.

Second live instance of the audit's keyword-in-a-phrase bug class. The first matched "desktop"
inside a typed-out path; this one matched it inside prose: `_extract_safe_machine_directory_listing`
fell back to `"desktop" in lowered → path = "~/Desktop"`, so "show me the files in my garden folder
on the desktop" listed the entire Desktop and threw "my garden folder" away. Traced live
2026-07-28 with zero model calls — the deterministic direct-render branch presented the wrong
folder's contents as the answer.

The fix resolves the named child among the root's direct children, per meaningful word, compact and
separator-insensitive ("garden" finds `my-garden-folder`). Only a UNIQUE match changes behaviour;
zero or several keep the root listing, so "what's on my desktop" is untouched and an ambiguous name
degrades to today's answer rather than guessing.

The stopword regression here is real: "what ARE the folders on my desktop" once left "are" as the
only meaningful token, and the 3-letter substring probe matched it inside an unrelated directory
(NULL-ARE-NTED), hijacking a plain root listing. Function words are stopwords now; that case is
pinned below.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.execution.planner import (
    _extract_safe_machine_directory_listing,
    _named_child_within,
)


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a Desktop the probe can scan, isolated from the real machine."""

    desktop = tmp_path / "Desktop"
    (desktop / "my-garden-folder").mkdir(parents=True)
    (desktop / "unrelated-project").mkdir()
    (desktop / "VOOL_RENTED_AB").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _path(text: str) -> str | None:
    extracted = _extract_safe_machine_directory_listing(text)
    return None if extracted is None else extracted["path"]


# --------------------------------------------------------------------------------------
# The measured failures
# --------------------------------------------------------------------------------------


def test_a_named_folder_beats_the_location_word(fake_home: Path) -> None:
    assert _path("show me the files in my garden folder on the desktop") == "~/Desktop/my-garden-folder"


def test_the_peek_phrasing_resolves_despite_stray_verbs(fake_home: Path) -> None:
    """"peek" and "tell" are not folder names; they must match no child and drop out."""

    extracted = _extract_safe_machine_directory_listing(
        "peek inside my garden folder on the desktop and tell me whats there"
    )
    assert extracted is not None
    assert extracted["path"] == "~/Desktop/my-garden-folder"
    # The " folder" token named the target — it did not ask for a folders-only view.
    assert extracted["directories_only"] is False


# --------------------------------------------------------------------------------------
# Behaviour that must NOT change
# --------------------------------------------------------------------------------------


def test_a_plain_root_listing_is_untouched(fake_home: Path) -> None:
    assert _path("show me the files on my desktop") == "~/Desktop"


def test_function_words_cannot_hijack_the_root_listing(fake_home: Path) -> None:
    """The NULL-ARE-NTED regression: "are" must never resolve a folder."""

    assert _path("what are the folders and files on my desktop?") == "~/Desktop"


def test_a_typed_path_still_wins_over_everything(fake_home: Path) -> None:
    assert _path("peek into ~/Desktop/my-garden-folder and list what is stored") == "~/Desktop/my-garden-folder"


def test_an_unknown_name_keeps_the_root_listing(fake_home: Path) -> None:
    """Zero matches must degrade to today's behaviour, never invent a folder."""

    assert _path("show me the files in my zeppelin folder on the desktop") == "~/Desktop"


def test_an_ambiguous_name_keeps_the_root_listing(fake_home: Path, tmp_path: Path) -> None:
    (tmp_path / "Desktop" / "garden-two").mkdir()
    assert _path("show me the files in my garden folder on the desktop") == "~/Desktop"


def test_other_roots_still_extract(fake_home: Path) -> None:
    assert _path("list my downloads") == "~/Downloads"


# --------------------------------------------------------------------------------------
# The probe itself
# --------------------------------------------------------------------------------------


def test_the_probe_is_separator_insensitive(fake_home: Path) -> None:
    assert _named_child_within("~/Desktop", "whats in my garden folder") == "~/Desktop/my-garden-folder"


def test_the_probe_ignores_hidden_directories(fake_home: Path, tmp_path: Path) -> None:
    (tmp_path / "Desktop" / ".garden-cache").mkdir()
    # Two matches would be ambiguous; the hidden one must not count as the second.
    assert _named_child_within("~/Desktop", "whats in my garden folder") == "~/Desktop/my-garden-folder"


def test_the_probe_returns_nothing_for_a_missing_root(fake_home: Path) -> None:
    assert _named_child_within("~/NoSuchRoot", "whats in my garden folder") == ""


def test_the_probe_never_recurses(fake_home: Path, tmp_path: Path) -> None:
    """Only direct children: a nested match must not be found (and must not cost a walk)."""

    (tmp_path / "Desktop" / "unrelated-project" / "deep-garden").mkdir(parents=True)
    (tmp_path / "Desktop" / "my-garden-folder").rmdir()
    assert _named_child_within("~/Desktop", "whats in my garden folder") == ""


# --------------------------------------------------------------------------------------
# Listing verbs. A phrasing the extractor does not recognise falls through to the model
# lane, where routing is nondeterministic — the same sentence answered correctly against
# one folder and refused against another, because a 0.6B arbiter picked differently and
# the 8B then chose workspace.list_files (workspace-relative, no workspace bound) for an
# absolute path. Recognising the phrasing keeps the turn deterministic.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing",
    [
        "give me a rundown of what lives in ~/Desktop/my-garden-folder",
        "give me a breakdown of what sits in ~/Desktop/my-garden-folder",
        "what is kept in ~/Desktop/my-garden-folder",
        "what lives in ~/Desktop/my-garden-folder",
        "show me the contents of ~/Desktop/my-garden-folder",
    ],
)
def test_no_listing_verb_phrasings_are_claimed_deterministically(fake_home: Path, phrasing: str) -> None:
    assert _path(phrasing) == "~/Desktop/my-garden-folder"


@pytest.mark.parametrize(
    "phrasing",
    [
        "write a file to my desktop",
        "create a folder on my desktop",
        "delete the old notes on my desktop",
    ],
)
def test_write_intent_is_never_claimed_as_a_listing(fake_home: Path, phrasing: str) -> None:
    """Widening the listing verbs must not swallow anything that mutates."""

    assert _extract_safe_machine_directory_listing(phrasing) is None
