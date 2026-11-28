"""A question about the owner's own life is looked for in the documents they keep before VOOL says it has nothing.

Benchmark on d8da327bc (local qwen3:8b and GLM flash, Web lookups off): about ten of 45 home-file facts were answered
"I don't have that saved" or "Web lookups are off" while the fact sat in a .txt, .md, .csv or one-page PDF under
Documents/<topic>, the Desktop or a folder of its own. Two doors were shut on the files:

- the turn's judge read the ask as a recall of the owner's own facts, and the route admitted the files loop only on
  words the ask shares with a document, so an ask worded otherwise than the file never reached it;
- the judge read it as a web lookup (or its words did), and with the web unable to run the turn went to the lookup's
  refusal, never to the files.

What decides it is what the turn can do and what the judges read, never shared words: the recall judge's reading
and whether a saved fact answers it, whether the web can run, and the world-or-own-life judge. A turn the judges send
is shown where the owner's documents are kept, so the model opens the one it is about, and what it read is weighed by
whether the reply answers, not by words. Finding nothing, the turn still falls through to memory's "not saved" or the
lookup's refusal. The facts and wordings here are invented; none is a benchmark row, and none of the asks below
shares a word with the file that answers it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import intent_arbiter, plugin_ask
from core.clean_loop import hook, route, tools
from core.clean_loop.registry import ToolContext
from tests.test_invoice_folder_follow_ups_served import _pdf

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}

FILES = {
    "Documents/Garden/greenhouse.txt": "Brightlea kit, glazing 4 mm polycarbonate\nBase 2.4 x 1.8 m\n",
    "Documents/Pets/kennel-booking.md": "# Kennel\nOakridge Kennels, drop-off Friday 11 June at 09:15\nDaily rate 23 EUR\n",
    "Documents/Bills/water-2026.csv": "quarter,amount\nQ1,61.40\nQ2,58.90\n",
    "Rowing/regatta.txt": "Autumn regatta at Lake Varn, crews meet 07:45 at boathouse 3\nKit: navy top, white cap\n",
}
PDF = ("Desktop/scan1187.pdf", ["MERIDIAN EYE CENTRE", "Prescription right eye: -1.75", "Left eye: -2.00"])

# Judged a recall of the owner's own facts; no word of any of them is in the file that answers it.
RECALLS = [
    "what do we pay a night when the dog stays away",          # Pets/kennel-booking.md: "Daily rate 23 EUR"
    "how strong are my glasses lenses",                        # Desktop/scan1187.pdf: "Prescription right eye"
    "how wide is that glass shed out back",                    # Garden/greenhouse.txt: "Base 2.4 x 1.8 m"
]
# Judged a web lookup, with the web unable to run; again no shared word.
LOOKUPS = [
    "when should I arrive for the boat race",                   # Rowing/regatta.txt
    "when does our pup go away for the summer",                 # Pets/kennel-booking.md
]
WORLD = [
    "who won the rowing gold at the last olympics",
    "which country makes the most polycarbonate",
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "house"
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    pdf = root / PDF[0]
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(_pdf(PDF[1], 10))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr("core.runtime_execution_tools._readable_machine_roots",
                        lambda: tuple(sorted(p for p in root.iterdir() if p.is_dir())))
    return root


@pytest.fixture
def no_world_judge(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the world-or-own-life judge was asked")

    monkeypatch.setattr(intent_arbiter, "arbitrate_world_follow_up", refuse)


def _web(monkeypatch, on: bool) -> None:
    monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda *_a, **_k: on)
    monkeypatch.setattr("core.policy_engine.local_only_mode", lambda *_a, **_k: False)


def _judged(monkeypatch, text: str, *picks: str) -> None:
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda asked: picks if asked == text else ())


def _world(monkeypatch, verdict: str | None) -> None:
    monkeypatch.setattr(intent_arbiter, "arbitrate_world_follow_up", lambda *_a, **_k: verdict)


def _decide(text: str, home: Path) -> route.RouteDecision:
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    return route.decide(
        text, source_context=OWNER, session_id="s-docs", resolve_brain=lambda: object(), env=ON,
        file_words=lambda: set(tools.file_words(home)),
        document_match=lambda ask: tools.document_matches(ask, ctx),
        documents_kept=lambda: bool(tools.kept_documents(ctx)),
    )


@pytest.mark.parametrize("ask", RECALLS + LOOKUPS)
def test_no_word_of_these_asks_admits_them(home, monkeypatch, ask, no_world_judge):
    _web(monkeypatch, True)
    _judged(monkeypatch, ask)
    assert _decide(ask, home).reason == "not_about_own_files"


@pytest.mark.parametrize("web_on", [False, True])
@pytest.mark.parametrize("ask", RECALLS)
def test_a_recall_nothing_saved_answers_searches_the_documents(home, monkeypatch, ask, web_on, no_world_judge):
    _web(monkeypatch, web_on)
    _judged(monkeypatch, ask, "memory_recall")
    decision = _decide(ask, home)
    assert decision.admit and decision.by_documents and decision.by_judgement, decision


@pytest.mark.parametrize("ask", LOOKUPS)
def test_a_lookup_about_the_owners_life_that_cannot_run_searches_the_documents(home, monkeypatch, ask):
    _web(monkeypatch, False)
    _judged(monkeypatch, ask, "web")
    _world(monkeypatch, "no")
    decision = _decide(ask, home)
    assert decision.admit and decision.by_documents and decision.by_judgement, decision


@pytest.mark.parametrize("ask", LOOKUPS)
def test_a_lookup_the_web_can_answer_keeps_the_web_lane(home, monkeypatch, ask, no_world_judge):
    _web(monkeypatch, True)
    _judged(monkeypatch, ask, "web")
    assert _decide(ask, home).reason == "judged_tool_turn"


@pytest.mark.parametrize("verdict", ["yes", None])
@pytest.mark.parametrize("ask", WORLD)
def test_a_lookup_about_the_world_keeps_its_refusal(home, monkeypatch, ask, verdict):
    _web(monkeypatch, False)
    _judged(monkeypatch, ask, "web")
    _world(monkeypatch, verdict)
    assert _decide(ask, home).reason == "judged_tool_turn"


def test_a_recall_a_saved_fact_answers_is_memory_s(home, monkeypatch, no_world_judge):
    _web(monkeypatch, False)
    ask = RECALLS[0]
    _judged(monkeypatch, ask, "memory_recall")
    monkeypatch.setattr(route, "_saved_memory_answers", lambda *_a, **_k: True)
    assert not _decide(ask, home).admit


def test_a_recall_with_no_documents_kept_is_memory_s(tmp_path, monkeypatch, no_world_judge):
    _web(monkeypatch, False)
    empty = tmp_path / "empty"
    (empty / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setattr("core.runtime_execution_tools._readable_machine_roots", lambda: (empty / "Documents",))
    ask = RECALLS[1]
    _judged(monkeypatch, ask, "memory_recall")
    assert _decide(ask, empty).reason == "not_about_own_files"


def test_the_judged_turn_is_shown_where_the_documents_are_kept(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    assert tools.document_map_pre_step(RECALLS[0], ctx) is None  # only a turn the judges sent
    ctx.source_context[tools.DOCUMENT_MAP_FLAG] = True
    shown = tools.document_map_pre_step(RECALLS[0], ctx)
    for relative in [*FILES, PDF[0]]:
        assert f"- {relative}" in shown, shown
    assert "Daily rate" not in shown and "-1.75" not in shown  # names only: the model reads before it answers


def test_what_a_judged_turn_read_is_weighed_by_the_reply_not_by_shared_words():
    read = SimpleNamespace(steps=[{"tool": "read_file", "ok": True, "result_chars": 60, "status": "ok"}],
                           seen=["Oakridge Kennels, drop-off Friday 11 June at 09:15\nDaily rate 23 EUR"],
                           answer="It's 23 EUR a day at Oakridge Kennels.", staged="")
    assert hook.loop_should_decline(RECALLS[0], read) == "irrelevant_matches_fell_through"
    assert hook.loop_should_decline(RECALLS[0], read, words_needed=False, judged=lambda *_a: True) == ""
    assert hook.loop_should_decline(RECALLS[0], read, words_needed=False,
                                    judged=lambda *_a: False) == "found_nothing_fell_through"


def test_only_a_live_reading_meets_the_web_switch_when_the_web_cannot_run():
    assert hook.front_door_needs_live_reading("what's the weather at Lake Varn tomorrow", {})
    assert not hook.front_door_needs_live_reading("how much was the water bill in the second quarter", {})


@pytest.mark.parametrize("connected", [False, True])
def test_a_mail_pick_with_no_mail_account_searches_the_documents(home, monkeypatch, connected):
    _web(monkeypatch, False)
    ask = "what came back to us from the revenue office"  # Bills/..., judged a mail search
    _judged(monkeypatch, ask, "mail")
    _world(monkeypatch, "no")
    monkeypatch.setattr("core.runtime_tool_contracts._email_lane_available", lambda kind: connected)
    decision = _decide(ask, home)
    if connected:
        assert decision.reason == "judged_tool_turn"  # the mail lane can search the inbox, and owns the turn
    else:
        assert decision.admit and decision.by_judgement, decision


def test_an_empty_word_search_shows_where_the_documents_are_kept(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER), ask=RECALLS[0])
    tools.warm_index(ctx)
    found = tools.find({"query": "overnight boarding fee"}, ctx)
    assert found.empty and "Documents/Pets/kennel-booking.md" in found.text, found.text
    assert "Daily rate" not in found.text  # names only
