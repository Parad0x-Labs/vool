"""A greeting, a thank-you or small talk takes no tool round, in any language.

iMac on the v0.8 line (local qwen3:8b): a greeting went to the tool lane and ran operator.list_tools, an 86 s
turn to say hello back. The plain answer has a tool card of its own (respond.direct: "hi, how are you?",
"thanks!"), and the card router read a fit to it as a fit to a tool: the card released the tool-less chat lane,
the round demanded a tool call, and the model called the first navigation tool it was offered. With the local
embedder up, a greeting in Lithuanian or German fits that card by meaning just as well.

The model and the embedder are scripted here: the embedder places every message below where the Mac's
embedder places a greeting (on the plain answer's own examples), and the model, whenever it is asked for a
tool, picks the first tool listed after the plain answer. None of them may reach it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core import tool_card_router

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-greeting", "turn_id": "t-greeting", "action_policy": "allowed"}

# Fresh wordings: none of them is a card example or a phrase VOOL's greeting fast path knows.
SMALL_TALK = (
    "hey you, lovely to see you again",
    "cheers for that, really appreciated",
    "Labas rytas! Kaip tau šiandien sekasi?",
    "Ačiū, labai padėjai",
    "Servus, alles gut bei dir?",
    "Vielen lieben Dank, das war super",
)
# A real tool ask the cards must still release (skill.list's card, by its words).
TOOL_ASK = "list my skills"


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


@pytest.fixture
def greeting_space(card_flag, monkeypatch: pytest.MonkeyPatch):
    """A scripted meaning space: the small talk above lands on the plain answer's own greeting examples, every
    other ask and example has a direction of its own (nothing else is near anything)."""
    index = tool_card_router._index()
    direct = next(e for e in index.entries if e.intent == tool_card_router.ANSWER_WITHOUT_A_TOOL)
    texts = sorted({a.text for e in index.entries for a in (*e.examples, *e.not_for)} | {TOOL_ASK})
    dims = len(texts) + 2   # one direction per text, one for any other ask, one for greetings
    greeting_axis = [0.0] * dims
    greeting_axis[-1] = 1.0
    greeting_examples = {a.text for a in direct.examples[:1]} | {a.text for a in direct.examples if "!" in a.text}

    def own_axis(n: int) -> list[float]:
        vector = [0.0] * dims
        vector[n] = 1.0
        return vector

    vectors = {text: (greeting_axis if text in greeting_examples else own_axis(n)) for n, text in enumerate(texts)}
    monkeypatch.setitem(tool_card_router._vectors, "backend", "neural:stub")
    monkeypatch.setattr(tool_card_router, "_semantic_vectors", lambda _index, *, wait: vectors)

    def embed(text: str):
        flat = " ".join(str(text or "").split())
        return (greeting_axis if flat in SMALL_TALK else vectors.get(flat, own_axis(dims - 2))), "neural:stub"

    monkeypatch.setattr(tool_card_router, "_embed_ask", embed)
    return direct


class _FirstListedTool:
    """A model that, whenever it is asked for a tool, calls the first tool listed after the plain answer."""

    def __init__(self) -> None:
        self.rounds = 0

    def resolve_tool_intent(self, **_kwargs):
        from core.capability_graph import _ALWAYS_VISIBLE_ORDER
        from core.memory_first_router import ModelExecutionDecision

        self.rounds += 1
        intent = _ALWAYS_VISIBLE_ORDER[1] if self.rounds == 1 else "respond.direct"
        arguments = {} if self.rounds == 1 else {"message": "Hello!"}
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": arguments},
            tool_calls=(SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent),),
            validation_state="validated", provider_id="p",
        )


def _drive(text: str, model: _FirstListedTool, tmp_path) -> dict:
    from apps.vool_agent import VoolAgent

    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = model
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False   # only the tool gate decides
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
        source_context={**OWNER, "session_id": session, "runtime_session_id": session,
                        "workspace": str(tmp_path), "workspace_root": str(tmp_path)},
        surface="api",
    ) or {}


@pytest.mark.parametrize("text", SMALL_TALK)
def test_small_talk_fitting_the_plain_answer_releases_no_tool_lane(text: str, greeting_space) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_use_retry import needed_tool_intents

    assert tool_card_router.match_cards(text) == (), "the plain answer's own card was read as a tool's"
    assert not requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER)).tools_required
    assert needed_tool_intents(text, OWNER) == ()


@pytest.mark.parametrize("text", SMALL_TALK)
def test_small_talk_runs_no_tool_even_with_a_model_that_picks_the_first_tool(text: str, greeting_space,
                                                                             tmp_path) -> None:
    model = _FirstListedTool()
    result = _drive(text, model, tmp_path)
    assert model.rounds == 0, "the turn reached a tool round"
    assert not ((result.get("details") or {}).get("tool_steps") or [])


def test_a_real_tool_ask_still_gets_its_tool(greeting_space) -> None:
    from core.execution_requirements import requirements_for

    assert "skill.list" in [m.intent for m in tool_card_router.releases_lane(TOOL_ASK, owner_local=True)]
    assert requirements_for(TOOL_ASK, task_class="chat_conversation", source_context=dict(OWNER)).tools_required


def test_the_plain_answer_is_never_a_card_match_of_its_own(greeting_space) -> None:
    # Beside a tool that fits better, the plain answer's card is dropped, not seated as a tool: it sits on every
    # round already, first.
    for text in (TOOL_ASK, *SMALL_TALK):
        assert tool_card_router.ANSWER_WITHOUT_A_TOOL not in [m.intent for m in tool_card_router.match_cards(text)]
