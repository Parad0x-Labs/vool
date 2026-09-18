"""Disposable Google Calendar / Microsoft Graph API services for protocol-level evidence.

REAL HTTP servers speaking each provider's documented JSON API shape — calendarList /
calendarView, v3 events endpoints with etags and pageTokens, @odata.nextLink paging,
If-Match/412, Idempotency-Key, recurring-instance expansion — so the google and graph
adapters execute their actual request/response protocol through the actual VOOL
transport. NOT the live providers: no real Google/Microsoft account is contacted, and
green tests here prove the WIRE, not the live service (the live-connection gate is
stated separately in the delivery).

Failure injection is first-class: 401/403 (authorization), 429 (rate limit), stale
If-Match (412 — the server's own check), and write-then-hang (timeout after the
provider applied the change).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from ._caldav_service import ALLOCATED_PORT


class JsonCalendarState:
    """One provider-shaped store (google or graph) plus injection knobs."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect  # "google" | "graph"
        self.calendars: dict[str, dict] = {}  # id -> {name, events: uid -> event dict}
        self.lock = threading.Lock()
        self.require_bearer = ""
        self.rate_limit_after = 0
        self.hang_seconds = 0.0
        self.request_count = 0
        self.page_size = 2  # small pages on purpose: pagination is a proven behavior

    def add_calendar(self, cid: str, name: str) -> None:
        self.calendars[cid] = {"name": name, "events": {}, "listing": {}}

    def set_listing(self, cid: str, **fields) -> None:
        """Extra fields the calendar list states for one calendar (Google accessRole/primary, Graph canEdit/isDefaultCalendar)."""
        self.calendars[cid]["listing"] = dict(fields)

    def put_event(self, cid: str, event: dict, *, etag: str) -> None:
        self.calendars[cid]["events"][str(event["id"])] = {"event": dict(event), "etag": etag}

    def seed_event(self, cid: str, uid: str, *, summary: str, start: datetime, minutes: int, tz_name: str = "UTC", recurring_series: str = "", description: str = "") -> str:
        end = start + timedelta(minutes=minutes)  # stored as UTC; the wire renders it
        block = {
            "dateTime": start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "timeZone": tz_name or "UTC",
        }
        end_block = {
            "dateTime": end.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "timeZone": tz_name or "UTC",
        }
        event_id = uid if not recurring_series else f"{recurring_series}_{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
        if self.dialect == "google":
            event = {"id": event_id, "summary": summary, "start": block, "end": end_block, "etag": "seed"}
            if description:
                event["description"] = description
        else:
            event = {"id": event_id, "subject": summary, "start": block, "end": end_block, "body": {"contentType": "text", "content": description}}
        etag = f'"v{len(self.calendars[cid]["events"]) + 1}"'
        self.put_event(cid, event, etag=etag)
        return etag

    def render(self, uid: str, row: dict) -> dict:
        event = dict(row["event"])
        if self.dialect == "google":
            event["etag"] = row["etag"]
            event["status"] = "confirmed"
            event["htmlLink"] = f"https://fixture.test/events/{uid}"
        else:
            event["@odata.etag"] = row["etag"]
            event["webLink"] = f"https://fixture.test/events/{uid}"
        return event

    def snapshot(self) -> dict:
        with self.lock:
            return {
                cid: {uid: {"etag": row["etag"], "summary": row["event"].get("summary") or row["event"].get("subject") or "",
                           "event": row["event"], "invitation_request": row.get("invitation_request")} for uid, row in cal["events"].items()}
                for cid, cal in self.calendars.items()
            }


def _norm_etag(value: str) -> str:
    return str(value or "").strip().strip('"').lstrip("W/").strip('"')


def _parse_instant(block: dict) -> datetime:
    raw = str(block.get("dateTime") or "")
    if not raw and block.get("date"):
        return datetime.fromisoformat(str(block["date"])).replace(tzinfo=timezone.utc)
    instant = datetime.fromisoformat(raw)
    return instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)


