"""A needs-a-tool ask reaches its tools; a no-tool ask stays tool-less; prose without a tool is retried once.

Root cause (2026-10-29 cloud re-audit, qwen3:8b): 286 of ~470 turns ended on the tool-less
`ordinary_plain_text_chat` route. The AI-first chat lane keeps a chat-class turn unless
`requirements_for` sees a tool demand, and the demand recognizers had no reading for most of the
owner's own live sources, so the model was never handed a tool and answered "your accountant
thing is scheduled for the 3rd at 11" (organizer:C08) with nothing scheduled. The 40 asks below
are fresh development asks, not the audit's: 26 need a live source, 2 ask a mailbox with no
account connected, 12 need no tool at all.
"""
from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from core.execution_requirements import email_not_connected_reply, requirements_for
from core.tool_demand_signals import owner_live_source_intents, owner_live_sources
from core.tool_use_retry import TOOL_USE_RETRY_KEY, needed_tool_intents, retry_offer_intents

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-live", "turn_id": "t-live"}
REMOTE = {**OWNER, "_owner_local": False}

# (ask, source the words need)
NEEDS = [
    ("ping me at half five to call the garage", "operator"),
    ("set a reminder for thursday morning to renew my passport", "operator"),
    ("which reminders have i got coming up?", "operator"),
    ("cancel the reminder about the garage", "operator"),
    ("jot down that the spare key is under the blue pot", "operator"),
    ("pop a note in my notes that the plumber charges 60 an hour", "operator"),
    ("do i have any notes about the car insurance?", "operator"),
    ("pull up my note about the holiday packing list", "operator"),
    ("whats happening on my calendar this friday?", "operator"),
    ("have i got anything on saturday evening?", "operator"),
    ("pencil in lunch with priya on the 14th at 1", "operator"),
    ("move my haircut on friday to 5pm", "operator"),
    ("is wednesday morning clear for a call?", "operator"),
    ("whats hogging my memory right now?", "operator"),
    ("list the programs running on this laptop", "operator"),
    ("how much free space is left on my drive?", "filesystem"),
    ("what pdfs are sitting in my downloads?", "filesystem"),
    ("find the spreadsheet called budget on my computer", "filesystem"),
    ("how many photos are in my pictures folder?", "filesystem"),
    ("whats in the readme file on my desktop?", "filesystem"),
    ("what music files have i got on this computer?", "filesystem"),
]
NO_ACCOUNT_MAIL = [
    "any unread mail from my landlord?",
    "did the bank email me this week?",
]
NO_TOOL = [
    "how long should I boil an egg for a runny yolk?",
    "write a short poem about autumn leaves",
    "what's the difference between a reminder and an alarm, in general?",
    "explain how compound interest works",
    "give me three dinner ideas with chickpeas",
    "translate good morning into spanish",
    "what is 17 times 23?",
    "how do I make a note in the iPhone notes app?",
    "any tips for keeping a calendar organised?",
    "why do computers slow down over time?",
    "hey, how are you today?",
    "write a polite email to my landlord asking about the deposit",
]


def _lane_agent():
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
    from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin

    class _Agent(ProceedIntentSupportMixin, FastPathFacadeMixin, HiveTopicFacadeMixin):
        def _is_chat_truth_surface(self, _source_context: object) -> bool:
            return True

    return _Agent()


def _offered_tools(text: str, context: dict) -> bool:
    """The shipped two-gate decision: the chat lane lets go AND the tool-intent gate opens."""
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    kept = should_keep_ai_first_chat_lane(
        _lane_agent(), user_input=text, classification={"task_class": "chat_conversation"},
        interpretation=None, source_context=dict(context), checkpoint_state=None,
    )
    return (not kept) and should_attempt_tool_intent(text, task_class="chat_conversation", source_context=dict(context))


@pytest.mark.parametrize(("text", "toolset"), NEEDS)
def test_a_live_source_ask_releases_the_lane_with_its_toolset(text: str, toolset: str) -> None:
    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER))
    assert requirements.tools_required, requirements.reason_codes
    assert toolset in requirements.allowed_toolsets
    assert _offered_tools(text, OWNER)


