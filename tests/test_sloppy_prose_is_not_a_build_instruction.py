"""The prose veto has to survive the way people actually type, not one grammatical shape.

`test_writing_prose_is_not_writing_a_file.py` fixed the reported sentence:

    Write a one-sentence description of my app Lumen, a note-taking app.   -> chat, no tools.

Measured live on 2026-08-11, one word shorter and it broke again::

    Write a one-sentence description my super app Thunder, a lightining monitor app.

    -> classified `unknown`, entered builder generation, created pending approvals under
       `generated/one-sentence-description-super-92ef86/`, proposed README.md, thunder.py and
       test_thunder.py, and put `workspace.write_file` approvals in front of the operator.

Both sentences ask for one sentence of marketing prose. The only difference is the preposition.

`_object_heads` ended the direct object at a preposition or a participle and nowhere else, so
"description OF my app Thunder" stopped at `of` and gave `description`, while "description my super
app Thunder" ran to the end of the line and gave `app`. A build noun then satisfied
`is_build_instruction(scope="project")`, `looks_like_agentic_build_request` handed the turn to the
builder at `turn_frontdoor.py:691`, and the builder scaffolded a project out of a request for a
sentence. The fix was too grammar-shape-specific: it depended on the operator typing the
preposition.

**The rule this file holds: the lexical verb `write` plus the noun `app` is not mutation
authority.** The deliverable is the head of the FIRST noun phrase after the verb. When that head is
prose -- a description, a tagline, a blurb, three names -- the turn is content generation whatever
nouns trail behind it, and no lane may open a mutation from it. Only a named file or a stated
destination outranks it.

So the object is now cut at all three things that end a noun phrase in English, in the order English
uses them:

  * a preposition or participle handing over to a modifier  -- "a description OF my app";
  * a determiner or possessive opening a second phrase      -- "a description | MY super app";
  * a finite verb opening a clause, which takes the noun before it as its subject
                                                            -- "product description | app IS thunder".

and a head is matched to the prose vocabulary within one typo from six characters up, because
`descrption` and `sentance` are the same request badly typed and were reaching the builder on the
spelling alone.

Every variant below is asserted against every production predicate that can open a mutation lane,
against the real builder controller in all four writing modes, and against the planner that emits
the `workspace.write_file` step -- because the operator saw approvals, and a helper returning False
proves nothing if the seam beside it still says yes.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.build_request_intent import (
    _ARTIFACT_ONLY_NOUNS,
    _CONTENT_HEAD_NOUNS,
    _IMPERATIVE_VERB_RE,
    _PROJECT_NOUNS,
    _is_content_generation,
    _is_content_head,
    _normalise,
    _object_heads,
    _within_one_edit,
    is_build_instruction,
)

# --------------------------------------------------------------------------------------
# The family
# --------------------------------------------------------------------------------------

# The turn the operator reported, verbatim, typos and all.
REPORTED = "Write a one-sentence description my super app Thunder, a lightining monitor app."

# Same request, written cleanly, each with a different verb. Two of these carry a hard build verb
# (`generate`, `create`) aimed at prose, which is the case the veto exists for.
CLEAN_PARAPHRASES = (
    "Generate a one-sentence description of my app Thunder, a lightning monitor app.",
    "Create a short description for my app Thunder.",
    "Compose a tagline for my app Thunder.",
    "Draft app store copy for Lumen.",
    "Come up with three names for my app Thunder.",
    "Make a one-line pitch for my app Thunder.",
    "Summarise my app Thunder in one sentence.",
)

# The ways it gets typed in a hurry. Grouped by what is wrong with them, because each group broke a
# different assumption in the parser.
SLOPPY_VARIANTS = (
    # Missing prepositions -- the reported defect, and the shape the previous fix depended on.
    "Write a one sentence description my app Lumen note taking app.",
    "Write description my product Bolt password manager.",
    "Write a tagline my tool Nova.",
    "Write a paragraph my app RainDesk.",
    # Typos in the object head itself.
    "write a one sentance descrption my app thunder",
    "write shrot blurb my app thunder lightning montior",
    # Reordered -- the topic first, the instruction second.
    "my app thunder, write a description",
    "thunder is my app, write me a short blurb",
    # Casual, no punctuation, chat filler.
    "write me a tagline my app thunder pls",
    "write me copy for my startup Orbit",
    # Fragments with no verb at all.
    "one sentence description my app thunder",
    "need a description my app thunder asap",
    # No preposition AND a copula clause glued on with no punctuation.
    "Write product description app is Thunder lightning monitor.",
)

PROSE_FAMILY = (REPORTED, *CLEAN_PARAPHRASES, *SLOPPY_VARIANTS)

# Requests that must keep reaching the filesystem. The fix may not be bought with these.
NEGATIVE_CONTROLS = (
    ('Write README.md with this text: "Thunder monitors lightning."', "named_file"),
    ("Create app.py with a CLI that prints thunder.", "named_file"),
    ("Build a small app called Thunder.", "controller"),
    ("Create a small Python project with README and tests.", "controller"),
    ("Write the following into notes.txt: hello thunder.", "named_file"),
    ("Create src/thunder.py containing a function named distance_to_storm.", "named_file"),
    # A build whose object carries a prose word as a MODIFIER. These are the rows that stop the
    # repair from being widened into "any content word anywhere vetoes the turn" -- which passes
    # every prose variant above and quietly kills a third of the build lane.
    ("Build a story app called Thunder.", "controller"),
    ("Create a name generator script.", "controller"),
    ("Create a script that writes a summary of the logs.", "controller"),
)

# Sentences that read like the family above and are not. The prose object is real in each; a named
# file or a stated destination sits beside it, and the file outranks the reading.
ADVERSARIAL_NEAR_MISSES = (
    "Write a one-sentence description of my app Thunder into README.md",
    "Write a short description my app Thunder and save it to a file",
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
# The authority boundary
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sentence", PROSE_FAMILY)
def test_no_production_detector_claims_a_build_from_the_family(agent, sentence: str) -> None:
    claimed = [name for name, verdict in _detectors(agent, sentence).items() if verdict]
    assert not claimed, f"{sentence!r} claimed a build via {claimed}"


@pytest.mark.parametrize("sentence", PROSE_FAMILY)
@pytest.mark.parametrize("mode", _WRITING_MODES)
def test_the_family_never_opens_the_builder_in_any_writing_mode(
    agent, sentence: str, mode: str
) -> None:
    """No controller means no `generated/...` directory and no `workspace.write_file` approval.

    Manual and review_edits are the modes that produced the approval prompts the operator saw for
    `generated/one-sentence-description-super-92ef86/`; build and auto would have written without
    asking.
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


