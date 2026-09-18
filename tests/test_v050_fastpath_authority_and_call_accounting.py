"""Fast-path authority across whole request FAMILIES, and a provider-call count that reconciles.

Companion to `tests/test_v050_fast_path_costs_no_speculative_inference.py`, which pins the two
defects behind a measured 136.41s turn (a routing gate that bought inference ahead of the lanes that
answer for free, and a `model_calls` field that counted nothing). That file works from the exact
request that was measured. This one exists because a fix aimed at a measured string is a fix aimed
at a string:

* **TASK A** drives eight SEMANTIC FAMILIES of deterministic local operation, each in several
  phrasings the smoke run never used, and asserts the count of provider calls ENTERED is zero --
  measured at the production seams, not read out of the field under test. Six negative controls
  (three semantic, three live/current) assert the same authority does not extend to work the runtime
  does not own.
* **TASK B** states the accounting rule as a matrix -- 0 calls, 1 call, a failed call followed by a
  successful fallback, and calls made ahead of a deterministic answer -- driven through the router's
  real candidate loop rather than through a hand-rolled recorder.

**The additional live defect this file adds a fix for.** `ok what is usd?` -- five words, one
definition -- was routed to the heavy lane and tried qwen3:14b, then cloud, then qwen3:8b. Measured
on the untouched base 6204d352, at the three decisions that actually buy weight:

    ok what is usd?      research  summarization  queen  paid=True   daily  60.0
    what is usd?         research  summarization  queen  paid=True   daily  60.0
    hey what is json?    config    action_plan    auto   paid=False  deep   None

...while the genuinely live questions read the OTHER way round:

    Latest BTC price.                    unknown  normalization_assist  auto  paid=False
    Current weather in Vilnius.          unknown  normalization_assist  auto  paid=False
    Explain the recent USD move today.   unknown  normalization_assist  auto  paid=False

A three-word definition bought a paid arm; a live market question did not reach the live lane. Both
halves are in `core/task_router.py` and both are pinned below, against each other, because fixing
either one alone is indistinguishable from breaking the other.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)
from tests.test_v050_fast_path_costs_no_speculative_inference import (  # noqa: F401
    ProviderInvocations,
    arbiter_is_reachable,
    invocations,
)

# The bytes each family must come back with. Distinctive strings, so an assertion cannot be
# satisfied by a plausible-looking summary of a file the runtime never opened.
_QA_EVIDENCE = "FASTPATH-EVIDENCE-4471"
_EXACT_ONE_EVIDENCE = "EXACT-ONE-6204"
_SEARCH_NEEDLE = "PROJECT-FILE-275"

#: Which read lane answers is the front door's business (the workspace lane owns a project-relative
#: name, the machine lane owns a path it can resolve on disk); this file's business is that ONE of
#: them did, with no inference bought to find out.
_READ_TOOLS = {"workspace.read_file", "machine.read_file"}
_LIST_TOOLS = {"workspace.list_dir", "machine.list_directory", "workspace.overview"}
_SEARCH_TOOLS = {"workspace.search_text", "workspace.code_search", "machine.search_text"}
_GIT_TOOLS = {"workspace.git_status"}

#: Every deterministic LOCAL-OPERATION tool this branch grants fast-path authority to. A negative
#: control that reaches one of these has been answered by a lane that does not own it.
_LOCAL_OPERATION_TOOLS = _READ_TOOLS | _LIST_TOOLS | _SEARCH_TOOLS | _GIT_TOOLS


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A real folder with real files. Every assertion below reads bytes that are actually there."""
    workspace = tmp_path / "vool-fastpath-families"
    (workspace / "src").mkdir(parents=True)
    (workspace / "qa_test.txt").write_text(f"{_QA_EVIDENCE}\nsecond line\n")
    (workspace / "package.json").write_text('{"name": "fastpath-probe", "version": "0.5.0"}\n')
    (workspace / "exact_one_file_6204.txt").write_text(f"{_EXACT_ONE_EVIDENCE}\n")
    (workspace / "src" / "hit.py").write_text(f"# {_SEARCH_NEEDLE} lives here\n")
    return workspace


