"""One named file is one file. Planner creativity is not mutation authority.

A QA drive in an isolated project asked::

    Create a file called notes.txt in this project containing the line: hello from qa.

and the runtime performed, in order:

    workspace.ensure_directory  generated/file-called-notes-txt-b92eec/
    workspace.write_file        notes.txt
    workspace.write_file        app.py
    workspace.write_file        README.md
    workspace.write_file        test_app.py
    sandbox.run_command         python -m unittest
    workspace.write_file        test_app.py            (again, from the fix loop)
    sandbox.run_command         python -m unittest      (again)

``notes.txt`` was correct. The other six mutations and both commands were invented -- not by a bad
model, but by a lane that had never been told what the request authorized. ``build_app_from_spec``
asks a model to "list the files for this project" (a prompt that instructs it to include a test
module and a README), then writes and runs whatever comes back, because until now the only scope
anyone computed was a build DIRECTORY derived from a digest of the operator's sentence.

The tests below drive that boundary at both ends: the resolver that decides the scope, the builder
that must obey it, and the public turn path from a raw English sentence to the exact set of tool
intents that reach the workspace. The last one is the one that would have caught this: every seam
in the trace above was individually working.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.builder import app_builder, mutation_scope
from core.agent_runtime.builder.support import controller_profile

WS = "/tmp/ws"

# The live request, verbatim, and what the model actually proposed for it on that drive.
QA_EXACT = "Create a file called notes.txt in this project containing the line: hello from qa."
QA_MODEL_PROPOSAL = ["notes.txt", "app.py", "README.md", "test_app.py"]

# The four other shapes the boundary has to get right, from the same specification.
README_EXACT = "Write README.md with this text: X"
# The same file, asked for as a brief rather than as verbatim content -- the shape the build lane
# owns. `README_EXACT` supplies its own text and belongs to the literal-write lane, which is pinned
# separately in test_a_verbatim_content_request_never_reaches_the_scaffolder.
README_BUILD = "Create README.md in this project with a short intro paragraph"
NESTED_EXACT = "Create src/foo.py with a function called foo"
PROJECT_BROAD = "Create a small Python project with app.py, README.md and tests"
APP_BROAD = "Build an app for tracking expenses"


# ---------------------------------------------------------------------------
# 1. The scope itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (QA_EXACT, ("notes.txt",)),
        (README_EXACT, ("README.md",)),
        (NESTED_EXACT, ("src/foo.py",)),
        # "next to it" -- a bare filename adopts the folder the request named, rather than landing
        # at the workspace root while its sibling lands in `qa1/`.
        (
            f"make a file at {WS}/qa1/fizz.py with a fizzbuzz function, and a test_fizz.py next to it. go.",
            ("qa1/fizz.py", "qa1/test_fizz.py"),
        ),
        # Two folders, two files. Flattening both into the first target directory is the silent
        # relocation this lane keeps removing, so the scope resolves them before anything flattens.
        ("create a/x.py and b/y.py", ("a/x.py", "b/y.py")),
    ],
)
def test_an_exact_request_authorizes_exactly_the_files_it_names(
    prompt: str, expected: tuple[str, ...]
) -> None:
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert scope.is_exact is True
    assert scope.paths == expected


@pytest.mark.parametrize(
    "prompt",
    [
        PROJECT_BROAD,
        APP_BROAD,
        "scaffold a flask api in svc with app.py",
        "generate a rest api with routes.py and models.py",
        "create a telegram bot in bot.py",
        # A whole To-Do app that happens to name its storage file. The app is the deliverable.
        (
            "Create a small Python command-line To-Do app in a new folder called vool-todo-test. It "
            "should add, list, and complete tasks. Store tasks locally in tasks.json. Build and "
            "verify it in the workspace. Do the work yourself."
        ),
        # Files asked for but not named: the operator handed the choice to the model themselves.
        "create notes.txt with hello, and a readme",
    ],
)
def test_a_request_for_a_deliverable_keeps_the_open_scope(prompt: str) -> None:
    """The seal must not become a way of refusing builds the operator asked for."""
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)

    assert scope.is_exact is False
    assert scope.authorizes("anything/at/all.py") is True
    assert scope.allow_commands is True


def test_the_widening_noun_must_be_the_object_of_the_build_verb() -> None:
    """"in this project" says WHERE. "a small python project" says WHAT.

    The same word decides the scope in both sentences, and reading it as a keyword rather than as
    the object of the verb is what would collapse the whole distinction. Both directions are pinned
    here because a fix in either one alone reintroduces the other's bug.
    """
    assert mutation_scope.request_widens_beyond_named_files(QA_EXACT) is False
    assert mutation_scope.request_widens_beyond_named_files("create notes.txt in the repo") is False
    assert mutation_scope.request_widens_beyond_named_files(PROJECT_BROAD) is True
    assert mutation_scope.request_widens_beyond_named_files("build me an api in api.py") is True


def test_a_filename_is_not_read_as_the_deliverable_it_is_named_after() -> None:
    """`\\bapp\\b` matches inside `app.py` and `\\bcli\\b` inside `cli.py`.

    This repository has already paid for substring collisions in build vocabulary once (see
    `build_request_intent`'s docstring: `app` in "happens", `cli` in "client"). A named file is a
    path, not a noun, so it is removed before the noun test runs.
    """
    for prompt in ("create app.py with a main function", "write cli.py that parses argv"):
        scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)
        assert scope.is_exact is True, prompt


def test_a_hyphenated_word_does_not_end_the_object_phrase() -> None:
    """"to-do" contains "to", and a plain `\\bto\\b` boundary ended the object phrase there.

    Measured while building this module: "Create a small Python command-line To-Do app in a new
    folder ..." resolved to EXACT with `tasks.json` as its only authorized target, which would have
    refused every other file of an app the operator explicitly asked for.
    """
    assert (
        mutation_scope.request_widens_beyond_named_files(
            "create a small python command-line to-do app in a folder"
        )
        is True
    )


def test_an_exact_request_does_not_authorize_running_anything() -> None:
    """Writing a file is not consent to execute one. Asking for a run is."""
    assert mutation_scope.resolve_mutation_scope(QA_EXACT, workspace_root=WS).allow_commands is False
    assert (
        mutation_scope.resolve_mutation_scope(
            "create fizz.py with a fizzbuzz function and run it", workspace_root=WS
        ).allow_commands
        is True
    )


def test_the_scope_refuses_a_neighbour_of_an_authorized_path() -> None:
    """The gate is target equality, not "somewhere under the same folder"."""
    scope = mutation_scope.resolve_mutation_scope(NESTED_EXACT, workspace_root=WS)

    assert scope.authorizes("src/foo.py") is True
    assert scope.authorizes("./src/foo.py") is True  # same target, different spelling
    assert scope.authorizes("src/bar.py") is False
    assert scope.authorizes("src/foo.py.bak") is False
    assert scope.authorizes_directory("src") is True
    assert scope.authorizes_directory("generated/anything") is False


def test_the_restraint_the_named_file_gate_owns_is_inherited_whole() -> None:
    """A question and an opt-out authorize nothing, so they must not resolve to an EXACT scope that
    a downstream lane could read as permission to write."""
    for prompt in (
        "i need retry.py but do not write any files",
        "why did you create that file earlier",
        "should i want a conftest.py in here?",
    ):
        scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)
        assert scope.is_exact is False, prompt
        assert scope.paths == ()


# ---------------------------------------------------------------------------
# 2. The builder must obey it
# ---------------------------------------------------------------------------


class _Runner:
    """Records every tool intent the build dispatches, and always succeeds."""

    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.returncode = returncode

    def __call__(self, intent, arguments, *, trusted_local_only=False):
        args = dict(arguments or {})
        self.calls.append((intent, str(args.get("path") or args.get("command") or "")))
        return app_builder.ToolOutcome(
            ok=True,
            response_text="Ran 1 test in 0.001s\nOK",
            details={"returncode": self.returncode},
        )

    def intents(self) -> list[str]:
        return [intent for intent, _ in self.calls]

    def paths_for(self, intent: str) -> list[str]:
        return [path for name, path in self.calls if name == intent]


def _generator(proposal: list[str], *, content: str = "hello from qa\n"):
    """A model that proposes `proposal` for the file list and `content` for every file."""
    seen: list[str] = []

    def generate_fn(prompt: str) -> str:
        seen.append(prompt)
        return json.dumps(proposal) if prompt.startswith("List the files") else content

    generate_fn.prompts = seen  # type: ignore[attr-defined]
    return generate_fn


def _build(prompt: str, proposal: list[str], *, target_rel: str | None = None, returncode: int = 0):
    scope = mutation_scope.resolve_mutation_scope(prompt, workspace_root=WS)
    runner = _Runner(returncode=returncode)
    generate_fn = _generator(proposal)
    report = app_builder.build_app_from_spec(
        request=prompt,
        target_rel=scope.root_dir if scope.is_exact else (target_rel or "generated/app"),
        source_context={"workspace": WS, "workspace_root": WS},
        generate_fn=generate_fn,
        run_tool_fn=runner,
        scope=scope,
    )
    return report, runner, generate_fn


def test_the_exact_request_writes_one_file_and_runs_nothing() -> None:
    """The whole defect, at the lane that performed it."""
    report, runner, _ = _build(QA_EXACT, QA_MODEL_PROPOSAL)

    assert report.files_written == ["notes.txt"]
    assert runner.paths_for("workspace.write_file") == ["notes.txt"]
    assert "sandbox.run_command" not in runner.intents()
    assert "workspace.ensure_directory" not in runner.intents()
    assert report.tests_ran is False


def test_no_generated_scaffold_directory_is_invented() -> None:
    """`generated/file-called-notes-txt-b92eec/` is a mutation the request never authorized, and the
    slug is derived from the operator's own sentence, which is what made it look intentional."""
    report, runner, _ = _build(QA_EXACT, QA_MODEL_PROPOSAL)

    assert report.target_dir == ""
    assert not [path for _, path in runner.calls if path.startswith("generated/")]


def test_the_model_is_never_asked_which_files_to_create() -> None:
    """`_LIST_PROMPT` instructs the model to include a unittest module and a README. Asking that
    question at all, for a request that already named its file, is the origin of the scaffold --
    and a generation whose answer can only be discarded is wall clock spent for nothing."""
    _, _, generate_fn = _build(QA_EXACT, QA_MODEL_PROPOSAL)

    assert not [p for p in generate_fn.prompts if p.startswith("List the files")]
    assert len(generate_fn.prompts) == 1
    assert "notes.txt" in generate_fn.prompts[0]


def test_a_folder_the_request_named_is_still_created() -> None:
    """The seal bounds mutation, it does not forbid it: `src/` is part of the requested target."""
    report, runner, _ = _build(NESTED_EXACT, ["src/foo.py", "README.md", "test_foo.py"])

    assert report.files_written == ["src/foo.py"]
    assert runner.paths_for("workspace.ensure_directory") == ["src"]
    assert "sandbox.run_command" not in runner.intents()


def test_an_escaping_target_is_refused_even_when_the_scope_declares_it() -> None:
    """An EXACT scope skips `_confine_relative_path`, so `authorizes` IS the confinement gate.

    Driven adversarially with a scope built by hand -- which is all a scope is, a frozen dataclass
    any caller can construct -- declaring four targets, three of which leave the workspace. A
    boundary that trusts its own inputs because its only current constructor happens to be careful
    is not a boundary, and this is the case where the flattening confinement the OPEN lane relies
    on is deliberately not running.
    """
    runner = _Runner()
    report = app_builder.build_app_from_spec(
        request=QA_EXACT,
        target_rel="",
        source_context={"workspace": WS},
        generate_fn=_generator([]),
        run_tool_fn=runner,
        scope=mutation_scope.MutationScope(
            kind=mutation_scope.EXACT,
            paths=("notes.txt", "../../etc/passwd", "/tmp/evil.py", "C:\\Windows\\evil.ps1"),
            root_dir="",
            allow_commands=False,
        ),
    )

    assert runner.paths_for("workspace.write_file") == ["notes.txt"]
    assert report.paths_refused == ["../../etc/passwd", "/tmp/evil.py", "C:/Windows/evil.ps1"]
    assert "sandbox.run_command" not in runner.intents()
    assert "workspace.ensure_directory" not in runner.intents()


def test_the_model_cannot_propose_a_target_under_an_exact_scope() -> None:
    """Requirement: a model may PROPOSE additional work; the runtime may not execute it.

    The generator here is the one from the live drive -- it answers the file-list question with
    `notes.txt, app.py, README.md, test_app.py`. Under an EXACT scope that answer has nowhere to
    go: the question is never asked, and the write set comes from the operator's sentence. The
    proposal is present in the harness and changes nothing, which is the assertion.
    """
    report, runner, generate_fn = _build(QA_EXACT, QA_MODEL_PROPOSAL)

    assert json.dumps(QA_MODEL_PROPOSAL) == _generator(QA_MODEL_PROPOSAL)("List the files")
    assert report.files_written == ["notes.txt"]
    assert runner.paths_for("workspace.write_file") == ["notes.txt"]
    assert not [p for p in generate_fn.prompts if "app.py" in p or "test_app.py" in p]


def test_a_refused_command_is_reported_as_refused_not_as_absent() -> None:
    """"No test command was detected" would be false: one was, and consent for it was missing."""
    prompt = f"make a file at {WS}/qa1/fizz.py with a fizzbuzz function, and a test_fizz.py next to it. go."
    report, runner, _ = _build(prompt, [])

    assert sorted(report.files_written) == ["qa1/fizz.py", "qa1/test_fizz.py"]
    assert "sandbox.run_command" not in runner.intents()
    assert report.commands_refused == ["python -m unittest -v test_fizz"]

    rendered = app_builder.render_app_build_response(report)
    assert "not to execute anything" in rendered
    assert "no test command was detected" not in rendered.lower()


def test_the_receipt_states_the_authorized_set() -> None:
    """The change ledger has to be checkable against what was asked, or a four-file scaffold reads
    as a clean success -- which is exactly how this defect survived its own receipt."""
    report, _, _ = _build(QA_EXACT, QA_MODEL_PROPOSAL)
    rendered = app_builder.render_app_build_response(report)

    assert report.scope_kind == mutation_scope.EXACT
    assert report.authorized_paths == ("notes.txt",)
    assert "you named `notes.txt`" in rendered
    assert "app.py" not in rendered and "test_app.py" not in rendered


@pytest.mark.parametrize("prompt", [PROJECT_BROAD, APP_BROAD])
def test_a_broad_request_still_gets_the_whole_build_lane(prompt: str) -> None:
    """The control. If this goes red the seal has become a general disabling of the builder."""
    report, runner, generate_fn = _build(prompt, ["app.py", "README.md", "test_app.py"])

    assert [p for p in generate_fn.prompts if p.startswith("List the files")], "no file list asked"
    assert len(report.files_written) == 3
    assert "sandbox.run_command" in runner.intents()
    assert runner.paths_for("workspace.ensure_directory") == ["generated/app"]
    assert report.tests_ran is True


def test_an_unsealed_caller_keeps_the_behaviour_it_had() -> None:
    """`scope=None` is the historical lane, unchanged -- every other caller of this function.

    Written as a bound on the blast radius: a change to a shared build entry point that silently
    altered every caller would be a defect even if this file's other tests all passed.
    """
    runner = _Runner()
    report = app_builder.build_app_from_spec(
        request=QA_EXACT,
        target_rel="generated/file-called-notes-txt-b92eec",
        source_context={"workspace": WS},
        generate_fn=_generator(QA_MODEL_PROPOSAL),
        run_tool_fn=runner,
    )

    assert report.scope_kind == mutation_scope.OPEN
    assert len(report.files_written) == 4
    assert "sandbox.run_command" in runner.intents()


# ---------------------------------------------------------------------------
# 3. The routing seam -- the scope is useless if the profile never applies it
# ---------------------------------------------------------------------------


def _profile(prompt: str) -> dict:
    """The real `controller_profile` with every branch that could mask the result switched off."""
    agent = SimpleNamespace(
        _should_run_builder_controller=lambda **_kw: True,
        _workspace_build_target=lambda **_kw: {
            "platform": "generic",
            "language": "python",
            # What `_unrooted_build_dir` produced for this very sentence on the live drive.
            "root_dir": "generated/file-called-notes-txt-b92eec",
        },
        _supports_bounded_builder_workflow_request=lambda **_kw: False,
        _looks_like_explicit_workspace_file_request=lambda _t: False,
        _looks_like_generic_workspace_bootstrap_request=lambda _t: False,
        _builder_support_gap_report=lambda **_kw: {"support_level": "unsupported", "reason": "gap"},
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


def test_the_profile_strips_the_invented_build_directory() -> None:
    profile = _profile(QA_EXACT)

    # P0 simple-file-write: this phrasing carries LITERAL content ("hello from qa"), so the
    # typed workflow lane executes it; the scope assertions below are what this test pins and
    # they are unchanged. (It reached model_build at base only because the literal routing
    # did not exist yet.)
    assert profile["mode"] == "workflow"
    assert profile["target"]["root_dir"] == ""
    assert profile["mutation_scope"]["kind"] == mutation_scope.EXACT
    assert profile["mutation_scope"]["authorized_paths"] == ["notes.txt"]
    assert profile["mutation_scope"]["allow_commands"] is False


def test_the_profile_keeps_the_named_destination() -> None:
    assert _profile(NESTED_EXACT)["target"]["root_dir"] == "src"
    assert _profile(f"create {WS}/qa2/greet.py containing a greet(name) function")["target"]["root_dir"] == "qa2"


def test_the_profile_leaves_a_broad_build_alone() -> None:
    profile = _profile(PROJECT_BROAD)

    assert profile["mode"] == "model_build"
    assert profile["target"]["root_dir"] == "generated/file-called-notes-txt-b92eec"
    assert profile["mutation_scope"]["kind"] == mutation_scope.OPEN


# ---------------------------------------------------------------------------
# 4. The public turn path -- raw sentence in, tool intents out
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _drive_turn(agent, prompt: str, proposal: list[str]) -> list[tuple[str, str]]:
    """Run the real builder turn, recording every tool intent that reaches the workspace.

    Only two things are stubbed: the model (so the generation is deterministic) and the tool
    executor (so nothing is written to a real disk). Routing, scope resolution, target selection,
    the file loop and the test-command decision are all the production code paths.
    """
    from core.agent_runtime.builder import controller as builder_controller

    calls: list[tuple[str, str]] = []

    def execute(payload, **_kwargs):
        args = dict(payload.get("arguments") or {})
        calls.append((str(payload.get("intent") or ""), str(args.get("path") or args.get("command") or "")))
        return SimpleNamespace(
            handled=True,
            ok=True,
            mode="tool_executed",
            status="executed",
            response_text="Ran 1 test in 0.001s\nOK",
            details={"returncode": 0},
        )

    with (
        mock.patch.object(builder_controller, "build_ollama_generate_fn", return_value=_generator(proposal)),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=execute),
        mock.patch.object(agent, "_emit_runtime_event", return_value=None),
    ):
        result = builder_controller.maybe_run_builder_controller(
            agent,
            task=SimpleNamespace(task_id="task-mutation-scope"),
            effective_input=prompt,
            classification={"task_class": "unknown"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="session-mutation-scope",
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
    return calls


def test_the_live_request_mutates_exactly_one_file(agent) -> None:
    """The trace from the QA drive, asserted as a set rather than a story.

    Before: ensure_directory generated/file-called-notes-txt-b92eec, four write_file calls, two
    sandbox.run_command calls. After: one write_file, and it is the file that was asked for.
    """
    calls = _drive_turn(agent, QA_EXACT, QA_MODEL_PROPOSAL)

    assert calls == [("workspace.write_file", "notes.txt")]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (README_BUILD, [("workspace.write_file", "README.md")]),
        (
            NESTED_EXACT,
            [("workspace.ensure_directory", "src"), ("workspace.write_file", "src/foo.py")],
        ),
    ],
)
def test_the_other_exact_shapes_mutate_exactly_their_target(agent, prompt, expected) -> None:
    assert _drive_turn(agent, prompt, ["app.py", "README.md", "test_app.py", "src/foo.py"]) == expected


def test_a_verbatim_content_request_never_reaches_the_scaffolder(agent) -> None:
    """"Write README.md with this text: X" supplies its own content, so the MODEL-BUILD lane --
    whose job is generating content from a brief -- must not own it.

    Updated by the P0 simple-file-write repair (audit of 59aa5ee7): a LITERAL content demand is
    one typed file operation, resolved by `core.execution.write_demand` and executed through
    this builder's workflow loop so `decide_tool_call` and the effect gates apply. What must
    never happen is unchanged: no model generation runs for content the user already supplied.
    Measured here: exactly one `workspace.write_file` for the named file with the supplied
    text, and no model-build payload.
    """
    from core.agent_runtime.builder import controller as builder_controller

    scope = mutation_scope.resolve_mutation_scope(README_EXACT, workspace_root=WS)
    assert scope.is_exact is True and scope.paths == ("README.md",)

    with mock.patch.object(agent, "_execute_tool_intent") as executed:
        claimed = builder_controller.maybe_run_builder_controller(
            agent,
            task=SimpleNamespace(task_id="task-verbatim"),
            effective_input=README_EXACT,
            classification={"task_class": "unknown"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="session-verbatim",
            source_context={
                "workspace": WS,
                "workspace_root": WS,
                "operating_mode": "auto",
                "surface": "api",
            },
            render_capability_truth_response_fn=lambda report: "gap",
            load_active_persona_fn=lambda *_a, **_kw: SimpleNamespace(
                display_name="Test", tone="plain", execution_style="direct", spirit_anchor="honest"
            ),
        )

    assert claimed is not None, "the literal write must be executed, not dropped"
    write_calls = [
        call for call in executed.call_args_list
        if str((call.args[0] if call.args else call.kwargs.get("payload", {})).get("intent") or "") == "workspace.write_file"
    ]
    assert len(write_calls) == 1, f"exactly one typed write expected, got: {executed.call_args_list}"
    payload = write_calls[0].args[0] if write_calls[0].args else write_calls[0].kwargs.get("payload")
    arguments = dict(payload.get("arguments") or {})
    assert arguments.get("path") == "README.md"
    assert arguments.get("content") == "X"
    assert "model_build" not in str(claimed), "the model-build lane must not run for supplied content"


def test_the_build_lane_seals_a_target_it_was_handed(agent) -> None:
    """`_run_model_build` resolves the scope itself instead of trusting the target it is given.

    Driven with the profile bypassed and the invented directory handed straight in -- the state any
    caller that predates the seal, or forgets it, leaves this lane in. It is the last thing between
    a sentence and a `workspace.write_file`, so an authority that only holds when an upstream caller
    remembers to apply it is not an authority. Without this the controller-side seal is unreachable
    while the profile-side one is present, and a sabotage of it stays green.
    """
    from core.agent_runtime.builder import controller as builder_controller

    calls: list[tuple[str, str]] = []

    def execute(payload, **_kwargs):
        args = dict(payload.get("arguments") or {})
        calls.append((str(payload.get("intent") or ""), str(args.get("path") or args.get("command") or "")))
        return SimpleNamespace(
            handled=True, ok=True, mode="tool_executed", status="executed",
            response_text="", details={"returncode": 0},
        )

    with (
        mock.patch.object(
            builder_controller, "build_ollama_generate_fn", return_value=_generator(QA_MODEL_PROPOSAL)
        ),
        mock.patch.object(agent, "_execute_tool_intent", side_effect=execute),
        mock.patch.object(agent, "_emit_runtime_event", return_value=None),
    ):
        result = builder_controller._run_model_build(
            agent,
            task=SimpleNamespace(task_id="task-handed-target"),
            effective_input=QA_EXACT,
            classification={"task_class": "unknown"},
            interpretation=SimpleNamespace(topic_hints=[]),
            session_id="session-handed-target",
            source_context={"workspace": WS, "workspace_root": WS, "operating_mode": "auto"},
            # The unsealed target, exactly as `_unrooted_build_dir` produced it on the live drive.
            target={
                "platform": "generic",
                "language": "python",
                "root_dir": "generated/file-called-notes-txt-b92eec",
            },
            load_active_persona_fn=lambda *_a, **_kw: None,
        )

    assert calls == [("workspace.write_file", "notes.txt")]
    ledger = dict((result.get("details") or {}).get("builder_model_build") or {})
    assert ledger["files_written"] == ["notes.txt"]
    assert ledger["mutation_scope"]["authorized_paths"] == ["notes.txt"]
    assert ledger["mutation_scope"]["allow_commands"] is False
    assert ledger["target_dir"] == ""


@pytest.mark.parametrize("prompt", [PROJECT_BROAD, APP_BROAD])
def test_a_broad_request_still_scaffolds_and_runs_on_the_public_path(agent, prompt) -> None:
    """The control at the public seam. A scope check that quietly turned every build into a
    single-file write would pass every assertion above and still have destroyed the product."""
    calls = _drive_turn(agent, prompt, ["app.py", "README.md", "test_app.py"])
    intents = [intent for intent, _ in calls]

    assert intents.count("workspace.write_file") == 3
    assert "sandbox.run_command" in intents
    assert intents[0] == "workspace.ensure_directory"