@pytest.mark.parametrize("sentence", PROSE_FAMILY)
def test_the_family_produces_no_workspace_write_step(sentence: str) -> None:
    """The planner is the seam that turns a turn into `workspace.write_file`, so it gets asked too."""

    from core.execution.planner import _extract_workspace_file_plan

    plan = _extract_workspace_file_plan(
        sentence, source_context={"workspace": "/tmp/ws", "workspace_root": "/tmp/ws"}
    )
    assert plan is None, f"{sentence!r} planned a workspace write: {plan!r}"


@pytest.mark.parametrize("sentence", PROSE_FAMILY)
def test_the_family_is_vetoed_on_its_object_and_not_by_accident(sentence: str) -> None:
    """The reason has to be the object head, or this whole file passes vacuously.

    "Write description my product Bolt password manager." claimed nothing BEFORE the repair too --
    not because the object was read correctly, but because `product` and `manager` happen to be
    absent from the build vocabulary. One noun added to that list and it routes to the builder. So
    each variant must be stopped by the thing that is supposed to stop it: either the content-
    generation veto fires, or the sentence carries no imperative build verb at all and no lane could
    have claimed it in the first place.
    """

    normalised = _normalise(sentence)
    if _IMPERATIVE_VERB_RE.search(normalised):
        assert _is_content_generation(normalised, whole_text=normalised), (
            f"{sentence!r} carries an imperative build verb and was not vetoed as prose -- "
            f"object heads were {_object_heads(normalised)}"
        )
    else:
        assert not _object_heads(normalised), sentence


@pytest.mark.parametrize(
    "sentence",
    [s for s in PROSE_FAMILY if "app" in s.lower() or "tool" in s.lower()],
)
def test_a_build_noun_is_present_and_still_loses_to_the_object(sentence: str) -> None:
    """The corpus must not be one the old code would have passed anyway.

    Every sentence here carries `app` or `tool` -- a PROJECT noun, in imperative reach of a build
    verb. That is exactly the pairing that opened the builder. The noun is still there; it just no
    longer decides.
    """

    from core.agent_runtime.build_request_intent import _PROJECT_NOUN_RE

    normalised = _normalise(sentence)
    assert _PROJECT_NOUN_RE.search(normalised), sentence
    assert not is_build_instruction(normalised, scope="project"), sentence
    assert not is_build_instruction(normalised), sentence


# --------------------------------------------------------------------------------------
# The mechanism, named -- removing any one of the three cuts fails a test that says which
# --------------------------------------------------------------------------------------


def test_a_determiner_ends_the_object_because_it_opens_a_second_phrase() -> None:
    """The cut the reported sentence needed. English puts one determiner per noun phrase.

    Without it, "a description my super app thunder" is read as a single 6-word noun phrase whose
    head is `app`, and the builder scaffolds a project out of a request for one sentence.
    """

    assert _object_heads("write a one-sentence description my super app thunder") == ["description"]
    assert _object_heads("write a tagline my tool nova") == ["tagline"]
    assert _object_heads("write description my product bolt password manager") == ["description"]
    # ... and the determiner that OPENS the object is not a boundary, or the object is always empty.
    assert _object_heads("write a haiku") == ["haiku"]
    assert _object_heads("write me an app description") == ["description"]


