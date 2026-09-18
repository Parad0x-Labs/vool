"""P1 request-context isolation: context must restore its previous value.

The confirmed defect this file pins: tests/foundation/test_r5_pipeline_order.py
bound ``req:http:r5-empty-sr`` through the raw ``set_request_context`` and never
restored it, so every later demand-ownership turn on the same context was
re-identified (``agent.run_once`` binds its own identity ONLY when none is
present) and failed closed against a foreign request id — test order changed
product truth. The laws under test:

1. The owning seam (``bound_request_context`` / ``bound_execution_context``)
   restores the PREVIOUS value on normal exit AND exception, nests LIFO, and is
   context-local (threads/tasks never share it).
2. A raw bind that bypasses the seam cannot poison the next test: the shared
   harness (``request_turn_context_isolation`` in tests/conftest.py) restores
   the identity family at the test boundary.
3. An EMPTY request-id bind must restore too — a cleared value must never
   permanently shadow an outer scope's binding.
4. Retry/resume identity: re-binding the same request id presents the SAME
   identity; after a scope exits, no stale identity survives into the resume.
"""
from __future__ import annotations

import asyncio
import threading

LEAKED_R5_ID = "req:http:r5-empty-sr"


# ---------------------------------------------------------------------------
# 1. The exact poisoned sequence, in miniature: a raw R5-style bind with no
#    cleanup, then a probe test. RED before the repair (probe sees the foreign
#    id), GREEN after (harness restores at the test boundary).
# ---------------------------------------------------------------------------


def test_a_raw_r5_style_bind_takes_effect_without_cleanup():
    """Replay of the R5 leak, verbatim: a raw setter with no restore. The bind
    MUST be visible mid-test — otherwise the probe below could pass vacuously
    without proving that restoration (not a broken setter) is what changed."""
    from core.semantic.semantic_admissions import current_request_id, set_request_context

    set_request_context(LEAKED_R5_ID)  # noqa: the deliberate bypass under test
    try:
        assert current_request_id() == LEAKED_R5_ID
    finally:
        # Deliberately NO restore here — this test simulates the pre-repair R5
        # test exactly; the shared harness owns the boundary restoration.
        pass


def test_b_next_test_observes_no_foreign_request_id():
    """The poisoned probe: whatever test ran before, this test must never
    inherit a foreign request identity. RED in the poisoned suite run."""
    from core.semantic.semantic_admissions import current_request_id

    assert current_request_id() != LEAKED_R5_ID


def test_reverse_order_control_probe_passes_alone():
    """Reverse-order control: the probe carries no leak of its own — run
    without the leaker ahead of it, it observes a clean context."""
    from core.semantic.semantic_admissions import current_request_id

    assert current_request_id() != LEAKED_R5_ID


# ---------------------------------------------------------------------------
# 2. The owning seam: restore on normal exit, exception, nesting.
# ---------------------------------------------------------------------------


def test_scoped_request_bind_restores_previous_on_normal_exit():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    with bound_request_context("req:outer") as bound:
        assert bound == "req:outer"
        assert current_request_id() == "req:outer"
    assert current_request_id() == ""


def test_scoped_request_bind_restores_previous_on_exception():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    sentinel = RuntimeError("unwinding")
    raised = False
    try:
        with bound_request_context("req:doomed"):
            assert current_request_id() == "req:doomed"
            raise sentinel
    except RuntimeError as exc:
        raised = exc is sentinel
    assert raised, "the scoped bind must never swallow the exception"
    assert current_request_id() == "", "exception unwinding must restore the previous value"


def test_nested_request_binds_restore_lifo():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    with bound_request_context("req:a"):
        with bound_request_context("req:b"):
            with bound_request_context("req:c") as innermost:
                assert innermost == "req:c"
                assert current_request_id() == "req:c"
            assert current_request_id() == "req:b", "inner exit restores the middle binding"
        assert current_request_id() == "req:a", "middle exit restores the outer binding"
    assert current_request_id() == "", "outer exit restores the pre-scope value"


