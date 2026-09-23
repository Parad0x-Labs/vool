"""C01 — the served sufficiency writer's eight scenarios over real /api/chat + restart.

The provider, daemon and workspace are isolated per run (see the module docstring of
``tests._served_sufficiency_provider`` for the reply law). One ordered journey boots one daemon,
certifies BOTH models through the daemon's own sealed operator probe, then drives:

 1 successful answer            -> one verified_success row, exact turn/session/task identity
 2 objectively wrong answer     -> the response-control validator rejects it: quality_failure
 3 refusal                      -> delivered refusal: the turn's exact stage outcome, no invention
 4 failed provider              -> transport failure: NO observation
 5 cancelled turn               -> operator stop: NO observation
 6 model switch                 -> each model's row carries its own model id
 7 two overlapping sessions     -> no cross-session borrowing of turn identity
 8 same-turn retry              -> one row despite two provider attempts (turn_key dedup)
 9 restart                      -> rows are durable; a new served turn appends
10 selection eligibility        -> adjustment consumes only fresh, same-model, same-kind rows
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._served_sufficiency_provider import MODEL_A, MODEL_B, PROVIDER_NAME, SEED, BodyAwareScriptedProvider

ACK_PROMPT = "Reply in exactly one word: acknowledged or denied, which do you prefer? Just one word."


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    store_dir = tmp_path / "blackbox-store"
    workspace.mkdir()
    (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    provider = BodyAwareScriptedProvider({MODEL_A: "acknowledged", MODEL_B: "second-model-acknowledged"})
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the runtime
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {}), (MODEL_B, {})]))
        provider.reset()
        yield {"home": home, "workspace": workspace, "daemon": daemon, "provider": provider}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _certify(daemon: ServedDaemon, model: str) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": PROVIDER_NAME, "model_name": model, "timeout_seconds": 60}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _rows(home: Path) -> list[dict[str, Any]]:
    store = home / "data" / "learning" / "sufficiency_observations.json"
    if not store.exists():
        return []
    payload = json.loads(store.read_text(encoding="utf-8"))
    return list(payload.get("observations") or [])


def _traces(home: Path, *, after_rowid: int = 0) -> list[dict[str, Any]]:
    db = home / "data" / "vool_web0_v2.db"
    if not db.exists():
        return []
    # READ-ONLY and guarded: connecting read-write would create the file (or an empty
    # schema) before the daemon's first write and silently swallow every later event.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT rowid, session_id, COALESCE(details_json,'') FROM runtime_session_events "
            "WHERE event_type='turn.trace_completed' AND rowid > ? ORDER BY rowid",
            (after_rowid,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    traces = []
    for rowid, session_id, details_json in rows:
        details = json.loads(details_json) if details_json else {}
        traces.append(
            {
                "rowid": rowid,
                "session_id": session_id,
                "turn_key": str(details.get("turn_key") or ""),
                "stage": str((details.get("stage_verdict") or {}).get("state") or ""),
                "fulfillment": str((details.get("fulfillment_outcome") or {}).get("fulfillment_status") or ""),
                "failure_codes": list((details.get("fulfillment_outcome") or {}).get("failure_codes") or []),
                "constraint": {
                    "shape": dict((details.get("response_constraint") or {}).get("shape") or {}),
                    "compliant": (details.get("response_constraint") or {}).get("compliant"),
                    "violation_count": int((details.get("response_constraint") or {}).get("violation_count") or 0),
                },
            }
        )
    return traces


def _last_trace(home: Path, *, after_rowid: int = 0, timeout: float = 30.0) -> dict[str, Any]:
    """Poll for the turn's trace, the way `_session_for_turn` below already polls.

    This read used to look ONCE, immediately after the HTTP turn returned. The daemon's
    trace write is durable but not synchronous with the response, so under a contended
    interval the row lands microseconds late and a single read reports a lost trace that
    was never lost -- the shape of the overlapping-session trace loss investigated in
    this lane, reproduced deterministically in
    tests/test_served_trace_read_is_not_a_race.py.

    Bounded on purpose: a trace that genuinely never arrives must still fail, and fail
    here rather than hang until the suite is killed.
    """
    deadline = time.monotonic() + timeout
    traces = _traces(home, after_rowid=after_rowid)
    while not traces and time.monotonic() < deadline:
        time.sleep(0.1)
        traces = _traces(home, after_rowid=after_rowid)
    assert traces, "no turn.trace_completed after the marker"
    return traces[-1]


def _session_for_turn(home: Path, turn_key: str, *, timeout: float = 30.0) -> str:
    """Poll the turn's durable events for the DERIVED runtime session id that owns it."""
    db = home / "data" / "vool_web0_v2.db"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if db.exists():
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT session_id FROM runtime_session_events WHERE details_json LIKE ? LIMIT 1",
                    (f'%{turn_key}%',),
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            finally:
                conn.close()
            if row:
                return str(row[0])
        time.sleep(0.25)
    raise AssertionError(f"turn {turn_key} never appeared in the event store")


