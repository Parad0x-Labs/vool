"""A turn's remote-call count must be complete no matter which thread made the call.

`remote_fetch_scope_active` documents the guarantee the whole privacy claim rests on: inside the
scope, zero is a COMPLETE account of the turn's remote calls, so a zero can prove a negative.

That guarantee was false. `_REMOTE_FETCH_ATTEMPTS` held an int, and `run_live_data_plan` fetches
inside a ThreadPoolExecutor. A pool thread starts with an EMPTY context, so the fetch neither saw
the turn's scope nor could report into it -- and even with the context copied, `.set()` on an int
rebinds only the worker's own copy. Reproduced live on 2026-08-13: two never-queried cities
returned distinct current conditions while the trace reported `web_calls: 0`.

The repair is ownership, not arithmetic: the ContextVar holds a per-scope ledger OBJECT, and the
pool copies the turn's context into each task. Every context that inherits the binding mutates the
same tally; two concurrent turns own two ledgers and cannot see each other.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import copy_context

from core.remote_fetch_policy import (
    note_remote_fetch_attempt,
    remote_fetch_attempt_count,
    remote_fetch_policy_scope,
    remote_fetch_scope_active,
)


def test_a_main_thread_fetch_is_counted() -> None:
    with remote_fetch_policy_scope({}):
        note_remote_fetch_attempt()
        assert remote_fetch_attempt_count() == 1


def test_a_worker_thread_fetch_reaches_the_owning_turn() -> None:
    """The exact defect: the fetch happens on a pool thread, the count is read on the main one."""

    with remote_fetch_policy_scope({}):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copy_context().run, note_remote_fetch_attempt).result()
        assert remote_fetch_attempt_count() == 1


def test_two_worker_fetches_in_one_turn_are_both_counted() -> None:
    with remote_fetch_policy_scope({}):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(copy_context().run, note_remote_fetch_attempt) for _ in range(2)]
            for future in futures:
                future.result()
        assert remote_fetch_attempt_count() == 2


def test_many_concurrent_worker_fetches_lose_nothing() -> None:
    """A lock-free increment would drop calls under contention; the tally is the privacy claim."""

    with remote_fetch_policy_scope({}):
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(copy_context().run, note_remote_fetch_attempt) for _ in range(200)]
            for future in futures:
                future.result()
        assert remote_fetch_attempt_count() == 200


def test_a_turn_that_fetched_nothing_still_reads_zero() -> None:
    with remote_fetch_policy_scope({}):
        assert remote_fetch_attempt_count() == 0
        assert remote_fetch_scope_active() is True


def test_a_worker_failure_does_not_lose_the_call_it_already_made() -> None:
    """The call left the machine before the error; the account must still show it."""

    def _fetch_then_fail() -> None:
        note_remote_fetch_attempt()
        raise TimeoutError("upstream timed out")

    with remote_fetch_policy_scope({}):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(copy_context().run, _fetch_then_fail)
            with suppress(TimeoutError):
                future.result()
        assert remote_fetch_attempt_count() == 1


def test_concurrent_turns_never_contaminate_each_other() -> None:
    """Two turns in flight at once, each in its own scope, each counting only its own calls."""

    seen: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def _turn(name: str, calls: int) -> None:
        with remote_fetch_policy_scope({}):
            barrier.wait(timeout=10)
            for _ in range(calls):
                note_remote_fetch_attempt()
            barrier.wait(timeout=10)
            seen[name] = remote_fetch_attempt_count()

    threads = [
        threading.Thread(target=_turn, args=("turn-a", 3)),
        threading.Thread(target=_turn, args=("turn-b", 7)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert seen == {"turn-a": 3, "turn-b": 7}


def test_a_later_turn_never_inherits_an_earlier_turn_s_calls() -> None:
    with remote_fetch_policy_scope({}):
        note_remote_fetch_attempt()
        note_remote_fetch_attempt()
        assert remote_fetch_attempt_count() == 2
    with remote_fetch_policy_scope({}):
        assert remote_fetch_attempt_count() == 0


def test_a_nested_scope_does_not_reopen_a_second_ledger() -> None:
    """`remote_fetch_turn_scope` defers to an outer boundary; the count must stay one turn's."""

    from core.remote_fetch_policy import remote_fetch_turn_scope

    with remote_fetch_policy_scope({}):
        note_remote_fetch_attempt()
        with remote_fetch_turn_scope({}):
            note_remote_fetch_attempt()
        assert remote_fetch_attempt_count() == 2


def test_outside_any_scope_a_call_is_not_tallied_into_a_shared_total() -> None:
    """Out of scope there is no turn to report into, and a process-wide total would be a lie.

    `remote_fetch_scope_active()` already tells a caller that a count read here proves nothing.
    """

    note_remote_fetch_attempt()

    assert remote_fetch_scope_active() is False
    assert remote_fetch_attempt_count() == 0


def test_the_live_data_pool_copies_the_turn_context_into_its_workers() -> None:
    """Without this the ledger, the forbidden flag and the scope marker are all invisible."""

    import inspect

    from core.agent_runtime import live_data_runner

    source = inspect.getsource(live_data_runner.run_live_data_plan)

    assert "copy_context()" in source, "pool tasks must inherit the turn's context"
    assert "pool.submit(" in source


def test_the_real_plan_runner_reports_its_workers_fetches_into_the_turn(monkeypatch) -> None:
    """Drive the PRODUCTION pool, not a hand-rolled one.

    The source assertion above is a proxy; this is the behaviour it stands for. Every subtask runs
    on a pool thread and reports one call, and the parent turn must see all of them.
    """

    from core.agent_runtime import live_data_runner
    from core.agent_runtime.live_data_plan import (
        LiveDataPlan,
        SubtaskLifecycle,
        SubtaskOutcome,
        _weather_subtask,
    )

    def _fake_run_one(subtask, *, timeout_s):
        note_remote_fetch_attempt()
        return SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.SUCCEEDED, result={"ok": True})

    monkeypatch.setattr(live_data_runner, "_run_one", _fake_run_one)

    # Built by the production factory, so the subtask shape is the one the runner really gets.
    subtasks = tuple(_weather_subtask("ctx-propagation", f"city-{index}") for index in range(4))
    plan = LiveDataPlan(
        plan_id="ctx-propagation",
        attempt_id="a1",
        subtasks=subtasks,
        original_request="weather in four cities",
    )

    with remote_fetch_policy_scope({}):
        outcomes = live_data_runner.run_live_data_plan(plan)
        counted = remote_fetch_attempt_count()

    assert len(outcomes) == 4
    assert counted == 4, "worker fetches did not reach the owning turn"
