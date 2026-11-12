from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.disclosure.test_served_preview import _json
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat
from tests.model_compare.test_served_compare import compare


@pytest.mark.parametrize('adapter', ['local_subprocess', 'local_model_path'])
def test_stdio_readiness_requires_explicit_protocol_locality_and_image_support(tmp_path, adapter):
    from core.model_compare.subprocess_replay import availability
    from storage.model_provider_manifest import ModelProviderManifest

    manifest = ModelProviderManifest(provider_name='stdio-readiness', model_name='stdio-model',
        source_type='subprocess', adapter_type=adapter, license_name='test', license_reference='test',
        weight_location='external', runtime_dependency='python', capabilities=['summarize'],
        runtime_config={'command': [sys.executable, '-c', 'pass'], 'model_path': str(tmp_path)},
        metadata={'deployment_class': 'local', 'cost_class': 'free_local', 'input_modalities': ['text']})
    assert availability(manifest)
    manifest.runtime_config['comparison_request_format'] = 'openai_messages_v1'
    assert availability(manifest) == ''
    assert availability(manifest, images=True)
    manifest.capabilities.append('multimodal')
    manifest.metadata['input_modalities'] = ['text', 'image']
    assert availability(manifest, images=True) == ''
    manifest.metadata['deployment_class'] = 'cloud'
    assert availability(manifest)
    manifest.metadata['deployment_class'] = 'local'
    manifest.metadata['cost_class'] = 'paid_cloud'
    assert availability(manifest)
    manifest.metadata['cost_class'] = 'free_local'
    manifest.runtime_config['command'] = [str(tmp_path/'missing-command')]
    assert availability(manifest)


@pytest.mark.parametrize('adapter,mode', [('local_subprocess', 'complete'),
    ('local_model_path', 'complete'), ('local_subprocess', 'unknown'),
    ('local_subprocess', 'tools'), ('local_subprocess', 'timeout'), ('local_subprocess', 'cancel')])
