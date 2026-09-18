"""Talking about building is not asking for it.

Five separate implementations of "is this a build request?" existed, each with its own vocabulary,
each matching by bare substring containment. Measured against the real functions before this change,
on 17 discussion sentences and 12 real build requests:

    looks_like_builder_request                        fast_paths_builder.py:7
    looks_like_agentic_build_request                  fast_paths_utility.py:612
    the inline gate in _should_run_builder_controller builder_facade.py:355
    _looks_like_write_intent_request                  builder_facade.py:434
    looks_like_explicit_workspace_file_request        fast_paths_builder.py:99  (found by measuring)

    -> 31 wrong verdicts of 116. Afterwards: 0 of 116.

They also disagreed with each other on nearly every row, so which one a turn happened to reach
decided whether files were written. `lets discuss whether we should build an api` reached the
file-writing builder.

Substring matching produced collisions that need only a build verb elsewhere in the sentence:
`app` sits inside "happens", `site` inside "opposite", `api` inside "rapid", `cli` inside "client".

The distinction now drawn once, in `core/agent_runtime/build_request_intent.py`, is instruction
versus deliberation — the one that decides whether anything is written to the operator's disk.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.build_request_intent import (
    is_build_instruction,
    is_deliberation,
    is_opted_out,
)

DELIBERATION = (
    "lets discuss whether we should build an api",
    "can we create a new skill?",
    "should i build a cli for this or use the existing one",
    "how would you build a bot like that",
    "is it worth creating a service for this",
    "explain how to implement a webhook",
    "what does scaffold mean in this context",
    "tell me about the tool you would generate",
    "why did you create that file earlier",
    "we already built the api last week",
    "pros and cons of building our own api",
    "should we create the files first or plan?",
    "i was thinking about building a dashboard",
    "a monolith vs a service for this workload",
)

# Sentences with no build verb in imperative position. A verb buried mid-clause is a noun phrase far
# more often than a command.
NOT_AN_INSTRUCTION = (
    "what happens if the agent times out",
    "the opposite approach would be simpler",
    "rapid prototyping is the goal here",
    "i want to understand the app architecture first",
    "the client library is in rapid development",
    "the tool creation process is slow",
    "what happens when we generate the report",
    # A build verb mid-clause, describing what something DOES. Added after a sabotage run: dropping
    # the imperative-position requirement broke no test, which meant nothing tested it.
    "the app we build every night is fine",
    "the script that generates reports lives in tools",
    "our api creates a new file per request",
    "the bot writes logs to disk",
    "this service adds a header to every response",
)

# An imperative build verb with NO build noun — only letters of one, inside an ordinary word:
# `cli` in "client", `api` in "rapid", `site` in "opposite", `app` in "happens". The word boundary is
# the only thing standing between these and a build, so they are the only fixtures that test it.
NOUN_ONLY_AS_A_SUBSTRING = (
    "add client-side validation",
    # Was "write rapid unit tests", which still exercised `api` inside `rapid` but stopped being a
    # counter-example the moment `test`/`tests` became real build nouns -- because "write unit
    # tests" IS a build request, and the runtime can honour it (`workspace.write_file` +
    # `workspace.run_tests`). The substring guard this row exists for is unchanged; only the
    # example moved off a word that now legitimately builds. See TEST_ARTIFACT_INSTRUCTIONS below.
    "write rapid validation logic",
    "make the opposite change",
    "create whatever happens to be missing",
)

# A regression test is an artifact this runtime can produce, and asking for one must reach a lane.
# Measured 2026-07-31: "Prove it. Create the smallest failing test that reproduces the corruption.
# Run it..." answered "I couldn't map that cleanly to a real action" three times, because a build
# VERB in imperative position found no build NOUN -- `test` was in neither vocabulary.
TEST_ARTIFACT_INSTRUCTIONS = (
    "create the smallest failing test that reproduces the corruption",
    "write a regression test for this bug",
    "add a unit test covering the empty input case",
)

INSTRUCTIONS = (
    "build me a telegram bot",
    "create a cli tool for parsing logs",
    "scaffold a fastapi service in the workspace",
    "implement the api now",
    "generate the code for the website",
    "start building the app",
    "write the files",
    "build and verify in the workspace",
    "please build me a telegram bot",
    "ok now create the api",
    "go ahead and scaffold the service",
    "can you build me a bot",
    "i want you to create a cli tool",
    "in my workspace, create a fastapi service",
    "make a new folder and add three files",
    "create a file notes.txt with the text hello",
)


@pytest.mark.parametrize("sentence", DELIBERATION)
def test_a_discussion_about_building_does_not_build(sentence: str) -> None:
    assert is_deliberation(sentence), f"{sentence!r} is not recognised as deliberation"
    assert not is_build_instruction(sentence)
    assert not is_build_instruction(sentence, scope="project")


@pytest.mark.parametrize("sentence", NOT_AN_INSTRUCTION)
def test_a_verb_outside_imperative_position_does_not_build(sentence: str) -> None:
    assert not is_build_instruction(sentence), f"{sentence!r} was read as an instruction"


@pytest.mark.parametrize("sentence", NOUN_ONLY_AS_A_SUBSTRING)
def test_letters_of_a_build_noun_inside_another_word_are_not_that_noun(sentence: str) -> None:
    assert not is_build_instruction(sentence), f"{sentence!r} was read as a build"
    assert not is_build_instruction(sentence, scope="project"), sentence


@pytest.mark.parametrize("sentence", TEST_ARTIFACT_INSTRUCTIONS)
def test_asking_for_a_test_reaches_a_lane(sentence: str) -> None:
    """The one artifact a debugging turn always asks for.

    "Prove it -- write the failing test and run it" is the correct next step after a bug claim, and
    the runtime owns both halves. Answering "I couldn't map that cleanly to a real action" to it
    made the whole prove-it-before-you-fix-it loop unreachable.
    """

    assert is_build_instruction(sentence), f"{sentence!r} was not recognised as an instruction"


@pytest.mark.parametrize("sentence", INSTRUCTIONS)
def test_a_real_instruction_still_builds(sentence: str) -> None:
    """The fix must not be bought by refusing to build at all."""

    assert is_build_instruction(sentence), f"{sentence!r} was not recognised as an instruction"


@pytest.mark.parametrize(
    "sentence",
    [
        "build me a bot but do not write any files",
        "design an api, advice only",
        "just plan the service for now",
        "scaffold the cli without writing anything",
    ],
)
def test_an_explicit_opt_out_stops_the_build(sentence: str) -> None:
    assert is_opted_out(sentence)
    assert not is_build_instruction(sentence)


def test_an_instruction_beside_a_question_still_builds() -> None:
    """Clauses are judged separately; the instruction is the part that writes files."""

    assert is_build_instruction("build me a bot, then tell me how it works")
    assert is_build_instruction("create the api. what does that cost to run?")


# --------------------------------------------------------------------------------------
# Scope — which lane a build request belongs to
# --------------------------------------------------------------------------------------


class TestScope:
    """A folder is not a project, and conflating them routes a turn into the wrong lane.

    `looks_like_agentic_build_request` selects the model-driven build lane. Widening its nouns to
    include `folder` and `file` sent "create a folder called tools and start putting code in there"
    down that lane instead of to the workspace tools that handle it, and three continuity tests
    caught it immediately.
    """

    @pytest.mark.parametrize(
        "sentence",
        [
            "create a folder called tools and start putting code in there",
            "create a file notes.txt with exactly this content: hi",
            "make a new directory for the reports",
        ],
    )
    def test_file_and_folder_work_is_an_artifact_build_not_a_project_build(self, sentence) -> None:
        assert is_build_instruction(sentence), sentence
        assert not is_build_instruction(sentence, scope="project"), sentence

    @pytest.mark.parametrize(
        "sentence",
        ["build me a telegram bot", "create a cli tool for parsing logs", "start building the app"],
    )
    def test_a_whole_deliverable_is_a_project_build(self, sentence: str) -> None:
        assert is_build_instruction(sentence, scope="project"), sentence


# --------------------------------------------------------------------------------------
# Wired, not merely written
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _all_five(agent, sentence: str) -> dict[str, bool]:
    from core.agent_runtime.fast_paths_builder import (
        looks_like_builder_request,
        looks_like_explicit_workspace_file_request,
    )
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    return {
        "looks_like_builder_request": looks_like_builder_request(sentence),
        "looks_like_agentic_build_request": looks_like_agentic_build_request(sentence),
        "_should_run_builder_controller": agent._should_run_builder_controller(
            effective_input=sentence,
            classification={"task_class": "system_design"},
            source_context={
                "workspace": "/tmp/ws", "workspace_root": "/tmp/ws", "operating_mode": "auto",
            },
        ),
        "_looks_like_write_intent_request": agent._looks_like_write_intent_request(sentence),
        "looks_like_explicit_workspace_file_request": looks_like_explicit_workspace_file_request(
            sentence
        ),
    }


@pytest.mark.parametrize("sentence", DELIBERATION + NOT_AN_INSTRUCTION)
def test_no_detector_anywhere_builds_on_a_discussion(agent, sentence: str) -> None:
    """The consolidation is the point. One detector saying no is worth nothing if another says yes.

    This is the assertion the previous shape could not make: five functions, one verdict.
    """

    claimed = [name for name, verdict in _all_five(agent, sentence).items() if verdict]
    assert not claimed, f"{sentence!r} claimed a build via {claimed}"


@pytest.mark.parametrize(
    "sentence", ["build me a telegram bot", "create a cli tool for parsing logs", "write the files"]
)
def test_a_real_build_still_reaches_the_builder(agent, sentence: str) -> None:
    verdicts = _all_five(agent, sentence)
    assert verdicts["_should_run_builder_controller"], f"{sentence!r} would not build"
    assert verdicts["looks_like_builder_request"]


def test_the_shared_module_is_the_only_place_the_decision_is_made() -> None:
    """A sixth private copy of the vocabulary would put the bug straight back."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "core" / "agent_runtime"
    for name in ("fast_paths_builder.py", "fast_paths_utility.py", "builder_facade.py"):
        source = (root / name).read_text(encoding="utf-8")
        if "build_request_intent" not in source:
            continue
        # The old inline verb/noun pair test, verbatim from builder_facade.py:356-360.
        assert '"scaffold", "implement", "generate", "start building"' not in source, name


