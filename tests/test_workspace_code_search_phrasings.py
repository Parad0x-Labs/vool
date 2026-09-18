"""Acceptance regressions for `workspace.search_text`, from phrasings driven at the live daemon.

Eight code-search phrasings were driven on the deployed build 2026-07-30. One reached the tool.
The other seven did not, in two shapes:

* "find all references to directories_only" was sent to WEB SEARCH, and answered out of MDN Web
  Docs. `directories_only` is an identifier in this checkout; nobody types it into a search engine.

* "grep for _GROUNDED_LISTING_VERDICTS", "where is explicit_path_in defined", "which files mention
  list_directory", "show me where count_only is used", "hunt down _ASKS_HOW_MANY_RE in the repo" and
  "look for the string safe local directories" reached no lane at all. Five returned "I couldn't map
  that cleanly to a real action" after about a minute each; one ran past 400 seconds.

The cause: every pattern in the detector required the sentence to NAME the workspace ("search the
codebase for X"). None of the seven does -- people say "grep for X" and expect the code they are
sitting in.
"""
from __future__ import annotations

import pytest

from core.execution.planner import _code_search_query, plan_tool_workflow


def _plan(text: str) -> tuple[str | None, str | None]:
    decision = plan_tool_workflow(
        user_text=text,
        task_class="unknown",
        executed_steps=[],
        source_context={"surface": "api", "platform": "api", "workspace": "/tmp/vool-ws"},
    )
    if not decision.handled:
        return None, None
    payload = decision.next_payload or {}
    return payload.get("intent"), (payload.get("arguments") or {}).get("query")


@pytest.mark.parametrize(
    ("text", "expected_query"),
    [
        # the one that went to the internet and came back with MDN
        ("find all references to directories_only", "directories_only"),
        # the six that reached no lane
        ("grep for _GROUNDED_LISTING_VERDICTS", "_GROUNDED_LISTING_VERDICTS"),
        ("where is explicit_path_in defined", "explicit_path_in"),
        ("which files mention list_directory", "list_directory"),
        ("show me where count_only is used", "count_only"),
        ("hunt down _ASKS_HOW_MANY_RE in the repo", "_ASKS_HOW_MANY_RE"),
        ("look for the string safe local directories", "safe local directories"),
    ],
)
def test_a_code_search_searches_this_checkout(text: str, expected_query: str) -> None:
    intent, query = _plan(text)
    assert intent == "workspace.search_text", f"{text!r} did not reach the workspace search"
    assert query == expected_query


def test_the_phrasing_that_already_worked_still_does() -> None:
    intent, query = _plan("search the codebase for machine_path_listing_intent")
    assert intent == "workspace.search_text"
    assert query == "machine_path_listing_intent"


@pytest.mark.parametrize(
    "text",
    [
        "search the web for python tutorials",
        "google the latest solana news",
        "look up FastAPI docs online",
        # same verb as a code search, and not one -- nothing code-shaped to search for
        "find all references to the Treaty of Versailles",
        "hunt down a good pasta recipe",
        # these two carry code-search vocabulary AND say where to look, so only the web check
        # can keep them out of this checkout
        "search online for all references to use_effect",
        "grep the web for react_hooks examples",
    ],
)
def test_a_question_about_the_internet_still_goes_to_the_internet(text: str) -> None:
    """The widening is only sound if the web lane keeps what belongs to it."""

    intent, _query = _plan(text)
    assert intent != "workspace.search_text"


def test_the_qualifier_is_not_part_of_the_needle() -> None:
    """"look for THE STRING x" -- searching for the qualifier verbatim finds nothing."""

    assert _code_search_query("look for the string safe local directories") == "safe local directories"
    assert _code_search_query("look for the function build_folder_overview") == "build_folder_overview"


def test_an_identifier_beats_the_prose_around_it() -> None:
    """The longest identifier is the subject; camelCase words in the sentence lose to it."""

    assert _code_search_query("grep for _GROUNDED_LISTING_VERDICTS") == "_GROUNDED_LISTING_VERDICTS"


def test_a_quoted_needle_wins_outright() -> None:
    """Quotes say "this exact text". An identifier inside them must not be picked out of it."""

    assert _code_search_query('grep for "the count_only flag is set"') == "the count_only flag is set"


# --------------------------------------------------------------------------------------
# a one-word symbol name is still a symbol name
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # `chunkify` is a real function in the bound workspace (g6/chunk.py). It carries no
        # underscore and no camelCase, so the identifier probe misses it -- this phrasing spent
        # 60.5s and returned "I couldn't map that cleanly to a real action".
        ("grep for chunkify", "chunkify"),
        ("where is clamp defined", "clamp"),
        ("show me where chunkify is used", "chunkify"),
    ],
)
def test_a_one_word_symbol_after_an_unambiguous_verb(text: str, expected: str) -> None:
    assert _code_search_query(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # a determiner in the symbol slot means a topic, not a symbol
        "find all references to the Treaty of Versailles",
        "which files mention the constitution",
        "where is the post office",
    ],
)
def test_a_determiner_in_the_symbol_slot_is_not_a_symbol(text: str) -> None:
    assert _code_search_query(text) == ""


# -- driven live 2026-07-31 -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrasing,expected",
    [
        # The measured failure, verbatim. `_percent` is a leading-underscore name with no second
        # underscore, which the identifier pattern could not match: the sentence fell through to the
        # trailing-phrase branch and searched for the literal "of _percent in the codebase".
        ("find all uses of _percent in the codebase", "_percent"),
        ("find all uses of _trim in the repo", "_trim"),
        ("list all uses of _percent in the repo", "_percent"),
        ("show me all uses of _first_visit in the code", "_first_visit"),
        ("which files use _percent", "_percent"),
        ("grep the repo for elliptical_drive_followup", "elliptical_drive_followup"),
        ("where is machine_diagnostics_intent defined", "machine_diagnostics_intent"),
        # These two reach NO needle shape, so only the identifier pattern can find the symbol in
        # them. Without them the first version of this fixture passed even with the leading-
        # underscore alternative deleted -- the needle shapes silently covered every case in it.
        ("hunt down _percent in the repo", "_percent"),
        ("in the codebase where does _percent come from", "_percent"),
        # The mirror image: `chunkify` has no underscore and no camelCase, so the identifier pattern
        # cannot see it and only the needle shape can. It is why "uses" has to be a usage noun
        # alongside "usages" -- people say "all uses of", and the shape listed only the latter.
        ("find all uses of chunkify in the repo", "chunkify"),
    ],
)
def test_a_private_symbol_is_searched_for_by_name_not_by_the_sentence(phrasing: str, expected: str) -> None:
    assert _code_search_query(" ".join(phrasing.lower().split())) == expected, (
        f"searching for anything other than {expected!r} walks the workspace and reports "
        f"'No text matches' for a symbol that is defined in this checkout."
    )


@pytest.mark.parametrize(
    "phrasing",
    [
        "find all references to the Treaty of Versailles",
        "hunt down a good pasta recipe",
        "search the web for python tutorials",
    ],
)
def test_widening_the_usage_nouns_does_not_make_prose_a_code_search(phrasing: str) -> None:
    assert _code_search_query(" ".join(phrasing.lower().split())) == ""
