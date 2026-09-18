"""pa_beta_gate -- revision-6: an event the operator cannot place stops every re-check before a write.

The adapters report some events they read but cannot place on the timeline themselves (a CalDAV floating
time has no zone; the operator decides). Before revision 6 the conflict checks at send time, at a refire
after proven absence and at an update's destination either skipped such an event or raised an untyped
comparison error outside the read's handling. These NOVEL cases put one such event in each re-check and
count the writes the provider received.

LABELLED: a synthetic CalDAV wire behind the real CalDAV adapter, the update lifecycle's calendar double,
and the real SQLite approval store; no account or network.
"""
from __future__ import annotations

import re
from dataclasses import replace

from core.kas.adapters.caldav import CalDavCalendarAdapter
from core.kas.contract import AdapterConfig, KasResponse, TransportUnknownError
from tests.pa_beta_gate import test_v5_calendar_update_cancel_lifecycle as lifecycle
from tests.pa_beta_gate.test_v5_calendar_effect_phase import _stage, _stored
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import _execute
from tests.pa_beta_gate.test_v6_typed_calendar_reads import _multistatus, _row, _vcal

_FLOATING_HOLD = _vcal("UID:floating-hold", "SUMMARY:Floating hold", "DTSTART:20260915T120000", "DTEND:20260915T130000")


class CalDavWire:
    """LABELLED synthetic CalDAV server: PUT stores an object; a UID query answers with that object and a
    range query with every stored object plus ``extra`` objects."""

    def __init__(self, *extra):
        self.objects: dict[str, str] = {}
        self.extra = list(extra)
        self.puts: list[str] = []
        self.lose_next_put = False

    def __call__(self, req):
        body = (req.body or b"").decode("utf-8")
        if req.method == "PUT":
            uid = re.search(r"(?m)^UID:(.+?)\r?$", body).group(1)
            self.puts.append(uid)
            if self.lose_next_put:
                self.lose_next_put = False
                raise TransportUnknownError("connection_reset_before_acceptance")
            self.objects[uid] = body
            return KasResponse(status=201)
        wanted = re.search(r"<C:text-match[^>]*>([^<]+)</C:text-match>", body)
        if wanted:
            rows = [(uid, ics) for uid, ics in self.objects.items() if uid == wanted.group(1)]
        else:
            rows = [*self.objects.items(), *((f"extra-{index}", ics) for index, ics in enumerate(self.extra))]
        return _multistatus(*(_row(uid, ics) for uid, ics in rows))


def _caldav(wire, config):
    return CalDavCalendarAdapter(transport=wire, config=AdapterConfig(provider_id="caldav", base_url=config.base_url))


def test_send_time_recheck_that_cannot_place_an_event_sends_nothing_and_stays_approvable(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="caldav", title="Novel pressure relief test")
    wire = CalDavWire(_FLOATING_HOLD)
    result = _execute(path, aid, config, _caldav(wire, config))
    assert result.status == "provider_refused" and result.details["reason"] == "unreadable_response", result.response_text
    assert wire.puts == [] and _stored(path, aid)[0] == "pending_approval"


def test_refire_after_proven_absence_that_cannot_place_an_event_sends_nothing(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="caldav", title="Novel backflow valve test")
    wire = CalDavWire()
    wire.lose_next_put = True
    assert _execute(path, aid, config, _caldav(wire, config)).status == "outcome_unproven"

    wire.extra.append(_FLOATING_HOLD)
    held = _execute(path, aid, config, _caldav(wire, config))
    assert not held.ok and "could not be re-checked" in held.response_text, held.response_text
    assert wire.puts == [scope["intent_uid"]] and _stored(path, aid)[0] == "outcome_unproven"

    wire.extra.clear()
    sent = _execute(path, aid, config, _caldav(wire, config))
    assert sent.ok and wire.puts == [scope["intent_uid"]] * 2 and _stored(path, aid)[0] == "executed", sent.response_text


def test_update_destination_recheck_that_cannot_place_an_event_moves_nothing(tmp_path):
    event = lifecycle._event()
    floating = replace(lifecycle._event(uid="floating-hold", title="Floating hold", start="2026-09-16T09:00:00", end="2026-09-16T10:00:00"), tz_name="")
    calendar = lifecycle.Calendar(event, floating)
    kind = "provider_calendar_update"
    path, aid = lifecycle._stage(tmp_path, kind, lifecycle._update_scope(event))
    result = lifecycle._approve(path, kind, aid, calendar)
    assert result.status == "provider_unreachable" and "unreadable_response" in result.response_text, result.response_text
    assert calendar.calls == [] and lifecycle._row(path, kind, aid)["status"] == "pending_approval"
