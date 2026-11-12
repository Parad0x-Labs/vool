"""An unverifiable erasure state never refuses a model on this computer.

The chat's erasure state governs what a cloud preview may retain. A local call
is not a disclosure: it still runs, without the in-process copy. A cloud call
in the same state is refused before anything is sent.
"""
from __future__ import annotations

import pytest

from core.disclosure import scope
from core.disclosure.sources import SourceCatalog
from core.disclosure.wire import DisclosureRefused, prepare, previews, request_context
from core.provider_invocation_gateway import DirectProviderRequest
from core.remote_fetch_policy import remote_fetch_policy_scope

BODY = {"model": "local-scripted", "messages": [{"role": "user", "content": "Suggest a bakery name."}]}


@pytest.fixture(autouse=True)
def _unverifiable(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))

    def incomplete(_chat_id, _project_id):
        raise RuntimeError("memory erasure is incomplete")

    monkeypatch.setattr(scope, "current_version", incomplete)


def _prepare(url, *, cloud):
    catalog = SourceCatalog()
    catalog.add("Suggest a bakery name.", "conversation", "*")
    request = DirectProviderRequest(trace_id="scope-turn", context={}, metadata={}, max_output_tokens=10)
    with remote_fetch_policy_scope({"chat_id": "scope-chat", "request_id": "scope-turn", "_disclosure_sources": catalog}):
        context = request_context(request, provider="local-provider", cloud=cloud)
        assert context["local_context"], "the local retry context must be present for this proof"
        return prepare(dict(BODY), url=url, context=context, request=request)


def test_a_local_call_runs_without_a_retained_copy():
    wire = _prepare("http://127.0.0.1:11434/api/chat", cloud=False)
    assert wire.cloud_destination is False and not wire.preview_id
    assert dict(wire) == BODY
    assert previews(chat_id="scope-chat") == []


def test_a_cloud_call_in_the_same_state_is_refused():
    with pytest.raises(DisclosureRefused, match="could not be verified"):
        _prepare("https://cloud.example.invalid/v1/chat/completions", cloud=True)
