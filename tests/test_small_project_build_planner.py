"""A small project whose file set the operator wrote out gets exactly that file set.

The live QA drive, verbatim::

    Create a small Python project called StormWatch with a README and one app.py file.
    Do not run tests.

and what came back: a reply table naming ``StormWatch``, no ``README.md``, no ``app.py``, a planned
``test_app.py`` the request had ruled out, no approval batch, and "I will keep this turn
conversational and will not propose or run an action." three times over.

Three readings of one sentence, all wrong, each in a different module:

1. ``intent_claims.action_policy_for_text`` read "Do not run tests" as a turn-wide ban. Every
   execution seam in the turn shut down -- including the writes the same sentence asked for. The
   three repeats are three tool intents each hitting ``blocked_by_action_policy``.
2. ``mutation_scope`` dropped its seal because the request said "project" (a multi-file noun) and
   "with a README" (a file it did not name), so a model was asked to "list the files for this
   project" -- a prompt that instructs it to include a test module -- and ``test_app.py`` came back.
3. Nothing read "one app.py file" or "Do not run tests" as the operator bounding their own request.

The rule: **a build request that BOUNDS its file set means the files it enumerates are the whole
set, even when it also says "project"** -- and a negation naming ONE operation constrains that
operation, not the turn.

Every negative control here is load-bearing in the opposite direction. An unbounded project request
must still reach the scaffolder, a question about a bounded project must still be a question, and
"Do not take action" must still stop the turn. A fix that seals every build would pass the first
half of this file and destroy the product.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from core import runtime_paths
from core.agent_runtime.builder import app_builder, mutation_scope, small_project_plan
from core.agent_runtime.intent_claims import (
    NO_COMMANDS_CONTEXT_KEY,
    ActionPolicy,
    action_policy_for_text,
    turn_action_constraints,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    PENDING_BATCH_CALLS_KEY,
    PermissionEffect,
    decide_tool_call,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
)
from core.tool_intent_executor import execute_tool_intent

WS = "/tmp/ws"

# The live defect, verbatim.
STORMWATCH = (
    "Create a small Python project called StormWatch with a README and one app.py file. "
    "Do not run tests."
)

# The semantic family: five ways of asking for the same thing, none of them a rephrasing of the
# fixture. Different verbs, different container nouns, different bounding words, different
# punctuation, and the file order deliberately reversed in two of them.
FAMILY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (STORMWATCH, "StormWatch", ("StormWatch/README.md", "StormWatch/app.py")),
    (
        "Make a tiny Python folder WeatherBug with README.md and app.py only.",
        "WeatherBug",
        ("WeatherBug/README.md", "WeatherBug/app.py"),
    ),
    (
        "Build project CalcLite: README plus app.py, no tests.",
        "CalcLite",
        ("CalcLite/README.md", "CalcLite/app.py"),
    ),
    (
        "Create folder HelloPy with exactly README.md and app.py.",
        "HelloPy",
        ("HelloPy/README.md", "HelloPy/app.py"),
    ),
    (
        "Generate a Python mini project named RainWatch, only app.py and README.",
        "RainWatch",
        ("RainWatch/app.py", "RainWatch/README.md"),
    ),
)

# The same requests typed the way people actually type: no capitals, no punctuation, a filename
# dictated out loud, a plus sign for "and", and one with no verb at all.
SLOPPY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "make py project stormwatch readme app.py no tests",
        "stormwatch",
        ("stormwatch/README.md", "stormwatch/app.py"),
    ),
    ("small python app folder, readme + app only", "", ("README.md", "app.py")),
    (
        "create project called Storm Watch with readme and app dot py",
        "StormWatch",
        ("StormWatch/README.md", "StormWatch/app.py"),
    ),
)

# Requests that must NOT become a bounded small-project plan. The first two are single-file writes
# that already worked and must keep working; the rest must still reach the full scaffolder or must
# not build at all.
NEGATIVE_CONTROLS = (
    "build a Python project with tests",
    "discuss how to structure a project, no files",
    "write a description of a Python project, no files",
    "Create a small Python project with app.py, README.md and tests",
    "Build an app for tracking expenses",
    "create notes.txt with hello, and a readme",
    "scaffold a flask api in svc with app.py",
    "generate a rest api with routes.py and models.py",
    "create a telegram bot in bot.py",
    "Create a project called Foo with a Flask app.py, templates and static assets",
    # A whole app that mentions its storage file three sentences after naming its folder. The
    # container IS named here, so only the "the tail after the name is a file list and nothing
    # else" rule keeps it out -- which is the rule most likely to be loosened by accident.
    (
        "Create a small Python command-line To-Do app in a new folder called vool-todo-test. It "
        "should add, list, and complete tasks. Store tasks locally in tasks.json. Build and verify "
        "it in the workspace. Do the work yourself."
    ),
    # "only" as an ordinary English intensifier. It restricts the operator's afternoon, not their
    # file set. The third one is the case where the adjacency rule is the ONLY thing holding: it
    # names a file, asks for no unnamed extra, and would seal a whole project down to `app.py` if
    # the marker were keyed on the word instead of on what the word sits next to.
    "Create a small Python project called Foo with app.py, a README and tests, I only have ten minutes",
    "Build a Python project called Foo. I can only spare a moment, so scaffold it however you like.",
    "Create a Python project called Foo with app.py, I only have ten minutes",
)

# Bounded file sets that are NOT instructions. Every one of these carries the marker words the
# planner keys on, so each is a way the bound could become a way around the restraint.
NOT_AN_INSTRUCTION = (
    "the project only has readme and app.py",
    "what about a project folder with only readme and app.py?",
    "lets discuss a project with only app.py and a readme",
    "should i make a project folder with only readme and app.py?",
    "my project folder needs readme and app.py only, i think",
    "a project folder with only readme and app.py is what i inherited",
    "why does that project only have readme and app.py",
    "project folder with only readme and app.py, do not write anything",
)


# --------------------------------------------------------------------------- #
# 1. "Do not run tests" is a constraint on running, not a refusal to act
# --------------------------------------------------------------------------- #


def test_the_live_prompt_no_longer_disables_the_whole_turn() -> None:
    """The first defect, at the line that caused it.

    ``_SPECIFIC_NO_ACTION_RE`` matches "Do not run", the tail "tests" was never read, and the turn
    came back FORBIDDEN -- which is what produced three copies of "I will keep this turn
    conversational" and zero files.
    """
    constraints = turn_action_constraints(STORMWATCH)

    assert constraints.policy is ActionPolicy.ALLOWED
    assert constraints.forbid_commands is True


@pytest.mark.parametrize("prompt", [item[0] for item in FAMILY + SLOPPY])
def test_no_member_of_the_family_is_answered_conversationally(prompt: str) -> None:
    assert action_policy_for_text(prompt) is ActionPolicy.ALLOWED


@pytest.mark.parametrize(
    "prompt",
    [
        # The whole point of the policy, and it must not move: a turn with no action in it.
        "Do not take action: what makes workspace search useful?",
        "I am thinking out loud: why do people organize Downloads folders? "
        "Do not list, open, or change anything.",
        "Do not run a search. Conceptually, why are indexes useful?",
        "Take no actions. What does a good README contain?",
        # A negation of a MUTATING verb really does withdraw the write lane, whatever else the
        # sentence asks for. This is the case the scoping rule must never swallow.
        "Create a small Python project called StormWatch with a README and app.py. Do not write any files.",
        "create app.py with a main function, but do not create files",
    ],
)
def test_a_genuine_no_action_turn_is_still_forbidden(prompt: str) -> None:
    assert action_policy_for_text(prompt) is ActionPolicy.FORBIDDEN


def test_the_scoping_needs_both_a_narrow_object_and_a_real_instruction() -> None:
    """Either half alone lets a no-action turn through, so both are asserted directly.

    "Do not run tests" with nothing else in the message is a bare constraint on a turn that asked
    for nothing -- there is no build to preserve, and the conservative reading stands.
    """
    assert action_policy_for_text("Do not run tests.") is ActionPolicy.FORBIDDEN
    assert action_policy_for_text("Do not run anything. Tell me about pytest.") is ActionPolicy.FORBIDDEN
    assert action_policy_for_text("Create app.py. Do not run it.") is ActionPolicy.ALLOWED


def test_a_bounded_create_request_keeps_the_carve_out_it_already_had() -> None:
    assert action_policy_for_text(
        "Create exactly three files: a.txt, b.txt, c.txt. Do not create anything else."
    ) is ActionPolicy.ALLOWED


# --------------------------------------------------------------------------- #
# 2. The plan: exactly the files the request enumerates
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("prompt", "root", "files"), FAMILY + SLOPPY)
def test_the_request_authorizes_exactly_the_files_it_enumerates(
    prompt: str, root: str, files: tuple[str, ...]
) -> None:
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert scope.is_exact is True, prompt
    assert scope.root_dir == root
    assert scope.paths == files


@pytest.mark.parametrize("prompt", [item[0] for item in FAMILY + SLOPPY])
def test_no_test_module_is_ever_in_the_plan(prompt: str) -> None:
    """The reported symptom. `test_app.py` was planned for a request that named two files."""
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert not [path for path in scope.paths if "test" in path.lower()]
    assert scope.authorizes("StormWatch/test_app.py") is False
    assert scope.authorizes("test_app.py") is False


@pytest.mark.parametrize("prompt", [item[0] for item in FAMILY + SLOPPY])
def test_a_plan_with_tests_forbidden_authorizes_no_command(prompt: str) -> None:
    assert mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS).allow_commands is False


@pytest.mark.parametrize("prompt", [item[0] for item in FAMILY + SLOPPY])
def test_no_path_in_the_plan_leaves_the_workspace(prompt: str) -> None:
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    for path in scope.paths:
        assert not path.startswith(("/", "~", "../"))
        assert ".." not in path.split("/")
    for outside in ("/etc/passwd", "../escape.py", "~/Desktop/x.py", "C:/win/x.py"):
        assert scope.authorizes(outside) is False


def test_a_workspace_rooted_path_is_rebased_and_an_outside_one_is_dropped() -> None:
    """The enumeration reader delegates this to `named_build_files` rather than re-deriving it. A
    second copy of "is this path reachable" is a second place for it to drift."""
    inside = f"Create a project called Zed with a README and one {WS}/Zed/app.py file, no tests."
    assert small_project_plan.enumerated_files(inside, workspace_root=WS) == ["README.md", "Zed/app.py"]

    outside = "Create a project called Zed with a README and one /tmp/elsewhere/app.py file, no tests."
    assert small_project_plan.enumerated_files(outside, workspace_root=WS) == ["README.md"]


def test_a_test_file_the_operator_named_is_still_planned() -> None:
    """The bound is the operator's, in both directions. Asking for tests by name gets tests."""
    scope = mutation_scope.resolve_mutation_scope(
        "Create folder HelloPy with exactly README.md, app.py and test_app.py.", workspace_root=WS
    )

    assert scope.paths == ("HelloPy/README.md", "HelloPy/app.py", "HelloPy/test_app.py")


