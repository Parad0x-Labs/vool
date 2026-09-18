"""The verb "write" is evidence of intent, not the API for it.

Measured live on 2026-08-11 against `qwen2.5:7b`, one sentence with nothing to build in it::

    Write a one-sentence description of my app Lumen, a note-taking app.

VOOL routed it into the file-writing builder. The operator watched `workspace.write_file`, repeated
"FILES CHANGED" blocks, a mutation approval prompt and generated paths go by, and the turn ended
"I can't build with qwen2.5:7b on this turn...". Nothing in that sentence asked for a file.

The chain, seam by seam:

    core/task_router.py:classify                  -> "creative_ideation"   (correct, and ignored)
    build_request_intent.is_build_instruction     -> True, scope="project" (the defect)
    fast_paths_utility.looks_like_agentic_build_request -> True
    turn_frontdoor.py:691                         -> {"result": None}, handing the turn to the builder
    builder_facade.py:375                         -> opens the controller, bypassing the task class

`is_build_instruction` asked "is there a build verb in imperative position, and a build noun
ANYWHERE in the sentence?". `write` is a build verb and `app` is a PROJECT noun, so the answer was
yes -- even though `app` sits inside "of my app Lumen", a prepositional phrase modifying
"description". A build noun in a modifier is what the request is ABOUT. The deliverable is the head
of the direct object, and here that head is a description.

The repair gives the object of the verb a vote: when the head of the direct object is prose, the
sentence is content generation and no lane may claim a build from it. It is a veto and nothing
more, so a request that legitimately builds keeps every verdict it had.

This file asserts the boundary, not the helper. Each prose turn is put through every production
detector that can open a mutation lane, and through the real builder controller in every writing
mode -- because a helper returning False is worth nothing if a detector beside it still says yes.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.build_request_intent import (
    _object_heads,
    is_build_instruction,
    names_a_file,
)

# The reported turn, its four siblings, and the sloppier ways the same thing gets typed.
PROSE_TURNS = (
    "Write a one-sentence description of my app Lumen, a note-taking app.",
    "Write me a short description for a photo editor.",
    "Write a haiku about rain.",
    "Write an email thanking Alice.",
    "Write three names for my app.",
    # Sloppy phrasing: no article, no punctuation, lowercase, a build noun used as a modifier.
    "write me an app description",
    "write description for lumen app",
    "write 3 name ideas for my note taking app",
    "make a short tagline for my cli tool",
    "write a blurb about the api we discussed",
    # The head is prose and a PROJECT noun trails it in a modifier -- the exact shape of the defect.
    "generate a summary of the service for the readme text",
)

# Requests that legitimately touch the operator's disk, each paired with the predicate that owns its
# lane. These are the rows the fix must not buy its correctness with.
MUTATION_TURNS = (
    ('Write README.md with the text "Lumen is a photo editor."', "named_file"),
    ("Create notes.txt in this project containing hello.", "controller"),
    ("Edit package.json and change the version to 0.5.1.", "controller"),
    ("Build the app in this project.", "controller"),
    # Explicit paths, the shape the named-file lane exists for.
    ("create qa5/slug.py with a slugify function", "controller"),
    ("write src/utils/dates.py with a parse_date helper", "named_file"),
    # A prose object WITH a destination is still a write: the file outranks the reading.
    ("write a description of my app into notes.txt", "controller"),
    ("write a description of my app to a file", "controller"),
)

_WRITING_MODES = ("build", "auto", "manual", "review_edits")


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _detectors(agent, sentence: str) -> dict[str, bool]:
    """Every production predicate that can put a turn on a path to the filesystem."""

    from core.agent_runtime.builder.named_file_build import looks_like_named_file_build_request
    from core.agent_runtime.fast_paths_builder import (
        looks_like_builder_request,
        looks_like_explicit_workspace_file_request,
    )
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    return {
        # turn_frontdoor.py:691 -- returns {"result": None} on this alone, which means "no fast path
        # claimed the turn" and hands it to the builder.
        "looks_like_agentic_build_request": looks_like_agentic_build_request(sentence),
        "looks_like_builder_request": looks_like_builder_request(sentence),
        "looks_like_explicit_workspace_file_request": looks_like_explicit_workspace_file_request(
            sentence
        ),
        "looks_like_named_file_build_request": looks_like_named_file_build_request(
            sentence, workspace_root="/tmp/ws"
        ),
        "_looks_like_write_intent_request": agent._looks_like_write_intent_request(sentence),
        "_should_run_builder_controller": agent._should_run_builder_controller(
            effective_input=sentence,
            classification={"task_class": "system_design"},
            source_context={
                "workspace": "/tmp/ws",
                "workspace_root": "/tmp/ws",
                "operating_mode": "auto",
            },
        ),
    }


# --------------------------------------------------------------------------------------
# The authority boundary: prose reaches no mutation lane
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", PROSE_TURNS)
def test_no_production_detector_turns_prose_into_a_build(agent, sentence: str) -> None:
    """One detector saying no is worth nothing if another beside it says yes.

    `_should_run_builder_controller` was reached through `turn_frontdoor`'s agentic-build escape
    hatch, so asserting only on the controller would have left the front door open.
    """

    claimed = [name for name, verdict in _detectors(agent, sentence).items() if verdict]
    assert not claimed, f"{sentence!r} claimed a build via {claimed}"


@pytest.mark.parametrize("sentence", PROSE_TURNS)
@pytest.mark.parametrize("mode", _WRITING_MODES)
def test_prose_never_opens_the_builder_controller_in_any_writing_mode(
    agent, sentence: str, mode: str
) -> None:
    """No builder controller means no generated paths and no mutation approval prompt.

    Manual and review_edits are the modes that produced the approval prompt the operator saw; Build
    and Auto are the ones that would have applied the write without asking.
    """

    profile = agent._builder_controller_profile(
        effective_input=sentence,
        classification={"task_class": "creative_ideation"},
        interpretation=None,
        source_context={
            "workspace": "/tmp/ws",
            "workspace_root": "/tmp/ws",
            "operating_mode": mode,
        },
    )
    assert profile.get("should_handle") is False, f"{sentence!r} opened the builder in {mode}"
    assert not profile.get("mode"), f"{sentence!r} was given build mode {profile.get('mode')!r}"


def test_the_task_class_cannot_stop_a_project_scope_claim(agent) -> None:
    """Nothing downstream was going to save this turn, which is why the verdict had to be fixed.

    `builder_facade.py:375` returns True on a project-scope build instruction alone, ahead of and
    independent of the task-class gate at :412. So a turn the router had already read as prose
    still opened the file-writing builder -- `creative_ideation` is not even in the allowed set at
    :412, and it never got asked. The gate is *supposed* to work that way for a real build; the
    only thing it can safely be handed is a correct verdict.
    """

    context = {"workspace": "/tmp/ws", "workspace_root": "/tmp/ws", "operating_mode": "auto"}
    prose_class = {"task_class": "creative_ideation"}
    assert agent._should_run_builder_controller(
        effective_input="build me a telegram bot", classification=prose_class, source_context=context
    ), "the task class does not gate a project-scope claim -- the verdict is the only guard"
    assert not agent._should_run_builder_controller(
        effective_input=PROSE_TURNS[0], classification=prose_class, source_context=context
    )


# --------------------------------------------------------------------------------------
# The mechanism, named -- so removing it fails a test that says why
# --------------------------------------------------------------------------------------


def test_the_head_of_the_direct_object_is_what_the_verb_asks_for() -> None:
    """A build noun in a modifier is a topic. The object head is the deliverable.

    English noun phrases are head-final, so "an app description" asks for a description and "a cli
    tool" asks for a tool. Reading the FIRST noun instead of the last puts the defect straight back.
    """

    assert _object_heads("write a one-sentence description of my app lumen") == ["description"]
    assert _object_heads("write me an app description") == ["description"]
    assert _object_heads("write three names for my app") == ["names"]
    assert _object_heads("write a haiku about rain") == ["haiku"]
    assert _object_heads("create a cli tool for parsing logs") == ["tool"]
    assert _object_heads("build me a telegram bot") == ["bot"]
    assert _object_heads("create the smallest failing test that reproduces the corruption") == [
        "test"
    ]


def test_a_prose_object_beside_a_file_object_is_still_a_build() -> None:
    """The veto needs EVERY object in the clause to be prose before it fires.

    `imperative_build_sentences` is the gate the named-file lane builds on, so a clause vetoed
    there takes "create notes.txt" down with the description.
    """

    from core.agent_runtime.build_request_intent import imperative_build_sentences

    assert imperative_build_sentences("write a description and create notes.txt")
    assert imperative_build_sentences("write a haiku, then create scratch/out.txt")
    assert is_build_instruction("write a summary and add a unit test")


def test_a_named_file_outranks_the_prose_reading() -> None:
    """"Write a description ... into notes.txt" is a write however the object is phrased."""

    assert names_a_file("write a description of my app into notes.txt")
    assert not names_a_file("write a description of my app lumen")
    assert is_build_instruction("write a description of my app into notes.txt")
    assert not is_build_instruction("write a description of my app lumen")


def test_a_stated_destination_outranks_the_prose_reading() -> None:
    """A destination given in words rather than as a path counts too."""

    assert is_build_instruction("write a description of my app to a file")
    assert is_build_instruction("write a haiku and save it as a file in the reports folder")
    # ... but "in this project" and "in the workspace" are how ordinary requests are phrased and
    # must NOT hand the override to every sentence that mentions the repo.
    assert not is_build_instruction("write a haiku about rain in this project")


# --------------------------------------------------------------------------------------
# The fix is not bought by refusing to write at all
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("sentence", "owner"), MUTATION_TURNS)
def test_a_real_mutation_request_still_reaches_its_lane(agent, sentence: str, owner: str) -> None:
    verdicts = _detectors(agent, sentence)
    if owner == "controller":
        assert verdicts["_should_run_builder_controller"], f"{sentence!r} lost the builder"
    else:
        assert verdicts["looks_like_named_file_build_request"], f"{sentence!r} lost the file lane"


@pytest.mark.parametrize(
    ("sentence", "expected_mode"),
    [
        # P0 simple-file-write: this sentence carries LITERAL content, so it is one typed
        # workspace write through the workflow lane — the exact shape the audit moved off
        # model_build. It stays builder-owned (the controller claims and executes it).
        ("Create notes.txt in this project containing hello.", "workflow"),
        ("Build the app in this project.", "model_build"),
        ("create qa5/slug.py with a slugify function", "model_build"),
        ("build me a telegram bot", "scaffold"),
    ],
)
def test_a_real_build_still_gets_its_build_mode(agent, sentence: str, expected_mode: str) -> None:
    """The controller must not merely open -- it must land in the same lane as before."""

    profile = agent._builder_controller_profile(
        effective_input=sentence,
        classification={"task_class": "system_design"},
        interpretation=None,
        source_context={
            "workspace": "/tmp/ws",
            "workspace_root": "/tmp/ws",
            "operating_mode": "build",
        },
    )
    assert profile.get("should_handle") is True, sentence
    assert profile.get("mode") == expected_mode, sentence
