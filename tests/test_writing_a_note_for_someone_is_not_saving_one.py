"""A request to WRITE a note for someone is writing for the model; only a request to keep a note is the notes tool.

UX finding J-03 (blue-team journey, measured 2026-10-19 on 153148eb): on a fresh install
"write me a short note to my neighbour about the fence" was parsed as `save_note`, answered with
the notes tool's developer form ('Use a form like: save a note titled "Agenda" with: ...'), and the
working status read "Running the build…" / "Reading the brief…" for what was a plain chat.

Pinned here:
* the parser's note recognizer: notes written for someone (to/for a person, "write me a note",
  thank-you/farewell kinds, "a note thanking ...") are not saves; save/store, write down, jot,
  take a note, make a note, titled/with:/note: forms are;
* composed-request co-presence reads the same recognizer (a written note is never a save part);
* the notes tool reads the plain ways people give a note's words, and its ask-back is plain words
  whose own example really saves; the calendar proposal ask-back the same;
* the companion status words for an ordinary turn claim no build, brief or lane, and the build
  phrase appears only while a build tool runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.operator.notes import handle_save_note, parse_note_request
from core.operator.parser import _SAVE_NOTE_RE, parse_operator_action_intent

WRITING_FOR_SOMEONE = [
    "write me a short note to my neighbour about the fence",
    "Write a thank-you note to my aunt for the birthday gift",
    "can you write a note to my landlord asking to fix the heating",
    "write a quick note for my son's teacher saying he was sick on Monday",
    "please write a short note congratulating my colleague on her promotion",
    "write a sympathy note for a friend who lost her dog",
    "Write a note about my Desktop layout.",
    "write a short note apologising to my sister for missing dinner",
    "create a note to my neighbour asking them to trim the hedge",
    "could you write my mum a little note for mother's day",
    "write a farewell note to a coworker who is leaving",
    "write a note thanking the nurse",
    "help me write a note to the school about the trip",
    "Write a one-sentence description of my app Lumen, a note-taking app.",
    "write a love note for my wife to find in her lunchbox",
    "take note of how long the bread takes and tell me a recipe",
    "Make a 6-word title for a QA note about local routing",
]

# (request, the body the notes tool must read from it)
KEEPING_A_NOTE = [
    ('save a note titled "Agenda" with: packaging and release checks', "packaging and release checks"),
    ("save a note saying buy milk on the way home", "buy milk on the way home"),
    ("save a note that says call the plumber on Monday", "call the plumber on Monday"),
    ("take a note: the boiler service is due in March", "the boiler service is due in March"),
    ("jot down a note: pick up the parcel at the post office", "pick up the parcel at the post office"),
    ("write down a note that the spare key is under the mat", "the spare key is under the mat"),
    ('create a note titled "Groceries" with: eggs, bread, apples', "eggs, bread, apples"),
    ('write a note titled "Fence" with: ask Tom about the posts', "ask Tom about the posts"),
    ("note this down: the wifi password is on the router", "the wifi password is on the router"),
    ("save this as a note: dentist on the 4th at 10", "dentist on the 4th at 10"),
    ("write a note to my notes: renew the passport", "renew the passport"),
    ("make a note: water the plants on Friday", "water the plants on Friday"),
    ("write a note to self: call the bank", "call the bank"),
]


@pytest.mark.parametrize("text", WRITING_FOR_SOMEONE)
def test_a_note_written_for_someone_is_not_a_notes_tool_request(text: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "save_note", (text, intent)
    assert _SAVE_NOTE_RE.search(text) is None, text


@pytest.mark.parametrize("text,body", KEEPING_A_NOTE)
def test_a_request_to_keep_a_note_reaches_the_notes_tool_with_its_words(text: str, body: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "save_note", (text, intent)
    assert parse_note_request(text)["body"] == body


@pytest.mark.parametrize("text", ["save a note", "create a new note", 'save a note titled "Fence"'])
def test_a_save_without_words_still_reaches_the_notes_tool(text: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "save_note"
    assert parse_note_request(text)["body"] == ""


def test_composed_requests_do_not_count_a_written_note_as_a_save_part() -> None:
    assert _SAVE_NOTE_RE.search("write my neighbour a note about the fence and remind me tomorrow at 9 to post it") is None
    assert _SAVE_NOTE_RE.search("save a note saying fence posts are in the shed and remind me tomorrow at 9") is not None


class _Gate:
    mode = "execute"
    reason = ""


def _save(text: str):
    from core.operator.models import OperatorActionIntent

    audits: list = []
    result = handle_save_note(
        OperatorActionIntent(kind="save_note", raw_text=text),
        task_id="t", session_id="s",
        evaluate_local_action_fn=lambda *a, **k: _Gate(),
        audit_log_fn=lambda *a, **k: audits.append((a, k)),
    )
    return result, audits


def test_the_notes_ask_back_is_plain_words_and_its_example_saves(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    asked, audits = _save("save a note")
    assert asked.ok is False and asked.status == "invalid_request"
    text = asked.response_text
    assert "Use a form like" not in text and "with:" not in text and "titled" not in text, text
    assert text.startswith("Happy to save a note, I just need its content. What should it say?"), text
    assert audits == []
    example = text.split("like: ", 1)[1].rstrip(".")
    assert parse_operator_action_intent(example).kind == "save_note"
    assert parse_note_request(example)["body"] == "call the plumber on Monday"


def test_the_calendar_ask_back_is_plain_words_and_its_example_reads(monkeypatch) -> None:
    from core.operator.calendar_provider import parse_event_proposal, propose_event
    from core.operator.models import OperatorActionIntent
    from core.operator.when import parse_when_expression

    asked = propose_event(
        OperatorActionIntent(kind="propose_calendar_event", raw_text="propose a meeting"),
        task_id="t", session_id="s", config=None, adapter=None,
        parse_when_fn=lambda _t: None, now_fn=None, create_pending_action_fn=None,
        evaluate_local_action_fn=None, audit_log_fn=None,
    )
    assert asked.ok is False and asked.status == "invalid_request"
    assert "Use a form like" not in asked.response_text, asked.response_text
    example = asked.response_text.split("like: ", 1)[1].rstrip(".")
    assert parse_operator_action_intent(example).kind == "propose_calendar_event"
    assert parse_event_proposal(example)["title"] == "Project review"
    when = parse_when_expression(example, now_fn=lambda: datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc))
    assert when is not None and when.ok, getattr(when, "problem", when)


# ------------------------------------------------------------------ status words

DEVELOPER_WORDS = ("build", "brief", "lane", "provenance", "runtime", "execution", "process tree", "tool output",
                   "authority", "artifact", "worktree", "sha", "served", "context")

DRIVER = r"""
const out = {plain: [], unknownTool: [], buildTool: []};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  for (let n = 0; n < 40; n++) {
    const a = "chat" + n, s = n * 7 + 1;
    VC.consume(a, { type: "task.started", seq: s });
    out.plain.push(VC.resolve(VC.view(a)).phraseText);
    VC.consume(a, { type: "x", raw_type: "model_routing_started", seq: s + 1 });
    out.plain.push(VC.resolve(VC.view(a)).phraseText);
    VC.consume(a, { type: "model.selected", seq: s + 2 });
    out.plain.push(VC.resolve(VC.view(a)).phraseText);
    VC.consume(a, { type: "model.call_started", seq: s + 3 });
    out.plain.push(VC.resolve(VC.view(a)).phraseText);
    VC.consume(a, { type: "task.finalizing", seq: s + 4 });
    out.plain.push(VC.resolve(VC.view(a)).phraseText);
    const b = "tool" + n;
    VC.consume(b, { type: "task.started", seq: s });
    VC.consume(b, { type: "tool.started", seq: s + 1, tool: "save_note" });
    out.unknownTool.push(VC.resolve(VC.view(b)).phraseText);
    const c = "build" + n;
    VC.consume(c, { type: "task.started", seq: s });
    VC.consume(c, { type: "tool.started", seq: s + 1, tool: "project_build" });
    out.buildTool.push(VC.resolve(VC.view(c)).phraseText);
  }
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""


def test_an_ordinary_turn_never_narrates_a_build_or_developer_work() -> None:
    from tests.test_companion_presentation_p2 import run_presentation

    out = run_presentation(DRIVER)
    assert "error" not in out, out
    plain = set(out["plain"]) | set(out["unknownTool"])
    assert plain and all(p.endswith("…") for p in plain), plain
    leaked = sorted(p for p in plain if any(w in p.lower() for w in DEVELOPER_WORDS))
    assert leaked == [], leaked
    assert set(out["buildTool"]) == {"Running the build…"}, set(out["buildTool"])
