from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any


def sync_public_presence(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None = None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    effective_status = agent._normalize_public_presence_status(status)
    with agent._public_presence_lock:
        agent._public_presence_status = effective_status
        if source_context is not None:
            agent._public_presence_source_context = dict(source_context)
    if _presence_sync_runs_inline(agent):
        _run_public_presence_sync_now(
            agent,
            status=effective_status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=audit_log_fn,
        )
        return
    _queue_public_presence_sync(
        agent,
        get_agent_display_name_fn=get_agent_display_name_fn,
        audit_log_fn=audit_log_fn,
    )


def _presence_sync_runs_inline(agent: Any) -> bool:
    backend_name = str(getattr(agent, "backend_name", "") or "").strip().lower()
    device = str(getattr(agent, "device", "") or "").strip().lower()
    return backend_name.startswith("test-") or device.endswith("-test")


def _queue_public_presence_sync(
    agent: Any,
    *,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    with agent._public_presence_lock:
        agent._public_presence_sync_pending = True
        if agent._public_presence_sync_inflight:
            return
        agent._public_presence_sync_inflight = True
    thread = threading.Thread(
        target=_public_presence_sync_worker,
        name="vool-public-presence-sync",
        daemon=True,
        kwargs={
            "agent": agent,
            "get_agent_display_name_fn": get_agent_display_name_fn,
            "audit_log_fn": audit_log_fn,
        },
    )
    with agent._public_presence_lock:
        agent._public_presence_sync_thread = thread
    thread.start()


def _public_presence_sync_worker(
    *,
    agent: Any,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    while True:
        with agent._public_presence_lock:
            agent._public_presence_sync_pending = False
            effective_status = str(agent._public_presence_status or "idle")
            source_context = dict(agent._public_presence_source_context or {})
        _run_public_presence_sync_now(
            agent,
            status=effective_status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=audit_log_fn,
        )
        with agent._public_presence_lock:
            if agent._public_presence_sync_pending:
                continue
            agent._public_presence_sync_inflight = False
            agent._public_presence_sync_thread = None
            return


def _run_public_presence_sync_now(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    # The presence handshake is the owning entry point of its own fetches: without a named
    # background scope the effect gateway denies them as outside any turn (R2b1) before the
    # pytest live-network fence ever sees them, so the sync could not even be blocked BY the
    # authority that reports it.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("agent.presence.sync"):
        _run_public_presence_sync_now_inner(
            agent,
            status=status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=audit_log_fn,
        )


def _run_public_presence_sync_now_inner(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    try:
        if agent._public_presence_registered:
            result = agent.public_hive_bridge.heartbeat_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=status,
                transport_mode=agent._public_transport_mode(source_context),
            )
            if not result.get("ok"):
                result = agent.public_hive_bridge.sync_presence(
                    agent_name=get_agent_display_name_fn(),
                    capabilities=agent._public_capabilities(),
                    status=status,
                    transport_mode=agent._public_transport_mode(source_context),
                )
        else:
            result = agent.public_hive_bridge.sync_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=status,
                transport_mode=agent._public_transport_mode(source_context),
            )
        if result.get("ok"):
            agent._public_presence_registered = True
    except Exception as exc:
        audit_log_fn(
            "public_hive_presence_sync_error",
            target_id=agent.persona_id,
            target_type="agent",
            details={"error": str(exc), "status": status},
        )
        return
    if not result.get("ok"):
        audit_log_fn(
            "public_hive_presence_sync_failed",
            target_id=agent.persona_id,
            target_type="agent",
            details={"status": status, **dict(result or {})},
        )


def start_public_presence_heartbeat(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._public_presence_running:
        return
    agent._public_presence_running = True
    agent._public_presence_thread = thread_factory(
        target=agent._public_presence_heartbeat_loop,
        name="vool-public-presence",
        daemon=True,
    )
    agent._public_presence_thread.start()


def start_idle_commons_loop(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._idle_commons_running:
        return
    agent._idle_commons_running = True
    agent._idle_commons_thread = thread_factory(
        target=agent._idle_commons_loop,
        name="vool-idle-commons",
        daemon=True,
    )
    agent._idle_commons_thread.start()


def public_presence_heartbeat_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
) -> None:
    while agent._public_presence_running:
        sleep_fn(120.0)
        with agent._public_presence_lock:
            last_status = str(agent._public_presence_status or "idle")
            source_context = dict(agent._public_presence_source_context or {})
        agent._sync_public_presence(
            status=agent._normalize_public_presence_status(last_status),
            source_context=source_context,
        )


def idle_commons_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    while agent._idle_commons_running:
        sleep_fn(90.0)
        if not agent._idle_commons_running:
            break
        try:
            agent._maybe_run_idle_commons_once()
            agent._maybe_run_autonomous_hive_research_once()
            # The curiosity queue's ONE consumer (F47: topics were queued and nothing in the
            # tree executed them). Same idle thread, same activity gate, one topic per tick.
            _consume = getattr(agent, "_maybe_execute_queued_curiosity_once", None)
            if callable(_consume):
                _consume()
        except Exception as exc:
            audit_log_fn(
                "idle_commons_loop_error",
                target_id=agent.persona_id,
                target_type="agent",
                details={"error": str(exc)},
            )


def maybe_run_idle_commons_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    audit_log_fn: Callable[..., Any],
) -> None:
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "social_commons", True)):
        return
    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_commons = now - float(agent._last_idle_commons_ts)
        seed_index = int(agent._idle_commons_seed_index)
    if idle_for_seconds < 300.0:
        return
    if since_last_commons < 900.0:
        return

    session_id = agent._idle_commons_session_id()
    commons = agent.curiosity.run_idle_commons(
        session_id=session_id,
        task_id="agent-commons",
        trace_id="agent-commons",
        seed_index=seed_index,
    )
    # R-9b (H-7): idle-commons is a CANONICAL semantic lane — its model-authored
    # body traverses A2 admission + A7 finalization exactly like every served
    # answer before it may leave the process. No private mini-authority here.
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    _public_body = str(commons.get("public_body") or commons.get("summary") or "")
    admit_semantic_result(
        {"response": _public_body, "route_reason": "idle_commons"},
        source_override=__import__(
            "core.semantic.semantic_result_seam", fromlist=["SemanticSource"]
        ).SemanticSource.MODEL,
        route_id_override="idle_commons",
        turn_id=str(session_id),
    )
    _commons_commit = finalize_answer(
        turn_id=str(session_id),
        canonical_content=_public_body,
    )
    publish_result: dict[str, Any] | None = None
    try:
        publish_result = agent.public_hive_bridge.publish_agent_commons_update(
            topic=str(dict(commons.get("topic") or {}).get("topic") or ""),
            topic_kind=str(dict(commons.get("topic") or {}).get("topic_kind") or "technical"),
            summary=str(commons.get("summary") or ""),
            public_body=str(_commons_commit.get("canonical_content") or ""),
            topic_tags=[str(tag) for tag in list(commons.get("topic_tags") or [])[:8]],
        )
    except Exception as exc:
        audit_log_fn(
            "idle_commons_publish_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "candidate_id": commons.get("candidate_id")},
        )
    if publish_result and str(publish_result.get("topic_id") or "").strip():
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id=str(publish_result.get("topic_id") or "").strip(),
        )
    with agent._activity_lock:
        agent._last_idle_commons_ts = now
        agent._idle_commons_seed_index = (seed_index + 1) % 64
    audit_log_fn(
        "idle_commons_cycle_complete",
        target_id=session_id,
        target_type="session",
        details={
            "idle_for_seconds": round(idle_for_seconds, 2),
            "candidate_id": commons.get("candidate_id"),
            "topic_id": str((publish_result or {}).get("topic_id") or ""),
            "publish_status": str((publish_result or {}).get("status") or "local_only"),
            "finalization_id": str((_commons_commit or {}).get("finalization_id") or ""),
            "topic": dict(commons.get("topic") or {}).get("topic"),
        },
    )


