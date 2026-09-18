"""Unreadable mailbox responses must not become successful empty searches."""
import urllib.request

import pytest

from core import email_tools
from tests.test_email_v2_independent_review import Reply, configure, isolated


@pytest.mark.parametrize("provider", ["gmail", "graph"])
@pytest.mark.parametrize("failure", ["malformed-json", "body-timeout"])
def test_unreadable_search_result_is_not_zero_matching_messages(monkeypatch, provider, failure):
    configure(monkeypatch, provider)

    def wire(req, **kwargs):
        assert req.method == "GET"
        if failure == "malformed-json":
            return Reply(b'{broken-json')
        return Reply(failure=TimeoutError("Synthetic mailbox-search body timeout"))

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    result = email_tools.search_email(account="review-account", sender="supplier@example.test", limit=10)
    assert not result.ok, (result.status, result.message, result.messages)