def _drive(
    make_agent_module: Any,  # noqa: F811 - pytest's fixture request, not a redefinition
    project: Path,
    text: str,
    *,
    session: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "surface": "api",
        "session_id": session,
        "runtime_session_id": session,
        "request_id": f"req-{session}",
        "workspace": str(project),
        "workspace_root": str(project),
    }
    os.chdir(project)
    return make_agent_module().run_once(text, session_id_override=session, source_context=context)


def _session_for(text: str) -> str:
    return f"fam-{abs(hash(text)) % 9999999}"


# =============================================================================================
# TASK A -- eight families of deterministic local operation, none of which may buy inference
# =============================================================================================

#: (family, phrasing, tools that may answer, bytes the answer must contain).
#:
#: Several phrasings per family on purpose. A single phrasing per family would let a fix that
#: happens to match the sentence the smoke run typed pass as a fix to the family -- which is the
#: exact failure mode `core/task_router.py`'s own `_bare_lookup_marker_claims_this_turn` docstring
#: describes, and the one the additional defect below is another instance of.
_FAMILIES = [
    # 1 -- read a named project file, and say what it says
    ("read_named_file", "Read qa_test.txt and tell me exactly what it says.", _READ_TOOLS, _QA_EVIDENCE),
    ("read_named_file", "read the qa_test.txt file and tell me exactly what it says", _READ_TOOLS, _QA_EVIDENCE),
    ("read_named_file", "open qa_test.txt", _READ_TOOLS, _QA_EVIDENCE),
    ("read_named_file", "show me the contents of qa_test.txt", _READ_TOOLS, _QA_EVIDENCE),
    # 2 -- read a manifest-shaped file, no "tell me" verb at all
    ("read_package_json", "Read package.json.", _READ_TOOLS, "fastpath-probe"),
    ("read_package_json", "what is in package.json", _READ_TOOLS, "fastpath-probe"),
    ("read_package_json", "cat package.json", _READ_TOOLS, "fastpath-probe"),
    # 3 -- repository status
    ("git_status", "Show git status.", _GIT_TOOLS, ""),
    ("git_status", "show git status", _GIT_TOOLS, ""),
    ("git_status", "what's the git status?", _GIT_TOOLS, ""),
    # 4 + 5 -- list the folder, in the two phrasings that were measured buying an arbiter call
    ("list_project", "List files in this project.", set(), "package.json"),
    ("list_project", "list the files in this project", set(), "package.json"),
    ("list_folder", "What files are in this folder?", set(), "package.json"),
    ("list_folder", "what files are in this folder", set(), "package.json"),
    # 6 -- a file whose name is not a word, so no lexical rule can be leaning on "qa" or "test"
    ("read_exact_one", "Open exact_one_file_6204.txt.", _READ_TOOLS, _EXACT_ONE_EVIDENCE),
    ("read_exact_one", "read exact_one_file_6204.txt", _READ_TOOLS, _EXACT_ONE_EVIDENCE),
    ("read_exact_one", "tell me exactly what exact_one_file_6204.txt says", _READ_TOOLS, _EXACT_ONE_EVIDENCE),
    # 7 -- the read framed as an exactness demand rather than as a read verb
    ("exact_says", "Tell me exactly what qa_test.txt says.", _READ_TOOLS, _QA_EVIDENCE),
    ("exact_says", "what exactly does qa_test.txt say", _READ_TOOLS, _QA_EVIDENCE),
    # 8 -- search the project for a literal
    ("search_project", f"Search this project for {_SEARCH_NEEDLE}.", _SEARCH_TOOLS, _SEARCH_NEEDLE),
    ("search_project", f"search this project for {_SEARCH_NEEDLE}", _SEARCH_TOOLS, _SEARCH_NEEDLE),
    ("search_project", f"search the project for {_SEARCH_NEEDLE}", _SEARCH_TOOLS, _SEARCH_NEEDLE),
]

