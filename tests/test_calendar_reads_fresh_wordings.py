"""Fresh everyday calendar-read wordings, written apart from the recorded-defect suite.

The person-path suite pins the given wordings word for word; this file adds wordings this job
wrote itself — different tenses (was/am/will), different determiners (my/the), with and without
the event's name — so the calendar read lane answers the SHAPE of the ask, not memorized
sentences. The same rig and the same seeded rows answer them; every read must carry the stored
row's own facts, and the three non-calendar asks must never touch the store.

LABELLED: disposable loopback Google-shaped fixture reached through the VOOL transport;
injected clock; no live provider, no model behind the test backend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests._person_turn_rig import make_agent, person_env, reply, turn  # noqa: F401  (fixtures)
from tests.pa_beta_gate._json_api_service import start_json_calendar_fixture
from tests.pa_beta_gate._pc_calendar_rig import Clock

NOW = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
ZONE = "Europe/Athens"
FAMILY_CAL = "family@fixture.test"
WORK_CAL = "work@fixture.test"
FAMILY_NAME = "Family Rhythms"
WORK_NAME = "Work Tracks"

MODEL_LEAD = "I couldn't get a live model response"

DENTIST = ("Dentist", "2026-09-23")          # Wednesday this week, 13:00
PLAY = ("School play", "2026-09-25")         # Friday this week, 18:00
SWIM = ("Swimming lesson", "2026-09-22")     # Tuesday, tomorrow, 13:30
RETRO = ("Team retro", "2026-10-01")         # Thursday NEXT week, 15:00
SEEDED_TOKENS = {fact for pair in (DENTIST, PLAY, SWIM, RETRO) for fact in pair} | {FAMILY_NAME, WORK_NAME}


def _seed(state) -> None:
    state.seed_event(FAMILY_CAL, "dentist@fixture", summary=DENTIST[0],
                     start=NOW + timedelta(days=2, hours=3), minutes=45, tz_name=ZONE)
    state.seed_event(FAMILY_CAL, "school-play@fixture", summary=PLAY[0],
                     start=NOW + timedelta(days=4, hours=8), minutes=120, tz_name=ZONE)
    state.seed_event(FAMILY_CAL, "swimming@fixture", summary=SWIM[0],
                     start=NOW + timedelta(days=1, hours=3, minutes=30), minutes=45, tz_name=ZONE)
    state.seed_event(WORK_CAL, "team-retro@fixture", summary=RETRO[0],
                     start=NOW + timedelta(days=10, hours=5), minutes=60, tz_name=ZONE)


@pytest.fixture
def store(person_env, monkeypatch):
    """The same connected, opted-in calendar store shape the recorded-defect suite seeds."""
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    clock = Clock(NOW, monkeypatch)
    server, state, base = start_json_calendar_fixture(
        dialect="google", calendars={FAMILY_CAL: FAMILY_NAME, WORK_CAL: WORK_NAME}
    )
    _seed(state)
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="google", base_url=base, label="Fresh wording fixture account")
    discovered = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock)
    assert discovered["ok"], discovered
    for calendar_id in (FAMILY_CAL, WORK_CAL):
        chosen = calendar_accounts.select_calendar(account["account_id"], calendar_id, selected=True)
        assert chosen["ok"], chosen
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    yield {"state": state, "requests": lambda: state.request_count}
    server.shutdown()
    server.server_close()


def _assert_read_the_store(result: dict, *facts: tuple[str, str]) -> str:
    text = reply(result)
    assert not text.startswith(MODEL_LEAD), f"a calendar read decayed into a toolless model reply: {text}"
    assert str(result.get("route") or "").startswith("action:operator_action"), (
        f"the calendar ask did not land in the operator action lane: {result.get('route')} :: {text}"
    )
    for word, date in facts:
        assert word in text, f"the reply does not carry the seeded row's title {word!r}: {text}"
        assert date in text, f"the reply does not carry the seeded row's date {date!r}: {text}"
    return text


def _assert_never_touched_the_store(result: dict, before: int, after: int) -> str:
    text = reply(result)
    route = str(result.get("route") or "")
    assert after == before, f"a non-calendar ask fetched the calendar ({after - before} requests): {text}"
    assert not route.startswith("action:operator_action"), f"the ask was captured by a calendar lane: {route} :: {text}"
    leaked = sorted(token for token in SEEDED_TOKENS if token in text)
    assert not leaked, f"the reply carries seeded calendar facts {leaked} it never read: {text}"
    return text


# -------------------------------------------------------------------------------------------
# Fresh wordings: the shape reads the store, not the recorded sentences
# -------------------------------------------------------------------------------------------


def test_when_is_my_swimming_lesson(store):
    """A my-determiner when-ask with the event's own words reads the Swimming lesson row."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "when is my swimming lesson", session="fw-when-swim"), SWIM)


