"""Reviewed project context and actual file tools through the served Settings page."""
from __future__ import annotations

import json

from core.chat_import import store
from core.context_namespace import authoritative_chat_workspace, load_chat_namespace
from core.runtime_execution_tools import execute_runtime_tool
from core.user_rules import active_rules
from tests.served_browser import launch_chromium
from tests.test_chat_import_browser import _export_zip, _panel
from tests.test_chat_import_browser import served as base_served
from tests.test_chat_import_migration_browser import served as served


def test_review_changes_require_review_again_and_context_survives_reload(served):
    base, tmp_path = served
    root = tmp_path / 'orchard-work'
    root.mkdir()
    (root / 'AGENTS.md').write_text('Use the orchard_checkpoint convention for all project work.\n')
    (root / 'recipe.txt').write_text('Roast the squash before adding the stock.\n')
    (root / '.cursor/rules').mkdir(parents=True)
    (root / '.cursor/rules/review.mdc').write_text('---\nglobs: "*.py"\nalwaysApply: false\n---\nUse orchard_conditional only for Python.\n')
    (root / '.cursor/mcp.json').write_text(json.dumps({'mcpServers':{'orchard':{'command':'python3','args':['-V'],'env':{'API_KEY':'sk-'+'T'*48}}}}))
    (root / 'cron').mkdir()
    (root / 'cron/jobs.json').write_text(json.dumps({'jobs':[{'prompt':'Review the orchard schedule.','schedule':{'kind':'cron','expr':'0 8 * * *'}}]}))
    export = tmp_path / 'history.zip'
    export.write_bytes(_export_zip())
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width':1120,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/settings#backup/chat_import')
        panel=_panel(page)
        panel.get_by_role('radio',name='A data export file (.zip)').check()
        panel.get_by_label('Data export file',exact=True).set_input_files(str(export))
        panel.get_by_role('button',name='Show my chats',exact=True).click()
        panel.locator('.vci-chat input').first.check()
        details=panel.locator('.vci-context').first
        details.locator('summary').click()
        assert not details.locator('.vci-working').is_checked()
        details.locator('.vci-folder').fill(str(root))
        details.locator('textarea.vci-instructions').fill('Use `recipe.txt` and flag the missing `inventory.txt`.')
        details.locator('.vci-review').click()
        details.locator('input.vci-instructions').wait_for()
        assert 'Missing' in details.locator('.vci-context-result').inner_text()
        assert 'inventory.txt' in details.locator('.vci-context-result').inner_text()
        details.locator('input.vci-instructions').check()
        details.locator('textarea.vci-instructions').fill('Use `recipe.txt`, flag `inventory.txt`, and keep source_custom_rule.')
        assert panel.locator('.vci-import').is_disabled()
        details.locator('.vci-review').click()
        details.locator('input.vci-instructions').wait_for()
        assert not details.locator('input.vci-instructions').is_checked()
        details.locator('input.vci-instructions').check()
        assert 'Conditional instructions are saved as drafts' in details.inner_text()
        for category in ('skills','tools','schedules'):details.locator('input.vci-'+category).check()
        panel.locator('.vci-continue').check()
        page.screenshot(path=str(tmp_path/'working-context-review.png'),full_page=True)
        panel.locator('.vci-import').click()
        panel.locator('.vci-status.vci-good').wait_for(timeout=30000)
        result=page.evaluate("async()=>await(await fetch('/api/chat/import/status')).json()")
        sid=result['first_session_id']
        workspace,reason=authoritative_chat_workspace(sid)
        assert workspace==str(root) and reason=='project'
        namespace=load_chat_namespace(sid)
        rules=active_rules(chat_id=sid,project_id=namespace.project_id)
        assert any('orchard_checkpoint' in rule.text for rule in rules)
        assert any('source_custom_rule' in rule.text for rule in rules)
        tool=execute_runtime_tool('workspace.read_file',{'path':'recipe.txt'},source_context={
            'session_id':sid,'workspace_root':workspace,'workspace':workspace,'project_id':namespace.project_id})
        assert tool and tool.ok and 'Roast the squash' in tool.response_text,tool
        page.reload()
        panel=_panel(page)
        overview=panel.locator('.vci-saved-context').first
        overview.wait_for();overview.locator(':scope > summary').click()
        assert 'inventory.txt' in overview.inner_text() and 'Missing' in overview.inner_text()
        assert str(root) in overview.inner_text()
        assert overview.locator('.vci-saved-resources').count()==4
        for resource in overview.locator('.vci-saved-resources').all():resource.locator('summary').click()
        assert 'orchard_conditional' in overview.inner_text() and '*.py' in overview.inner_text()
        assert 'python3' in overview.inner_text() and 'Review the orchard schedule.' in overview.inner_text()
        assert 'sk-'+'T'*48 not in page.content()
        page.screenshot(path=str(tmp_path/'working-context-saved.png'),full_page=True)
        panel.locator('.vci-undo').first.click()
        page.wait_for_function("document.querySelector('.vci-status').textContent==='Import removed.'")
        assert store.continuation_history(sid)==[]
        assert json.loads(page.evaluate("async()=>JSON.stringify(await(await fetch('/api/chat/import/batches')).json())"))['batches'][0]['contexts']==[]
        assert errors==[]
    finally:
        browser.close();manager.stop()


