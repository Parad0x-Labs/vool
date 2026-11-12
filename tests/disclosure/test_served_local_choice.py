from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon


def test_local_choice_retries_through_canonical_chat_without_cloud_dispatch(tmp_path):
    local = local_provider()
    with _provider_context(local), recording_daemon(tmp_path) as (daemon, cloud, raw):
        register_local(daemon, local)
        session = rig.canonical_session('disclosure-local-choice')
        question = 'Summarize the attached workshop document.'
        record = daemon.upload(data=b'The workshop opens at noon.', name='workshop.txt',
                               declared_type='text/plain', session_id=session)
        ident = record.get('attachment_id') or record.get('id') or record['attachment']['id']
        with ThreadPoolExecutor() as executor:
            future = executor.submit(daemon.chat, question, session_id=session, attachments=[ident])
            deadline = time.monotonic() + 30
            pending = None
            while time.monotonic() < deadline and not future.done():
                pending = next((p for p in _json(daemon.base_url + '/api/disclosure/pending')['requests']
                                if p['state'] == 'waiting'), None)
                if pending:
                    break
                time.sleep(.05)
            assert pending, {'result': future.result() if future.done() else None, 'log': daemon.log_tail()}
            choices = _json(daemon.base_url + '/api/disclosure/local-models?id=' + pending['id'])['models']
            assert choices == [{'id': 'disclosure-local:scripted', 'provider': 'disclosure-local'}], choices
            retry = _json(daemon.base_url + '/api/disclosure/local', {
                'id': pending['id'], 'sha256': pending['sha256'], 'model': choices[0]['id']})
            assert retry['attachment_meta'][0]['size_bytes'] == len(b'The workshop opens at noon.')
            assert retry['attachment_meta'][0]['name'].startswith('prepared-')
            cancelled = future.result(timeout=20)
        assert raw == [], {'cancelled': cancelled, 'cloud': raw}
        response = daemon.chat(retry['question'], session_id=session, model=retry['model'],
                               workspace=retry['workspace'], attachments=retry['attachments'],
                               disclosure_local_retry=retry['token'])
        assert 'noon' in response['message']['content'].lower(), {'response': response, 'log': daemon.log_tail()}
        assert local.calls and any('noon' in json.dumps(c) for c in local.calls), local.calls
        assert raw == []
        with pytest.raises(HTTPError) as refused:
            daemon.chat(retry['question'], session_id=session, model=retry['model'],
                        workspace=retry['workspace'], attachments=retry['attachments'],
                        disclosure_local_retry=retry['token'])
        assert refused.value.code == 409


def local_provider():
    local = rig.CapturingProvider(reply_fn=lambda body: 'The workshop opens at noon, answered locally.')
    handler = local._server.RequestHandlerClass
    original_get = handler.do_GET
    def inventory(self):
        if self.path.startswith('/api/tags'):
            return self._send({'models': [{'name': 'disclosure-local:scripted'}]})
        return original_get(self)
    handler.do_GET = inventory
    return local


def register_local(daemon, local):
    script = '''
from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest
m=get_provider_manifest('disclosure-recording','disclosure-recording:scripted')
m=m.model_copy(deep=True,update={'provider_name':'disclosure-local','model_name':'disclosure-local:scripted'})
m.runtime_config['base_url']=BASE
m.metadata.update(deployment_class='local',cost_class='free_local')
ModelRegistry().register_manifest(m)
'''.replace('BASE', repr(local.base_url))
    result = subprocess.run([sys.executable, '-c', script], env=daemon.env(), cwd=rig.REPO_ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert daemon.certify(provider_name='disclosure-local', model_name='disclosure-local:scripted', timeout=60)['state'] == 'verified'
    local.reset()
