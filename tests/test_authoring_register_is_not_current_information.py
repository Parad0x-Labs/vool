"""A request to BUILD software whose specification mentions freshness is not a current-information ask.

Measured 2026-09-06 (served transcript, 23:30): "create a simple tg bot in that folder … its only
task will be to check latest chats the owner is in and pull the summary" was classified GROUNDED
(`current_information_required=True`) because of the word "latest" inside the bot's specification.
The turn then ran a web search, and the publication gate withheld the answer because the search
results never reached the model -- for a request whose deliverable was code, not a claim about
the world. The freshness marker describes what the artifact should do; it does not ask the
runtime to look anything up now.

Controls keep the current-information verdict where it belongs: a question about the world that
carries the same marker, and an authoring request that explicitly demands sources.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for

AUTHORING = [
    "now lets create a simple tg bot in that folder - lets Name it Vool ping - it's only task will be to check latest chats the owner is in and pull the summary from it.",
    "build me a small web page that shows the latest three posts from an rss feed",
    "write a python script that watches a folder and prints the newest file every minute",
    "implement a cli tool that fetches the current weather for a city passed as an argument",
    "make a discord bot that posts today's top headlines to a channel",
]
CURRENT = [
    "what is the latest python version",
    "what are today's top headlines",
    "what is the current weather in vilnius",
]
EXPLICIT_EVIDENCE = [
    "write a python script that prints the newest file in a folder, and cite your sources for the api you use",
]
MIXED_WITH_LIVE_CLAUSE = [
    "what is 1000 TRY in EUR right now, and create a small python script that prints the result",
    "create a small python script that prints the current gold price; also, what is the gold price right now?",
    "build a dashboard for our sales team and tell me today's EUR to USD rate",
]
DOCUMENT_ARTIFACTS = [
    "write a report on the latest ecb rate decision",
    "create a summary of this week's eurozone inflation data",
]


@pytest.mark.parametrize("text", AUTHORING)
def test_software_authoring_requests_are_not_current_information(text: str) -> None:
    req = requirements_for(text)
    assert req.current_information_required is False, (text, req.answer_mode, req.reason_codes)
    assert req.answer_mode != "GROUNDED", (text, req.reason_codes)


@pytest.mark.parametrize("text", CURRENT)
def test_questions_about_the_world_still_require_current_information(text: str) -> None:
    req = requirements_for(text)
    assert req.current_information_required is True, (text, req.reason_codes)


@pytest.mark.parametrize("text", EXPLICIT_EVIDENCE + DOCUMENT_ARTIFACTS)
def test_explicit_evidence_demands_and_document_artifacts_keep_the_grounded_verdict(text: str) -> None:
    req = requirements_for(text)
    assert req.current_information_required is True, (text, req.reason_codes)


@pytest.mark.parametrize("text", MIXED_WITH_LIVE_CLAUSE)
def test_a_live_data_clause_beside_an_authoring_clause_keeps_the_requirement(text: str) -> None:
    req = requirements_for(text)
    assert req.current_information_required is True, (text, req.answer_mode, req.reason_codes)
