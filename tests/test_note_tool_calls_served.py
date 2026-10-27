"""Notes on the served turn: a model's note call keeps what it asked for, and "yes, save it as a note" saves the answer.

Measured on the served path at aeb58465 (real daemon, scripted loopback provider, this file's rig):

* A model that seats the operator family and calls ``operator.find_notes {"query": "venue"}`` got every note in the
  workspace, titles and paths only: the query never reached the notes search
  (``core.execution.operator_tools.build_operator_action_intent`` kept no argument for it).
* ``operator.show_note {"title": "Venue plan"}`` answered "No note titled '' in this workspace." for a note that exists.
* After an answer that offered to save its plan, "yes, save it as a note" was taken by the notes lane, which found no
  content in those words and asked "what should it say?". The plan was never saved.

Nothing else may save an answer: review of 366aeeec measured words that refuse the save or ask about it ("no thanks,
don't save it as a note", "how do I save it as a note?") and a private chat each saving the plan, and review of
654e834e measured a forget, a private stretch that had ended, and VOOL's own ask-back or receipt doing the same. The
chat window here sends every earlier message with each turn, as the /chat page does.

Every case is a real served turn over HTTP. Assertions read what the model was shown (the tool observation bound
for the provider), the reply, and the note files on disk. LABELLED: scripted provider, no live model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from tests._served_skill_rig import PROVIDER_MANIFEST_ID, REPO_ROOT, ProviderState, ServedDaemon, make_provider_server

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
PLAN_ASK = "plan a gentle three-day workout for me"
PARTY_ANSWER = "Shopping list for the party:\n- zucchinicake\n- candles\n\nWant me to save this list as a note?"


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


def _chat(rig, text: str, session: str, *, typed: bool = False, script: list | None = None,
          shown: list | None = None) -> dict:
    """One served turn. `typed` sends it the way an Auto chat does, so the chat's own lanes may take it; otherwise
    the pinned model gets it. `shown` is the chat window's earlier messages, sent ahead of this one."""
    rig.state.requests.clear()
    rig.state.script = list(script or [{"final": "Done."}])
    payload = {
        "model": PROVIDER_MANIFEST_ID,
        "messages": [*(shown or []), {"role": "user", "content": text}],
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


def _then(rig, shown: list, text: str, session: str, script: list | None = None) -> tuple[str, list]:
    """A typed turn sent the way the /chat page sends it, every earlier message with it: its reply, and the
    window's messages after it."""
    reply = _answer(_chat(rig, text, session, typed=True, script=script, shown=shown))
    return reply, [*shown, {"role": "user", "content": text}, {"role": "assistant", "content": reply}]


def _plan_first(rig, session: str) -> list:
    reply, shown = _then(rig, [], PLAN_ASK, session, script=[{"final": PLAN_ANSWER}])
    assert "Day 2: gentle stretching" in reply, reply
    return shown


def test_yes_save_it_as_a_note_saves_the_answer_it_follows(rig) -> None:
    before = set(_notes(rig))
    shown = _plan_first(rig, "save-the-plan")

    reply, _ = _then(rig, shown, "yes, save it as a note", "save-the-plan")

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


def test_an_answer_the_window_did_not_send_back_still_asks(rig) -> None:
    _plan_first(rig, "not-shown")
    before = set(_notes(rig))

    # A client that sends only the new message: the log alone cannot say the plan is the answer just given.
    reply = _answer(_chat(rig, "yes, save it as a note", "not-shown", typed=True))

    assert "I just need its content" in reply, reply
    assert set(_notes(rig)) == before


@pytest.mark.parametrize("words", [
    "no thanks, don't save it as a note",
    "do not save that as a note",
    "should I save it as a note?",
    "how do I save it as a note?",
])
def test_words_that_do_not_ask_for_the_save_save_nothing(rig, words) -> None:
    session = "not-asked-" + re.sub(r"\W+", "-", words).strip("-")
    shown = _plan_first(rig, session)
    before = set(_notes(rig))

    reply, _ = _then(rig, shown, words, session)

    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, f"{words!r} saved the answer"


@pytest.mark.parametrize("first", ["how do I save it as a note?", "yes, save it as a note"])
def test_save_it_again_after_vools_own_reply_saves_nothing(rig, first) -> None:
    session = "again-" + re.sub(r"\W+", "-", first).strip("-")
    shown = _plan_first(rig, session)
    _, shown = _then(rig, shown, first, session)
    before = set(_notes(rig))

    # The newest answer is VOOL's ask-back or its save receipt: neither offered a save, so neither is saved.
    reply, _ = _then(rig, shown, "ok, save it as a note", session)

    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "VOOL's own reply was saved as the answer"


def test_save_it_as_a_note_in_a_private_chat_saves_no_older_answer(rig) -> None:
    session = "private-save"
    shown = _plan_first(rig, session)
    switched, shown = _then(rig, shown, "private chat", session)
    assert "private" in switched.lower(), switched
    party, shown = _then(rig, shown, "what should I buy for the party?", session, script=[{"final": PARTY_ANSWER}])
    assert "candles" in party, party
    before = set(_notes(rig))

    reply, _ = _then(rig, shown, "yes, save it as a note", session)

    # The private answer is in no log to quote, and the plan before it is not what "it" means.
    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "a private chat saved an answer from before it went private"


def test_save_it_as_a_note_after_private_chat_ended_saves_no_older_answer(rig) -> None:
    session = "private-ended"
    shown = _plan_first(rig, session)
    _, shown = _then(rig, shown, "private chat", session)
    _, shown = _then(rig, shown, "what should I buy for the party?", session, script=[{"final": PARTY_ANSWER}])
    _, shown = _then(rig, shown, "end private chat", session)
    before = set(_notes(rig))

    reply, _ = _then(rig, shown, "yes, save it as a note", session)

    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "the plan from before the private stretch was saved"


def test_save_it_as_a_note_after_a_forget_saves_no_older_answer(rig) -> None:
    session = "after-forget"
    shown = _plan_first(rig, session)
    party, shown = _then(rig, shown, "what should I buy for the party?", session, script=[{"final": PARTY_ANSWER}])
    assert "zucchinicake" in party, party
    forgot, shown = _then(rig, shown, "forget zucchinicake", session)
    assert "Forget applied" in forgot, forgot
    # The window leaves the forgotten exchange out. Re-sent, the forgotten words make this save turn fail with a 500
    # at the checkpoint (CheckpointTransitionRefused), measured the same at aeb58465: a separate fault, not this one.
    shown = [message for message in shown if "zucchinicake" not in message["content"]]
    before = set(_notes(rig))

    reply, _ = _then(rig, shown, "yes, save it as a note", session)

    # The forget took the party answer out of the log, which left the plan newest there.
    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "the plan from before the forgotten answer was saved"


def test_save_it_as_a_note_after_a_council_card_saves_no_older_answer(rig) -> None:
    # The /chat page's own id shape, which the server keeps as the chat's id, so the card lands in this chat.
    session = "openclaw:" + "c0c11c0a5d0e4a7b9e21"
    shown = _plan_first(rig, session)
    # A council convened from this chat finishes and writes its card to the chat, as core.persistent_memory does; the
    # page shows the card but does not send it back with the next message.
    code = ("from core.persistent_memory import append_assistant_artifact_event as card;"
            f"assert card(session_id={session!r}, text='Council summary: AGREE 3/3. Adopt the gym plan.',"
            " artifact_kind='council_summary', artifact={'run_id': 'r1', 'state': 'converged'})")
    subprocess.run([sys.executable, "-c", code], env=dict(os.environ, VOOL_HOME=str(rig.daemon.home)), cwd=str(REPO_ROOT),
                   check=True, capture_output=True, timeout=120)
    before = set(_notes(rig))

    reply, _ = _then(rig, shown, "yes, save it as a note", session)

    assert "Note saved" not in reply, reply
    assert set(_notes(rig)) == before, "the plan from before the council card was saved"
