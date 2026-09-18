"""Independent verification of identity, refresh and reply-preservation boundaries."""
import base64
import email
import io
import json
import time
import urllib.error
import urllib.request

import pytest

from core import email_drafts
from core.email_providers.base import _OAuthClient
from tests.test_email_v2_independent_review import SESSION, Reply, configure, draft, isolated

REAL_ACCESS_TOKEN = _OAuthClient.access_token


@pytest.mark.parametrize("provider", ["gmail", "graph"])
@pytest.mark.parametrize("profile_failure", ["timeout", "forbidden", "missing-identity"])
def test_inconclusive_profile_cannot_authorize_a_swapped_grant(monkeypatch, provider, profile_failure):
    creds = configure(monkeypatch, provider)
    creds["_oauth_handle"]["token_url"] = "https://oauth.example.test/token"
    monkeypatch.setattr(_OAuthClient, "access_token", REAL_ACCESS_TOKEN)
    sent = []

    def wire(req, **kwargs):
        if req.full_url == "https://oauth.example.test/token":
            return Reply(b'{"access_token":"other-principal-access","expires_in":3600}')
        if req.method == "GET":
            if profile_failure == "timeout":
                raise TimeoutError("Synthetic profile read timeout")
            if profile_failure == "forbidden":
                raise urllib.error.HTTPError(req.full_url, 403, "Profile unavailable", {}, io.BytesIO(b'{}'))
            return Reply(b'{}')
        sent.append(req.get_header("Authorization"))
        response = Reply(b'{"id":"accepted"}')
        response.status = 200 if provider == "gmail" else 202
        return response

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    did = draft(subject=f"{provider} identity {profile_failure}")
    creds["_oauth_handle"]["refresh_token"] = "different-principal-grant"
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not sent, (result.status, sent)
    assert not result.ok


@pytest.mark.parametrize("operation", ["read", "mutation"])
def test_401_retries_once_with_a_fresh_token(monkeypatch, operation):
    monkeypatch.setattr(_OAuthClient, "access_token", REAL_ACCESS_TOKEN)
    client = _OAuthClient(provider="gmail", token_url="https://oauth.example.test/token",
                          client_id="c", client_secret="s", refresh_token="refresh", scopes="scope")
    client._access_token = "revoked-access"
    client._expires_at = time.time() + 3600
    headers = []
    refreshes = []

    def wire(req, **kwargs):
        if req.full_url == "https://oauth.example.test/token":
            refreshes.append(req.data)
            return Reply(b'{"access_token":"fresh-access","expires_in":3600}')
        headers.append(req.get_header("Authorization"))
        if headers[-1] == "Bearer revoked-access":
            raise urllib.error.HTTPError(req.full_url, 401, "expired", {}, io.BytesIO(b'{}'))
        return Reply(b'{"id":"result"}')

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    try:
        if operation == "read":
            result = client.api_read("GET", "https://provider.example.test/messages")
            assert result["id"] == "result"
        else:
            result = client.api_mutation("POST", "https://provider.example.test/send", payload={"body": "approved"})
            assert result.accepted
    except Exception as exc:
        pytest.fail(f"Refresh recovery failed: {type(exc).__name__}; API tokens={headers}; refreshes={len(refreshes)}")
    assert len(refreshes) == 1 and headers == ["Bearer revoked-access", "Bearer fresh-access"]


@pytest.mark.parametrize("parent", ["<parent-original@example.test>", "<parent-novel@example.test>"])
def test_graph_reply_transmits_parent_binding_or_refuses(monkeypatch, parent):
    configure(monkeypatch, "graph")
    sent = []

    def wire(req, **kwargs):
        if req.method == "GET":
            if "/me?" in req.full_url:
                return Reply(b'{"mail":"review@example.test"}')
            return Reply(b'{"value":[{"id":"resolved-parent-id","subject":"Delivery slot"}]}')
        sent.append((req.full_url, req.get_header("Content-type"), req.data))
        response = Reply(b"")
        response.status = 202
        return response

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    result = email_drafts.send_draft(draft(reply_to=parent), session_id=SESSION)
    if not sent:
        assert not result.ok
        return
    url, content_type, raw = sent[0]
    # Accept native reply binding or the supported MIME send representation.
    # An arbitrary JSON field containing the parent is not threading evidence.
    if url.endswith("/messages/resolved-parent-id/reply"):
        return
    assert url.endswith("/sendMail") and content_type.split(";")[0] == "text/plain", (result.status, url, content_type, raw)
    message = email.message_from_bytes(base64.b64decode(raw, validate=True))
    assert message["In-Reply-To"] == parent
    assert parent in str(message["References"])


@pytest.mark.parametrize("damaged", [
    b'{"drafts":{"old":{"status":"delivery_unknown"',
    b'{"drafts":[],"status":"sending"}',
], ids=["parse-invalid", "schema-invalid"])
def test_failed_quarantine_preserves_original_bytes(monkeypatch, damaged):
    path = email_drafts._drafts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(damaged)
    original_rename = type(path).rename

    def denied_quarantine(self, target):
        if self == path:
            raise PermissionError("Synthetic quarantine rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(type(path), "rename", denied_quarantine)
    try:
        email_drafts._load()
    except OSError:
        pass  # A fail-closed recovery refusal is valid.
    assert path.read_bytes() == damaged, "Damaged reservation evidence was overwritten after quarantine failed"
