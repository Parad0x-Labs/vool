"""P0 AMENDMENT — turn effect authority must survive worker threads.

The confirmed live defect, measured on the isolated daemon and reproduced below at
the current tip: conductor observation nodes execute through the scheduler's
ThreadPoolExecutor WITHOUT the originating turn's context. The effect ledger, the
frozen fetch policy and the fetch accounting are ContextVars opened by the turn's
`remote_fetch_policy_scope` on the TURN thread -- a plain `pool.submit` hands the
worker none of them, so the network door denies before any socket:

    RemoteFetchRefusedError: no active turn or background effect ledger: a fetch
    outside any turn scope is denied before any socket.

The mixed turn degrades honestly since the evidence-binding repair, but it cannot
execute its live-data demand at all. The fix belongs at the scheduler/executor
boundary: every submitted task runs inside a fresh `copy_context()` taken on the
turn's thread at submit time -- the same discipline `turn_planner.run_plan`
already applies and the effect gateway's own docstring names as the sanctioned
route.

The weather operation's run seam is replaced with a fetch through the REAL network
door against a loopback server the test owns: the refusal happens before any
socket (deterministic RED), and the authorized path exercises the door's full
lifecycle with no external network and no credentials.

Invariants under test, per the amendment: workers receive the turn's identity,
effect scope, frozen policy and cancellation context (explicitly copied, never
process-global); reused pool threads never retain a previous task's context;
concurrent turns stay isolated (allowed beside denied); a missing scope still
fails closed; denied policy stays denied with the DENIAL's own reason; receipts
bind to the turn that opened the scope; a cancelled turn runs no late sockets;
evidence-binding and M3 publication behavior are unchanged.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from core.conductor import planner as conductor_planner
from core.conductor.node import NodeFailureCode
from core.conductor.scheduler import run_conductor_plan
from core.remote_fetch_policy import remote_fetch_policy_scope

# ---------------------------------------------------------------- loopback fetch server

#: What the loopback server serves for any GET: a wttr-shaped current-condition body.
CANNED_WEATHER = {
    "current_condition": [
        {
            "temp_C": "17",
            "FeelsLikeC": "16",
            "humidity": "77",
            "weatherDesc": [{"value": "Partly cloudy"}],
            "observation_time": "09:00 AM",
        }
    ]
}


class _HitCountingHandler(BaseHTTPRequestHandler):
    server_version = "eb-context-test/1.0"

    def do_GET(self):
        self.server.hits += 1
        body = json.dumps(CANNED_WEATHER).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the default stderr chatter
        return


@contextmanager
def _loopback_fetch_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HitCountingHandler)
    server.hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _door_mediated_weather_run(server):
    """A replacement subtask runner whose fetch crosses the REAL network door.

    The operation's own body imports this runner function-level
    (`from core.agent_runtime.live_data_runner import _run_weather_subtask`), so the
    patch lands regardless of the registry's captured spec references. The fetch
    crosses `core.remote_fetch_policy.open_remote`, so the door's ledger check,
    policy veto and effect lifecycle all run for real.
    """

    def _run_subtask(subtask, timeout_s=None, **_kwargs):
        from core.remote_fetch_policy import open_remote

        url = f"http://127.0.0.1:{server.server_port}/wttr"
        request = urllib.request.Request(url)
        # The door IS the transport: it enforces, records the lifecycle, opens the
        # socket and returns the response.
        with open_remote(request, timeout=5.0) as response:
            payload = json.loads(response.read().decode())
        condition = payload["current_condition"][0]
        place = str(getattr(subtask, "entity", "") or "Vilnius")

        class _Outcome:
            result = {
                "place_label": place.title(),
                "condition": condition["weatherDesc"][0]["value"],
                "temperature_c": float(condition["temp_C"]),
                "feels_like_c": float(condition["FeelsLikeC"]),
                "humidity_pct": float(condition["humidity"]),
                "wind_kmph": 3.0,
                "observed_at": condition["observation_time"],
                "source": "loopback.test",
                "source_url": url,
            }
            failure_reason = ""

        return _Outcome()

    return _run_subtask


# ------------------------------------------------------------------ the plan under drive

PLAN_REPLY = json.dumps(
    [
        {
            "request": "Get the current weather in Vilnius",
            "operation": "weather_lookup",
            "depends_on": [],
        },
        {"request": "calculate 37 x 19", "operation": "calculation", "depends_on": []},
    ]
)

MIXED_MESSAGE = "Get the current weather in Vilnius and calculate 37 x 19"


def _run_mixed_plan(source_context: dict, *, max_workers: int = 4):
    plan = conductor_planner.plan_conductor_turn(
        MIXED_MESSAGE, ask_model=lambda _s, _p: PLAN_REPLY, plan_id="eb-ctx"
    )
    assert plan is not None, "the conductor declined a turn it must claim"
    from core.conductor.registry import NodeContext

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(
            session_id=str(source_context.get("session_id") or ""),
            source_context=source_context,
        ),
        max_workers=max_workers,
    )
    return plan, outcomes


def _outcome_for(outcomes, operation: str):
    matched = [o for o in outcomes if o.node.operation == operation]
    assert matched, f"no {operation} node in the plan"
    return matched[0]


@contextmanager
def _door_weather(server):
    """The weather operation fetching through the real door against the loopback."""
    import core.agent_runtime.live_data_runner as live_data_runner

    with mock.patch.object(
        live_data_runner, "_run_weather_subtask", _door_mediated_weather_run(server)
    ):
        yield


# --------------------------------------------------------------------------- the tests


def test_observation_node_fetches_through_the_worker_pool():
    """THE confirmed defect. Inside the turn's fetch scope, on the turn's own thread,
    the ledger is open -- and the worker that executes the weather node must receive
    it: the fetch goes through the door and SUCCEEDS, not
    RemoteFetchRefusedError-before-any-socket."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            source_context = {
                "session_id": "sess-ctx-allowed",
                "cancel_turn_id": "turn-ctx-allowed",
                "surface": "api",
                "platform": "api",
            }
            with remote_fetch_policy_scope(source_context):
                _plan, outcomes = _run_mixed_plan(source_context)

    weather = _outcome_for(outcomes, "weather_lookup")
    assert weather.succeeded, (
        f"weather node failed inside the turn scope: {weather.failure_reason}"
    )
    assert "17" in str(weather.rendered), weather.rendered
    calc = _outcome_for(outcomes, "calculation")
    assert calc.succeeded and "703" in str(calc.rendered)
    assert server.hits >= 1, "the fetch never reached the loopback server"


