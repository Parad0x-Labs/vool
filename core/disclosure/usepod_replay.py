"""Content-free privacy lineage for an already reviewed UsePod envelope.

This is a deny-only check. The existing client still owns every quote, claim,
proof, attempt bound and settlement; a refusal never releases a paid hold.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from core.disclosure.network import UsePodConsent
from core.disclosure.wire import DisclosureRefused, WirePayload

_SCHEMA = "vool.disclosure.usepod-replay.v2"


def _home_hash() -> str:
    from core.runtime_paths import active_vool_home

    return hashlib.sha256(str(active_vool_home().resolve()).encode()).hexdigest()


def _epoch(kind: str, chat: str, project: str) -> str:
    if kind == "chat":
        from core.disclosure.scope import current_version

        value = current_version(chat, project)
        if value is None:
            raise DisclosureRefused("The original chat's privacy could not be verified.")
        return value
    if kind != "home":
        raise DisclosureRefused("The original privacy scope is unavailable.")
    from core.memory.revocation import generation, memory_home_is_new, serialized, snapshot

    with serialized():
        # The canonical read distinguishes a genuinely new home from a lost
        # authority. No ledger is created or repaired by this check.
        if memory_home_is_new():
            value = "new-memory-home"
        else:
            snapshot()
            value = generation()
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class ReplayBinding:
    body_sha256: str
    envelope_sha256: str
    origin: str
    path: str
    protocol: str
    model: str
    operation: str
    home_sha256: str
    scope_kind: str
    chat: str
    project: str
    epoch: str
    rules_revision: int
    route_sha256: str
    live_scope_required: bool
    dispatch_check: Callable[[], None] | None = field(default=None, repr=False, compare=False, metadata={"record": False})

    @classmethod
    def capture(cls, wire: WirePayload, envelope):
        from core.disclosure.wire import get_preview

        if not isinstance(wire, WirePayload) or not wire._wire_consumed:
            raise DisclosureRefused("The original request was not reviewed for dispatch.")
        item = get_preview(wire.preview_id)
        if (item.decision != {"action": "send"} or item.body != envelope.body
                or wire.destination != envelope.origin + envelope.path_template
                or not isinstance(wire.network_consent, UsePodConsent)):
            raise DisclosureRefused("The original request changed before dispatch.")
        kind = "chat" if item.scope_version is not None else "home"
        chat = item.chat_id if kind == "chat" else ""
        epoch = _epoch(kind, chat, item.project_id)
        if kind == "chat" and epoch != item.scope_version:
            raise DisclosureRefused("The original chat changed before dispatch.")
        def check_live_turn():
            import threading
            if item.invalidated or (isinstance(item.cancel_event, threading.Event) and item.cancel_event.is_set()):
                raise DisclosureRefused("This turn was stopped. Nothing was resent.")
            if item.council_scope is not None:
                item.council_scope.verify()

        return cls(hashlib.sha256(envelope.body).hexdigest(), envelope.binding_sha256,
                   envelope.origin, envelope.path_template, str(envelope.protocol), envelope.model_id,
                   envelope.operation_id, _home_hash(), kind, chat, item.project_id, epoch,
                   item.rule_revision, wire.network_consent.route_sha, item.council_scope is not None, check_live_turn)

    def public_record(self) -> dict:
        return {"schema": _SCHEMA, **{name: getattr(self, name) for name, value in self.__dataclass_fields__.items()
                                     if value.metadata.get("record", True)}}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or record.get("schema") != _SCHEMA:
            raise DisclosureRefused("This recovery request has no verified disclosure lineage.")
        fields = {name: record.get(name) for name, value in cls.__dataclass_fields__.items() if value.metadata.get("record", True)}
        if (type(fields["rules_revision"]) is not int or fields["rules_revision"] < 0
                or type(fields["live_scope_required"]) is not bool
                or any(not isinstance(value, str) for name, value in fields.items() if name not in {"rules_revision", "live_scope_required"})
                or any(not re.fullmatch(r"[0-9a-f]{64}", fields[name])
                       for name in ("body_sha256", "envelope_sha256", "home_sha256", "epoch", "route_sha256"))):
            raise DisclosureRefused("This recovery request's privacy record is unreadable.")
        return cls(**fields)

    def verify(self, envelope) -> None:
        from core.auto_local_only_mode import turn_is_local_only
        from core.disclosure.rules import read_rules
        from core.remote_fetch_policy import remote_fetch_forbidden

        if self.live_scope_required and self.dispatch_check is None:
            raise DisclosureRefused("This Council recovery needs a new live review. Nothing was resent.")
        if self.dispatch_check is not None:
            self.dispatch_check()
        if (self.home_sha256 != _home_hash()
                or self.body_sha256 != hashlib.sha256(envelope.body).hexdigest()
                or self.envelope_sha256 != envelope.binding_sha256
                or (self.origin, self.path, self.protocol, self.model, self.operation)
                != (envelope.origin, envelope.path_template, str(envelope.protocol),
                    envelope.model_id, envelope.operation_id)):
            raise DisclosureRefused("The recovery request changed. Nothing was resent.")
        if (turn_is_local_only(None) or remote_fetch_forbidden()
                or _epoch(self.scope_kind, self.chat, self.project) != self.epoch
                or read_rules(self.project)["revision"] != self.rules_revision
                or not UsePodConsent(self.origin, self.model, self.route_sha256).allowed(
                    self.origin + self.path, envelope.payload())):
            raise DisclosureRefused("Privacy or route choices changed. Nothing was resent.")


class ReplayCheckedTransport:
    """Keep the canonical transport, checking privacy before each physical POST."""

    def __init__(self, transport, binding: ReplayBinding):
        self._transport = transport
        self._binding = binding

    def __getattr__(self, name):
        return getattr(self._transport, name)

    def post_envelope(self, envelope, *, origin, **kwargs):
        from core.usepod.transport import DISPATCH_NOT_SENT, UsePodTransportError

        try:
            if origin != self._binding.origin:
                raise DisclosureRefused("The recovery destination changed.")
            self._binding.verify(envelope)
        except (DisclosureRefused, RuntimeError, ValueError, OSError, sqlite3.Error):
            # The canonical paid-retry handler retains its existing unknown
            # liability on a transport refusal, including one before this POST.
            raise UsePodTransportError("disclosure_replay_refused", dispatch_state=DISPATCH_NOT_SENT) from None
        return self._transport.post_envelope(envelope, origin=origin, **kwargs)
