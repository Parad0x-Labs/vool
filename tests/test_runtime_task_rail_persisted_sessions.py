from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from core.runtime_task_rail_event_render import RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
from core.runtime_task_rail_summary_client import RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT


def _persisted_session(
    session_id: str,
    review_state: str,
    *,
    approval_state: str = "cleared",
    validation_state: str = "passed",
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "status": "completed",
        "request_preview": f"Persisted {review_state} review",
        "task_class": "chat",
        "event_count": 4,
        "updated_at": "2026-08-10T12:00:00Z",
        "execution_history": {
            "bounded_execution": {
                "approval_state": approval_state,
                "model_review_state": review_state,
                "validation_state": validation_state,
            }
        },
    }


def _render_persisted_sessions(sessions: list[dict[str, object]]) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the shipped task-rail renderer probe")
    program = (
        RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
        + RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
        + "\nconst sessions = "
        + json.dumps(sessions)
        + r""";
let selectedSessionId = sessions[0].session_id;
const knownEvents = [];
const sessionListEl = {
  innerHTML: '',
  querySelectorAll() { return []; },
};
function escapeHtml(value) { return String(value == null ? '' : value); }
function statusClass(value) { return String(value || ''); }
function shortTime(value) { return String(value || ''); }
renderSessions();
console.log(JSON.stringify({ html: sessionListEl.innerHTML }));
"""
    )
    result = subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return str(json.loads(result.stdout)["html"])


def _session_card(html: str, session_id: str) -> str:
    marker = f'<div class="session-id">{session_id}</div>'
    start = html.index(marker)
    end = html.index("</button>", start)
    return html[start:end]


def test_persisted_sessions_execute_the_shipped_renderer_with_distinct_review_and_validation_states() -> None:
    sessions = [
        _persisted_session("review-not-run", "not_run", validation_state="not_run"),
        _persisted_session("review-running", "running"),
        _persisted_session("review-passed", "passed"),
        _persisted_session("review-flagged", "flagged", approval_state="pending"),
        _persisted_session("review-blocked", "blocked"),
        _persisted_session("review-degraded", "degraded"),
        _persisted_session("review-runtime-failed", "runtime_failed"),
    ]

    html = _render_persisted_sessions(sessions)

    not_run = _session_card(html, "review-not-run")
    assert "model review" not in not_run
    assert "validation" not in not_run

    for state in ("running", "passed", "flagged", "blocked", "degraded", "runtime_failed"):
        card = _session_card(html, f"review-{state.replace('_', '-')}")
        assert f"model review {state}" in card
        assert "validation passed" in card

    flagged = _session_card(html, "review-flagged")
    assert "approval pending" in flagged
    assert "model review flagged" in flagged

    blocked = _session_card(html, "review-blocked")
    assert "model review blocked" in blocked
    assert "model review flagged" not in blocked

    degraded = _session_card(html, "review-degraded")
    assert "model review degraded" in degraded
    assert "model review flagged" not in degraded

    runtime_failed = _session_card(html, "review-runtime-failed")
    assert "model review runtime_failed" in runtime_failed
    assert "model review flagged" not in runtime_failed