def test_asking_for_tests_without_naming_one_still_reaches_the_scaffolder() -> None:
    """"with tests" hands the file list back to the model, which is the operator's own widening."""
    scope = mutation_scope.resolve_mutation_scope(
        "Create a project called HelloPy with only a README, app.py and tests", workspace_root=WS
    )

    assert scope.is_exact is False


# --------------------------------------------------------------------------- #
# 3. The negative controls
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prompt", NEGATIVE_CONTROLS)
def test_an_unbounded_build_request_keeps_the_open_scope(prompt: str) -> None:
    """The control that matters most: a seal applied to every build destroys the scaffolder."""
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert scope.is_exact is False, prompt
    assert scope.authorizes("anything/at/all.py") is True
    assert scope.allow_commands is True


@pytest.mark.parametrize("prompt", NOT_AN_INSTRUCTION)
def test_a_bounded_file_set_is_not_a_way_around_the_restraint(prompt: str) -> None:
    """Each of these carries the marker words the planner keys on, and none of them is a build."""
    assert small_project_plan.resolve_small_project_plan(prompt, workspace_root=WS) is None
    assert mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS).paths == ()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [("create README.md only", ("README.md",)), ("write app.py only", ("app.py",))],
)
def test_a_single_named_file_still_authorizes_only_itself(prompt: str, expected: tuple[str, ...]) -> None:
    """The negative controls that ask for ONE file. Neither may grow a companion."""
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert scope.paths == expected
    assert scope.allow_commands is False