@pytest.mark.parametrize(("text", "toolset"), NEEDS)
def test_the_offer_seats_a_tool_for_the_needed_source(text: str, toolset: str) -> None:
    from core.tool_offer_assembly import assemble_tool_offer

    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER))
    offer = assemble_tool_offer(
        task_class="chat_conversation", toolset_hints=requirements.allowed_toolsets,
        user_text=text, source_context=dict(OWNER),
    )
    wanted = set(needed_tool_intents(text, OWNER))
    assert wanted and wanted & set(offer.intents), (offer.intents, wanted)


@pytest.mark.parametrize("text", NO_TOOL)
def test_a_no_tool_ask_stays_tool_less(text: str) -> None:
    assert owner_live_sources(text) == ()
    assert needed_tool_intents(text, OWNER) == ()
    assert not requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER)).tools_required
    assert not _offered_tools(text, OWNER)


@pytest.mark.parametrize(("text", "toolset"), NEEDS)
def test_a_remote_turn_never_seats_the_owners_sources(text: str, toolset: str) -> None:
    assert needed_tool_intents(text, REMOTE) == ()
    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(REMOTE))
    assert "owner_live_source_request" not in requirements.reason_codes


@pytest.mark.parametrize("text", NO_ACCOUNT_MAIL)
def test_a_mailbox_ask_with_no_account_is_answered_from_that_fact(text: str, monkeypatch) -> None:
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: False)
    reply = email_not_connected_reply(text, dict(OWNER))
    assert reply and "No email account is connected" in reply
    assert email_not_connected_reply(text, dict(REMOTE)) is None


def test_a_connected_or_unknown_mailbox_is_not_answered_from_a_fact(monkeypatch) -> None:
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: True)
    assert email_not_connected_reply(NO_ACCOUNT_MAIL[0], dict(OWNER)) is None
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: None)
    assert email_not_connected_reply(NO_ACCOUNT_MAIL[0], dict(OWNER)) is None
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: False)
    assert email_not_connected_reply("write a polite email to my landlord asking about the deposit", dict(OWNER)) is None


def test_the_audit_r4_wordings_are_read_as_live_sources() -> None:
    """The re-audit's fake-action asks, as regression anchors (not the measuring set)."""
    assert owner_live_sources("put my accountant thing on the 3rd at 11") == ("calendar",)
    assert owner_live_sources("add a note to my notes app that the boiler service is due nov 12") == ("notes",)
    assert owner_live_sources("am I free next Tuesday afternoon at all?") == ("calendar",)
    assert owner_live_sources("is there anything running in the background i should be worried about?") == ("processes",)
    assert owner_live_sources("what videos do i have on here?") == ("files",)
    assert "operator.inspect_processes" in owner_live_source_intents("whats hogging my memory right now?")
    # About VOOL itself, not the owner's processes.
    assert owner_live_sources("are you running on my mac or up in some cloud somewhere?") == ()


def test_a_retry_round_offers_only_the_needed_tools() -> None:
    from core.tool_offer_assembly import assemble_tool_offer

    text = "which reminders have i got coming up?"
    context = {**OWNER, TOOL_USE_RETRY_KEY: {"intents": list(needed_tool_intents(text, OWNER)), "directive": "x"}}
    offer = assemble_tool_offer(task_class="chat_conversation", toolset_hints=("operator",), user_text=text,
                                source_context=context)
    allowed = retry_offer_intents(context)
    assert allowed and set(offer.intents) <= allowed
    assert "operator.list_reminders" in offer.intents


def test_the_retry_directive_reaches_the_prompt() -> None:
    from core.tool_use_retry import retry_directive, tool_use_retry_directive

    directive = tool_use_retry_directive(("operator.list_reminders",))
    assert "operator.list_reminders" in directive and "Do not answer from memory" in directive
    assert retry_directive({TOOL_USE_RETRY_KEY: {"directive": directive}}) == directive
    assert retry_directive({}) == ""


