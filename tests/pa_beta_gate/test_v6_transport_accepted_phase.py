"""pa_beta_gate -- revision-6: a mutation the provider accepted stays accepted when its reply is lost.

The independent review of revision 5 (originals in ``test_calendar_v5_independent_review.py``) showed the
real KAS transport reading the reply body before the status: a 201 whose body timed out or arrived
incomplete escaped as a raw error, so the create rested as never accepted. Its request-phase mapping also
turned any post-send failure whose text matched none of its timeout/reset/pipe/incomplete markers into a
definitive TransportDeniedError -- "refused before any socket" -- which releases the approval as never
sent. The cases here are new failure shapes at both points, through the real transport, the real Google,
Graph and CalDAV adapters and the production approval executor, plus the controls that must keep working:
definitive pre-wire failures stay denied, a real rejection stays the provider's answer, a complete reply
whose connection fails to close keeps its answer, reads keep their mapping, and a replay after known
acceptance only verifies.

LABELLED: ``remote_fetch_policy.open_remote_url`` is replaced by a scripted reply double (no socket), and
provider state is a synthetic store the scripted server applies writes to.
"""
from __future__ import annotations

import http.client
import io
import json
import re
import socket
import ssl
import urllib.error
from dataclasses import replace
from urllib.parse import unquote, urlparse

import pytest

from core.kas.adapters.caldav import CalDavCalendarAdapter
from core.kas.adapters.google_calendar import GoogleCalendarAdapter, google_event_id
from core.kas.adapters.graph_calendar import GraphCalendarAdapter
from core.kas.contract import (
    AdapterConfig,
    CalendarWriteAcceptedError,
    CalEvent,
    KasRequest,
    TransportAcceptedError,
    TransportDeniedError,
    TransportUnknownError,
)
from core.kas.transport import build_transport
from tests.pa_beta_gate import test_v5_calendar_update_cancel_lifecycle as lifecycle
from tests.pa_beta_gate.test_v5_calendar_effect_phase import _stage, _stored
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import _execute
from tests.pa_beta_gate.test_v6_typed_calendar_reads import _multistatus, _row, _vcal

_HOST = "calendar.example.test"
_BASE = f"https://{_HOST}/api"


class Reply:
    """A reply whose status line and headers arrived; what happens to its body is scripted."""

    def __init__(self, status, body=b"", *, fails_with=None, close_fails_with=None):
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body, self._fails_with, self._close_fails_with = body, fails_with, close_fails_with

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        if self._close_fails_with is not None:
            raise self._close_fails_with
        return False

    def read(self, *args):
        if self._fails_with is not None:
            raise self._fails_with
        return self._body


def _transport(monkeypatch, opened):
    from core import remote_fetch_policy

    monkeypatch.setattr(remote_fetch_policy, "open_remote_url", lambda url, **kwargs: opened(url, kwargs))
    return build_transport(provider_id="calendar", allowed_hosts=(_HOST,))


def _answer(outcome):
    def opened(url, kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return opened


def _request(method, *, mutating=True):
    body = b"{}" if method in {"POST", "PUT", "PATCH"} else None
    return KasRequest(method=method, url=f"{_BASE}/me/events/evt-1", purpose="probe", body=body, mutating=mutating)


# ---------------------------------------------------------------------------------------------
# The transport: the first known status decides acceptance
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "reply"), [
    pytest.param("PATCH", Reply(200, fails_with=ConnectionResetError(54, "Connection reset by peer")), id="update-200-reset-mid-body"),
    pytest.param("DELETE", Reply(204, fails_with=http.client.IncompleteRead(b"", 17)), id="delete-204-incomplete"),
    pytest.param("POST", Reply(202, fails_with=TimeoutError("body stalled")), id="create-202-body-timeout"),
    pytest.param("PUT", Reply(201, fails_with=ssl.SSLError("record layer failure")), id="put-201-tls-failure-in-body"),
])
def test_success_status_is_acceptance_even_when_the_body_is_lost(monkeypatch, method, reply):
    with pytest.raises(TransportAcceptedError) as caught:
        _transport(monkeypatch, _answer(reply))(_request(method))
    assert isinstance(caught.value, TransportUnknownError), "a caller that only knows UNKNOWN stays safe"
    assert caught.value.status_code == reply.status


def test_complete_reply_keeps_its_answer_when_closing_the_connection_fails(monkeypatch):
    reply = Reply(201, b'{"id": "evt-1"}', close_fails_with=OSError(9, "Bad file descriptor"))
    response = _transport(monkeypatch, _answer(reply))(_request("POST"))
    assert (response.status, response.json()) == (201, {"id": "evt-1"})


