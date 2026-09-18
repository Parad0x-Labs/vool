"""The scheduler emitting is not the same as the UI being able to see it.

`test_a_running_node_is_visible_while_it_runs.py` proves the conductor emits per-node lifecycle to
whatever callable is injected. That proves nothing about the product: with no caller wiring an
emitter, every emission goes to `None` and the panel still shows silence.

This is the wiring half. It asserts that a node's lifecycle lands in the same durable, cursor-paged
table `/api/runtime/events` reads, which is what an AGENTS panel actually polls -- and that it lands
there in a shape a renderer can use without parsing prose.

Written because the sibling family stayed green through the entire time the emitter was being
dropped in `_context_for`: a predicate can be correct while nothing consults it.
"""

from __future__ import annotations

import json

import pytest

from core.runtime_continuity import append_runtime_event, list_runtime_session_events


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """A scratch database, so this never reads or writes the operator's runtime home."""

    from storage import db as storage_db

    storage_db.configure_default_db_path(str(tmp_path / "events.db"))
    yield


def _events(session_id: str, event_type: str | None = None) -> list[dict]:
    rows = list_runtime_session_events(session_id=session_id) or []
    rows = rows.get("events", rows) if isinstance(rows, dict) else rows
    out = []
    for r in rows:
        if event_type and str(r.get("event_type")) != event_type:
            continue
        out.append(r)
    return out


#: Keys the store owns on every row. Everything else on a row came from `details`.
_ROW_KEYS = frozenset({"session_id", "seq", "event_type", "message", "created_at"})


def _details(row: dict) -> dict:
    """`details` is FLATTENED onto the row, not nested under a `details` key.

    Measured, and worth pinning: a reader that expects `row["details"]["node_id"]` finds nothing
    and silently renders an empty panel. It also means a `details` key colliding with one of
    `_ROW_KEYS` would be overwritten by the store's own value -- which is why the node record uses
    `node_id`/`operation`/`state` rather than, say, `message`.
    """

    d = row.get("details")
    if isinstance(d, str):
        return json.loads(d)
    if isinstance(d, dict) and d:
        return dict(d)
    return {k: v for k, v in row.items() if k not in _ROW_KEYS}


# ---------------------------------------------------------------------------------------------
# The transport carries the record without loss
# ---------------------------------------------------------------------------------------------


def test_a_node_receipt_survives_the_round_trip_intact() -> None:
    """The event store preserves arbitrary nested detail, so no schema change is needed to carry
    node identity, timing and the rendered work."""

    detail = {
        "schema": "agent_node_completed_v1",
        "plan_id": "plan-1",
        "node_id": "weather",
        "operation": "fresh_data.weather",
        "state": "succeeded",
        "ok": True,
        "duration_s": 1.25,
        "depends_on": ["lookup"],
        "started_at_iso": "2026-08-14T20:00:00Z",
        "completed_at_iso": "2026-08-14T20:00:01Z",
        "rendered": "It is 14C in Vilnius.",
        "rendered_truncated": False,
    }
    append_runtime_event(
        session_id="sess-round-trip",
        event_type="agent_node_completed",
        message="weather succeeded in 1.2s",
        details=detail,
    )

    rows = _events("sess-round-trip", "agent_node_completed")
    assert len(rows) == 1
    got = _details(rows[0])
    for key, value in detail.items():
        assert got[key] == value, key


def test_two_agents_in_one_session_stay_separable() -> None:
    """The panel groups by node. One session carrying several nodes must not collapse into one row.

    This is the axis the existing task rail lacks entirely -- it keys on session alone.
    """

    for node_id in ("alpha", "beta", "gamma"):
        append_runtime_event(
            session_id="sess-many",
            event_type="agent_node_started",
            message=f"{node_id} started",
            details={"node_id": node_id, "operation": "probe", "plan_id": "p"},
        )

    rows = _events("sess-many", "agent_node_started")
    assert {_details(r)["node_id"] for r in rows} == {"alpha", "beta", "gamma"}