#: Search phrasings that do NOT reach `workspace.search_text` on this branch, because the needle is
#: a hyphenated literal and `core/execution/planner.py::_CODE_IDENTIFIER_RE` recognises underscore
#: and camelCase symbols only. They are here for the defect they DID have, which is in scope: the
#: folder-overview lane claimed all three from the bare demonstrative "in this project" and answered
#: a search with a full directory listing, in 0s, needle never searched for.
_SEARCH_PHRASINGS_NOT_ANSWERED_BY_A_LISTING = [
    f"find {_SEARCH_NEEDLE} in this project",
    f"grep this project for {_SEARCH_NEEDLE}",
    f"where is {_SEARCH_NEEDLE} in this project",
]


@pytest.mark.parametrize("text", _SEARCH_PHRASINGS_NOT_ANSWERED_BY_A_LISTING)
def test_a_search_request_is_never_answered_with_a_directory_listing(
    make_agent_module, invocations, arbiter_is_reachable, project, text: str  # noqa: F811
) -> None:
    """A request that names something to look for is not a request to list the container.

    The demonstrative says WHERE to look. Treating it as the whole request produced an inventory
    that never mentions the needle -- a wrong answer delivered with a deterministic lane's
    authority, which is the failure mode the fast-path rule is supposed to remove, not create.
    """
    result = _drive(make_agent_module, project, text, session=_session_for(text))
    response = str(result.get("response") or "")

    assert result.get("route") != "deterministic:folder_overview_fast_path", (
        f"{text!r} was answered by the folder-overview lane: {response[:200]!r}"
    )
    # The needle is in exactly one file; a listing names every file and none of the contents.
    assert "Top level" not in response, f"{text!r} still got an inventory: {response[:200]!r}"


@pytest.mark.parametrize(("family", "text", "expected_tools", "evidence"), _FAMILIES)
def test_a_deterministic_local_operation_enters_no_provider_call(
    make_agent_module, invocations, arbiter_is_reachable, project,  # noqa: F811
    family: str, text: str, expected_tools: set[str], evidence: str,
) -> None:
    """The rule, stated once and applied to every family.

    **Fast-path authority.** When a deterministic local authority can answer a request from the
    files and the repository it is bound to, it answers FIRST. No speculative provider call may be
    entered ahead of it -- not to classify the request, not to choose the lane, not to pick the
    tool. Inference is bought only for what remains after the deterministic authorities have
    declined.

    `arbiter_is_reachable` is not decoration: without it the arbiter fails open at its tag probe and
    a zero-call assertion would pass on a turn where nothing could have been called anyway.
    """
    result = _drive(make_agent_module, project, text, session=_session_for(text))

    assert invocations.count == 0, (
        f"[{family}] {text!r} entered {invocations.count} provider call(s) before answering "
        f"deterministically: {invocations.timeline}"
    )
    assert result["fast_path_hit"] is True, f"[{family}] {text!r} did not reach a deterministic lane"
    assert result["model_calls"] == 0
    if expected_tools:
        assert any(f"tool:{name}" in invocations.timeline for name in expected_tools), (
            f"[{family}] {text!r} -> {invocations.timeline}"
        )
    if evidence:
        assert evidence in str(result["response"]), (
            f"[{family}] {text!r} answered without the bytes: {str(result['response'])[:200]!r}"
        )


# ---------------------------------------------------------------------------------------------
# Negative controls -- the authority above must not reach work the runtime does not own
# ---------------------------------------------------------------------------------------------

_SEMANTIC_CONTROLS = [
    "Summarize this repository architecture.",
    "Audit this file for bugs.",
    "Compare two files semantically.",
]
_LIVE_CONTROLS = [
    "Explain the recent USD move today.",
    "Current weather in Vilnius.",
    "Latest BTC price.",
]