def test_the_readme_stays_an_unnamed_extra_when_the_request_is_unbounded() -> None:
    """"create notes.txt with hello, and a readme" left the choice open and must keep doing so.

    This is the one shape where reading a bare "readme" as `README.md` would be wrong, and it is
    the difference between the two halves of this file: the bound is what converts an extra into
    a named deliverable.
    """
    unbounded = "create notes.txt with hello, and a readme"

    # The bound is the ONLY thing separating the two readings, so it is asserted directly rather
    # than inferred from the scope the two requests end up with.
    assert small_project_plan.bounds_its_file_set(unbounded) is False
    assert small_project_plan.enumerates_a_named_container(unbounded) is False
    assert small_project_plan.resolve_small_project_plan(unbounded, workspace_root=WS) is None
    # So the older reading still owns it, and still calls the readme an extra that widens.
    assert mutation_scope.request_widens_beyond_named_files(unbounded) is True

    assert small_project_plan.bounds_its_file_set(STORMWATCH) is True
    assert small_project_plan.enumerated_files(STORMWATCH, workspace_root=WS) == ["README.md", "app.py"]


def test_a_restricting_word_only_bounds_the_list_it_is_sitting_on() -> None:
    """"only" is an ordinary English intensifier, and a marker list that keys on the WORD would
    read "I only have ten minutes" as a file-set bound. Both directions, at the predicate."""
    assert small_project_plan.bounds_its_file_set("README.md and app.py only", workspace_root=WS) is True
    assert small_project_plan.bounds_its_file_set("only app.py and README", workspace_root=WS) is True
    assert small_project_plan.bounds_its_file_set("with exactly README.md and app.py", workspace_root=WS) is True
    # An article between the word and the file keeps the phrase together; a pronoun breaks it.
    assert small_project_plan.bounds_its_file_set("with only a README and app.py", workspace_root=WS) is True
    assert (
        small_project_plan.bounds_its_file_set(
            "a project called Foo with app.py, I only have ten minutes", workspace_root=WS
        )
        is False
    )
    assert (
        small_project_plan.bounds_its_file_set(
            "a project with app.py, a README and tests, I only have ten minutes", workspace_root=WS
        )
        is False
    )
    # No file to be sitting on at all: a restricting word alone bounds nothing.
    assert small_project_plan.bounds_its_file_set("build me only the best project", workspace_root=WS) is False


