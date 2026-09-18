"""Build-mode code-build gate (T017 / mode enforcement Slice 2).

In Build/Auto mode a plain "build a <bot/service>" triggers the file-writing builder; in Ask/Plan it
never does (read-only); with no mode set the prior explicit-request behavior is unchanged. This tests
the decision gate only (no files are written)."""
from __future__ import annotations

from apps.vool_agent import VoolAgent

_CLS = {"task_class": "unknown"}
_WS = {"workspace": "/tmp/ws", "workspace_root": "/tmp/ws"}


def _agent():
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.start()
    return agent


def _should(agent, text, mode):
    ctx = dict(_WS)
    if mode is not None:
        ctx["operating_mode"] = mode
    return agent._should_run_builder_controller(effective_input=text, classification=_CLS, source_context=ctx)


def test_build_mode_triggers_builder_on_plain_build_request() -> None:
    agent = _agent()
    assert _should(agent, "build a telegram bot that greets new users", "build") is True
    assert _should(agent, "create a discord bot", "auto") is True
    # Manual/Review may enter the builder, but every write still crosses the central controller
    # and therefore becomes an exact approval preview before dispatch.
    assert _should(agent, "build a telegram bot that greets new users", "manual") is True
    assert _should(agent, "create a discord bot", "review_edits") is True
    assert _should(agent, "generate a python script to rename files", "build") is True


def test_ask_and_plan_modes_never_run_the_file_writing_builder() -> None:
    agent = _agent()
    assert _should(agent, "build a telegram bot", "ask") is False
    assert _should(agent, "build a telegram bot", "plan") is False


def test_build_mode_respects_an_explicit_no_files_opt_out() -> None:
    agent = _agent()
    assert _should(agent, "build a telegram bot, just plan it, no files", "build") is False


def test_no_mode_keeps_prior_behavior_for_a_bare_build_request() -> None:
    # Without a mode and without explicit "write the files"/workspace language, a bare "build a bot"
    # does NOT newly trigger the writer -- the change is scoped to Build/Auto.
    agent = _agent()
    assert _should(agent, "build a telegram bot", None) is False


_TODO = (
    "Create a small Python command-line To-Do app in a new folder called vool-todo-test. It should "
    "add, list, and complete tasks. Store tasks locally in tasks.json. Build and verify it in the "
    "workspace. Do the work yourself."
)


def test_todo_app_build_request_routes_to_builder_in_build_mode() -> None:
    # A full "create an app ... build and verify in the workspace" request must run the file-writing
    # builder in Build/Auto -- it must NOT be hijacked into a read of the not-yet-created tasks.json.
    agent = _agent()
    assert _should(agent, _TODO, "build") is True
    assert _should(agent, _TODO, "auto") is True
    assert _should(agent, _TODO, "ask") is False
    assert _should(agent, _TODO, "plan") is False


def test_explicit_app_build_profile_prefers_model_builder() -> None:
    agent = _agent()
    profile = agent._builder_controller_profile(
        effective_input=(
            "create a Python CLI app in a new folder with automated tests, "
            "then run the tests and build it"
        ),
        classification=_CLS,
        interpretation=None,
        source_context={**_WS, "operating_mode": "build"},
    )
    assert profile["mode"] == "model_build"


def test_write_intent_detector_distinguishes_builds_from_reads() -> None:
    # The read-only refusal guard only fires on genuine write intents, never on read-only questions.
    agent = _agent()
    assert agent._looks_like_write_intent_request(_TODO) is True
    assert agent._looks_like_write_intent_request("build a telegram bot that greets users") is True
    assert agent._looks_like_write_intent_request("what is the biggest file on D drive") is False
    assert agent._looks_like_write_intent_request("read the file config.json") is False
    assert agent._looks_like_write_intent_request("how many drives does this pc have") is False
    assert agent._looks_like_write_intent_request("list the folder contents of the workspace") is False
