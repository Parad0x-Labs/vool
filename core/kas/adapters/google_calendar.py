"""Google Calendar, as a KAS adapter: Calendar API v3 JSON in, typed VOOL data out.

Nothing here decides anything — no socket, no credential, no policy. It builds v3 REST
requests, hands them to the transport VOOL gave it, and reads the JSON replies into the
shared calendar vocabulary. The wire shapes are the documented public API:

* calendars discovery: ``GET /users/me/calendarList`` (paged by ``pageToken``);
* range reads: ``GET /calendars/{calendarId}/events`` with ``timeMin``/``timeMax`` and
  ``singleEvents=true`` so recurring SERIES expand into dated instances — an instance's
  id is ``{seriesId}_{RFC3339Instant}`` and its etag is its own, which is exactly the
  original-vs-instance targeting the contract's uid+etag identity needs;
* one event: ``GET /calendars/{calendarId}/events/{eventId}``;
* create: ``POST /calendars/{calendarId}/events`` (``Idempotency-Key`` header honored);
* update: ``PATCH /calendars/{calendarId}/events/{eventId}`` with ``If-Match`` on the
  etag the caller last saw — a concurrent server-side change answers 412, surfaced as
  ``precondition_failed`` and never silently clobbered;
* cancel: ``DELETE`` with ``If-Match``.

Bearer credentials are attached by the transport from a named binding; this adapter
never sees a token. 401/403 are the provider's definitive authorization answers
(``authorization_expired`` / ``http_403``), reported truthfully — token refresh and
revocation are the owning credential authority's concern, not the adapter's.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from core.kas.adapters._json_calendars import cal_to_json, collection_rows, event_from_json, json_body
from core.kas.contract import (
    AdapterConfig,
    CalCalendar,
    CalendarAdapter,
    CalendarReadUnusableError,
    CalendarRefusedError,
    CalEvent,
    KasRequest,
    KasResponse,
    TransportAcceptedError,
    accepted_reply_lost,
    accepted_write_error,
)
from core.kas.registry import register_adapter

#: Bounded completeness. Reads page until the provider stops or this cap is reached;
#: hitting the cap WITH pages remaining raises a typed incomplete_read refusal — a
#: partial result must never answer a free-slot or conflict-free claim.
_MAX_PAGES = 50


def google_event_id(durable_uid: str) -> str:
    """A valid Google event id for a durable logical intent identity.

    Google requires ids from [a-v0-9] with length 5..1024; a UUID-with-suffix is
    rejected outright. The mapping is DETERMINISTIC (sha256 of the intent uid, hex
    digits are within the alphabet), so replaying the same intent addresses the same
    provider identity — reconciliation, not duplication.
    """
    import hashlib

    seed = str(durable_uid or "").strip()
    if not seed:
        import uuid

        seed = uuid.uuid4().hex
    if re.fullmatch(r"[a-v0-9]{5,1024}", seed):
        return seed
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
    return CalendarRefusedError(status, reason=reason, detail=f"google calendar {purpose} refused: HTTP {status}")


def _cal_id(calendar_id: str) -> str:
    calendar = str(calendar_id or "").strip()
    if not calendar:
        raise CalendarRefusedError(400, reason="missing_calendar", detail="no calendar id was given")
    if calendar.startswith(("http://", "https://")):
        calendar = calendar.rstrip("/").rpartition("/")[2] or calendar
    return quote(calendar.strip("/"), safe="@")


@register_adapter
class GoogleCalendarAdapter(CalendarAdapter):
    kind = "calendar"
    provider_id = "google"

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

    def list_calendars(self) -> list[CalCalendar]:
        entries: list[tuple[int, dict[str, Any]]] = []
        token = ""
        pages = 0
        seen_tokens: set[str] = set()
        while True:
            url = self._url("users/me/calendarList") + (f"?pageToken={quote(token)}" if token else "?maxResults=100")
            response = self._send_json(KasRequest(method="GET", url=url, purpose="list calendars", timeout=self._timeout))
            what = f"google calendar list calendars, page {pages + 1}"
            payload = json_body(response, what=what)
            entries.extend((response.status, item) for item in collection_rows(payload, key="items", kind="calendar#calendarList", status=response.status, what=what))
            token = str(payload.get("nextPageToken") or "")
            pages += 1
            if not token:
                break
            if token in seen_tokens:
                raise CalendarRefusedError(509, reason="incomplete_read", detail="the provider repeated a page token; the calendar list cannot be proven complete")
            seen_tokens.add(token)
            if pages >= _MAX_PAGES:
                raise CalendarRefusedError(509, reason="incomplete_read", detail=f"the calendar list hit its page bound ({_MAX_PAGES}) with pages remaining")
        calendars: list[CalCalendar] = []
        for status, item in entries:
            cid = item["id"].strip() if isinstance(item.get("id"), str) else ""
            if not cid:
                raise CalendarReadUnusableError(status, detail="google calendar list calendars: a calendar has no id")
            # calendarList accessRole: owner, writer and writerWithoutPrivateAccess may write; reader and
            # freeBusyReader may not. A listing that states no role (or one this code does not know) leaves it unknown.
            role = item.get("accessRole")
            can_write = True if role in ("owner", "writer", "writerWithoutPrivateAccess") else False if role in ("reader", "freeBusyReader") else None
            calendars.append(CalCalendar(
                provider_id=self.provider_id,
                calendar_id=cid,
                display_name=str(item.get("summaryOverride") or item.get("summary") or cid),
                can_write=can_write,
                provider_default=item.get("primary") is True,
            ))
        return calendars

    def events_in_range(self, calendar_id: str, *, start_utc: str, end_utc: str) -> list[CalEvent]:
        events: list[CalEvent] = []
        token = ""
        pages = 0
        seen_tokens: set[str] = set()
        cal = _cal_id(calendar_id)
        while True:
            url = (
                self._url(f"calendars/{cal}/events")
                + f"?timeMin={quote(start_utc)}&timeMax={quote(end_utc)}&singleEvents=true&orderBy=startTime&maxResults=250"
                + (f"&pageToken={quote(token)}" if token else "")
            )
            response = self._send_json(KasRequest(method="GET", url=url, purpose="events in time range", timeout=self._timeout))
            what = f"google calendar events in time range, page {pages + 1}"
            payload = json_body(response, what=what)
            for index, item in enumerate(collection_rows(payload, key="items", kind="calendar#events", status=response.status, what=what), start=1):
                if item.get("status") == "cancelled":
                    # A cancelled instance occupies no time; Google guarantees only its id, series and
                    # original start, so it is set aside explicitly rather than read as an event.
                    continue
                organizer = item.get("organizer")
                owner = organizer.get("calendarId") if isinstance(organizer, dict) else None
                events.append(event_from_json(item, provider_id=self.provider_id, calendar_id=owner if isinstance(owner, str) and owner else cal,
                                              status=response.status, etag_key="etag", href_key="htmlLink", what=f"{what}, row {index}"))
            token = str(payload.get("nextPageToken") or "")
            pages += 1
            if not token:
                break
            if token in seen_tokens:
                raise CalendarRefusedError(509, reason="incomplete_read", detail="the provider repeated a page token; the read cannot be proven complete")
            seen_tokens.add(token)
            if pages >= _MAX_PAGES:
                raise CalendarRefusedError(509, reason="incomplete_read", detail=f"the range read hit its page bound ({_MAX_PAGES}) with pages remaining; availability cannot be claimed from a partial read")
        timed = sorted((e for e in events if not e.all_day), key=lambda e: (e.start_utc, e.uid))
        all_day = sorted((e for e in events if e.all_day), key=lambda e: (e.start_date, e.uid))
        return all_day + timed

    def get_event(self, calendar_id: str, uid: str) -> CalEvent:
        clean = str(uid or "").strip()
        if not clean:
            raise CalendarRefusedError(400, reason="missing_uid", detail="no event id was given")
        url = self._url(f"calendars/{_cal_id(calendar_id)}/events/{quote(clean, safe='@_')}")
        response = self._send_json(KasRequest(method="GET", url=url, purpose="event by id", timeout=self._timeout))
        # Only 404/410 (raised by _send_json) prove absence. A success reply that is not this event --
        # no body, an unusable event, another id -- is unreadable, never not_found.
        what = "google calendar event by id"
        event = event_from_json(json_body(response, what=what), provider_id=self.provider_id, calendar_id=_cal_id(calendar_id),
                                status=response.status, etag_key="etag", href_key="htmlLink", what=what)
        if event.uid != clean:
            raise CalendarReadUnusableError(response.status, detail=f"{what}: the reply carries a different event")
        return event

    def create_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        durable = str(event.uid or "").strip()
        uid = google_event_id(durable)
        body = cal_to_json(event, uid=uid, provider="google")
        headers = {"Content-Type": "application/json"}
        if uid:
            headers["Idempotency-Key"] = f"create:{uid}"
        # Google's documented guest-notification policy (Events.insert sendUpdates): 'none'
        # stores attendees WITHOUT emailing anyone; 'all' asks Google to notify every guest.
        # The invitation is an outward effect the approval reviewed, so it is stated EXACTLY:
        # all when the event carries reviewed attendees, none otherwise -- never the silent
        # default, never an unreviewed send.
        send_updates = "all" if event.attendees else "none"
        url = self._url(f"calendars/{_cal_id(calendar_id)}/events") + f"?sendUpdates={send_updates}"
        try:
            response = self._send_json(KasRequest(method="POST", url=url, purpose="create event", headers=headers, body=json.dumps(body).encode("utf-8"), timeout=self._timeout, mutating=True, idempotency_key=f"create:{uid}" if uid else ""))
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(uid, exc, purpose="create") from None
        # Past this line the provider ACCEPTED the create: nothing below may read as a refusal.
        try:
            payload = response.json() or {}
            created_id = str(payload.get("id") or uid)
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="create", status_code=response.status) from exc
        try:
            return self.get_event(calendar_id, created_id)
        except Exception as exc:
            raise accepted_write_error(created_id, exc, purpose="create", status_code=response.status) from exc

    def update_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        current = self.get_event(calendar_id, event.uid)
        etag = str(event.etag or current.etag).strip(" 	")
        if not etag:
            raise CalendarRefusedError(409, reason="no_version_token", detail="the provider gave no etag to protect this update")
        # Round-trip fidelity: patch only the vocabulary's own fields, so provider-side
        # recurrence rules, attendees and extensions survive the update untouched.
        patch = cal_to_json(event, uid="", provider="google")
        patch.pop("id", None)
        url = self._url(f"calendars/{_cal_id(calendar_id)}/events/{quote(str(event.uid), safe='@_')}")
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

    def durable_to_provider_id(self, durable_uid: str) -> str:
        """The durable intent's valid Google id — the SAME deterministic mapping the
        create path uses, so a lost create reply is reconciled by READING the mapped
        id instead of POSTing again into a 409."""
        return google_event_id(durable_uid)

    def cancel_event(self, calendar_id: str, uid: str, etag: str) -> bool:
        current = self.get_event(calendar_id, uid)
        token = str(etag or current.etag).strip(" 	")
        headers = {"If-Match": token} if token else {}
        url = self._url(f"calendars/{_cal_id(calendar_id)}/events/{quote(str(uid), safe='@_')}")
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


_GOOGLE_INSTANCE_RE = re.compile(r"^(?P<series>[A-Za-z0-9][A-Za-z0-9-]*?)_(?P<instant>\d{8}T\d{6}Z)$")


def splits_recurring_instance(uid: str) -> tuple[str, str] | None:
    """A Google recurring-instance id -> (series id, instance instant), or None.

    The original-vs-instance distinction the provider itself makes: an instance id is
    the series id plus the dated instant it was expanded from, so a caller can name
    EITHER the series (edit all) or one instance (edit one) and the identity stays
    honest about which it addressed.
    """
    match = _GOOGLE_INSTANCE_RE.match(str(uid or "").strip())
    if not match:
        return None
    return match.group("series"), match.group("instant")


__all__ = ["GoogleCalendarAdapter", "google_event_id", "splits_recurring_instance"]
