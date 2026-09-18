"""A QA drive on 2026-07-30 asked the live daemon for named source files. It said it could not.

Four clear build instructions, four outcomes. Two of them -- the two below, verbatim -- answered:

    "No. I do not have a real bounded builder path for that request on this runtime."

with nothing written. The answer was false: a third phrasing in the same drive went down the
model-driven build lane and produced three correct, parsing files with passing tests. What differed
was scope, not capability. See core/agent_runtime/builder/named_file_build.py.

The third phrasing failed differently and is pinned here too: asked for a script "in
<workspace>/qa3", it built in `<workspace>/reverse.py/` -- "called reverse.py" was read as a FOLDER
name, and the rooted directory that was actually named got dropped for being rooted. The reply then
named `reverse.py` as the destination, which is the silent relocation this project keeps removing.

The restraint side is load-bearing and tested here in the same breath: these gates must not turn a
question about building into a build.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime import build_request_intent, fast_paths_builder
from core.agent_runtime.builder import named_file_build
from core.agent_runtime.builder.support import controller_profile

WS = "/Users/example-user/.vool_runtime/workspace"

# The two verbatim prompts that were refused.
QA1 = f"make a file at {WS}/qa1/fizz.py with a fizzbuzz function, and a test_fizz.py next to it. go."
QA2 = (
    f"Could you please create {WS}/qa2/greet.py containing a function greet(name) that returns a "
    "greeting string? Thanks!"
)
# The verbatim prompt that built in the wrong folder.
QA3 = f"build me a small python script in {WS}/qa3 called reverse.py that reverses a string"


# ---------------------------------------------------------------------------
# 1. The refusal that was false
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", [QA1, QA2, QA3])
def test_a_named_file_build_is_recognised_as_a_build(prompt: str) -> None:
    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "create qa5/slug.py with a slugify function",
        "write parser.py that parses an ISO date",
        "go ahead and generate qa7/stack.py implementing a Stack class",
        "pls make qa8/temp.py that converts celsius to fahrenheit, and a test for it",
        "add a retry.py with an exponential backoff helper",
        "build tokenizer.py that splits a sentence into words",
    ],
)
def test_a_bare_filename_is_a_build_noun(prompt: str) -> None:
    """The shared gate wants a word like "file" or "script". A filename is more concrete than both,
    and requiring the word as well is what made "create qa5/slug.py ..." not a build at all."""
    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is True


@pytest.mark.parametrize(
    "prompt",
    [
        f"i need {WS}/g5/wordcount.py that counts words in a block of text",
        f"knock up {WS}/g7/initials.py that turns a full name into initials, ta",
        f"whip up {WS}/x/slug.py that slugifies a title",
        "i want retry.py with an exponential backoff helper",
        "throw together cache.py with a simple LRU",
    ],
)
def test_a_build_asked_for_without_an_imperative_verb(prompt: str) -> None:
    """Both of these came back "I couldn't map that cleanly to a real action." in the EIGHT-FRESH-
    PHRASING retest, which is the whole reason fresh phrasings are the rule: the first fixture set
    all happened to use imperative verbs. "i need X" is a statement and "knock up" is in nobody's
    verb list, yet both name a file and ask for it to exist."""

    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "i need to know what greet.py does",
        "i want to understand fizz.py",
        "do i need a setup.py?",
        "should i want a conftest.py in here?",
        "i need to read parser.py first",
        "would you say i need a requirements.txt?",
        "i need to decide between calc.py and math_utils.py",
    ],
)
def test_the_need_route_is_not_a_way_around_the_restraint(prompt: str) -> None:
    """"i need X" is a request for X; "i need to KNOW about X" is a question. The mental verb after
    need/want is the difference, and a question stays a question however it opens."""

    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is False


@pytest.mark.parametrize(
    "prompt",
    [
        "lets discuss whether i need a config.yaml",
        "lets talk about whether i want a conftest.py",
        "i need retry.py but do not write any files",
        "i want cache.py, just plan it, no files",
    ],
)
def test_the_second_route_honours_deliberation_and_opt_out_too(prompt: str) -> None:
    """The colloquial/need route is not a way around the restraint the imperative route honours.

    These four are the ones that PROVE the shared gate is load-bearing here: each reaches
    `_asks_in_another_way` (no question mark, no interrogative opening), so only the explicit
    deliberation/opt-out check stops them. Written because a sabotage that removed that check left
    every other case in this file green.
    """
    assert named_file_build._asks_in_another_way(prompt) is True
    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is False


def test_the_named_files_are_read_out_of_the_request() -> None:
    assert named_file_build.named_build_files(QA1, workspace_root=WS) == ["qa1/fizz.py", "test_fizz.py"]
    assert named_file_build.named_build_files(QA2, workspace_root=WS) == ["qa2/greet.py"]


# ---------------------------------------------------------------------------
# 2. The destination the user actually named
# ---------------------------------------------------------------------------


def test_the_requested_folder_wins_over_the_default() -> None:
    assert named_file_build.named_file_build_root(QA1, workspace_root=WS) == "qa1"
    assert named_file_build.named_file_build_root(QA2, workspace_root=WS) == "qa2"


def test_a_workspace_rooted_folder_is_rebased_not_dropped() -> None:
    """`<workspace>/qa3` is reachable, so the honest answer is `qa3` -- not "" and a build somewhere
    else. Compare test_the_builder_root_is_not_a_slash_stripped_rewrite, which pins the OUTSIDE case."""
    assert fast_paths_builder.extract_requested_builder_root(QA3, workspace_root=WS) == "qa3"


def test_a_filename_is_never_taken_as_the_build_folder() -> None:
    """"called reverse.py" built `reverse.py/reverse.py`."""
    assert named_file_build.is_source_filename("reverse.py") is True
    assert named_file_build.is_source_filename("sandbox/discord-bot") is False
    assert fast_paths_builder.extract_requested_builder_root("create a folder called reverse.py") == ""


def test_a_path_outside_the_workspace_is_still_dropped() -> None:
    """`write_root_honesty` owns refusing these by name; this lane must not quietly rebase them."""
    assert named_file_build.workspace_relative_path("/tmp/vool_qa_build5/x.py", workspace_root=WS) == ""
    assert named_file_build.named_build_files("create /tmp/elsewhere/x.py with a helper", workspace_root=WS) == []
    assert fast_paths_builder.extract_requested_builder_root(
        "scaffold a rust crate in /tmp/vool_qa_build5", workspace_root=WS
    ) == ""


# ---------------------------------------------------------------------------
# 3. The wiring -- the predicate is useless if the profile never asks it
# ---------------------------------------------------------------------------


def _profile(prompt: str) -> dict:
    """Run the real ``controller_profile`` with every OTHER branch deliberately shut off.

    The workflow probe returns nothing and the bootstrap/explicit-file detectors say no, which is
    exactly the state the live daemon was in for QA1 and QA2. So the only thing that can keep this
    off the capability-gap report is the named-file branch itself.
    """
    gap = {"support_level": "unsupported", "reason": "I do not have a real bounded builder path"}
    agent = SimpleNamespace(
        _should_run_builder_controller=lambda **_kw: True,
        _workspace_build_target=lambda **_kw: {
            "platform": "generic",
            "language": "python",
            "root_dir": "generated/workspace-starter",
        },
        _supports_bounded_builder_workflow_request=lambda **_kw: False,
        _looks_like_explicit_workspace_file_request=lambda _t: False,
        _looks_like_generic_workspace_bootstrap_request=lambda _t: False,
        _builder_support_gap_report=lambda **_kw: gap,
    )
    return controller_profile(
        agent,
        effective_input=prompt,
        classification={"task_class": "unknown"},
        interpretation=SimpleNamespace(topic_hints=[]),
        source_context={"workspace": WS, "workspace_root": WS},
        plan_tool_workflow_fn=lambda **_kw: SimpleNamespace(handled=False, next_payload=None),
        looks_like_workspace_bootstrap_request_fn=lambda _t: False,
    )


@pytest.mark.parametrize("prompt", [QA1, QA2])
def test_the_profile_routes_a_named_file_build_instead_of_reporting_a_gap(prompt: str) -> None:
    profile = _profile(prompt)

    assert profile["supported"] is True, "answered the capability gap for a build it can do"
    assert profile["mode"] == "model_build"
    assert "gap_report" not in profile


def test_the_profile_builds_in_the_folder_the_user_named() -> None:
    """`generated/workspace-starter` is the default. Using it while the user named `qa1` is the
    relocation, and the reply would name a folder they never asked for."""
    assert _profile(QA1)["target"]["root_dir"] == "qa1"
    assert _profile(QA2)["target"]["root_dir"] == "qa2"


def test_the_profile_still_reports_a_gap_for_a_request_with_no_named_file() -> None:
    """A request that names no file and no artifact destination is not claimed as a build.

    Updated against the accepted exact-scaffold-scope repair (9d745667): such a request now
    DECLINES the builder (`artifact_without_destination`, handing the turn to the ordinary
    model lane) rather than reporting the builder's capability gap -- which claimed the
    request for a lane that cannot perform it. Verified pre-existing against a pristine
    HEAD archive before this update; the old assertions rotted with that repair."""
    profile = _profile("do something clever with my infrastructure")

    assert profile["should_handle"] is False
    assert profile["declined"] == "artifact_without_destination"


# ---------------------------------------------------------------------------
# 4. The lane the routing fix now feeds must accept what it is handed
# ---------------------------------------------------------------------------


def test_the_model_may_echo_the_absolute_path_the_request_gave_it() -> None:
    """Routing the request here was only half of it.

    A live retest of the fix reached the build lane and answered "I couldn't build that in the
    workspace: the model did not return a usable file list." The request named its destination in
    full, so the model repeated that absolute path in its file list -- the obvious thing to do --
    and every entry was discarded as unsafe, leaving nothing to build.
    """
    from core.agent_runtime.builder.app_builder import _confine_relative_path

    echoed = f"{WS}/f1/palindrome.py"
    assert _confine_relative_path(echoed, target_dir="f1", workspace_root=WS) == "f1/palindrome.py"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"{WS}/f1/test_palindrome.py", "f1/test_palindrome.py"),
        ("f1/palindrome.py", "f1/palindrome.py"),
        ("palindrome.py", "f1/palindrome.py"),
        # Flattened: the project is FLAT by contract, so a sub-directory is dropped rather than
        # honoured -- see test_a_nested_path_is_flattened_because_the_project_is_flat.
        (f"{WS}/other/x.py", "f1/x.py"),
    ],
)
def test_every_shape_the_model_returns_lands_in_the_target(raw: str, expected: str) -> None:
    from core.agent_runtime.builder.app_builder import _confine_relative_path

    assert _confine_relative_path(raw, target_dir="f1", workspace_root=WS) == expected


def test_a_nested_path_is_flattened_because_the_project_is_flat() -> None:
    """A live build of "build <ws>/g8/clamp.py with a clamp(value, low, high) function" put the file
    at `g8/build/clamp.py` while its own generated README described it at the top level. The lane's
    contract says the project is FLAT and tells the test module to import by bare filename, so a
    nested source file cannot be imported by the tests written for it."""
    from core.agent_runtime.builder.app_builder import _confine_relative_path

    assert _confine_relative_path("build/clamp.py", target_dir="g8", workspace_root=WS) == "g8/clamp.py"
    assert _confine_relative_path("src/lib/util.py", target_dir="g8", workspace_root=WS) == "g8/util.py"


def test_flattening_happens_after_the_escape_check_not_before() -> None:
    """Taking the last segment first would turn `../secret` into `<target>/secret` -- discarding the
    very `..` that makes it an escape. Caught by running the existing confinement tests."""
    from core.agent_runtime.builder.app_builder import _confine_relative_path

    assert _confine_relative_path("../secret", target_dir="g8", workspace_root=WS) is None
    assert _confine_relative_path("a/../../b", target_dir="g8", workspace_root=WS) is None


@pytest.mark.parametrize(
    "raw", ["/etc/passwd", "/tmp/elsewhere/x.py", "../escape.py", "C:/win/x.py", "/Users/other/x.py"]
)
def test_confinement_still_refuses_everything_outside_the_workspace(raw: str) -> None:
    """Rebasing INSIDE the workspace must not become rebasing anything. Confinement is the point."""
    from core.agent_runtime.builder.app_builder import _confine_relative_path

    assert _confine_relative_path(raw, target_dir="f1", workspace_root=WS) is None
    # With no workspace known, an absolute path has nothing to be relative TO.
    assert _confine_relative_path(raw, target_dir="f1", workspace_root="") is None


# ---------------------------------------------------------------------------
# 5. "Ran 0 tests ... OK" is not a pass
# ---------------------------------------------------------------------------

_EMPTY_RUN = "Ran 0 tests in 0.000s\n\nOK"
_REAL_PASS = "test_a (t.T) ... ok\n\nRan 7 tests in 0.001s\n\nOK"
_REAL_FAIL = "Ran 3 tests in 0.002s\n\nFAILED (failures=1)"


def test_an_empty_test_run_is_not_reported_as_passing() -> None:
    """Live build, verbatim reply: "Built 2 file(s) in `g2` ... Tests passed. (Ran 0 tests in
    0.000s | OK)". Nothing was verified, and the sentence saying otherwise is the one thing this
    lane must never produce. `unittest discover` prints OK for an empty suite."""
    from core.agent_runtime.builder.app_builder import _tests_passed_from_output, tests_actually_ran

    assert tests_actually_ran(_EMPTY_RUN) is False
    assert _tests_passed_from_output(_EMPTY_RUN) is False
    assert _tests_passed_from_output(_REAL_PASS) is True
    assert _tests_passed_from_output(_REAL_FAIL) is False


def test_the_test_command_comes_from_the_files_that_exist() -> None:
    """The deeper cause: the command was picked off the PLANNED list. That build listed
    `test_titlecase.py`, never generated its contents, and ran discover anyway."""
    from core.agent_runtime.builder.app_builder import _pick_test_command

    assert _pick_test_command(["g2/titlecase.py", "g2/README.md"]) is None
    assert _pick_test_command(["g1/vowels.py", "g1/test_vowels.py"]) is not None


def test_a_planned_test_file_that_was_never_written_does_not_get_run() -> None:
    """The whole build, not the helper.

    Asserting on `_pick_test_command` alone left the CALL SITE untested: switching it back to the
    planned list kept every other case green. This drives `build_app_from_spec` with a model that
    LISTS a test module and then returns nothing for it -- exactly what the live build did -- and
    asserts no test command was ever run and nothing claims a pass.
    """
    import re as _re

    from core.agent_runtime.builder import app_builder

    def generate(prompt: str) -> str:
        if prompt.startswith("List the files"):
            return '["titlecase.py", "test_titlecase.py", "README.md"]'
        # Key on the file this prompt is FOR. Every per-file prompt also lists the whole project,
        # so a bare "test_titlecase in prompt" matches all of them -- which silently made a first
        # version of this test return "" for every file and build nothing at all.
        target = _re.search(r"exactly one file for this project: `([^`]+)`", prompt)
        if target and target.group(1) == "test_titlecase.py":
            return ""  # the model produced no contents for the test module
        return "def titlecase(s):\n    return s.title()\n"

    commands: list[str] = []

    def run_tool(intent: str, arguments: dict, **_kw):
        if intent == "sandbox.run_command":
            commands.append(str(arguments.get("command") or ""))
            return app_builder.ToolOutcome(ok=True, response_text=_EMPTY_RUN)
        return app_builder.ToolOutcome(ok=True, response_text="ok")

    report = app_builder.build_app_from_spec(
        request="please write g2/titlecase.py that title-cases a sentence",
        target_rel="g2",
        source_context={"workspace": WS, "workspace_root": WS},
        generate_fn=generate,
        run_tool_fn=run_tool,
    )

    assert "g2/test_titlecase.py" not in report.files_written
    assert commands == [], "ran a test command for a test file that was never written"
    assert report.tests_ran is False
    assert "Tests passed" not in app_builder.render_app_build_response(report)


def test_the_reply_says_nothing_was_verified_rather_than_passed_or_failed() -> None:
    from core.agent_runtime.builder.app_builder import AppBuildReport, render_app_build_response

    reply = render_app_build_response(
        AppBuildReport(
            handled=True,
            target_dir="g2",
            files_written=["g2/titlecase.py", "g2/README.md"],
            tests_ran=True,
            tests_passed=False,
            test_output=_EMPTY_RUN,
        )
    )

    assert "Tests passed" not in reply
    assert "no tests" in reply and "nothing was verified" in reply
    # Nor the other lie: this was not a failing suite.
    assert "still failing" not in reply


def test_a_genuine_pass_is_still_reported_as_a_pass() -> None:
    """The guard must not turn every build into an unverified one."""
    from core.agent_runtime.builder.app_builder import AppBuildReport, render_app_build_response

    reply = render_app_build_response(
        AppBuildReport(
            handled=True,
            target_dir="g1",
            files_written=["g1/vowels.py", "g1/test_vowels.py"],
            tests_ran=True,
            tests_passed=True,
            test_output=_REAL_PASS,
        )
    )

    # The build reply is now a runtime-composed receipt rather than prose, so a pass reads
    # `| Outcome | ✅ passed |`. What this test guards is unchanged: a genuine pass must not be
    # downgraded to "nothing was verified" by the empty-suite guard.
    assert "✅ passed" in reply
    assert "nothing was verified" not in reply
    assert "still failing" not in reply


def test_a_runner_that_prints_no_count_is_judged_on_its_verdict() -> None:
    """`npm test` has no "Ran N tests" line; the count guard must not fail it closed."""
    from core.agent_runtime.builder.app_builder import tests_actually_ran

    assert tests_actually_ran("All suites green\nOK") is True


# ---------------------------------------------------------------------------
# 6. The restraint that must not move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "should we write a fizzbuzz in fizz.py or just inline it?",
        "lets discuss whether we should build an api",
        "what would go into a greet.py if you were writing one?",
        "just plan out calc.py, do not write any files",
        "explain how a setup.py works",
        "tell me about the tool you would generate",
        "whats the difference between requirements.txt and pyproject.toml?",
        "i am thinking about creating a config.yaml eventually",
        "why would someone put a conftest.py at the repo root?",
        "compare writing tests in test_x.py versus doctests",
        "do not write anything, just tell me what main.py should contain",
        "is it a good idea to create a Makefile for this?",
        "which is better, a setup.py or a pyproject.toml?",
        "how does a fizz.py test usually get structured?",
    ],
)
def test_a_question_about_a_file_is_not_an_instruction_to_write_it(prompt: str) -> None:
    assert named_file_build.looks_like_named_file_build_request(prompt, workspace_root=WS) is False


def test_the_shared_gate_still_decides_what_an_instruction_is() -> None:
    """This lane supplies its own NOUN and borrows the instruction test whole. If the shared gate
    stops seeing an imperative, this lane must stop seeing a build."""
    assert build_request_intent.imperative_build_sentences("create qa5/slug.py with a slugify function")
    assert build_request_intent.imperative_build_sentences("should we create slug.py?") == []
    assert build_request_intent.imperative_build_sentences("create slug.py but do not write files") == []