# --------------------------------------------------------------------------- #
# 4. The builder obeys the plan
# --------------------------------------------------------------------------- #


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, intent, arguments, *, trusted_local_only=False):
        args = dict(arguments or {})
        self.calls.append((intent, str(args.get("path") or args.get("command") or "")))
        return app_builder.ToolOutcome(
            ok=True, response_text="Ran 1 test in 0.001s\nOK", details={"returncode": 0}
        )

    def intents(self) -> list[str]:
        return [intent for intent, _ in self.calls]


def _generator(proposal: list[str]):
    """A model that answers the file-list question with the scaffold from the live drive."""
    seen: list[str] = []

    def generate_fn(prompt: str) -> str:
        seen.append(prompt)
        return json.dumps(proposal) if prompt.startswith("List the files") else "print('storm')\n"

    generate_fn.prompts = seen  # type: ignore[attr-defined]
    return generate_fn


def test_the_build_writes_the_two_files_and_runs_nothing() -> None:
    scope = mutation_scope.resolve_mutation_scope(STORMWATCH, workspace_root=WS)
    runner = _Runner()
    generate_fn = _generator(["app.py", "README.md", "test_app.py"])

    report = app_builder.build_app_from_spec(
        request=STORMWATCH,
        target_rel=scope.root_dir,
        source_context={"workspace": WS, "workspace_root": WS},
        generate_fn=generate_fn,
        run_tool_fn=runner,
        scope=scope,
    )

    assert report.files_written == ["StormWatch/README.md", "StormWatch/app.py"]
    assert runner.calls == [
        ("workspace.ensure_directory", "StormWatch"),
        ("workspace.write_file", "StormWatch/README.md"),
        ("workspace.write_file", "StormWatch/app.py"),
    ]
    assert "sandbox.run_command" not in runner.intents()
    assert report.tests_ran is False
    # The model still proposes `test_app.py`; under an EXACT scope the question is never asked, so
    # the proposal has nowhere to go. That is the assertion, not that the model behaved.
    assert not [p for p in generate_fn.prompts if p.startswith("List the files")]
    assert not [p for p in generate_fn.prompts if "test_app.py" in p]


