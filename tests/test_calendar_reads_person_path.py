"""Person-path tests: calendar read asks answered from the provider's own rows.

Every ask is driven through the turn function the daemon itself calls for a turn
(``VoolAgent.run_once`` via the shared person rig) on a real agent whose only calendar
store is a seeded loopback provider: two calendars with distinctive names and events at
known instants around a fixed injected clock. A read ask must answer FROM THE ROWS (the
reply carries the real event's date/title/calendar-name derived from the seeded data), a
read ask with no store must say plainly that no calendar is connected, and a non-calendar
ask must not touch the store at all (the loopback service's own request counter stays
flat and the reply carries no seeded facts). A read ask is never allowed to decay into a
toolless model reply, which on the model-less test backend is easy to recognise.

LABELLED: disposable loopback Google-shaped fixture reached through the VOOL transport;
injected clock; no live provider, no model behind the test backend.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests._person_turn_rig import make_agent, person_env, reply, turn
from tests.pa_beta_gate._json_api_service import start_json_calendar_fixture
from tests.pa_beta_gate._pc_calendar_rig import Clock

# Monday 2026-09-21 07:00 UTC = 10:00 in the saved zone. Every window word ("tomorrow",
# "this week", "next week", "friday") resolves against this instant, so the expected rows
# and dates are stable no matter when the suite runs.
NOW = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
ZONE = "Europe/Athens"
FAMILY_CAL = "family@fixture.test"
WORK_CAL = "work@fixture.test"
FAMILY_NAME = "Family Rhythms"
WORK_NAME = "Work Tracks"

# The model lane's own lead when no model is reachable on the test backend. A calendar
# read whose reply starts with this did not read anything.
MODEL_LEAD = "I couldn't get a live model response"

# Distinctive facts only the seeded rows carry (dates are the Athens renderings).
DENTIST = ("Dentist", "2026-09-23")          # Wednesday this week, 13:00
PLAY = ("School play", "2026-09-25")         # Friday this week, 18:00
SWIM = ("Swimming lesson", "2026-09-22")     # Tuesday, tomorrow, 13:30
RETRO = ("Team retro", "2026-10-01")         # Thursday NEXT week, 15:00
SEEDED_FACTS = (DENTIST, PLAY, SWIM, RETRO)
SEEDED_TOKENS = {fact for pair in SEEDED_FACTS for fact in pair} | {FAMILY_NAME, WORK_NAME}


def _seed(state) -> None:
    """Distinctive events around the fixed clock: tomorrow, this week, and next week."""
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
    """A connected, opted-in calendar store: two named calendars, four seeded events."""
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    clock = Clock(NOW, monkeypatch)
    server, state, base = start_json_calendar_fixture(
        dialect="google", calendars={FAMILY_CAL: FAMILY_NAME, WORK_CAL: WORK_NAME}
    )
    _seed(state)
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="google", base_url=base, label="Family fixture account")
    discovered = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock)
    assert discovered["ok"], discovered
    for calendar_id in (FAMILY_CAL, WORK_CAL):
        chosen = calendar_accounts.select_calendar(account["account_id"], calendar_id, selected=True)
        assert chosen["ok"], chosen
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    yield {"state": state, "requests": lambda: state.request_count}
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------------------------
# Helpers: what every read ask owes the person
# ---------------------------------------------------------------------------------------------


def _assert_not_toolless(result: dict) -> str:
    """The reply came from a lane that read something, not from a model-less fallback."""
    text = reply(result)
    assert not text.startswith(MODEL_LEAD), f"a calendar read decayed into a toolless model reply: {text}"
    assert str(result.get("route") or "") not in {"model_minimal:local", "model:local"}, (
        f"the calendar ask was routed to the model lane: {result.get('route')} :: {text}"
    )
    return text


def _assert_read_the_store(result: dict, *facts: tuple[str, str]) -> str:
    """The reply is owned by the calendar lane AND carries the seeded row's own facts."""
    text = _assert_not_toolless(result)
    assert str(result.get("route") or "").startswith("action:operator_action"), (
        f"the calendar ask did not land in the operator action lane: {result.get('route')} :: {text}"
    )
    for word, date in facts:
        assert word in text, f"the reply does not carry the seeded row's title {word!r}: {text}"
        assert date in text, f"the reply does not carry the seeded row's date {date!r}: {text}"
    return text


