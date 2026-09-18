"""Served `_req_token` blocker — pass-001 regressions (the SERVED sibling of A9 RC-3).

Root cause (evidence: council/served-req-token-root-cause/pass-001-openrouter-glm53flash-20260827):
`24f7e9f9` deleted the else-branch `_req_token = None` from `VoolAgent.run_once`
while keeping the finally's unconditional read. The A0 door (`dispatch_post`)
binds `_CURRENT_REQUEST_ID` BEFORE `run_once`, so every served turn takes the
else branch — the turn computed its answer and THEN died at the finally with
``UnboundLocalError: cannot access local variable '_req_token'``:

  * the computed answer was replaced by a 500 (buffered) / turn_failed (streamed)
  * `update_runtime_attempt` + `set_execution_terminal` never ran → the attempt
    stayed RUNNING and its L0 fence row stayed ACTIVE forever (A9 RC-3 at runtime)
  * `clear_execution_context()` was skipped → the execution-identity fence tuple
    leaked past the turn on the serving context
  * a genuine inner exception was masked by the UnboundLocalError

Every existing suite is CLI-shaped: `run_once` tests run with NO bound context
(the interior lane binds its own token), and every served test injects a fake
`run_agent`/`run_agent_provider` — so a 100%-deterministic served crash stayed
invisible. These regressions drive the REAL seams end to end: the door's own
`accept_invocation` + `set_request_context`, the real `run_once`, and the real
`dispatch_post` with a real agent on all three served lanes. On any head that
reverts the repair, every test here fails with exactly the production failure.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from storage.db import configure_default_db_path, get_connection
from storage.migrations import run_migrations

TERMINAL_ATTEMPT_STATES = {"SUCCEEDED", "FAILED_PROVIDER", "CANCELLED"}
TERMINAL_EXECUTION_STATES = {"COMPLETED", "FAILED", "CANCELLED"}

SESSION_HANDLE = "served-req-token-pass001"
TURN_TEXT = "Say hello."


def _served_session_id() -> str:
    # Mirrors stable_openclaw_session_id's digest for a non-canonical handle.
    return f"openclaw:{hashlib.sha256(SESSION_HANDLE.encode('utf-8')).hexdigest()[:20]}"


class _ServedLaneHarness(unittest.TestCase):
    """One temp DB shared by both durable stores, per test (A9 pass-001 pattern)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "served_req_token.db"
        run_migrations(db_path=str(self._db_path))
        configure_default_db_path(str(self._db_path))
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )

        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        # Lazily-created tables gate on a one-shot module flag; a fresh temp DB
        # in a long-lived pytest process would never see them. Re-arm and let
        # every owner re-run its idempotent CREATE IF NOT EXISTS here.
        import core.task_state_machine as _task_state
        import core.trace_id as _trace_id

        for _module in (_trace_id, _task_state):
            _module._TABLE_READY = False
            _module._init_table()

    def tearDown(self) -> None:
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )
        from core.semantic import semantic_admissions

        # Whatever the turn under test did, the harness must not leak its own
        # bindings into the next test (the door token is the harness's to reset,
        # exactly as dispatch_post's finally does in production).
        semantic_admissions.clear_execution_context()
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        configure_default_db_path(None)
        import core.task_state_machine as _task_state
        import core.trace_id as _trace_id

        for _module in (_trace_id, _task_state):
            _module._TABLE_READY = False
        self._tmp.cleanup()

    def _make_agent(self):
        from apps.vool_agent import VoolAgent

        return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def _bind_door(self, raw_value: str):
        """Bind the A0 request context exactly as dispatch_post does
        (core/web/api/service.py: accept_invocation(external_kind='http') then
        set_request_context(accepted['request_id'])). Returns (token, accepted)."""
        from core.invocation.ledger import accept_invocation
        from core.semantic.semantic_admissions import set_request_context

        accepted = accept_invocation(
            external_kind="http",
            external_value=raw_value,
            principal="owner_local",
            session_binding="",
            privacy_local_only=True,
            raw_digest="sha256:" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest(),
        )
        assert str(accepted.get("outcome") or "").startswith("ACCEPTED"), accepted
        return set_request_context(accepted["request_id"]), accepted

    def _unbind_door(self, token) -> None:
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        if token is not None:
            _CURRENT_REQUEST_ID.reset(token)

    def _attempt_rows(self) -> list[dict]:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT attempt_id, session_id, lifecycle_state, execution_id"
                " FROM runtime_attempts ORDER BY created_at, rowid"
            ).fetchall()
            return [
                {
                    "attempt_id": row[0],
                    "session_id": row[1],
                    "lifecycle_state": row[2],
                    "execution_id": row[3],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def _execution_rows(self) -> list[dict]:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT execution_id, request_id, state FROM executions ORDER BY rowid"
            ).fetchall()
            return [
                {"execution_id": row[0], "request_id": row[1], "state": row[2]}
                for row in rows
            ]
        finally:
            conn.close()

    def _assert_all_terminal(self, *, attempts: int) -> None:
        """Every attempt terminal (no zombie RUNNING/RECEIVED), every L0 fence
        row terminal (never ACTIVE) — the A9 RC-3 law on the served lane."""
        attempt_rows = self._attempt_rows()
        self.assertEqual(len(attempt_rows), attempts)
        for row in attempt_rows:
            self.assertIn(
                row["lifecycle_state"],
                TERMINAL_ATTEMPT_STATES,
                f"attempt {row['attempt_id']} stuck at {row['lifecycle_state']}",
            )
        execution_rows = self._execution_rows()
        self.assertTrue(execution_rows)
        for row in execution_rows:
            self.assertIn(
                row["state"],
                TERMINAL_EXECUTION_STATES,
                f"L0 fence {row['execution_id']} stuck at {row['state']}",
            )


def _chat_body(**overrides):
    body = {
        "messages": [{"role": "user", "content": TURN_TEXT}],
        "session_id": SESSION_HANDLE,
        "turn_id": "served-turn-001",
    }
    body.update(overrides)
    return body


def _dispatch_chat(agent, *, path: str = "/api/chat", body: dict | None = None):
    """One REAL served turn through the production door with a real agent."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path=path,
        body=body if body is not None else _chat_body(),
        headers={"Host": "127.0.0.1"},
        runtime=RuntimeServices(display_name="VOOL", agent=agent),
        model_name="test-backend",
        workspace_root_provider=lambda: tempfile.mkdtemp(prefix="served-ws-"),
        client_host="127.0.0.1",
        request_id=f"auto-{uuid.uuid4().hex}",
    )


def _drain_stream(resp) -> list[dict]:
    parsed: list[dict] = []
    for chunk in resp.stream:
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return parsed


# ---------------------------------------------------------------------------
# 1 + 5 — the unit-precise served condition: door-bound run_once
# ---------------------------------------------------------------------------


class DoorBoundRunOnceTests(_ServedLaneHarness):
    def test_door_bound_run_once_returns_the_computed_answer(self) -> None:
        # This exact call shape is the production failure: bind the door, call
        # the real run_once. Pre-repair this raised UnboundLocalError AFTER the
        # inner call had already computed the answer.
        from core.semantic.semantic_admissions import (
            _CURRENT_REQUEST_ID,
            current_execution_identity,
        )

        agent = self._make_agent()
        door_token, accepted = self._bind_door(f"auto-{uuid.uuid4().hex}")
        try:
            result = agent.run_once(
                TURN_TEXT,
                session_id_override=_served_session_id(),
                source_context={"surface": "api", "platform": "api"},
            )
        finally:
            door_binding_alive = _CURRENT_REQUEST_ID.get()
            self._unbind_door(door_token)
        self.assertIsInstance(result, dict)
        self.assertTrue(str(result.get("response") or "").strip())
        # The door owns its token: run_once must leave the binding alone
        # (A0 request-door ownership model unchanged).
        self.assertEqual(door_binding_alive, accepted["request_id"])
        # Fence hygiene: clear_execution_context ran even though the door
        # binding was still live when the finally executed.
        self.assertIsNone(current_execution_identity())

    def test_door_bound_turn_inner_exception_is_never_masked(self) -> None:
        # The finally's UnboundLocalError used to REPLACE the true error. The
        # inner exception type must surface untouched.
        agent = self._make_agent()
        door_token, _accepted = self._bind_door(f"auto-{uuid.uuid4().hex}")
        try:
            with mock.patch.object(
                type(agent),
                "_run_once_inner",
                side_effect=ValueError("inner boom"),
            ):
                with self.assertRaises(ValueError) as ctx:
                    agent.run_once(
                        TURN_TEXT,
                        session_id_override=_served_session_id(),
                        source_context={"surface": "api", "platform": "api"},
                    )
            self.assertIn("inner boom", str(ctx.exception))
            self.assertNotIn("UnboundLocal", type(ctx.exception).__name__)
        finally:
            self._unbind_door(door_token)
        # ...and the failed served turn still terminalizes (see next class for
        # the row-level assertions).


# ---------------------------------------------------------------------------
# 3 + 4 + 5 — terminalization and fence hygiene on the served lane
# ---------------------------------------------------------------------------


class ServedTurnTerminalizationTests(_ServedLaneHarness):
    def test_successful_door_bound_turn_terminalizes_attempt_and_fence(self) -> None:
        from core.semantic.semantic_admissions import current_execution_identity

        agent = self._make_agent()
        door_token, _accepted = self._bind_door(f"auto-{uuid.uuid4().hex}")
        try:
            with mock.patch.object(
                type(agent),
                "_run_once_inner",
                return_value={"success": True, "response": "served answer"},
            ):
                result = agent.run_once(
                    TURN_TEXT,
                    session_id_override=_served_session_id(),
                    source_context={"surface": "api", "platform": "api"},
                )
            self.assertTrue(result["success"])
        finally:
            self._unbind_door(door_token)
        self._assert_all_terminal(attempts=1)
        self.assertEqual(self._attempt_rows()[0]["lifecycle_state"], "SUCCEEDED")
        self.assertEqual(self._execution_rows()[0]["state"], "COMPLETED")
        self.assertIsNone(current_execution_identity())

    def test_failed_door_bound_turn_is_never_a_zombie(self) -> None:
        from core.semantic.semantic_admissions import current_execution_identity

        agent = self._make_agent()
        door_token, _accepted = self._bind_door(f"auto-{uuid.uuid4().hex}")
        try:
            with mock.patch.object(
                type(agent),
                "_run_once_inner",
                return_value={"success": False, "response": "no answer"},
            ):
                agent.run_once(
                    TURN_TEXT,
                    session_id_override=_served_session_id(),
                    source_context={"surface": "api", "platform": "api"},
                )
        finally:
            self._unbind_door(door_token)
        self._assert_all_terminal(attempts=1)
        self.assertEqual(self._attempt_rows()[0]["lifecycle_state"], "FAILED_PROVIDER")
        self.assertEqual(self._execution_rows()[0]["state"], "FAILED")
        self.assertIsNone(current_execution_identity())

    def test_raising_door_bound_turn_still_terminalizes_and_stays_clean(self) -> None:
        from core.semantic.semantic_admissions import current_execution_identity

        agent = self._make_agent()
        door_token, _accepted = self._bind_door(f"auto-{uuid.uuid4().hex}")
        try:
            with mock.patch.object(
                type(agent),
                "_run_once_inner",
                side_effect=ValueError("inner boom"),
            ):
                with self.assertRaises(ValueError):
                    agent.run_once(
                        TURN_TEXT,
                        session_id_override=_served_session_id(),
                        source_context={"surface": "api", "platform": "api"},
                    )
        finally:
            self._unbind_door(door_token)
        self._assert_all_terminal(attempts=1)
        self.assertEqual(self._attempt_rows()[0]["lifecycle_state"], "FAILED_PROVIDER")
        self.assertEqual(self._execution_rows()[0]["state"], "FAILED")
        self.assertIsNone(current_execution_identity())


# ---------------------------------------------------------------------------
# 2 + 3 + 4 + 6 — the full production door: dispatch_post /api/chat
# ---------------------------------------------------------------------------


class ServedApiChatDoorTests(_ServedLaneHarness):
    def test_buffered_api_chat_serves_an_answer_instead_of_the_req_token_500(self) -> None:
        agent = self._make_agent()
        resp = _dispatch_chat(agent)
        self.assertEqual(resp.status, 200, getattr(resp, "body", b""))
        payload = json.loads(resp.body)
        self.assertTrue(payload["done"])
        self.assertTrue(
            str(payload["message"]["content"]).strip(),
            "the served answer must not be empty",
        )
        self._assert_all_terminal(attempts=1)

    def test_second_served_turn_on_the_same_agent_succeeds_cleanly(self) -> None:
        # The crash used to leak the fence tuple and zombie rows on the serving
        # context; the next turn on the same agent/thread must still work.
        agent = self._make_agent()
        from core.semantic.semantic_admissions import current_execution_identity

        first = _dispatch_chat(agent, body=_chat_body(turn_id="served-turn-001"))
        self.assertEqual(first.status, 200, getattr(first, "body", b""))
        self.assertIsNone(current_execution_identity())
        second = _dispatch_chat(agent, body=_chat_body(turn_id="served-turn-002"))
        self.assertEqual(second.status, 200, getattr(second, "body", b""))
        payload = json.loads(second.body)
        self.assertTrue(payload["done"])
        self.assertTrue(str(payload["message"]["content"]).strip())
        self.assertIsNone(current_execution_identity())
        self._assert_all_terminal(attempts=2)


# ---------------------------------------------------------------------------
# 7 — streamed /api/chat parity
# ---------------------------------------------------------------------------


class StreamedApiChatParityTests(_ServedLaneHarness):
    def test_streamed_api_chat_completes_and_terminalizes(self) -> None:
        agent = self._make_agent()
        resp = _dispatch_chat(agent, body=_chat_body(stream=True))
        self.assertEqual(resp.status, 200)
        self.assertIn("ndjson", resp.content_type)
        lines = _drain_stream(resp)
        self.assertTrue(lines, "stream produced no frames")
        terminal = [line for line in lines if line.get("done") is True]
        self.assertEqual(len(terminal), 1, f"expected one terminal frame, got {lines!r}")
        self.assertEqual(terminal[0].get("done_reason"), "stop")
        content = "".join(
            str(line.get("message", {}).get("content") or "")
            for line in lines
            if isinstance(line.get("message"), dict)
        )
        self.assertTrue(content.strip(), "stream served no answer content")
        self.assertNotIn("turn_failed", [line.get("done_reason") for line in lines])
        self._assert_all_terminal(attempts=1)


# ---------------------------------------------------------------------------
# 8 — /v1/chat/completions and /api/generate parity
# ---------------------------------------------------------------------------


class OpenAiCompatAndGenerateParityTests(_ServedLaneHarness):
    def test_v1_chat_completions_serves_an_openai_answer(self) -> None:
        agent = self._make_agent()
        resp = _dispatch_chat(agent, path="/v1/chat/completions")
        self.assertEqual(resp.status, 200, getattr(resp, "body", b""))
        payload = json.loads(resp.body)
        content = str(payload["choices"][0]["message"]["content"]).strip()
        self.assertTrue(content)
        self._assert_all_terminal(attempts=1)

    def test_api_generate_serves_an_answer(self) -> None:
        agent = self._make_agent()
        resp = _dispatch_chat(
            agent,
            path="/api/generate",
            body={"prompt": TURN_TEXT, "turn_id": "served-gen-001"},
        )
        self.assertEqual(resp.status, 200, getattr(resp, "body", b""))
        payload = json.loads(resp.body)
        self.assertTrue(payload["done"])
        self.assertTrue(str(payload.get("response") or "").strip())
        self._assert_all_terminal(attempts=1)


# ---------------------------------------------------------------------------
# 10 — the served door surfaces the INNER error, never the UnboundLocalError
# ---------------------------------------------------------------------------


class ServedExceptionHonestyTests(_ServedLaneHarness):
    def test_buffered_chat_500_carries_the_inner_error_class(self) -> None:
        agent = self._make_agent()
        with mock.patch.object(
            type(agent),
            "_run_once_inner",
            side_effect=ValueError("inner boom"),
        ):
            resp = _dispatch_chat(agent)
        self.assertEqual(resp.status, 500)
        payload = json.loads(resp.body)
        self.assertIn("ValueError", payload["error"])
        self.assertNotIn("UnboundLocalError", payload["error"])
        self._assert_all_terminal(attempts=1)
        self.assertEqual(self._attempt_rows()[0]["lifecycle_state"], "FAILED_PROVIDER")


# ---------------------------------------------------------------------------
# 9 — the interior fallback (CLI / one-shot / discord in-process) still works
# ---------------------------------------------------------------------------


class InteriorFallbackStillWorksTests(_ServedLaneHarness):
    def test_interior_lane_binds_and_resets_its_own_context(self) -> None:
        from core.semantic.semantic_admissions import (
            _CURRENT_REQUEST_ID,
            current_execution_identity,
        )

        agent = self._make_agent()
        self.assertEqual(_CURRENT_REQUEST_ID.get(), "")
        result = agent.run_once(
            TURN_TEXT,
            session_id_override="interior-sess",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIsInstance(result, dict)
        self.assertTrue(str(result.get("response") or "").strip())
        # run_once accepted its own invocation and reset its own token.
        self.assertEqual(_CURRENT_REQUEST_ID.get(), "")
        self.assertIsNone(current_execution_identity())
        self._assert_all_terminal(attempts=1)
        # Real inner results carry no `success` key, so the turn-door default
        # (turn_ok=True) decides the verdict; either terminal state is lawful
        # here — the pinned verdicts live in ServedTurnTerminalizationTests.
        self.assertIn(
            self._attempt_rows()[0]["lifecycle_state"], TERMINAL_ATTEMPT_STATES
        )


if __name__ == "__main__":
    unittest.main()
