"""A clock answer must never silently substitute this machine's zone for a zone the user named.

Measured on the served surface, 2026-08-14::

    U: ... just tell me what time it is in Tokyo right now ...
    A: Current time is 22:58 EEST.
       tool | date_time_fast_path | tool-generated answer | 1 local routing call

Tokyo was 05:10 JST. Worse than a wrong number: the answer does not say "in Tokyo" at all, because
the label was empty -- so the user sees a confident, tool-attributed local time with nothing hinting
their city was dropped.

`_UTILITY_TIMEZONE_ALIASES` held three entries, all Vilnius. Every other place on Earth fell to
`datetime.now().astimezone()`. A second fail-open sat underneath it: `utility_now_for_timezone`
swallowed an unknown IANA key into local time too, so a resolver bug produced the same wrong answer
one layer down.

THE INVARIANT, which is what these tests defend -- not the word "Tokyo":

    a turn that NAMES a place gets that place's clock, or no answer from this lane at all.

The resolver is built from the tzdb's own canonical table (`zone1970.tab`/`zone.tab`), NOT from
`zoneinfo.available_timezones()`. That is the whole safety property and it is measured: the wider
set is where the compass words (north/south/east/west/central/pacific/mountain), the pseudo-zones
(Factory, Universal, ROC, MET, WET), the abbreviations, and `Etc/GMT+5` -- which POSIX defines as
UTC MINUS 5 -- all live. Against the canonical table those resolve to nothing and there are zero
ambiguous leaves, while real settlements still resolve.

No city list is hard-coded anywhere in the repair, and none is hard-coded here beyond what any test
must name to be a test.
"""

from __future__ import annotations

from zoneinfo import ZoneInfoNotFoundError

import pytest

from core.agent_runtime.fast_paths_utility import (
    contextual_time_followup_timezone,
    date_time_fast_path,
    extract_utility_timezone,
    utility_names_unresolved_location,
    utility_now_for_timezone,
)


def _zone(text: str) -> str:
    return extract_utility_timezone(text)[0]


def _answer(text: str) -> str | None:
    return date_time_fast_path(None, text, source_surface="api")


# ---------------------------------------------------------------------------------------------
# G1 -- the reported lane, in wording that is NOT the report
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "zone"),
    (
        ("what time is it in tokyo", "Asia/Tokyo"),
        ("whats the time in reykjavik", "Atlantic/Reykjavik"),
        ("current time in kolkata", "Asia/Kolkata"),
        ("time in dubai currently", "Asia/Dubai"),
        ("what time is it in honolulu now", "Pacific/Honolulu"),
        ("what time is it in oslo?", "Europe/Oslo"),
        ("time in sao paulo please", "America/Sao_Paulo"),
        ("what time is it in new york right now", "America/New_York"),
        ("what time is it in los angeles", "America/Los_Angeles"),
        ("whats the time in phoenix", "America/Phoenix"),
    ),
)
def test_a_named_city_resolves_to_its_own_zone(text: str, zone: str) -> None:
    assert _zone(text) == zone, text


def test_the_answer_names_the_place_it_answered_for() -> None:
    """The silent half of the defect. A wrong time with no location is undetectable by the reader."""

    answer = _answer("what time is it in tokyo")

    assert answer and "Tokyo" in answer


def test_a_foreign_city_does_not_get_this_machines_clock() -> None:
    """The invariant itself, asserted without naming a timezone abbreviation.

    Compared against the local answer rather than a fixed string, so this test does not depend on
    where it is run, what season it is, or DST.
    """

    local = _answer("what time is it")
    foreign = _answer("what time is it in tokyo")

    assert local and foreign
    assert local.split("is")[-1].strip() != foreign.split("is")[-1].strip()


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the false-positive surface the canonical table is chosen to remove
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        # compass and region words that are real zone leaves in the WIDER table
        "what time is it in central europe",
        "whats the time in the pacific northwest",
        "what time is it in north korea",
        "what time is it in west virginia",
        # tzdb pseudo-zones
        "what time is it at the factory",
        "whats the current time in universal terms",
        "what time is it in general terms",
        # sign-inverted offset zone: Etc/GMT+5 is UTC-5, so resolving it answers ten hours wrong
        "whats the time in gmt+5",
    ),
)
def test_a_word_that_is_only_incidentally_a_zone_name_is_not_a_place(text: str) -> None:
    assert _zone(text) == "", text