# --------------------------------------------------------------------------------------
# A test request must reach a lane that can write and run one
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", TEST_ARTIFACT_INSTRUCTIONS)
def test_a_test_request_is_recognised_as_a_test_artifact(sentence: str) -> None:
    """Adding `test` to the build nouns was only half the fix.

    It made `is_build_instruction` say yes, which routed the turn to the builder -- which knows
    starters and bot scaffolds and refused with "I do not have a real bounded builder path".
    Measured 2026-07-31 on "Prove the bug you identified before fixing it. Create the smallest
    deterministic regression test that reproduces the failure." One refusal became a different
    refusal. This predicate is what sends it to the model-driven lane that writes a file and runs
    it instead.
    """

    from core.agent_runtime.build_request_intent import looks_like_test_artifact_request

    assert looks_like_test_artifact_request(sentence), f"{sentence!r} did not reach the test lane"


@pytest.mark.parametrize(
    "sentence",
    [
        "how do I write tests for this module?",
        "what do you think about our testing strategy",
        "build me a telegram bot",
        "create a folder called scratch",
    ],
)
def test_only_a_test_build_request_reaches_the_test_lane(sentence: str) -> None:
    """Questions about testing, and builds that are not tests, must not be captured.

    A false positive here writes a file nobody asked for.
    """

    from core.agent_runtime.build_request_intent import looks_like_test_artifact_request

    assert not looks_like_test_artifact_request(sentence), f"{sentence!r} was captured"