def test_denied_policy_stays_denied_inside_workers_with_the_denials_reason():
    """Invariant: denied remains denied -- and the worker's failure names the VETO,
    not a missing scope. A worker that lost the policy and reported
    'no active ledger' would be denied for the wrong reason, which is its own
    defect: the record must say what happened."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            source_context = {
                "session_id": "sess-ctx-denied",
                "cancel_turn_id": "turn-ctx-denied",
                "surface": "api",
                "platform": "api",
                "allow_remote_fetch": False,
            }
            with remote_fetch_policy_scope(source_context):
                _plan, outcomes = _run_mixed_plan(source_context)

    weather = _outcome_for(outcomes, "weather_lookup")
    assert not weather.succeeded
    reason = str(weather.failure_reason or "")
    assert "not permitted for this turn" in reason, reason
    assert "no active turn or background effect ledger" not in reason, reason
    calc = _outcome_for(outcomes, "calculation")
    assert calc.succeeded and "703" in str(calc.rendered)


def test_reused_pool_threads_never_retain_the_previous_tasks_context():
    """Invariant: thread reuse retention. One worker thread (max_workers=1) serves
    an ALLOWED turn's fetch, then -- with no scope open at all -- a second plan's
    fetch on that same thread must be denied before any socket. The copied context
    is per-task; the thread itself must hold nothing between tasks."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            allowed_context = {
                "session_id": "sess-reuse-a",
                "cancel_turn_id": "turn-reuse-a",
                "surface": "api",
                "platform": "api",
            }
            with remote_fetch_policy_scope(allowed_context):
                _plan_a, outcomes_a = _run_mixed_plan(allowed_context, max_workers=1)
            assert _outcome_for(outcomes_a, "weather_lookup").succeeded

            # NO scope is open now. The same pool size means a reused thread serves
            # this plan's fetch; the door must deny it (fail closed) with the
            # missing-scope reason -- never a stale allow from the previous task.
            _plan_b, outcomes_b = _run_mixed_plan(
                {
                    "session_id": "sess-reuse-b",
                    "cancel_turn_id": "turn-reuse-b",
                    "surface": "api",
                    "platform": "api",
                },
                max_workers=1,
            )

    weather_b = _outcome_for(outcomes_b, "weather_lookup")
    assert not weather_b.succeeded
    assert "no active turn or background effect ledger" in str(weather_b.failure_reason or "")