@pytest.mark.parametrize("text", _SEMANTIC_CONTROLS + _LIVE_CONTROLS)
def test_a_negative_control_is_not_answered_by_a_local_operation_lane(
    make_agent_module, invocations, arbiter_is_reachable, project, text: str  # noqa: F811
) -> None:
    """None of these is a local operation, so none may be answered as though it were.

    This is the half of the rule that stops "answer deterministically when you can" from becoming
    "answer deterministically always". A file inventory is not an architecture, a directory listing
    is not a bug audit, and neither is a currency move.

    Asserted on the TOOL, not on `fast_path_hit`: three of these six legitimately reach a
    deterministic lane and are right to. The live-info lane claims the three live/current controls
    and declines them honestly because web lookup is off in this runtime -- that is a correct
    deterministic answer about the runtime's own reach, not a local operation standing in for one.
    """
    result = _drive(make_agent_module, project, text, session=_session_for(text))

    reached = [step for step in invocations.timeline if step.startswith("tool:")]
    claimed = [step for step in reached if step.removeprefix("tool:") in _LOCAL_OPERATION_TOOLS]
    assert not claimed, (
        f"{text!r} was answered by a deterministic local-operation lane ({claimed}); it is not a "
        f"local operation. Full timeline: {invocations.timeline}"
    )
    assert str(result.get("response") or "").strip(), f"{text!r} produced no answer at all"


@pytest.mark.parametrize("text", _SEMANTIC_CONTROLS)
def test_a_semantic_control_still_reaches_the_model_lane(
    make_agent_module, invocations, arbiter_is_reachable, project, text: str  # noqa: F811
) -> None:
    """Semantic work still goes to a model. The fix removes speculation, not interpretation.

    `Summarize this repository architecture.` is here for a reason it did not used to satisfy: on
    the untouched base the folder-overview lane claimed it and answered, in 0s and with no model at
    all, with a directory listing. `_FOLDER_OVERVIEW_RE` stops at the project noun and ignores what
    follows it, so "summarize this repository" matched and "architecture" was discarded. That is a
    confident wrong answer, which is worse than the confabulation the lane exists to prevent.
    """
    result = _drive(make_agent_module, project, text, session=_session_for(text))

    assert result.get("fast_path_hit") is not True or invocations.count >= 1, (
        f"{text!r} was answered deterministically with no model consulted: "
        f"route={result.get('route')!r} response={str(result.get('response'))[:160]!r}"
    )


# ---------------------------------------------------------------------------------------------
# Ordinary tiny chat -- the additional live defect, at the decisions that actually buy weight
# ---------------------------------------------------------------------------------------------


def _route(text: str) -> dict[str, Any]:
    """The real routing decisions, taken through the real functions rather than a mirror of them.

    The same three mechanisms `tests/test_v050_trivial_tasks_stay_trivial.py` uses, because each is
    a real mechanism and not a proxy: `research` means `provider_role=queen` and
    `allow_paid_fallback=True`; the `deep` lane is the heavyweight model tier; and a fallback budget
    of **None** is an UNBOUNDED sequential fallback loop.
    """
    from core.local_inference_autopilot import _resolve_lane
    from core.memory_first_router import resolve_fallback_budget_seconds
    from core.reasoning_engine import explicit_planner_style_requested
    from core.task_router import classify, model_execution_profile

    classification = classify(text, {"chat_surface": True})
    profile = model_execution_profile(
        classification["task_class"],
        chat_surface=True,
        planner_style_requested=explicit_planner_style_requested(text),
    )
    lane = _resolve_lane(
        user_text=text,
        task_kind=str(profile["task_kind"]),
        output_mode=str(profile["output_mode"]),
        source_context={},
        local_available=True,
        has_tiny_lane=True,
        has_deep_lane=True,
    )
    return {
        "task_class": classification["task_class"],
        "provider_role": profile["provider_role"],
        "allow_paid_fallback": profile["allow_paid_fallback"],
        "lane": lane,
        "budget": resolve_fallback_budget_seconds(
            lane,
            forced_cpu=False,
            no_usable_gpu=False,
            output_mode=str(profile["output_mode"]),
        ),
    }


