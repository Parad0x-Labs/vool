"""A tool the cards offered gets used, and its result is what the reply stands on.

Measured on the tool-use kit (qwen3:8b, tool cards on, 2026-10-29/30): cards raised "right tool ran"
but the remaining misses had owning causes past the offer. Each test below pins one of them on the
real code path (the model is scripted where a model would answer):

- a folder the owner named was SEATED for `machine.list_directory`, the model called it, and the
  executor refused it because its own admission proof read only the list-directory word probe
  ("which PDFs are sitting in my Downloads right now?" -> "I did not inspect a local directory");
- a tool seated by its card and answered in prose was never retried, because the one-shot retry
  only knew the hand-written recognizers.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from core import tool_card_router
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent
from core.tool_use_retry import TOOL_USE_RETRY_KEY, needed_tool_intents

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-cards", "turn_id": "t-cards", "action_policy": "allowed"}
REMOTE = {**OWNER, "_owner_local": False}

# Not the fresh measuring asks: development phrasings of the same shapes.
FOLDER_ASKS = (
    "are there any spreadsheets or pdfs sitting in my Downloads?",
    "what videos have I got in my Movies folder?",
    "how many screenshots are on my desktop?",
)
NOT_A_FOLDER = (
    "Which of PNG, TXT, GIF, and PDF is the format defined by ISO/IEC 15948? List only actual members "
    "of that requested standard; do not infer membership merely because every item is a file format.",
    "Which image formats support transparency?",
    "how do I make a new folder on a mac?",
)


@pytest.fixture
def card_flag(monkeypatch: pytest.MonkeyPatch):
    from core import policy_engine

    monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "1")
    policy_engine.load(force_reload=True)
    tool_card_router.reset()
    yield
    monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
    policy_engine.load(force_reload=True)
    tool_card_router.reset()


def _list(path: str, context: dict):
    return execute_tool_intent(
        {"intent": "machine.list_directory", "arguments": {"path": path}},
        task_id="t-cards",
        session_id="s-cards",
        source_context=context,
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )


# -- (a) the seated folder tool is admitted by the executor ---------------------------------------


@pytest.mark.parametrize("text", FOLDER_ASKS)
def test_a_named_owner_folder_admits_the_listing_the_offer_seated(text: str, tmp_path, monkeypatch) -> None:
    from core.agent_runtime.agent import _owner_folder_demand
    from core.execution_requirements import requirements_for
    from core.tool_offer_assembly import assemble_tool_offer

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "invoice_77.pdf").write_bytes(b"%PDF-1.4\n")
    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER))
    offer = assemble_tool_offer(task_class="chat_conversation", toolset_hints=requirements.allowed_toolsets,
                                user_text=text, source_context=dict(OWNER))
    assert "machine.list_directory" in offer.intents, "precondition: the offer seats the listing for this ask"

    context = {**OWNER, "_semantic_machine_list_directory_admitted": _owner_folder_demand(text, OWNER)}
    execution = _list("~/Downloads", context)
    assert execution.ok and execution.status == "executed", execution.response_text
    assert "invoice_77.pdf" in execution.response_text


@pytest.mark.parametrize("text", NOT_A_FOLDER)
def test_a_knowledge_ask_still_cannot_authorize_a_listing(text: str) -> None:
    from core.agent_runtime.agent import _owner_folder_demand

    context = {**OWNER, "_semantic_machine_list_directory_admitted": _owner_folder_demand(text, OWNER)}
    execution = _list("~/Desktop", context)
    assert not execution.ok and execution.status == "blocked_by_semantic_preflight"


@pytest.mark.parametrize("text", FOLDER_ASKS)
def test_a_remote_turn_naming_a_folder_is_not_admitted(text: str) -> None:
    from core.agent_runtime.agent import _owner_folder_demand

    assert _owner_folder_demand(text, REMOTE) is False


# -- (b) a card-seated tool answered in prose gets the one-shot retry ------------------------------


def _decision(intent: str, arguments: dict):
    from core.memory_first_router import ModelExecutionDecision

    return ModelExecutionDecision(
        source="scripted", task_hash="h", used_model=True, confidence=0.8,
        structured_output={"intent": intent, "arguments": dict(arguments)},
        tool_calls=(SimpleNamespace(intent=intent, arguments=dict(arguments), call_id=f"c-{intent}", name=intent),),
        validation_state="validated", provider_id="p",
    )


class _Router:
    """Round 1 answers in prose. Round 2 calls `then`, round 3 closes."""

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
        return _decision("respond.direct", {"message": "These are the skills the tool listed."})


def _drive(text: str, router: _Router, context: dict, tmp_path):
    import uuid

    from apps.vool_agent import VoolAgent

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
                        "workspace": str(tmp_path), "workspace_root": str(tmp_path)},
        surface="api",
    ) or {}


def test_a_card_only_ask_is_a_needed_tool_ask(card_flag) -> None:
    # No hand-written recognizer reads skills; only the skill.list card does.
    assert "skill.list" in needed_tool_intents("list my skills", OWNER)
    assert needed_tool_intents("list my skills", REMOTE) == ()


def test_a_card_seated_tool_answered_in_prose_is_retried_with_that_tool(card_flag, tmp_path) -> None:
    router = _Router("You don't have any skills installed yet.", ("skill.list", {}))
    result = _drive("list my skills", router, OWNER, tmp_path)

    assert len(router.contexts) >= 2, "the prose reply was accepted as the answer"
    retry = router.contexts[1].get(TOOL_USE_RETRY_KEY) or {}
    assert "skill.list" in retry.get("intents", [])
    assert "skill.list" in ((result.get("details") or {}).get("tool_steps") or [])
    assert result.get("response") != "You don't have any skills installed yet."


def test_without_the_card_flag_a_card_only_ask_is_not_retried(tmp_path, monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "0")
    policy_engine.load(force_reload=True)
    tool_card_router.reset()
    try:
        assert needed_tool_intents("list my skills", OWNER) == ()
    finally:
        monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
        policy_engine.load(force_reload=True)
        tool_card_router.reset()


@pytest.mark.parametrize("text", ("give me three name ideas for a grey cat", "what is 17 times 23?"))
def test_a_no_tool_ask_with_cards_on_is_not_retried(text: str, card_flag) -> None:
    assert needed_tool_intents(text, OWNER) == ()


# -- (d) a "saved / I'll remember that" claim with no tool receipt is corrected -------------------

SESSION = "openclaw:tool-cards-use-claims"


@pytest.fixture
def fresh_turns():
    from core.final_answer_authorship import reset_for_tests
    from core.grounding_lifecycle import reset_for_tests as reset_lifecycles

    reset_lifecycles()
    reset_for_tests()
    yield
    reset_lifecycles()
    reset_for_tests()


def _gate(request_text: str, reply: str, request_id: str, tool: str = ""):
    from core.action_claim_gate import gate_action_claims
    from core.grounding_lifecycle import record_model_authorship, register_required
    from core.semantic.semantic_admissions import bound_request_context

    with bound_request_context(request_id):
        context = {"session_id": SESSION, "request_id": request_id, "turn_id": request_id}
        register_required(context, request_text=request_text, reason_codes=("test",))
        record_model_authorship(context)
        if tool:
            from core.turn_model_call_ledger import begin_turn, record_tool_execution

            begin_turn(context)
            record_tool_execution(context, tool)
        return gate_action_claims(reply, turn_id=request_id, source_context=context, connected=lambda _n: False)


SAVE_CLAIMS = (
    # (request, model reply with no tool run, the line the correction must carry)
    ("remember that the spare key is under the blue pot",
     "Got it! I'll remember that the spare key is under the blue pot.", "Nothing was saved"),
    ("jot down that the boiler service is on the 12th",
     "Saved! The boiler service is on the 12th.", "Nothing was saved"),
    ("save a note: the boiler service is on the 12th",
     "Saved! The boiler service is on the 12th.", "No note was saved"),
    ("add a contact: Rasa, phone 0612 3456",
     "I've added Rasa to your contacts with the number 0612 3456.", "No contact was saved or changed"),
    ("merk dir, dass der Schlüssel unter dem Topf liegt",
     "Alles klar, ich merke mir das.", "Es wurde nichts gespeichert"),
)


@pytest.mark.parametrize(("request_text", "reply", "line"), SAVE_CLAIMS)
def test_a_save_claim_with_no_tool_receipt_is_corrected(request_text, reply, line, fresh_turns) -> None:
    published, record = _gate(request_text, reply, f"req:save:{abs(hash(reply))}")
    assert record["publication"] == "action_claims_corrected", record
    assert line in published
    assert reply not in published


def test_a_save_claim_backed_by_a_tool_run_is_left_alone(fresh_turns) -> None:
    reply = "Got it! I'll remember that the spare key is under the blue pot."
    published, record = _gate("remember that the spare key is under the blue pot", reply, "req:save:backed",
                              tool="profile.remember")
    assert published == reply and record["publication"] == "execution_evidence_present"


@pytest.mark.parametrize("reply", (
    "I remember reading that eggs float when they go bad.",
    "Remember to drink water before your run.",
    "If you want, I'll remember it for next time.",
    "You can save his number in your contacts app.",
    "A good habit is to keep a small notebook so you remember things.",
))
def test_recall_advice_and_offers_are_not_save_claims(reply: str) -> None:
    from core.action_claim_gate import action_claims

    assert action_claims(reply) == []


# -- (c) a reply built from a real folder read carries that read's receipt -----------------------


def _session_events(session: str) -> list[dict]:
    from core.runtime_continuity import list_runtime_session_events

    return list_runtime_session_events(session, limit=200)


def test_a_folder_overview_answer_logs_its_folder_read(tmp_path, monkeypatch) -> None:
    import uuid

    from apps.vool_agent import VoolAgent
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_read_request

    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "Desktop" / "reports"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("# Reports\n", encoding="utf-8")
    (folder / "q3.csv").write_text("a,b\n", encoding="utf-8")
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    context = {**OWNER, "session_id": session, "runtime_session_id": session}

    result = maybe_handle_direct_machine_read_request(
        VoolAgent(backend_name="test-backend", device="cards-use"),
        "what's in my reports folder",
        session_id=session,
        source_surface="api",
        source_context=context,
    )

    assert result is not None and "q3.csv" in result["response"]
    assert result["mode"] == "tool_executed"
    assert [r["tool_name"] for r in result["tool_receipts"]] == ["machine.list_directory"]
    executed = [e for e in _session_events(session) if e.get("event_type") == "tool_executed"]
    assert [e.get("tool_name") for e in executed] == ["machine.list_directory"]
    assert any(o.get("intent") == "machine.list_directory" for o in context.get("runtime_tool_observations") or [])



# -- the folder search a seated tool runs finds the folder the owner named ------------------------


def _roots(monkeypatch, root) -> None:
    import core.machine_diagnostics as md

    monkeypatch.setattr(md, "disk_usage", lambda *a, **k: [{"mount": str(root)}])


def test_find_folder_matches_the_named_words_in_any_order(tmp_path, monkeypatch) -> None:
    from core.runtime_execution_tools import _machine_find_folder

    (tmp_path / "Pictures" / "Holiday Lisbon 2026").mkdir(parents=True)
    _roots(monkeypatch, tmp_path)
    result = _machine_find_folder({"name": "Lisbon holiday"})
    matches = (result.details or {}).get("matches") or []
    assert [os.path.basename(m) for m in matches] == ["Holiday Lisbon 2026"], result.response_text


def test_find_folder_still_needs_every_named_word(tmp_path, monkeypatch) -> None:
    from core.runtime_execution_tools import _machine_find_folder

    (tmp_path / "Pictures" / "Holiday Porto 2025").mkdir(parents=True)
    (tmp_path / "Documents" / "Lisbonate").mkdir(parents=True)
    _roots(monkeypatch, tmp_path)
    result = _machine_find_folder({"name": "Lisbon holiday"})
    assert not ((result.details or {}).get("matches") or []), result.response_text
