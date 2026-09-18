"""A comma-qualified place resolves whenever its country has a currency.

Fourteen euro-area members, the UK's constituent countries and the UAE were missing from the
place table while their cities were known. The qualifier rule is right -- "Manchester, New
Hampshire" must not resolve as GBP off the bare city -- so an unknown COUNTRY unresolved the whole
place: "Lisbon, Portugal" carried no currency, and a travel story naming such a place fell out of
the typed contract into a model call. New wording of that class, none of it the story that failed.
"""

from __future__ import annotations

import pytest

from core.currency_travel_spend import _location_code, travel_spend_intent


@pytest.mark.parametrize(
    ("place", "code"),
    [
        ("Tallinn, Estonia", "EUR"),
        ("Valletta, Malta", "EUR"),
        ("Bratislava, Slovakia", "EUR"),
        ("Cork, Ireland", "EUR"),
        ("Nicosia, Cyprus", "EUR"),
        ("Split, Croatia", "EUR"),
        ("Plovdiv, Bulgaria", "EUR"),
        ("Cardiff, Wales", "GBP"),
        ("Aberdeen, Scotland", "GBP"),
        ("Leeds, England", "GBP"),
        ("Abu Dhabi, United Arab Emirates", "AED"),
        ("Sharjah, UAE", "AED"),
    ],
)
def test_a_qualified_place_resolves_through_its_country(place: str, code: str) -> None:
    assert _location_code(place) == code


def test_the_qualifier_still_refuses_a_country_the_table_does_not_know() -> None:
    # The negative-evidence rule stays: an unknown qualifier never falls back to the bare city
    # (New Hampshire itself is a known US region, so the probe uses a region no table knows).
    assert _location_code("Manchester, Vortania") == ""


def test_a_travel_story_between_two_formerly_unknown_countries_parses() -> None:
    story = (
        "I carry 900 units of local currency in Riga, Latvia and travel to Cardiff, Wales to buy a coat "
        "for 120 units of local currency. Given 1 EUR = 0.85 GBP, what remains in Wales' currency? "
        "Show your working."
    )
    request = travel_spend_intent(story)
    assert request is not None, "the typed contract must own a story between two euro/sterling places"
    assert (request.source_code, request.target_code) == ("EUR", "GBP")
    assert request.missing_rate is False


def test_an_explicit_unit_wins_over_the_place_it_is_held_in() -> None:
    # The place says JPY; the traveller says what they hold. The story stays a typed calculation.
    story = (
        "I have 400 USD in Kyoto, Japan. I want to buy a kimono in Osaka, Japan for 30,000 JPY. "
        "If 1 USD = 150 JPY, how much do I have left? Show the math."
    )
    request = travel_spend_intent(story)
    assert request is not None
    assert (request.source_code, request.target_code) == ("USD", "JPY")
    assert _location_code("Kyoto, Japan", unit="USD") == "USD"
    assert _location_code("Kyoto, Japan") == "JPY"