#: A definition is the lightest semantic turn there is. Deliberately spread across domain
#: vocabularies -- currency, config formats, networking, containers -- because the defect was that
#: the verdict depended on which vocabulary the noun belonged to, not on the shape of the question.
_TINY_CHAT = [
    "ok what is usd?",          # the measured request, verbatim
    "what is usd?",
    "what is usd",
    "hey what is json?",        # was `config` -> action_plan -> DEEP lane, UNBOUNDED budget
    "ok what is yaml?",
    "what is a vpn?",
    "ok what is a vpn?",
    "what's SSH?",
    "whats an LLM",
    "what is docker",
    "what are cookies?",
    "what does API stand for?",
    "what does TCP mean?",
]


@pytest.mark.parametrize("text", _TINY_CHAT)
def test_a_bare_definition_does_not_buy_the_heavy_lane(text: str) -> None:
    decision = _route(text)
    assert decision["lane"] != "deep", f"{text!r} -> {decision}"
    assert decision["budget"] is not None, f"{text!r} kept an UNBOUNDED fallback budget: {decision}"
    assert decision["provider_role"] != "queen", f"{text!r} -> {decision}"
    assert decision["allow_paid_fallback"] is False, f"{text!r} bought a paid arm: {decision}"


#: The controls for the rule above, and the half that was failing in the opposite direction. Each
#: of these must KEEP the heavy lane: a demotion that also caught them would have swapped one wrong
#: verdict for another.
_LIVE_LOOKUPS = [
    "Explain the recent USD move today.",
    "Current weather in Vilnius.",
    "Latest BTC price.",
    "what is the latest BTC price right now",
    "what is the BTC price today",
    "what is the weather in Vilnius right now",
    "look up what is foocorp",
    "who is the ceo of foocorp",
]


@pytest.mark.parametrize("text", _LIVE_LOOKUPS)
def test_a_live_lookup_keeps_the_research_lane(text: str) -> None:
    """A fact that is only true right now needs the lane that can go and get it.

    All three of the brief's live/current controls read `unknown` on the untouched base, because
    `looks_like_live_recency_lookup` tested space-padded markers (`" latest "`, `" current "`,
    `" today "`) against text that was neither padded nor stripped of its sentence punctuation -- so
    a marker at the start or end of the sentence never matched, and the verdict depended on where
    in the sentence the user happened to put the word.
    """
    decision = _route(text)
    assert decision["task_class"] == "research", f"{text!r} -> {decision}"
    assert decision["provider_role"] == "queen", f"{text!r} -> {decision}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The frame, at both ends of the sentence and behind an opener.
        ("what is usd?", True),
        ("ok what is usd?", True),
        ("what does TCP mean?", True),
        # A live VALUE noun under the same frame is a lookup, not a definition.
        ("what is the btc price", False),
        ("what is the weather", False),
        ("what is the exchange rate", False),
        # A second clause means the frame is only the sentence's opening.
        ("what is usd and then convert 40 eur", False),
        # A deictic points at this conversation, not at a term -- anywhere in the phrase, not only
        # in first position.
        ("what is that", False),
        ("what is this project", False),
        # A preposition makes the frame relational: a diagnosis, or a read.
        ("what is wrong with this config", False),
        ("what is the difference between a vpn and a proxy", False),
        ("what is json used for", False),
        # A named path or workspace noun keeps the lane it already had.
        ("what is in package.json", False),
        ("what is /tmp/session.log", False),
        # An explicit lookup verb or a public-entity shape is a lookup.
        ("look up what is foocorp", False),
        ("who is the ceo of foocorp", False),
        # A genitive is an ATTRIBUTE of an ENTITY -- a fact to look up, not a word to define.
        ("what is the capital of France", False),
        ("what is the population of Vilnius", False),
        # Too long to be a bare term.
        ("what is the best way to structure a python monorepo for teams", False),
    ],
)
def test_the_definitional_frame_claims_only_a_bare_term(text: str, expected: bool) -> None:
    """The predicate itself, at its boundaries -- so a passing family above is not a coincidence."""
    from core.task_router import looks_like_bare_definitional_question

    assert looks_like_bare_definitional_question(text) is expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Start of sentence, end of sentence, and welded to a full stop: the three positions the
        # space-padded markers could not see.
        ("Latest BTC price.", True),
        ("Current weather in Vilnius.", True),
        ("Explain the recent USD move today.", True),
        ("latest btc price", True),
        # ...and the mid-sentence position that always worked, unchanged.
        ("what is the latest BTC price right now", True),
        # Recency without a live domain, and a live domain without recency, are both still False:
        # the predicate is an AND and normalization must not have turned it into an OR.
        ("what happened", False),
        ("what is the latest release of this library", False),
        ("btc", False),
        ("weather", False),
        # A place name is not a currency or a market. The currency markers are whole words for
        # exactly this reason: " eur" would fire on "europe", " stock" on "stockholm", and a turn
        # would buy the paid lane on the strength of three letters inside a city.
        # "what happened" is a live domain of its own, so this is live on that ground, not on
        # "europe" -- the control for the currency guard is the next line, where nothing else fires.
        ("what happened in europe today", True),
        ("the current european history syllabus", False),
        ("current news in stockholm", True),   # `news` is a live domain in its own right
        # ...and the currency question itself still reads live, at either end of the sentence.
        ("USD today", True),
        ("today the usd moved", True),
    ],
)
def test_live_recency_detection_does_not_depend_on_sentence_position(text: str, expected: bool) -> None:
    from core.task_router import looks_like_live_recency_lookup

    assert looks_like_live_recency_lookup(text) is expected, text


