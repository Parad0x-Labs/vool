"""Four routing gaps of the v0.8 line, each read by the message's own judgement or by its shape, never by its words.

N-011  A statement with a topic word ("good news, ...") was refused "Web lookups are off": every reader before the
       refusal reads topic words. The refusal now stands only when the message's own web yes/no (any language) says it
       needs the internet; with no model to judge it the words decide, as before.
V-048  An order to run something was admitted to the read-only clean loop unless an English run verb opened it ("start
       the tests and list the failures", any order in another language): the loop now also reads the turn judge's pick.
N-012  A fragment cut after a comma had its span one character early, so the execution text lost its last letter
       ("... viešbutis Jūr"); and a clause of a message judged a file write was claimed as a READ by its file name,
       so the rest of the list ran as a second request. The run_tests card showed the workspace folder twice.
L29/105 "tonight at 11:30" was set for 11:30 in the morning.

The judge is scripted (no model); the wordings are built from parts at random so no sentence is a fixed answer.
"""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core import intent_arbiter, plugin_ask
from core.request_trust import OWNER_LOCAL_KEY

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw"}
KEYS = [key for key, _description, _seats in plugin_ask.BUILTIN_CHOICES]


@pytest.fixture(autouse=True)
def _clean():
    from core.agent_runtime.answer_coverage import interpret_request
    from core.online_asks import forget_web_verdicts

    plugin_ask.forget()
    forget_web_verdicts()
    interpret_request.cache_clear()
    yield
    plugin_ask.forget()
    forget_web_verdicts()
    interpret_request.cache_clear()


def _judge(monkeypatch, keys: tuple[str, ...]) -> None:
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(
        intent_arbiter, "arbitrate_plugin_ask", lambda message, choices, **_k: tuple(KEYS.index(k) + 1 for k in keys)
    )


# ------------------------------------------------------------------------------------------------ N-011

_OPENERS = ("good news", "great news", "big news", "some news", "happy news")
_TOLD = (
    "the plumber finally fixed the boiler", "I got my old desk back at work", "my cousin's flight landed safely",
    "the vet says the dog is fine", "maintenance is over and I'm back in my old office",
)


def _turn(text: str, session: str) -> str:
    from tests.test_web_lookups_switch_gates_live_data import _ask

    return _ask(text, session=session)


@pytest.mark.parametrize("seed", range(3))
def test_a_statement_judged_no_web_is_not_refused_as_a_lookup(seed, monkeypatch):
    from core.remote_fetch_policy import WEB_OFF_LOOKUP_TEXT, WEB_OFF_TEXT

    rng = random.Random(f"n011-{seed}")
    text = f"{rng.choice(_OPENERS)}{rng.choice((', ', ': ', '! '))}{rng.choice(_TOLD)}"
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda message, **_k: asked.append(message) or "no")
    reply = _turn(text, f"n011-no-{seed}")
    assert WEB_OFF_LOOKUP_TEXT not in reply and WEB_OFF_TEXT not in reply, (text, reply)
    assert len(asked) == 1, asked   # one judgement per message, however many readers ask


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_lookup_the_judgement_confirms_or_nothing_judged_is_still_refused(verdict, monkeypatch):
    from core.remote_fetch_policy import WEB_OFF_LOOKUP_TEXT

    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda message, **_k: verdict)
    reply = _turn("give me the latest news on the rail strike", f"n011-{verdict}")
    assert WEB_OFF_LOOKUP_TEXT in reply, reply


# ------------------------------------------------------------------------------------------------ V-048

_RUNS = (
    "start the tests and list the failures", "kick off the build and tell me what broke",
    "spin up the dev server and tell me if it crashes", "paleisk testus ir parodyk klaidas",
    "starte die Tests und zeig mir die Fehler", "lanza las pruebas y enséñame los errores",
)


@pytest.mark.parametrize("text", _RUNS)
def test_an_order_judged_a_run_never_takes_the_read_only_loop(text, monkeypatch):
    from core.clean_loop import route

    _judge(monkeypatch, ("run",))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="v048")
    decision = route.decide(text, source_context=OWNER, resolve_brain=lambda: object(), env={})
    assert not decision.admit and decision.reason == "run_turn", (text, decision)