@pytest.mark.parametrize(
    "text",
    (
        "what time is it in wake of the outage",
        "what time is it in jersey shore reruns",
        "what time is it in oral argument",
        "what time is it in troll mode",
    ),
)
def test_a_place_word_that_does_not_end_the_phrase_is_not_a_destination(text: str) -> None:
    """Object-final. If the sentence goes on to say what it was about, the word was not a place.

    Written after a first attempt that matched shorter prefixes of the phrase and leaked all four.
    """

    assert _zone(text) == "", text


@pytest.mark.parametrize(
    "text",
    (
        "what is the current response time in production",
        "how much time did we spend in meetings",
        "the least of my worries",
        "actually contact the reactor team",
        "rise like a phoenix",
        "phoenix prod is on fire, just explain the metaphor",
        "run the test suite",
    ),
)
def test_ordinary_sentences_resolve_no_zone_at_all(text: str) -> None:
    """`production` contains `uct`; `least` contains `east`; `reactor` contains `roc`.

    A substring scan over the wider zone set answered "Current time in Uct is ..." for the first of
    these -- measured. Word boundaries and a locative slot are both required.
    """

    assert _zone(text) == "", text


def test_a_turn_naming_no_place_still_answers_locally() -> None:
    """The fix must not make the common case fail closed."""

    assert _answer("what time is it")
    assert _answer("what is the date")
    assert utility_names_unresolved_location("what time is it") is False


# ---------------------------------------------------------------------------------------------
# FAIL CLOSED -- an unresolvable place must yield no answer, never a local one
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "what time is it in central europe",
        "what time is it at the factory",
        "what time is it in west virginia",
        "what time is it in narnia",
    ),
)
def test_an_unresolvable_named_place_declines_rather_than_answering_local(text: str) -> None:
    assert utility_names_unresolved_location(text) is True, text
    assert _answer(text) is None, text


def test_an_unknown_zone_key_raises_instead_of_becoming_local_time() -> None:
    """The second fail-open. A resolver bug must not degrade into this machine's clock.

    `utility_now_for_timezone` used to swallow every failure, so an invalid IANA name produced
    local time under the same tool-grounded footer -- the same wrong answer, one layer down.
    """

    # The specific type, not a blind Exception: a blind assert would also pass if the function
    # raised TypeError for some unrelated reason, which is not the property being defended.
    with pytest.raises(ZoneInfoNotFoundError):
        utility_now_for_timezone("Not/AZone")

    assert utility_now_for_timezone("")  # empty still means "no zone named" -> local, by design


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_tense_word_does_not_inherit_the_previous_turns_place() -> None:
    """"now" and "current" are tense markers, not place markers.

    After a turn about a foreign city, "what time is it now" plainly means HERE. The inheritance
    list used to include `now`/`current`/`right now`, which was invisible while the only resolvable
    zone equalled this machine's -- and becomes a live wrong-answer generator the moment foreign
    cities resolve.
    """

    stored = {"utility_kind": "time", "timezone": "Asia/Tokyo", "label": "Tokyo"}

    assert contextual_time_followup_timezone("what time is it now", recent_utility_context=stored) == ("", "")
    assert contextual_time_followup_timezone("whats the current time", recent_utility_context=stored) == ("", "")


def test_a_real_place_reference_still_inherits() -> None:
    """Negative control on that removal: "there" genuinely refers to the previous place."""

    stored = {"utility_kind": "time", "timezone": "Asia/Tokyo", "label": "Tokyo"}

    assert contextual_time_followup_timezone("what time is it there", recent_utility_context=stored) == (
        "Asia/Tokyo",
        "Tokyo",
    )


def test_the_resolver_uses_the_canonical_table_not_every_known_zone() -> None:
    """The design decision, pinned. Widening the universe re-admits every trap at once."""

    from core.agent_runtime.fast_paths_utility import _canonical_zone_leaves

    leaves = _canonical_zone_leaves()

    assert leaves, "no canonical zone table was found on this host"
    for trap in ("west", "central", "east", "north", "south", "pacific", "mountain", "factory", "universal"):
        assert trap not in leaves, trap
    for real in ("tokyo", "phoenix", "vilnius"):
        assert real in leaves, real