def test_a_started_row_is_distinguishable_from_a_completed_row() -> None:
    """"Is an agent at work" is answered by having one without the other."""

    append_runtime_event(
        session_id="sess-live",
        event_type="agent_node_started",
        message="a started",
        details={"node_id": "a"},
    )

    assert len(_events("sess-live", "agent_node_started")) == 1
    assert _events("sess-live", "agent_node_completed") == []


# ---------------------------------------------------------------------------------------------
# THE WIRING -- the agent must actually inject an emitter
# ---------------------------------------------------------------------------------------------


def test_the_agent_wires_an_emitter_into_the_conductor_context() -> None:
    """Sabotage-proof for the wiring, and the reason this file exists.

    Asserts against the real call site: `core.agent_runtime.agent` (moved from apps by M1) must pass `emit_node_event` when it
    builds the conductor's `NodeContext`. Without it the scheduler emits into `None`, every test in
    the sibling family still passes, and the product shows nothing.

    Checked structurally rather than by driving a full turn, because a full conductor turn needs a
    planner model. The behavioural half is covered by the sibling family; this covers the seam
    between them, which is precisely what was broken once already.
    """

    import ast
    import pathlib

    source = pathlib.Path("core/agent_runtime/agent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    wired = False
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "run_conductor_plan":
            continue
        for kw in call.keywords:
            if kw.arg == "context" and isinstance(kw.value, ast.Call):
                if any(k.arg == "emit_node_event" for k in kw.value.keywords):
                    wired = True

    assert wired, (
        "core/agent_runtime/agent.py calls run_conductor_plan without emit_node_event -- per-node "
        "lifecycle never reaches the event stream and any agents view is blind"
    )


def test_the_agent_wires_an_emitter_into_the_LIVE_DATA_lane_too() -> None:
    """The lane that actually runs, and the reason this test exists.

    Measured on the served surface on 2026-08-14: "whats the weather in Vilnius and also what time
    is it in Tokyo" produced `live_data_plan_created` / `live_data_plan_completed` and **zero** node
    events, because only the conductor was wired.

    The conductor is deliberately the RARE lane -- `requires_conductor()` is False for anything the
    existing lanes already serve together, so an ordinary multi-city turn goes to the live-data
    runner instead. Wiring only the conductor leaves the Agents panel blank for the common case
    while every unit test stays green.
    """

    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("core/agent_runtime/agent.py").read_text(encoding="utf-8"))

    calls = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and (getattr(call.func, "id", None) or getattr(call.func, "attr", None)) == "run_live_data_plan"
    ]
    assert calls, "run_live_data_plan is not called from the agent at all"
    unwired = [c for c in calls if not any(k.arg == "emit_node_event" for k in c.keywords)]
    assert not unwired, (
        f"{len(unwired)} of {len(calls)} run_live_data_plan call sites do not pass emit_node_event "
        "-- the common concurrent lane would report nothing to the Agents panel"
    )


def test_both_runners_accept_an_emitter() -> None:
    """Signature-level check on the two runners, including the SEQUENTIAL fallback.

    The fallback is what runs when the pool cannot start, which is exactly when an operator most
    wants to see what is happening. A panel that goes blank on degradation is worse than none.
    """

    import inspect

    from core.agent_runtime.live_data_runner import run_live_data_plan, run_live_data_plan_sequential
    from core.conductor.registry import NodeContext

    assert "emit_node_event" in inspect.signature(run_live_data_plan).parameters
    assert "emit_node_event" in inspect.signature(run_live_data_plan_sequential).parameters
    assert "emit_node_event" in {f.name for f in __import__("dataclasses").fields(NodeContext)}


def test_the_emitter_writes_both_lifecycle_types_with_a_readable_message() -> None:
    """Drive the agent's own emitter closure shape: a human-readable message plus the structured
    record. A feed that renders `message` must not show a raw dict, and a panel that renders from
    `details` must not have to parse prose."""

    append_runtime_event(
        session_id="sess-shape",
        event_type="agent_node_started",
        message="weather started (fresh_data.weather)",
        details={"node_id": "weather", "operation": "fresh_data.weather"},
    )
    append_runtime_event(
        session_id="sess-shape",
        event_type="agent_node_completed",
        message="weather succeeded in 1.2s",
        details={"node_id": "weather", "state": "succeeded", "duration_s": 1.2},
    )

    rows = _events("sess-shape")
    messages = [str(r.get("message") or "") for r in rows]
    assert any("started" in m for m in messages)
    assert any("succeeded" in m and "1.2s" in m for m in messages)
    assert all(not m.strip().startswith("{") for m in messages), "a dict leaked into the feed text"