def _call_event(home: Path, turn_key: str) -> dict[str, Any]:
    """The turn's ANSWER call: the last completed call on the turn, which carries the routing
    task kind the selection request used (supporting calls — planners, classifiers — may ride
    the same turn with their own identity)."""
    db = home / "data" / "vool_web0_v2.db"
    assert db.exists(), "no event database"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT COALESCE(details_json,'') FROM runtime_session_events "
            "WHERE event_type='model.call_completed' AND details_json LIKE ? ORDER BY rowid",
            (f'%{turn_key}%',),
        ).fetchall()
    finally:
        conn.close()
    assert rows, f"no model.call_completed for {turn_key}"
    events = [json.loads(row[0]) for row in rows]
    routed = [event for event in events if str(event.get("task_kind") or "").strip()]
    return (routed or events)[-1]


def _row_for_turn(home: Path, turn_key: str) -> dict[str, Any]:
    matches = [row for row in _rows(home) if row.get("turn_key") == turn_key]
    assert matches, f"no sufficiency row for turn {turn_key}; rows={_rows(home)}"
    return matches[0]


def _chat(daemon: ServedDaemon, message: str, session: str, **extra: Any) -> dict[str, Any]:
    return daemon.chat(message, session_id=session, mode="auto", timeout=300.0, **extra)


