from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat
from tests.model_compare.test_served_compare import compare, register, streaming_provider


@pytest.mark.parametrize('fault_table', ['model_call_timing', 'token_usage'])
def test_failed_counter_write_preserves_other_authoritative_measurements(tmp_path, fault_table):
    target = streaming_provider()
    with _provider_context(target), recording_daemon(tmp_path) as (daemon, _, _):
        session = rig.canonical_session('missing-comparison-times')
        reviewed_chat(daemon, 'Summarize this fact: a repair workshop opens at noon.', session_id=session)
        source = [p for p in _json(daemon.base_url+'/api/disclosure/pending')['requests'] if p.get('can_compare')][-1]
        register(daemon, [target])
        # Real SQLite fault on this job's timing table, not a replacement ledger,
        # exception mock, spending authority, or model adapter.
        script = '''
from storage.db import get_connection
conn=get_connection()
assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_call_timing'").fetchone()
conn.execute("CREATE TRIGGER counter_write_fault BEFORE INSERT ON FAULT_TABLE BEGIN SELECT RAISE(FAIL,'counter write unavailable'); END")
conn.commit()
conn.close()
'''.replace('FAULT_TABLE', fault_table)
        result = subprocess.run([sys.executable, '-c', script], cwd=rig.REPO_ROOT, env=daemon.env(),
                                capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        choices = _json(daemon.base_url+'/api/disclosure/compare/models?id='+source['id'])['models']
        selected = [m['id'] for m in choices if m['provider'] == 'compare-0']
        job, _ = compare(daemon, source, selected)
        answer = job['results'][0]
        assert answer['state'] == 'completed' and answer['spend_state'] == 'settled', job
        query = '''
import json
from storage.db import get_connection
conn=get_connection()
row=conn.execute('SELECT prompt_tokens,output_tokens,usd_actual FROM token_usage WHERE entry_id=?',(CALL,)).fetchone()
conn.close()
print(json.dumps(list(row) if row else None))
'''.replace('CALL', repr(answer['model_call_id']))
        result = subprocess.run([sys.executable, '-c', query], cwd=rig.REPO_ROOT, env=daemon.env(),
                                capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        actual_usage = json.loads(result.stdout.splitlines()[-1])
        assert actual_usage == ([40, 8, .001] if fault_table == 'model_call_timing' else None)
        evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
        if evidence:
            tag = os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')
            Path(evidence, 'missing-counter-'+fault_table+'-'+tag+'.json').write_text(json.dumps({'job': job, 'actual_usage': actual_usage}, indent=2))
        assert answer['measured'] is not None, 'Known ledger usage vanished with the failed timing row.'
        assert answer['measured']['prompt_tokens'] == (40 if actual_usage else None)
        assert answer['measured']['output_tokens'] == (8 if actual_usage else None)
        assert answer['measured']['billed_usd'] == .001
        if fault_table == 'model_call_timing':
            assert answer['measured']['first_token_ms'] is None and answer['measured']['total_ms'] is None
        else:
            assert answer['measured']['first_token_ms'] is not None
            assert answer['measured']['total_ms'] >= answer['measured']['first_token_ms']
        assert answer.get('accounting_error'), job
        if fault_table == 'token_usage':
            assert answer['measured']['billed_source'] == 'spend_ledger'
        manager, browser = served_browser.launch_chromium()
        page = browser.new_page(viewport={'width': 1100, 'height': 850})
        try:
            page.goto(daemon.base_url+'/chat', wait_until='networkidle')
            page.evaluate('(id) => openSession(id)', session)
            page.locator('.cloud-disclosure-answer:visible').last.click(timeout=15000)
            page.get_by_role('button', name='Try this with another model', exact=True).last.click()
            page.wait_for_selector('#cloudCompare[open]')
            page.locator('#cloudCompareModels input[value="'+selected[0]+'"]').check()
            page.locator('#cloudCompareRun').click()
            page.wait_for_function("() => document.getElementById('cloudCompareStatus').textContent === 'Comparison finished.'")
            column = page.locator('#cloudCompareResults section').last
            assert '0.001 USD' in column.inner_text()
            assert 'Some measurements could not be saved or read.' in column.inner_text()
            if fault_table == 'model_call_timing':
                assert 'First text: Unknown' in column.inner_text() and 'Total: Unknown' in column.inner_text()
            else:
                assert 'Tokens in / out: Unknown / Unknown' in column.inner_text()
            assert len(target.raw) == 1, 'Viewing the completed result must not repeat a billed model call.'
            if evidence:
                page.screenshot(path=str(Path(evidence).parent/'shots'/('M5-missing-'+fault_table+'-'+tag+'.png')), full_page=True)
        finally:
            browser.close()
            manager.stop()
