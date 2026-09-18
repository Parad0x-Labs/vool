"""A build that takes hours must say so, and must stop.

Two silences, one shape.

`builder/controller.py` posted straight to `requests.post` and emitted nothing. Every other model
lane announces itself through `core/memory_first_router.py` with `model.call_started`,
`model.call_completed` and `model.call_failed`, which is what the UI renders as activity. One build
issues up to 65 sequential generations — 1 plan + 16 files + 3 fix rounds x 16 — each with a 300s
timeout and a 6144-12288 token budget. At the per-call limit that is 5.4 hours during which the
operator sees a spinner and cannot tell a working build from a wedged one. A per-call timeout cannot
bound that, because it applies 65 times; only an aggregate ceiling can.

`mark_stale_runtime_checkpoints_interrupted` had no time predicate and ran once, at process start.
A task that wedged AFTER startup left a `running` row with zero steps that survived for the entire
life of the process — nothing swept it, and the operator saw an in-flight task that would never
move again.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.builder.controller import (
    BuilderGenerationBudget,
    build_ollama_generate_fn,
)


class _Response:
    def __init__(self, content: str = "ok", done_reason: str = "stop") -> None:
        self._content, self._done_reason = content, done_reason

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": {"content": self._content}, "done_reason": self._done_reason}


def _events(context: dict) -> list[dict]:
    return context.setdefault("_captured_events", [])


@pytest.fixture
def captured():
    """Capture what the operator would see, through the real emitter."""

    seen: list[dict] = []

    def _emit(source_context, *, event_type, message, details=None):
        seen.append({"event_type": event_type, "message": message, **(details or {})})

    with mock.patch(
        "core.agent_runtime.builder.controller.emit_runtime_event", side_effect=_emit
    ):
        yield seen


# --------------------------------------------------------------------------------------
# Visible
# --------------------------------------------------------------------------------------


def test_each_generation_announces_its_start_and_finish(captured) -> None:
    generate = build_ollama_generate_fn(
        base_url="http://127.0.0.1:11434",
        model_tag="qwen3:8b",
        source_context={"surface": "api"},
    )
    with mock.patch("requests.post", return_value=_Response("hello")):
        assert generate("write a file") == "hello"

    kinds = [event["event_type"] for event in captured]
    assert kinds == ["model.call_started", "model.call_completed"], kinds
    assert captured[0]["model_id"] == "qwen3:8b"
    assert captured[0]["locality"] == "local"
    assert captured[0]["call_index"] == 1
    assert captured[1]["characters"] == len("hello")


def test_a_failed_generation_is_reported_not_swallowed(captured) -> None:
    """The old code returned `"", False` on any exception with no trace of it anywhere."""

    generate = build_ollama_generate_fn(
        base_url="http://127.0.0.1:11434",
        model_tag="qwen3:8b",
        source_context={"surface": "api"},
    )
    with mock.patch("requests.post", side_effect=ConnectionError("refused")):
        assert generate("write a file") == ""

    failures = [event for event in captured if event["event_type"] == "model.call_failed"]
    assert len(failures) == 1
    assert failures[0]["reason"] == "ConnectionError"


def test_the_failure_event_does_not_carry_the_exception_text(captured) -> None:
    """This reaches the operator's event stream, and an exception body can carry a URL or payload."""

    generate = build_ollama_generate_fn(
        base_url="http://127.0.0.1:11434",
        model_tag="qwen3:8b",
        source_context={"surface": "api"},
    )
    secret = "http://127.0.0.1:11434/api/chat?token=abcd1234"
    with mock.patch("requests.post", side_effect=ConnectionError(secret)):
        generate("write a file")

    assert not any("abcd1234" in str(value) for event in captured for value in event.values())


def test_every_generation_in_a_build_is_counted(captured) -> None:
    budget = BuilderGenerationBudget()
    generate = build_ollama_generate_fn(
        base_url="http://127.0.0.1:11434",
        model_tag="qwen3:8b",
        source_context={"surface": "api"},
        budget=budget,
    )
    with mock.patch("requests.post", return_value=_Response("x")):
        for _ in range(5):
            generate("write a file")

    assert budget.calls == 5
    starts = [event for event in captured if event["event_type"] == "model.call_started"]
    assert [event["call_index"] for event in starts] == [1, 2, 3, 4, 5]


def test_a_build_with_no_context_still_generates(captured) -> None:
    """Telemetry is never allowed to be the reason a build fails."""

    generate = build_ollama_generate_fn(base_url="http://x", model_tag="qwen3:8b")
    with mock.patch("requests.post", return_value=_Response("hello")):
        assert generate("write a file") == "hello"
    assert captured == []


