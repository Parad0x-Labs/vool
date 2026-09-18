"""A web search must search the question the user asked, not a rewritten one.

Driven against the running daemon on `5cdce5d` over `/api/chat`, one fresh session per turn. Five
unrelated questions came back saying the search results were about something else:

    "search the web for the current population of Iceland"
        -> "The search results returned only MDN Web Docs (Mozilla Developer Network) pages about
            web development — nothing about Iceland's population."                        (27.8s)
    "google the release date of the Nintendo Switch 2"
        -> "the search results ... returned MDN Web Docs and PageSpeed Insights, which are
            unrelated."                                                                   (40.4s)
    "what is the latest version of Python?"
        -> "The sources are MDN Web Docs ... and a Wikipedia page that only mentions Python 3.0
            from 2008"                                                                    (16.0s)
    "look up what a Merkle tree is and summarise it"
        -> answered from MDN's Certificate Transparency page and an Apple page about B-trees
    "how do I hide columns in Google Sheets?"
        -> reported in the previous QA pass as answered with "the CSS visibility property"

Every one of those refusals was correct behaviour on the evidence it was given. The evidence was
the defect. `retrieval/web_adapter.planned_search_query` rewrites the query through a source
profile, `_infer_topic_kind` returns `general` for anything that does not name news / an API /
design — which is most questions — and the `general` branch led with `official_docs`, whose
template is:

    {topic} official docs site:docs.python.org OR site:developer.mozilla.org OR site:web.dev
    OR site:developer.android.com OR site:developer.apple.com

So "who won the world cup in 2022" was sent to the search engine as a Python-documentation query.
There was no open-web profile in the table at all.

The same shape appeared a second time one level down: `platform_topic` matched the words "bot",
"api" and "webhook", which belong to every platform, so "how do I use the fetch api in javascript"
was searched as `site:core.telegram.org OR site:discord.com`.

This is the same mistake the machine lane made with ` cpu ` and ` ram `, and the clock lane with
"registry": a topic WORD standing in for a topic.
"""
from __future__ import annotations

import pytest

from core.source_reputation import get_source_profile, profiles_for_topic, render_query
from retrieval.web_adapter import _infer_topic_kind


def _first_query(question: str, *, task_class: str = "unknown") -> str:
    kind = _infer_topic_kind(question, task_class=task_class, topic_hints=[])
    profiles = profiles_for_topic(kind, question)
    assert profiles, question
    return render_query(profiles[0], question)


#: The five verbatim questions whose answers reported irrelevant sources, plus ordinary questions
#: of the same shape. None of these is about Python, Mozilla, Android or Apple.
ORDINARY_QUESTIONS = (
    "current population of Iceland",
    "release date of the Nintendo Switch 2",
    "what is the latest version of Python",
    "what a Merkle tree is",
    "how do I hide columns in Google Sheets",
    "who won the world cup in 2022",
    "what time does the pharmacy on the high street close",
    "how long should I boil an egg",
)


@pytest.mark.parametrize("question", ORDINARY_QUESTIONS)
def test_an_ordinary_question_is_not_rewritten_into_a_developer_docs_search(question: str) -> None:
    assert _first_query(question) == question, (
        "the user's own words must be what reaches the search engine"
    )


@pytest.mark.parametrize("question", ORDINARY_QUESTIONS)
def test_no_site_filter_is_bolted_onto_an_ordinary_question(question: str) -> None:
    """`site:` is the mechanism that produced the wrong evidence, so it is named directly."""
    assert "site:" not in _first_query(question)


def test_the_open_web_profile_exists_and_restricts_nothing() -> None:
    """An empty allow list is what `_notes_from_research` reads as "do not restrict"."""
    profile = get_source_profile("open_web")
    assert profile is not None, "the general branch has nowhere to send an ordinary question"
    assert profile.allow_domains == ()
    assert profile.query_template == "{topic}"


@pytest.mark.parametrize(
    "question",
    [
        "how do I use the fetch api in javascript",
        "what webhook format does stripe use",
        "write a bot that posts to my own server",
    ],
)
def test_a_generic_platform_word_does_not_pin_the_search_to_telegram(question: str) -> None:
    first = _first_query(question)
    assert "core.telegram.org" not in first
    assert "discord.com" not in first


@pytest.mark.parametrize(
    "question,domain",
    [
        ("how do I send a telegram bot message", "core.telegram.org"),
        ("discord slash command permissions", "discord.com"),
    ],
)
def test_a_named_platform_still_reaches_its_own_docs(question: str, domain: str) -> None:
    """The narrowing is worth keeping where the user actually named the platform."""
    assert domain in _first_query(question)


def test_a_technical_question_still_leads_with_the_docs_but_can_leave_them() -> None:
    """Docs first is right for a technical question; docs ONLY is what made stripe unanswerable."""
    profiles = profiles_for_topic("technical", "what webhook format does stripe use")
    ids = [profile.profile_id for profile in profiles]
    assert ids[0] == "official_docs"
    assert "open_web" in ids
    assert ids.index("official_docs") < ids.index("open_web")


def test_a_news_question_can_reach_past_the_four_wire_services() -> None:
    ids = [profile.profile_id for profile in profiles_for_topic("news", "what happened today")]
    assert ids[0] == "reputable_news"
    assert "open_web" in ids