def test_a_tool_the_loop_does_not_hold_keeps_the_normal_lane_and_a_read_does_not(monkeypatch):
    from core.clean_loop import route

    text = "pasižymėk priminimą šeštą peržiūrėti strigčių žurnalus"
    _judge(monkeypatch, ("reminders",))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="v048")
    decision = route.decide(text, source_context=OWNER, resolve_brain=lambda: object(), env={})
    # The judged reminder seats the reminder tools, which the operator reading then names too.
    assert not decision.admit and decision.reason in {"judged_tool_turn", "operator_turn"}, decision
    assert route._judged_tools("why is my laptop so slow lately") == frozenset()


def test_nothing_judged_leaves_the_words_to_decide():
    from core.clean_loop import route

    assert route.decide("Run the tests and show me the errors", source_context=OWNER, resolve_brain=lambda: object(),
                        env={}).reason == "run_turn"
    assert route._judged_tools("start the tests and list the failures") == frozenset()


# ------------------------------------------------------------------------------------------------ N-012

_ITEMS = ("Palanga", "liepos 10 d.", "viešbutis Jūra", "Zelt", "Schlafsack und Lampe", "tienda", "saco de dormir",
          "žibintuvėlis", "Ölkanne", "café", "naïve übung")


@pytest.mark.parametrize("seed", range(8))
def test_every_fragment_span_holds_exactly_its_words(seed):
    from core.agent_runtime.answer_coverage import interpret_request
    from core.agent_runtime.demand_ownership import execution_units

    rng = random.Random(f"n012-span-{seed}")
    items = rng.sample(_ITEMS, 4)
    text = f"{rng.choice(('Sukurk failą a.txt su tekstu:', 'Erstelle b.md mit', 'Crea c.txt con'))} " + ", ".join(items)
    for unit in interpret_request(text).units:
        assert text[unit.start:unit.end] == unit.text, (text, unit)
    # Whatever the grouping, the last item reaches an execution unit whole.
    assert any(text.rstrip().endswith(unit_text) for _id, unit_text in execution_units(text)), text


@pytest.mark.parametrize("seed", range(6))
def test_a_message_judged_a_file_write_runs_as_one_request(seed, monkeypatch):
    from core.agent_runtime.demand_ownership import execution_units

    rng = random.Random(f"n012-one-{seed}")
    items = rng.sample(_ITEMS, 3)
    text = rng.choice((
        "Erstelle {f} mit {a}, {b} und {c}", "Sukurk failą {f} su tekstu: {a}, {b}, {c}",
        "Prašau, sukurk failą {f}: {a}, {b} ir {c}", "Crea el archivo {f} con: {a}, {b} y {c}",
    )).format(f=f"list{rng.randint(1, 99)}.txt", a=items[0], b=items[1], c=items[2])
    _judge(monkeypatch, ("files",))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="n012")
    units = execution_units(text)
    assert len(units) == 1 and units[0][1] == text.strip(), (text, units)


def test_the_run_card_shows_the_folder_the_command_runs_in(tmp_path):
    from core.mode_permission_policy import _affected_resources_display

    root = tmp_path / "garden-app"
    (root / "src").mkdir(parents=True)
    context = {"workspace": str(root)}
    for cwd in ("garden-app", "./garden-app", ".", "workspace"):
        assert _affected_resources_display({"cwd": cwd, "command": "pytest"}, context) == [str(root)], cwd
    assert _affected_resources_display({"cwd": "src"}, context) == [f"{root}/src"]
    assert _affected_resources_display({"path": "notes.txt"}, context) == [f"{root}/notes.txt"]


# ------------------------------------------------------------------------------------------------ L29 row 105

@pytest.mark.parametrize("said,hour", [
    ("tonight at 11:30", 23), ("this evening at 8:15", 20), ("this afternoon at 3:45", 15), ("at 9:05 tonight", 21),
    ("at 9:30", 9), ("at 07:30 tonight", 7), ("at 19:30", 19), ("this morning at 10:30", 10),
])
def test_a_twelve_hour_clock_lands_on_the_side_of_noon_the_sentence_names(said, hour):
    from core.operator.when import parse_when_expression

    now = datetime(2026, 11, 23, 6, 0, tzinfo=ZoneInfo("Europe/Vilnius"))
    resolution = parse_when_expression(said, now_fn=lambda: now)
    assert resolution.ok and datetime.fromisoformat(resolution.due_wall).hour == hour, (said, resolution)


