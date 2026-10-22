"""Notes on the served turn: a model's note call keeps what it asked for, and "yes, save it as a note" saves the answer.

Measured on the served path at aeb58465 (real daemon, scripted loopback provider, this file's rig):

* A model that seats the operator family and calls ``operator.find_notes {"query": "venue"}`` got every note in the
  workspace, titles and paths only: the query never reached the notes search
  (``core.execution.operator_tools.build_operator_action_intent`` kept no argument for it).
* ``operator.show_note {"title": "Venue plan"}`` answered "No note titled '' in this workspace." for a note that exists.
* After an answer that offered to save its plan, "yes, save it as a note" was taken by the notes lane, which found no
  content in those words and asked "what should it say?". The plan was never saved.

Words that refuse the save or ask about it ("no thanks, don't save it as a note", "how do I save it as a note?"), and a
private chat, whose newest answer is never logged, must not save an answer: review of 366aeeec measured each one
saving the plan.

Every case is a real served turn over HTTP. Assertions read what the model was shown (the tool observation bound
for the provider), the reply, and the note files on disk. LABELLED: scripted provider, no live model.
"""
from __future__ import annotations

import json
import re

import pytest

from tests._served_skill_rig import PROVIDER_MANIFEST_ID, ProviderState, ServedDaemon, make_provider_server

#: What the operator family's expansion says once it seated the family for the turn.
SEATED = "Seated the `operator` family"
VENUE_BODY = "call the venue on Tuesday about the deposit"
GROCERIES_BODY = "eggs and milk"
PLAN_ANSWER = (
    "Gentle three-day plan:\n"
    "Day 1: a 20 minute walk.\n"
    "Day 2: gentle stretching for 15 minutes.\n"
    "Day 3: a 25 minute walk.\n"
    "\n"
    "Want me to save this plan as a note?"
)
PARTY_ANSWER = "Shopping list for the party:\n- cake\n- candles\n\nWant me to save this list as a note?"


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """One provider and one certified daemon, its notes in a scratch workspace holding two notes."""
    tmp = tmp_path_factory.mktemp("note-tool-calls")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir()
    workspace = tmp / "workspace"
    workspace.mkdir()
    daemon = ServedDaemon(home, port, extra_env={"VOOL_WORKSPACE_ROOT": str(workspace)}).start()
    try:
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        result = cert.get("result") or cert
        assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"
        rig = type("Rig", (), {"state": state, "daemon": daemon, "workspace": workspace})
        for title, body in (("Venue plan", VENUE_BODY), ("Groceries", GROCERIES_BODY)):
            reply = _chat(rig, f'save a note titled "{title}" with: {body}', "note-seed", typed=True)
            assert f"Note saved: '{title}'" in _answer(reply), _answer(reply)
        assert len(_notes(rig)) == 2
        yield rig
    finally:
        daemon.stop()
        server.shutdown()


def _chat(rig, text: str, session: str, *, typed: bool = False, script: list | None = None) -> dict:
    """One served turn. `typed` sends it the way an Auto chat does, so the chat's own lanes may take it; otherwise
    the pinned model gets it."""
    rig.state.requests.clear()
    rig.state.script = list(script or [{"final": "Done."}])
    payload = {
        "model": PROVIDER_MANIFEST_ID,
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "mode": "auto" if typed else "manual",
        "session_id": session,
    }
    if typed:
        payload["model_selection"] = "sticky"
    return rig.daemon.post("/v1/chat/completions", payload)