#: The curiosity consumer's gates. Idle means the user has not sent a turn for this long;
#: the interval keeps one background topic per idle tick even when the loop ticks faster.
CURIOSITY_QUEUE_IDLE_SECONDS = 60.0
CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS = 60.0
#: A `running` row older than this belongs to an executor that died mid-topic; it is re-queued.
CURIOSITY_QUEUE_STALE_RUNNING_SECONDS = 900


def maybe_execute_queued_curiosity_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    audit_log_fn: Callable[..., Any],
    capacity_fn: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Execute at most ONE queued curiosity topic, off the user's critical path.

    The roam QUEUES its topics inside the served turn (F46) and this is the lane that runs
    them: the existing idle thread, so no scheduler is added. Every gate is a fact about the
    machine or the user, never about the topic:

    * the curiosity policy is enabled and the user's `idle_research_assist` preference is on;
    * no turn is in flight, the user has been idle for `CURIOSITY_QUEUE_IDLE_SECONDS` since the
      last turn ENDED, and the last background topic ran at least
      `CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS` ago;
    * `core.helper_scheduler.HelperScheduler` -- the authority that already keeps mesh work
      from starving the local user -- says the node has capacity (CPU/memory ceilings);
      an authority that cannot be consulted did not say yes;
    * the claim is atomic in the store, so two consumers never run one topic;
    * execution is bounded by the policy's `max_total_roam_seconds` and is cancelled at the
      next web-call boundary the moment the user sends a turn or the runtime is stopping --
      a cancelled or deadline-cut topic goes back to `queued` with a run row naming why.

    Returns the executor's report (topic, terminal status, candidate id, seconds) or None
    when a gate held. Chat never waits on this: it runs on the idle thread and the user's
    activity is the cancellation signal.
    """
    try:
        if not bool(getattr(agent.curiosity.config, "enabled", True)):
            return None
    except Exception:
        return None
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "idle_research_assist", True)):
        return None
    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last = now - float(getattr(agent, "_last_curiosity_execute_ts", 0.0) or 0.0)
        turns_in_flight = int(getattr(agent, "_turns_in_flight", 0) or 0)
    # A turn in flight IS activity, however long it runs (measured: a 163 s research turn
    # looked idle to a start-only mark and the consumer ran beside it).
    if turns_in_flight > 0:
        return None
    if idle_for_seconds < CURIOSITY_QUEUE_IDLE_SECONDS:
        return None
    if since_last < CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS:
        return None
    if capacity_fn is None:
        capacity_fn = _default_curiosity_capacity
    try:
        has_capacity = bool(capacity_fn())
    except Exception as exc:
        audit_log_fn(
            "curiosity_queue_deferred",
            target_id=agent.persona_id if hasattr(agent, "persona_id") else "agent",
            target_type="agent",
            details={"reason": "capacity_authority_unavailable", "error": str(exc)},
        )
        return None
    if not has_capacity:
        audit_log_fn(
            "curiosity_queue_deferred",
            target_id=agent.persona_id if hasattr(agent, "persona_id") else "agent",
            target_type="agent",
            details={"reason": "no_capacity"},
        )
        return None
    from storage.curiosity_state import claim_next_queued_curiosity_topic

    row = claim_next_queued_curiosity_topic(
        stale_running_after_seconds=CURIOSITY_QUEUE_STALE_RUNNING_SECONDS
    )
    if row is None:
        return None
    tick_started = now

    def _cancelled() -> bool:
        if not bool(getattr(agent, "_idle_commons_running", True)):
            return True
        with agent._activity_lock:
            if int(getattr(agent, "_turns_in_flight", 0) or 0) > 0:
                return True
            return float(agent._last_user_activity_ts) > tick_started

    try:
        # THE OWNING ENTRY POINT of legitimate background work opens the named effect scope
        # (`core.effect_gateway.named_background_effect_scope`): every remote door fails closed
        # outside a turn, and the consumer runs on the idle thread outside any turn. Measured on
        # the controlled rig (9d7b6a3d/d4aae964): the stub saw no request during any consumer
        # tick -- each search was refused at the door and the topic ended `empty`.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(
            "curiosity.queue_consumer",
            source_context={
                "surface": "background",
                "lane": "curiosity_consumer",
                "session_id": str(row.get("session_id") or ""),
            },
        ):
            report = agent.curiosity.execute_queued_topic(row, cancelled=_cancelled)
    except Exception as exc:
        from storage.curiosity_state import requeue_curiosity_topic

        requeue_curiosity_topic(str(row.get("topic_id") or ""))
        audit_log_fn(
            "curiosity_topic_execution_error",
            target_id=str(row.get("topic_id") or ""),
            target_type="curiosity_topic",
            details={"error": str(exc), "topic": str(row.get("topic") or "")},
        )
        with agent._activity_lock:
            agent._last_curiosity_execute_ts = time_fn()
        return None
    with agent._activity_lock:
        agent._last_curiosity_execute_ts = time_fn()
    audit_log_fn(
        "curiosity_topic_executed",
        target_id=str(row.get("topic_id") or ""),
        target_type="curiosity_topic",
        details={
            "topic": str(report.get("topic") or ""),
            "topic_kind": str(report.get("topic_kind") or ""),
            "status": str(report.get("status") or ""),
            "candidate_id": report.get("candidate_id"),
            "cached": bool(report.get("cached")),
            "seconds": report.get("seconds"),
            "idle_for_seconds": round(idle_for_seconds, 2),
            "session_id": str(row.get("session_id") or ""),
        },
    )
    return report


def _default_curiosity_capacity() -> bool:
    from core.helper_scheduler import HelperScheduler

    return bool(HelperScheduler().can_accept_mesh_task())


def stop_background_runtime_threads(agent: Any, *, join_timeout: float = 2.0) -> dict[str, bool]:
    """Stop the idle-commons and presence loops; report whether each thread exited in time.

    The flags are the loops' own conditions, and the curiosity consumer reads the same flag
    as its cancellation signal, so an in-flight topic stops at its next web-call boundary and
    goes back to `queued`. Threads are daemon threads: one that is still inside a call when
    the timeout passes ends with the process, and the report says so (False).
    """
    agent._idle_commons_running = False
    agent._public_presence_running = False
    report: dict[str, bool] = {}
    for name in ("_idle_commons_thread", "_public_presence_thread"):
        thread = getattr(agent, name, None)
        if thread is None:
            continue
        try:
            thread.join(timeout=max(0.0, float(join_timeout)))
            report[name] = not thread.is_alive()
        except Exception:
            report[name] = False
    return report


def maybe_run_autonomous_hive_research_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    pick_signal_fn: Callable[[list[dict[str, Any]]], dict[str, Any] | None],
    research_topic_fn: Callable[..., Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "accept_hive_tasks", True)):
        return
    if not bool(getattr(prefs, "idle_research_assist", True)):
        return
    if not agent.public_hive_bridge.enabled():
        return

    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_research = now - float(agent._last_idle_hive_research_ts)
    if idle_for_seconds < 240.0:
        return
    if since_last_research < 900.0:
        return

    queue_rows = agent.public_hive_bridge.list_public_research_queue(limit=12)
    signal = pick_signal_fn(queue_rows)
    if not signal:
        return

    auto_session_id = f"auto-research:{signal.get('topic_id') or ''!s}"
    lane_context = {"surface": "background", "platform": "openclaw", "lane": "autonomous_research"}
    agent._sync_public_presence(status="busy", source_context=lane_context)
    try:
        result = research_topic_fn(
            signal,
            public_hive_bridge=agent.public_hive_bridge,
            curiosity=agent.curiosity,
            hive_activity_tracker=agent.hive_activity_tracker,
            session_id=auto_session_id,
            auto_claim=True,
        )
        audit_log_fn(
            "idle_hive_research_cycle_complete",
            target_id=str(signal.get("topic_id") or auto_session_id),
            target_type="topic",
            details=result.to_dict(),
        )
        with agent._activity_lock:
            agent._last_idle_hive_research_ts = now
        if result.ok and result.topic_id:
            with contextlib.suppress(Exception):
                agent.hive_activity_tracker.note_watched_topic(session_id=auto_session_id, topic_id=result.topic_id)
    finally:
        agent._sync_public_presence(
            status=agent._idle_public_presence_status(),
            source_context=lane_context,
        )


def idle_commons_session_id(*, get_local_peer_id_fn: Callable[[], str]) -> str:
    return f"agent-commons:{get_local_peer_id_fn()}"


def normalize_public_presence_status(agent: Any, status: str) -> str:
    lowered = str(status or "idle").strip().lower()
    if lowered == "busy":
        return "busy"
    return agent._idle_public_presence_status()


def idle_public_presence_status(*, load_preferences_fn: Callable[[], Any]) -> str:
    prefs = load_preferences_fn()
    return "idle" if bool(getattr(prefs, "accept_hive_tasks", True)) else "limited"