def _assert_never_touched_the_store(result: dict, before: int, after: int) -> str:
    """A non-calendar ask: no provider request, no seeded fact, no calendar lane."""
    text = reply(result)
    route = str(result.get("route") or "")
    assert after == before, f"a non-calendar ask fetched the calendar ({after - before} requests): {text}"
    assert not route.startswith("action:operator_action"), f"the ask was captured by a calendar lane: {route} :: {text}"
    leaked = sorted(token for token in SEEDED_TOKENS if token in text)
    assert not leaked, f"the reply carries seeded calendar facts {leaked} it never read: {text}"
    return text


# ---------------------------------------------------------------------------------------------
# Read asks answered from the rows (given wordings)
# ---------------------------------------------------------------------------------------------


def test_next_week_agenda_is_answered_from_the_rows(store):
    """"whats on my calendar next week" reads the store and answers with next week's real
    row (title, date, calendar name) — and keeps this week's rows out of the window."""
    agent = make_agent()
    text = _assert_read_the_store(turn(agent, "whats on my calendar next week", session="cal-next-week"), RETRO)
    assert FAMILY_NAME not in text and WORK_NAME in text, text
    for word, date in (DENTIST, PLAY, SWIM):  # this week's events stay outside "next week"
        assert word not in text, f"{word!r} is not next week but was answered: {text}"


def test_which_calendars_names_them_from_the_store(store):
    """"what calendars do i have" lists the store's own calendar names through the person path."""
    agent = make_agent()
    text = _assert_read_the_store(turn(agent, "what calendars do i have", session="cal-which"), (FAMILY_NAME, ""), (WORK_NAME, ""))
    assert FAMILY_CAL in text or WORK_CAL in text, text


def test_dentist_any_time_soon_reads_the_store(store):
    """"am I seeing the dentist any time soon" must read the rows and answer with the real
    appointment (title and date), never a toolless model reply."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "am I seeing the dentist any time soon", session="cal-dentist-soon"), DENTIST)


def test_when_is_the_school_play_reads_the_store(store):
    """"when is the school play" must read the rows and answer with the real event date,
    never a toolless model reply."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "when is the school play", session="cal-when-play"), PLAY)


# ---------------------------------------------------------------------------------------------
# Read asks answered from the rows (own wordings, same behaviour)
# ---------------------------------------------------------------------------------------------


def test_tomorrow_wording_picks_the_tomorrow_row(store):
    """"what do I have tomorrow" answers with tomorrow's real row and nothing else."""
    agent = make_agent()
    text = _assert_read_the_store(turn(agent, "what do I have tomorrow", session="cal-tomorrow"), SWIM)
    for word, _date in (DENTIST, PLAY, RETRO):
        assert word not in text, f"{word!r} is not tomorrow but was answered: {text}"


def test_list_my_calendars_wording(store):
    """"list my calendars" lists the store's calendar names through the person path."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "list my calendars", session="cal-list-mine"), (FAMILY_NAME, ""), (WORK_NAME, ""))


def test_quoted_title_search_reads_the_row(store):
    """A title search with a quoted word finds the real event and says when it is."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, 'search my calendar for "dentist"', session="cal-search-quoted"), DENTIST)