def test_interrupted_context_blocks_serving_and_reload_keeps_complete_undo(served):
    from storage.db import get_connection

    base,tmp_path=served
    root=tmp_path/'interrupted-work';root.mkdir()
    (root/'AGENTS.md').write_text('Keep the orchard_folio checkpoint visible.\n')
    export=tmp_path/'fault-history.zip';export.write_bytes(_export_zip())
    active_rules()  # Canonical owner creates its schema before the real storage fault.
    connection=get_connection()
    connection.execute("CREATE TRIGGER import_rules_fault BEFORE INSERT ON user_rules WHEN NEW.created_turn LIKE 'chat-import:%' BEGIN SELECT RAISE(ABORT,'synthetic storage failure'); END")
    connection.commit()
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(viewport={'width':1120,'height':900})
        page.goto(base+'/settings#backup/chat_import');panel=_panel(page)
        panel.get_by_role('radio',name='A data export file (.zip)').check()
        panel.get_by_label('Data export file',exact=True).set_input_files(str(export))
        panel.get_by_role('button',name='Show my chats',exact=True).click()
        panel.locator('.vci-chat input').first.check()
        details=panel.locator('.vci-context').first;details.locator('summary').click()
        details.locator('.vci-folder').fill(str(root));details.locator('.vci-review').click()
        details.locator('input.vci-instructions').wait_for();details.locator('input.vci-instructions').check()
        panel.locator('.vci-continue').check();panel.locator('.vci-import').click()
        page.wait_for_function("document.querySelector('.vci-status').textContent.includes('Undo it, then import again')")
        result=page.evaluate("async()=>await(await fetch('/api/chat/import/status')).json()")
        assert result['state']=='failed' and result['working_context']
        sid,=store.batch_sessions(result['run_id'])
        assert store.context_pending(sid)
        answer=page.evaluate("""async(sid)=>{let r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,messages:[{role:'user',content:'Continue the checkpoint.'}]})});return {status:r.status,body:await r.json()};}""",sid)
        assert answer['status']==409 and answer['body']['error']=='import_context_incomplete'
        page.reload();panel=_panel(page);panel.locator('.vci-undo').first.wait_for()
        assert 'Undo it, then import again' in panel.locator('.vci-batches').inner_text()
        panel.locator('.vci-undo').first.click()
        page.wait_for_function("document.querySelector('.vci-status').textContent==='Import removed.'")
        assert store.imported_history_messages(sid)==[]
        assert load_chat_namespace(sid).lifecycle_state=='deleted'
        assert (root/'AGENTS.md').read_text()=='Keep the orchard_folio checkpoint visible.\n'
    finally:
        connection.execute('DROP TRIGGER import_rules_fault');connection.commit();connection.close()
        browser.close();manager.stop()