def test_scoped_bind_restores_exactly_despite_intervening_raw_set():
    """Token discipline: a raw re-set inside the scope (a simulated bypasser)
    must not defeat restoration — the previous value is restored EXACTLY, not
    the last value someone happened to set."""
    from core.semantic.semantic_admissions import bound_request_context, current_request_id, set_request_context

    with bound_request_context("pre"):
        with bound_request_context("outer-req"):
            set_request_context("sneaker-req")  # bypasses the seam, no restore
            assert current_request_id() == "sneaker-req"
        # Token reset restores the value from BEFORE the scope's own set: the
        # raw inner set is undone with the scope, and the OUTER binding ("pre")
        # — not the sneaker, not a cleared default — is what survives.
        assert current_request_id() == "pre", "scope exit undoes even raw inner sets"
        assert current_request_id() != "sneaker-req"
    assert current_request_id() == ""


def test_scoped_execution_bind_restores_and_nests():
    from core.semantic.semantic_admissions import (
        bound_execution_context,
        current_execution_identity,
    )

    outer = {"execution_id": "ex:outer", "generation": 1, "runtime_epoch": "e1"}
    inner = {"execution_id": "ex:inner", "generation": 2, "runtime_epoch": "e1"}
    with bound_execution_context(outer):
        assert current_execution_identity() == outer
        with bound_execution_context(inner):
            assert current_execution_identity() == inner
        assert current_execution_identity() == outer, "inner exit restores the outer fence"
    assert current_execution_identity() is None


def test_scoped_execution_bind_restores_on_exception():
    from core.semantic.semantic_admissions import (
        bound_execution_context,
        current_execution_identity,
    )

    try:
        with bound_execution_context({"execution_id": "ex:x", "generation": 1, "runtime_epoch": "e"}):
            raise ValueError("boom")
    except ValueError:
        pass
    assert current_execution_identity() is None


# ---------------------------------------------------------------------------
# 3. Empty request id: an empty bind must restore too, and must never
#    permanently shadow an outer scope's binding.
# ---------------------------------------------------------------------------


def test_empty_request_id_binds_and_restores():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    with bound_request_context("req:before-empty"):
        with bound_request_context("") as empty:
            assert empty == ""
            assert current_request_id() == ""
        assert current_request_id() == "req:before-empty", (
            "an empty inner bind must restore the outer binding, never strand the scope empty"
        )
    assert current_request_id() == ""


# ---------------------------------------------------------------------------
# 4. Concurrency: threads and asyncio tasks are isolated by the ContextVar.
# ---------------------------------------------------------------------------


def test_two_threads_bind_without_cross_contamination():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    with bound_request_context("req:main-thread"):
        start = threading.Barrier(2)
        seen: dict[str, str] = {}

        def _worker(name: str, req: str) -> None:
            with bound_request_context(req):
                start.wait(timeout=10)
                seen[name] = current_request_id()

        t1 = threading.Thread(target=_worker, args=("t1", "req:thread-one"))
        t2 = threading.Thread(target=_worker, args=("t2", "req:thread-two"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert seen == {"t1": "req:thread-one", "t2": "req:thread-two"}, (
            "a concurrent thread's bind must never cross into another thread"
        )
        assert current_request_id() == "req:main-thread", (
            "worker binds must never leak back into the spawning context"
        )
    assert current_request_id() == ""


def test_asyncio_tasks_bind_without_cross_contamination():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    async def _main() -> tuple[str, dict[str, str]]:
        with bound_request_context("req:main-task"):
            seen: dict[str, str] = {}

            async def _worker(name: str, req: str) -> None:
                with bound_request_context(req):
                    await asyncio.sleep(0)
                    seen[name] = current_request_id()

            await asyncio.gather(_worker("w1", "req:task-one"), _worker("w2", "req:task-two"))
            return current_request_id(), seen

    main_seen, task_seen = asyncio.run(_main())
    assert task_seen == {"w1": "req:task-one", "w2": "req:task-two"}
    assert main_seen == "req:main-task", "task binds must never leak into the event-loop caller"


# ---------------------------------------------------------------------------
# 5. Retry/resume identity.
# ---------------------------------------------------------------------------


def test_retry_rebinds_present_the_same_identity_and_resume_starts_clean():
    from core.semantic.semantic_admissions import bound_request_context, current_request_id

    with bound_request_context("req:retry-target") as first:
        pass
    assert current_request_id() == "", "a finished attempt must leave no stale identity"
    with bound_request_context("req:retry-target") as second:
        assert first == second == "req:retry-target", (
            "a retry of the same request must present the SAME identity"
        )
    assert current_request_id() == "", "and on resume the context is clean again"
