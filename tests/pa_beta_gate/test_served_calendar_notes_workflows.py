"""pa_beta_gate — SERVED ingress workflows for the calendar/notes vertical.

These drive the REAL agent turn (`VoolAgent.run_once`) — the served ingress, routing,
demand decomposition and operator dispatch — not the parser/dispatcher boundary directly.
The model lane is stubbed with a deterministic stand-in exactly the way the runtime's own
mixed-demand tests do (labelled: MODEL STAND-IN); every calendar/notes effect executes
for real against the disposable CalDAV service and the workspace filesystem.

Proven here: multi-obligation messages preserve every obligation or leave it explicitly
pending; follow-ups ("option 1, propose ...") resolve the prior turn's offer in the same
session; approvals resume; history records the turns; and a foreign session never
resolves another chat's offer.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

from ._caldav_service import start_caldav_fixture

pytestmark = [pytest.mark.pa_beta]

VILNIUS_CAL = "/calendars/vilnius/"

_ORIGINAL_COMPOUND = (
    "Check Tuesday afternoon for a free 30-minute slot and save a note "
    "with agenda: packaging and release checks."
)
_NOVEL_COMPOUND = (
    "Look at Thursday morning on my calendar for a free 20-minute slot and "
    "save a note with agenda: venue lockdown list."
)


@pytest.fixture
def served_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Athens")

    server, state, base_url, port = start_caldav_fixture(calendars={VILNIUS_CAL: "Vilnius"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "caldav")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base_url)
    monkeypatch.setenv("VOOL_CALENDAR_ID", VILNIUS_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")

    fixed = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: fixed.astimezone(ZoneInfo("Europe/Athens")))

    harness = _Harness(f"sess-cal-{uuid.uuid4().hex[:8]}")
    yield {"harness": harness, "state": state, "workspace": workspace, "port": port}
    harness.close()
    server.shutdown()
    server.server_close()


def _turn(harness, text: str, workspace: str, *, session_id: str | None = None, operating_mode: str = "auto") -> str:
    """One REAL served turn (model lane stubbed with the deterministic stand-in).

    `operating_mode` is the user's own mode selection: "auto" lets the authenticated
    provider READ run like any governed network read; "manual" keeps the mode matrix's
    prompt for it (asserted as a negative control below).
    """
    import contextlib

    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="served-cal", provider_id="served-cal",
        provider_name="stand-in", model_name="served-cal",
        output_text="MODEL STAND-IN: no general answer needed for this operator turn.",
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = str(workspace)
    context["operating_mode"] = operating_mode
    with contextlib.ExitStack() as stack:
        stack.enter_context(_patch(harness.agent.memory_router, "resolve", decision))
        result = harness.agent.run_once(text, source_context=context, session_id_override=session_id or harness.session_id)
    return str(result.get("response") or "")


def _patch(target, attr, decision):
    from unittest import mock

    return mock.patch.object(target, attr, return_value=decision)


def _approval_id(text: str) -> str:
    match = re.search(r"approve calendar ([0-9a-f-]{36})", text)
    assert match, f"no approval id offered in the served answer: {text!r}"
    return match.group(1)


def test_served_original_journey_preserves_both_obligations(served_env):
    harness = served_env["harness"]
    state = served_env["state"]
    workspace = served_env["workspace"]
    state.seed_event(VILNIUS_CAL, "busy@fixture", summary="Standing sync",
                     start=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc), minutes=60)

    answer = _turn(harness, _ORIGINAL_COMPOUND, workspace)
    # BOTH obligations of the compound message are preserved in one served answer.
    assert "Standing sync" in answer, f"availability half missing: {answer!r}"
    assert "Free 30-minute options" in answer, answer
    notes = list((workspace / "notes").glob("*.md")) if (workspace / "notes").is_dir() else []
    assert notes, f"note half missing; answer was {answer!r}"
    assert "packaging and release checks" in notes[0].read_text(encoding="utf-8")

    # Follow-up reference resolves the SAME session's offer; the served turn answers.
    proposal = _turn(harness, 'option 1, propose "Project review"', workspace)
    assert "approval_required" in proposal or "Reply with: approve calendar" in proposal, proposal
    action_id = _approval_id(proposal)
    assert not any(row["summary"] == "Project review" for row in state.snapshot()[VILNIUS_CAL].values()), "proposal is not an event"

    executed = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in executed, executed
    assert any(row["summary"] == "Project review" for row in state.snapshot()[VILNIUS_CAL].values())

    # History: the turns are recorded in the owning session's conversation log.
    from core.persistent_memory import recent_conversation_events

    events = recent_conversation_events(harness.session_id, limit=10)
    recorded = " ".join(str(event) for event in events)
    assert "approve calendar" in recorded or len(events) >= 2, "served turns must land in session history"


def test_served_novel_journey_and_cross_session_offer_isolation(served_env):
    harness = served_env["harness"]
    state = served_env["state"]
    workspace = served_env["workspace"]
    state.seed_event(VILNIUS_CAL, "blocker@fixture", summary="Team demo",
                     start=datetime(2026, 9, 17, 6, 30, tzinfo=timezone.utc), minutes=90)

    answer = _turn(harness, _NOVEL_COMPOUND, workspace)
    assert "Team demo" in answer, answer
    assert "Free 20-minute options" in answer, answer
    assert any("venue lockdown list" in path.read_text(encoding="utf-8") for path in (workspace / "notes").glob("*.md"))

    proposal = _turn(harness, 'option 2, propose "Lockdown prep"', workspace)
    action_id = _approval_id(proposal)

    # A FOREIGN session cannot resolve this chat's offer or approval: no leakage.
    foreign = _Harness(f"sess-foreign-{uuid.uuid4().hex[:8]}")
    try:
        foreign_answer = _turn(foreign, "option 2, propose \"Lockdown prep\"", served_env["workspace"], session_id=foreign.session_id)
        assert "option 2 is not among" in foreign_answer or "don't have a live slot offer" in foreign_answer, foreign_answer
        stolen = _turn(foreign, f"approve calendar {action_id}", served_env["workspace"], session_id=foreign.session_id)
        assert "belongs to a different chat session" in stolen, stolen
    finally:
        foreign.close()
    assert not any(row["summary"] == "Lockdown prep" for row in state.snapshot()[VILNIUS_CAL].values())

    # The owning session completes the journey: approve, then cancel only the event.
    executed = _turn(harness, f"approve calendar {action_id}", workspace)
    assert "created on the provider and verified" in executed, executed
    cancel = _turn(harness, 'cancel the "Lockdown prep" event', workspace)
    cancel_id = _approval_id(cancel)
    cancelled = _turn(harness, f"approve calendar {cancel_id}", workspace)
    assert "verified gone" in cancelled, cancelled
    assert not any(row["summary"] == "Lockdown prep" for row in state.snapshot()[VILNIUS_CAL].values())
    # The NOTE survives the event's cancellation — obligations are independent.
    assert any("venue lockdown list" in path.read_text(encoding="utf-8") for path in (workspace / "notes").glob("*.md"))


def test_served_provider_create_never_auto_books_in_any_mode(served_env):
    """Negative control: whatever the mode, a provider CREATE only happens on its own
    explicit approval turn — a propose clause stages, it never books, and the provider
    state stays empty until the approval arrives."""
    harness = served_env["harness"]
    state = served_env["state"]
    workspace = served_env["workspace"]
    for mode in ("manual", "auto"):
        answer = _turn(harness, 'propose "Never auto" on 2026-09-15 16:00 Europe/Athens for 30m', workspace, operating_mode=mode)
        assert "approve calendar" in answer, f"{mode}: no staged approval turn: {answer!r}"
        assert not any(row["summary"] == "Never auto" for row in state.snapshot()[VILNIUS_CAL].values()), f"{mode}: booked without approval"
