"""CP4 — cross-turn isolation and identity on the REAL /api/chat boundary (green at HEAD).

Served via tests/_reader_served_rig.py: isolated daemon (own port + VOOL_HOME, install
profile hybrid-fallback), certified scripted loopback provider (protocol fixture, never a
real model), obligations seeded directly into the daemon's own database. No paid calls, no
local models. Plan-creation events are emitted BEFORE any network fetch, so the isolation
assertions hold with or without egress.

1. two sessions, two obligations, nudges in both: each session plans only its own subject,
   and every result row carries its own turn/request identity;
2. request identity at the accept-once door: the same body under the same X-Request-ID
   replays the SAME finalization without executing a second turn; a fresh request id on
   identical text executes a fresh turn.
"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.request import Request, urlopen

from kit_lib import GOLD_ANSWER, GOLD_QUESTION


def _events_for_turn(home, session_id: str, turn_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(home / "data" / "vool_web0_v2.db") as conn:
        rows = conn.execute(
            "SELECT event_type, details_json FROM runtime_session_events WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
    return [(event_type, str(details or "")) for event_type, details in rows if turn_id in str(details or "")]


def _seed_obligation(home, session_id: str, operation: str, slots: list[str], request_text: str) -> None:
    with sqlite3.connect(home / "data" / "vool_web0_v2.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO live_data_obligation_memory "
            "(session_id, operation, slots_json, request_text, absorbed_json, source_turn_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '', ?)",
            (session_id, operation, json.dumps(slots), request_text, json.dumps([request_text]),
             "2026-09-06T00:00:00.000000+00:00"),
        )
        conn.commit()


def test_served_two_sessions_keep_obligations_and_turn_identity_isolated(tmp_path):
    from tests import _reader_served_rig as rig

    with rig.CapturingProvider(default="FIXTURE-REPLY: served isolation probe.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=120).get("state") == "verified"

            session_a = rig.canonical_session("iso-gold")
            session_b = rig.canonical_session("iso-weather")
            _seed_obligation(daemon.home, session_a, "market_quote", ["Gold"], GOLD_QUESTION)
            _seed_obligation(daemon.home, session_b, "weather_lookup", ["Kaunas"], "Get weather for Kaunas.")

            daemon.chat(
                "and now?",
                session_id=session_a, turn_id="turn-a-nudge",
                messages=[
                    {"role": "user", "content": GOLD_QUESTION},
                    {"role": "assistant", "content": GOLD_ANSWER},
                    {"role": "user", "content": "and now?"},
                ],
            )
            daemon.chat(
                "and now?",
                session_id=session_b, turn_id="turn-b-nudge",
                messages=[
                    {"role": "user", "content": "Get weather for Kaunas."},
                    {"role": "assistant", "content": "Kaunas: 18 C."},
                    {"role": "user", "content": "and now?"},
                ],
            )

            events_a = _events_for_turn(daemon.home, session_a, "turn-a-nudge")
            events_b = _events_for_turn(daemon.home, session_b, "turn-b-nudge")

            plans_a = [details for kind, details in events_a if kind == "live_data_plan_created"]
            plans_b = [details for kind, details in events_b if kind == "live_data_plan_created"]
            assert plans_a and "Gold" in plans_a[0] and "Kaunas" not in plans_a[0], plans_a[0][:400]
            assert plans_b and "Kaunas" in plans_b[0] and "Gold" not in plans_b[0], plans_b[0][:400]

            # Identity: every event row for a turn carries that turn's id, and each session's
            # rows never mention the other session's turn.
            assert events_a and events_b
            all_a = " ".join(details for _, details in events_a)
            all_b = " ".join(details for _, details in events_b)
            assert "turn-b-nudge" not in all_a
            assert "turn-a-nudge" not in all_b

            with sqlite3.connect(daemon.home / "data" / "vool_web0_v2.db") as conn:
                requests = {
                    row[0] for row in conn.execute(
                        "SELECT DISTINCT json_extract(details_json, '$.request_id') "
                        "FROM runtime_session_events WHERE session_id IN (?, ?)",
                        (session_a, session_b),
                    )
                    if row[0]
                }
            assert len(requests) >= 2, requests
        finally:
            daemon.stop()


def test_served_request_identity_replays_only_under_the_same_request_id(tmp_path):
    from tests import _reader_served_rig as rig

    with rig.CapturingProvider(default="FIXTURE-REPLY: identity probe.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=120).get("state") == "verified"
            session = rig.canonical_session("identity")
            body = json.dumps({
                "messages": [{"role": "user", "content": "hello there, general knowledge only"}],
                "stream": False, "session_id": session, "model": daemon.model, "mode": "auto",
                "turn_id": "identity-turn",
            }).encode("utf-8")

            def post(request_id: str | None) -> dict:
                headers = {"Content-Type": "application/json"}
                if request_id:
                    headers["X-Request-ID"] = request_id
                request = Request(f"{daemon.base_url}/api/chat", data=body, headers=headers, method="POST")
                with urlopen(request, timeout=120) as response:
                    return json.loads(response.read().decode("utf-8"))

            first = post("audit-fixed-request-id-1")
            # Let any post-response background work of the first turn (conversation mining,
            # grounding sweeps) settle BEFORE clearing the capture, so the replay window
            # measures only what the replay itself dispatches.
            time.sleep(10)
            provider.reset()
            replayed = post("audit-fixed-request-id-1")
            # Replay window: the accept-once door answers from the finalization store and
            # must not dispatch anything.
            replay_window = list(provider.payloads())
            provider.reset()
            fresh = post(None)

            def _content(result: dict) -> str:
                message = result.get("message") or {}
                return str(message.get("content") or "")

            # The buffered shape carries the answer in message.content; the replay branch
            # answers from the a7 finalization with done_reason "stop" and a
            # response.replay commit, and never reaches the provider again.
            assert _content(first), first
            assert _content(replayed) == _content(first), (first, replayed)
            assert replayed.get("done_reason") == "stop"
            commit = replayed.get("vool_response_commit") or {}
            assert commit.get("type") == "response.replay", commit
            assert replay_window == [], f"a replay must not reach the provider again: {replay_window}"
            assert _content(fresh)
            assert len(provider.payloads()) >= 1, "a fresh request id must execute a fresh turn"

            with sqlite3.connect(daemon.home / "data" / "vool_web0_v2.db") as conn:
                turns = conn.execute(
                    "SELECT COUNT(*) FROM runtime_session_events WHERE session_id = ? "
                    "AND event_type = 'task_received'",
                    (session,),
                ).fetchone()[0]
            assert turns == 2, f"expected exactly two executed turns (first + fresh), saw {turns}"
        finally:
            daemon.stop()
