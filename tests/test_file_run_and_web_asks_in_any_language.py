"""A file made or changed, the project's tests run, and the web asked for, in any language and any words.

Blind audit of 58f60bbf3 (2026-11-22), reproduced on the app from source with qwen3:8b: "Sukurk naują failą
pirkiniai.txt ir įrašyk į jį: pienas, duona, sviestas" and "Parašyk python failą skaiciuotuvas.py ... ir paleisk
testus" were both answered "There is no file at ...": the read lane takes every named file as a read unless an
English edit verb stands beside it, and no writing or running tool was ever offered. A Lithuanian web ask got no
web tool. The turn's judge (core.plugin_ask) now has a choice for each; its answer stands the read lane down and
seats the tools. The judge is scripted here (no model); wordings are built from parts at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, plugin_ask
from core.agent_runtime.fast_paths_utility import _direct_workspace_read_request
from core.execution_requirements import requirements_for
from core.request_trust import OWNER_LOCAL_KEY
from core.tool_demand_signals import resolve_demand_signals

OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-files-any-language"}
KEYS = [key for key, _description, _seats in plugin_ask.BUILTIN_CHOICES]
SEATS = {key: seats for key, _description, seats in plugin_ask.BUILTIN_CHOICES}

_NAMES = ("pirkiniai", "sarasas", "notizen", "tareas", "plan", "skaiciuotuvas", "calc")
_EXTS = ("txt", "md", "py", "csv")
_MAKE = (
    "Sukurk naują failą {f} ir įrašyk į jį: {w}",
    "sukurk faila {f} ir irasyk i ji {w}",            # no accents
    "Parašyk python failą {f} su funkcija ir paleisk testus",
    "Leg bitte die Datei {f} an und schreib {w} hinein",
    "Crea el archivo {f} y escribe dentro: {w}",
    "pls mak a fiel {f} with {w} in it",               # typos
    "Pridėk eilutę „{w}“ į {f}",
)
_WORDS = ("pienas, duona", "Milch und Brot", "huevos y harina", "eggs, flour")


@pytest.fixture(autouse=True)
def _clean():
    from core import capability_graph as cg

    plugin_ask.forget()
    cg.reset()
    yield
    plugin_ask.forget()
    cg.reset()


def _judge(monkeypatch, keys: tuple[str, ...], web: str | None = "yes") -> list:
    calls: list = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_web_ask", lambda *_a, **_k: web)

    wanted = {description for key, description, _seats in plugin_ask.BUILTIN_CHOICES if key in keys}

    def arbitrate(message, choices, **kwargs):
        # By the choices shown, so a second reading over a shorter list still picks what is meant.
        calls.append(message)
        return tuple(number for number, choice in enumerate(choices, 1) if choice in wanted)

    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    return calls


def _ask(rng: random.Random) -> str:
    name = f"{rng.choice(_NAMES)}{rng.randint(1, 99)}.{rng.choice(_EXTS)}"
    return rng.choice(_MAKE).format(f=name, w=rng.choice(_WORDS))


@pytest.mark.parametrize("seed", range(10))
def test_a_file_asked_to_be_made_is_not_read_and_the_writer_is_seated(seed, monkeypatch):
    rng = random.Random(f"make-a-file-{seed}")
    text = _ask(rng)
    keys = ("files", "run") if "test" in text else ("files",)
    calls = _judge(monkeypatch, keys)
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert calls, text   # the words alone seated no writer, so the judge was asked
    assert _direct_workspace_read_request(text) is None, text
    signals = resolve_demand_signals(text)
    assert "workspace.write_file" in signals.explicit_intents and signals.write_demand, (text, signals)
    if "run" in keys:
        assert "workspace.run_tests" in signals.explicit_intents, (text, signals)
    # ...and the turn may not stay in the tools-less chat lane, where the model promised the file and wrote nothing.
    required = requirements_for(text, task_class="unknown", source_context=OWNER)
    assert required.forbids_toolless_lane() and "workspace" in required.allowed_toolsets, (text, required)


@pytest.mark.parametrize("seed", range(6))
def test_a_file_the_judge_reads_as_no_work_is_still_read(seed, monkeypatch):
    # The stand-down follows the judgement, not the language: "what is in <file>" in any words stays a read.
    rng = random.Random(f"read-a-file-{seed}")
    name = f"{rng.choice(_NAMES)}{rng.randint(1, 99)}.{rng.choice(_EXTS)}"
    text = rng.choice(("Kas parašyta {f}?", "Was steht in {f}?", "¿Qué hay en {f}?", "whats in {f}")).format(f=name)
    _judge(monkeypatch, rng.choice(((), ("read",))))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert (_direct_workspace_read_request(text) or {}).get("path") == name, text


def test_no_judgement_leaves_the_words_to_decide(monkeypatch):
    text = "Sukurk naują failą pirkiniai.txt ir įrašyk į jį: pienas"
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", lambda *a, **k: None)
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert plugin_ask.judged_builtins(text) == ()
    assert "workspace.write_file" not in resolve_demand_signals(text).explicit_intents


@pytest.mark.parametrize("text", [
    "Paieškok internete, kada šiandien dirba Kauno pilies muziejus",
    "paieskok internete orus rytojui Klaipedoje",
    "Schau mal online nach, was ein Ticket nach Riga kostet",
    "busca en la web cuándo abre el mercado de Triana",
])
def test_a_web_ask_in_any_language_seats_the_web_as_the_persons_own_ask(text, monkeypatch):
    _judge(monkeypatch, ("web",))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    signals = resolve_demand_signals(text)
    assert "web.search" in signals.explicit_intents and "web" in signals.required_families, signals
    assert not signals.web_by_online_reading_only
    required = requirements_for(text, task_class="unknown", source_context=OWNER)
    assert required.forbids_toolless_lane() and "web_search" in required.allowed_toolsets, (text, required)


def test_a_remote_turn_gets_none_of_these(monkeypatch):
    calls = _judge(monkeypatch, ("files",))
    text = "Sukurk failą planas.txt ir įrašyk: rytoj"
    plugin_ask.judge_turn(text, source_context={OWNER_LOCAL_KEY: False}, session_id="x")
    assert calls == [] and _direct_workspace_read_request(text) is not None
    assert "workspace" not in requirements_for(text, task_class="unknown", source_context={OWNER_LOCAL_KEY: False}).allowed_toolsets


@pytest.mark.parametrize("web", ["no", None])
def test_a_plain_question_the_judge_put_to_the_web_is_not_refused_as_a_lookup(web, monkeypatch):
    # qwen3:8b over the list picked the web for "Kas yra fotosintezė?" and most plain questions (6 of 10); with Web
    # lookups off that seat refuses the question. The web stays only when its own yes/no says the message needs it.
    rng = random.Random(f"plain-{web}")
    text = rng.choice(("Kas yra fotosintezė?", "Was ist ein schwarzes Loch?", "¿Quién escribió Don Quijote?"))
    _judge(monkeypatch, ("web",), web=web)
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="x")
    assert plugin_ask.judged_builtins(text) == ()
    signals = resolve_demand_signals(text)
    assert "web.search" not in signals.explicit_intents
    assert "web_search" not in requirements_for(text, task_class="unknown", source_context=OWNER).allowed_toolsets