def test_configured_stdio_provider_replays_complete_input_on_served_comparison(tmp_path, adapter, mode):
    # This is the outside provider's JSON stdio protocol, not a replacement for
    # VOOL's adapter, admission, comparison, measurement or ledger code.
    received = tmp_path/'provider-received.json'
    backend = tmp_path/'provider.py'
    backend.write_text('''import json,sys,time,os
from pathlib import Path
body=json.load(sys.stdin)
import psutil
Path(sys.argv[1]+'.process').write_text(json.dumps({'pid':os.getpid(),'created':psutil.Process().create_time()}))
Path(sys.argv[1]).write_text(json.dumps(body))
time.sleep(2 if sys.argv[2] in {'timeout','cancel'} else .25)
reply={'output_text':'The workshop opens at noon.','usage':{'prompt_tokens':53,'completion_tokens':9}}
if sys.argv[2]!='unknown': reply['finish_reason']='stop'
if sys.argv[2]=='tools': reply['tool_calls']=[{'function':{'name':'upload','arguments':'{}'}}]
print(json.dumps(reply))
''')
    with recording_daemon(tmp_path) as (daemon, _, _):
        session = rig.canonical_session('stdio-compare')
        reviewed_chat(daemon, 'Summarize this public fact: a repair workshop opens at noon.', session_id=session)
        source = [p for p in _json(daemon.base_url+'/api/disclosure/pending')['requests'] if p.get('can_compare')][-1]
        config = {'command': [sys.executable, str(backend), str(received), mode],
                  'comparison_request_format': 'openai_messages_v1',
                  'model_path': str(tmp_path), 'timeout_seconds': .5 if mode == 'timeout' else 3}
        script = '''
from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest
m=get_provider_manifest('disclosure-recording','disclosure-recording:scripted').model_copy(deep=True,
    update={'provider_name':'compare-stdio','model_name':'stdio-model','adapter_type':ADAPTER,'source_type':'subprocess'})
m.runtime_config.clear()
m.runtime_config.update(CONFIG)
m.metadata.update(deployment_class='local',cost_class='free_local')
ModelRegistry().register_manifest(m)
'''.replace('ADAPTER', repr(adapter)).replace('CONFIG', repr(config))
        registered = subprocess.run([sys.executable, '-c', script], env=daemon.env(), cwd=rig.REPO_ROOT,
                                    capture_output=True, text=True, timeout=20, check=False)
        assert registered.returncode == 0, registered.stderr
        choices = _json(daemon.base_url+'/api/disclosure/compare/models?id='+source['id'])['models']
        choice = next(m for m in choices if m['provider'] == 'compare-stdio')
        assert choice['available'], choice
        if mode == 'cancel':
            job_id = _json(daemon.base_url+'/api/disclosure/compare/start', {
                'id': source['id'], 'sha256': source['sha256'], 'models': [choice['id']]})['id']
            deadline = time.monotonic()+10
            while not received.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert received.exists(), daemon.log_tail()
            _json(daemon.base_url+'/api/disclosure/compare/cancel', {'id': job_id})
            while time.monotonic() < deadline:
                job = _json(daemon.base_url+'/api/disclosure/compare/job?id='+job_id)
                if job['state'] != 'running':
                    break
                time.sleep(.05)
            assert job['state'] == 'cancelled', job
            previews = []
        else:
            job, previews = compare(daemon, source, [choice['id']])
        assert not previews, 'A local command must not ask for a cloud grant.'
        result = job['results'][0]
        actual = json.loads(received.read_text())
        original = json.loads(source['body'])
        process_record = json.loads(Path(str(received)+'.process').read_text())
        try:
            process = psutil.Process(process_record['pid'])
            assert process.create_time() != process_record['created'] or not process.is_running(), 'The finished call left its provider running.'
        except psutil.NoSuchProcess:
            pass
        assert job['original_measured']['billed_usd'] is None, 'A configured-free label is not an actual provider bill.'
        assert actual['messages'] == original['messages']
        assert actual['model'] == 'stdio-model'
        for key, value in original.items():
            if key not in {'model', 'stream', 'stream_options'}:
                assert actual[key] == value
        assert 'context' not in actual and 'prompt' not in actual
        if mode in {'timeout', 'cancel'}:
            assert result['state'] == ('failed' if mode == 'timeout' else 'cancelled'), job
            assert result['measured']['prompt_tokens'] is None
        else:
            assert result['state'] == ('completed' if mode == 'complete' else 'refused'), job
            assert result['same_input'] is True, job
            assert result['measured']['prompt_tokens'] == 53
            assert result['measured']['output_tokens'] == 9
        assert result['measured']['first_token_ms'] is None, 'Buffered stdio cannot measure first token.'
        assert result['measured']['total_ms'] >= 0
        assert result['measured']['billed_usd'] is None, 'No provider billing report is unknown, even locally.'
        if mode == 'complete' and adapter == 'local_subprocess':
            manager, browser = served_browser.launch_chromium()
            page = browser.new_page(viewport={'width': 1100, 'height': 850})
            try:
                page.goto(daemon.base_url+'/chat', wait_until='networkidle')
                page.evaluate('(id) => openSession(id)', session)
                page.locator('.cloud-disclosure-answer:visible').last.click(timeout=15000)
                page.get_by_role('button', name='Try this with another model', exact=True).last.click()
                page.wait_for_selector('#cloudCompare[open]')
                page.locator('#cloudCompareModels input[value="'+choice['id']+'"]').check()
                page.locator('#cloudCompareRun').click()
                page.wait_for_function("() => document.getElementById('cloudCompareStatus').textContent === 'Comparison finished.'")
                assert 'stdio-model' in page.locator('#cloudCompareResults').inner_text()
                assert '53' in page.locator('#cloudCompareResults').inner_text()
                assert page.locator('#cloudCompareResults section').count() == 2
                evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
                if evidence:
                    page.screenshot(path=str(Path(evidence).parent/'shots'/('M5-stdio-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')+'.png')), full_page=True)
            finally:
                browser.close()
                manager.stop()
