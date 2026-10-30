"""Everyday asks the 2026-10-29 PC-vs-phone audit left to the model, which failed them on qwen3:8b.

"my laptop has been proper sluggish today, whats slowing it down?" got a clarifying question; "i took some
screenshots last week i think, where are they?" got "I couldn't get a usable model response"; "what background
services are running?" waited for an ambiguity check that timed out; "how full is my computers storage rn?" got
"that tool didn't run"; "in my Pictures folder, anything from the last few days only?" tried to read a file named
Pictures. Each is now answered by the tool that measures it.
"""
from __future__ import annotations

import pytest

from core.execution.constants import (
    machine_diagnostics_intent,
    machine_file_search_intent,
    machine_kind_listing_covers_turn,
    machine_kind_listing_intent,
    machine_live_load_intent,
)
from core.operator.parser import parse_operator_action_intent


@pytest.mark.parametrize("said", [
    "my laptop has been proper sluggish today, whats slowing it down?",
    "why is my mac so slow",
    "what is slowing down my computer",
    "this pc is lagging, which app is causing it",
])
def test_a_slow_machine_is_measured(said):
    assert machine_live_load_intent(said) == "machine.list_processes"


@pytest.mark.parametrize("said", [
    # Someone else's machine, a general question, a writing task, and the disk lane keep their answers.
    "my manager's laptop is slow, why?",
    "is a macbook slow at gaming?",
    "my laptop is slow, write a blog post about laptops",
    "write a script that explains why my laptop is slow",
    "my mac is slow, is the disk full?",
])
def test_not_a_slow_machine_measurement(said):
    assert machine_live_load_intent(said) is None


def test_background_services_are_listed():
    intent = parse_operator_action_intent("what background services are running?")
    assert intent is not None and intent.kind == "inspect_services"


@pytest.mark.parametrize(("said", "kind", "place", "days", "name"), [
    ("i took some screenshots last week i think, where are they?", "image", "", 7.0, "screenshot"),
    ("I downloaded a few pdfs yesterday, where did they go?", "pdf", "Downloads", 2.0, ""),
    ("so i took some photos, can you find them", "image", "", None, ""),
    ("in my Pictures folder, anything from the last few days only?", "", "Pictures", 3.0, ""),
    ("anything new in my downloads from today?", "", "Downloads", 1.0, ""),
])
def test_things_i_made_and_lost_are_listed(said, kind, place, days, name):
    listing = machine_kind_listing_intent(said)
    assert listing is not None
    assert (listing.kind, listing.place, listing.max_age_days, listing.name_contains) == (kind, place, days, name)
    assert machine_kind_listing_covers_turn(said)


@pytest.mark.parametrize("said", [
    "i took some photos, what should i call the album",
    "in my downloads folder, anything i should delete?",
    "find my pictures folder",
    "i took some screenshots last week i think",
])
def test_saying_i_made_something_is_not_a_listing(said):
    assert machine_kind_listing_intent(said) is None


def test_a_second_ask_keeps_the_turn_on_the_planner():
    said = "i took some screenshots, where are they? and email them to bob"
    assert not machine_kind_listing_covers_turn(said)


@pytest.mark.parametrize("said", [
    "how full is my computers storage rn?", "is my disk gonna fill up soon or am i fine?", "how full is my laptop's drive",
])
def test_a_full_disk_question_reads_the_disk(said):
    assert machine_diagnostics_intent(said) == "machine.disk_usage"


@pytest.mark.parametrize("said", [
    "how full is the venue storage room", "is my storage unit gonna fill up", "will my calendar fill up",
])
def test_other_storage_is_not_this_disk(said):
    assert machine_diagnostics_intent(said) is None


@pytest.mark.parametrize(("said", "expected"), [
    # Merged 0.7.6 kind nouns made "resume" and "cv" kinds; they are still what the file is called.
    ("find my resume pdf", ("pdf", ("resume",), "")),
    ("where did i put my cv? the 2026 one", ("", ("cv",), "")),
    # An app to get, and the newest of a kind (the listing's), are not a file searched by name.
    ("find the cheapest pdf editor", None),
    ("find the newest screenshot from my downloads", None),
])
def test_file_lookups_after_the_merge(said, expected):
    assert machine_file_search_intent(said) == expected
