"""SERVED: a family the model expands is callable in the later rounds of that turn (revision 6, Gate B).

One real served conversation (tests/_email_served_conversation.py): the daemon, POST /api/chat, shipped
routing, the tool offer, the tool loop and the production certification door, over the ORIGINAL Gmail
inbox fixture. Two things are SIMULATED and labelled: the MODEL is the certified scripted model, whose
choices here are SCRIPTED (ask for the `skill` family once, then call `skill.list` when the runtime
offers it), and the PROVIDERS are tests/provider_api_fixture.py on loopback.

Asserted from what the daemon actually sent the model and recorded:

* before the expansion no round offers the family's tools; the expansion executes and names the family's
  own seats; EVERY later round of the same turn natively offers every seat it named: the round in which the
  model calls one of them (and the call executes), and the round after that call, which reads the turn's
  navigation set again -- a read that cleared the set would leave that round without the family;
* in every tool round the prompt's text catalog lists exactly the tools the native definitions carry
  (one offer per round, core.tool_offer_assembly);
* the expansion does not outlive its turn: the conversation's next turn offers none of the family's tools.

`skill` stands in for a non-email family because its tools are available and read-only in this isolated
runtime. `knowledge` has no available implementation here; expanding it is refused as
`family_unavailable` (tests/test_runtime_turn_navigation.py), so it cannot show a family's tools
becoming callable.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests import _reader_served_rig as rig
from tests._email_served_conversation import (
    ORIGINAL_GMAIL,
    Action,
    EmailConversation,
    ModelView,
    ScriptedEmailModel,
    Step,
    _install,
    _observations,
    _offers,
    call,
    say,
)

pytestmark = [pytest.mark.pa_beta]

CATALOG_LINE = re.compile(r"(?:^|\s)- ([a-z0-9_]+(?:\.[a-z0-9_]+)+)\(")
#: The runtime's one-tool capability probe: a certification round, not a round of the turn's offer.
CAPABILITY_PROBE_TOOLS = ["vool__capability_probe"]
FAMILY = "skill"
INTENT = "skill.list"
FIRST_TURN = "Check my email from the orchard supplier."


def _native(intent: str) -> str:
    return intent.replace(".", "__")


class RoundRecordingModel(ScriptedEmailModel):
    """The scripted model, recording for every tool round of the turn's offer the native tool names, the
    prompt catalog and the seats the turn's expansion observation named. The runtime's one-tool capability
    probe round is answered as usual and not recorded: it carries no offer and no catalog."""

    def __init__(self) -> None:
        super().__init__()
        self.rounds: list[dict[str, Any]] = []

    def answer(self, path: str, body: dict[str, Any]) -> dict[str, Any] | str:
        native = sorted(rig._tool_names(body))
        record: dict[str, Any] | None = None
        if native and native != CAPABILITY_PROBE_TOOLS:
            messages = [message for message in (body.get("messages") or []) if isinstance(message, dict)]
            prompt = rig._all_text(messages)
            expansions = [item for item in _observations(prompt) if item.get("intent") == "capability.expand_family"]
            record = {
                "native": native,
                "catalog": sorted(set(CATALOG_LINE.findall(prompt))),
                "expansion_seats": list((expansions[-1] if expansions else {}).get("seated_intents") or []),
            }
        reply = super().answer(path, body)
        if record is not None:
            record["choice"] = self.requests[-1]["choice"]
            self.rounds.append(record)
        return reply


def plan_expand_and_call(family: str, intent: str) -> Callable[[ModelView], Action]:
    """SCRIPTED: ask for `family` once, call `intent` once the runtime offers it, then report."""
    def plan(view: ModelView) -> Action:
        if not view.results("capability.expand_family"):
            return call("capability.expand_family", family=family)
        if view.results(intent):
            return say(f"SCRIPT: {intent} ran ({view.results(intent)[-1].get('status')}).")
        if _offers(view, intent):
            return call(intent)
        return say(f"SCRIPT: {intent} was not offered after the expansion.")
    return plan


def plan_report_offer(intent: str) -> Callable[[ModelView], Action]:
    """SCRIPTED: call nothing; say whether `intent` is offered in this turn."""
    def plan(view: ModelView) -> Action:
        return say(f"SCRIPT: {intent} offered: {_offers(view, intent)}.")
    return plan


def _evidence_path(tmp_path: Path) -> Path:
    configured = os.environ.get("VOOL_EMAIL_JOURNEY_EVIDENCE_DIR", "").strip()
    folder = Path(configured) if configured else tmp_path
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"served-expansion-{FAMILY}-{time.strftime('%Y%m%d-%H%M%S')}.json"


def test_served_expanded_family_stays_callable_in_every_later_round_of_its_turn_and_ends_with_it(tmp_path) -> None:
    conversation = EmailConversation(tmp_path / "expansion", ORIGINAL_GMAIL, label=f"expansion-{FAMILY}")
    model = RoundRecordingModel()
    conversation.model = model
    _install(conversation.provider, model)
    evidence = _evidence_path(tmp_path)
    expand_rounds: list[dict[str, Any]] = []
    later_rounds: list[dict[str, Any]] = []
    try:
        conversation.open()
        mark = len(model.rounds)
        expansion_turn = conversation.turn(Step("expand", FIRST_TURN, plan_expand_and_call(FAMILY, INTENT)))
        expand_rounds = model.rounds[mark:]
        mark = len(model.rounds)
        later_turn = conversation.turn(Step("later", FIRST_TURN, plan_report_offer(INTENT)))
        later_rounds = model.rounds[mark:]
    except Exception as exc:
        conversation.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        conversation.close()
        evidence.write_text(json.dumps({**conversation.to_json(), "rounds": model.rounds}, indent=2, default=str))

    # Before the expansion: the family is not offered, and the model asks for it. The turn then has the round
    # that calls the family's tool and the round after that call.
    assert len(expand_rounds) >= 3, [record["choice"] for record in expand_rounds]
    assert _native(INTENT) not in expand_rounds[0]["native"], expand_rounds[0]
    assert expand_rounds[0]["choice"][0] == "capability__expand_family", expand_rounds[0]["choice"]
    expansion_facts = [fact for fact in expansion_turn.facts
                       if fact["kind"] == "tool" and fact["name"] == "capability.expand_family"]
    assert expansion_facts and all(fact["ok"] for fact in expansion_facts), expansion_turn.facts

    # Every later round of the same turn: every seat the expansion named is natively callable. In the first of
    # them the model calls one and the call runs; the round after that call is where a navigation set that a
    # read had cleared would already be gone.
    after_expansion = expand_rounds[1:]
    seats = after_expansion[0]["expansion_seats"]
    assert INTENT in seats, after_expansion[0]
    for record in after_expansion:
        assert {_native(intent) for intent in seats} <= set(record["native"]), record
    assert after_expansion[0]["choice"][0] == _native(INTENT), after_expansion[0]["choice"]
    ran = [fact for fact in expansion_turn.facts if fact["kind"] == "tool" and fact["name"] == INTENT]
    assert ran and all(fact["ok"] for fact in ran), expansion_turn.facts
    assert expansion_turn.reply.startswith(f"SCRIPT: {INTENT} ran"), expansion_turn.reply

    # One offer per round: the catalog the model reads lists exactly the tools it can call.
    for record in expand_rounds + later_rounds:
        assert record["catalog"], f"a tool round carried no prompt catalog: {record['native']}"
        assert set(record["catalog"]) == {name.replace("__", ".") for name in record["native"]}, record

    # The expansion ended with its turn.
    assert later_rounds, "the later turn reached no tool round"
    assert all(_native(INTENT) not in record["native"] for record in later_rounds), later_rounds
    assert later_turn.reply == f"SCRIPT: {INTENT} offered: False.", later_turn.reply
    assert conversation.closed.get("leftover_processes") == [], conversation.closed