def test_served_sufficiency_writer_eight_scenarios_restart_and_eligibility(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: BodyAwareScriptedProvider = served["provider"]
    home: Path = served["home"]

    # ---- CERTIFICATION: both models through the daemon's own sealed operator probe ----
    for model in (MODEL_A, MODEL_B):
        result = _certify(daemon, model)
        assert result.get("state") == "verified", f"{model} certification: {result}"
    provider.reset()
    assert _rows(home) == [], "a fresh home must start with an empty observation store"

    # ---- 1. SUCCESSFUL ANSWER: one eligible, exactly-keyed row ----
    marker = _traces(home)[-1]["rowid"] if _traces(home) else 0
    _chat(daemon, ACK_PROMPT, "sfx-success", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    assert trace["fulfillment"] == "fulfilled", trace
    assert trace["stage"] == "success", trace
    row = _row_for_turn(home, trace["turn_key"])
    call = _call_event(home, trace["turn_key"])
    assert row["outcome"] == "verified_success"
    assert row["stage_state"] == "success"
    assert row["provider_id"] == call["provider_id"]
    assert row["model_id"] == call["model_id"] == MODEL_A
    assert row["task_kind"] == str(call.get("task_kind") or ""), row
    assert row["task_kind"] and row["task_kind"] != "unknown", (
        f"the served turn's row must carry the routing task kind, got {row['task_kind']!r}"
    )
    assert row["session_id"] == trace["session_id"], (row, trace)

    # ---- 2. OBJECTIVELY WRONG ANSWER, judged by the runtime's own mechanical validator ----
    # The served constraint machinery (exact_words) PARSES the demand, scores the answer and
    # deterministically repairs a violation before the turn terminalizes, so the terminal truth
    # is a delivered, compliant answer. The writer must record exactly that terminal outcome —
    # it must NOT invent a failure the runtime did not conclude, and it never claims
    # correctness: correctness is owned by the external holdout, not by this store.
    provider.table[MODEL_A] = "The answer is five, my friend."  # wrong AND not one word
    before = len(_rows(home))
    marker = _traces(home)[-1]["rowid"]
    _chat(daemon, "Answer in exactly one word: what is two plus two?", "sfx-wrong", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    assert trace["constraint"]["shape"].get("exact_words") == 1, trace
    assert trace["constraint"]["compliant"] is True, trace  # repaired to one compliant word
    row = _row_for_turn(home, trace["turn_key"])
    assert row["outcome"] == "verified_success" and row["stage_state"] == "success", row
    assert row["model_id"] == MODEL_A and row["task_kind"], row
    assert len(_rows(home)) == before + 1
    provider.table[MODEL_A] = "acknowledged"

    # ---- 3. REFUSAL: a delivered refusal is recorded as its exact stage outcome ----
    marker = _traces(home)[-1]["rowid"]
    provider.table[MODEL_A] = "I am sorry, but I cannot help with that request."
    _chat(daemon, "Write me a hostile message about my neighbour.", "sfx-refusal", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    row = _row_for_turn(home, trace["turn_key"])
    assert trace["stage"] == "success", trace
    assert row["outcome"] == "verified_success" and row["stage_state"] == "success", row
    provider.table[MODEL_A] = "acknowledged"

    # ---- 4. FAILED PROVIDER: a transport failure records NO observation ----
    before = len(_rows(home))
    marker = _traces(home)[-1]["rowid"]
    provider.fail_models.add(MODEL_A)  # the endpoint answers 500: a real transport failure
    out = _chat(daemon, ACK_PROMPT, "sfx-failprov", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    assert trace["stage"] == "provider_error", trace
    assert trace["fulfillment"] == "failed", trace
    assert len(_rows(home)) == before, f"a failed provider turn must write nothing; rows={_rows(home)}"
    provider.fail_models.clear()

    # ---- 5. CANCELLED TURN: the operator's stop records NO observation ----
    # The provider holds the first call long enough that the operator's stop lands while it is
    # still in flight; the runtime honors the stop at the next lane attempt (its own
    # turn_cancelled refusal). The stopped turn's terminal truth is CANCELLED — never a
    # provider failure and never a delivered answer — so the sufficiency writer records
    # NOTHING: an operator stop must never become evidence about a provider, in either
    # direction.
    before = len(_rows(home))
    marker = _traces(home)[-1]["rowid"]
    provider.slow_seconds = 10.0
    stream_result: dict[str, Any] = {}

    def _slow_turn() -> None:
        stream_result["final"] = daemon.chat_stream(
            f"SLOW-TURN {ACK_PROMPT}", session_id="sfx-cancel", model=MODEL_A, turn_id="sfx-cancel-1", timeout=120.0
        )

    worker = threading.Thread(target=_slow_turn)
    worker.start()
    time.sleep(2.0)
    # The server registers the cancel Event under (DERIVED runtime session, turn_id); resolve
    # the derived session for this turn from the turn's own durable events, exactly what a
    # client holding the server's session id would do.
    cancel_session = _session_for_turn(home, "sfx-cancel-1")
    cancel = daemon.cancel_turn(session_id=cancel_session, turn_id="sfx-cancel-1")
    worker.join(timeout=120)
    provider.slow_seconds = 0.0
    assert cancel.get("state") == "cancelled", cancel
    trace = _last_trace(home, after_rowid=marker)
    assert trace["stage"] == "operator_stopped", trace
    assert trace["fulfillment"] == "cancelled", trace
    assert len(_rows(home)) == before, f"a stopped turn must write nothing; rows={_rows(home)}"

    # ---- 6. MODEL SWITCH: each model's row carries its own model id ----
    marker = _traces(home)[-1]["rowid"]
    _chat(daemon, ACK_PROMPT, "sfx-switch", model=MODEL_B)
    trace_b = _last_trace(home, after_rowid=marker)
    row_b = _row_for_turn(home, trace_b["turn_key"])
    assert row_b["model_id"] == MODEL_B, row_b
    assert row_b["provider_id"] == f"{PROVIDER_NAME}:{MODEL_B}"
    assert row_b["task_kind"] != "unknown" and row_b["task_kind"] == _call_event(home, trace_b["turn_key"]).get("task_kind")

    # ---- 7. TWO OVERLAPPING SESSIONS: no cross-session borrowing ----
    marker = _traces(home)[-1]["rowid"]
    provider.slow_seconds = 3.0
    outcomes: dict[str, dict[str, Any]] = {}

    def _overlap(session: str) -> None:
        outcomes[session] = daemon.chat_stream(f"SLOW-TURN {ACK_PROMPT}", session_id=session, model=MODEL_A, timeout=120.0)

    threads = [threading.Thread(target=_overlap, args=(name,)) for name in ("sfx-ov-a", "sfx-ov-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    provider.slow_seconds = 0.0
    overlap_traces = _traces(home, after_rowid=marker)
    assert len(overlap_traces) == 2, overlap_traces
    sessions = {t["session_id"] for t in overlap_traces}
    assert len(sessions) == 2, f"two distinct runtime sessions expected: {overlap_traces}"
    assert {t["turn_key"] for t in overlap_traces} == {t["turn_key"] for t in overlap_traces}
    for t in overlap_traces:
        row = _row_for_turn(home, t["turn_key"])
        assert row["session_id"] == t["session_id"], (row, t)

    # ---- 8. SAME-TURN RETRY: two provider attempts, one row ----
    before = len(_rows(home))
    marker = _traces(home)[-1]["rowid"]
    provider.empty_once.add(MODEL_A)
    provider._emptied.discard(MODEL_A)
    _chat(daemon, ACK_PROMPT, "sfx-retry", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    turn_rows = [row for row in _rows(home) if row.get("turn_key") == trace["turn_key"]]
    assert len(turn_rows) == 1, f"one (turn, provider) is one observation; got {turn_rows}"
    assert turn_rows[0]["outcome"] == "verified_success", turn_rows[0]
    assert len(_rows(home)) == before + 1
    provider.empty_once.clear()

    # ---- 9. RESTART: rows are durable, new turns append ----
    rows_before_restart = _rows(home)
    daemon.stop()
    daemon.start(timeout=240)
    assert _rows(home) == rows_before_restart, "the observation store must survive a cold restart"
    marker = _traces(home)[-1]["rowid"]
    _chat(daemon, ACK_PROMPT, "sfx-post-restart", model=MODEL_A)
    trace = _last_trace(home, after_rowid=marker)
    row = _row_for_turn(home, trace["turn_key"])
    assert row["outcome"] == "verified_success" and row["turn_key"] not in {r["turn_key"] for r in rows_before_restart}

    # ---- 10. SELECTION ELIGIBILITY: only fresh, same-kind, same-model rows count ----
    script = '''
import json, sys
sys.path.insert(0, "@ROOT@")
from core.learning.model_sufficiency import list_sufficiency_observations, sufficiency_adjustment

rows = list_sufficiency_observations()
by_identity = {}
for row in rows:
    if row.get("outcome") != "verified_success":
        continue
    key = (row.get("task_kind"), row.get("provider_id"), row.get("model_id"))
    by_identity.setdefault(key, []).append(row)
eligible = max(by_identity.values(), key=len)
assert len(eligible) >= 3, f"expected >=3 verified rows on one identity, got {{len(eligible)}}"
kind, provider_id, model_id = eligible[0]["task_kind"], eligible[0]["provider_id"], eligible[0]["model_id"]
bonus = sufficiency_adjustment(task_kind=kind, provider_id=provider_id, model_id=model_id)
assert bonus == 0.35, f"three fresh verified successes must cap the bonus, got {{bonus}}"
# a DIFFERENT model's identity is isolated: the second model has a single success -> below floor
other_models = [m for (_, _, m) in by_identity if m != model_id]
for other in other_models:
    other_bonus = sufficiency_adjustment(task_kind=kind, provider_id=provider_id, model_id=other)
    assert other_bonus == 0.0, f"{{other}} must stay below the observation floor, got {{other_bonus}}"
# the unknown kind can never be selected for, so it must adjust nothing anywhere
assert sufficiency_adjustment(task_kind="unknown", provider_id=provider_id, model_id=model_id) == 0.0
print(json.dumps({"identity": [kind, provider_id, model_id], "bonus": bonus, "rows": len(rows)}))
'''
    out = run_in_home(home, script.replace("@ROOT@", str(REPO_ROOT)))
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["bonus"] == 0.35 and summary["rows"] >= 6, summary