def test_an_emitter_that_raises_does_not_break_the_build() -> None:
    generate = build_ollama_generate_fn(
        base_url="http://x", model_tag="qwen3:8b", source_context={"surface": "api"}
    )
    with mock.patch(
        "core.agent_runtime.builder.controller.emit_runtime_event",
        side_effect=RuntimeError("event bus down"),
    ), mock.patch("requests.post", return_value=_Response("hello")):
        assert generate("write a file") == "hello"


# --------------------------------------------------------------------------------------
# Bounded
# --------------------------------------------------------------------------------------


def test_the_budget_stops_the_build_instead_of_running_65_more_calls(captured) -> None:
    budget = BuilderGenerationBudget(total_seconds=10.0)
    generate = build_ollama_generate_fn(
        base_url="http://x", model_tag="qwen3:8b",
        source_context={"surface": "api"}, budget=budget,
    )
    with mock.patch("requests.post", return_value=_Response("x")):
        assert generate("first") == "x"
        # Age the budget past its ceiling without waiting for it.
        budget.started_monotonic -= 20.0
        with mock.patch("requests.post", side_effect=AssertionError("called past the ceiling")):
            assert generate("second") == ""

    assert budget.exhausted()
    assert "wall-clock budget" in budget.exhausted_reason
    assert any(
        event.get("reason") == "builder_budget_exhausted" for event in captured
    ), "the ceiling must be announced, not silent"


def test_a_calls_timeout_is_clipped_to_what_is_left() -> None:
    """Otherwise the last call can overrun the ceiling by a further 300 seconds."""

    budget = BuilderGenerationBudget(total_seconds=900.0)
    assert budget.call_timeout() == pytest.approx(300.0)

    budget.started_monotonic -= 880.0
    assert 0 < budget.call_timeout() <= 21.0

    budget.started_monotonic -= 100.0
    assert budget.exhausted()


def test_the_default_ceiling_is_far_below_the_unbounded_worst_case() -> None:
    """65 sequential calls at the 300s per-call timeout is 5.4 hours."""

    from core.agent_runtime.builder.controller import (
        _BUILDER_CALL_TIMEOUT_SECONDS,
        _BUILDER_TOTAL_WALL_CLOCK_SECONDS,
    )

    unbounded_worst_case = 65 * _BUILDER_CALL_TIMEOUT_SECONDS
    assert unbounded_worst_case / 10 > _BUILDER_TOTAL_WALL_CLOCK_SECONDS


def test_a_build_stopped_by_the_ceiling_is_not_reported_as_success() -> None:
    """A completion claim with nothing behind it is the failure this project keeps removing.

    Behavioural, not a source-text assertion. The first version of this test checked that
    `succeeded = False` appeared in the source, and a sabotage that changed the guard to `if False:`
    left that text untouched and passed — so the test proved nothing about the branch being live.
    """

    from core.agent_runtime.builder.controller import build_finished_successfully

    finished = dict(files_written=["main.py"], tests_ran=True, tests_passed=True)

    assert build_finished_successfully(**finished) is True
    assert build_finished_successfully(**finished, budget_exhausted_reason="") is True
    assert (
        build_finished_successfully(
            **finished, budget_exhausted_reason="wall-clock budget of 900s spent after 40 calls"
        )
        is False
    ), "a build the ceiling stopped is not a build that finished"

    # And the pre-existing rules still hold on their own.
    assert build_finished_successfully(files_written=[], tests_ran=False, tests_passed=False) is False
    assert (
        build_finished_successfully(files_written=["a.py"], tests_ran=True, tests_passed=False)
        is False
    )
    assert (
        build_finished_successfully(files_written=["a.py"], tests_ran=False, tests_passed=False)
        is True
    )


def test_the_operator_is_told_what_was_written_before_the_ceiling() -> None:
    from core.agent_runtime.builder.controller import early_stop_note

    note = early_stop_note(
        "builder wall-clock budget of 900s spent after 40 generation(s)",
        files_written=["main.py", "test_main.py"],
    )
    assert "Stopped early." in note
    assert "2 file(s) were written" in note
    assert "40 generation(s)" in note

    assert early_stop_note("", files_written=["main.py"]) == ""
    assert early_stop_note("   ", files_written=[]) == ""


def test_the_report_is_wired_to_that_decision() -> None:
    """The named function is worth nothing if `_run_model_build` computes its own answer."""

    import inspect

    from core.agent_runtime.builder import controller

    source = inspect.getsource(controller._run_model_build)
    assert "succeeded = build_finished_successfully(" in source
    assert "note = early_stop_note(" in source
    assert "budget_exhausted_reason=generation_budget.exhausted_reason" in source