class _JsonHandler(BaseHTTPRequestHandler):
    state: JsonCalendarState
    dialect: str

    def log_message(self, *args):
        pass

    # -- plumbing ------------------------------------------------------------------

    def _path_parts(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return unquote(parsed.path), parse_qs(parsed.query)

    def _check_access(self) -> bool:
        self.state.request_count += 1
        if self.state.rate_limit_after and self.state.request_count > self.state.rate_limit_after:
            self._reply(429, {"error": {"code": 429, "message": "rate limited"}}, headers={"Retry-After": "30"})
            return False
        expected = self.state.require_bearer
        if expected:
            auth = self.headers.get("Authorization", "")
            if not auth:
                self._reply(401, {"error": {"code": 401, "message": "credential required"}}, headers={"WWW-Authenticate": "Bearer realm=\"fixture\""})
                return False
            if auth != f"Bearer {expected}":
                self._reply(403, {"error": {"code": 403, "message": "credential refused"}})
                return False
        return True

    def _reply(self, status: int, body: dict, headers: dict | None = None):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _find_calendar(self, cid: str) -> dict | None:
        return self.state.calendars.get(cid)

    # -- routes --------------------------------------------------------------------

    def do_GET(self):
        if not self._check_access():
            return
        path, query = self._path_parts()
        state = self.state
        if state.dialect == "google":
            if path.endswith("/users/me/calendarList"):
                items = [{"id": cid, "summary": cal["name"], **cal.get("listing", {})} for cid, cal in state.calendars.items()]
                return self._reply(200, {"items": items})
            if "/events/" in path:
                cid = path.split("/calendars/")[1].split("/events/")[0]
                uid = path.rsplit("/events/", 1)[1]
                cal = self._find_calendar(cid)
                row = (cal or {}).get("events", {}).get(uid)
                if row is None:
                    return self._reply(404, {"error": {"code": 404, "message": "no such event"}})
                return self._reply(200, state.render(uid, row))
            if path.endswith("/events") or path.endswith("/events/"):
                cid = path.split("/calendars/")[1].split("/events")[0]
                cal = self._find_calendar(cid)
                if cal is None:
                    return self._reply(404, {"error": {"code": 404, "message": "no such calendar"}})
                tmin = datetime.fromisoformat(query["timeMin"][0].replace("Z", "+00:00"))
                tmax = datetime.fromisoformat(query["timeMax"][0].replace("Z", "+00:00"))
                rows = [
                    (uid, row) for uid, row in cal["events"].items()
                    if _parse_instant(row["event"]["start"]) < tmax and tmin < _parse_instant(row["event"].get("end") or row["event"]["start"])
                ]
                rows.sort(key=lambda pair: _parse_instant(pair[1]["event"]["start"]))
                size = state.page_size
                page_token = query.get("pageToken", [""])[0]
                start_index = 0 if not page_token else int(page_token) if page_token.isdigit() else 0
                page = rows[start_index:start_index + size]
                body = {"items": [state.render(uid, row) for uid, row in page]}
                if start_index + size < len(rows):
                    body["nextPageToken"] = str(start_index + size)
                return self._reply(200, body)
        else:
            if path.endswith("/me/calendars"):
                return self._reply(200, {"value": [{"id": cid, "name": cal["name"], **cal.get("listing", {})} for cid, cal in state.calendars.items()]})
            if "/calendarview" in path:
                cid = path.split("/calendars/")[1].split("/calendarview")[0]
                cal = self._find_calendar(cid)
                if cal is None:
                    return self._reply(404, {"error": {"code": 404, "message": "no such calendar"}})
                tmin = datetime.fromisoformat(query["startDateTime"][0].replace("Z", "+00:00"))
                tmax = datetime.fromisoformat(query["endDateTime"][0].replace("Z", "+00:00"))
                rows = [
                    (uid, row) for uid, row in cal["events"].items()
                    if _parse_instant(row["event"]["start"]) < tmax and tmin < _parse_instant(row["event"].get("end") or row["event"]["start"])
                ]
                rows.sort(key=lambda pair: _parse_instant(pair[1]["event"]["start"]))
                size = state.page_size
                skip = int(query.get("$skiptoken", ["0"])[0] or 0)
                page = rows[skip:skip + size]
                body = {"value": [state.render(uid, row) for uid, row in page]}
                if skip + size < len(rows):
                    # Graph serves the next page as an ABSOLUTE URL with its query encoded; a relative link or a raw
                    # "+00:00" offset is not what the provider sends (the adapter refuses to follow an off-origin link).
                    next_query = urlencode({"$skiptoken": skip + size, "startDateTime": query["startDateTime"][0], "endDateTime": query["endDateTime"][0]})
                    body["@odata.nextLink"] = f"http://{self.headers.get('Host')}{quote(path, safe='/=-_:')}?{next_query}"
                return self._reply(200, body)
            if "/events/" in path:
                uid = path.rsplit("/events/", 1)[1]
                for cal in state.calendars.values():
                    row = cal["events"].get(uid)
                    if row is not None:
                        return self._reply(200, state.render(uid, row))
                return self._reply(404, {"error": {"code": 404, "message": "no such event"}})
        self._reply(404, {"error": {"code": 404, "message": f"no route {path}"}})

    def do_POST(self):
        if not self._check_access():
            return
        path, query = self._path_parts()
        state = self.state
        body = self._read_body()
        if state.dialect == "google":
            if "/events" in path and path.count("/") >= 3:
                cid = path.split("/calendars/")[1].split("/events")[0]
                cal = self._find_calendar(cid)
                if cal is None:
                    return self._reply(404, {"error": {"code": 404, "message": "no such calendar"}})
                # Google's guest-notification contract: sendUpdates none (default) stores
                # attendees silently; 'all' is the provider's accepted REQUEST to email each
                # guest. The fixture models the POLICY -- accepted requests are recorded, and
                # no email is ever claimed as delivered here.
                send_updates = query.get("sendUpdates", ["none"])[0]
                invitation_request = None
                if send_updates == "all" and body.get("attendees"):
                    invitation_request = {
                        "policy": "all",
                        "requested_for": [entry.get("email") for entry in body["attendees"] if isinstance(entry, dict)],
                        "state": "accepted",
                    }
                uid = str(body.get("id") or f"fixture-{state.request_count:06d}")
                idem = self.headers.get("Idempotency-Key", "")
                if idem and any(row.get("idempotency") == idem for row in cal["events"].values()):
                    return self._reply(200, state.render(uid, cal["events"].get(uid) or {"event": body, "etag": '"v-dup"'}))
                etag = f'"v{len(cal["events"]) + 1}"'
                stored = dict(body)
                stored["id"] = uid
                cal["events"][uid] = {"event": stored, "etag": etag, "idempotency": idem}
                if invitation_request is not None:
                    cal["events"][uid]["invitation_request"] = invitation_request
                if state.hang_seconds > 0:
                    import time

                    time.sleep(state.hang_seconds)
                return self._reply(200, state.render(uid, cal["events"][uid]))
        else:
            if path.endswith("/events"):
                cid = path.split("/calendars/")[1].split("/events")[0]
                cal = self._find_calendar(cid)
                if cal is None:
                    return self._reply(404, {"error": {"code": 404, "message": "no such calendar"}})
                uid = str(body.get("id") or f"fixture-graph-{state.request_count:06d}")
                etag = f'W/"v{len(cal["events"]) + 1}"'
                stored = dict(body)
                stored["id"] = uid
                cal["events"][uid] = {"event": stored, "etag": etag}
                if state.hang_seconds > 0:
                    import time

                    time.sleep(state.hang_seconds)
                return self._reply(201, state.render(uid, cal["events"][uid]))
        self._reply(404, {"error": {"code": 404, "message": f"no route {path}"}})

    def do_PATCH(self):
        if not self._check_access():
            return
        path, _query = self._path_parts()
        state = self.state
        patch = self._read_body()
        uid = path.rsplit("/events/", 1)[1]
        for cal in state.calendars.values():
            row = cal["events"].get(uid)
            if row is not None:
                if_match = self.headers.get("If-Match", "")
                if if_match and _norm_etag(if_match) != _norm_etag(row["etag"]):
                    return self._reply(412, {"error": {"code": 412, "message": "etag mismatch"}})
                row["event"].update({k: v for k, v in patch.items() if k in {"summary", "subject", "start", "end", "description", "body"}})
                row["etag"] = f'"v{len(row["etag"]) + 4}"' if state.dialect == "google" else f'W/"v{len(row["etag"]) + 4}"'
                if state.hang_seconds > 0:
                    import time

                    time.sleep(state.hang_seconds)
                return self._reply(200, state.render(uid, row))
        self._reply(404, {"error": {"code": 404, "message": "no such event"}})

    def do_DELETE(self):
        if not self._check_access():
            return
        path, _query = self._path_parts()
        state = self.state
        uid = path.rsplit("/events/", 1)[1]
        for cal in state.calendars.values():
            row = cal["events"].pop(uid, None)
            if row is not None:
                if_match = self.headers.get("If-Match", "")
                if if_match and _norm_etag(if_match) != _norm_etag(row["etag"]):
                    cal["events"][uid] = row  # restore: not deleted
                    return self._reply(412, {"error": {"code": 412, "message": "etag mismatch"}})
                if state.hang_seconds > 0:
                    import time

                    time.sleep(state.hang_seconds)
                return self._reply(204, {})
        self._reply(404, {"error": {"code": 404, "message": "no such event"}})


def start_json_calendar_fixture(*, dialect: str, calendars: dict[str, str] | None = None, preferred_port: int = 0):
    """Start one provider-shaped disposable service. Returns (server, state, base_url)."""
    state = JsonCalendarState(dialect)
    for cid, name in (calendars or {}).items():
        state.add_calendar(cid, name)

    # Per-SERVER binding: two fixtures of different dialects must not overwrite each
    # # other (a class attribute would make the first server speak the second dialect).
    handler_class = type(
        f"_JsonHandler_{dialect}",
        (_JsonHandler,),
        {"state": state, "dialect": dialect},
    )
    server = ThreadingHTTPServer(("127.0.0.1", preferred_port or 0), handler_class)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state, f"http://127.0.0.1:{server.server_address[1]}/"


__all__ = ["ALLOCATED_PORT", "JsonCalendarState", "start_json_calendar_fixture"]
