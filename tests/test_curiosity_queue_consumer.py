"""The curiosity queue has ONE consumer, and it never runs on the user's critical path.

F46 made the served roam QUEUE its topics (executing them inline cost plain questions 60-97 s).
F47 then measured the other half: seven topics queued in a served home and nothing in the tree
executed them -- the only readers were the activity tracker (display) and the tests' own
stand-in. This is that consumer, on the idle thread that already exists
(`presence.idle_commons_loop`), and these pins are built around the failure modes a background
lane meets in production: a user who comes back mid-topic, a runtime that is shutting down, a
host with no capacity, a keyless home whose searches return nothing, two consumers racing for
one row, an executor that died mid-topic, and a topic that cannot finish in its budget.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime import presence
from core.curiosity_policy import CuriosityConfig
from core.curiosity_roamer import CuriosityRoamer, topic_from_queue_row
from core.source_reputation import profiles_for_topic
from storage.curiosity_state import (
    claim_next_queued_curiosity_topic,
    curiosity_queue_counts,
    queue_curiosity_topic,
    recent_curiosity_runs,
    recent_curiosity_topics,
)
from storage.db import get_connection
from storage.migrations import run_migrations

NOTE = {"summary": "Use official docs and avoid token leaks.", "source_label": "duckduckgo.com"}
SEARCH = "retrieval.web_adapter.WebAdapter.search_query"


def _config(*, roam_seconds: int = 8) -> CuriosityConfig:
    return CuriosityConfig(
        enabled=True, mode="bounded_auto", auto_execute_task_classes=("research",),
        max_topics_per_task=1, max_queries_per_topic=2, max_snippets_per_query=2,
        prefer_metadata_first=True, allow_news_pulse=True, news_max_topics_per_task=1,
        technical_max_topics_per_task=2, min_interest_score=0.40,
        min_understanding_confidence=0.4, skip_if_retrieval_confidence_at_least=0.95,
        max_total_roam_seconds=roam_seconds, auto_promote_to_canonical=False,
    )


@pytest.fixture(autouse=True)
def _clean_store():
    run_migrations()
    conn = get_connection()
    try:
        for table in ("curiosity_runs", "curiosity_topics", "candidate_knowledge_lane", "web_notes", "learning_shards"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    with mock.patch("core.curiosity_roamer.policy_engine.allow_web_fallback", return_value=True):
        yield


def _queue(topic: str = "telegram bot building", kind: str = "technical", priority: float = 0.7) -> str:
    return queue_curiosity_topic(
        session_id="s-consumer", task_id="task-1", trace_id="task-1", topic=topic, topic_kind=kind,
        reason="test", priority=priority,
        source_profiles=[p.to_dict() for p in profiles_for_topic(kind, topic)],
    )


def _agent(*, idle_for: float = 120.0, running: bool = True, roam_seconds: int = 8) -> SimpleNamespace:
    now = time.time()
    return SimpleNamespace(
        curiosity=CuriosityRoamer(_config(roam_seconds=roam_seconds)),
        _activity_lock=threading.Lock(),
        _last_user_activity_ts=now - idle_for,
        _last_curiosity_execute_ts=0.0,
        _turns_in_flight=0,
        _idle_commons_running=running,
        persona_id="persona-test",
    )


def _tick(agent: SimpleNamespace, *, capacity_fn=lambda: True, prefs_on: bool = True):
    logs: list[tuple] = []
    report = presence.maybe_execute_queued_curiosity_once(
        agent,
        load_preferences_fn=lambda: SimpleNamespace(idle_research_assist=prefs_on),
        time_fn=time.time,
        audit_log_fn=lambda *a, **k: logs.append((a, k)),
        capacity_fn=capacity_fn,
    )
    return report, logs


def _status(topic_id: str) -> str:
    for row in recent_curiosity_topics(limit=50):
        if row["topic_id"] == topic_id:
            return str(row["status"])
    return ""


def test_an_idle_tick_executes_one_queued_topic_and_records_the_run() -> None:
    first = _queue("telegram bot building", priority=0.9)
    second = _queue("discord bot building", priority=0.5)
    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, logs = _tick(_agent())
    assert report is not None and report["topic_id"] == first, "highest priority first"
    assert report["status"] == "completed" and report["candidate_id"]
    assert search.call_count >= 1
    assert _status(second) == "queued", "one topic per tick"
    runs = recent_curiosity_runs(limit=5)
    assert runs and runs[0]["outcome"] == "candidate_recorded" and runs[0]["topic_id"] == first
    assert any(a[0] == "curiosity_topic_executed" for a, _ in logs)


def test_the_consumer_runs_its_topic_inside_a_named_background_effect_scope() -> None:
    """MEASURED on the controlled rig: the remote door refused every consumer search because the
    idle thread runs outside any turn; the stub saw no request and the topic ended `empty`."""
    from core.effect_gateway import current_effect_ledger

    _queue()
    seen: dict = {}

    def _search(*a, **k):
        seen["ledger"] = current_effect_ledger()
        return [NOTE]

    with mock.patch(SEARCH, side_effect=_search):
        report, _ = _tick(_agent())
    assert report is not None and report["status"] == "completed"
    assert seen.get("ledger") is not None, "the web call ran with no effect ledger -- the door refuses that"


def test_no_results_is_a_terminal_state_not_a_retry_loop() -> None:
    """A keyless home: every search returns nothing. The topic ends `empty`, the run says
    `no_results`, and the next tick finds nothing to claim -- no busy loop on a dead search."""
    topic_id = _queue()
    with mock.patch(SEARCH, return_value=[]):
        report, _ = _tick(_agent())
    assert report is not None and report["status"] == "empty"
    assert recent_curiosity_runs(limit=1)[0]["outcome"] == "no_results"
    agent = _agent()
    with mock.patch(SEARCH, return_value=[]) as search:
        again, _ = _tick(agent)
    assert again is None and search.call_count == 0
    assert _status(topic_id) == "empty"


def test_an_active_user_holds_the_queue() -> None:
    topic_id = _queue()
    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, _ = _tick(_agent(idle_for=5.0))
    assert report is None and search.call_count == 0
    assert _status(topic_id) == "queued"


def test_a_turn_in_flight_holds_the_queue_however_long_it_runs() -> None:
    """MEASURED on rig s51 (dad61973): a 163 s research turn looked idle to the start-only
    activity mark and the consumer executed a topic beside it."""
    topic_id = _queue()
    agent = _agent(idle_for=500.0)
    agent._turns_in_flight = 1
    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, _ = _tick(agent)
    assert report is None and search.call_count == 0 and _status(topic_id) == "queued"


def test_a_turn_starting_mid_topic_cancels_it() -> None:
    topic_id = _queue()
    agent = _agent()

    def _search(*a, **k):
        agent._turns_in_flight = 1  # a served turn began; its activity mark may lag
        return [NOTE]

    with mock.patch(SEARCH, side_effect=_search) as search:
        report, _ = _tick(agent)
    assert search.call_count == 1
    assert report is not None and report["status"] == "queued" and _status(topic_id) == "queued"


def test_the_turn_span_counts_in_flight_and_restarts_the_idle_clock_at_its_end() -> None:
    from core.agent_runtime.agent import VoolAgent

    agent = SimpleNamespace(_activity_lock=threading.Lock(), _last_user_activity_ts=0.0, _turns_in_flight=0)
    before = time.time()
    with VoolAgent._turn_in_flight(agent):
        assert agent._turns_in_flight == 1
        started_mark = agent._last_user_activity_ts
        assert started_mark >= before
        time.sleep(0.05)
    assert agent._turns_in_flight == 0
    assert agent._last_user_activity_ts > started_mark, "the idle clock starts when the turn ENDS"
    try:
        with VoolAgent._turn_in_flight(agent):
            raise RuntimeError("turn failed")
    except RuntimeError:
        pass
    assert agent._turns_in_flight == 0, "a failed turn is not left in flight"


def test_the_preference_off_holds_the_queue() -> None:
    topic_id = _queue()
    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, _ = _tick(_agent(), prefs_on=False)
    assert report is None and search.call_count == 0 and _status(topic_id) == "queued"


def test_user_activity_cancels_between_web_calls_and_requeues() -> None:
    """The user sends a turn while a topic is running: the topic stops at the next web-call
    boundary, goes back to `queued`, and its run row says `cancelled`."""
    topic_id = _queue()
    agent = _agent()
    assert len(topic_from_queue_row(recent_curiosity_topics(limit=1)[0]).source_profiles) >= 2

    def _search(*a, **k):
        with agent._activity_lock:
            agent._last_user_activity_ts = time.time() + 1.0
        return [NOTE]

    with mock.patch(SEARCH, side_effect=_search) as search:
        report, _ = _tick(agent)
    assert search.call_count == 1, "the second source profile is never queried"
    assert report is not None and report["status"] == "queued" and report["candidate_id"] is None
    assert _status(topic_id) == "queued"
    assert recent_curiosity_runs(limit=1)[0]["outcome"] == "cancelled"


def test_shutdown_cancels_an_in_flight_topic_and_requeues() -> None:
    topic_id = _queue()
    agent = _agent()

    def _search(*a, **k):
        agent._idle_commons_running = False
        return [NOTE]

    with mock.patch(SEARCH, side_effect=_search) as search:
        report, _ = _tick(agent)
    assert search.call_count == 1
    assert report is not None and report["status"] == "queued"
    assert _status(topic_id) == "queued"


def test_no_capacity_defers_and_leaves_the_queue_untouched() -> None:
    topic_id = _queue()
    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, logs = _tick(_agent(), capacity_fn=lambda: False)
    assert report is None and search.call_count == 0 and _status(topic_id) == "queued"
    assert any(a[0] == "curiosity_queue_deferred" and k["details"]["reason"] == "no_capacity" for a, k in logs)


def test_a_raising_capacity_authority_is_not_a_yes() -> None:
    topic_id = _queue()

    def _boom() -> bool:
        raise RuntimeError("psutil unavailable")

    with mock.patch(SEARCH, return_value=[NOTE]) as search:
        report, logs = _tick(_agent(), capacity_fn=_boom)
    assert report is None and search.call_count == 0 and _status(topic_id) == "queued"
    assert any(k["details"]["reason"] == "capacity_authority_unavailable" for _, k in logs)


def test_the_interval_holds_a_second_topic_in_the_same_window() -> None:
    _queue("first", priority=0.9)
    second = _queue("second", priority=0.5)
    agent = _agent()
    with mock.patch(SEARCH, return_value=[NOTE]):
        first_report, _ = _tick(agent)
        second_report, _ = _tick(agent)
    assert first_report is not None and second_report is None
    assert _status(second) == "queued"


def test_concurrent_claimers_never_run_the_same_topic() -> None:
    _queue()
    won: list[dict] = []
    barrier = threading.Barrier(8)

    def _claim() -> None:
        barrier.wait()
        row = claim_next_queued_curiosity_topic()
        if row is not None:
            won.append(row)

    threads = [threading.Thread(target=_claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(won) == 1, [r["topic_id"] for r in won]
    assert curiosity_queue_counts() == {"running": 1}


def test_the_claim_holds_the_write_lock_from_select_to_update(monkeypatch) -> None:
    """DETERMINISTIC form of the race the threaded test only samples.

    While this claimer is between its SELECT (row still `queued`) and its UPDATE, a second
    writer with a short busy timeout must be locked out: the claim is one write transaction.
    If the lock were released mid-claim the second writer would succeed here and the same
    topic could run twice.
    """
    import sqlite3

    import storage.curiosity_state as state
    from storage.db import active_default_db_path

    topic_id = _queue()
    real_get_connection = state.get_connection
    seen: dict[str, object] = {}

    class _Probing:
        def __init__(self, conn) -> None:
            self._conn = conn

        def execute(self, sql, params=()):
            cursor = self._conn.execute(sql, params)
            if "SELECT" in sql and "status = 'queued'" in sql:
                other = sqlite3.connect(str(active_default_db_path()), timeout=0.2)
                try:
                    other.execute(
                        "UPDATE curiosity_topics SET status = 'running' WHERE topic_id = ?",
                        (topic_id,),
                    )
                    other.commit()
                    seen["other_writer"] = "succeeded"
                except sqlite3.OperationalError as exc:
                    seen["other_writer"] = f"locked out: {exc}"
                finally:
                    other.close()
            return cursor

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(state, "get_connection", lambda *a, **k: _Probing(real_get_connection(*a, **k)))
    row = state.claim_next_queued_curiosity_topic()
    monkeypatch.undo()
    assert seen.get("other_writer", "").startswith("locked out"), seen
    assert row is not None and row["topic_id"] == topic_id
    assert curiosity_queue_counts() == {"running": 1}


def test_a_stale_running_row_is_requeued_and_claimed_again() -> None:
    """An executor died mid-topic (process exit): its `running` row is an orphan and the next
    claim, past the stale window, picks it up instead of stranding it forever."""
    topic_id = _queue()
    first = claim_next_queued_curiosity_topic()
    assert first is not None and first["topic_id"] == topic_id
    assert claim_next_queued_curiosity_topic() is None, "a fresh running row is not stolen"
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE curiosity_topics SET updated_at = '2020-01-01T00:00:00+00:00' WHERE topic_id = ?",
            (topic_id,),
        )
        conn.commit()
    finally:
        conn.close()
    again = claim_next_queued_curiosity_topic(stale_running_after_seconds=900)
    assert again is not None and again["topic_id"] == topic_id
    assert _status(topic_id) == "running"


def test_the_deadline_requeues_a_topic_that_cannot_finish_in_budget() -> None:
    topic_id = _queue()

    def _slow(*a, **k):
        time.sleep(1.2)
        return [NOTE]

    with mock.patch(SEARCH, side_effect=_slow) as search:
        report, _ = _tick(_agent(roam_seconds=1))
    assert search.call_count == 1, "the deadline is checked before the second web call"
    assert report is not None and report["status"] == "queued"
    assert _status(topic_id) == "queued"
    assert recent_curiosity_runs(limit=1)[0]["outcome"] == "deadline"


def test_a_topic_that_cannot_finish_three_times_is_exhausted_not_requeued_forever() -> None:
    """MEASURED on rig s51 (7338f1e9): with no reachable search provider each web call took
    24 s, the 8 s deadline cut the topic after the first call, and it was re-queued every tick."""
    from core.curiosity_roamer import CURIOSITY_TOPIC_MAX_ATTEMPTS

    topic_id = _queue()

    def _slow(*a, **k):
        time.sleep(1.2)
        return [NOTE]

    outcomes = []
    for _attempt in range(CURIOSITY_TOPIC_MAX_ATTEMPTS):
        agent = _agent(roam_seconds=1)
        with mock.patch(SEARCH, side_effect=_slow):
            report, _ = _tick(agent)
        assert report is not None and report["topic_id"] == topic_id
        outcomes.append(report["status"])
    assert outcomes[:-1] == ["queued"] * (CURIOSITY_TOPIC_MAX_ATTEMPTS - 1)
    assert outcomes[-1] == "exhausted"
    assert _status(topic_id) == "exhausted"
    assert [r["outcome"] for r in recent_curiosity_runs(limit=10)] == ["deadline"] * CURIOSITY_TOPIC_MAX_ATTEMPTS
    with mock.patch(SEARCH, side_effect=_slow) as search:
        again, _ = _tick(_agent(roam_seconds=1))
    assert again is None and search.call_count == 0, "an exhausted topic is never claimed again"


def test_an_executor_exception_requeues_and_is_audited() -> None:
    topic_id = _queue()
    agent = _agent()
    with mock.patch.object(agent.curiosity, "execute_queued_topic", side_effect=RuntimeError("boom")):
        report, logs = _tick(agent)
    assert report is None and _status(topic_id) == "queued"
    assert any(a[0] == "curiosity_topic_execution_error" for a, _ in logs)


def test_a_row_with_malformed_profiles_is_still_executable() -> None:
    topic = topic_from_queue_row(
        {"topic_id": "x", "topic": "  weird   spacing ", "topic_kind": "technical",
         "reason": "", "priority": "nope", "source_profiles": [{"bogus": 1}, "not-a-dict"]}
    )
    assert topic.topic == "weird spacing" and topic.priority == 0.0
    assert topic.source_profiles, "falls back to the kind's profiles rather than poisoning the run"


def test_the_idle_loop_calls_the_consumer_once_per_tick_and_stops_on_the_flag() -> None:
    calls = {"consume": 0, "sleep": 0}
    agent = SimpleNamespace(
        _idle_commons_running=True,
        _maybe_run_idle_commons_once=lambda: None,
        _maybe_run_autonomous_hive_research_once=lambda: None,
        _maybe_execute_queued_curiosity_once=lambda: calls.__setitem__("consume", calls["consume"] + 1),
        persona_id="p",
    )

    def _sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        if calls["sleep"] >= 2:
            agent._idle_commons_running = False

    presence.idle_commons_loop(agent, sleep_fn=_sleep, audit_log_fn=lambda *a, **k: None)
    assert calls["consume"] == 1, "the flag is honoured before the tick body runs"


def test_stop_background_runtime_threads_flips_the_flags_and_reports() -> None:
    class _Thread:
        def __init__(self, alive_after_join: bool) -> None:
            self._alive = alive_after_join
            self.joined_with = None

        def join(self, timeout=None) -> None:
            self.joined_with = timeout

        def is_alive(self) -> bool:
            return self._alive

    agent = SimpleNamespace(
        _idle_commons_running=True, _public_presence_running=True,
        _idle_commons_thread=_Thread(False), _public_presence_thread=_Thread(True),
    )
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.5)
    assert agent._idle_commons_running is False and agent._public_presence_running is False
    assert report == {"_idle_commons_thread": True, "_public_presence_thread": False}
    assert agent._idle_commons_thread.joined_with == 0.5
