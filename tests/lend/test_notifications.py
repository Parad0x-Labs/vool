"""Closing a lending approval never fabricates an owner action or closes another source."""


def test_resolved_source_item_preserves_read_dismissal_and_other_items(tmp_path,monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    monkeypatch.setattr(runtime_paths,'_VOOL_HOME_OVERRIDE',None)
    from storage.migrations import run_migrations

    run_migrations()
    from core.operator import notification_center as center

    pending=center.record_item(dedupe_key='lend:one',source_kind='background_run',
                               title='Lending approval',payload={'section':'needs'})
    other=center.record_item(dedupe_key='reminder:one',source_kind='reminder',title='A different item')
    assert center.supersede_by_key('lend:one',source_kind='model_offer')==0
    assert center.load_item(pending['notification_id'])['superseded_at'] is None
    assert center.supersede_by_key('lend:one',source_kind='background_run')==1
    closed=center.load_item(pending['notification_id'])
    assert closed['superseded_at'] and closed['read_at'] is None and closed['dismissed_at'] is None
    assert center.supersede_by_key('lend:one',source_kind='background_run')==0
    untouched=center.load_item(other['notification_id'])
    assert untouched['superseded_at'] is None and untouched['read_at'] is None and untouched['dismissed_at'] is None