# --------------------------------------------------------------------------------------
# The checkpoint deadline
# --------------------------------------------------------------------------------------


class TestCheckpointDeadline:
    @pytest.fixture(autouse=True)
    def _isolated_db(self, tmp_path):
        import core.runtime_continuity as rc
        from storage.migrations import run_migrations

        db_path = tmp_path / "runtime-continuity.db"
        run_migrations(db_path=db_path)
        rc.configure_runtime_continuity_db_path(str(db_path))
        rc.reset_runtime_continuity_state()
        yield rc
        rc.reset_runtime_continuity_state()
        rc.configure_runtime_continuity_db_path(None)

    @staticmethod
    def _running_checkpoint(rc, session_id: str) -> str:
        checkpoint = rc.create_runtime_checkpoint(
            session_id=session_id,
            request_text="build a bot",
            source_context={"runtime_session_id": session_id},
            task_class="system_design",
        )
        return str(checkpoint["checkpoint_id"])

    def test_startup_still_closes_every_running_checkpoint(self, _isolated_db) -> None:
        """The process that owned those rows is gone, so none of them can be progressing."""

        rc = _isolated_db
        self._running_checkpoint(rc, "s1")
        assert rc.mark_stale_runtime_checkpoints_interrupted() == 1

    def test_a_checkpoint_that_is_still_moving_is_not_closed(self, _isolated_db) -> None:
        """The deadline is measured from `updated_at`, so progress keeps resetting it."""

        rc = _isolated_db
        self._running_checkpoint(rc, "s2")
        assert rc.mark_stale_runtime_checkpoints_interrupted(older_than_seconds=900.0) == 0

    def test_a_checkpoint_past_its_deadline_is_closed(self, _isolated_db) -> None:
        rc = _isolated_db
        checkpoint_id = self._running_checkpoint(rc, "s3")

        conn = rc._conn()
        try:
            conn.execute(
                "UPDATE runtime_checkpoints SET updated_at = '2020-01-01T00:00:00+00:00' "
                "WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            conn.commit()
        finally:
            conn.close()

        assert rc.mark_stale_runtime_checkpoints_interrupted(older_than_seconds=900.0) == 1
        assert rc.get_runtime_checkpoint(checkpoint_id)["status"] == "interrupted"

    def test_the_reason_says_which_rule_closed_it(self, _isolated_db) -> None:
        """"Runtime stopped" is false for a deadline sweep — the runtime is still up."""

        rc = _isolated_db
        checkpoint_id = self._running_checkpoint(rc, "s4")
        conn = rc._conn()
        try:
            conn.execute(
                "UPDATE runtime_checkpoints SET updated_at = '2020-01-01T00:00:00+00:00' "
                "WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            conn.commit()
        finally:
            conn.close()

        rc.mark_stale_runtime_checkpoints_interrupted(older_than_seconds=900.0)
        failure = str(rc.get_runtime_checkpoint(checkpoint_id)["failure_text"])
        assert "no progress" in failure and "15 minutes" in failure

    def test_the_sweep_runs_on_a_turn_not_only_at_startup(self) -> None:
        """Startup was the only caller, so a task that wedged after it never cleared."""

        import inspect

        from apps import vool_agent

        assert "sweep_stale_checkpoints_if_due()" in inspect.getsource(
            vool_agent.VoolAgent.run_once
        )

    def test_the_sweep_is_throttled(self) -> None:
        import core.runtime_continuity as rc

        rc._last_sweep_monotonic = 0.0
        with mock.patch.object(
            rc, "mark_stale_runtime_checkpoints_interrupted", return_value=7
        ) as swept:
            assert rc.sweep_stale_checkpoints_if_due(now_monotonic=1000.0) == 7
            assert rc.sweep_stale_checkpoints_if_due(now_monotonic=1010.0) == 0
            assert rc.sweep_stale_checkpoints_if_due(now_monotonic=1400.0) == 7
        assert swept.call_count == 2
        rc._last_sweep_monotonic = 0.0

    def test_a_broken_sweep_never_fails_the_turn(self) -> None:
        import core.runtime_continuity as rc

        rc._last_sweep_monotonic = 0.0
        with mock.patch.object(
            rc, "mark_stale_runtime_checkpoints_interrupted", side_effect=RuntimeError("db locked")
        ):
            assert rc.sweep_stale_checkpoints_if_due(now_monotonic=2000.0) == 0
        rc._last_sweep_monotonic = 0.0