# =============================================================================================
# TASK B -- the accounting rule, as a matrix
# =============================================================================================
#
# THE RULE. `turn.trace_completed.model_calls` counts **provider invocation attempts ENTERED while
# executing one turn**, one increment per entry, at the two seams that enter one: an adapter task
# method through `MemoryFirstRouter._invoke_manifest`, and a direct provider HTTP post that bypasses
# the adapter (the intent arbiter). Retries and failures each count once -- the input was spent and
# the wall clock was paid. Calls the runtime DECLINED to make (open circuit, failed health probe, no
# manifest resolved) count zero, because no provider call was entered. `core/turn_model_call_ledger.py`
# states this in full; these tests are that statement executed.


def _register_probe_manifest(registry: Any, provider_name: str) -> Any:
    manifest = registry.register_manifest(
        {
            "provider_name": provider_name,
            "model_name": provider_name,
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize"],
            "runtime_config": {"base_url": "http://127.0.0.1:1"},
            "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    # `core.final_answer_authorship` refuses an uncertified LOOPBACK model before its adapter is
    # built, so without this the probe below counts zero calls and proves nothing about the
    # ledger. Certifying it is what an operator does; it does not bypass the authority.
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    return manifest


def _delete_probe_manifests(names: tuple[str, ...]) -> None:
    """Remove only what this test registered. A stray enabled provider is exactly the cross-test
    coupling that shows up as one shard failing while the same file passes alone."""
    from storage.db import get_connection

    connection = get_connection()
    try:
        for name in names:
            connection.execute(
                "DELETE FROM model_provider_manifests WHERE provider_name = ?", (name,)
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("outcomes", "expected_calls"),
    [
        # One call that succeeded.
        (("ok",), 1),
        # A call that failed, then a fallback that succeeded. TWO providers were entered and two
        # lots of wall clock were paid; a count that reported the successful one would describe a
        # turn that took twice as long as it claims.
        (("raise", "ok"), 2),
        # Two failures. Same rule, no successful call to hide behind.
        (("raise", "raise"), 2),
        # A retry after a failure is a third entry, not a re-labelling of the first.
        (("raise", "raise", "ok"), 3),
    ],
)
def test_the_ledger_counts_every_provider_call_entered(
    outcomes: tuple[str, ...], expected_calls: int
) -> None:
    """Driven through `MemoryFirstRouter._invoke_manifest` itself, once per attempt.

    Not through a hand-rolled recorder: that would prove the ledger can count while leaving the
    line that calls it -- the one a refactor would drop -- covered by nothing. A real manifest is
    registered and the ADAPTER is stubbed, because a provider that raises is still a provider that
    was called.
    """
    from adapters.base_adapter import ModelRequest, ModelResponse
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_model_calls
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    reset_for_tests()

    name = f"ledger-probe-{uuid.uuid4().hex[:8]}"
    registry = ModelRegistry()
    manifest = _register_probe_manifest(registry, name)
    router = MemoryFirstRouter(registry)
    remaining = list(outcomes)

    class _Adapter:
        def run_text_task(self, request: Any) -> Any:
            if remaining.pop(0) == "raise":
                raise RuntimeError("provider unreachable")
            return ModelResponse(output_text="answered", raw_response={})

        def supports_streaming(self) -> bool:
            return False

    context: dict[str, Any] = {"request_id": f"router-seam-{name}"}
    begin_turn(context)
    task = create_task_record("count this call")
    request = ModelRequest(task_kind="summarize", prompt="count this call")

    try:
        with mock.patch.object(registry, "build_adapter", return_value=_Adapter()), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ):
            for _ in outcomes:
                router._invoke_manifest(
                    manifest=manifest,
                    request=request,
                    output_mode="plain_text",
                    task=task,
                    source_context=context,
                )
    finally:
        _delete_probe_manifests((name,))

    assert turn_model_calls(context) == expected_calls, (
        f"{outcomes} entered {expected_calls} provider call(s) and the ledger counted "
        f"{turn_model_calls(context)}"
    )


def test_a_call_the_runtime_declined_to_make_is_not_counted() -> None:
    """The other side of the rule: `model_calls` is entries, not intentions.

    A health probe that fails means no provider call was entered, so there is nothing to count.
    Without this the rule would drift into "attempts considered", and a turn that spent no wall
    clock at all would report calls it never made.
    """
    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from core.model_registry import ModelRegistry
    from core.task_router import create_task_record
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_model_calls
    from storage.migrations import run_migrations

    run_migrations()
    reset_provider_health()
    reset_for_tests()

    name = f"declined-probe-{uuid.uuid4().hex[:8]}"
    registry = ModelRegistry()
    manifest = _register_probe_manifest(registry, name)
    router = MemoryFirstRouter(registry)
    entered: list[str] = []

    class _Adapter:
        def run_text_task(self, request: Any) -> Any:
            entered.append("run_text_task")
            raise AssertionError("the runtime declined this call; it must not be entered")

        def health_check(self) -> dict[str, Any]:
            # The gate the runtime actually consults before entering a task method.
            return {"ok": False, "error": "probe_failed"}

        def supports_streaming(self) -> bool:
            return False

    context: dict[str, Any] = {"request_id": f"declined-{name}"}
    begin_turn(context)

    try:
        with mock.patch.object(registry, "build_adapter", return_value=_Adapter()), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=True
        ):
            router._invoke_manifest(
                manifest=manifest,
                request=ModelRequest(task_kind="summarize", prompt="do not call"),
                output_mode="plain_text",
                task=create_task_record("do not call"),
                source_context=context,
            )
    finally:
        _delete_probe_manifests((name,))

    assert entered == [], "the health probe did not actually stop the call, so this proves nothing"
    assert turn_model_calls(context) == 0


