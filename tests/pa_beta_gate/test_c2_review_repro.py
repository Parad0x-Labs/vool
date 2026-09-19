"""Independent review: real state/handlers, synthetic Notes and loopback calendar."""
import sqlite3

import pytest

from tests.pa_beta_gate._caldav_service import start_caldav_fixture
from tests.pa_beta_gate.test_pc_conflict_recheck import T0, WORK_CAL, Clock, _choose, _run, home


def test_corrupt_selection_store_is_not_empty_calendar_selection(home, monkeypatch):
    from core.operator import calendar_accounts, calendar_agenda
    def broken(**kw):
        raise sqlite3.DatabaseError('database disk image is malformed')
    monkeypatch.setattr(calendar_accounts, 'list_accounts', broken)
    with pytest.raises(Exception):
        calendar_agenda.selected_sources()


def test_caldav_description_is_preserved_during_update():
    from core.kas.adapters.caldav import CalDavCalendarAdapter
    from core.kas.contract import CalEvent
    event = CalEvent(provider_id='caldav', uid='review', calendar_id=WORK_CAL,
                     summary='New title', start_utc='2026-09-22T12:00:00+00:00',
                     end_utc='2026-09-22T13:00:00+00:00', tz_name='UTC', description='Keep my agenda')
    base='BEGIN:VEVENT\r\nUID:review\r\nSUMMARY:Old title\r\nDTSTART:20260922T120000Z\r\nDTEND:20260922T130000Z\r\nDESCRIPTION:Keep my agenda\r\nEND:VEVENT'
    result=object.__new__(CalDavCalendarAdapter)._render_event(event, uid='review', base=base)
    assert 'DESCRIPTION:Keep my agenda' in result, result


def test_rename_replay_never_retargets_a_reused_title(home, monkeypatch):
    from core.operator.notes import deliver_apple_note_mutation
    from tests.pa_beta_gate.test_pc_notes_identity import FakeNotes
    original={'title':'Draft', 'folder':'Work', 'account':'iCloud', 'body':'original'}
    fake=FakeNotes({'id-original':dict(original)}).install(monkeypatch)
    args=dict(kind='rename', title='Draft', new_title='Reviewed', folder='Work', account='iCloud',
              task_id='same-approved-request', session_id='review-session')
    first=deliver_apple_note_mutation(**args)
    assert first['ok'], first
    assert fake.notes['id-original']['title']=='Reviewed'
    fake.notes['id-replacement']={**original,'body':'unrelated new note'}
    second=deliver_apple_note_mutation(**args)
    assert fake.notes['id-replacement']['title']=='Draft', second
    assert len([s for s in fake.scripts if 'set name of theNote to' in s])==1


def test_move_refuses_unreadable_other_calendar(home, monkeypatch):
    from datetime import timedelta

    from core.operator import calendar_provider
    Clock(T0,monkeypatch)
    server,state,base,_=start_caldav_fixture(calendars={WORK_CAL:'Work'})
    try:
        _choose('caldav',base,WORK_CAL,'Work',default_write=True)
        state.seed_event(WORK_CAL,'move-review',summary='Roadmap',start=T0+timedelta(days=1),minutes=30)
        proposal=_run('move the "Roadmap" event to 2026-09-23 11:00 Europe/Athens',session_id='move-review')
        assert proposal.status=='approval_required',proposal.response_text
        before=state.snapshot()
        monkeypatch.setattr(calendar_provider,'_other_selected_conflicts',
                            lambda *a,**kw:([],[{'source':'Other','status':'provider_unreachable'}]))
        result=_run('approve calendar '+proposal.details['action_id'],session_id='move-review')
        assert not result.ok,result.response_text
        assert state.snapshot()==before
    finally:
        server.shutdown()
        server.server_close()
