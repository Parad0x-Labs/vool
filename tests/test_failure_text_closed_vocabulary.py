"""R3 (AUD-20260829-003): served failure text is closed-vocabulary.

The defect this closes: `core/conductor/scheduler.py` stored a failed node's reason as
`f"{type(exc).__name__}: {exc}"[:200]`, discarding the traceback, and
`core/conductor/compose.reader_facing_reason` stripped the exception-class prefix and returned
whatever text was left AS-IS -- default-open. A `_weather_run` `ImportError` naming the internal
symbol `_weather_subtask` and an absolute filesystem path reached the operator verbatim (evidence
E001: "could not be answered: cannot import name '_weather_subtask' from
'core.agent_runtime.live_data_plan' (/Users/.../live_data_plan.py)").

The fix, end to end:
  - `core/conductor/node.py`: `NodeFailureCode` (closed vocabulary) + `NodeOutcome.receipt_id`,
    `failure_detail_full`, `failure_traceback` (untruncated text + full traceback, receipt-store
    only, never read by the renderer).
  - `core/conductor/scheduler.py`: every failure site assigns a code and mints a receipt id;
    the three sites that catch a real exception (`_run_one`'s render/node catches,
    `run_conductor_plan`'s pool-fault catch) also capture the FULL text and traceback.
  - `core/conductor/compose.py`: `reason_for_outcome` is the fail-closed renderer -- codes that
    can only originate from a caught exception map to a fixed generic phrase plus the receipt id,
    and NEVER consult the outcome's text at all. `reader_facing_reason` (the tier-2 fallback for
    outcomes built without a code) inverts the old default-open polarity: unrecognized text that
    still looks like leaked internals (an embedded exception-class shape, a filesystem path, an
    internal module path) renders a generic line instead of being passed through.

This file tests the PROPERTY over a set of raised exception types (not one hard-coded case, per
project law 0.4/0.2) by driving the REAL `run_conductor_plan` -> `compose_answer` pipeline, the
same pipeline the served app uses.
"""

from __future__ import annotations

import re

import pytest

from core.conductor.graph import build_graph
from core.conductor.node import ConductorNode, NodeFailureCode, NodeOutcome, node_receipt
from core.conductor.planner import ConductorPlan
from core.conductor.registry import (
    NodeContext,
    OperationSpec,
    register_operation,
    unregister_operation,
)
from core.conductor.scheduler import run_conductor_plan
from tests.conductor_product import compose_product

# ---------------------------------------------------------------------------------------------
# Leak detector, independent of the one inside compose.py -- this file must not simply re-check
# that compose.py agrees with itself.
# ---------------------------------------------------------------------------------------------

_FORBIDDEN_SUBSTRINGS = ("site-packages", "/Users/", "/home/", "core.agent_runtime", "core.conductor")
_EXCEPTION_CLASS_NAME_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b")
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])/[\w.-]+(?:/[\w.-]+)+")


def _assert_clean(served_text: str, *, forbidden_extra: tuple[str, ...] = ()) -> None:
    for marker in _FORBIDDEN_SUBSTRINGS + forbidden_extra:
        assert marker not in served_text, f"leaked {marker!r} into served text: {served_text!r}"
    match = _EXCEPTION_CLASS_NAME_RE.search(served_text)
    assert match is None, f"leaked exception class name {match.group() if match else ''!r}: {served_text!r}"
    match = _ABSOLUTE_PATH_RE.search(served_text)
    assert match is None, f"leaked filesystem path {match.group() if match else ''!r}: {served_text!r}"


# ---------------------------------------------------------------------------------------------
# A property over a SET of raised exception types -- the whole point is that this is not a
# per-shape allowlist. Each raises a message deliberately shaped to leak something specific.
# ---------------------------------------------------------------------------------------------


class _WeirdInternalError(RuntimeError):
    """An exotic, never-before-seen exception TYPE -- proves the fix is not keyed to a class name."""


_RUN_RAISERS: dict[str, Exception] = {
    "importerror_with_symbol_and_path": ImportError(
        "cannot import name '_weather_subtask' from 'core.agent_runtime.live_data_plan' "
        "(/Users/operator/Desktop/vool-checkout/core/agent_runtime/live_data_plan.py)"
    ),
    "attributeerror_with_dotted_module": AttributeError(
        "module 'core.conductor.registry' has no attribute '_internal_table'"
    ),
    "keyerror_with_field_name": KeyError("temperature_c"),
    "valueerror_plain_message": ValueError("comparison needs at least two dependencies; got 1"),
    "typeerror_bad_shape": TypeError("weather_lookup returned NoneType, expected a mapping"),
    "runtimeerror_pool_style": RuntimeError("pool died"),
    "exotic_unregistered_exception_type": _WeirdInternalError(
        "internal state desync at /private/tmp/vool-scratch/session_9f2/state.db"
    ),
    "message_mentions_site_packages": RuntimeError(
        "handler crashed inside "
        "/Users/operator/vool/.venv/lib/python3.11/site-packages/httpx/_client.py line 900"
    ),
}