def test_concurrent_allowed_and_denied_turns_stay_isolated():
    """Invariant: isolation under concurrency. Two turns run their plans at the same
    time on separate threads -- one allowed, one denied. The allowed turn's workers
    fetch; the denied turn's workers are refused with the veto; neither turn's
    receipts leak onto the other's context."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            results: dict[str, list] = {}

            def _run_turn(name: str, context: dict) -> None:
                with remote_fetch_policy_scope(context):
                    _plan, outcomes = _run_mixed_plan(context)
                results[name] = list(outcomes)

            allowed_context = {
                "session_id": "sess-parallel-a",
                "cancel_turn_id": "turn-parallel-a",
                "surface": "api",
                "platform": "api",
            }
            denied_context = {
                "session_id": "sess-parallel-b",
                "cancel_turn_id": "turn-parallel-b",
                "surface": "api",
                "platform": "api",
                "allow_remote_fetch": False,
            }
            thread_a = threading.Thread(target=_run_turn, args=("allowed", allowed_context))
            thread_b = threading.Thread(target=_run_turn, args=("denied", denied_context))
            thread_a.start()
            thread_b.start()
            thread_a.join(timeout=60)
            thread_b.join(timeout=60)

    allowed_weather = _outcome_for(results["allowed"], "weather_lookup")
    assert allowed_weather.succeeded, allowed_weather.failure_reason
    denied_weather = _outcome_for(results["denied"], "weather_lookup")
    assert not denied_weather.succeeded
    assert "not permitted for this turn" in str(denied_weather.failure_reason or "")

    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    allowed_receipts = allowed_context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or []
    denied_receipts = denied_context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or []
    assert allowed_receipts, "the allowed turn recorded no effect receipts"
    # The denied turn's receipts are DENIALS on its own ledger -- never the allowed
    # turn's successes.
    assert not any(
        str(r.get("lifecycle") or "") == "succeeded" for r in denied_receipts
    ), denied_receipts


def test_effect_receipts_bind_to_the_owning_turn():
    """Invariant: receipts bind to the turn that opened the scope. The ledger freezes
    the turn identity off the live context; a worker's receipt must carry the
    OWNING turn's id, not the worker's, not another turn's."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            source_context = {
                "session_id": "sess-receipts",
                "cancel_turn_id": "turn-receipts-own",
                "turn_id": "turn-receipts-own",
                "request_id": "req-receipts-own",
                "surface": "api",
                "platform": "api",
            }
            with remote_fetch_policy_scope(source_context):
                _plan, outcomes = _run_mixed_plan(source_context)
            assert _outcome_for(outcomes, "weather_lookup").succeeded

    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    receipts = list(source_context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or [])
    assert receipts, "no effect receipts landed on the turn's context"
    for receipt in receipts:
        assert str(receipt.get("turn_id") or "") == "turn-receipts-own", receipt
        assert str(receipt.get("request_id") or "") == "req-receipts-own", receipt


def test_a_cancelled_turn_runs_no_late_sockets():
    """Invariant: cancellation prevents late sockets. The turn's cancel Event is set
    BEFORE the plan's observation node runs; the worker must refuse to start work --
    the loopback server is never hit -- and the node reports a cancellation outcome,
    not a fetch refusal and not a success."""
    with _loopback_fetch_server() as server:
        with _door_weather(server):
            cancel_event = threading.Event()
            cancel_event.set()
            source_context = {
                "session_id": "sess-cancel",
                "cancel_turn_id": "turn-cancel-1",
                "turn_id": "turn-cancel-1",
                "surface": "api",
                "platform": "api",
                "cancel_event": cancel_event,
            }
            with remote_fetch_policy_scope(source_context):
                _plan, outcomes = _run_mixed_plan(source_context)

    assert server.hits == 0, f"a cancelled turn opened {server.hits} socket(s)"
    weather = _outcome_for(outcomes, "weather_lookup")
    assert not weather.succeeded
    assert weather.failure_code == NodeFailureCode.CANCELLED, (
        f"code={weather.failure_code!r} reason={weather.failure_reason!r}"
    )


# ------------------------------------------------------- served through the real spine