def test_the_build_announces_its_whole_plan_before_the_first_mutation() -> None:
    """A batch is fingerprinted on byte-exact arguments, so a plan announced without contents
    authorizes nothing. Asserted against the ORDER of the calls, not just their presence."""
    scope = mutation_scope.resolve_mutation_scope(STORMWATCH, workspace_root=WS)
    runner = _Runner()
    announced: list[list[dict]] = []

    app_builder.build_app_from_spec(
        request=STORMWATCH,
        target_rel=scope.root_dir,
        source_context={"workspace": WS, "workspace_root": WS},
        generate_fn=_generator([]),
        run_tool_fn=runner,
        scope=scope,
        announce_plan_fn=lambda calls: announced.append([dict(c) for c in calls]),
    )

    assert len(announced) == 1, "the plan must be announced exactly once, before anything is written"
    plan = announced[0]
    assert [call["intent"] for call in plan] == [
        "workspace.ensure_directory",
        "workspace.write_file",
        "workspace.write_file",
    ]
    assert [str(call["arguments"].get("path")) for call in plan] == [
        "StormWatch",
        "StormWatch/README.md",
        "StormWatch/app.py",
    ]
    assert all(call["arguments"].get("content") for call in plan[1:]), "a plan without bytes is not a plan"


# --------------------------------------------------------------------------- #
# 5. The tool boundary honours "do not run tests" on its own
# --------------------------------------------------------------------------- #


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def test_a_command_is_refused_when_the_turn_forbade_running() -> None:
    """The scope stops the build lane. This is the gate for every OTHER lane that reaches a
    command, driven with the permission controller mocked so only this check can refuse it."""
    with mock.patch("core.tool_intent_executor.decide_tool_call") as decide:
        result = execute_tool_intent(
            {"intent": "sandbox.run_command", "arguments": {"command": "python -m unittest"}},
            task_id="stormwatch-task",
            session_id="stormwatch-session",
            source_context={"surface": "api", NO_COMMANDS_CONTEXT_KEY: True},
            hive_activity_tracker=_tracker(),
        )

    assert result.status == "blocked_by_no_run_constraint"
    assert result.details["executed"] is False
    decide.assert_not_called()


def test_the_same_turn_may_still_write_the_files_it_asked_for() -> None:
    """The half that was destroyed before: the constraint must not take the writes with it."""
    # This case is about the no-run constraint NOT taking the writes with it, so the write has to be
    # permitted for the constraint to be the only thing that could stop it. It used to get there by
    # switching the permission gate off (`mode_policy_is_active` -> False) -- which is the bypass
    # this branch removed. It states an authorizing mode instead.
    with mock.patch(
        "core.tool_intent_executor.execute_authorized_runtime_tool",
        return_value=SimpleNamespace(
            handled=True, ok=True, status="executed", mode="tool_executed",
            response_text="wrote", user_safe_response_text="wrote", details={},
        ),
    ):
        result = execute_tool_intent(
            {"intent": "workspace.write_file", "arguments": {"path": "StormWatch/app.py", "content": "x"}},
            task_id="stormwatch-task",
            session_id="stormwatch-session",
            source_context={
                "surface": "api",
                "operating_mode": "auto",
                "workspace": WS,
                NO_COMMANDS_CONTEXT_KEY: True,
            },
            hive_activity_tracker=_tracker(),
        )

    assert result.status != "blocked_by_no_run_constraint"


