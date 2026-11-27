"""An Offline chat's refusals say the chat is Offline (CUT 33 and 44).

Installed-app gates on the v0.8 line:
- gate 50 case 8, "search the web for cello strings" in an Offline chat: the front door answered with its Offline line,
  and the publication gate swapped it for "I didn't look anything up for this ..." because a judge had run that turn;
- gate 43 case 15, "what's the weather in Vilnius right now?": the Offline line was stored, and the gate re-derived the
  refusal without the turn's context, so the person read "Web lookups are off ... turn them on in Settings";
- gate 43 case 18, "email hello to ..." with no account: answered as an inbox read, never naming Offline.
"""
from __future__ import annotations

import pytest

from core import remote_fetch_policy as policy
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import publication_verdict


@pytest.mark.parametrize("decline", [
    policy.OFFLINE_LOOKUP_TEXT, policy.OFFLINE_TEXT, policy.WEB_OFF_LOOKUP_TEXT, policy.MACHINE_OFFLINE_TEXT,
])
def test_the_runtime_decline_is_published_as_it_is_after_a_judge_ran(decline):
    lifecycle = GroundingLifecycle(lifecycle_id="offline-decline", identity=TurnIdentity(),
                                   request_text="search the web for violin rosin", model_authored=True)
    verdict = publication_verdict(lifecycle, decline)
    assert verdict.state == "published" and verdict.content == decline, verdict


def test_a_model_answer_that_only_quotes_a_decline_is_still_checked():
    lifecycle = GroundingLifecycle(lifecycle_id="offline-quote", identity=TurnIdentity(),
                                   request_text="how much is a pint in london", model_authored=True)
    answer = policy.OFFLINE_LOOKUP_TEXT + " A pint costs about £7.50 there."
    verdict = publication_verdict(lifecycle, answer)
    assert "£7.50" not in verdict.content, verdict


def _owner_offline_context() -> dict:
    return {"surface": "api", "platform": "api", "client_host": "127.0.0.1"}


def _judged_a_mail_ask(monkeypatch) -> None:
    """The turn's judge read the message as about the person's mail (core.plugin_ask, as on the installed app for
    "email hello to tester at example dot com"); no model runs here."""
    from core import tool_demand_signals

    real = tool_demand_signals.owner_live_sources
    monkeypatch.setattr(tool_demand_signals, "owner_live_sources",
                        lambda text: tuple(dict.fromkeys((*real(text), "mail"))))


def test_a_mail_send_with_no_account_in_an_offline_chat_names_offline(monkeypatch):
    from core import action_claim_gate, request_trust
    from core import execution_requirements as requirements

    monkeypatch.setattr(request_trust, "request_is_owner_local", lambda _context: True)
    monkeypatch.setattr(action_claim_gate, "_mail_connected", lambda: False)
    monkeypatch.setattr(policy, "local_only_active", lambda _context=None: True)
    _judged_a_mail_ask(monkeypatch)
    reply = requirements.email_not_connected_reply("email hello to dana at example dot com", _owner_offline_context())
    assert reply is not None and reply.startswith("This chat is Offline"), reply
    assert "nothing was sent" in reply, reply


def test_a_mail_send_with_no_account_says_nothing_was_sent_not_inbox(monkeypatch):
    from core import action_claim_gate, request_trust
    from core import execution_requirements as requirements

    monkeypatch.setattr(request_trust, "request_is_owner_local", lambda _context: True)
    monkeypatch.setattr(action_claim_gate, "_mail_connected", lambda: False)
    monkeypatch.setattr(policy, "local_only_active", lambda _context=None: False)
    _judged_a_mail_ask(monkeypatch)
    send = requirements.email_not_connected_reply("email hello to dana at example dot com", _owner_offline_context())
    assert send is not None and "nothing was sent" in send and "inbox" not in send, send
    read = requirements.email_not_connected_reply("any new emails from my landlord today?", _owner_offline_context())
    assert read is not None and "inbox" in read, read
