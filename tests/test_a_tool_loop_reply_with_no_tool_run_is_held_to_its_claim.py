"""A done claim the tool loop's model wrote with no tool run is checked, in any language.

iMac gate on 7ce02be05 (qwen3:8b): "nebereikia priminimo apie augalų laistymą, ištrink jį" (there was no such reminder)
was answered "Priminimas apie augalų laistymą buvo ištrintas." with no tool run, and the turn was signed "no action
claimed". The tool loop closed with its own words (respond.direct); the action result it rode in was stamped as written
by no model, and the done-claim check passes over replies VOOL composes itself, so the judge was never asked. Reproduced
on the app from source with a log on the check; the judge itself reads all of these sentences as "done".

Here the judge is scripted; the asks, replies and languages are drawn at random, and the result is built by the same
builder the served turn uses.
"""
from __future__ import annotations

import random

import pytest

import core.intent_arbiter as intent_arbiter
from apps.vool_agent import VoolAgent
from core import plugin_ask
from core.agent_runtime import action_honesty_validator as validator

CASES = (
    ("nebereikia priminimo apie {thing}, ištrink jį", "Priminimas apie {thing} buvo ištrintas.", "To nepadariau"),
    ("gal gali panaikinti priminimą apie {thing}?", "Gerai, priminimą apie {thing} panaikinau.", "To nepadariau"),
    ("lösch bitte die Erinnerung wegen {thing}", "Die Erinnerung wegen {thing} ist gelöscht.", "Das habe ich nicht"),
    ("borra el recordatorio de {thing} porfa", "Listo, borré el recordatorio de {thing}.", ""),
    ("drop the reminder about {thing}, don't need it", "Done, the {thing} reminder is gone.", "I have not done that"),
)
THINGS = ("augalų laistymą", "dem Ölwechsel", "llamar al fontanero", "the dentist", "batų taisyklą", "the gym bag")


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def _served_result(ask: str, reply: str, *, tool_name: str) -> dict:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    return agent._action_fast_path_result(
        task_id="task-" + str(random.randint(1, 10_000)), session_id="openclaw:claim", user_input=ask, response=reply,
        confidence=0.6, source_context={"surface": "openclaw"}, reason="model_tool_intent_direct_response",
        success=True, details={"tool_name": tool_name, "tool_steps": []}, mode_override="advice_only",
        task_outcome="success",
    )


def _script(monkeypatch, answer):
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_done_claim", lambda reply, **_k: (asked.append(reply), answer)[1])
    return asked


@pytest.mark.parametrize("seed", range(10))
def test_a_done_claim_the_tool_loop_wrote_with_no_tool_run_does_not_stand(seed, monkeypatch) -> None:
    rng = random.Random(f"loop-claim-{seed}")
    ask_shape, reply_shape, correction = rng.choice(CASES)
    thing = rng.choice(THINGS)
    ask, reply = ask_shape.format(thing=thing), reply_shape.format(thing=thing)
    plugin_ask._record_builtins(ask, (rng.choice(["contacts", "reminders", "things"]),))
    asked = _script(monkeypatch, "done")
    result = _served_result(ask, reply, tool_name="respond.direct")

    assert result["model_execution"]["used_model"] is True
    out = validator.enforce_final_action_honesty(result, user_input=ask, session_id="openclaw:claim",
                                                 source_context={"session_id": "openclaw:claim"})

    assert asked == [reply]
    assert reply not in out["response"], out["response"]
    assert (out.get("action_honesty_validator") or {}).get("applied"), out
    # The line says nothing was changed; which language it is in follows the turn's language evidence (a short ask
    # may carry none, and then it is the app language: a v0.8.1 item, not this fix).
    assert out["response"].startswith((correction or "I have not done that", "I have not done that")), out["response"]


@pytest.mark.parametrize("seed", range(4))
def test_a_plain_answer_the_tool_loop_wrote_stands(seed, monkeypatch) -> None:
    rng = random.Random(f"loop-plain-{seed}")
    ask = rng.choice(CASES)[0].format(thing=rng.choice(THINGS))
    reply = rng.choice(["Neradau tokio priminimo.", "Ich finde keine solche Erinnerung.", "I can't find that reminder."])
    plugin_ask._record_builtins(ask, ("reminders",))
    _script(monkeypatch, "none")
    out = validator.enforce_final_action_honesty(_served_result(ask, reply, tool_name="respond.direct"),
                                                 user_input=ask, session_id="openclaw:claim",
                                                 source_context={"session_id": "openclaw:claim"})
    assert out["response"] == reply


def test_an_action_result_vool_wrote_is_still_not_judged(monkeypatch) -> None:
    asked = _script(monkeypatch, "done")
    ask = "drop the reminder about the gym bag"
    reply = "I could not find a pending reminder for that."
    result = _served_result(ask, reply, tool_name="reminder.cancel")
    assert result["model_execution"] == {"source": "channel_action", "used_model": False}
    validator.enforce_final_action_honesty(result, user_input=ask, session_id="openclaw:claim",
                                           source_context={"session_id": "openclaw:claim"})
    assert asked == []
