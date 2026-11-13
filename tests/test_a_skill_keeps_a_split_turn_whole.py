"""A turn a skill picks reaches that skill whole: no planner splits it.

Measured on the Mac skills A/B (v0.8 round 6 candidate 11eef8dc, qwen3:8b, loop on and off), with
eight VOOL skills added from Browse:

- "Turn this into an X article:" with a pasted article was split by the turn planner into planned
  sub-turns that fetched the article's links and looked for files; seven minutes later the turn
  ended in an HTTP 500. The paste is the one object of the one request.
- "my disk is full, where did the space go" and "assertion error in my tests, what is the cause"
  were cut in two by the conductor, whose explanation step answered one half with no skill and no
  tools, while disk-space-advisor and test-failure-helper were installed for exactly those words.
- With the loop on, pdf-document-review took "explain this pdf contract in Downloads" away from
  the clean loop. The normal lane may not seat its pdf tools (the seat law) and its offer held
  none, so the model tried workspace.read_file and failed; the loop has the pdf tools itself.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.turn_planner import (
    _CONVERTS_PASTED_MATERIAL_RE,
    plan_turn,
    single_shape_needs_no_planner,
)
from tests.test_vool_skills_install_without_a_key import _runtime, home, no_network  # fixtures, used by name
from tests.test_vool_tool_skills_from_browse import _OWN_ROWS, _install

# A short stand-in for the A/B article: headings, a link and a picture marker, several sentences.
PASTE = (
    "Alder Row does not need a bigger catalogue before it needs a clearer way to borrow. Our tool "
    "library began in March 2026 with neighbours sharing things they already owned.\n\n"
    "## Start with what the register records\n\n"
    "Our [borrowing register](https://library.example.org/report?period=autumn#loans) records 40 "
    "participating households and 120 completed loans. Those are different measures: a household "
    "can borrow more than once.\n\n[PICTURE 1: register.png]\n\n"
    "## Make the shelf easy to read\n\n"
    "The [shelf map](https://library.example.org/shelves) groups items by the job they help with.\n"
)

CONVERT_HEADS = (
    "Turn this into an X article:",
    "Make this an X thread:",
    "Format this as an X article:",
    "Turn these notes into a blog post:",
    "Please turn the text below into a LinkedIn post:",
    "Convert this to a press release:",
    "can you make this into a newsletter:",
)

OWNER = {"surface": "desktop", "_owner_local": True, "client_host": "127.0.0.1"}


def _browse_skill(name: str, home, monkeypatch) -> None:
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == name)
    _install(entry["id"], home, monkeypatch)


@pytest.mark.parametrize("head", CONVERT_HEADS)
def test_turning_pasted_material_into_something_is_one_request(head: str) -> None:
    turn = f"{head}\n\n{PASTE}"
    assert single_shape_needs_no_planner(turn) is True

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a paste to turn into one piece bought a decomposition call")

    assert plan_turn(turn, ask_model=_must_not_run) == []


@pytest.mark.parametrize(
    "head",
    [
        "Turn on the lights",
        "Turn off the oven and text Ana",
        "Make a list of what to pack",
        "Make a note and remind me tomorrow",
        "Three things",
        "Do these",
        "Check the weather and the news",
    ],
)
def test_a_head_that_does_not_turn_the_paste_into_something_keeps_the_planner(head: str) -> None:
    assert _CONVERTS_PASTED_MATERIAL_RE.match(head) is None
    # A colon may still introduce a LIST of requests; those keep the planner (pinned in
    # tests/local_speed/test_planner_declines_single_shape.py).
    assert single_shape_needs_no_planner(
        f"{head}:\n\nbook the dentist for Tuesday morning, renew the parking permit online, and "
        "send the plumber the photos of the leak"
    ) is False


def _recording_planner(monkeypatch) -> list[str]:
    calls: list[str] = []

    def _build(*_args, **_kwargs):
        def _ask(_system: str, prompt: str) -> str:
            calls.append(prompt)
            return '[{"request": "' + prompt.replace('"', "'") + '", "depends_on": []}]'

        return _ask

    monkeypatch.setattr("core.agent_runtime.turn_planner_hook.build_planner_ask_model", _build)
    return calls


@pytest.fixture
def agent(home):
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


@pytest.mark.parametrize(
    ("name", "ask"),
    [
        ("disk-space-advisor", "my disk is full, where did the space go"),
        ("test-failure-helper", "assertion error in my tests, what is the cause"),
    ],
)
def test_the_conductor_leaves_a_turn_an_added_skill_picks_whole(agent, home, no_network, monkeypatch, name, ask):
    calls = _recording_planner(monkeypatch)

    def _conduct():
        return agent._maybe_answer_conductor_turn(
            effective_input=ask, raw_input=ask, session_id="skill-whole", source_context=dict(OWNER)
        )

    assert _conduct() is None
    assert calls, "without the skill the conductor plans this turn (the A/B split it in two)"
    calls.clear()
    _browse_skill(name, home, monkeypatch)
    assert _conduct() is None
    assert calls == [], f"the conductor split a turn {name} picks"


def test_the_turn_planner_leaves_a_turn_an_added_skill_picks_whole(agent, home, no_network, monkeypatch):
    from core.agent_runtime import turn_planner

    asked: list[str] = []

    def _plan(text, **_kwargs):
        asked.append(text)
        return []

    monkeypatch.setattr(turn_planner, "plan_turn", _plan)
    ask = "my disk is full, where did the space go"

    def _planned():
        return agent._maybe_answer_planned_turn(
            effective_input=ask, session_id="skill-whole", source_context=dict(OWNER)
        )

    assert _planned() is None
    assert asked == [ask], "without the skill the planner is asked about this turn"
    asked.clear()
    _browse_skill("disk-space-advisor", home, monkeypatch)
    assert _planned() is None
    assert asked == [], "the turn planner was asked to split a turn an added skill picks"


@pytest.mark.parametrize(
    ("name", "ask", "loop_keeps"),
    [
        ("pdf-document-review", "explain this pdf contract in Downloads", True),
        ("disk-space-advisor", "my disk is full, where did the space go", True),
        ("csv-insights", "what does this csv say about our sales", True),
        # Its workspace tools are not the loop's either: the normal lane at least has its words.
        ("test-failure-helper", "assertion error in my tests, what is the cause", False),
    ],
)
def test_the_loop_keeps_an_ask_whose_skill_needs_the_loops_own_tools(home, no_network, monkeypatch, name, ask, loop_keeps):
    from core.clean_loop.route import LOOP_RUNTIME_TOOLS, decide
    from core.tool_offer_assembly import skill_needs_lane_tools, skill_picks_turn

    monkeypatch.delenv("VOOL_CLEAN_LOCAL_LOOP", raising=False)  # the 0.8 default: on
    _browse_skill(name, home, monkeypatch)
    # The skill is picked, and the conductor and the planner leave the turn whole for it...
    assert skill_picks_turn(ask, source_context=dict(OWNER)) == "added_skill_turn"
    # ...but the clean loop keeps it when the skill needs tools only the loop has here.
    assert skill_needs_lane_tools(name, LOOP_RUNTIME_TOOLS) is loop_keeps
    decision = decide(ask, source_context=dict(OWNER), session_id="loop-tools", resolve_brain=lambda: object())
    if loop_keeps:
        assert decision.admit, decision.reason
    else:
        assert not decision.admit and decision.reason == "added_skill_turn", decision.reason


def test_the_loops_tools_are_real_runtime_tools() -> None:
    from core.clean_loop.route import LOOP_RUNTIME_TOOLS
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    missing = LOOP_RUNTIME_TOOLS - set(contracts)
    assert not missing, sorted(missing)