# ---------------------------------------------------------------------------------------------
# Through the real tool loop, with the model stubbed: prose first, then a tool call.
# ---------------------------------------------------------------------------------------------


def _decision(intent: str, arguments: dict):
    from core.memory_first_router import ModelExecutionDecision

    return ModelExecutionDecision(
        source="scripted", task_hash="h", used_model=True, confidence=0.8,
        structured_output={"intent": intent, "arguments": dict(arguments)},
        tool_calls=(SimpleNamespace(intent=intent, arguments=dict(arguments), call_id=f"c-{intent}", name=intent),),
        validation_state="validated", provider_id="p",
    )


class _Router:
    """Round 1 answers in prose (respond.direct). Round 2 calls `then`, round 3 closes."""

    def __init__(self, prose: str, then: tuple[str, dict] | None):
        self.prose, self.then = prose, then
        self.contexts: list[dict] = []

    def resolve_tool_intent(self, **kwargs):
        self.contexts.append(dict(kwargs.get("source_context") or {}))
        round_no = len(self.contexts)
        if round_no == 1 or self.then is None:
            return _decision("respond.direct", {"message": self.prose})
        if round_no == 2:
            return _decision(*self.then)
        return _decision("respond.direct", {"message": "Here is what the tool found."})


def _drive(text: str, router: _Router, context: dict):
    from apps.vool_agent import VoolAgent

    workspace = pathlib.Path(tempfile.mkdtemp())
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    return agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input=text,
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=session,
        source_context={**context, "session_id": session, "runtime_session_id": session,
                        "workspace": str(workspace), "workspace_root": str(workspace)},
        surface="api",
    ) or {}


def test_prose_on_a_needs_tool_turn_is_retried_once_with_only_the_needed_tools() -> None:
    text = "which reminders have i got coming up?"
    router = _Router("You have no reminders coming up.", ("operator.list_reminders", {}))
    result = _drive(text, router, OWNER)

    assert len(router.contexts) >= 2, "the prose reply was accepted as the answer"
    retry = router.contexts[1].get(TOOL_USE_RETRY_KEY) or {}
    assert "operator.list_reminders" in retry.get("intents", [])
    assert "Do not answer from memory" in retry.get("directive", "")
    assert TOOL_USE_RETRY_KEY not in router.contexts[0]
    assert "operator.list_reminders" in ((result.get("details") or {}).get("tool_steps") or [])
    assert result.get("response") != "You have no reminders coming up."


def test_the_retry_is_spent_once_and_then_the_reply_stands() -> None:
    text = "which reminders have i got coming up?"
    router = _Router("You have no reminders coming up.", None)
    _drive(text, router, OWNER)
    assert len(router.contexts) == 2


@pytest.mark.parametrize("context", [OWNER, REMOTE])
def test_a_no_tool_prose_reply_is_never_retried(context: dict) -> None:
    router = _Router("About six minutes.", ("operator.list_reminders", {}))
    _drive("how long should I boil an egg for a runny yolk?", router, context)
    # The tool-intent gate keeps this ask off the tool loop entirely (no catalog, no tool round);
    # if a later change ever lets it in, it still must not be retried toward a tool.
    assert len(router.contexts) <= 1
    assert not any(TOOL_USE_RETRY_KEY in ctx for ctx in router.contexts)


def test_a_remote_needs_tool_reply_is_not_retried() -> None:
    router = _Router("You have no reminders coming up.", ("operator.list_reminders", {}))
    _drive("which reminders have i got coming up?", router, REMOTE)
    assert len(router.contexts) <= 1
    assert not any(TOOL_USE_RETRY_KEY in ctx for ctx in router.contexts)


def test_a_mailbox_ask_with_no_account_never_reaches_the_model(monkeypatch) -> None:
    monkeypatch.setattr("core.action_claim_gate._mail_connected", lambda: False)
    router = _Router("No new emails today.", None)
    result = _drive("did the bank email me this week?", router, OWNER)
    assert router.contexts == []
    assert "No email account is connected" in str(result.get("response") or "")
