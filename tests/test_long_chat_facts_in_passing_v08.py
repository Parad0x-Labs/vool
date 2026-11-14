"""Facts said in passing over a long chat, replaced and then restored, without any "remember" ask.

The owner's requirement (14 Nov 2026): "message #5 my new padlock is 5555, #30 I lost my padlock, #55 new code is
9999, #100 what's my padlock number? -> 9999; #155 I found my first padlock so we use it again -> 5555". Memory and
context are one lane. Measured on the line head 0a84ca8f in a served long chat (scripted model, real routing):
  - every change said on one day was marked "[conflicting records] -- give both" (turns of one day had no order);
  - "I'm back in my old locker" left the newer number marked current;
  - a new chat asking "what room is my desk in at work?" was quoted the FIRST room, not the one it moved to;
  - "remind me, what's my locker number now?" was split by the planner and "remind me" refused as a reminder.
All names, places and values are invented.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from core.updated_fact_marking import (
    CONFLICT_MARKER,
    CURRENT_MARKER,
    SUPERSEDED_MARKER,
    compute_markers,
    mark_updated_fact_lines,
)


def _said(text: str, seq: int | None, day: str = "2026-11-14") -> dict[str, Any]:
    return {"line": f"- user said (recorded {day}): {text}", "role": "user", "delivered": True,
            "statement_at": None, "source_sequence": seq}


LOCKER_1 = "the gym gave me locker 214 this season, right by the door"
LOCKER_OUT = "ugh they kicked me out of my locker today, some maintenance on that row"
LOCKER_2 = "ok sorted, they put me in locker 87 for now, down by the showers"
LOCKER_BACK = "good news, maintenance is done and I'm back in my old locker"


def _marks(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {line.split("): ", 1)[1]: mark for line, mark in compute_markers(rows, []).items()}


def test_one_day_changes_take_the_order_they_were_said_in():
    marks = _marks([_said(LOCKER_1, 3), _said(LOCKER_OUT, 13), _said(LOCKER_2, 21)])
    assert marks[LOCKER_2] == CURRENT_MARKER
    assert marks[LOCKER_1] == SUPERSEDED_MARKER


def test_a_restore_makes_the_earlier_value_current_again():
    marks = _marks([_said(LOCKER_1, 3), _said(LOCKER_OUT, 13), _said(LOCKER_2, 21), _said(LOCKER_BACK, 61)])
    assert marks[LOCKER_1] == CURRENT_MARKER
    assert marks[LOCKER_2] == SUPERSEDED_MARKER


def test_a_restore_said_before_the_change_restores_nothing():
    marks = _marks([_said(LOCKER_1, 3), _said(LOCKER_BACK, 10), _said(LOCKER_2, 21)])
    assert marks[LOCKER_2] == CURRENT_MARKER
    assert marks[LOCKER_1] == SUPERSEDED_MARKER


def test_a_plan_changed_twice_in_one_sitting_ends_on_the_last_word():
    van = "we're driving up to Corrin's wedding in the van on the 12th"
    train = "van's in the garage till next month, so it's the train to the wedding instead"
    again = "garage called, the van is ready early, so we're driving after all"
    marks = _marks([_said(van, 5), _said(train, 80), _said(again, 220)])
    assert marks[again] == CURRENT_MARKER
    assert marks[train] == SUPERSEDED_MARKER  # never current again through an older pair
    assert marks[van] == SUPERSEDED_MARKER


def test_one_shared_word_never_chains_across_days():
    van = "we're driving up to Corrin's wedding in the van on the 12th"
    train = "it's the train to the wedding instead"
    marks = _marks([_said(van, 5, "2026-11-01"), _said(train, 80, "2026-11-14")])
    assert marks == {}


def test_same_moment_without_an_order_still_says_both():
    one = _said("The guest list stands at 30 people.", 5)
    two = _said("Make that 26 people for the dinner.", 5)
    assert set(compute_markers([one, two], []).values()) == {CONFLICT_MARKER}


def test_distilled_lines_with_only_a_day_take_their_conversation_log_order():
    old = "- user said: my desk at work moved to the third floor, room 3B (recorded: 2026-11-14)"
    new = "- user said: they want my desk back on the second floor, room 2F from monday (recorded: 2026-11-14)"
    block = "<retrieved_context>\nDistilled local facts.\n" + new + "\n" + old + "\n</retrieved_context>"
    order = {"my desk at work moved to the third floor, room 3b": 4,
             "they want my desk back on the second floor, room 2f from monday": 40}
    marked = mark_updated_fact_lines(block, said_order=lambda bodies: {b: order[b] for b in bodies if b in order})
    assert new + " " + CURRENT_MARKER in marked
    assert old + " " + SUPERSEDED_MARKER in marked
    unordered = mark_updated_fact_lines(block)  # no log order: as before, both
    assert unordered.count(CONFLICT_MARKER) == 2


@pytest.mark.parametrize(
    "text",
    ["remind me, what's my locker number now?", "remind me what's my locker number now?",
     "can you remind me: which room is my desk in?"],
)
def test_remind_me_with_a_question_is_a_recall_not_a_reminder(text):
    from core.agent_runtime.turn_planner import recall_question_needs_no_planner
    from core.operator.parser import parse_operator_action_intent

    assert parse_operator_action_intent(text) is None
    assert recall_question_needs_no_planner(text)  # one request: the planner is not asked to split it


def test_remind_me_comma_without_a_question_mark_is_not_a_reminder_either():
    from core.operator.parser import parse_operator_action_intent

    assert parse_operator_action_intent("remind me, what's the shed code") is None


@pytest.mark.parametrize("text", ["remind me to call mum at 5", "remind me, call the dentist tomorrow at 9",
                                  "remind me tomorrow what my locker is"])
def test_a_reminder_stays_a_reminder(text):
    from core.operator.parser import parse_operator_action_intent

    assert getattr(parse_operator_action_intent(text), "kind", None) == "schedule_reminder"


# --- served: one long-ish chat said in passing, then a new chat -------------------------------------------------

MODEL = "stub-private:4b"
SHOWN: list[dict[str, Any]] = []


def _stream(base_url: str, text: str, session: str) -> str:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session}
    request = Request(f"{base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    return str(commit.get("canonical_content") or answer)


FILLER = ["lol ok", "what's a good name for a grey cat?", "nice, thanks", "any quick idea for a rainy sunday?",
          "haha true", "how do I get coffee stains off a mug?", "ok cool", "what goes well with roasted carrots?"]


def _chat() -> list[str]:
    turns = [FILLER[0], LOCKER_1, FILLER[1], FILLER[2], "my desk at work moved to the third floor, room 3B"]
    turns += [FILLER[3], LOCKER_OUT, FILLER[4], LOCKER_2, FILLER[5]]
    turns += ["they want my desk back on the second floor, room 2F from monday"]
    turns += FILLER * 2
    turns += ["which locker am I using at the gym these days?"]
    turns += [*FILLER[:4], LOCKER_BACK, *FILLER[4:7]]
    turns += ["what's my locker number now?"]
    return turns


def test_served_a_long_chat_keeps_the_current_value_and_a_new_chat_quotes_it(tmp_path: Path):
    from tests._blackbox_served_rig import ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import SEED
    from tests.test_private_chat_served_workflow import _certify, _Provider

    class _Recording(_Provider):
        def reply(self, model: str, body: dict[str, Any]) -> Any:
            messages = body.get("messages") or []
            system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
            last = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
            if "You are VOOL" in system[:200] and last.startswith(("which locker", "what's my locker")):
                SHOWN.append({"q": last, "text": "\n".join(str(m.get("content") or "") for m in messages)})
                return "noted."
            return super().reply(model, body)

    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    owner = tmp_path / "owner"
    for sub in ("Desktop", "Documents", "Downloads"):
        (owner / sub).mkdir(parents=True)
    with _Recording({MODEL: "noted."}) as provider:
        run_in_home(home, SEED.format(root=repo, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0", "HOME": str(owner.resolve())}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            for text in _chat():
                _stream(daemon.base_url, text, "long-chat-in-passing")
            desk = _stream(daemon.base_url, "what room is my desk in at work?", "long-chat-new")
    marked = {s["q"]: [ln for ln in s["text"].split("\n") if " said" in ln[:40]] for s in SHOWN}
    first = marked["which locker am I using at the gym these days?"]
    assert any("locker 87" in ln and ln.endswith(CURRENT_MARKER) for ln in first), first
    assert not any(ln.endswith(CONFLICT_MARKER) for ln in first), first
    last = marked["what's my locker number now?"]
    assert any("locker 214" in ln and ln.endswith(CURRENT_MARKER) for ln in last), last
    assert not any("locker 87" in ln and ln.endswith(CURRENT_MARKER) for ln in last), last
    assert "2F" in desk and "3B" not in desk, desk