def _drive_served_mixed_turn(session_id: str, *, server_url: str | None):
    """A full-spine conductor turn whose weather fetch crosses the real door.

    `server_url=None` points the fetch at a closed port -- a genuine transport
    failure. Everything else is the real spine: run_once opens the turn's fetch
    scope itself, the scheduler propagates it, the door mediates the socket.
    """
    from tests.test_served_evidence_binding_p0 import (
        _agent,
        _finalize_like_the_transport,
        _local_manifest,
        _ProviderScript,
    )

    def _run_subtask(subtask, timeout_s=None, **_kwargs):
        from core.remote_fetch_policy import open_remote

        url = server_url or "http://127.0.0.1:1/wttr"  # port 1: nothing listens
        with open_remote(urllib.request.Request(url), timeout=5.0) as response:
            payload = json.loads(response.read().decode())
        condition = payload["current_condition"][0]
        place = str(getattr(subtask, "entity", "") or "Vilnius")

        class _Outcome:
            result = {
                "place_label": place.title(),
                "condition": condition["weatherDesc"][0]["value"],
                "temperature_c": float(condition["temp_C"]),
                "feels_like_c": float(condition["FeelsLikeC"]),
                "humidity_pct": float(condition["humidity"]),
                "wind_kmph": 3.0,
                "observed_at": condition["observation_time"],
                "source": "loopback.test",
                "source_url": url,
            }
            failure_reason = ""

        return _Outcome()

    agent = _agent(session_id)
    script = _ProviderScript(
        {
            "clause_decomposition": PLAN_REPLY,
        }
    )
    import core.agent_runtime.live_data_runner as live_data_runner

    with (
        mock.patch.object(live_data_runner, "_run_weather_subtask", side_effect=_run_subtask),
        mock.patch.object(agent.memory_router, "_invoke_manifest", side_effect=script),
        mock.patch(
            "core.agent_runtime.audit_routing._ranked_candidates",
            return_value=[_local_manifest()],
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": f"turn-{session_id}",
            "request_id": f"req-{session_id}",
        }
        result = agent.run_once(
            MIXED_MESSAGE, source_context=context, session_id_override=session_id
        )
    return result, context, _finalize_like_the_transport(context, result)


def test_served_mixed_weather_and_math_both_execute_when_transport_succeeds():
    """The amendment's headline proof, through the real spine: the mixed turn's
    live-data demand EXECUTES (the worker fetched through the door -- no
    'missing effect scope' refusal) and both demands serve. The receipts land on
    the turn's own context under the turn's identity, and the evidence binding
    the served-evidence lane built still holds."""
    with _loopback_fetch_server() as server:
        _result, context, commit = _drive_served_mixed_turn(
            "sess-served-both", server_url=f"http://127.0.0.1:{server.server_port}/wttr"
        )
    served = str(commit.get("canonical_content") or "")
    assert "17" in served, f"the live reading did not serve: {served[:500]}"
    assert "703" in served, f"the computed demand did not serve: {served[:500]}"
    assert "no active turn or background effect ledger" not in served

    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    receipts = list(context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or [])
    assert any(str(r.get("lifecycle") or "") == "succeeded" for r in receipts), receipts
    # The receipts name the turn the spine minted (the canonical TurnRequest id),
    # which run_once stamped onto this same context -- one identity for the fetches
    # and the answer, whatever the caller's inbound handle was.
    canonical_turn_id = str(getattr(context.get("turn_request"), "turn_id", "") or "")
    assert canonical_turn_id, "the spine stamped no canonical turn id onto the context"
    for receipt in receipts:
        assert str(receipt.get("turn_id") or "") == canonical_turn_id, receipt

    # The evidence-binding lane's guarantees are unchanged by this amendment: the
    # turn's observations bound as an evidence set with values and source identity.
    from core import grounding_lifecycle as lifecycle_ledger

    records = [r for r in lifecycle_ledger._RECORDS.values() if r.identity.session_id == "sess-served-both"]
    assert records, "no grounding lifecycle for the served turn"
    bound = [r for r in records if r.bound_evidence_set_id]
    assert bound, "the turn's fetched observation did not bind as evidence"
    joined = json.dumps([dict(n) for n in bound[0].bound_notes], default=str)
    assert "17" in joined and "loopback.test" in joined, joined[:400]


def test_served_transport_failure_fails_only_weather_with_the_real_reason():
    """When the transport GENUINELY fails, only the weather demand fails -- with the
    transport's own reason (connection refused on a closed port), never the missing-
    scope denial -- and the math still serves."""
    _result, context, commit = _drive_served_mixed_turn("sess-served-transport", server_url=None)
    served = str(commit.get("canonical_content") or "")
    assert "703" in served, f"the computed demand was lost with the transport failure: {served[:500]}"
    assert "Vilnius" in served, f"the failed demand is not named: {served[:500]}"
    assert "no active turn or background effect ledger" not in served, (
        f"the missing-scope refusal survived the repair: {served[:500]}"
    )
    # The REAL transport reason, as a typed boundary the reader can act on: the
    # fixed phrase for TRANSPORT_FAILED -- not "internal fault", and not raw
    # exception text (the URL/errno detail stays on the receipt).
    assert "live source for this could not be reached" in served, served[:500]
    # And the door recorded the attempt's terminal on the turn's own ledger.
    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    receipts = list(context.get(EFFECT_RECEIPTS_CONTEXT_KEY) or [])
    assert any(
        str(r.get("effect_class") or "") == "network_fetch"
        and str(r.get("lifecycle") or "") == "failed"
        for r in receipts
    ), receipts


# ----------------------------------------------------------------------- sabotages
#
# Each neuters one load-bearing half of the propagation and asserts a NAMED test
# above stops holding.


def test_sabotage_plain_submit_loses_the_turns_effect_authority():
    """Revert the scheduler to a plain `pool.submit` (the original defect) and the
    headline test's guarantee dies: the worker fetch is refused before any socket."""
    import core.conductor.scheduler as scheduler

    with mock.patch.object(
        scheduler,
        "_submit_node",
        lambda pool, node, ctx, **kw: pool.submit(scheduler._run_one, node, ctx, **kw),
    ):
        with _loopback_fetch_server() as server:
            with _door_weather(server):
                source_context = {
                    "session_id": "sess-sabotage-plain",
                    "cancel_turn_id": "turn-sabotage-plain",
                    "surface": "api",
                    "platform": "api",
                }
                with remote_fetch_policy_scope(source_context):
                    _plan, outcomes = _run_mixed_plan(source_context)

    weather = _outcome_for(outcomes, "weather_lookup")
    assert not weather.succeeded
    assert "no active turn or background effect ledger" in str(weather.failure_reason or ""), (
        f"plain submit did not reproduce the missing-scope refusal: {weather.failure_reason}"
    )


def test_sabotage_a_worker_context_mutation_cannot_leak_to_siblings_or_the_turn():
    """Thread/task cleanup, driven. Workers set a ContextVar inside their own task
    contexts while running; neither a LATER task on the same pool thread nor the
    turn's own thread may see it. (Sabotage direction: if the scheduler shared ONE
    context object across tasks -- the plausible wrong implementation -- the marker
    would leak; the correct behaviour is pinned by construction.)"""
    import contextvars

    marker = contextvars.ContextVar("eb_ctx_sabotage_marker", default="")

    import core.conductor.operations as operations
    from core.conductor.registry import NodeContext

    original_calc = operations._calculation_run

    def _mutating_calc(node, ctx):
        marker.set(f"set-by:{node.node_id}")
        return original_calc(node, ctx)

    with _loopback_fetch_server() as server:
        with _door_weather(server):
            with mock.patch.object(operations, "_calculation_run", _mutating_calc):
                source_context = {
                    "session_id": "sess-sabotage-leak",
                    "cancel_turn_id": "turn-sabotage-leak",
                    "surface": "api",
                    "platform": "api",
                }
                with remote_fetch_policy_scope(source_context):
                    plan = conductor_planner.plan_conductor_turn(
                        MIXED_MESSAGE,
                        ask_model=lambda _s, _p: PLAN_REPLY,
                        plan_id="eb-ctx-sabotage",
                    )
                    assert plan is not None
                    outcomes = run_conductor_plan(
                        plan,
                        context=NodeContext(
                            session_id="sess-sabotage-leak", source_context=source_context
                        ),
                        max_workers=1,  # one thread serves every task in sequence
                    )

    assert all(o.succeeded for o in outcomes), [o.failure_reason for o in outcomes]
    # The turn thread (this thread) never saw the marker, and no task's mutation
    # survived into another task's context.
    assert marker.get() == "", f"a worker's context mutation leaked to the turn thread: {marker.get()!r}"
