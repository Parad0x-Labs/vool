"""Opt-in continuation and durable undo through the served Settings screen."""
from __future__ import annotations

import pytest

from core.chat_import import store
from tests.served_browser import launch_chromium
from tests.test_chat_import_browser import _export_zip, _panel
from tests.test_chat_import_browser import served as base_served


@pytest.fixture
def served(base_served):
    connection = store.connect()
    has_ledger = hasattr(store, "BATCH_SESSIONS_TABLE")
    try:
        if has_ledger:
            connection.execute(f"DELETE FROM {store.BATCH_SESSIONS_TABLE}")
            connection.execute(f"DELETE FROM {store.BATCHES_TABLE}")
        connection.commit()
        yield base_served
    finally:
        if has_ledger:
            connection.execute(f"DELETE FROM {store.BATCH_SESSIONS_TABLE}")
            connection.execute(f"DELETE FROM {store.BATCHES_TABLE}")
        connection.commit()
        connection.close()


def test_review_opt_in_reload_and_undo_through_settings(served):
    base, tmp_path = served
    export = tmp_path / 'history.zip'
    export.write_bytes(_export_zip())
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1120, 'height': 900})
        page.goto(f'{base}/settings#backup/chat_import')
        panel = _panel(page)
        assert 'project context, instructions and file references' in panel.locator('.vci-limits').inner_text()
        panel.get_by_role('radio', name='A data export file (.zip)').check()
        panel.get_by_label('Data export file', exact=True).set_input_files(str(export))
        panel.get_by_role('button', name='Show my chats', exact=True).click()
        panel.locator('.vci-chat').first.wait_for()
        assert not panel.locator('.vci-continue').is_checked()
        panel.locator('.vci-chat input[type=checkbox]').first.check()
        panel.locator('.vci-continue').check()
        panel.locator('.vci-import').click()
        panel.locator('.vci-status.vci-good').wait_for(timeout=30000)
        assert 'history will be included' in panel.locator('.vci-where').inner_text()
        batches = store.import_batches()
        batch = next(b for b in batches if b['chats'])
        sid, = store.batch_sessions(batch['batch_id'])
        assert store.continuation_history(sid)
        # Later empty imports must not push an earlier undoable batch out of the screen.
        for i in range(55):
            batch_id = 'empty-' + str(i)
            store.create_batch(batch_id)
            store.set_batch_state(batch_id, 'done')
        page.reload()
        panel = _panel(page)
        panel.locator('.vci-undo').first.wait_for()
        page.screenshot(path=str(tmp_path / 'import-ready.png'), full_page=True)
        panel.locator('.vci-undo').first.click()
        page.wait_for_function("document.querySelector('.vci-status').textContent === 'Import removed.'")
        assert store.imported_history_messages(sid) == []
        assert store.continuation_history(sid) == []
        page.reload()
        panel = _panel(page)
        assert panel.locator('.vci-undo').count() == 0
    finally:
        browser.close()
        manager.stop()
