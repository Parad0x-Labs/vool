from __future__ import annotations

import threading

import pytest

from adapters.base_adapter import ModelRequest
from core.cloud_escalation_policy import set_free_cloud_enabled
from core.disclosure.wire import DisclosureCancelled
from core.provider_invocation_gateway import seal_provider_invocation
from core.remote_fetch_policy import remote_fetch_policy_scope
from tests.disclosure.test_direct_helpers import receiver


def test_stop_after_sealing_refuses_the_previously_approved_model_bytes(tmp_path, monkeypatch):
    from core import provider_http

    monkeypatch.setenv('VOOL_HOME', str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    event = threading.Event()
    with receiver() as (proxy, received):
        endpoint = 'http://cancel-after-review.invalid/chat'
        request = ModelRequest('chat', 'Synthetic public specification.',
                               context={'chat_id': 'cancel-after-review'}, metadata={'privacy_class': 'public'})
        with remote_fetch_policy_scope({'chat_id': 'cancel-after-review', 'cancel_event': event}):
            permit = seal_provider_invocation(request=request, provider_id='cancel-after-review',
                model_id='scripted-outside', operation='chat', payload={
                    'model': 'scripted-outside', 'messages': [{'role': 'user', 'content': request.prompt}]},
                destination_url=endpoint, cloud_destination=True)
            event.set()
            with pytest.raises(DisclosureCancelled):
                provider_http.post(endpoint, json=permit.consume(), proxies={'http': proxy}, timeout=5)
        assert received == []