def test_where_is_the_school_play(store):
    """A where-ask over a named event reads that event's row (the play)."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "where is the school play", session="fw-where-play"), PLAY)


def test_am_i_attending_the_school_play(store):
    """A present-tense participation ask ("am I attending") reads the play's row."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "am I attending the school play", session="fw-attending"), PLAY)


def test_was_the_dentist_this_week(store):
    """A past-tense wording ("was the dentist ...") still reads the store and answers with the
    appointment the rows carry — the search's own window covers the row it finds."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "was the dentist this week", session="fw-was-dentist"), DENTIST)


def test_what_am_i_doing_on_wednesday(store):
    """A doing-ask with a weekday and NO event name reads that day's real row (the dentist)."""
    agent = make_agent()
    text = _assert_read_the_store(turn(agent, "what am I doing on wednesday", session="fw-doing-wed"), DENTIST)
    for word, _date in (PLAY, SWIM, RETRO):
        assert word not in text, f"{word!r} is not Wednesday but was answered: {text}"


def test_do_we_have_anything_on_friday(store):
    """A we-form agenda ask with no event name reads Friday's real row (the play)."""
    agent = make_agent()
    text = _assert_read_the_store(turn(agent, "do we have anything on friday", session="fw-we-friday"), PLAY)
    for word, _date in (DENTIST, SWIM, RETRO):
        assert word not in text, f"{word!r} is not Friday but was answered: {text}"


# -------------------------------------------------------------------------------------------
# Non-calendar asks: the store is never read
# -------------------------------------------------------------------------------------------


def test_when_is_the_next_bus_never_touches_the_calendar(store):
    """"when is the next bus" is a live/world ask: no calendar request, no seeded fact, no
    calendar lane — even though its shape is a when-ask like the reads above."""
    agent = make_agent()
    before = store["requests"]()
    result = turn(agent, "when is the next bus", session="fw-neg-bus")
    _assert_never_touched_the_store(result, before, store["requests"]())


def test_show_me_the_photo_called_sunset_never_touches_the_calendar(store):
    """"show me the photo called sunset" is a file ask: the photo keeps its own lane and the
    calendar store stays untouched."""
    agent = make_agent()
    before = store["requests"]()
    result = turn(agent, "show me the photo called sunset", session="fw-neg-photo")
    _assert_never_touched_the_store(result, before, store["requests"]())


def test_whats_the_time_in_tokyo_never_touches_the_calendar(store):
    """"what's the time in Tokyo" is a clock ask: the calendar store stays untouched."""
    agent = make_agent()
    before = store["requests"]()
    result = turn(agent, "what's the time in Tokyo", session="fw-neg-clock")
    _assert_never_touched_the_store(result, before, store["requests"]())


@pytest.mark.parametrize(
    "ask",
    [
        "where is the eiffel tower",
        "where is the nearest pharmacy",
        "where is the nearest doctor",
        "where is the bathroom",
        "where is the settings page",
        "when is the world cup final",
        "when is the election",
        "when is the best time to plant tomatoes",
    ],
)
def test_a_world_or_place_question_never_touches_the_calendar(store, ask):
    """A bare when/where ask shares the named-event frame's shape, but a subject with no event of
    the person's own (a landmark, a room, a public date) is not a calendar row: the store stays
    untouched and no calendar lane answers "No event matching"."""
    agent = make_agent()
    before = store["requests"]()
    result = turn(agent, ask, session="fw-neg-world")
    _assert_never_touched_the_store(result, before, store["requests"]())