def test_inspect_wording_answers_from_the_event_row(store):
    """"show me the event called School play" reads that event's stored row (title and date)."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "show me the event called School play", session="cal-inspect"), PLAY)


def test_unquoted_search_wording_reads_the_row(store):
    """A person does not quote the word: "search my calendar for dentist" must search, not
    ask which event while a Dentist row sits in the store."""
    agent = make_agent()
    result = turn(agent, "search my calendar for dentist", session="cal-search-plain")
    text = _assert_read_the_store(result, DENTIST)
    assert "Which event" not in text, text


def test_anything_on_tomorrow_wording_reads_the_store(store):
    """"do I have anything on tomorrow" reads the rows (tomorrow's real event), never a
    toolless model reply."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "do I have anything on tomorrow", session="cal-on-tomorrow"), SWIM)


def test_this_weeks_appointments_wording_reads_the_store(store):
    """"show me this week's appointments" answers with this week's three real rows."""
    agent = make_agent()
    text = _assert_read_the_store(
        turn(agent, "show me this week's appointments", session="cal-this-week"), DENTIST, PLAY, SWIM
    )
    assert RETRO[0] not in text, f"next week's row is not this week: {text}"


def test_anything_coming_up_with_the_dentist_reads_the_store(store):
    """"anything coming up with the dentist" reads the rows and answers with the real
    appointment."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "anything coming up with the dentist", session="cal-coming-up"), DENTIST)


def test_is_the_school_play_soon_reads_the_store(store):
    """"is the school play soon" reads the rows and answers from the real event date."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "is the school play soon", session="cal-play-soon"), PLAY)


def test_what_have_i_got_on_friday_reads_the_store(store):
    """"what have I got on friday" reads the rows: this Friday's real event is the school
    play."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "what have I got on friday", session="cal-friday"), PLAY)


def test_where_is_the_dentist_appointment_reads_the_store(store):
    """"where is the dentist appointment" reads the event's row and answers about the real
    appointment (its date; the seeded row stores no place)."""
    agent = make_agent()
    _assert_read_the_store(turn(agent, "where is the dentist appointment", session="cal-where-dentist"), DENTIST)


# The given wordings above sat behind their own strict xfail markers while the routing defect
# stood; the calendar read lane owns them now, so the markers are gone and the asks must pass.


# ---------------------------------------------------------------------------------------------
# No store connected: plain words, not model prose
# ---------------------------------------------------------------------------------------------


def test_no_store_agenda_ask_says_plainly_no_calendar(person_env, monkeypatch):
    """With nothing connected, a calendar read ask says plainly there is no calendar to
    read — it never falls back to a toolless model reply."""
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    Clock(NOW, monkeypatch)
    agent = make_agent()
    result = turn(agent, "whats on my calendar next week", session="cal-none-agenda")
    text = _assert_not_toolless(result)
    assert str(result.get("route") or "").startswith("action:operator_action"), result.get("route")
    assert "calendar" in text.lower(), text
    assert MODEL_LEAD not in text


def test_no_store_calendar_list_ask_says_plainly_no_provider(person_env, monkeypatch):
    """With no provider configured, 'what calendars do i have' says so in plain words."""
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    Clock(NOW, monkeypatch)
    agent = make_agent()
    result = turn(agent, "what calendars do i have", session="cal-none-list")
    text = _assert_not_toolless(result)
    assert str(result.get("route") or "").startswith("action:operator_action"), result.get("route")
    assert "calendar" in text.lower(), text
    assert MODEL_LEAD not in text


def test_no_store_event_ask_says_plainly_no_calendar(person_env, monkeypatch):
    """With nothing connected, an event ask names the calendar in plain words rather than
    a toolless model reply."""
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    Clock(NOW, monkeypatch)
    agent = make_agent()
    for ask, session in (
        ("am I seeing the dentist any time soon", "cal-none-dentist"),
        ("when is the school play", "cal-none-play"),
    ):
        result = turn(agent, ask, session=session)
        text = _assert_not_toolless(result)
        assert "calendar" in text.lower(), text


# ---------------------------------------------------------------------------------------------
# Negatives: asks that must never read the store
# ---------------------------------------------------------------------------------------------


def test_non_calendar_asks_never_touch_the_store(store):
    """Four non-calendar asks: the loopback provider's request counter stays flat, the
    reply carries no seeded fact, and no calendar lane captures the turn."""
    agent = make_agent()
    for ask, session in (
        ("i hate calendars", "cal-neg-hate"),
        ("what is a leap year", "cal-neg-leap"),
        ("write me a poem about mondays", "cal-neg-poem"),
        ("what does calendar mean in spanish", "cal-neg-spanish"),
    ):
        before = store["requests"]()
        result = turn(agent, ask, session=session)
        _assert_never_touched_the_store(result, before, store["requests"]())