def test_a_finite_verb_takes_the_noun_before_it_as_its_subject() -> None:
    """The cut for "write product description app is thunder".

    Stopping at the verb alone leaves `app` as the last token of the object and the defect stands.
    The noun immediately before a finite verb belongs to the clause that verb opens.
    """

    assert _object_heads("write product description app is thunder lightning monitor") == [
        "description"
    ]
    assert _object_heads("write a blurb the app is called thunder") == ["blurb"]


def test_a_head_is_matched_within_one_typo_from_six_characters_up() -> None:
    """`descrption` is `description` mistyped, and it was reaching the builder on the spelling."""

    assert _is_content_head("descrption")
    assert _is_content_head("sentance")
    assert _is_content_head("tagine")
    assert _is_content_head("description")
    # Two edits is not a typo, and short words are matched exactly -- see the guard below.
    assert not _is_content_head("descrpton")
    assert not _is_content_head("app")
    assert not _is_content_head("api")
    assert not _within_one_edit("thunder", "monitor")


def test_no_build_noun_is_one_typo_from_prose() -> None:
    """The typo tolerance must never swallow a real deliverable.

    Bounded at one edit from six characters up so `app`, `api`, `bot` and `cli` cannot drift into
    the prose vocabulary. This holds that line against every future addition to either list rather
    than against the four words that motivated it.
    """

    for noun in _PROJECT_NOUNS + _ARTIFACT_ONLY_NOUNS:
        if noun in _CONTENT_HEAD_NOUNS:
            continue
        assert not _is_content_head(noun), f"{noun!r} would be vetoed as prose"


# --------------------------------------------------------------------------------------
# The fix is not bought by refusing to write
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("sentence", "owner"), NEGATIVE_CONTROLS)
def test_a_real_mutation_request_still_reaches_its_lane(agent, sentence: str, owner: str) -> None:
    verdicts = _detectors(agent, sentence)
    if owner == "controller":
        assert verdicts["_should_run_builder_controller"], f"{sentence!r} lost the builder"
    else:
        assert verdicts["looks_like_named_file_build_request"], f"{sentence!r} lost the file lane"


@pytest.mark.parametrize(
    "sentence",
    [
        "Build a story app called Thunder.",
        "Create a name generator script.",
        "Create a script that writes a summary of the logs.",
        "Create a blog app in this project.",
    ],
)
def test_a_prose_word_in_a_modifier_does_not_veto_a_build(sentence: str) -> None:
    """Position is the whole rule. `story`, `name` and `summary` are prose words in build requests.

    The cheap version of this repair -- veto whenever a content word appears anywhere in the
    sentence -- passes every prose variant in this file and takes these four with it. English
    compounds are head-final: "a STORY app" is an app, "a NAME generator" is a generator. Only the
    head of the object decides.
    """

    normalised = _normalise(sentence)
    assert not _is_content_generation(normalised, whole_text=normalised), sentence
    assert is_build_instruction(normalised, scope="project"), sentence


@pytest.mark.parametrize(
    ("sentence", "expected_mode"),
    [
        ("Build a small app called Thunder.", "model_build"),
        ("Create a small Python project with README and tests.", "model_build"),
        ("Create src/thunder.py containing a function named distance_to_storm.", "model_build"),
        ("build me a telegram bot", "scaffold"),
    ],
)
def test_a_real_build_lands_in_the_same_lane_as_before(
    agent, sentence: str, expected_mode: str
) -> None:
    """Modes measured on the base commit before the repair and pinned here unchanged.

    Opening the controller is not enough -- a build that quietly changes lane is a regression the
    prose tests above would never see.
    """

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


@pytest.mark.parametrize("sentence", ADVERSARIAL_NEAR_MISSES)
def test_a_named_destination_outranks_the_prose_reading(agent, sentence: str) -> None:
    """The near-miss: the same prose object, with somewhere to put it.

    These belong to the named-file lane, not to chat. The veto must not fire on them at all -- and
    the scope they resolve to must stay sealed to the file the operator named, so "write it into
    README.md" cannot become a project scaffold either.
    """

    from core.agent_runtime.builder.mutation_scope import resolve_mutation_scope

    normalised = _normalise(sentence)
    assert not _is_content_generation(normalised, whole_text=normalised), sentence
    assert is_build_instruction(normalised), sentence
    claimed = [name for name, verdict in _detectors(agent, sentence).items() if verdict]
    assert claimed, f"{sentence!r} reached no lane at all"

    scope = resolve_mutation_scope(sentence, workspace_root="/tmp/ws")
    if "README.md" in sentence:
        assert scope.kind == "exact", scope
        assert scope.paths == ("README.md",), scope
