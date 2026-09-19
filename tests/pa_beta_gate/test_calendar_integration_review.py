"""Independent acceptance regressions; candidate implementation remains unchanged."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.kas.adapters.caldav import CalDavCalendarAdapter, _parse_vevent
from core.kas.contract import AdapterConfig, CalEvent
from tests.pa_beta_gate.test_calendar_provider_vertical import VILNIUS_CAL, _run, vilnius_env


def _adapter(base="https://calendar.example.test/dav/user/"):
    return CalDavCalendarAdapter(transport=None, config=AdapterConfig(provider_id="caldav", base_url=base))


@pytest.mark.parametrize("title,new_title", [("Project review", "Release review"), ("Site visit", "Inspection")])
def test_update_has_one_vevent_component(title, new_title):
    adapter = _adapter()
    event = CalEvent(provider_id="caldav", uid="review-event", calendar_id=VILNIUS_CAL,
                     summary=title, start_utc="2026-09-15T13:00:00+00:00",
                     end_utc="2026-09-15T13:30:00+00:00", tz_name="Europe/Athens")
    original = adapter._render_event(event, uid=event.uid)
    assert original.count("BEGIN:VEVENT") == 1
    current = _parse_vevent(original, provider_id="caldav", calendar_id=VILNIUS_CAL,
                            href="/calendars/vilnius/review.ics", etag='"v1"')
    updated = adapter._render_event(replace(current, summary=new_title), uid=event.uid, base=current.raw_component)
    assert updated.count("BEGIN:VEVENT") == updated.count("END:VEVENT") == 1, updated
    assert "SUMMARY:" + new_title in updated


@pytest.mark.parametrize("base,href,expected", [
    ("https://calendar.example.test/dav/user/", "/calendars/team/event.ics", "https://calendar.example.test/calendars/team/event.ics"),
    ("https://calendar.example.test/remote.php/dav/", "/dav/calendars/alex/", "https://calendar.example.test/dav/calendars/alex/"),
])
def test_provider_root_relative_href_keeps_its_origin_path(base, href, expected):
    assert _adapter(base)._absolute_href(href) == expected


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_notes_never_follow_a_symlink_outside_the_workspace(tmp_path, monkeypatch, kind):
    from core.operator import notes
    workspace, outside = tmp_path / "project", tmp_path / "other-project"
    workspace.mkdir()
    outside.mkdir()
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    if kind == "directory":
        (workspace / "notes").symlink_to(outside, target_is_directory=True)
        try:
            notes.create_note(title="Agenda", body="private project note", session_id="review")
        except (OSError, ValueError, PermissionError):
            pass
        assert list(outside.iterdir()) == [], "note creation wrote into another project's directory"
    else:
        foreign = outside / "foreign.md"
        foreign.write_text("---\ntitle: Foreign\n---\n\nPRIVATE_OUTSIDE_MARKER\n")
        (workspace / "notes").mkdir()
        (workspace / "notes" / "linked.md").symlink_to(foreign)
        try:
            rows = notes.search_notes(query="PRIVATE_OUTSIDE_MARKER")
        except (OSError, ValueError, PermissionError):
            rows = []
        assert not rows, "note search read a file outside the project boundary"


@pytest.mark.parametrize("title,hour", [("Project review", 11), ("Site inspection", 14)])
def test_move_rechecks_new_slot_conflicts_after_preview(vilnius_env, title, hour):
    session = "review-conflict-" + str(hour)
    _, proposal = _run(f'propose "{title}" on 2026-09-15 16:00 Europe/Athens for 30m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    _, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    uid = created.details["uid"]
    before = vilnius_env["state"].snapshot()[VILNIUS_CAL][uid]["start_utc"]
    _, move = _run(f'move the "{title}" event to Friday at {hour}:00 Europe/Athens', session_id=session)
    assert move.status == "approval_required", move.response_text
    vilnius_env["state"].seed_event(VILNIUS_CAL, "late-conflict", summary="New conflicting event",
                                   start=datetime(2026, 9, 11, hour - 3, tzinfo=timezone.utc), minutes=60)
    _, result = _run(f"approve calendar {move.details['action_id']}", session_id=session)
    after = vilnius_env["state"].snapshot()[VILNIUS_CAL][uid]["start_utc"]
    assert not result.ok and after == before, (result.status, result.response_text, before, after)