# --------------------------------------------------------------------------- #
# 6. Manual mode: one grant for the planned workspace changes
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _isolated_permission_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _stormwatch_plan(root) -> list[dict]:
    scope = mutation_scope.resolve_mutation_scope(STORMWATCH, workspace_root=str(root))
    announced: list[list[dict]] = []
    app_builder.build_app_from_spec(
        request=STORMWATCH,
        target_rel=scope.root_dir,
        source_context={"workspace": str(root), "workspace_root": str(root)},
        generate_fn=_generator([]),
        run_tool_fn=_Runner(),
        scope=scope,
        announce_plan_fn=lambda calls: announced.append([dict(c) for c in calls]),
    )
    return announced[0]


def test_the_planned_workspace_changes_are_offered_as_one_batch(
    tmp_path, _isolated_permission_state
) -> None:
    """The real plan the build lane announces, gated by the real permission controller.

    Built from `build_app_from_spec` rather than hand-written, so a plan that stops carrying its
    contents -- or stops including the directory -- fails here instead of passing on a fixture.
    """
    root = tmp_path / "ws"
    root.mkdir()
    plan = _stormwatch_plan(root)
    set_active_mode("chat-storm", "manual", client_turn_id="turn-storm")
    context = {
        "runtime_session_id": "chat-storm",
        "operating_mode": "manual",
        "workspace_root": str(root),
        "workspace": str(root),
        "cancel_turn_id": "turn-storm",
        PENDING_BATCH_CALLS_KEY: [dict(call) for call in plan[1:]],
    }

    decision = decide_tool_call(
        intent=str(plan[0]["intent"]),
        arguments=dict(plan[0]["arguments"]),
        task_id="task-storm",
        source_context=context,
    )
    request = decision.approval_request
    assert request is not None
    assert "request" in request["scope_options"]
    assert request["planned_action_count"] == 3
    assert [(item["intent"], item["target"]) for item in request["planned_actions"]] == [
        ("workspace.ensure_directory", "StormWatch"),
        ("workspace.write_file", "StormWatch/README.md"),
        ("workspace.write_file", "StormWatch/app.py"),
    ]

    # One grant, and it covers the directory and both files -- and nothing else.
    assert resolve_approval(request["approval_id"], decision="allow", scope="request")["scope"] == "request"
    resumed = {**context, "mode_approval_token": request["approval_id"]}
    for call in plan:
        effect = decide_tool_call(
            intent=str(call["intent"]),
            arguments=dict(call["arguments"]),
            task_id="task-storm",
            source_context=resumed,
        ).effect
        assert effect is PermissionEffect.ALLOW, call["arguments"]
    unplanned = {
        "intent": "workspace.write_file",
        "arguments": {"path": "StormWatch/test_app.py", "content": "x"},
    }
    assert (
        decide_tool_call(
            intent=unplanned["intent"],
            arguments=dict(unplanned["arguments"]),
            task_id="task-storm",
            source_context=resumed,
        ).effect
        is PermissionEffect.REQUIRE_APPROVAL
    ), "the grant covered a file the request never named"