@pytest.fixture
def _leaky_op():
    """One operation per test, registered fresh so parametrize cases never share raise state."""
    name = "r3_probe_leaky_run_op"
    holder: dict[str, Exception] = {}

    def _run(node, ctx):
        raise holder["exc"]

    register_operation(
        OperationSpec(
            name=name,
            description="probe that raises whatever the test wants",
            expand_arguments=lambda *a, **k: [],
            run=_run,
            render=lambda node, result: "",
        ),
        replace=True,
    )
    try:
        yield name, holder
    finally:
        unregister_operation(name)


def _run_single_node_plan(op_name: str, request_text: str = "what time is in rome now"):
    node = ConductorNode(node_id="probe_node", operation=op_name, request_text=request_text)
    plan = ConductorPlan(plan_id="r3-probe-plan", original_request=request_text, graph=build_graph([node]))
    outcomes = run_conductor_plan(plan, context=NodeContext())
    return plan, outcomes, outcomes[0]


@pytest.mark.parametrize("case_name", sorted(_RUN_RAISERS))
def test_no_raised_exception_shape_leaks_through_the_served_answer(_leaky_op, case_name):
    """The property: for ANY exception type raised inside a node's `run`, the SERVED text is
    clean. Not parametrized on a fixed string -- on the raised exception's TYPE and content."""
    op_name, holder = _leaky_op
    exc = _RUN_RAISERS[case_name]
    holder["exc"] = exc

    plan, outcomes, outcome = _run_single_node_plan(op_name)
    composed = compose_product(plan, outcomes)

    # The receipt/log store side MUST still carry the full story -- this is not "delete the
    # evidence", it is "don't print it". Restores exactly what AUD-20260829-003 found missing.
    assert outcome.failure_code is NodeFailureCode.NODE_EXCEPTION
    assert outcome.receipt_id, "every failing node must carry an opaque receipt id"
    assert type(exc).__name__ in outcome.failure_detail_full
    assert str(exc) in outcome.failure_detail_full
    assert outcome.failure_traceback, "the full traceback must be captured for the receipt store"
    assert "Traceback (most recent call last)" in outcome.failure_traceback

    receipt = node_receipt(outcome, plan_id=plan.plan_id)
    assert receipt["receipt_id"] == outcome.receipt_id
    assert receipt["failure_detail_full"] == outcome.failure_detail_full
    assert receipt["failure_traceback"] == outcome.failure_traceback

    # The served side MUST NOT carry any of it.
    _assert_clean(composed.text)
    assert outcome.receipt_id in composed.text, "the receipt id itself should reach the reader"
    # Never the raw message content, whatever it said.
    assert str(exc) not in composed.text


def test_a_render_failure_is_also_closed_vocabulary(_leaky_op):
    """The SAME property, but for the render() catch site (NodeFailureCode.RENDER_FAILED), which
    scheduler.py handles in a separate except block from the run()/operation-not-found one."""
    name = "r3_probe_leaky_render_op"
    register_operation(
        OperationSpec(
            name=name,
            description="probe whose render() raises",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: {},
            render=lambda node, result: (_ for _ in ()).throw(
                ValueError("render blew up reading /Users/operator/secret/config.yaml")
            ),
        ),
        replace=True,
    )
    try:
        plan, outcomes, outcome = _run_single_node_plan(name)
        composed = compose_product(plan, outcomes)

        assert outcome.failure_code is NodeFailureCode.RENDER_FAILED
        assert outcome.receipt_id
        assert "/Users/operator/secret/config.yaml" in outcome.failure_detail_full
        assert outcome.failure_traceback

        _assert_clean(composed.text)
        assert "config.yaml" not in composed.text
    finally:
        unregister_operation(name)


def test_missing_result_fields_shows_field_names_but_never_raw_exception_text():
    """RESULT_MISSING_FIELDS is a SAFE code (the field names are developer-declared, not
    exception-derived) -- this pins that it still renders informatively, unlike the
    exception-derived codes."""
    name = "r3_probe_missing_fields_op"
    register_operation(
        OperationSpec(
            name=name,
            description="probe returning an incomplete result",
            expand_arguments=lambda *a, **k: [],
            run=lambda node, ctx: {},
            render=lambda node, result: "unreachable",
            required_result_fields=("temperature_c",),
        ),
        replace=True,
    )
    try:
        # `required_result_fields` must be declared on the NODE too -- `_run_one` checks
        # `node.required_result_fields`, which the real planner copies from the chosen
        # OperationSpec when it resolves a clause; this test builds the node directly, so it must
        # copy it explicitly or the check is vacuously satisfied against an empty tuple.
        node = ConductorNode(
            node_id="probe_node",
            operation=name,
            request_text="what time is in rome now",
            required_result_fields=("temperature_c",),
        )
        plan = ConductorPlan(plan_id="r3-probe-plan", original_request="probe", graph=build_graph([node]))
        outcomes = run_conductor_plan(plan, context=NodeContext())
        outcome = outcomes[0]
        composed = compose_product(plan, outcomes)

        assert outcome.failure_code is NodeFailureCode.RESULT_MISSING_FIELDS
        _assert_clean(composed.text)
        assert "temperature_c" in composed.text, "the missing field name is safe and should still show"
    finally:
        unregister_operation(name)