def test_a_deterministic_turn_that_bought_no_inference_reports_zero() -> None:
    """The 0 row of the matrix, taken from the real deterministic result builder."""
    from core.agent_runtime.fast_command_surface import _fast_path_route_metadata
    from core.turn_model_call_ledger import begin_turn, reset_for_tests

    reset_for_tests()
    context: dict[str, Any] = {"request_id": "zero-call-turn"}
    begin_turn(context)
    metadata = _fast_path_route_metadata("workspace_runtime_fast_path", source_context=context)
    assert metadata["model_calls"] == 0


def test_a_deterministic_turn_that_bought_inference_reports_what_it_bought() -> None:
    """The row the live defect sat in: calls made UPSTREAM of a deterministic answer.

    Two provider calls, then a 0.0008s file read, reported as `model_calls: 0` and
    `fallback_reason: model_not_used`. The count is now the calls entered, and the reason
    distinguishes "no model ran" from "no model ran FOR THE ANSWER" instead of claiming the first
    when only the second is true.
    """
    from core.agent_runtime.fast_command_surface import _fast_path_route_metadata
    from core.turn_model_call_ledger import begin_turn, record_provider_call, reset_for_tests

    reset_for_tests()
    context: dict[str, Any] = {"request_id": "two-preflight-turn"}
    begin_turn(context)
    record_provider_call(context)
    record_provider_call(context)
    assert _fast_path_route_metadata("workspace_runtime_fast_path", source_context=context)[
        "model_calls"
    ] == 2