# ---------------------------------------------------------------------------------------------
# BEHAVIOUR -- the LIVE-DATA lane must actually emit, not merely accept the parameter
# ---------------------------------------------------------------------------------------------


def _weather(location, **_kwargs):
    from core.weather_result_contract import WeatherResult

    table = {"kaunas": 28.0, "tallinn": 22.0, "warsaw": 34.0}
    if location not in table:
        return None
    return WeatherResult(
        location=location, place_label=location.title(), condition="Sunny",
        temperature_c=table[location], feels_like_c=table[location], humidity_pct=50.0,
        wind_kmph=10.0, observed_at="12:00 PM", source_label="wttr.in",
        source_url=f"https://wttr.in/{location}",
    )


def _drive_live_data(runner_name: str):
    """Run a real multi-city live-data plan with an emitter attached, offline."""

    from unittest import mock

    from core.agent_runtime import live_data_runner
    from core.agent_runtime.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(
        "weather in Kaunas, Tallinn and Warsaw", plan_id="p-live", attempt_id="a-live"
    )
    seen: list[tuple[str, dict]] = []
    runner = getattr(live_data_runner, runner_name)
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather):
        kwargs = {"approval_decisions": {}, "emit_node_event": lambda t, d: seen.append((t, dict(d)))}
        if runner_name == "run_live_data_plan":
            kwargs["timeout_s"] = 5
        outcomes = runner(plan, **kwargs)
    return outcomes, seen


def test_the_live_data_lane_emits_started_and_completed_for_every_subtask() -> None:
    """The lane that actually runs on an ordinary multi-city turn.

    Measured on the served surface before this was wired: such a turn produced
    `live_data_plan_completed` and ZERO node events, so the Agents panel was blank for the common
    case while every unit test stayed green.
    """

    outcomes, seen = _drive_live_data("run_live_data_plan")

    started = [d for t, d in seen if t == "agent_node_started"]
    completed = [d for t, d in seen if t == "agent_node_completed"]
    assert len(outcomes) >= 2
    assert len(started) == len(outcomes)
    assert len(completed) == len(outcomes)
    assert all(d["lane"] == "live_data" for d in started + completed)
    assert all(d["node_id"] for d in started)
    assert any(isinstance(d.get("duration_s"), float) for d in completed)


def test_the_SEQUENTIAL_fallback_also_reports() -> None:
    """The fallback runs when the pool cannot start -- exactly when an operator most needs to see
    what is happening. A panel that goes blank on degradation is worse than no panel."""

    outcomes, seen = _drive_live_data("run_live_data_plan_sequential")

    assert len(outcomes) >= 2
    assert len([d for t, d in seen if t == "agent_node_started"]) == len(outcomes)
    assert len([d for t, d in seen if t == "agent_node_completed"]) == len(outcomes)


def test_a_live_data_emitter_that_raises_cannot_change_the_outcomes() -> None:
    """Isolation, on this lane too: observation must never alter the work."""

    from unittest import mock

    from core.agent_runtime.live_data_plan import build_live_data_plan
    from core.agent_runtime.live_data_runner import run_live_data_plan

    plan = build_live_data_plan("weather in Kaunas and Warsaw", plan_id="p", attempt_id="a")

    def boom(event_type, detail):
        raise RuntimeError("observer down")

    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather):
        clean = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
        hostile = run_live_data_plan(plan, approval_decisions={}, timeout_s=5, emit_node_event=boom)

    assert [o.state for o in clean] == [o.state for o in hostile]
    assert [o.ok for o in clean] == [o.ok for o in hostile]
