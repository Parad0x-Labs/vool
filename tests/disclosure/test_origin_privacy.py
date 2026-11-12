from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from adapters.base_adapter import ModelRequest
from core.cloud_escalation_policy import set_free_cloud_enabled
from core.disclosure.wire import REVIEW_WAITS_KEY, DisclosureCancelled, DisclosureRefused, decide, previews
from core.provider_invocation_gateway import seal_provider_invocation
from core.remote_fetch_policy import remote_fetch_policy_scope
from tests.disclosure.test_direct_helpers import receiver


def send(endpoint, proxy, privacy):
    from core import provider_http

    request = ModelRequest('chat', 'SYNTHETIC_PRIVATE_SCOPE_CONTENT_7192',
                           context={'chat_id': 'origin-privacy'}, metadata={'privacy_class': privacy})
    # This test answers its previews while the call waits, as the chat page does, so it declares it.
    with remote_fetch_policy_scope({REVIEW_WAITS_KEY: 'wait'}):
        permit = seal_provider_invocation(request=request, provider_id='origin-privacy', model_id='scripted-outside',
            operation='chat', payload={'model': 'scripted-outside', 'messages': [{'role': 'user', 'content': request.prompt}]},
            destination_url=endpoint, cloud_destination=True)
        return provider_http.post(endpoint, json=permit.consume(), proxies={'http': proxy}, timeout=5)


@pytest.mark.parametrize('privacy', ['secrets', 'wallet', 'unknown', None])
def test_an_original_cloud_request_cannot_bypass_canonical_hard_privacy(tmp_path, monkeypatch, privacy):
    monkeypatch.setenv('VOOL_HOME', str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (proxy, received):
        with pytest.raises(DisclosureRefused):
            send('http://origin-privacy.invalid/chat', proxy, privacy)
        assert received == []


@pytest.mark.parametrize('action', ['send', 'cancel'])
def test_explicit_private_class_requires_review_even_without_file_metadata(tmp_path, monkeypatch, action):
    monkeypatch.setenv('VOOL_HOME', str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (proxy, received), ThreadPoolExecutor() as executor:
        future = executor.submit(send, 'http://origin-private.invalid/chat', proxy, 'private-files')
        waiting = []
        deadline = time.monotonic()+5
        while not future.done() and time.monotonic() < deadline:
            waiting = [item for item in previews(chat_id='origin-privacy') if item['state'] == 'waiting']
            if waiting:
                break
            time.sleep(.01)
        try:
            assert waiting and received == [], {'waiting': waiting, 'received': received}
            item = waiting[0]
            decide(item['id'], item['sha256'], action)
            if action == 'cancel':
                with pytest.raises(DisclosureCancelled):
                    future.result(timeout=5)
                assert received == []
            else:
                assert future.result(timeout=5).status_code == 200
                assert [entry['body'] for entry in received] == [item['body'].encode()]
        finally:
            for item in waiting:
                if item['state'] == 'waiting':
                    try:
                        decide(item['id'], item['sha256'], 'cancel')
                    except ValueError:
                        pass