# ---------------------------------------------------------------------------------------------
# Provider token accounting -- every call is in the denominator, or is named as unmetered
# ---------------------------------------------------------------------------------------------


def test_token_totals_name_the_calls_they_could_not_measure() -> None:
    """A total over a subset of the turn's calls must not be presented as the turn's total.

    `usages` holds one entry per call that reached the usage-recording seam. The intent arbiter
    posts straight to the provider and never reaches it, so its call contributed no entry at all --
    it was not a call missing its usage block, it was absent from the denominator. A turn that spent
    an arbitration call and one answering call therefore reported `1 model call, from provider
    receipts for every call` while two were made.
    """
    from core.token_usage_receipt import aggregate_call_usage, usage_receipt_line

    metered = [{"prompt_tokens": 1200, "completion_tokens": 80}]

    # Without the ledger, the aggregate can only describe what it can see, and says so completely.
    blind = aggregate_call_usage(metered)
    assert blind["calls"] == 1 and blind["complete"] is True

    # With it, the call it cannot see is counted and named rather than dropped.
    reconciled = aggregate_call_usage(metered, provider_calls=2)
    assert reconciled["calls"] == 2
    assert reconciled["calls_metered"] == 1
    assert reconciled["calls_unmetered"] == 1
    assert reconciled["complete"] is False
    assert reconciled["lower_bound"] is True
    # The measured figures are unchanged -- unmetered means uncounted, not estimated.
    assert reconciled["input_tokens"] == 1200
    assert reconciled["output_tokens"] == 80

    sentence = usage_receipt_line(reconciled)
    assert "LOWER BOUND" in sentence
    assert "unmetered" in sentence
    assert "2 call(s)" in sentence


def test_a_missing_usage_block_and_an_unmetered_call_are_named_separately() -> None:
    """Two different reasons a total is short. Collapsing them tells the operator the provider was
    silent when in fact the runtime never asked it."""
    from core.token_usage_receipt import aggregate_call_usage, usage_receipt_line

    totals = aggregate_call_usage(
        [{"prompt_tokens": 900, "completion_tokens": 40}, {}], provider_calls=3
    )
    assert totals["calls"] == 3
    assert totals["calls_missing_usage"] == 1
    assert totals["calls_unmetered"] == 1
    sentence = usage_receipt_line(totals)
    assert "reported no usage" in sentence
    assert "unmetered" in sentence


def test_a_ledger_that_counted_fewer_calls_never_invents_a_negative_surplus() -> None:
    """If the ledger is BEHIND the usage list, that is a ledger gap; hiding it would be the same
    class of defect as the one this whole branch removes."""
    from core.token_usage_receipt import aggregate_call_usage

    totals = aggregate_call_usage(
        [{"prompt_tokens": 10, "completion_tokens": 1}, {"prompt_tokens": 20, "completion_tokens": 2}],
        provider_calls=1,
    )
    assert totals["calls_unmetered"] == 0
    assert totals["calls"] == 2
    assert totals["complete"] is True
