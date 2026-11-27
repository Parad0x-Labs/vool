"""A general question or small talk raises no tool card and no tool round, in any language.

iMac on a6a5f29b7 (qwen3:8b, Manual mode): a Lithuanian how-long cooking question and an English "hope your weekend
was good" both got a Manual approval card. The cards' meaning arm placed each one near a single loose example of a
tool VOOL's turn judge is asked about (the agenda card's "do I have anything this weekend", whose only content word is
a time span), the card released the tool lane, and the tool round called that tool. The turn's judge had read the same
message against the same tool's description and picked nothing.

The embedder is scripted here: each stray message is placed on one tool example, as the Mac's embedder placed them.
The model, whenever it is asked for a tool, calls the tool the card seated.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core import plugin_ask, tool_card_router

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-stray", "turn_id": "t-stray", "action_policy": "allowed"}

# Fresh wordings (no card example, no greeting fast-path phrase), each with the tool example the scripted embedder
# puts it on: the kind of loose neighbour a general question or small talk finds by meaning.
STRAY = (
    ("en", "what's a good temperature to roast a chicken at?", "ping me in 20 minutes to check the oven",
     "operator.schedule_reminder"),
    ("en", "glad the week is nearly over, it's been a long one", "show my schedule for next week",
     "operator.show_agenda"),
    ("lt", "Kodėl rudenį lapai pagelsta?", "priminkite man rytoj paskambinti mamai", "operator.schedule_reminder"),
    ("lt", "Smagaus tau penktadienio vakaro!", "ką turiu kalendoriuje šiandien", "operator.show_agenda"),
    ("de", "Wie lange braucht Licht von der Sonne bis zur Erde?", "erinnere mich morgen an den Arzttermin",
     "operator.schedule_reminder"),
    ("de", "Schönes Wochenende gehabt? Meins war ruhig.", "was steht morgen in meinem Kalender",
     "operator.show_agenda"),
)
# Real asks of the same tools, placed on the same examples: the judge picked their tool, so the card stands.
TOOL_ASKS = (
    ("en", "put a reminder in for the parcel pickup at 5", "remind me to call mum at 6", "operator.schedule_reminder",
     "reminders"),
    ("lt", "kas mano kalendoriuje poryt?", "ką turiu kalendoriuje šiandien", "operator.show_agenda", "calendar"),
    ("de", "Was habe ich am Donnerstag im Kalender?", "was steht morgen in meinem Kalender", "operator.show_agenda",
     "calendar"),
)
# A tool no judge choice names (the disk): the judge never read the message against it, so its card is not overruled.
DISK_ASK = ("Wie viel Platz ist auf der Platte noch übrig?", "Wie viel Speicherplatz ist auf meiner Festplatte noch frei?",
            "operator.inspect_disk_usage")


@pytest.fixture
def card_flag(monkeypatch: pytest.MonkeyPatch):
    from core import policy_engine

    monkeypatch.setenv("VOOL_TOOL_CARD_ROUTING", "1")
    policy_engine.load(force_reload=True)
    tool_card_router.reset()
    plugin_ask.forget()
    yield
    plugin_ask.forget()
    monkeypatch.delenv("VOOL_TOOL_CARD_ROUTING", raising=False)
    policy_engine.load(force_reload=True)
    tool_card_router.reset()


@pytest.fixture
def stray_space(card_flag, monkeypatch: pytest.MonkeyPatch):
    """A scripted meaning space: every example has a direction of its own, and each message above lands exactly on
    the example it is listed with (cosine 1: the strongest fit the meaning arm can give)."""
    index = tool_card_router._index()
    texts = sorted({a.text for e in index.entries for a in (*e.examples, *e.not_for)})
    dims = len(texts) + 1

    def axis(n: int) -> list[float]:
        vector = [0.0] * dims
        vector[n] = 1.0
        return vector

    vectors = {text: axis(n) for n, text in enumerate(texts)}
    placed = {row[1]: row[2] for row in STRAY} | {row[1]: row[2] for row in TOOL_ASKS} | {DISK_ASK[0]: DISK_ASK[1]}
    for example in placed.values():
        assert example in vectors, f"the card example {example!r} is gone"
    monkeypatch.setitem(tool_card_router._vectors, "backend", "neural:stub")
    monkeypatch.setattr(tool_card_router, "_semantic_vectors", lambda _index, *, wait: vectors)
    monkeypatch.setattr(tool_card_router, "_usable", lambda intent: True)

    def embed(text: str):
        flat = " ".join(str(text or "").split())
        return vectors.get(placed.get(flat, flat), axis(dims - 1)), "neural:stub"

    monkeypatch.setattr(tool_card_router, "_embed_ask", embed)


class _CallsTheSeatedTool:
    """A model that, whenever it is asked for a tool, calls the first offered tool the cards seated."""

    def __init__(self, intent: str) -> None:
        self.intent = intent
        self.rounds = 0

    def resolve_tool_intent(self, **_kwargs):
        from core.memory_first_router import ModelExecutionDecision

        self.rounds += 1
        intent = self.intent if self.rounds == 1 else "respond.direct"
        arguments = {} if self.rounds == 1 else {"message": "Sure."}
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": arguments},
            tool_calls=(SimpleNamespace(intent=intent, arguments=arguments, call_id=f"c-{intent}", name=intent),),
            validation_state="validated", provider_id="p",
        )


def _drive(text: str, model: _CallsTheSeatedTool, tmp_path) -> dict:
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


@pytest.mark.parametrize(("lang", "text", "example", "intent"), STRAY)
def test_the_meaning_arm_alone_still_finds_the_loose_neighbour(lang, text, example, intent, stray_space) -> None:
    # The reproduction: with no judgement on the turn (the judge failed or was not asked), the meaning arm releases
    # the tool, as it did on the iMac. The words decide alone then, as before.
    assert intent in [m.intent for m in tool_card_router.releases_lane(text, owner_local=True)]


@pytest.mark.parametrize(("lang", "text", "example", "intent"), STRAY)
def test_a_tool_the_judge_read_and_did_not_pick_is_not_released_by_its_card(lang, text, example, intent,
                                                                          stray_space) -> None:
    from core.execution_requirements import requirements_for
    from core.tool_use_retry import needed_tool_intents

    plugin_ask._record_builtins(text, ())   # the turn's judge: no tool of the person's
    assert tool_card_router.routed_intents(text, owner_local=True) == ()
    assert tool_card_router.releases_lane(text, owner_local=True) == ()
    assert not requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER)).tools_required
    assert needed_tool_intents(text, OWNER) == ()


@pytest.mark.parametrize(("lang", "text", "example", "intent"), STRAY)
def test_a_general_question_runs_no_tool_round(lang, text, example, intent, stray_space, tmp_path) -> None:
    plugin_ask._record_builtins(text, ())
    model = _CallsTheSeatedTool(intent)
    result = _drive(text, model, tmp_path)
    assert model.rounds == 0, "the turn reached a tool round"
    assert not ((result.get("details") or {}).get("tool_steps") or [])
    assert not result.get("approval_request")


@pytest.mark.parametrize(("lang", "text", "example", "intent", "pick"), TOOL_ASKS)
def test_a_real_ask_the_judge_picked_keeps_its_card(lang, text, example, intent, pick, stray_space) -> None:
    from core.execution_requirements import requirements_for

    plugin_ask._record_builtins(text, (pick,))
    assert intent in [m.intent for m in tool_card_router.releases_lane(text, owner_local=True)]
    assert requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER)).tools_required


def test_a_pick_of_another_tool_does_not_carry_a_card_the_judge_left_out(stray_space) -> None:
    text = STRAY[1][1]
    plugin_ask._record_builtins(text, ("notes",))
    assert "operator.show_agenda" not in [m.intent for m in tool_card_router.routed_intents(text, owner_local=True)]


def test_a_tool_no_judge_choice_names_keeps_its_card(stray_space) -> None:
    text, _example, intent = DISK_ASK
    plugin_ask._record_builtins(text, ())
    assert intent in [m.intent for m in tool_card_router.releases_lane(text, owner_local=True)]


@pytest.mark.parametrize("text", (
    "hope your weekend was relaxing, mine flew by",
    "Kaip praėjo savaitgalis? Pas mane buvo ramu.",
    "Wie war dein Wochenende? Hoffentlich erholsam.",
    "friday already, can you believe it",
))
def test_words_alone_meet_no_agenda_example_by_a_time_span(text: str, card_flag) -> None:
    # The agenda card's examples name the calendar or the schedule: a remark that only shares a day or a weekend
    # with one of them shares one word, which the word arm reads as coincidence.
    assert "operator.show_agenda" not in [m.intent for m in tool_card_router.match_cards(text, semantic="off")]


def test_the_agenda_card_still_matches_its_own_asks_by_words(card_flag) -> None:
    for text in ("do I have anything on my calendar this weekend?", "what is on my schedule for friday?"):
        got = tool_card_router.match_cards(text, semantic="off")
        assert got and got[0].intent == "operator.show_agenda", got
