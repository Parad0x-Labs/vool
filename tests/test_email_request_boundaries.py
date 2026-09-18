"""Discussion of email is not a mailbox request; asking for a text is not asking to send it.

Two negative controls from the revision-6 gate table still crossed a boundary at 0e3e19ec (probe
evidence probe-gate-a-wordings-r6-final-candidate-d8fd5ab6.txt):

* "For example, a user might type pull up my latest email." -- the example utterance's `latest` read as a
  request for current information: the requirements authority demanded web evidence and the workflow
  planner planned `web.search` for it.
* "Write me a sample cold outreach email template for freelance clients." -- the `email.send` demand rule
  named the email family for a request whose deliverable is a kind of text (a template), so the tool
  offer seated the email tools.

Both are repaired at the owners that already decide what the user's words ask for: reported example
utterances are not eligible request text (core.retrieval_constraints), and an outgoing-message demand is
recognized the way a mailbox request is (core.tool_demand_signals) -- with instruction authority first, and
a kind of text refused. ORIGINAL + NOVEL wordings for each, and controls that must keep their reading.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

EXAMPLE_UTTERANCES = (
    "For example, a user might type pull up my latest email.",                                # ORIGINAL
    "For instance, someone could ask: what's the latest news on kiln prices?",                 # NOVEL
    "An example request would be: check the latest weather in Riga.",                          # NOVEL
    "Say a customer writes \"show me my newest invoices\" -- how would you handle that wording?",  # NOVEL, quoted
)
TEXT_KIND_REQUESTS = (
    "Write me a sample cold outreach email template for freelance clients.",   # ORIGINAL
    "Can you write an example follow-up email I could adapt for my clients?",  # NOVEL
    "Draft a generic thank-you email format for our workshop attendees.",     # NOVEL
    "How should I write a polite reminder email?",                            # NOVEL, a how-question
)
OUTGOING_MESSAGE_REQUESTS = (
    "Send an email to Mara saying Wednesday works.",
    "Reply to the delivery email and accept Wednesday.",
    "Forward that invoice email to accounting.",
    "Draft a reply to the kiln shop saying we accept the estimate.",
    "Write an email to my landlord asking when the lease renews, and send it.",
)
MAILBOX_REQUESTS = (
    "Pull up my latest email.",
    "Check my email from the orchard supplier.",
)
WEB_REQUESTS = (
    "What's the latest news on kiln prices?",
    "Search the web for the current price of kiln shelves.",
)


@pytest.fixture(autouse=True)
def _isolated_policy(tmp_path, monkeypatch):
    from core import policy_engine, runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous = policy_engine._POLICY_CACHE
    policy = dict(policy_engine.load(force_reload=True))
    policy["email"] = {**(policy.get("email") or {}), "read_enabled": True, "send_enabled": True}
    policy_engine._POLICY_CACHE = policy
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous
        runtime_paths.configure_runtime_home(None)


def _context() -> dict[str, Any]:
    session = "openclaw:boundary-" + uuid.uuid4().hex
    return {"surface": "openclaw", "platform": "openclaw", "runtime_session_id": session,
            "session_id": session, "turn_id": "turn-" + uuid.uuid4().hex}


def _planned(text: str) -> str:
    from core.execution.planner import plan_tool_workflow

    plan = plan_tool_workflow(user_text=text, task_class="chat_conversation", executed_steps=[], source_context=_context())
    return str((plan.next_payload or {}).get("intent") or "") if plan.handled else ""


# ---------------------------------------------------------------------------------------------
# Reported example utterances are not the user's request
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", EXAMPLE_UTTERANCES)
def test_a_reported_example_utterance_asks_for_no_evidence_and_plans_no_search(text: str) -> None:
    from core.execution_requirements import requirements_for
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.tool_demand_signals import resolve_demand_signals

    eligible = analyze_retrieval_constraints(text).eligible_text
    assert "latest" not in eligible.lower() and "newest" not in eligible.lower(), eligible
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert "request_promises_evidence" not in requirements.reason_codes, requirements.reason_codes
    assert not requirements.tools_required, requirements.reason_codes
    assert _planned(text) == "", _planned(text)
    assert not resolve_demand_signals(text).explicit_intents


@pytest.mark.parametrize("text", WEB_REQUESTS)
def test_control_the_users_own_current_information_request_keeps_its_web_plan(text: str) -> None:
    from core.execution_requirements import requirements_for

    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert requirements.tools_required and "web_search" in requirements.allowed_toolsets, requirements.reason_codes
    assert _planned(text) == "web.search"


CONFINED_MAILBOX_REQUESTS = (
    "Now check my personal inbox for the orchard supplier.",          # ORIGINAL: served 2026-09-14, planned onto web.search
    "Search my personal inbox for the Willow Ridge orchard.",         # NOVEL: another sender that reads like an entity
)
ENTITY_LOOKUPS = (
    "Look up the orchard supplier's website.",
    "Who is the orchard supplier for Willow Ridge?",
)


@pytest.mark.parametrize("text", CONFINED_MAILBOX_REQUESTS)
def test_a_mailbox_request_naming_a_sender_is_never_planned_onto_the_web(text: str) -> None:
    """The requirements confine the turn to the email family; a sender that also reads like a public entity
    is not a reason to search the web for it. Measured served 2026-09-14: "Now check my personal inbox for the
    orchard supplier." was planned as an entity lookup, ran the research lane and reached no email tool."""
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import resolve_demand_signals

    assert "email.read" in resolve_demand_signals(text).explicit_intents
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert requirements.tools_required and tuple(requirements.allowed_toolsets) == ("email",), requirements.reason_codes
    assert _planned(text) == "", _planned(text)