@pytest.mark.parametrize("text", ["remind me at six to look at the crash logs", "remind me at 6pm to check the disk space"])
def test_a_reminder_order_with_a_machine_word_is_not_the_loops(text):
    from core.clean_loop import route

    assert route.decide(text, source_context=OWNER, resolve_brain=lambda: object(), env={}).reason == "operator_turn"


@pytest.mark.parametrize("local_now", ["2026-11-24T00:05:00", "2026-11-24T02:40:00", "2026-11-24T03:59:00"])
def test_tomorrow_said_in_the_small_hours_is_asked_with_both_dates(local_now):
    from core.operator.when import parse_when_expression

    now = datetime.fromisoformat(local_now).replace(tzinfo=ZoneInfo("Europe/Vilnius"))
    for said in ("tomorrow at 9", "remind me tomorrow morning to water the plants", "tomorrow 18:30"):
        resolution = parse_when_expression(said, now_fn=lambda: now)
        assert not resolution.ok, said
        assert "Tuesday 24 November" in resolution.problem and "Wednesday 25 November" in resolution.problem, said
    # The two answers the question offers each settle the day.
    today = parse_when_expression("today at 9", now_fn=lambda: now)
    after = parse_when_expression("the 25th at 9", now_fn=lambda: now)
    assert today.ok and today.due_wall.startswith("2026-11-24T09:00"), today
    assert after.ok and after.due_wall.startswith("2026-11-25T09:00"), after


@pytest.mark.parametrize("local_now,day", [("2026-11-24T04:00:00", 25), ("2026-11-23T23:50:00", 24)])
def test_tomorrow_outside_the_small_hours_is_the_next_date(local_now, day):
    from core.operator.when import parse_when_expression

    now = datetime.fromisoformat(local_now).replace(tzinfo=ZoneInfo("Europe/Vilnius"))
    resolution = parse_when_expression("tomorrow at 9", now_fn=lambda: now)
    assert resolution.ok and datetime.fromisoformat(resolution.due_wall).day == day, resolution


@pytest.mark.parametrize("memory", ["memory_recall", "memory_keep"])
def test_a_web_pick_beside_a_memory_pick_is_dropped_without_asking(memory, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda message, **_k: asked.append(message) or "yes")
    kept = plugin_ask._web_confirmed("prmink kokios spalvos mano paspirtukas", ("web", memory), OWNER, "m")
    assert kept == (memory,) and asked == []


@pytest.mark.parametrize("text,keys", [
    ("start the tests in this project and list what failed", ("run",)),
    ("run the tests for this project and list the failures", ()),
    ("paleisk šio projekto testus ir išvardink, kas nepavyko", ("run",)),
])
def test_a_run_order_naming_the_project_is_not_answered_with_a_listing(text, keys, monkeypatch, tmp_path):
    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request

    (tmp_path / "calc.py").write_text("x = 1\n")
    _judge(monkeypatch, keys)
    if keys:
        plugin_ask.judge_turn(text, source_context=OWNER, session_id="c4")
    context = {**OWNER, "workspace": str(tmp_path)}
    assert maybe_handle_folder_overview_request(
        object(), text, session_id="c4", source_surface="openclaw", source_context=context
    ) is None, text


@pytest.mark.parametrize("text,keys", [
    ("kick off the test suite here and tell me which ones broke", ("run",)),
    ("execute the project tests and say which ones broke", ()),
])
def test_a_run_order_is_not_a_source_inquiry(text, keys, monkeypatch):
    from core.conductor.operations import _workspace_source_inquiry

    _judge(monkeypatch, keys)
    if keys:
        plugin_ask.judge_turn(text, source_context=OWNER, session_id="d1")
    assert not _workspace_source_inquiry(text)
    assert not _workspace_source_inquiry(text.split(" and ")[0])   # the clause the conductor reads
    assert _workspace_source_inquiry("which file in this project implements the test suite runner")
