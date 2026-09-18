"""Independent provider-boundary probes. No sockets, real credentials or messages."""
import base64
import email.utils
import http.client
import json
import urllib.error
import urllib.request

import pytest

from core import email_drafts, email_tools
from core.email_providers.base import _OAuthClient
from tests.test_email_v2_independent_review import (
    SESSION, Reply, configure, draft, isolated, with_profile,
)

# Capture before fixtures replace token acquisition; the identity probe runs real refresh logic.
REAL_ACCESS_TOKEN = _OAuthClient.access_token


@pytest.mark.parametrize("provider", ["gmail", "graph"])
@pytest.mark.parametrize("failure", ["headers-timeout", "wrapped-reset", "incomplete-body", "invalid-utf8", "json-array"])
def test_uncertain_or_accepted_send_never_reopens_for_retry(monkeypatch, provider, failure):
    configure(monkeypatch, provider)
    calls = []

    def wire(req, **kwargs):
        assert req.method == "POST"
        calls.append(req.data)
        # A POST may already have reached the provider before header acquisition fails.
        if failure == "headers-timeout":
            raise TimeoutError("Synthetic timeout waiting for response headers after POST")
        if failure == "wrapped-reset":
            raise urllib.error.URLError(ConnectionResetError("Synthetic reset after POST"))
        response = Reply()
        response.status = 200 if provider == "gmail" else 202
        if failure == "incomplete-body":
            response.failure = http.client.IncompleteRead(b'{"id":', 12)
        elif failure == "invalid-utf8":
            response.data = b'\xff\xfe'
        else:
            response.data = b'[]'
        return response

    monkeypatch.setattr(urllib.request, "urlopen", with_profile(wire))
    did = draft(subject=f"{provider} {failure}")
    first = email_drafts.send_draft(did, session_id=SESSION)
    again = email_drafts.send_draft(did, session_id=SESSION)
    assert len(calls) == 1, (first.status, again.status, len(calls))


@pytest.mark.parametrize("subject", ["Pasiūlymas € 42", "会議の確認 — 火曜日"])
def test_graph_preserves_approved_unicode_subject(monkeypatch, subject):
    configure(monkeypatch, "graph")
    sent = []

    def wire(req, **kwargs):
        sent.append(json.loads(req.data))
        response = Reply(b"")
        response.status = 202
        return response

    monkeypatch.setattr(urllib.request, "urlopen", with_profile(wire))
    did = draft(subject=subject)
    approved = email_drafts.get_draft(did, session_id=SESSION).draft["subject"]
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok, result.message
    assert sent[0]["message"]["subject"] == approved


@pytest.mark.parametrize("recipients", [["approved@example.test"], ["reviewed-one@example.test", "reviewed-two@example.test"]])
def test_graph_native_reply_preserves_approved_recipients(monkeypatch, recipients):
    configure(monkeypatch, "graph")
    sent = []

    def wire(req, **kwargs):
        if req.method == "GET":
            return Reply(json.dumps({"value": [{"id": "parent-id",
                "from": {"emailAddress": {"address": "original-sender@example.test"}},
                "replyTo": [{"emailAddress": {"address": "parent-reply-target@example.test"}}]}]}).encode())
        sent.append((str(req.get_header("Content-type") or ""), req.data))
        response = Reply(b"")
        response.status = 202
        return response

    monkeypatch.setattr(urllib.request, "urlopen", with_profile(wire))
    saved = email_drafts.save_draft(to=recipients, subject="Reviewed revised reply", body="Approved reply text",
        account="review-account", session_id=SESSION, in_reply_to="<parent@example.test>", kind="reply")
    assert saved.ok
    did = saved.draft["draft_id"]
    assert email_drafts.approve_draft(did, session_id=SESSION).ok
    result = email_drafts.send_draft(did, session_id=SESSION)
    if not sent:
        assert not result.ok  # Refusing unsupported semantics is safe; altering them is not.
        return
    content_type, data = sent[0]
    if content_type.split(";")[0] == "text/plain":
        # Revision 5: a reply travels as the exact base64 MIME message (the documented sendMail form).
        message = email.message_from_bytes(base64.b64decode(data, validate=True))
        actual = [address for _name, address in email.utils.getaddresses([str(message["To"])])]
    else:
        actual = [row["emailAddress"]["address"] for row in json.loads(data).get("message", {}).get("toRecipients", [])]
    assert actual == recipients, (result.status, sent[0])


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_oauth_grant_swap_with_stale_metadata_cannot_reuse_approval(monkeypatch, provider):
    creds = configure(monkeypatch, provider)
    creds["_oauth_handle"]["token_url"] = "https://oauth.example.test/token"
    monkeypatch.setattr(_OAuthClient, "access_token", REAL_ACCESS_TOKEN)
    sent = []

    def wire(req, **kwargs):
        if req.full_url == "https://oauth.example.test/token":
            from urllib.parse import parse_qs
            grant = parse_qs(req.data.decode())["refresh_token"][0]
            token = "other-principal-access" if grant == "different-principal-grant" else "original-principal-access"
            return Reply(json.dumps({"access_token": token, "expires_in": 3600}).encode())
        if req.method == "GET":
            other = req.get_header("Authorization") == "Bearer other-principal-access"
            address = "other@example.test" if other else "review@example.test"
            return Reply(json.dumps({"emailAddress": address, "mail": address,
                                    "userPrincipalName": address, "id": address}).encode())
        sent.append({"authorization": req.get_header("Authorization"), "body": req.data})
        response = Reply(b'{"id":"accepted"}')
        response.status = 200 if provider == "gmail" else 202
        return response

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    did = draft(subject=f"principal binding {provider}")
    # The real refresh now resolves to another synthetic principal while metadata stays old.
    creds["_oauth_handle"]["refresh_token"] = "different-principal-grant"
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not sent, (result.status, [x["authorization"] for x in sent])


@pytest.mark.parametrize("shape", [{"drafts": []}, {"version": 99}])
def test_valid_json_with_invalid_store_shape_is_not_silently_overwritten(shape):
    path = email_drafts._drafts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(shape).encode()
    path.write_bytes(original)
    result = email_drafts.save_draft(to="new@example.test", subject="After damaged state",
                                    body="New draft", session_id=SESSION)
    quarantines = list(path.parent.glob("drafts.corrupt-*.json"))
    preserved = path.read_bytes() == original or any(p.read_bytes() == original for p in quarantines)
    assert preserved, (result.status, "Original malformed-store bytes were overwritten without quarantine")
