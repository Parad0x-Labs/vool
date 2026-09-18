"""The skill tools worked and nothing routed chat to them.

Measured 2026-07-30. `skill.create`, `skill.validate` and `skill.install` draft, check and activate
a real `SKILL.md` that the real loader loads -- driven DIRECTLY through `execute_runtime_tool`. From
chat there was no route at all: `plan_tool_workflow` returns `handled=False` for every create and
validate phrasing tried, and no fast path existed, so the only way in was a small local model
choosing to emit the tool JSON itself. That is the failure the fast paths exist to remove, and a
blind drive of eight skill phrasings created zero skills with three hanging at 300s.

One phrasing was not merely uncovered. This::

    new skill: qa-wordcount. counts words in a block of text. go ahead and create it.

routed, deterministically, to `hive.create_topic` -- posting a research TOPIC titled with the
request. A wrong tool answering confidently is worse than no tool at all, which is why that exact
sentence is the first fixture here.

The restraint half is load-bearing and is tested in the same file: a question ABOUT skills must not
author one, and the lane must never invent a name.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime import fast_paths_skill
from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request, skill_request


@pytest.fixture(autouse=True)
def _never_the_operators_real_plugin_tree(tmp_path):
    """Every test in this module writes into a TEMPORARY plugins tree -- including the ones that are
    not supposed to write at all.

    Not belt-and-braces. Sabotaging the "a nameless request is asked about, not guessed" branch into
    a create made `test_the_prompt_for_a_name_does_not_claim_anything_was_written` author a real
    `new-skill/SKILL.md` in the operator's own plugins directory on their Desktop. A test that is
    only harmless while the code is correct is not isolated, and sabotage is precisely when the code
    is not correct.
    """
    import core.skill_tools as skill_tools

    with mock.patch.object(skill_tools, "plugins_root", lambda: tmp_path):
        yield


# ---------------------------------------------------------------------------
# 1. The phrasings that created nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "name"),
    [
        ("create a skill called qa-echo that takes a word and echoes it back twice", "qa-echo"),
        (
            "Could you please author a new skill named qa-greeter? It should greet a person by name.",
            "qa-greeter",
        ),
        ("i need a skill for converting celsius to fahrenheit. make it. call it qa-temp.", "qa-temp"),
        ("make me a skil named qa-slug that turns a title into a url slug", "qa-slug"),
        ("new skill: qa-wordcount. counts words in a block of text. go ahead and create it.", "qa-wordcount"),
        ("draft a skill called release-notes that summarises CHANGELOG.md by version", "release-notes"),
        ("i want a skill named log-triage that groups errors by module", "log-triage"),
        ("build a skill called pr-summary for summarising a pull request", "pr-summary"),
    ],
)
def test_a_named_skill_request_is_recognised(prompt: str, name: str) -> None:
    assert skill_request(prompt) == {"action": "create", "name": name}


def test_the_heading_phrasing_that_reached_the_hive_is_a_skill_request() -> None:
    """`hive.create_topic` claimed this one, so it is pinned on its own."""

    assert skill_request("new skill: qa-wordcount. counts words in a block of text. go ahead and create it.") == {
        "action": "create",
        "name": "qa-wordcount",
    }


@pytest.mark.parametrize(
    ("prompt", "action"),
    [
        ("validate the qa-echo skill", "validate"),
        ("install the qa-echo skill", "install"),
        ("list my skills", "list"),
        ("what skills do i have", "list"),
        ("show me my skills", "list"),
    ],
)
def test_the_other_three_verbs_route_too(prompt: str, action: str) -> None:
    assert (skill_request(prompt) or {}).get("action") == action


def test_listing_is_a_read_and_survives_the_deliberation_gate() -> None:
    """The shared gate reads "what skills do i have" as discussion -- right for a gate that stops
    WRITES, wrong here. Listing is decided before it, so a direct question gets an answer."""

    from core.agent_runtime import build_request_intent

    assert build_request_intent.is_deliberation(" what skills do i have ") is True
    assert (skill_request("what skills do i have") or {}).get("action") == "list"


# ---------------------------------------------------------------------------
# 2. A name is never invented
# ---------------------------------------------------------------------------


def test_a_skill_with_no_name_is_asked_for_rather_than_guessed() -> None:
    assert skill_request("create a skill that does something") == {"action": "needs_name", "name": ""}


def test_the_prompt_for_a_name_does_not_claim_anything_was_written() -> None:
    agent = _agent()
    result = maybe_handle_skill_request(
        agent, "create a skill that does something", session_id="s", source_context={}
    )

    body = result["response"].lower()
    assert "name" in body
    assert "created" not in body and "drafted" not in body


# ---------------------------------------------------------------------------
# 3. The restraint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "what is a skill and how does it work?",
        "how would i create a skill?",
        "should i make a skill for this?",
        "explain how skills are ranked",
        "tell me about the skills you already have loaded",
        "lets discuss whether we need a skill",
        "dont write anything, just tell me if a skill would help",
        "whats the difference between a skill and a plugin?",
        "why do skills need a description?",
    ],
)
def test_a_question_about_skills_authors_nothing(prompt: str) -> None:
    assert skill_request(prompt) is None


def test_a_build_request_is_not_a_skill_request() -> None:
    """The word is the boundary: "make me a script" belongs to the builder, not here."""

    assert skill_request("create a python script that parses logs") is None
    assert skill_request("build me a telegram bot") is None


# ---------------------------------------------------------------------------
# 4. The lane runs the REAL tool, and does not install on its own
# ---------------------------------------------------------------------------


def _agent():
    return SimpleNamespace(
        _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")}
    )


def test_a_create_request_calls_skill_create_with_a_usable_description() -> None:
    calls: list[tuple] = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return SimpleNamespace(response_text="Drafted the skill.", status="ok", ok=True)

    with mock.patch("core.runtime_execution_tools.execute_runtime_tool", fake):
        result = maybe_handle_skill_request(
            _agent(),
            "create a skill called qa-echo that takes a word and echoes it back twice",
            session_id="s",
            source_context={"operating_mode": "auto"},
        )

    assert result is not None
    assert [c[0] for c in calls] == ["skill.create"]
    args = calls[0][1]
    assert args["name"] == "qa-echo"
    # create_skill REFUSES an empty description on purpose: the match corpus is name + description,
    # so a skill without one can never rank and drafting it would be writing something inert.
    assert args["description"].strip()
    assert args["body"].strip()


def test_creating_never_installs() -> None:
    """Authoring is not authorisation to change behaviour -- the same line core/skill_tools.py draws."""

    calls: list[str] = []

    def fake(intent, arguments, **kwargs):
        calls.append(intent)
        return SimpleNamespace(response_text="ok", status="ok", ok=True)

    with mock.patch("core.runtime_execution_tools.execute_runtime_tool", fake):
        maybe_handle_skill_request(
            _agent(), "create a skill called qa-echo that echoes a word", session_id="s", source_context={}
        )

    assert "skill.install" not in calls


def test_a_missing_tool_is_reported_not_pretended() -> None:
    with mock.patch("core.runtime_execution_tools.execute_runtime_tool", lambda *a, **k: None):
        result = maybe_handle_skill_request(
            _agent(), "validate the qa-echo skill", session_id="s", source_context={}
        )

    assert "did not pretend" in result["response"]


def test_the_lane_declines_a_turn_it_does_not_own() -> None:
    """None means "no fast path claimed this", so dispatch continues exactly as before."""

    assert (
        maybe_handle_skill_request(
            _agent(), "what is the weather today", session_id="s", source_context={}
        )
        is None
    )


# ---------------------------------------------------------------------------
# 5. Wired into the front door
# ---------------------------------------------------------------------------


def test_the_front_door_itself_answers_a_named_skill(tmp_path) -> None:
    """The REAL front door, not the handler in isolation.

    A first version of this test read `turn_frontdoor.py` and compared character offsets of the
    handler names. That measures the source layout, not the dispatch: sabotaging the wiring to
    `skill_turn = None and maybe_handle_skill_request(...)` leaves the string in the file and the
    test green. This drives `_handle_turn_frontdoor` and asserts a real staged skill came back.
    """
    import core.skill_tools as skill_tools
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    text = "create a skill called qa-frontdoor that echoes a word back twice"
    with mock.patch.object(skill_tools, "plugins_root", lambda: tmp_path), mock.patch.object(
        agent, "_startup_sequence_fast_path", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.maybe_handle_preference_command", return_value=(False, "")
    ), mock.patch.object(
        agent, "_maybe_handle_credit_command", return_value=None
    ), mock.patch.object(
        agent, "_maybe_handle_workspace_audit_request", return_value=None
    ):
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text,
            source_surface="openclaw",
            session_id="skill-front-door",
            source_context={"operating_mode": "auto", "surface": "openclaw", "_owner_local": True},
            persona=mock.sentinel.persona,
            interpreted=SimpleNamespace(understanding_confidence=0.8),
        )

    result = outcome.get("result")
    assert result is not None, "the front door let a named skill request fall through to a model"
    assert "qa-frontdoor" in str(result.get("response") or "")
    # The file really exists, in staging, not merely a reply that says so.
    staged = tmp_path / skill_tools.STAGING_DIRNAME / "qa-frontdoor" / "SKILL.md"
    assert staged.is_file(), "the reply announced a skill that is not on disk"
    assert not list((tmp_path / "plugins").glob("*/skills/qa-frontdoor/SKILL.md")), "create installed it"


@pytest.mark.parametrize(
    "text",
    [
        "create a skill called qa-frontdoor that echoes a word back twice",
        "make a skill called qa-frontdoor that upper-cases a sentence",
        "new skill: qa-frontdoor. echoes a word back twice. go ahead and create it.",
    ],
)
def test_the_verb_forms_are_not_swallowed_by_the_agentic_build_gate(tmp_path, text: str) -> None:
    """`skill` is a PROJECT noun, so "create a skill called X" satisfies
    `looks_like_agentic_build_request`, and that gate returns `{"result": None}` -- "no fast path
    claimed this turn" -- handing a skill-authoring request to the app builder.

    Measured live with the lane sitting below that gate: the heading form was drafted in 0s while
    "create a skill called qa-echo2 ..." and "make a skill called qa-upper ..." both fell through to
    a cloud model and produced nothing. The lane is now above it.
    """
    import core.skill_tools as skill_tools
    from apps.vool_agent import VoolAgent
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    if text.startswith(("create", "make")):
        assert looks_like_agentic_build_request(text) is True, "fixture no longer exercises the gate"

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    with mock.patch.object(skill_tools, "plugins_root", lambda: tmp_path), mock.patch.object(
        agent, "_startup_sequence_fast_path", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.maybe_handle_preference_command", return_value=(False, "")
    ), mock.patch.object(
        agent, "_maybe_handle_credit_command", return_value=None
    ), mock.patch.object(
        agent, "_maybe_handle_workspace_audit_request", return_value=None
    ):
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text,
            source_surface="api",
            session_id="skill-gate",
            # A workspace is bound, which is the other half of the agentic-build gate's condition.
            source_context={
                "operating_mode": "auto",
                "surface": "api",
                "_owner_local": True,
                "workspace": str(tmp_path),
                "workspace_root": str(tmp_path),
            },
            persona=mock.sentinel.persona,
            interpreted=SimpleNamespace(understanding_confidence=0.8),
        )

    assert outcome.get("result") is not None, "the agentic-build gate swallowed a skill request"
    assert (tmp_path / skill_tools.STAGING_DIRNAME / "qa-frontdoor" / "SKILL.md").is_file()


def test_the_module_exports_what_the_front_door_imports() -> None:
    assert hasattr(fast_paths_skill, "maybe_handle_skill_request")