# ---------------------------------------------------------------------------------------------
# Unit-level pin on the fail-closed dispatch itself: an exception-derived code must NEVER expose
# outcome text, even text an attacker (or a bug) deliberately planted there.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [NodeFailureCode.NODE_EXCEPTION, NodeFailureCode.RENDER_FAILED, NodeFailureCode.SCHEDULER_FAULT],
)
def test_exception_derived_codes_never_consult_failure_reason_text(code):
    """Direct unit pin, independent of the integration tests above: for the three codes that can
    only originate from a caught exception, `reason_for_outcome` must not read `failure_reason` /
    `failure_detail_full` AT ALL -- planting an obviously dangerous string there and asserting it
    never appears is the strongest form of this property this test suite can express."""
    from core.conductor.compose import reason_for_outcome

    node = ConductorNode(node_id="n", operation="whatever", request_text="whatever")
    poison = "ImportError: /Users/nobody/should/see/this/core.agent_runtime.leak (site-packages)"
    outcome = NodeOutcome(
        node=node,
        failure_code=code,
        receipt_id="ndf-unittest0000",
        failure_reason=poison,
        failure_detail_full=poison,
    )

    rendered = reason_for_outcome(outcome)

    assert poison not in rendered
    assert "/Users/" not in rendered
    assert "site-packages" not in rendered
    assert "core.agent_runtime" not in rendered
    assert "ndf-unittest0000" in rendered


def test_unresolved_and_dependency_failed_codes_show_their_safe_detail():
    """The other half of the same pin: codes whose detail is developer/planner-authored (never
    exception text) ARE allowed to show it -- this is not a blanket ban on all detail, only on
    exception-derived detail. Losing this would be a quality regression nobody asked for."""
    from core.conductor.compose import reason_for_outcome

    node = ConductorNode(
        node_id="n",
        operation="unresolved",
        request_text="what is the airspeed of an unladen swallow",
        unresolved_reason="nothing in this runtime can look up airspeed trivia",
    )
    outcome = NodeOutcome(node=node, failure_code=NodeFailureCode.UNRESOLVED)
    assert reason_for_outcome(outcome) == "nothing in this runtime can look up airspeed trivia"

    dep_node = ConductorNode(node_id="d", operation="comparison", request_text="which is warmer")
    dep_outcome = NodeOutcome(
        node=dep_node,
        failure_code=NodeFailureCode.DEPENDENCY_FAILED,
        failure_reason="depends on rome_weather, which did not succeed",
    )
    assert reason_for_outcome(dep_outcome) == "depends on rome_weather, which did not succeed"


# ---------------------------------------------------------------------------------------------
# Tier 2 (`reader_facing_reason`, the fallback for outcomes built with no NodeFailureCode --
# most commonly a caller written before this field existed). This is a NEW code path -- the old
# function returned unrecognized text as-is; this pins ONLY the new leak-detection addition, since
# every case `tests/test_v050_smoke_side_findings.py` already exercises was already safe via the
# exception-class-prefix strip or the marker table and would pass even without this addition.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dangerous_text",
    [
        # No leading exception-class prefix (so the old prefix-strip never touched it) and no
        # marker-table hit -- the shape this function had no defense against before R3.
        "lookup failed at /Users/operator/Desktop/vool-checkout/core/agent_runtime/live_data_plan.py",
        "handler bound to core.agent_runtime.live_data_plan._weather_subtask could not resolve",
        "traceback frame in /private/tmp/build/.venv/lib/python3.11/site-packages/httpx/_client.py",
        # An exception-class shape embedded mid-string, unprefixed at position 0.
        "adapter wrapper failed: KeyError: 'temperature_c'",
    ],
)
def test_reader_facing_reason_fails_closed_on_text_with_no_leading_exception_prefix(dangerous_text):
    from core.conductor.compose import reader_facing_reason

    rewritten = reader_facing_reason(dangerous_text)

    assert dangerous_text not in rewritten
    for marker in _FORBIDDEN_SUBSTRINGS:
        assert marker not in rewritten, f"leaked {marker!r} via the tier-2 fallback: {rewritten!r}"
    assert rewritten.strip()


@pytest.mark.parametrize(
    "bare_name",
    [
        # A string that is ONLY an exception-class name carries no reader-facing content at
        # all -- `_run_single` always prefixes these with the class name, but a caller can
        # hand the bare token straight to the composer (measured: the planned-task failure
        # path did exactly this with "ConnectionError"). Fail closed to the generic phrase.
        "ConnectionError",
        "RuntimeError",
        "TimeoutError",
    ],
)
def test_reader_facing_reason_renders_a_bare_exception_name_generic(bare_name):
    from core.conductor.compose import reader_facing_reason

    rewritten = reader_facing_reason(bare_name)
    assert rewritten == "could not be completed"
