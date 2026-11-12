"""Red team round 4 (real app on the v0.8 head 7f8ac923, 2026-11-12): world asks read alike.

* W2 "can i legally use a photo from a news site on my blog" was held to a web lookup because the
  word "news" named where the photo came from; with Web lookups off it got the web-off line and,
  retried with them on, "I didn't look anything up for this". Q2/Q5 "can i use a picture from a
  newspaper website on my instagram" was answered. The same question now reads the same way.
* R1 "whats the max size of a zip i can email with gmail" was answered "I couldn't find a file
  called 'max size of a'": a service's size limit was read as a file to measure.

The wordings below are new, except the measured ones named.
"""
from __future__ import annotations

import pytest

from core.execution.constants import machine_size_query_intent
from core.execution_requirements import requirements_for


@pytest.mark.parametrize("asked", (
    "can i legally use a photo from a news site on my blog",           # measured W2
    "can i use a picture from a newspaper website on my instagram",    # measured Q2
    "is it ok to repost an image from a news website on facebook",
    "can i quote a sentence from a news outlet in my essay",
    "how do i credit a photographer from a news agency",
))
def test_a_news_organisation_named_as_a_source_does_not_ask_for_news(asked):
    need = requirements_for(asked, source_context={})
    assert "current_info_signal:news_request" not in need.reason_codes, need
    assert not need.current_information_required, need


@pytest.mark.parametrize("asked", (
    "any news about the strike today",
    "news about spacex",
    "show me recent news coverage about Rust",
    "what did the news site say about the election today",
))
def test_news_asked_for_still_needs_current_information(asked):
    assert requirements_for(asked, source_context={}).current_information_required


@pytest.mark.parametrize("asked", (
    "whats the max size of a zip i can email with gmail",               # measured R1
    "what is the maximum size of a video i can upload to youtube",
    "whats the size limit for a pdf in gmail",
    "how large an attachment can you send with outlook",
    "whats the upload limit for a file on google drive",
))
def test_a_services_size_limit_is_not_a_file_to_measure(asked):
    assert machine_size_query_intent(asked) is None


@pytest.mark.parametrize(("asked", "name"), (
    ("whats the size of holiday.zip", "holiday.zip"),
    ("how big is the backup zip", "backup"),
    ("what size is the invoice pdf", "invoice"),
))
def test_a_named_file_is_still_measured(asked, name):
    found = machine_size_query_intent(asked)
    assert found is not None and found[1] == name, found
