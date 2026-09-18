"""Natural first-turn mailbox requests reach a callable read tool (revision 6, Gate A).

The request class: the user asks, in their own words, for mail they RECEIVED. Revision 5 recognised it
with one closed verb list (read/check/list/summarize/review/show/open/look at/go through), so "Pull up
the most recent email from the kiln repair shop." and "Search my mail for the Harbor Paper invoice."
got no email read tool -- the chat lane kept them, or the planner sent them to the web
(tests/test_runtime_release_gates.py reproduces both unchanged). The recognizer is object-anchored now
(core.tool_demand_signals.mailbox_retrieval_intents), and every gate such a turn crosses reads it:

* the demand signals and the offer: `email.read` is a callable seat, not only the family's send tool;
* the requirements authority: an email-account action, tools required, confined to the email toolset;
* the planner gate admits the turn, and the workflow planner plans it onto neither the web nor the disk.

ORIGINAL wordings are the review's failures, its working controls and the revision-5 gate table; NOVEL
wordings use other senders, data and verbs. NEGATIVE controls keep the class from swallowing every
mention of mail: a question about email as a topic, how-to and authoring requests, quoted and example
text, a first-person report, the account's own settings, a hypothetical frame. A tool prohibition
("Without using any tools", "Don't use any tools") outranks a mailbox request, and a disabled email
lane offers no email tool at all.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

ORIGINAL_FAILED = (
    "Pull up the most recent email from the kiln repair shop.",
    "Search my mail for the Harbor Paper invoice.",
)
ORIGINAL_WORKING = (
    "Check my email from the orchard supplier.",
    "Read the latest email from the kiln repair shop.",
)
ORIGINAL_GATE_TABLE = (
    "Pull up the latest message from the kiln repair shop.",
    "Find the email the kiln repair shop sent me.",
    "Search my email for the kiln estimate.",
    "Look through my inbox for the kiln estimate.",
    "Did the kiln repair shop email me an estimate?",
    "Show me what the kiln repair shop emailed me.",
    "Search my inbox for messages from the kiln repair shop.",
    "Find the message Harbor Paper sent about invoice 4471.",
    "Pull up whatever the orchard supplier emailed me this week.",
)
NOVEL = (
    "Dig out the message Willow Ridge sent us about the apple crates.",
    "Has Northfield Kiln Repair emailed me since Monday?",
    "Grab the newest message from billing@harborpaper.example.test.",
    "Anything unread in our inbox from the glaze supplier?",
    "I need the invoice email that Harbor Paper sent last Tuesday.",
)
NEGATIVE = (
    "What's a good way to organise supplier emails?",
    "How do I stop emails from going to spam?",
    "How do I search my mail in Outlook?",
    "What is the difference between an email header and an envelope recipient?",
    "Write me a sample cold outreach email template for freelance clients.",
    "Tell me about stoicism in two sentences.",
    'My friend texted "check my email tonight" as a joke. What does that phrase usually mean?',
    "For example, a user might type pull up my latest email.",
    "I got an email from my landlord yesterday.",
    "What's my email address?",
    "What does the inbox zero method mean?",
    "Suppose I had no inbox at all. How would people reach me?",
)
PROHIBITED = (
    "Without using any tools, what is in my inbox?",
    "Don't use any tools, just tell me what is in my inbox.",
    "Do not use any tools. Pull up the most recent email from the kiln repair shop.",
)


@pytest.fixture(autouse=True)
def _isolated_policy(tmp_path, monkeypatch):
    """A hermetic policy home, restored afterwards: a cached ambient policy left behind changes the tool
    surface later suites see (tests/test_email_action_routing.py measured it)."""
    from core import policy_engine, runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous = policy_engine._POLICY_CACHE
    policy_engine._POLICY_CACHE = None
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous
        runtime_paths.configure_runtime_home(None)


def _email_lane(enabled: bool) -> None:
    from core import policy_engine

    policy = dict(policy_engine.load(force_reload=True))
    policy["email"] = {**(policy.get("email") or {}), "read_enabled": enabled, "send_enabled": enabled}
    policy_engine._POLICY_CACHE = policy


def _context() -> dict[str, Any]:
    session = "openclaw:mailbox-" + uuid.uuid4().hex
    return {"surface": "openclaw", "platform": "openclaw", "runtime_session_id": session,
            "session_id": session, "turn_id": "turn-" + uuid.uuid4().hex}


@pytest.mark.parametrize("text", ORIGINAL_FAILED + ORIGINAL_WORKING + ORIGINAL_GATE_TABLE + NOVEL)
def test_a_request_for_received_mail_reaches_a_callable_read_tool_on_its_first_turn(text: str) -> None:
    from core.execution.planner import plan_tool_workflow, should_attempt_tool_intent
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import mailbox_retrieval_intents, resolve_demand_signals
    from core.tool_offer_assembly import assemble_tool_offer

    _email_lane(True)
    assert mailbox_retrieval_intents(text) == ("email.read",)
    signals = resolve_demand_signals(text)
    assert "email.read" in signals.explicit_intents and "email" in signals.required_families, signals

    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert requirements.forbids_toolless_lane(), requirements.reason_codes
    assert "email_account_action_request" in requirements.reason_codes
    assert "email" in requirements.allowed_toolsets and "web" not in requirements.allowed_toolsets

    assert should_attempt_tool_intent(text, task_class="chat_conversation", source_context=_context())
    plan = plan_tool_workflow(user_text=text, task_class="chat_conversation", executed_steps=[],
                              source_context=_context())
    planned = str((plan.next_payload or {}).get("intent") or "")
    assert not planned.startswith(("web.", "machine.", "workspace.")), (plan.reason, planned)

    offered = set(assemble_tool_offer(user_text=text, task_class="unknown", source_context=_context()).intents)
    assert "email.read" in offered, sorted(offered)


@pytest.mark.parametrize("text", NEGATIVE)
def test_a_mention_question_or_report_about_email_is_not_a_mailbox_request(text: str) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import mailbox_retrieval_intents, resolve_demand_signals

    _email_lane(True)
    assert mailbox_retrieval_intents(text) == ()
    assert "email.read" not in resolve_demand_signals(text).explicit_intents
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert "email_account_action_request" not in requirements.reason_codes, requirements.reason_codes


@pytest.mark.parametrize("text", PROHIBITED)
def test_a_tool_prohibition_outranks_a_mailbox_request(text: str) -> None:
    from core.agent_runtime.intent_claims import ActionPolicy, action_policy_for_text
    from core.execution.planner import should_attempt_tool_intent
    from core.execution_requirements import requirements_for
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.tool_demand_signals import mailbox_retrieval_intents

    _email_lane(True)
    assert analyze_retrieval_constraints(text).forbids_all_tools
    assert action_policy_for_text(text) is ActionPolicy.FORBIDDEN
    assert mailbox_retrieval_intents(text) == ()
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert not requirements.tools_required, requirements.reason_codes
    assert not should_attempt_tool_intent(text, task_class="chat_conversation", source_context=_context())


@pytest.mark.parametrize(
    ("text", "all_tools"),
    [
        ("Answer without using any tools: what is 17 times 23?", True),
        ("No need to use any tools, what is the capital of France?", True),
        ("Without any tools, estimate how long a 12 km walk takes.", True),
        ("Without using any web search, explain why the sky is blue.", False),
    ],
)
def test_a_determiner_does_not_hide_a_tool_prohibition(text: str, all_tools: bool) -> None:
    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(text)
    assert constraints.forbids_all_tools is all_tools, constraints
    assert constraints.forbids_external_retrieval


@pytest.mark.parametrize("text", ORIGINAL_FAILED + NOVEL[:2])
def test_a_disabled_email_lane_offers_no_email_tool_and_claims_no_account_action(text: str) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_offer_assembly import assemble_tool_offer

    _email_lane(False)
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert "email_account_action_request" not in requirements.reason_codes, requirements.reason_codes
    offered = assemble_tool_offer(user_text=text, task_class="unknown", source_context=_context()).intents
    assert not [intent for intent in offered if intent.startswith("email.")], offered