@pytest.mark.parametrize("text", CONFINED_MAILBOX_REQUESTS)
def test_a_mailbox_request_naming_a_sender_is_not_classified_as_research(text: str) -> None:
    """The task router's lookup detectors keyed on the word "email" to keep a mailbox request out of the
    research class; "my personal inbox for the orchard supplier" carried no such word. A request about the
    user's own mail is decided by the mailbox recognizer, whatever the sender phrase looks like."""
    from core.task_router import classify, looks_like_explicit_lookup_request, looks_like_public_entity_lookup_request

    assert not looks_like_public_entity_lookup_request(text)
    assert not looks_like_explicit_lookup_request(text)
    assert classify(text, {"surface": "openclaw", "platform": "openclaw"}).get("task_class") != "research"


@pytest.mark.parametrize("text", ENTITY_LOOKUPS)
def test_control_a_public_entity_lookup_keeps_its_web_plan(text: str) -> None:
    from core.task_router import classify, looks_like_public_entity_lookup_request

    assert looks_like_public_entity_lookup_request(text)
    assert classify(text, {"surface": "openclaw", "platform": "openclaw"}).get("task_class") == "research"
    assert _planned(text) == "web.search", _planned(text)


@pytest.mark.parametrize("text", MAILBOX_REQUESTS)
def test_control_the_users_own_mailbox_request_still_reaches_the_read_tool(text: str) -> None:
    from core.tool_demand_signals import resolve_demand_signals
    from core.tool_offer_assembly import assemble_tool_offer

    assert "email.read" in resolve_demand_signals(text).explicit_intents
    assert _planned(text) != "web.search"
    assert "email.read" in assemble_tool_offer(user_text=text, task_class="unknown", source_context=_context()).intents


# ---------------------------------------------------------------------------------------------
# A kind of text is not an outgoing message
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", TEXT_KIND_REQUESTS)
def test_asking_for_a_kind_of_email_text_names_no_email_tool(text: str) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_demand_signals import resolve_demand_signals
    from core.tool_offer_assembly import assemble_tool_offer

    signals = resolve_demand_signals(text)
    assert not [intent for intent in signals.explicit_intents if intent.startswith("email.")], signals
    assert "email" not in signals.required_families, signals
    requirements = requirements_for(text, task_class="chat_conversation", source_context=_context())
    assert not requirements.tools_required and "email" not in requirements.allowed_toolsets, requirements.reason_codes
    # The offer's base seating keeps one representative per enabled family (the model's entry for
    # capability.expand_family), whatever the text says; what a request must not do is seat the email
    # tools BECAUSE of its words. So the email seats of a kind-of-text request are exactly those of an
    # unrelated sentence.
    unrelated = assemble_tool_offer(user_text="Tell me about stoicism in two sentences.", task_class="unknown",
                                    source_context=_context()).intents
    offered = assemble_tool_offer(user_text=text, task_class="unknown", source_context=_context()).intents
    assert [i for i in offered if i.startswith("email.")] == [i for i in unrelated if i.startswith("email.")], offered
    assert "email.draft.save" not in offered and "email.read" not in offered, offered


@pytest.mark.parametrize("text", OUTGOING_MESSAGE_REQUESTS)
def test_control_an_outgoing_message_request_still_names_the_email_family(text: str) -> None:
    from core.tool_demand_signals import resolve_demand_signals

    signals = resolve_demand_signals(text)
    assert "email.send" in signals.explicit_intents and "email" in signals.required_families, (text, signals)


def test_control_a_prohibition_still_outranks_an_outgoing_message_request() -> None:
    from core.tool_demand_signals import resolve_demand_signals

    signals = resolve_demand_signals("Without using any tools, write an email to Mara saying Wednesday works.")
    assert not [intent for intent in signals.explicit_intents if intent.startswith("email.")], signals


def test_control_a_quoted_send_request_is_someone_elses_text() -> None:
    from core.tool_demand_signals import resolve_demand_signals

    signals = resolve_demand_signals('My manager wrote "send an email to the whole team" -- what did she mean by that?')
    assert not [intent for intent in signals.explicit_intents if intent.startswith("email.")], signals


# ---------------------------------------------------------------------------------------------
# Open email work does not trap a genuinely new task
# ---------------------------------------------------------------------------------------------


def test_an_explicit_web_lookup_leaves_the_email_lane_while_email_work_is_open(monkeypatch) -> None:
    from core import email_work_state
    from core.execution.planner import plan_tool_workflow
    from core.execution_requirements import requirements_for

    monkeypatch.setattr(email_work_state, "email_work_owns_follow_up_references", lambda *a, **k: True)
    text = "Look up the latest kiln relining prices on the web."
    context = _context()
    plan = plan_tool_workflow(user_text=text, task_class="chat_conversation", executed_steps=[], source_context=context)
    assert plan.handled and (plan.next_payload or {}).get("intent") == "web.search", (plan.reason, plan.next_payload)
    requirements = requirements_for(text, task_class="chat_conversation", source_context=context)
    assert "web_search" in requirements.allowed_toolsets, requirements.reason_codes