def test_lost_body_without_a_readable_success_status_is_unknown_not_accepted(monkeypatch):
    with pytest.raises(TransportUnknownError) as caught:
        _transport(monkeypatch, _answer(Reply(None, fails_with=http.client.IncompleteRead(b"{", 40))))(_request("POST"))
    assert not isinstance(caught.value, TransportAcceptedError)


def test_read_whose_body_is_lost_is_a_transport_failure_never_an_acceptance(monkeypatch):
    with pytest.raises(TransportDeniedError) as caught:
        _transport(monkeypatch, _answer(Reply(200, fails_with=TimeoutError("stalled"))))(_request("GET", mutating=False))
    assert caught.value.reason == "transport_failed"


@pytest.mark.parametrize("failure", [
    pytest.param(http.client.RemoteDisconnected("Remote end closed connection without response"), id="remote-closed-without-reply"),
    pytest.param(ConnectionAbortedError(53, "Software caused connection abort"), id="connection-aborted"),
    pytest.param(http.client.BadStatusLine("garbled"), id="garbled-status-line"),
    pytest.param(urllib.error.URLError(OSError(65, "No route to host")), id="no-route-to-host"),
    pytest.param(urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer")), id="reset-while-sending"),
    pytest.param(TimeoutError("timed out"), id="timeout"),
])
def test_mutation_that_may_have_left_the_machine_is_unknown_never_denied(monkeypatch, failure):
    with pytest.raises(TransportUnknownError) as caught:
        _transport(monkeypatch, _answer(failure))(_request("POST"))
    assert not isinstance(caught.value, TransportAcceptedError)


@pytest.mark.parametrize(("failure", "reason"), [
    pytest.param(urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known")), "transport_failed", id="name-not-resolved"),
    pytest.param(urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")), "transport_failed", id="connection-refused"),
    pytest.param(urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")), "transport_failed", id="certificate-rejected"),
    pytest.param("policy", "egress_denied", id="egress-policy-refusal"),
])
def test_mutation_that_provably_never_left_stays_denied(monkeypatch, failure, reason):
    if failure == "policy":
        from core.remote_fetch_policy import RemoteFetchRefusedError

        failure = RemoteFetchRefusedError("remote fetch is not permitted for this turn")
    with pytest.raises(TransportDeniedError) as caught:
        _transport(monkeypatch, _answer(failure))(_request("POST"))
    assert caught.value.reason == reason


def test_real_rejection_of_a_mutation_is_the_providers_answer(monkeypatch):
    rejection = urllib.error.HTTPError(f"{_BASE}/me/events", 409, "Conflict", {"Content-Type": "application/json"}, io.BytesIO(b'{"error": "conflict"}'))
    response = _transport(monkeypatch, _answer(rejection))(_request("POST"))
    assert (response.status, response.json()) == (409, {"error": "conflict"})


def test_read_that_fails_while_sending_keeps_its_transport_failure(monkeypatch):
    with pytest.raises(TransportDeniedError) as caught:
        _transport(monkeypatch, _answer(http.client.RemoteDisconnected("closed")))(_request("GET", mutating=False))
    assert caught.value.reason == "transport_failed"


# ---------------------------------------------------------------------------------------------
# A scripted provider behind the real transport and the real adapters
# ---------------------------------------------------------------------------------------------


class ScriptedProvider:
    """LABELLED synthetic calendar provider behind the REAL KAS transport (open_remote_url replaced).

    Writes are applied to ``events`` the way the provider applies them. ``outcomes`` scripts each write's
    reply in order: None (delivered), ("body_lost", exc) (applied, status sent, body lost), ("reply_lost",
    exc) (applied, no reply at all), ("not_sent", exc) (never reached the provider)."""

    def __init__(self, monkeypatch, dialect):
        self.dialect = dialect
        self.events: dict[str, dict] = {}
        self.writes: list[str] = []
        self.outcomes: list = []
        self.transport = _transport(monkeypatch, self._open)

    def adapter(self):
        cls = {"graph": GraphCalendarAdapter, "google": GoogleCalendarAdapter, "caldav": CalDavCalendarAdapter}[self.dialect]
        return cls(transport=self.transport, config=AdapterConfig(provider_id=self.dialect, base_url=_BASE))

    def seed(self, event_id, title):
        if self.dialect == "caldav":
            self.events[event_id] = {"ics": _vcal(f"UID:{event_id}", f"SUMMARY:{title}", "DTSTART:20260915T120000Z", "DTEND:20260915T123000Z")}
            return
        block = lambda at: {"dateTime": at, "timeZone": "UTC"}  # noqa: E731
        key, title_key = ("etag", "summary") if self.dialect == "google" else ("@odata.etag", "subject")
        self.events[event_id] = {"id": event_id, title_key: title, "start": block("2026-09-15T12:00:00+00:00"),
                                 "end": block("2026-09-15T12:30:00+00:00"), key: '"v1"'}

    def _open(self, url, kwargs):
        method, path, body = kwargs["method"], unquote(urlparse(url).path), kwargs.get("data") or b""
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return self._read(path, body, url)
        self.writes.append(method)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome and outcome[0] == "not_sent":
            raise outcome[1]
        status, payload = self._apply(method, path, body)
        if outcome and outcome[0] == "reply_lost":
            raise outcome[1]
        if outcome and outcome[0] == "body_lost":
            return Reply(status, fails_with=outcome[1])
        return Reply(status, payload)

    def _apply(self, method, path, body):
        tail = path.rsplit("/", 1)[-1]
        if self.dialect == "caldav":
            uid = tail.removesuffix(".ics")
            if method == "DELETE":
                self.events.pop(uid, None)
                return 204, b""
            self.events[uid] = {"ics": body.decode("utf-8")}
            return 201, b""
        etag_key = "etag" if self.dialect == "google" else "@odata.etag"
        if method == "POST":
            data = json.loads(body)
            event_id = data.get("id") or f"graph-{len(self.events) + 1}"
            self.events[event_id] = {**data, "id": event_id, etag_key: '"v1"'}
            return (200 if self.dialect == "google" else 201), json.dumps(self.events[event_id]).encode("utf-8")
        if method == "PATCH":
            self.events[tail] = {**self.events[tail], **json.loads(body), etag_key: '"v2"'}
            return 200, json.dumps(self.events[tail]).encode("utf-8")
        self.events.pop(tail, None)
        return 204, b""

    def _read(self, path, body, url):
        if self.dialect == "caldav":
            wanted = re.search(r"<C:text-match[^>]*>([^<]+)</C:text-match>", body.decode("utf-8"))
            rows = [(uid, row["ics"]) for uid, row in self.events.items() if not wanted or uid == wanted.group(1)]
            return Reply(207, _multistatus(*(_row(uid, ics) for uid, ics in rows)).body)
        if path.endswith("/calendarview"):
            return Reply(200, json.dumps({"value": list(self.events.values())}).encode("utf-8"))
        if path.endswith("/events"):
            return Reply(200, json.dumps({"items": list(self.events.values())}).encode("utf-8"))
        event_id = path.rsplit("/", 1)[-1]
        if event_id in self.events:
            return Reply(200, json.dumps(self.events[event_id]).encode("utf-8"))
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"error": {"code": "notFound"}}'))


@pytest.mark.parametrize(("dialect", "failure"), [
    pytest.param("graph", http.client.IncompleteRead(b'{"id": "gr', 180), id="graph-create-incomplete"),
    pytest.param("google", TimeoutError("body stalled"), id="google-create-body-timeout"),
    pytest.param("caldav", ConnectionResetError(54, "Connection reset by peer"), id="caldav-put-reset"),
])
def test_create_accepted_with_a_lost_body_is_accepted_unverified_with_the_identity_it_has(monkeypatch, dialect, failure):
    provider = ScriptedProvider(monkeypatch, dialect)
    provider.outcomes = [("body_lost", failure)]
    event = CalEvent(provider_id=dialect, calendar_id="work", uid="intent-7@vool.local", summary="Novel generator test",
                     start_utc="2026-09-15T12:00:00+00:00", end_utc="2026-09-15T12:30:00+00:00", tz_name="UTC")
    with pytest.raises(CalendarWriteAcceptedError) as caught:
        provider.adapter().create_event("work", event)
    retained = {"graph": "", "google": google_event_id("intent-7@vool.local"), "caldav": "intent-7@vool.local"}[dialect]
    assert caught.value.provider_uid == retained and len(provider.events) == 1
    assert provider.writes == ["PUT" if dialect == "caldav" else "POST"]


@pytest.mark.parametrize(("dialect", "operation"), [
    pytest.param(dialect, operation, id=f"{dialect}-{operation}") for dialect in ("graph", "google", "caldav") for operation in ("update", "cancel")
])
def test_update_and_cancel_accepted_with_a_lost_body_keep_the_event_identity(monkeypatch, dialect, operation):
    provider = ScriptedProvider(monkeypatch, dialect)
    provider.seed("evtlost7", "Novel boiler descale")
    current = provider.adapter().get_event("work", "evtlost7")
    provider.outcomes = [("body_lost", http.client.IncompleteRead(b"", 32))]
    with pytest.raises(CalendarWriteAcceptedError) as caught:
        if operation == "update":
            provider.adapter().update_event("work", replace(current, summary="Novel boiler descale (moved)"))
        else:
            provider.adapter().cancel_event("work", current.uid, current.etag)
    assert (caught.value.provider_uid, caught.value.reason) == ("evtlost7", "accepted_reply_unreadable")
    write = "DELETE" if operation == "cancel" else ("PUT" if dialect == "caldav" else "PATCH")
    assert provider.writes == [write]


# ---------------------------------------------------------------------------------------------
# The production executor: accepted evidence is durable, a replay only verifies
# ---------------------------------------------------------------------------------------------


def test_graph_create_accepted_with_a_lost_body_rests_accepted_and_a_replay_only_verifies(monkeypatch, tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel switchgear inspection")
    provider = ScriptedProvider(monkeypatch, "graph")
    provider.outcomes = [("body_lost", http.client.IncompleteRead(b'{"id":', 311))]
    first = _execute(path, aid, config, provider.adapter())
    state, evidence = _stored(path, aid)
    assert (first.status, state, evidence["operation"]["phase"]) == ("outcome_unproven", "outcome_unproven", "accepted"), first.response_text
    assert provider.writes == ["POST"] and len(provider.events) == 1

    replay = _execute(path, aid, config, provider.adapter())  # a fresh adapter and transport: only durable evidence carries over
    assert replay.ok and _stored(path, aid)[0] == "executed", replay.response_text
    assert provider.writes == ["POST"], "no repeat dispatch after known acceptance"


def test_graph_create_accepted_then_removed_is_reported_missing_never_recreated(monkeypatch, tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel busbar torque check")
    provider = ScriptedProvider(monkeypatch, "graph")
    provider.outcomes = [("body_lost", TimeoutError("body stalled"))]
    assert _execute(path, aid, config, provider.adapter()).status == "outcome_unproven"
    provider.events.clear()
    replay = _execute(path, aid, config, provider.adapter())
    assert replay.status == "effect_missing" and provider.writes == ["POST"] and provider.events == {}, replay.response_text


def test_google_create_whose_reply_never_came_back_is_reconciled_by_reading_not_resending(monkeypatch, tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="google", title="Novel feeder pump swap")
    provider = ScriptedProvider(monkeypatch, "google")
    provider.outcomes = [("reply_lost", http.client.RemoteDisconnected("Remote end closed connection without response"))]
    first = _execute(path, aid, config, provider.adapter())
    assert first.status == "outcome_unproven" and _stored(path, aid)[0] == "outcome_unproven", first.response_text

    replay = _execute(path, aid, config, provider.adapter())
    assert replay.ok and provider.writes == ["POST"], replay.response_text
    assert list(provider.events) == [google_event_id(scope["intent_uid"])] and _stored(path, aid)[0] == "executed"


def test_google_create_refused_before_connecting_returns_to_approval_and_sends_once_on_reapproval(monkeypatch, tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="google", title="Novel intake screen clean")
    provider = ScriptedProvider(monkeypatch, "google")
    provider.outcomes = [("not_sent", urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")))]
    first = _execute(path, aid, config, provider.adapter())
    assert first.details["reason"] == "transport_failed" and _stored(path, aid)[0] == "pending_approval" and provider.events == {}, first.response_text

    second = _execute(path, aid, config, provider.adapter())
    assert second.ok and provider.writes == ["POST", "POST"] and len(provider.events) == 1, second.response_text


def test_update_accepted_with_a_lost_body_is_verified_on_replay_without_a_second_patch(monkeypatch, tmp_path):
    provider = ScriptedProvider(monkeypatch, "graph")
    provider.seed("evt-update", "Novel chiller check")
    event = provider.adapter().get_event("work", "evt-update")
    kind = "provider_calendar_update"
    path, aid = lifecycle._stage(tmp_path, kind, lifecycle._update_scope(event))
    provider.outcomes = [("body_lost", ConnectionResetError(54, "Connection reset by peer"))]
    first = lifecycle._approve(path, kind, aid, provider.adapter())
    assert first.status == "outcome_unproven" and first.details.get("accepted") is True, first.response_text

    replay = lifecycle._approve(path, kind, aid, provider.adapter())
    assert replay.ok and provider.writes == ["PATCH"] and lifecycle._row(path, kind, aid)["status"] == "executed", replay.response_text


def test_cancel_accepted_with_a_lost_body_is_recorded_on_replay_without_a_second_delete(monkeypatch, tmp_path):
    provider = ScriptedProvider(monkeypatch, "google")
    provider.seed("evtcancel1", "Novel roof drain check")
    event = provider.adapter().get_event("work", "evtcancel1")
    kind = "provider_calendar_cancel"
    path, aid = lifecycle._stage(tmp_path, kind, {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    provider.outcomes = [("body_lost", http.client.IncompleteRead(b"", 2))]
    first = lifecycle._approve(path, kind, aid, provider.adapter())
    assert first.status == "outcome_unproven" and first.details.get("accepted") is True, first.response_text
    assert "evtcancel1" not in provider.events

    replay = lifecycle._approve(path, kind, aid, provider.adapter())
    assert replay.ok and provider.writes == ["DELETE"] and lifecycle._row(path, kind, aid)["status"] == "executed", replay.response_text