def _answer(reply: dict) -> str:
    return str(((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _notes(rig) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted((rig.workspace / "notes").glob("*.md"))}


def _model_calls(rig, intent: str, arguments: dict, session: str) -> dict:
    """A notes turn on the pinned model: it seats the operator family, calls `intent`, then answers.

    Returns the observation of that call exactly as the runtime bound it for the provider."""
    marker = f'"intent": "{intent}"'
    _chat(rig, "summarize my notes from this week", session, script=[
        {"tool": "capability.expand_family", "arguments": {"family": "operator"}},
        {"tool": intent, "arguments": arguments, "prompt_contains": SEATED},
        {"final": "Done.", "prompt_contains": marker},
    ])
    for body in reversed(rig.state.requests):
        for message in body.get("messages") or []:
            for line in str(message.get("content") or "").splitlines():
                if marker in line and line.lstrip().startswith("- {"):
                    return json.loads(line.lstrip()[2:])
    raise AssertionError(f"the model's {intent} call never came back as an observation")


def test_a_model_notes_search_looks_for_its_query(rig) -> None:
    observation = _model_calls(rig, "operator.find_notes", {"query": "venue"}, "find-notes")
    shown = observation["response_preview"]
    assert observation["ok"], observation
    assert shown.startswith("Notes matching 'venue' (1):"), shown
    assert VENUE_BODY in shown
    assert "Groceries" not in shown, "the search listed every note instead of the ones about its query"


def test_a_model_note_read_opens_the_note_it_names(rig) -> None:
    observation = _model_calls(rig, "operator.show_note", {"title": "Venue plan"}, "show-note")
    shown = observation["response_preview"]
    assert observation["ok"], observation
    assert shown.startswith("Note: Venue plan"), shown
    assert VENUE_BODY in shown


def test_yes_save_it_as_a_note_saves_the_answer_it_follows(rig) -> None:
    before = set(_notes(rig))
    first = _chat(rig, "plan a gentle three-day workout for me", "save-the-plan", typed=True,
                  script=[{"final": PLAN_ANSWER}])
    assert "Day 2: gentle stretching" in _answer(first), _answer(first)

    reply = _answer(_chat(rig, "yes, save it as a note", "save-the-plan", typed=True))

    assert "I just need its content" not in reply, reply
    assert "Note saved: 'Gentle three-day plan'" in reply, reply
    added = {name: text for name, text in _notes(rig).items() if name not in before}
    assert len(added) == 1, added
    saved = next(iter(added.values()))
    assert "Day 2: gentle stretching for 15 minutes." in saved
    assert "Day 3: a 25 minute walk." in saved
    assert "Want me to save" not in saved, "the closing offer was saved as part of the plan"


def test_save_it_as_a_note_with_no_answer_before_it_still_asks(rig) -> None:
    before = set(_notes(rig))

    reply = _answer(_chat(rig, "yes, save it as a note", "nothing-said-yet", typed=True))

    assert "I just need its content" in reply, reply
    assert set(_notes(rig)) == before, "a note was saved from an answer this chat never gave"


@pytest.mark.parametrize("words", [
    "no thanks, don't save it as a note",
    "do not save that as a note",
    "should I save it as a note?",
    "how do I save it as a note?",
])
def test_words_that_do_not_ask_for_the_save_save_nothing(rig, words) -> None:
    session = "not-asked-" + re.sub(r"\W+", "-", words).strip("-")
    _chat(rig, "plan a gentle three-day workout for me", session, typed=True, script=[{"final": PLAN_ANSWER}])
    before = set(_notes(rig))

    reply = _answer(_chat(rig, words, session, typed=True))

    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, f"{words!r} saved the answer"


def test_save_it_as_a_note_in_a_private_chat_saves_no_older_answer(rig) -> None:
    session = "private-save"
    _chat(rig, "plan a gentle three-day workout for me", session, typed=True, script=[{"final": PLAN_ANSWER}])
    assert "private" in _answer(_chat(rig, "private chat", session, typed=True)).lower()
    party = _chat(rig, "what should I buy for the party?", session, typed=True, script=[{"final": PARTY_ANSWER}])
    assert "candles" in _answer(party), _answer(party)
    before = set(_notes(rig))

    reply = _answer(_chat(rig, "yes, save it as a note", session, typed=True))

    # The private answer is in no log to quote, and the plan before it is not what "it" means.
    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "a private chat saved an answer from before it went private"
