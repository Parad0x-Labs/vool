"""Apple Calendar on macOS, as a KAS-family adapter: EventKit in, typed VOOL data out.

This is the DECLARED NATIVE-OS boundary the calendar matrix requires, and it is
deliberately NOT an HTTP adapter: it holds no socket and ignores the transport it is
handed, because a local OS database is not a remote service and pretending otherwise
(faking an HTTP server to resemble the cloud adapters) would prove nothing about the
real store. What it shares with the cloud adapters is the one CalendarAdapter CONTRACT
and every VOOL-side law above it (permission door, approval turns, receipts).

Real integration surface (Apple EventKit, macOS):
* ``EKEventStore`` scoped to events; the app-level permission state is read from
  ``EKEventStore.authorizationStatusForEntityType`` — NOT bypassed and NOT re-requested
  in a loop. A denied/restricted state is a RECOVERABLE, typed state whose message
  names the exact System Settings pane, because only the user can grant it.
* calendars: ``calendarsForEntityType:`` with source identity (``calendar.source.title``,
  iana time zone, ``allowsContentModifications``) — read-only calendars are reported as
  read-only, never silently selected for writes.
* range reads: ``predicateForEventsWithStartDate:endDate:calendars:`` +
  ``eventsMatchingPredicate:`` (EventKit already expands recurring series into dated
  instances there).
* identity: ``eventIdentifier`` (stable across launches); version: the event's
  modification date string — EventKit has no etag, and the adapter SAYS so rather than
  inventing a token. Update/cancel therefore re-read the event first, exactly like the
  cloud adapters' etag discipline, and refuse on a changed modification date.
* mutations: ``saveEvent:span:commit:error:`` / ``removeEvent:span:commit:error:`` with
  ``EKSpanThisEvent`` for one instance and ``ThisAndFuture`` only when the caller named
  the series.

Runtime requirements (the packaging gate this adapter states out loud): macOS with the
pyobjc EventKit binding importable and the Calendars permission granted to the host app.
Absent binding or platform -> typed ``os_binding_unavailable``; absent permission ->
typed ``os_permission_denied``. Neither is ever reported as a connected calendar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.kas.registry import register_adapter
from core.kas.contract import (
    AdapterConfig,
    CalCalendar,
    CalendarAdapter,
    CalendarReadUnusableError,
    CalendarRefusedError,
    CalendarWriteAcceptedError,
    CalEvent,
    accepted_write_error,
)

_PERMISSION_GUIDE = (
    "grant Calendars access in System Settings > Privacy & Security > Calendars "
    "for this app, then ask again"
)


def _binding_error(detail: str) -> CalendarRefusedError:
    return CalendarRefusedError(
        503,
        reason="os_binding_unavailable",
        detail=detail,
    )


def _load_eventkit():
    """Import the pyobjc EventKit binding or raise the typed unavailable state.

    The binding is a packaging requirement of the native product (declared in its
    runtime requirements), not something this adapter installs or shims: a missing
    binding is an honest, typed state with the exact requirement named.
    """
    try:
        import EventKit
        import Foundation

        return EventKit, Foundation
    except Exception as exc:  # pragma: no cover - platform/binding dependent
        raise _binding_error(
            f"the pyobjc EventKit binding is required for native Apple Calendar "
            f"(macOS only); this runtime cannot import it ({exc}). "
            "Package the binding with the native app to enable this adapter."
        ) from None


class EventKitStore:
    """The real store behind the adapter, isolated so tests can inject authorization
    states WITHOUT faking calendar data: everything here is the actual EventKit call
    surface, and the adapter never fabricates events."""

    def __init__(self) -> None:
        EventKit, Foundation = _load_eventkit()
        self._ek = EventKit
        self._foundation = Foundation
        self._store = EventKit.EKEventStore.alloc().init()

    def authorization(self) -> str:
        """The OS's answer for Calendars access, read WITHOUT triggering a prompt.

        pyobjc exposes the class method with its selector's trailing underscore; the
        answer is the AuthorizationStatus enum (0 notDetermined, 1 restricted,
        2 denied, 3 authorized), mapped to its named truth.
        """
        raw = self._ek.EKEventStore.authorizationStatusForEntityType_(self._ek.EKEntityTypeEvent)
        try:
            code = int(raw)
        except (TypeError, ValueError):
            code = -1
        return {
            0: "not_determined",
            1: "restricted",
            2: "denied",
            3: "authorized",
        }.get(code, str(raw))

    def require_authorized(self) -> None:
        state = self.authorization()
        if state != "authorized":
            raise CalendarRefusedError(
                403,
                reason="os_permission_denied",
                detail=f"Apple Calendar access is {state}; {_PERMISSION_GUIDE}. Nothing was read or changed.",
            )

    def calendars(self) -> list[Any]:
        self.require_authorized()
        return list(self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or [])

    def events_in_range(self, start: datetime, end: datetime, calendar_ids: list[str]) -> list[Any]:
        """Events in a window, scoped by the CALENDAR THE CALLER NAMED.

        Identifiers are read through the selector bridge (they are methods, not
        values). An explicit selection that matches NOTHING is a typed refusal — never
        silently broadened to nil, because Apple documents a nil calendar list as
        searching ALL of the user's calendars and an unresolved name must not become an
        unrestricted query. An empty selection explicitly means all permitted calendars.
        """
        self.require_authorized()
        permitted = self.calendars()
        wanted = [str(value or "").strip() for value in (calendar_ids or []) if str(value or "").strip()]
        if wanted:
            calendars = [c for c in permitted if str(_sel(c, "calendarIdentifier") or "") in wanted]
            if not calendars:
                raise CalendarRefusedError(
                    404,
                    reason="unknown_calendar",
                    detail=f"none of the requested calendars are in the permitted store: {', '.join(wanted)}",
                )
        else:
            calendars = list(permitted)
        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            self._foundation.NSDate.dateWithTimeIntervalSince1970_(start.timestamp()),
            self._foundation.NSDate.dateWithTimeIntervalSince1970_(end.timestamp()),
            calendars or None,
        )
        return list(self._store.eventsMatchingPredicate_(predicate) or [])

    def event_by_id(self, uid: str) -> Any:
        self.require_authorized()
        event = self._store.eventWithEventIdentifier_(str(uid or "").strip())
        if event is None:
            raise CalendarRefusedError(404, reason="not_found", detail=f"no event with identifier {uid} in the permitted store")
        return event

    def save(self, event: Any) -> None:
        ok, error = self._store.saveEvent_span_commit_error_(event, self._ek.EKSpanThisEvent, True, None)
        if not ok:
            raise CalendarRefusedError(409, reason="os_save_refused", detail=f"EventKit refused the save: {error}")

    def remove(self, event: Any) -> None:
        ok, error = self._store.removeEvent_span_commit_error_(event, self._ek.EKSpanThisEvent, True, None)
        if not ok:
            raise CalendarRefusedError(409, reason="os_remove_refused", detail=f"EventKit refused the removal: {error}")

    def new_event(self) -> Any:
        self.require_authorized()
        return self._ek.EKEvent.eventWithEventStore_(self._store)


#: EKEventStatus: none 0, confirmed 1, tentative 2, canceled 3.
_EK_EVENT_STATUS_CANCELED = 3


def _nsdate_to_utc(nsdate: Any, foundation: Any) -> datetime:
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970(), tz=timezone.utc)


def _sel(obj: Any, name: str, default: Any = None) -> Any:
    """Read one EventKit property through the ACTUAL selector bridge.

    PyObjC exposes Objective-C accessors as METHODS (``event.eventIdentifier()``,
    ``calendar.source()``); reading them as Python attributes yields bound-method
    objects whose str() is junk and whose truthiness is always True. Accessor-or-value:
    call it when it is callable, return it when it is a plain property, default when
    absent.
    """
    value = getattr(obj, name, None)
    if value is None:
        return default
    if callable(value):
        try:
            return value()
        except TypeError:
            return value
    return value


def _version_of(event: Any) -> str:
    """EventKit has no etag; the modification date is the honest version token."""
    modified = _sel(event, "lastModifiedDate")
    if modified is None:
        return ""
    if isinstance(modified, str):
        return modified
    interval = getattr(modified, "timeIntervalSince1970", None)
    if callable(interval):
        from datetime import datetime as _dt

        return f"ek-modified-{_dt.fromtimestamp(interval(), tz=timezone.utc).isoformat()}"
    return str(modified)


def _ek_to_cal(event: Any, *, provider_id: str, foundation: Any) -> CalEvent:
    """Project one native event through the selector bridge (accessors are CALLS).

    An event without its identifier or without a readable start and end date cannot be placed: that
    is the typed unreadable answer -- never an invented "now", never a dropped row.
    """
    uid = str(_sel(event, "eventIdentifier") or "").strip()
    if not uid:
        raise CalendarReadUnusableError(detail="an event in the Mac event store has no identifier")
    start_raw = _sel(event, "startDate")
    end_raw = _sel(event, "endDate")
    if start_raw is None or end_raw is None:
        missing = "start" if start_raw is None else "end"
        raise CalendarReadUnusableError(detail=f"an event in the Mac event store has no {missing} date")
    try:
        start = _nsdate_to_utc(start_raw, foundation)
        end = _nsdate_to_utc(end_raw, foundation)
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        raise CalendarReadUnusableError(detail="an event in the Mac event store has dates that are not readable") from None
    all_day = bool(_sel(event, "isAllDay", False))
    calendar = _sel(event, "calendar")
    tz_name = ""
    if calendar is not None:
        zone = _sel(calendar, "timeZone")
        if zone is not None:
            zone_name = _sel(zone, "name")
            tz_name = str(zone_name or "")
    status = _sel(event, "status")
    cancelled = isinstance(status, int) and not isinstance(status, bool) and status == _EK_EVENT_STATUS_CANCELED
    # Every occurrence of a repeating event carries the series' eventIdentifier; occurrenceDate is the original
    # occurrence date, kept when one occurrence is moved (detached), so it is the occurrence's identity.
    original_start = ""
    if bool(_sel(event, "hasRecurrenceRules", False)) or bool(_sel(event, "isDetached", False)):
        occurrence = _sel(event, "occurrenceDate")
        if occurrence is not None:
            try:
                original_start = _nsdate_to_utc(occurrence, foundation).isoformat()
            except (AttributeError, TypeError, ValueError, OverflowError, OSError):
                raise CalendarReadUnusableError(detail="an occurrence in the Mac event store has an original date that is not readable") from None
    link = _sel(event, "URL")
    web_url = link if isinstance(link, str) else str(_sel(link, "absoluteString") or "") if link is not None else ""
    return CalEvent(
        provider_id=provider_id,
        uid=uid,
        calendar_id=str(_sel(calendar, "calendarIdentifier") or "") if calendar is not None else "",
        etag=_version_of(event),
        summary=str(_sel(event, "title") or ""),
        start_utc="" if all_day else start.isoformat(),
        end_utc="" if all_day else end.isoformat(),
        all_day=all_day,
        start_date=start.date().isoformat() if all_day else "",
        end_date=end.date().isoformat() if all_day else "",
        tz_name=tz_name,
        description=str(_sel(event, "notes") or ""),
        href="eventkit://" + uid,
        raw_component="",
        location=str(_sel(event, "location") or ""),
        web_url=web_url if web_url.lower().startswith(("https://", "http://")) else "",
        cancelled=cancelled,
        series_id=uid if original_start else "",
        original_start=original_start,
    )


@register_adapter
class EventKitCalendarAdapter(CalendarAdapter):
    """Native Apple Calendar through the shared calendar contract.

    Google/Exchange calendars that the user already synced INTO the Mac event store are
    visible here WITH their source identity — what this proves is the EventKit route
    into that store, not the direct Google/Graph API integration, and the capability
    truth says exactly that.
    """

    kind = "calendar"
    provider_id = "eventkit"

    def __init__(self, *, transport: Any, config: AdapterConfig, store: EventKitStore | None = None) -> None:
        super().__init__(transport=transport, config=config)
        self._store = store if store is not None else EventKitStore()
        self._foundation = None

    def _foundation_module(self):
        if self._foundation is None:
            _EventKit, Foundation = _load_eventkit()
            self._foundation = Foundation
        return self._foundation

    def list_calendars(self) -> list[CalCalendar]:
        rows: list[CalCalendar] = []
        for calendar in self._store.calendars():
            source = _sel(calendar, "source")
            source_name = ""
            if source is not None:
                source_title = _sel(source, "title")
                source_name = str(source_title or "") if source_title is not None else ""
            allows = _sel(calendar, "allowsContentModifications")  # whether items can be added, edited and deleted
            rows.append(CalCalendar(
                provider_id=self.provider_id,
                calendar_id=str(_sel(calendar, "calendarIdentifier") or ""),
                display_name=str(_sel(calendar, "title") or "") + (f" [{source_name}]" if source_name else ""),
                can_write=bool(allows) if isinstance(allows, (bool, int)) else None,
            ))
        return rows

    def events_in_range(self, calendar_id: str, *, start_utc: str, end_utc: str) -> list[CalEvent]:
        start = datetime.fromisoformat(str(start_utc).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(end_utc).replace("Z", "+00:00"))
        foundation = self._foundation_module()
        events = []
        for event in self._store.events_in_range(start, end, [str(calendar_id or "").strip()] if calendar_id else []):
            events.append(_ek_to_cal(event, provider_id=self.provider_id, foundation=foundation))
        timed = sorted((e for e in events if not e.all_day), key=lambda e: (e.start_utc, e.uid))
        all_day = sorted((e for e in events if e.all_day), key=lambda e: (e.start_date, e.uid))
        return all_day + timed

    def get_event(self, calendar_id: str, uid: str) -> CalEvent:
        # The store's own "no such event" (event_by_id) is the only absence; an event it returns that
        # cannot be read raises the typed unreadable answer instead.
        event = self._store.event_by_id(uid)
        return _ek_to_cal(event, provider_id=self.provider_id, foundation=self._foundation_module())

    def create_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        writable = [c for c in self._store.calendars() if str(c.calendarIdentifier()) == str(calendar_id) and c.allowsContentModifications()]
        if not writable:
            raise CalendarRefusedError(403, reason="calendar_read_only", detail="that calendar is read-only in the permitted store; select a writable calendar")
        foundation = self._foundation_module()
        ek_event = self._store.new_event()
        ek_event.setTitle_(event.summary)
        ek_event.setNotes_(event.description or None)
        target = writable[0]
        ek_event.setCalendar_(target)
        if event.all_day and event.start_date:
            ek_event.setAllDay_(True)
            ek_event.setStartDate_(foundation.NSDate.dateWithTimeIntervalSince1970_(datetime.fromisoformat(event.start_date).timestamp()))
            ek_event.setEndDate_(foundation.NSDate.dateWithTimeIntervalSince1970_((datetime.fromisoformat(event.end_date or event.start_date) + timedelta(days=1)).timestamp()))
        else:
            start = datetime.fromisoformat(str(event.start_utc).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(event.end_utc).replace("Z", "+00:00")) if event.end_utc else start
            ek_event.setStartDate_(foundation.NSDate.dateWithTimeIntervalSince1970_(start.timestamp()))
            ek_event.setEndDate_(foundation.NSDate.dateWithTimeIntervalSince1970_(end.timestamp()))
        self._store.save(ek_event)
        # Saved: the effect happened. EventKit assigns the identifier itself, so it is the
        # identity to retain and verify; nothing below may read as a refusal.
        try:
            uid = str(ek_event.eventIdentifier() or "")
        except Exception as exc:
            raise accepted_write_error("", exc, purpose="create") from exc
        if not uid:
            raise CalendarWriteAcceptedError(reason="accepted_without_identity", detail="EventKit saved the event but returned no identifier")
        try:
            return self.get_event(calendar_id, uid)
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="create") from exc

    def update_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        current = self.get_event(calendar_id, event.uid)
        seen_version = str(event.etag or "").strip()
        if seen_version and current.etag and seen_version != current.etag:
            raise CalendarRefusedError(
                412,
                reason="precondition_failed",
                detail="the event changed in the Mac event store after you last saw it (modification-date mismatch); nothing was overwritten",
            )
        ek_event = self._store.event_by_id(event.uid)
        foundation = self._foundation_module()
        ek_event.setTitle_(event.summary)
        if event.description:
            ek_event.setNotes_(event.description)
        if not event.all_day and event.start_utc:
            start = datetime.fromisoformat(event.start_utc.replace("Z", "+00:00"))
            end = datetime.fromisoformat(event.end_utc.replace("Z", "+00:00")) if event.end_utc else start
            ek_event.setStartDate_(foundation.NSDate.dateWithTimeIntervalSince1970_(start.timestamp()))
            ek_event.setEndDate_(foundation.NSDate.dateWithTimeIntervalSince1970_(end.timestamp()))
        self._store.save(ek_event)
        try:
            return self.get_event(calendar_id, event.uid)
        except Exception as exc:
            raise accepted_write_error(str(event.uid), exc, purpose="update") from exc

    def cancel_event(self, calendar_id: str, uid: str, etag: str) -> bool:
        current = self.get_event(calendar_id, uid)
        seen_version = str(etag or "").strip()
        if seen_version and current.etag and seen_version != current.etag:
            raise CalendarRefusedError(
                412,
                reason="precondition_failed",
                detail="the event changed in the Mac event store after you last saw it; nothing was cancelled",
            )
        ek_event = self._store.event_by_id(uid)
        self._store.remove(ek_event)
        try:
            self.get_event(calendar_id, uid)
        except CalendarRefusedError as exc:
            if exc.reason == "not_found":
                return True
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        raise CalendarRefusedError(409, reason="still_present", detail="the store still serves the event after removal")

    def durable_to_provider_id(self, durable_uid: str) -> str:
        """EventKit assigns ``eventIdentifier`` itself on save and does not store the client uid,
        so no durable mapping exists: identity is the identifier retained from the save."""
        return ""


def eventkit_availability() -> dict[str, str]:
    """The adapter's own packaging/permission state, stated for capability truth."""
    try:
        _load_eventkit()
    except CalendarRefusedError as exc:
        return {"state": "unavailable", "reason": exc.reason, "detail": exc.detail}
    return {"state": "binding_available", "reason": "", "detail": "permission is granted at runtime by the OS prompt / System Settings"}


__all__ = ["EventKitCalendarAdapter", "EventKitStore", "eventkit_availability"]
