"""Served proof: user rules said in plain words in the chat -- add, list, remove, forget -- on one isolated daemon.

The provider records the exact request bodies, so "the model sees the rule" is read from what it was actually sent,
and every rules reply is the daemon's own answer over /api/chat. "Forget the rule about X" must also reach an
instruction the owner gave in plain words ("from now on ..."), which is kept in the standing-instructions store:
before this fix the rules lane claimed that turn, found nothing in its own store and the instruction stayed.
Authored wording only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-rules:4b"
PLAIN_TASK = "Suggest a name for a small bakery."


class _Provider(BodyAwareScriptedProvider):
    def reply(self, model: str, body: dict[str, Any]) -> Any:
        if "well-known referents" in json.dumps(body):
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        return super().reply(model, body)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(f"{daemon.base_url}/api/model-tool-certification/run", method="POST",
                      data=json.dumps({"provider_name": "ollama-local", "model_name": MODEL,
                                       "timeout_seconds": 120}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=400) as response:
        return json.loads(response.read().decode())


def _answer(payload: dict[str, Any]) -> str:
    return str(payload.get("response") or payload.get("message", {}).get("content") or payload)


def test_rules_said_in_chat_are_saved_listed_shown_to_the_model_and_removed(tmp_path):
    home = tmp_path / "home"
    folder = tmp_path / "bakery"
    folder.mkdir()
    provider = _Provider({MODEL: "Try Crumb and Kettle."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat_a, chat_b = "openclaw:" + "a" * 20, "openclaw:" + "b" * 20

            def say(text: str, session: str = chat_a) -> tuple[str, str]:
                before = len(provider.calls)
                answer = _answer(daemon.chat(text, session_id=session, model=MODEL, workspace=str(folder),
                                             timeout=600.0))
                sent = "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}"
                                 for c in provider.calls[before:])
                return answer, sent

            saved, _ = say("add to global rules: always answer in British English")
            assert "Saved rule" in saved, saved
            saved_chat, _ = say("add to chat rules: never use the word cupcake")
            assert "Saved rule" in saved_chat, saved_chat

            listing, _ = say("what are my rules?")
            assert "British English" in listing and "cupcake" in listing, listing

            # Both rules reach the model in this chat; only the global one in another chat.
            _, sent_here = say(PLAIN_TASK)
            assert "British English" in sent_here and "cupcake" in sent_here
            _, sent_other = say(PLAIN_TASK, chat_b)
            assert "British English" in sent_other and "cupcake" not in sent_other

            removed, _ = say("forget the rule about cupcake")
            assert "Removed" in removed, removed
            _, sent_after = say(PLAIN_TASK)
            assert "cupcake" not in sent_after and "British English" in sent_after

            # An instruction said in plain words is taken back by the same "forget the rule about X".
            say("From now on give me distances in kilometres.")
            _, sent_standing = say(PLAIN_TASK, chat_b)
            assert "kilometres" in sent_standing
            forgot, _ = say("forget the rule about kilometres")
            assert "Nothing was removed" not in forgot, forgot
            assert "kilometres" in forgot, forgot
            _, sent_forgot = say(PLAIN_TASK, chat_b)
            assert "kilometres" not in sent_forgot

            # Nothing matching anywhere still removes nothing, and says so.
            none, _ = say("forget the rule about parking")
            assert "Nothing was removed" in none, none


def test_a_rules_removal_that_also_forgets_something_else_is_not_owned_by_the_rules_lane():
    """The rules lane may answer a whole-turn rules removal; a turn that also asks to forget a memory still needs the
    memory forget's own verified outcome."""
    from core.user_rules import rules_command_owns_forget

    assert rules_command_owns_forget("forget the rule about cupcake")
    assert rules_command_owns_forget("remove rule 2")
    assert not rules_command_owns_forget("forget the rule about cupcake and forget my address")
    assert not rules_command_owns_forget("forget my address")
