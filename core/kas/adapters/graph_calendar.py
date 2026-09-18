"""Microsoft Outlook/Microsoft365 calendars, as a KAS adapter: Graph v1.0 JSON in, typed
VOOL data out.

Nothing here decides anything — no socket, no credential, no policy. It builds Microsoft
Graph REST requests, hands them to the transport VOOL gave it, and reads the JSON replies
into the shared calendar vocabulary. The wire shapes are the documented public API:

* calendars discovery: ``GET /me/calendars`` (paged by ``@odata.nextLink``);
* range reads: ``GET /me/calendars/{id}/calendarview?startDateTime=&endDateTime=`` — the
  calendarView endpoint expands recurring series into dated instances server-side, and
  instance ids/etags are their own, which is the original-vs-instance identity;
* one event: ``GET /me/events/{id}``;
* create: ``POST /me/calendars/{id}/events``;
* update: ``PATCH /me/events/{id}`` with ``If-Match`` on the etag the caller last saw
  (Graph serves weak etags; a concurrent change answers 412, surfaced as
  ``precondition_failed`` and never silently clobbered);
* cancel: ``DELETE`` with ``If-Match``.

Bearer credentials are attached by the transport from a named binding; this adapter
never sees a token. Personal (outlook.com) and organizational (Microsoft365) accounts
are the SAME Graph surface here — the honest differences (tenant restrictions, shared
calendars, delegated scopes) arrive as the provider's own typed answers (403 with a
reason, 429) and are reported as exactly what they are, never silently redirected to a
different calendar.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlsplit

from core.kas.adapters._json_calendars import cal_to_json, collection_rows, event_from_json, json_body
from core.kas.contract import (
    AdapterConfig,
    CalCalendar,
    CalendarAdapter,
    CalendarReadUnusableError,
    CalendarRefusedError,
    CalendarWriteAcceptedError,
    CalEvent,
    KasRequest,
    KasResponse,
    TransportAcceptedError,
    accepted_reply_lost,
    accepted_write_error,
)
from core.kas.registry import register_adapter

#: Bounded completeness (see google_calendar._MAX_PAGES): a cap hit with pages
#: remaining, a repeated skip token, or a next link the host pin refuses to follow all
#: raise a typed incomplete_read refusal rather than returning a partial truth.
_MAX_PAGES = 50


def graph_transaction_id(durable_uid: str) -> str:
    """Graph's DOCUMENTED create-deduplication identity, derived from the durable intent.

    Graph deduplicates POSTs that carry the same ``transactionId`` (an opaque string on
    the event resource), assigning only one provider id. Derived deterministically from
    the logical intent uid so a replay of one intent can never become two events.
    """
    import hashlib

    seed = str(durable_uid or "").strip()
    if not seed:
        import uuid

        seed = uuid.uuid4().hex
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _refusal(response: KasResponse, *, purpose: str) -> CalendarRefusedError:
    status = int(response.status)
    reason = {
        401: "authorization_expired",
        403: "http_403",
        404: "not_found",
        409: "already_exists",
        410: "not_found",
        412: "precondition_failed",
        429: "rate_limited",
    }.get(status, f"http_{status}")
    return CalendarRefusedError(status, reason=reason, detail=f"microsoft graph {purpose} refused: HTTP {status}")


def _id(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise CalendarRefusedError(400, reason="missing_calendar", detail="no calendar id was given")
    if clean.startswith(("http://", "https://")):
        clean = clean.rstrip("/").rpartition("/")[2] or clean
    return quote(clean.strip("/"), safe="=-_:")


@register_adapter
class GraphCalendarAdapter(CalendarAdapter):
    kind = "calendar"
    provider_id = "graph"

    def __init__(self, *, transport: Any, config: AdapterConfig) -> None:
        super().__init__(transport=transport, config=config)
        self._timeout = float(str(config.options.get("timeout") or "20"))

    def _send_json(self, request: KasRequest) -> KasResponse:
        response = self.send(request)
        if response.ok:
            return response
        raise _refusal(response, purpose=str(request.purpose or "request"))

    def _url(self, suffix: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{suffix.lstrip('/')}"

    def _paged(self, first_url: str, *, purpose: str) -> list[tuple[int, dict[str, Any]]]:
        """Every row of a paged collection, with the status of the page that served it."""
        entries: list[tuple[int, dict[str, Any]]] = []
        url: str | None = first_url
        pages = 0
        seen_urls: set[str] = set()
        while url:
            response = self._send_json(KasRequest(method="GET", url=url, purpose=purpose, timeout=self._timeout))
            what = f"microsoft graph {purpose}, page {pages + 1}"
            payload = json_body(response, what=what)
            entries.extend((response.status, item) for item in collection_rows(payload, key="value", status=response.status, what=what))
            # Graph pages by ABSOLUTE @odata.nextLink; same-origin only, so a compromised
            # reply cannot steer credentialed traffic to another host through this loop.
            # A next link the pin refuses to follow is NOT a silent stop: the read is
            # provably incomplete and says so.
            nxt = str(payload.get("@odata.nextLink") or "").strip()
            pages += 1
            if not nxt:
                break
            origin_a = (urlsplit(self._url("")).scheme, urlsplit(self._url("")).netloc)
            origin_b = (urlsplit(nxt).scheme, urlsplit(nxt).netloc)
            if origin_a != origin_b:
                raise CalendarRefusedError(509, reason="incomplete_read", detail="the provider's next page link points off-origin; following it would leave the pinned host, so the read stops incomplete")
            if nxt in seen_urls:
                raise CalendarRefusedError(509, reason="incomplete_read", detail="the provider repeated a page link; the read cannot be proven complete")
            seen_urls.add(nxt)
            url = nxt
            if pages >= _MAX_PAGES:
                raise CalendarRefusedError(509, reason="incomplete_read", detail=f"the read hit its page bound ({_MAX_PAGES}) with pages remaining; availability cannot be claimed from a partial read")
        return entries

    def list_calendars(self) -> list[CalCalendar]:
        entries = self._paged(self._url("me/calendars"), purpose="list calendars")
        calendars: list[CalCalendar] = []
        for status, item in entries:
            cid = item["id"].strip() if isinstance(item.get("id"), str) else ""
            if not cid:
                raise CalendarReadUnusableError(status, detail="microsoft graph list calendars: a calendar has no id")
            can_edit = item.get("canEdit")  # true if the user can write to the calendar; absent leaves it unknown
            calendars.append(CalCalendar(
                provider_id=self.provider_id,
                calendar_id=cid,
                display_name=str(item.get("name") or cid),
                can_write=can_edit if isinstance(can_edit, bool) else None,
                provider_default=item.get("isDefaultCalendar") is True,
            ))
        return calendars

    def events_in_range(self, calendar_id: str, *, start_utc: str, end_utc: str) -> list[CalEvent]:
        url = (
            self._url(f"me/calendars/{_id(calendar_id)}/calendarview")
            + f"?startDateTime={quote(start_utc)}&endDateTime={quote(end_utc)}&$top=100"
        )
        entries = self._paged(url, purpose="events in time range")
        events: list[CalEvent] = [
            event_from_json(item, provider_id=self.provider_id, calendar_id=_id(calendar_id), status=status,
                            etag_key="@odata.etag", href_key="webLink", what=f"microsoft graph events in time range, row {index}")
            for index, (status, item) in enumerate(entries, start=1)
        ]
        timed = sorted((e for e in events if not e.all_day), key=lambda e: (e.start_utc, e.uid))
        all_day = sorted((e for e in events if e.all_day), key=lambda e: (e.start_date, e.uid))
        return all_day + timed

    def get_event(self, calendar_id: str, uid: str) -> CalEvent:
        clean = str(uid or "").strip()
        if not clean:
            raise CalendarRefusedError(400, reason="missing_uid", detail="no event id was given")
        url = self._url(f"me/events/{_id(clean)}")
        response = self._send_json(KasRequest(method="GET", url=url, purpose="event by id", timeout=self._timeout))
        # Only 404/410 (raised by _send_json) prove absence. A success reply that is not this event --
        # no body, an unusable event, another id -- is unreadable, never not_found.
        what = "microsoft graph event by id"
        event = event_from_json(json_body(response, what=what), provider_id=self.provider_id, calendar_id=_id(calendar_id),
                                status=response.status, etag_key="@odata.etag", href_key="webLink", what=what)
        if event.uid != clean:
            raise CalendarReadUnusableError(response.status, detail=f"{what}: the reply carries a different event")
        return event

    def create_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        uid = str(event.uid or "").strip()
        body = cal_to_json(event, uid="", provider="graph")
        if uid:
            # Graph's documented deduplication field — NOT an invented common header:
            # a replayed create returns the SAME provider-assigned event.
            body["transactionId"] = graph_transaction_id(uid)
        headers = {"Content-Type": "application/json"}
        if uid:
            headers["Idempotency-Key"] = f"create:{uid}"
        url = self._url(f"me/calendars/{_id(calendar_id)}/events")
        try:
            response = self._send_json(KasRequest(method="POST", url=url, purpose="create event", headers=headers, body=json.dumps(body).encode("utf-8"), timeout=self._timeout, mutating=True, idempotency_key=f"create:{uid}" if uid else ""))
        except TransportAcceptedError as exc:
            # Accepted, and the reply carrying the provider-assigned id was lost: there is no id to
            # retain, so recovery identifies the event by its transactionId, never by content.
            raise accepted_reply_lost("", exc, purpose="create") from None
        # Past this line the provider ACCEPTED the create. Graph returns the created object with
        # its provider-assigned id: that is the identity to retain and verify, never a guess, and
        # nothing below may read as a refusal.
        try:
            created_id = str((response.json() or {}).get("id") or "").strip()
        except Exception as exc:
            raise accepted_write_error("", exc, purpose="create", status_code=response.status) from exc
        if not created_id:
            raise CalendarWriteAcceptedError(status_code=response.status, reason="accepted_without_identity",
                                             detail="the provider accepted the create but returned no event id")
        try:
            return self.get_event(calendar_id, created_id)
        except Exception as exc:
            raise accepted_write_error(created_id, exc, purpose="create", status_code=response.status) from exc

    def update_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        current = self.get_event(calendar_id, event.uid)
        etag = str(event.etag or current.etag).strip(" \t")
        if not etag:
            raise CalendarRefusedError(409, reason="no_version_token", detail="the provider gave no etag to protect this update")
        patch = cal_to_json(event, uid="", provider="graph")
        patch.pop("id", None)
        url = self._url(f"me/events/{_id(str(event.uid))}")
        try:
            self._send_json(KasRequest(
                method="PATCH", url=url, purpose="update event",
                headers={"Content-Type": "application/json", "If-Match": etag},
                body=json.dumps(patch).encode("utf-8"), timeout=self._timeout, mutating=True,
            ))
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(str(event.uid), exc, purpose="update") from None
        # Past this line the provider ACCEPTED the update: a failed read-back is not a refusal.
        try:
            return self.get_event(calendar_id, event.uid)
        except Exception as exc:
            raise accepted_write_error(str(event.uid), exc, purpose="update") from exc

    def cancel_event(self, calendar_id: str, uid: str, etag: str) -> bool:
        current = self.get_event(calendar_id, uid)
        token = str(etag or current.etag).strip()
        headers = {"If-Match": token} if token else {}
        url = self._url(f"me/events/{_id(str(uid))}")
        try:
            self._send_json(KasRequest(method="DELETE", url=url, purpose="cancel event", headers=headers, timeout=self._timeout, mutating=True))
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(str(uid), exc, purpose="cancellation") from None
        try:
            self.get_event(calendar_id, uid)
        except CalendarRefusedError as exc:
            if exc.reason == "not_found":
                return True
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        raise CalendarRefusedError(409, reason="still_present", detail="the provider still serves the event after deletion")


    def durable_to_provider_id(self, durable_uid: str) -> str:
        """Graph assigns ids itself: no deterministic mapping exists. Recovery identifies the
        event by the id retained at acceptance or by its documented transactionId, never by
        content and never by guessing an id."""
        return ""

    def durable_correlation(self, durable_uid: str) -> str:
        """Graph's documented ``transactionId`` for the durable intent: set on create, returned on
        reads once set, and unchangeable afterwards -- which stored event this operation wrote."""
        return graph_transaction_id(durable_uid) if str(durable_uid or "").strip() else ""


__all__ = ["GraphCalendarAdapter", "graph_transaction_id"]
