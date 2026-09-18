"""SCALPEL fixture repair, failure class Activity/7 follow-up, 2026-08-06.

Found live: driving the real isolated-daemon fixture, a turn that was refused before nomination
ever started (an unresolvable pinned model) rendered a correct, honest chat answer but left ZERO
record in the durable event store -- `_blocked_decision` is a separate early-return path from the
main `run_stepped_audit` body, and the Phase 1.7 persistence call lived only at the end of that
body. An early blocked exit is exactly the kind of turn an operator most needs a durable trail for
(a vendor-collision refusal, a provider that could not be reached at all) -- it must not be the one
case that leaves nothing behind.

SCALPEL final-tip verification follow-up, 2026-08-06: driving the real isolated daemon at
46b946dc found the persistence fix above real but incomplete -- the record it wrote carried
`terminal_state`/`target`/`blocked_reason`/`model_routing`, but neither the fixture/checkout
identity (source SHA, daemon SHA, resolved workspace/target) nor measured timing that a
proven/unproven candidate's own durable record already carries. An operator reading Activity for a
BLOCKED turn could not tell which source tree or which daemon build produced it, nor how long it
took. `test_an_early_blocked_exit_carries_fixture_identity_and_timing` below is the regression for
that gap; `test_sabotage_dropping_fixture_identity_and_timing_is_caught` proves the specific dict
keys are load-bearing, not incidental.

ARGUS repair D5, 2026-08-06: the fix above still persisted the record through a SYNTHETIC
`{"session_id": session_id}` context, not the turn's real `source_context`. Durable storage kept
working (`emit_runtime_event` resolves that off `runtime_session_id`/`session_id`, both present on
the synthetic dict), but `emit_runtime_event` ALSO reads `cancel_turn_id` (tagged onto the event as
`client_turn_id`) and `runtime_event_stream_id` (used to look up a live per-stream sink) directly
from `source_context` -- neither key exists on a one-field synthetic dict, so a BLOCKED event never
carried a client turn id and never reached a live-open Activity stream, only durable storage.
`test_an_early_blocked_exit_carries_turn_and_task_identity_and_reaches_the_live_stream` is the
regression for that gap, driven with a real `source_context` carrying `cancel_turn_id`, `turn_id`
(the same field `checkpoints.prepare_runtime_checkpoint` stamps on every real turn), and a
registered live stream sink.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime.stepped_audit import run_stepped_audit
from core.runtime_continuity import list_runtime_session_events
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink

_BLOCKED_DURABLE_CONTEXT = {
    "workspace_audit_evidence_collected": True,
    "workspace_audit_evidence": {
        "all_paths": ("x.py",),
        "inspected_paths": ("x.py",),
        "sources": {"x.py": "x = 1\n"},
        "workspace_root": "/tmp/blocked-durable-ws",
        "incomplete_files": (),
    },
    "requested_model": "some-unreachable-model",
}


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        memory_router=SimpleNamespace(
            _requested_model_manifest=lambda ctx: None,
        ),
        _execute_tool_intent=lambda *a, **k: None,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )


def _run_blocked(
    session_id: str, *, extra_context: dict | None = None, task_id: str | None = None
) -> tuple[object, list[dict]]:
    context = {"runtime_session_id": session_id, **_BLOCKED_DURABLE_CONTEXT, **(extra_context or {})}
    decision = run_stepped_audit(
        _agent(),
        task=SimpleNamespace(task_id=task_id if task_id is not None else f"task-{session_id}"),
        effective_input="Audit x.py. Do not modify anything.",
        source_context=context,
        session_id=session_id,
    )
    events = list_runtime_session_events(session_id, limit=200)
    detail_events = [e for e in events if e.get("event_type") == "audit_candidate_detail"]
    return decision, detail_events


def test_an_early_blocked_exit_still_reaches_durable_activity() -> None:
    decision, detail_events = _run_blocked("blocked-durable-1")
    assert decision is not None

    assert detail_events, "an early blocked exit left no durable record at all"
    assert detail_events[-1].get("terminal_state") == "blocked"
    assert detail_events[-1].get("blocked_reason")


def test_an_early_blocked_exit_carries_fixture_identity_and_timing() -> None:
    """SCALPEL final-tip verification, 2026-08-06: the same fixture-safety identity and measured
    timing a proven/unproven candidate's durable record carries must also survive a pre-nomination
    BLOCKED exit -- this is what the final-tip production drive found still missing."""
    _, detail_events = _run_blocked("blocked-durable-2")
    assert detail_events, "an early blocked exit left no durable record at all"
    record = detail_events[-1]

    fixture_identity = record.get("fixture_identity")
    assert isinstance(fixture_identity, dict) and fixture_identity, (
        "blocked record has no fixture_identity at all"
    )
    assert fixture_identity.get("daemon_checkout_sha"), (
        "blocked record does not name which daemon checkout produced it"
    )
    # This fixture resolves evidence (a real workspace_root and target), so the resolved workspace
    # identity must be present too -- not only the daemon's own SHA.
    assert fixture_identity.get("resolved_workspace_root") == "/tmp/blocked-durable-ws"

    timing = record.get("timing")
    assert isinstance(timing, dict) and timing, "blocked record has no timing at all"
    assert isinstance(timing.get("elapsed_seconds"), (int, float))
    assert timing["elapsed_seconds"] >= 0


def test_an_early_blocked_exit_carries_turn_and_task_identity_and_reaches_the_live_stream() -> None:
    """ARGUS repair D5: a BLOCKED exit's durable record must carry `client_turn_id` (from
    `cancel_turn_id`, the same field every other runtime event is tagged with) and `task_id` (the
    attempt identity `run_stepped_audit` was itself given -- no second ID system invented), AND the
    event must reach a LIVE, currently-registered Activity stream sink, not only durable storage."""
    session_id = "blocked-durable-live-stream"
    stream_id = "blocked-durable-live-stream-id"
    live_events: list[dict] = []
    register_runtime_event_sink(stream_id, live_events.append)
    try:
        decision, detail_events = _run_blocked(
            session_id,
            extra_context={"cancel_turn_id": "turn-known-123", "runtime_event_stream_id": stream_id},
            task_id="task-known-abc",
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision is not None
    assert detail_events, "an early blocked exit left no durable record at all"
    record = detail_events[-1]
    assert record.get("client_turn_id") == "turn-known-123", record
    assert record.get("task_id") == "task-known-abc", record

    live_detail_events = [e for e in live_events if e.get("event_type") == "audit_candidate_detail"]
    assert live_detail_events, (
        "the BLOCKED event reached durable storage but never reached the live-registered stream "
        "sink -- an operator watching Activity in real time would see nothing"
    )
    assert live_detail_events[-1].get("client_turn_id") == "turn-known-123"
    assert live_detail_events[-1].get("task_id") == "task-known-abc"
    assert live_detail_events[-1].get("terminal_state") == "blocked"


def test_sabotage_a_synthetic_context_drops_turn_identity_and_the_live_stream(monkeypatch) -> None:
    """ARGUS repair D5 sabotage: reverts the REAL `_persist_audit_detail` call inside
    `_blocked_decision` back to the pre-fix synthetic `{"session_id": ...}` context -- by wrapping
    the real function and discarding everything the caller passed except `session_id` -- and proves
    both `client_turn_id` and the live stream delivery disappear again."""
    import core.agent_runtime.stepped_audit as stepped_audit_module

    real_persist = stepped_audit_module._persist_audit_detail

    def sabotaged_persist(source_context, detail):
        # SABOTAGE: exactly the pre-D5 shape -- a synthetic one-field context, discarding
        # cancel_turn_id/runtime_event_stream_id the real turn actually carried.
        synthetic = {"session_id": str((source_context or {}).get("session_id") or "")}
        return real_persist(synthetic, detail)

    monkeypatch.setattr(stepped_audit_module, "_persist_audit_detail", sabotaged_persist)

    session_id = "blocked-durable-sabotage-stream"
    stream_id = "blocked-durable-sabotage-stream-id"
    live_events: list[dict] = []
    register_runtime_event_sink(stream_id, live_events.append)
    try:
        _decision, detail_events = _run_blocked(
            session_id,
            extra_context={"cancel_turn_id": "turn-known-999", "runtime_event_stream_id": stream_id},
            task_id="task-known-xyz",
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert detail_events, "sabotage setup broke durable persistence entirely"
    assert detail_events[-1].get("client_turn_id") is None, (
        "the real regression must fail once source_context is synthesized down to session_id only"
    )
    assert not any(e.get("event_type") == "audit_candidate_detail" for e in live_events), (
        "the real regression must fail once runtime_event_stream_id is dropped -- the event must "
        "not reach the live stream sink under the sabotaged (pre-D5) context"
    )


def test_an_early_blocked_exit_shows_measured_wall_time_in_the_chat_report() -> None:
    """ARGUS repair D9: the runtime measures `elapsed_seconds` for every BLOCKED exit (persisted to
    Activity since the earlier fix above), but the CHAT-FACING report never rendered it --
    `render_audit_report` prints `verdict.usage_line` for every terminal state, but `_blocked_
    decision` only ever populated it with a bare token receipt gated on `totals.get("calls")`,
    which is always empty for a pre-nomination BLOCKED exit (zero provider calls were ever made).
    The wall-time line must render regardless -- and no token count may be fabricated for a turn
    that made zero calls."""
    decision, _ = _run_blocked("blocked-durable-telemetry")
    report = str(getattr(decision, "output_text", "") or "")

    assert "Measured wall time:" in report, report
    assert "Largest context component:" in report, report
    assert "not available (no provider call was made this turn)" in report, report
    # Zero provider calls were ever made on this early-exit path -- no token count may appear.
    assert "tokens" not in report.lower() or "context component" in report.lower(), report
    assert "input_tokens" not in report and "prompt_tokens" not in report, report


def test_sabotage_reverting_blocked_telemetry_line_drops_wall_time(monkeypatch) -> None:
    """ARGUS repair D9 sabotage: reverts the REAL production `_blocked_telemetry_line` back to the
    pre-fix shape (a bare, calls-gated token receipt, never the wall-time/context lines) and proves
    the chat report loses "Measured wall time:" again."""
    import core.agent_runtime.stepped_audit as stepped_audit_module
    from core.token_usage_receipt import usage_receipt_line

    def sabotaged_telemetry_line(*, elapsed_seconds, totals):
        # SABOTAGE: the exact pre-D9 body.
        return usage_receipt_line(totals) if totals.get("calls") else ""

    monkeypatch.setattr(stepped_audit_module, "_blocked_telemetry_line", sabotaged_telemetry_line)

    decision, _ = _run_blocked("blocked-durable-telemetry-sabotage")
    report = str(getattr(decision, "output_text", "") or "")

    assert "Measured wall time:" not in report, (
        "the real regression must fail once _blocked_telemetry_line reverts to the pre-D9 shape -- "
        f"but the wall-time line is still present: {report!r}"
    )


def test_sabotage_dropping_fixture_identity_and_timing_is_caught(monkeypatch) -> None:
    """ARGUS repair D7, 2026-08-06: the original version of this test built a hand-written dict and
    asserted its own keys were absent -- it could never fail no matter what the real
    `_blocked_decision`/`_persist_audit_detail` code did, since it never called either one. Real
    production sabotage instead: monkeypatch the REAL `_persist_audit_detail` to strip
    `fixture_identity`/`timing` from whatever detail dict `_blocked_decision` actually built and
    passed it, run the real `run_stepped_audit` end to end, and prove the durable record comes back
    without them -- the exact pre-fix shape the tests above exist to rule out."""
    import core.agent_runtime.stepped_audit as stepped_audit_module

    real_persist = stepped_audit_module._persist_audit_detail

    def sabotaged_persist(source_context, detail):
        # SABOTAGE: strips exactly the two keys the fix added, then calls the real persistence
        # function with everything else intact -- proving those two keys, not the persistence
        # mechanism itself, are what the regression tests above depend on.
        stripped = {k: v for k, v in detail.items() if k not in ("fixture_identity", "timing")}
        return real_persist(source_context, stripped)

    monkeypatch.setattr(stepped_audit_module, "_persist_audit_detail", sabotaged_persist)

    _, detail_events = _run_blocked("blocked-durable-sabotage")
    assert detail_events, "sabotage setup broke durable persistence entirely, not just the two keys"
    record = detail_events[-1]

    assert "fixture_identity" not in record, (
        "sabotage failed to strip fixture_identity -- this run cannot prove the earlier test catches "
        "its absence"
    )
    assert "timing" not in record, (
        "sabotage failed to strip timing -- this run cannot prove the earlier test catches its absence"
    )
    # The real assertions from test_an_early_blocked_exit_carries_fixture_identity_and_timing,
    # run again here against the SABOTAGED record, to prove they actually go red for this reason.
    fixture_identity = record.get("fixture_identity")
    assert not (isinstance(fixture_identity, dict) and fixture_identity), (
        "the real regression assertion must fail once fixture_identity is stripped from the "
        f"production record, but it did not: {record!r}"
    )