def test_no_curated_city_list_was_smuggled_in() -> None:
    """A bigger allowlist is the same defect as the three-entry one, just harder to notice.

    The repair must resolve from shipped data. The only curated entries permitted are the operator
    label aliases and the offset pseudo-zones, which the canonical table deliberately excludes.
    """

    from core.agent_runtime import fast_paths_utility as mod

    assert len(mod._UTILITY_TIMEZONE_ALIASES) <= 4
    assert len(mod._UTILITY_OFFSET_ALIASES) <= 6
    assert "tokyo" not in mod._UTILITY_TIMEZONE_ALIASES
    assert "tokyo" not in mod._UTILITY_OFFSET_ALIASES


# ---------------------------------------------------------------------------------------------
# A STATED OUTPUT FORM -- this lane answers in one shape; a turn asking for another gets declined
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        # the measured reproduction: right time, wrong form, and nothing compared the two
        "just tell me what time it is in Tokyo right now. But here is the catch: format the time "
        "output strictly as a valid bash command that echoes the time to a text file. "
        "No conversational text.",
        "what time is it in tokyo, return it as a json object",
        "what time is it in tokyo, in the form of a shell command",
        # NOT here: "Output exactly one word." The lane has a correct one-word form and now offers
        # it -- see test_a_one_word_contract_is_ANSWERED_not_declined. Declining that one sent the
        # turn to a model which replied "15".
        "what time is it? reply with exactly HELLO and nothing else",
    ),
)
def test_a_turn_that_states_its_own_output_form_is_declined(text: str) -> None:
    """A lane that cannot honour a stated contract must decline, so a lane that can gets the turn.

    Compliance is measured against the answer this lane is about to return, rather than by
    enumerating contract kinds -- and `changed` counts as well as `compliant`, because
    `apply_raw_output_contract` REPAIRS the text and then calls it compliant. Checking compliance
    alone let "reply with exactly HELLO and nothing else" through: the contract had rewritten the
    clock sentence into HELLO and reported success.
    """

    assert _answer(text) is None, text


@pytest.mark.parametrize(
    "text",
    (
        "what time is it in tokyo. no markdown",
        "what time is it in tokyo. thanks",
        "whats the time in oslo please",
        "what time is it",
    ),
)
def test_a_contract_this_lane_already_satisfies_still_answers(text: str) -> None:
    """The false-decline control, and it caught a real bug in the resolver.

    "in tokyo. no markdown" captured the place as "tokyo. no markdown", because `.` sat inside the
    place character class (there for "st.petersburg"). No zone matched, the fail-closed rule fired,
    and a good question got no answer. A period may JOIN letters; it may never trail one.
    """

    assert _answer(text) is not None, text


def test_a_place_word_inside_a_sentence_does_not_swallow_the_rest() -> None:
    """The resolver-level form of the same bug."""

    assert extract_utility_timezone("what time is it in tokyo. no markdown")[0] == "Asia/Tokyo"
    assert extract_utility_timezone("what time is it in oslo. thanks")[0] == "Europe/Oslo"


def test_a_one_word_contract_is_ANSWERED_not_declined() -> None:
    """Declining is for a contract the lane cannot meet -- not for one its first shape cannot.

    Measured on the served surface: after the decline landed, "what time is it in Tokyo? Output
    exactly one word." fell through to a model that replied "15" -- wrong, and worse than the prose
    sentence it replaced. The lane has a correct one-word form and should offer it.
    """

    answer = _answer("what time is it in Tokyo? Output exactly one word.")

    assert answer is not None, "the lane declined a contract it can satisfy"
    assert len(answer.split()) == 1
    assert ":" in answer


def test_a_one_word_date_is_also_answered() -> None:
    answer = _answer("what is the date? Output exactly one word.")

    assert answer is not None and len(answer.split()) == 1


def test_the_terse_forms_report_the_same_observation() -> None:
    """Shorter wording, never a different fact: the terse shape must agree with the sentence."""

    full = _answer("what time is it in tokyo")
    terse = _answer("what time is it in Tokyo? Output exactly one word.")

    assert full and terse
    assert terse.split(":")[0] in full, (full, terse)


def test_a_clock_value_counts_as_one_word() -> None:
    """The counter split on `:`, so no clock value could ever satisfy a one-word contract --
    the same class as a decimal point splitting `1.8`. A hyphenated date already counted as one."""

    from core.response_constraints import _WORD_RE

    assert len(_WORD_RE.findall("06:49")) == 1
    assert len(_WORD_RE.findall("2026-08-15")) == 1
    assert len(_WORD_RE.findall("06:49 JST")) == 2
    assert len(_WORD_RE.findall("hello world")) == 2