# --------------------------------------------------------------------------- #
# 7. The public turn path -- raw sentence in, tool intents out
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _drive_turn(agent, prompt: str, proposal: list[str]):
    """The real builder turn. Only the model and the tool executor are stubbed."""
    from core.agent_runtime.builder import controller as builder_controller

    calls: list[tuple[str, str]] = []
    batches: list[list[dict]] = []

    def execute(payload, **kwargs):
        args = dict(payload.get("arguments") or {})
        calls.append((str(payload.get("intent") or ""), str(args.get("path") or args.get("command") or "")))
        context = dict(kwargs.get("source_context") or {})
        batches.append([dict(item) for item in (context.get(PENDING_BATCH_CALLS_KEY) or [])])
        return SimpleNamespace(
            handled=True, ok=True, mode="tool_executed", status="executed",
            response_text="Ran 1 test in 0.001s\nOK", details={"returncode": 0},
        )

    with (
        mock.patch.object(builder_controller, "build_ollama_generate_fn", return_value=_generator(proposal)),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=execute),
        mock.patch.object(agent, "_emit_runtime_event", return_value=None),
    ):
        result = builder_controller.maybe_run_builder_controller(
            agent,
            task=SimpleNamespace(task_id="task-small-project"),
            effective_input=prompt,
            classification={"task_class": "unknown"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="session-small-project",
            source_context={
                "workspace": WS,
                "workspace_root": WS,
                "operating_mode": "auto",
                "surface": "api",
            },
            render_capability_truth_response_fn=lambda report: "gap",
            load_active_persona_fn=lambda *_a, **_kw: None,
        )
    assert result is not None, "the builder lane did not claim the turn"
    return result, calls, batches


@pytest.mark.parametrize(("prompt", "root", "files"), FAMILY + SLOPPY)
def test_the_public_turn_mutates_exactly_the_enumerated_files(
    agent, prompt: str, root: str, files: tuple[str, ...]
) -> None:
    """The end-to-end shape of the defect: routing, scope, target, file loop and test decision are
    all production code. Before this, the trace for StormWatch had zero writes in it."""
    _result, calls, _batches = _drive_turn(agent, prompt, ["app.py", "README.md", "test_app.py"])

    expected = [("workspace.ensure_directory", root)] if root else []
    expected += [("workspace.write_file", path) for path in files]
    assert calls == expected


def test_the_activity_files_changed_match_the_receipts(agent) -> None:
    """The reply's file table, the change ledger and the tool calls are three views of one set.

    The live drive had a table naming StormWatch with nothing behind it, so the table is asserted
    against the calls that actually reached the workspace rather than against itself.
    """
    result, calls, _batches = _drive_turn(agent, STORMWATCH, ["app.py", "README.md", "test_app.py"])

    written = [path for intent, path in calls if intent == "workspace.write_file"]
    ledger = dict((result.get("details") or {}).get("builder_model_build") or {})
    assert ledger["files_written"] == written == ["StormWatch/README.md", "StormWatch/app.py"]
    assert ledger["mutation_scope"]["authorized_paths"] == written
    assert ledger["mutation_scope"]["allow_commands"] is False
    assert ledger["target_dir"] == "StormWatch"
    response = str(result.get("response") or "")
    for path in written:
        assert path.rsplit("/", 1)[-1] in response
    assert "test_app.py" not in response


def test_the_first_write_carries_the_rest_of_the_plan_for_approval(agent) -> None:
    """Without the hand-off the controller sees one write, asks about one write, and a three-call
    project costs three prompts. Asserted at the seam that carries it, per call."""
    _result, calls, batches = _drive_turn(agent, STORMWATCH, ["app.py", "README.md", "test_app.py"])

    head_batch = [(str(item["intent"]), str(dict(item["arguments"]).get("path"))) for item in batches[0]]
    assert calls[0] == ("workspace.ensure_directory", "StormWatch")
    assert head_batch == [
        ("workspace.write_file", "StormWatch/README.md"),
        ("workspace.write_file", "StormWatch/app.py"),
    ]
    # Each member drops out as it is dispatched, so the last call heads no batch and cannot
    # re-authorize the ones already spent.
    assert len(batches[1]) == 1
    assert batches[2] == []


def test_an_unbounded_project_still_scaffolds_and_runs_on_the_public_path(agent) -> None:
    """The control at the public seam. A change that quietly turned every build into a two-file
    write would pass every assertion above and still have destroyed the scaffolder."""
    _result, calls, _batches = _drive_turn(
        agent, "Create a small Python project with app.py, README.md and tests", ["app.py", "README.md", "test_app.py"]
    )
    intents = [intent for intent, _ in calls]

    assert intents.count("workspace.write_file") == 3
    assert "sandbox.run_command" in intents
    assert intents[0] == "workspace.ensure_directory"
