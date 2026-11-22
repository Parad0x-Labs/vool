"""VOOL's own personal tools and every added plugin are offered when a person asks for them in any language.

Outside review of 261a0389 (2026-11-22), first offers measured with the release line's own offer assembly: a reminder
set, listed or cancelled in Lithuanian or German, a contact saved in Lithuanian or German, unread mail in Spanish and a
phone send in Lithuanian all reached the model without the tool; only English word rules seated them. Plugins already
had a judge for this (core.plugin_ask), but it showed only the first 8 plugins, picked one, and a failed call fell back
to English words without asking again.

Now one judgement at the start of the turn covers both: the added plugins (all of them) and, when the words seat none
of VOOL's own personal tools on an owner-local turn, those tools too. More than one may be picked. A failed call is
asked once more. The judge is scripted here (no model); the wordings are built from parts at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, plugin_ask
from core.capability_graph import model_visible_specs
from core.request_trust import OWNER_LOCAL_KEY

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-any-language"}
KEYS = [key for key, _description, _seats in plugin_ask.BUILTIN_CHOICES]

ASKS = {
    "reminders": ("Primink man rytoj {n} val. nupirkti duonos", "Erinnere mich morgen um {n} Uhr an den Zahnarzt",
                  "Kokie mano priminimai šiai savaitei?", "Lösch die Erinnerung für den Müll"),
    "contacts": ("Išsaugok Ievą Kalnienę, telefonas +370 6{n}0 12345", "Speichere Anna Berg mit der Nummer 0170 {n}234567",
                 "Kas yra Jono telefono numeris?"),
    "calendar": ("Ar esu laisvas penktadienį {n} val.?", "Was steht morgen in meinem Kalender?"),
    "mail": ("¿tengo correos sin leer?", "Ar gavau naujų laiškų šiandien?"),
    "notes": ("Užsirašyk: spintos raktas po kilimėliu {n}", "Finde meine Notiz über das Rezept"),
    "phone": ("Nusiųsk sąskaitą {n} į mano telefoną", "Schick die PDF-Rechnung {n} auf mein Handy",
              "shoot invoice {n} over to my phone"),
    # Tooling audit items 5 and 7: a saved household thing, and the open folder's changes since the last commit.
    "things": ("Kada baigiasi mano skalbimo mašinos garantija?", "Welche Seriennummer hat mein Geschirrspüler?",
               "where did I put the manual for the boiler, it's in my things"),
    "changes": ("Kokius failus pakeičiau šiame aplanke nuo paskutinio commit?",
                "Was habe ich in diesem Ordner seit dem letzten Commit geändert?",
                "list the files I edited but haven't committed yet",
                "what have I changed in this folder since the last commit"),
    # Blind audit of 58f60bbf3: a file made, tests run, the web asked for in other words and other languages.
    "files": ("Sukurk naują failą sąrašas{n}.txt ir įrašyk į jį pieną", "Leg eine Datei notizen{n}.md mit drei Zeilen an",
              "Crea un archivo tareas{n}.txt con dos líneas", "make a file called groceries{n}.txt that says eggs"),
    "read": ("Kas parašyta faile planas{n}.txt?", "Zeig mir, was in notizen{n}.md steht", "¿Qué dice el archivo tareas{n}.txt?"),
    "run": ("Paleisk šio projekto testus ir pasakyk kiek nepraėjo", "Lass die Tests im Projekt laufen, Versuch {n}",
            "Ejecuta las pruebas del proyecto, por favor"),
    "web": ("Paieškok internete, kada šiandien atidaryta Vilniaus {n} biblioteka", "Busca en internet el tiempo de mañana en Bilbao",
            "Schau im Netz nach, wann der Zug {n} nach Hamburg fährt"),
}


@pytest.fixture(autouse=True)
def _clean():
    from core import capability_graph as cg

    plugin_ask.forget()
    cg.reset()
    yield
    plugin_ask.forget()
    cg.reset()


def _script(monkeypatch, answer_for, calls):
    def arbitrate(message, choices, **kwargs):
        calls.append((message, list(choices)))
        return answer_for(message, choices)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: "yes")


def _offered(text: str) -> set[str]:
    return {str(spec.get("intent") or "") for spec in model_visible_specs(user_text=text, source_context=OWNER)}


@pytest.mark.parametrize("seed", range(12))
def test_a_personal_tool_asked_in_another_language_is_offered(seed, monkeypatch):
    rng = random.Random(f"any-language-{seed}")
    key = rng.choice([k for k in KEYS if k != "mail"])
    text = rng.choice(ASKS[key]).format(n=rng.randint(2, 9))
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (KEYS.index(key) + 1,), calls)
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-any-language")
    assert len(calls) == 1 and len(calls[0][1]) == len(KEYS), calls
    assert plugin_ask.judged_builtins(text) == (key,)
    seats = dict((k, seats) for k, _d, seats in plugin_ask.BUILTIN_CHOICES)[key]
    offered = _offered(text)
    assert offered & set(seats), (text, key, sorted(offered))


def test_words_that_already_seat_a_personal_tool_ask_no_judge(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (), calls)
    rng = random.Random("settled")
    text = f"{rng.choice(['remind me', 'please remind me'])} to call the bank tomorrow at {rng.randint(2, 9)}pm"
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-any-language")
    assert calls == []


def test_an_ask_about_this_computers_files_still_reaches_the_judge(monkeypatch):
    # "files" is this computer's own read, which none of the choices is: an ask about the folder's changes since the
    # last commit seats it by its words, and the judge must still be asked (tooling audit item 7). A source a choice
    # reads (notes, calendar, mail) settles the ask as before.
    from core.tool_demand_signals import owner_live_sources

    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (KEYS.index("changes") + 1,), calls)
    text = "what have I changed in this folder since the last commit"
    assert "files" in owner_live_sources(text)
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-any-language")
    assert len(calls) == 1 and plugin_ask.judged_builtins(text) == ("changes",)
    assert {"workspace.git_status", "workspace.git_diff"} <= _offered(text)


def test_a_memory_command_asks_no_judge(monkeypatch):
    # The memory lane answers remember/forget before any tool is offered; a judge call there only cost the turn a
    # model call, and with the model down a forget turn waited on it (CI on 58f60bbf3, file lock order test).
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (1,), calls)
    rng = random.Random("memory-commands")
    for text in (f"Forget storage label MINT-{rng.randint(10, 99)}", f"Remember that my locker is number {rng.randint(10, 99)}"):
        plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-any-language")
    assert calls == []


def test_a_remote_turn_is_never_asked_about_this_machines_tools(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (1,), calls)
    plugin_ask.judge_turn("Primink man rytoj 9 val. paskambinti", source_context={OWNER_LOCAL_KEY: False}, session_id="x")
    assert calls == [] and plugin_ask.judged_builtins("Primink man rytoj 9 val. paskambinti") == ()


def test_every_added_plugin_is_shown_and_more_than_one_can_be_picked(monkeypatch):
    rng = random.Random("many-plugins")
    plugins = [(f"plugin-{index}-{rng.randint(100, 999)}", f"Tool {index}: does thing {index}.") for index in range(12)]
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(plugins))
    wanted = sorted(rng.sample(range(8, 12), 2))   # beyond the eight the judge used to see
    calls: list = []
    _script(monkeypatch, lambda _m, _c: tuple(index + 1 for index in wanted), calls)
    text = "padaryk " + " ir ".join(f"dalyką {index}" for index in wanted)
    assert plugin_ask.judge_turn(text, source_context=OWNER, session_id="x") == tuple(plugins[i][0] for i in wanted)
    assert all(description in calls[0][1] for _id, description in plugins)


def test_a_plugin_and_a_personal_tool_in_one_message(monkeypatch):
    plugins = [("parcel-lookup", "Parcel lookup: finds where a parcel is by its tracking code.")]
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: list(plugins))
    calls: list = []
    _script(monkeypatch, lambda _m, _c: (1, 1 + 1 + KEYS.index("reminders")), calls)
    text = "Kur mano siunta PX-4410-C? Ir primink man rytoj 10 val. ją pasiimti"
    assert plugin_ask.judge_turn(text, source_context=OWNER, session_id="x") == ("parcel-lookup",)
    assert plugin_ask.judged_builtins(text) == ("reminders",)


def test_a_failed_judge_is_asked_once_more(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    answers = [None, (KEYS.index("calendar") + 1,)]
    calls: list = []
    _script(monkeypatch, lambda _m, _c: answers[len(calls) - 1], calls)
    text = "Ar esu laisvas penktadienį 3 val.?"
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert len(calls) == 2 and plugin_ask.judged_builtins(text) == ("calendar",)


def test_the_judges_answer_reader_takes_several_numbers_and_refuses_the_rest():
    read = intent_arbiter._numbers_reader(4)
    assert read('{"answer": [2, 4, 2]}') == "2,4"
    assert read('{"answer": []}') == "0" and read('{"answer": 0}') == "0"
    assert read('{"answer": [5]}') is None and read("not json") is None
