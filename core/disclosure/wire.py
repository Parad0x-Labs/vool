"""Owner-reviewed, ephemeral disclosure at the serialized provider boundary.

No plaintext snapshot or reverse mapping is persisted. The existing invocation
gateway still owns provider manifests and budget admission. This authority runs
before that gateway reserves/claims a call, and transports consume its exact bytes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class DisclosureRefused(RuntimeError):
    pass


class DisclosureCancelled(DisclosureRefused):
    pass


class LocalModelRequested(DisclosureCancelled):
    pass


class DisclosurePreviewNeeded(DisclosureCancelled):
    """The request needs the owner's review, and this caller cannot show it.

    Raised at once instead of waiting: nothing was sent, and the preview stays
    open on the chat page, where the owner can approve exactly these bytes.
    """

    def __init__(self, preview: dict[str, Any]) -> None:
        self.preview = preview
        super().__init__(PREVIEW_NEEDED_DETAIL)


#: What a caller that cannot show previews tells the person.
PREVIEW_NEEDED_DETAIL = (
    "This request needs your review before it goes to a cloud model. Open this chat in VOOL to see "
    "exactly what would be sent and approve it, then send your message again. Nothing was sent."
)

#: A served turn's declaration that its caller shows previews and answers them while the turn
#: waits (the chat page). Any other caller gets DisclosurePreviewNeeded at once.
REVIEW_WAITS_KEY = "disclosure_review"
#: The served door's holder for previews a turn could not show; read back for the typed result.
PENDING_HOLDER_KEY = "_disclosure_pending"
#: A caller that must tell "this boundary stopped the call" from a provider failure puts a dict in its
#: request metadata under this key; a refusal fills in its kind and message. The router copies
#: metadata per call, so the caller's own dict is how the answer comes back (core.clean_loop.brain).
STOP_NOTE_KEY = "_disclosure_stop_note"
#: Set by a caller whose request carries what it read on the owner's computer in plain messages
#: (the clean loop's tool results): such a request carries the owner's files.
CARRIES_FILES_KEY = "_disclosure_carries_files"


def encode_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def encode_ascii_body(payload: dict[str, Any]) -> bytes:
    """Use the existing prepaid envelope's exact ASCII JSON codec."""
    return json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def _home() -> str:
    from core.runtime_paths import active_vool_home

    return str(active_vool_home())


@dataclass
class Preview:
    home: str
    chat_id: str
    turn_id: str
    provider: str
    url: str
    body: bytes = field(repr=False)
    attachments: tuple[dict[str, Any], ...] = ()
    preview_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    decision: dict[str, Any] | None = None
    sent: bool = False
    omitted: int = 0
    project_id: str = ""
    rule_revision: int = 0
    scope_version: str | None = field(default=None, repr=False)
    cancel_event: Any = field(default=None, repr=False)
    ready: bool = False
    request_format: str = "openai"
    provider_call_id: str = ""
    timing_recorded: bool = False
    output_text: str = field(default="", repr=False)
    usd_report_complete: bool | None = None
    replay_context: dict[str, Any] = field(default_factory=dict, repr=False)
    restore_map: Any = field(default=None, repr=False)
    timing: Any = field(default=None, repr=False)
    replay: Any = field(default=None, repr=False)
    privacy_scope: Any = field(default=None, repr=False)
    local_context: dict[str, Any] = field(default_factory=dict, repr=False)
    invalidated: bool = False
    can_transform: bool = True
    council_scope: Any = field(default=None, repr=False)
    detached: bool = False
    has_files: bool = False
    binding: Any = field(default=None, repr=False)
    event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def public(self) -> dict[str, Any]:
        return {"id": self.preview_id, "chat_id": self.chat_id, "turn_id": self.turn_id,
                "provider": self.provider, "destination": self.url, "sha256": self.digest,
                "body": self.body.decode("utf-8"), "attachments": list(self.attachments),
                "omitted_sections": self.omitted, "project_id": self.project_id,
                "can_transform": self.can_transform,
                "council_run_id": self.council_scope.run_id if self.council_scope is not None else "",
                "can_compare": self.ready and self.sent, "detached": self.detached,
                "state": "sent" if self.sent else "waiting" if self.decision is None else "decided"}

    def pending(self) -> dict[str, Any]:
        """The typed pending entry a caller that cannot show previews receives."""
        from urllib.parse import urlsplit

        return {"type": "disclosure_preview", "preview_id": self.preview_id, "chat_id": self.chat_id,
                "provider": self.provider, "destination_host": urlsplit(self.url).hostname or "",
                "attachments": list(self.attachments), "detail": PREVIEW_NEEDED_DETAIL,
                "approve_at": "chat_page"}


_LOCK = threading.RLock()
_PREVIEWS: dict[str, Preview] = {}
_APPROVED: dict[tuple[str, str, str], set[tuple[str, str, int, str | None, str]]] = {}
_REVIEWED: set[tuple[str, str, str]] = set()
_CANCELLED: set[tuple[str, str, str]] = set()
_FILES_APPROVED: set[tuple[str, str]] = set()
#: Exact bodies the owner approved on the chat page for a caller that could not wait:
#: (home, chat, binding) -> approval time. Only the same bytes, to the same destination,
#: under the same rules and erasure state, may go out on a later send.
_DETACHED_APPROVED: dict[tuple[Any, ...], float] = {}
_TTL = 300.0
_LIMIT = 128


def _prune() -> None:
    now = time.monotonic()
    for ident, item in list(_PREVIEWS.items()):
        if now - item.created > _TTL:
            _invalidate(item)
            del _PREVIEWS[ident]
    live = {(p.home, p.chat_id, p.turn_id) for p in _PREVIEWS.values()}
    for key in set(_APPROVED) - live:
        del _APPROVED[key]
    _CANCELLED.intersection_update(live)
    _REVIEWED.intersection_update(live)
    chats = {(p.home, p.chat_id) for p in _PREVIEWS.values()}
    _FILES_APPROVED.intersection_update(chats)
    for approved, at in list(_DETACHED_APPROVED.items()):
        if now - at > _TTL:
            del _DETACHED_APPROVED[approved]


def _current(item: Preview) -> bool:
    if item.invalidated:
        return False
    if item.replay is not None:
        try:
            item.replay.source()
        except (KeyError, ValueError, RuntimeError):
            return False
    if item.scope_version is None:
        return True
    from core.disclosure.scope import current_version

    try:
        return current_version(item.chat_id, item.project_id) == item.scope_version
    except (RuntimeError, ValueError, OSError, sqlite3.Error):
        return False


def _invalidate(item: Preview) -> None:
    with _LOCK:
        key = (item.home, item.chat_id, item.turn_id)
        _CANCELLED.add(key)
        _FILES_APPROVED.discard(key[:2])
        item.invalidated = True
        if item.council_scope is not None:
            item.council_scope.clear_sources()
            item.council_scope = None
        # Keep only an expiring cancellation tombstone so an in-flight retry
        # cannot reopen the erased turn. No request text remains in the cache.
        item.output_text = ""
        item.ready = False
        item.restore_map = None
        item.privacy_scope = None
        item.replay_context.clear()
        item.local_context.clear()
        from core.model_compare.service import invalidate_source
        invalidate_source(item.preview_id)
        item.body = b""
        item.attachments = ()
        item.url = ""
        item.provider = ""
        item.decision = {"action": "cancel"}
        item.event.set()
        if isinstance(item.cancel_event, threading.Event):
            item.cancel_event.set()


def previews(*, chat_id: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        _prune()
        items = [p for p in _PREVIEWS.values()
                 if p.home == _home() and (chat_id is None or p.chat_id == chat_id)]
    result = []
    # The erasure fence is read outside the preview mutex: canonical deletion
    # can close a chat while holding its own writer fence, without inversion.
    for item in items:
        if _current(item):
            result.append(item.public())
        else:
            _invalidate(item)
    return result


def get_preview(preview_id: str) -> Preview:
    with _LOCK:
        _prune()
        item = _PREVIEWS.get(preview_id)
    if item is None or item.home != _home():
        raise KeyError("preview expired")
    if not _current(item):
        _invalidate(item)
        raise ValueError("This chat's privacy state changed. Nothing was sent.")
    return item


def decide(preview_id: str, digest: str, action: str, *, indices: list[int] | None = None,
           sections: list[str] | None = None) -> None:
    if action not in {"send", "cancel", "local", "parts", "replace"}:
        raise ValueError("unknown choice")
    with _LOCK:
        _prune()
        item = _PREVIEWS.get(preview_id)
        if item is None or item.home != _home():
            raise KeyError("preview expired")
        if item.digest != digest or item.decision is not None or item.sent:
            raise ValueError("request changed; review it again")
        if action in {"parts", "replace"} and not item.can_transform:
            raise ValueError("This internal request can be sent, stopped, or retried locally.")
        if item.detached:
            # No turn is waiting on this preview. Sending approves exactly these bytes for the
            # owner's next send of the same request; nothing goes out now.
            if action not in {"send", "cancel"}:
                raise ValueError("This request can be approved or stopped here. Send the message again to change it.")
            if action == "send":
                _DETACHED_APPROVED[(item.home, item.chat_id, item.binding)] = time.monotonic()
                if item.has_files:
                    _FILES_APPROVED.add((item.home, item.chat_id))
        if action == "parts":
            if sections is not None:
                if not isinstance(sections, list) or any(not isinstance(s, str) for s in sections):
                    raise ValueError("choose message sections")
            elif not isinstance(indices, list) or any(type(n) is not int for n in indices):
                raise ValueError("choose message sections")
        item.decision = {"action": action, "indices": indices, "sections": sections}
        item.event.set()


def close_chat(chat_id: str) -> None:
    with _LOCK:
        for ident, p in list(_PREVIEWS.items()):
            if p.home == _home() and p.chat_id == chat_id:
                _invalidate(p)
                p.decision = {"action": "cancel"}
                p.event.set()
                if isinstance(p.cancel_event, threading.Event):
                    p.cancel_event.set()
                del _PREVIEWS[ident]
        key = (_home(), chat_id)
        _FILES_APPROVED.discard(key)
        for approved in list(_DETACHED_APPROVED):
            if approved[:2] == key:
                del _DETACHED_APPROVED[approved]
        for turn in list(_APPROVED):
            if turn[:2] == key:
                del _APPROVED[turn]
        _CANCELLED.difference_update(t for t in list(_CANCELLED) if t[:2] == key)
        _REVIEWED.difference_update(t for t in list(_REVIEWED) if t[:2] == key)


class WirePayload(dict):
    """A normal JSON object for adapter compatibility plus a bound byte envelope."""

    def __init__(self, payload: dict[str, Any], body: bytes, url: str, preview_id: str = "", *, encoder=encode_body, control_only: bool = False, cloud_destination: bool | None = None) -> None:
        super().__init__(payload)
        self.wire_body = body
        self.encoder = encoder
        self.control_only = control_only
        self.cloud_destination = cloud_destination
        self.destination = url
        self.preview_id = preview_id
        self._wire_consumed = False
        self.timing = None
        #: The model call id of the owner's own paid pick, when the router reserved this call for it.
        self.owner_paid_call = ""

    def consume_wire(self, url: str) -> bytes:
        from core import auto_local_only_mode
        from core.cloud_transport import _operator_cloud_network_allowed
        from core.remote_fetch_policy import remote_fetch_forbidden

        with _LOCK:
            candidate = _PREVIEWS.get(self.preview_id) if self.preview_id else None
        if candidate is not None and not _current(candidate):
            _invalidate(candidate)
            raise DisclosureRefused("This chat's privacy state changed. Nothing was sent.")
        if candidate is not None and isinstance(candidate.cancel_event, threading.Event) and candidate.cancel_event.is_set():
            raise DisclosureCancelled("This turn was stopped. Nothing was sent.")
        if candidate is not None and candidate.council_scope is not None:
            candidate.council_scope.verify()
        # Review can take minutes. Re-read the existing operator authorities at
        # dispatch, including cloud manifests served on a loopback endpoint.
        if self.cloud_destination is True or not auto_local_only_mode.endpoint_is_local(url):
            # The owner's explicit pick of a paid model is its own authority, exactly as the router
            # honors it with cloud help off: a reservation the server made for THIS call, still
            # standing at dispatch. Local-only and the turn's veto below still refuse it.
            network_allowed = (_operator_cloud_network_allowed()
                               or _paid_call_still_reserved(self.owner_paid_call, model=str(self.get("model") or "")))
            if (remote_fetch_forbidden() or auto_local_only_mode.turn_is_local_only(None)
                    or not network_allowed):
                raise DisclosureRefused("Cloud models are off for this turn. Nothing was sent.")
        with _LOCK:
            if self._wire_consumed or self.destination != url or self.encoder(self) != self.wire_body:
                raise DisclosureRefused("The request changed. Review the new request before sending.")
            if self.preview_id:
                _prune()
                item = _PREVIEWS.get(self.preview_id)
                if (item is None or item.home != _home() or item.body != self.wire_body
                        or item.url != url or item.decision != {"action": "send"}):
                    raise DisclosureRefused("This preview expired or changed. Nothing was sent.")
            else:
                from core.auto_local_only_mode import endpoint_is_local

                if not endpoint_is_local(url) and not self.control_only:
                    raise DisclosureRefused("Cloud model requests need a disclosure receipt.")
            if self.preview_id and item.project_id:
                from core.disclosure.rules import read_rules
                if read_rules(item.project_id)["revision"] != item.rule_revision:
                    raise DisclosureRefused("Project rules changed. Review the request again. Nothing was sent.")
            if self.timing is not None:
                self.timing.dispatch()
            self._wire_consumed = True
            return self.wire_body


def _select_sections(body: dict[str, Any], sections: list[str]) -> list[dict[str, Any]]:
    selected = set(sections)
    allowed: set[str] = set()
    messages = body.get("messages", [])
    result = []
    for index, message in enumerate(messages):
        whole = f"m:{index}"
        allowed.add(whole)
        content = message.get("content")
        images = message.get("images")
        if isinstance(images, list):
            allowed.add(f"t:{index}")
            allowed.update(f"i:{index}:{n}" for n in range(len(images)))
        if isinstance(content, list):
            allowed.update(f"b:{index}:{n}" for n in range(len(content)))
        if whole in selected:
            result.append(message)
        else:
            kept = ([block for n, block in enumerate(content) if f"b:{index}:{n}" in selected]
                    if isinstance(content, list) else content if f"t:{index}" in selected else None)
            kept_images = [image for n, image in enumerate(images or []) if f"i:{index}:{n}" in selected] if isinstance(images, list) else []
            if kept or kept_images:
                narrowed = {**message, "content": kept or ""}
                narrowed.pop("images", None)
                if kept_images:
                    narrowed["images"] = kept_images
                result.append(narrowed)
    if selected - allowed:
        raise DisclosureRefused("The selected sections are unavailable. Nothing was sent.")
    return result


def _carries_files_or_memory(sources: list[Any], chat_id: str) -> bool:
    """Whether the assembled context holds files or remembered information.

    The conversation itself does not: this chat's dialogue (including the
    pages its own earlier turns admitted), and the instructions VOOL builds from
    runtime policy and the current turn without stored memory (its identity and
    tone, mode and safety rules, the stated memory scope, the task class).
    Everything else does, including tool observations, a page remembered from
    another chat, and any source type added later.
    """
    from core.tiered_context_loader import _NON_MEMORY_DELIVERY_SOURCE_TYPES

    conversation = {
        "dialogue_turn", "recent_dialogue", "session_state", "dialogue_continuity", "active_mission",
        *(_NON_MEMORY_DELIVERY_SOURCE_TYPES - {"tool_observation"}),
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        kind = str(source.get("source_type") or "")
        if kind in conversation:
            continue
        origin = str(dict(source.get("metadata") or {}).get("memory_origin_chat_id") or "")
        if kind == "turn_context_page" and chat_id and origin == chat_id:
            continue
        return True
    return False


def request_context(request: Any, *, provider: str, cloud: bool) -> dict[str, Any]:
    from core.remote_fetch_policy import current_remote_fetch_context

    bound = current_remote_fetch_context()
    from core.council.disclosure import CouncilDisclosureScope
    council_scope = bound.get("_council_disclosure_scope")
    if not isinstance(council_scope, CouncilDisclosureScope):
        council_scope = None
    if council_scope is not None:
        council_scope.verify()
    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, dict):
        raise DisclosureRefused("The request identity is unavailable. Nothing was sent.")
    context = getattr(request, "context", None) or metadata.get("context_manifest") or {}
    if not metadata.get("_disclosure_internal_turn"):
        metadata["_disclosure_internal_turn"] = uuid.uuid4().hex
    sources = list(context.get("items_included") or [])
    types = {str(s.get("source_type") or "") for s in sources if isinstance(s, dict)}
    from core.disclosure.sources import SourceCatalog

    catalog = bound.get("_disclosure_sources")
    if metadata.get("_disclosure_control_only") is True:
        catalog = None
    ingress_files = isinstance(catalog, SourceCatalog) and (catalog.has_attachments or any(kind != "conversation" for _, kind, _ in catalog.entries))
    chat_id = council_scope.chat_id if council_scope else str(bound.get("chat_id") or context.get("chat_id") or metadata.get("session_id") or "")
    file_content = (ingress_files or bool(getattr(request, "attachments", None)) or _carries_files_or_memory(sources, chat_id)
                    or metadata.get(CARRIES_FILES_KEY) is True)
    question = next((text for text, kind, _ in catalog.entries if kind == "conversation"), "") if isinstance(catalog, SourceCatalog) else ""
    attachments = getattr(request, "attachments", None) or (list(catalog.prepared.values()) if isinstance(catalog, SourceCatalog) else [])
    local_context = {"question": question, "workspace": str(bound.get("workspace") or ""),
                     "attachments": copy.deepcopy(attachments),
                     "has_images": bool(isinstance(catalog, SourceCatalog) and catalog.has_images),
                     "attachment_context": {k: bound[k] for k in ("runtime_session_id", "session_id", "attachment_turn_id") if k in bound}} if question else {}
    incomplete = any(not (entry.get("data_url") or entry.get("text") or entry.get("images")) for entry in attachments)
    if incomplete and not local_context.get("attachment_context", {}).get("attachment_turn_id"):
        local_context = {}
    if council_scope is not None:
        local_context = {}
    from core.disclosure.privacy import for_request
    privacy_scope = for_request(request, catalog)
    from core.cloud_privacy_policy import evaluate_cloud_privacy
    from core.cloud_provider_contract import PrivacyClass
    explicit_privacy = metadata.get("privacy_class", (getattr(request, "context", None) or {}).get("privacy_class", PrivacyClass.USER_APPROVED))
    explicit_decision = evaluate_cloud_privacy(privacy_class=explicit_privacy)
    return {"council_scope": council_scope, "privacy_scope": privacy_scope, "local_context": local_context, "chat_id": chat_id,
            "project_id": council_scope.project_id if council_scope else str(bound.get("project_id") or context.get("project_id") or ""),
            "turn_id": str(bound.get("request_id") or getattr(request, "trace_id", None) or getattr(request, "turn_id", None)
                           or metadata.get("request_id") or metadata["_disclosure_internal_turn"]),
            "provider": provider, "cloud": cloud, "has_files": file_content,
            "sensitive": not explicit_decision.allowed or bool(metadata.get("_disclosure_sensitive")) or bool(types & {
                "runtime_memory", "semantic_memory", "user_profile", "private_file", "session_summary",
            }),
            "cancel_event": bound.get("cancel_event"),
            # Only the chat page answers a preview while its turn waits; a Council's previews
            # are answered in its parent chat. Every other caller is told at once.
            "review_waits": council_scope is not None or bound.get(REVIEW_WAITS_KEY) == "wait",
            "pending_holder": bound.get(PENDING_HOLDER_KEY),
            "catalog": catalog,
            "replay_context": {k: bound[k] for k in ("workspace", "workspace_root", "local_only", "local_only_mode", "cloud_disabled", "allow_remote_fetch", "_disclosure_sources") if k in bound},
            "attachments": tuple({"name": str(a.get("filename") or a.get("name") or ""),
                                  "size": a.get("size_bytes", a.get("size", a.get("bytes")))}
                                 for a in attachments if isinstance(a, dict))}


def _reserved_model(model_call_id: str) -> str:
    """The model a standing spend reservation holds for this call, or "" when there is none.

    Only core.paid_call_reservation makes one, from server-held state, for the owner's explicit pick
    of a paid model (never for an automatic fallback). A released, settled or held call is not one.
    """
    if not model_call_id:
        return ""
    try:
        from storage.db import get_connection

        conn = get_connection()
        try:
            # Read only: a home that never reserved a paid call has no ledger table, and none is made here.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_spend_reservations'").fetchone():
                return ""
            row = conn.execute("SELECT status, model_id FROM model_spend_reservations WHERE model_call_id = ?",
                               (str(model_call_id),)).fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    if row is None or str(row[0]) != "reserved" or str(row[1] or "") in {"", "x.api"}:
        return ""
    return str(row[1])


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return str(urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _lane_host(provider_id: str, model: str) -> str:
    """The host of the registered lane ``provider_id`` serving ``model``, or "" when no such lane is on."""
    try:
        from storage.model_provider_manifest import list_provider_manifests

        for manifest in list_provider_manifests(enabled_only=True):
            if str(manifest.provider_id) == str(provider_id) and str(manifest.model_name) == str(model):
                return _host(str((manifest.runtime_config or {}).get("base_url") or ""))
    except Exception:
        return ""
    return ""


def _paid_call_still_reserved(model_call_id: str, *, model: str = "") -> bool:
    """Whether the spend ledger still holds a standing reservation for this call AND this model."""
    reserved = _reserved_model(model_call_id)
    return bool(reserved) and reserved == str(model or "")


def _owner_paid_call(request: Any, payload: dict[str, Any], *, url: str, provider_id: str) -> str:
    """The call id when this request is the owner's reserved paid pick, bound to the reserved model and
    the reserved lane's host: a reservation never carries another model or another destination."""
    call_id = str(getattr(request, "model_call_id", "") or "")
    model = str(payload.get("model") or "")
    reserved = _reserved_model(call_id)
    if not reserved or reserved != model:
        return ""
    lane = _lane_host(provider_id, reserved)
    return call_id if lane and lane == _host(url) else ""


def prepare(payload: dict[str, Any], *, url: str, context: dict[str, Any], request: Any, encoder=encode_body) -> WirePayload:
    """Resolve one owner choice before the invocation gateway claims any spend (see _prepare).

    A refusal is also written to the caller's stop note when it brought one (STOP_NOTE_KEY)."""
    try:
        wire = _prepare(payload, url=url, context=context, request=request, encoder=encoder)
        wire.owner_paid_call = _owner_paid_call(request, wire, url=url, provider_id=str(context.get("provider") or ""))
        return wire
    except DisclosureRefused as exc:
        note = (getattr(request, "metadata", None) or {}).get(STOP_NOTE_KEY)
        if isinstance(note, dict):
            note.update(kind=type(exc).__name__, message=str(exc))
        raise


def _prepare(payload: dict[str, Any], *, url: str, context: dict[str, Any], request: Any, encoder=encode_body) -> WirePayload:
    """Resolve one owner choice before the invocation gateway claims any spend.

    Every transformed payload is presented again. A retry with a different byte
    body in the same turn needs a new choice. Plain chat without sources does
    not interrupt; its exact envelope is available only in this process.
    """
    from core.auto_local_only_mode import endpoint_is_local
    from core.disclosure.settings import load_settings
    from core.model_compare.prepared import replay_for
    replay = replay_for(request)
    body = replay.apply(payload, provider=context["provider"], url=url) if replay else copy.deepcopy(payload)
    if replay:
        source = replay.source()
        if source.restore_map is not None:
            request.metadata["_disclosure_restore"] = source.restore_map
            request.metadata["_disclosure_restore_sha"] = hashlib.sha256(encoder(body)).hexdigest()
    if request.metadata.get("_disclosure_restore_sha") != hashlib.sha256(encoder(body)).hexdigest():
        request.metadata.pop("_disclosure_restore", None)
        request.metadata.pop("_disclosure_restore_sha", None)
    local = not context["cloud"] and endpoint_is_local(url)
    if local and not context.get("local_context") and not replay:
        return WirePayload(body, encoder(body), url, encoder=encoder, cloud_destination=False)
    from core import auto_local_only_mode
    from core.remote_fetch_policy import remote_fetch_forbidden

    if not local and (remote_fetch_forbidden() or auto_local_only_mode.turn_is_local_only(None)):
        raise DisclosureRefused("Cloud models are off for this turn.")
    from core.disclosure.rules import read_rules
    from core.egress_gate import project_project_sources

    project_id = context.get("project_id", "")
    from core.disclosure.scope import current_version

    try:
        scope_version = current_version(context["chat_id"], project_id)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        if local and not replay:
            # A model on this computer is not a disclosure. When the chat's erasure state
            # cannot be verified, the request is only kept out of the in-process copy used
            # for viewing and comparison; the local answer is not refused.
            return WirePayload(body, encoder(body), url, encoder=encoder, cloud_destination=False)
        raise DisclosureRefused("This chat's privacy state could not be verified. Nothing was sent.") from exc
    rules = read_rules(project_id)
    omitted = 0
    rules_ask = False
    privacy_scope = context.get("privacy_scope")
    direct_helper = bool(request.metadata.get("_disclosure_direct_helper"))
    control_only = direct_helper and request.metadata.get("_disclosure_control_only") is True
    if not local and direct_helper and privacy_scope is not None:
        from core.cloud_transport import _operator_cloud_network_allowed
        if not _operator_cloud_network_allowed():
            raise DisclosureRefused("Cloud models are off. Turn them on before sending this request.")
        privacy_scope.preflight(body)
        if not context["chat_id"]:
            if not control_only:
                raise DisclosureRefused("A cloud helper needs an open task for review. Nothing was sent.")
            privacy_scope.approve_exact(body, all_fields=True)
            return WirePayload(body, encoder(body), url, encoder=encoder, control_only=True, cloud_destination=True)
        from core.cloud_provider_contract import PrivacyClass
        if set(privacy_scope.classes) - {PrivacyClass.PUBLIC, PrivacyClass.USER_APPROVED}:
            context = {**context, "sensitive": True}
    if replay:
        privacy_scope = replay.source().privacy_scope
        if not local and privacy_scope is not None:
            privacy_scope.preflight()
    if not local:
        from core.egress_gate import project_messages_for_destination
        messages = body.get("messages")
        if isinstance(messages, list):
            narrowed = project_messages_for_destination(messages, destination_class="cloud_provider")
            omitted += len(messages) - len(narrowed)
            body["messages"] = narrowed
            if replay and not narrowed:
                raise DisclosureRefused("Privacy rules kept this request on your computer. Nothing was sent.")
    projected_source = False
    if replay:
        source = replay.source()
        projected_source = source.replay_context.get("_cloud_projected", False)
        if source.project_id != project_id or source.scope_version != scope_version:
            raise DisclosureRefused("The prepared task belongs to another chat or privacy state.")
    if not local and rules["rules"] and not projected_source:
        messages, project_omitted, rules_ask = project_project_sources(
            body.get("messages", []), project_id=project_id, rules=rules, catalog=context.get("catalog"))
        # Non-message free text (tool definitions, prompts, extensions) has no
        # source proof here. Do not let it bypass source projection.
        omitted += project_omitted
        native_options = body.get("options")
        body = {k: v for k, v in body.items() if k in {
            "model", "temperature", "top_p", "max_tokens", "max_completion_tokens", "stream", "stream_options"}}
        if isinstance(native_options, dict):
            body["options"] = {k: v for k, v in native_options.items() if k in {
                "temperature", "top_p", "top_k", "min_p", "num_predict", "num_ctx", "seed", "repeat_penalty", "repeat_last_n"}
                and type(v) in {int, float, bool}}
        body["messages"] = messages
        if not messages:
            raise DisclosureRefused("Project rules kept this request on your computer. No cloud request was sent.")
    if not local and privacy_scope is not None:
        try:
            # The ordinary initial call has the same hard privacy boundary as
            # helpers and comparison. Projection happens first so withheld
            # content is never promoted by this check.
            privacy_scope.preflight(body)
        except ValueError as exc:
            raise DisclosureRefused(str(exc)) from None
    key = (_home(), context["chat_id"], context["turn_id"])
    settings = load_settings()
    local_context_allowed = True
    while True:
        wire = encoder(body)
        item = Preview(_home(), context["chat_id"], context["turn_id"], context["provider"], url,
                       wire, context["attachments"])
        item.request_format = context.get("request_format", "ollama" if url.rstrip("/").endswith("/api/chat") else "openai")
        item.omitted = omitted
        item.project_id = project_id
        item.rule_revision = rules["revision"]
        item.scope_version = scope_version
        item.cancel_event = context.get("cancel_event")
        item.local_context = (context.get("local_context") or {}) if local_context_allowed and not replay else {}
        item.replay_context = {**context.get("replay_context", {}), "_cloud_projected": not local}
        item.restore_map = request.metadata.get("_disclosure_restore")
        item.replay = replay
        item.privacy_scope = privacy_scope
        item.can_transform = not direct_helper
        item.council_scope = context.get("council_scope")
        item.has_files = bool(context["has_files"])
        from core.disclosure.timing import CallTiming
        item.timing = CallTiming()
        if replay:
            def set_dispatch_deadline(request=request, seconds=replay.timeout_seconds):
                from core import runtime_active_clock
                from core.provider_call_deadline import PROVIDER_DEADLINE_KEY
                deadline = runtime_active_clock.monotonic() + seconds
                existing = request.metadata.get(PROVIDER_DEADLINE_KEY)
                request.metadata[PROVIDER_DEADLINE_KEY] = min(existing, deadline) if isinstance(existing, (int, float)) else deadline
            item.timing.on_dispatch = set_dispatch_deadline
        request.metadata["_disclosure_timing"] = item.timing
        binding = (url, context["provider"], rules["revision"], scope_version, item.digest)
        with _LOCK:
            _prune()
            if key in _CANCELLED:
                raise DisclosureCancelled("Nothing was sent.")
            prior = _APPROVED.get(key, set())
            ask = not local and (bool(replay) or rules_ask or bool(omitted) or (key in _REVIEWED and binding not in prior) or context["sensitive"] or (
                settings["preview_enabled"] and context["has_files"]
                and key[:2] not in _FILES_APPROVED))
            if ask and (key[0], key[1], binding) in _DETACHED_APPROVED:
                # The owner approved exactly these bytes on the chat page after an earlier
                # send could not wait for the review.
                del _DETACHED_APPROVED[(key[0], key[1], binding)]
                ask = False
            if len(_PREVIEWS) >= _LIMIT:
                raise DisclosureRefused("Too many open requests. Close a preview and try again.")
            detach = ask and binding not in prior and not context.get("review_waits")
            if detach:
                item.detached = True
                item.binding = binding
                item.can_transform = False
                item.local_context = {}
            _PREVIEWS[item.preview_id] = item
        if detach:
            pending = item.pending()
            holder = context.get("pending_holder")
            if isinstance(holder, list):
                holder.append(pending)
            raise DisclosurePreviewNeeded(pending)
        if ask and binding not in prior:
            waiting_deadline = time.monotonic() + _TTL
            while not item.event.wait(min(.2, max(0, waiting_deadline - time.monotonic()))):
                if item.council_scope is not None:
                    try:
                        item.council_scope.verify()
                    except (RuntimeError, ValueError):
                        _invalidate(item)
                        raise
                if isinstance(item.cancel_event, threading.Event) and item.cancel_event.is_set():
                    item.decision = {"action": "cancel"}
                    break
                if time.monotonic() >= waiting_deadline:
                    with _LOCK:
                        _PREVIEWS.pop(item.preview_id, None)
                    raise DisclosureCancelled("Nothing was sent. The preview expired.")
            choice = item.decision or {"action": "cancel"}
            action = choice["action"]
            if action in {"cancel", "local"}:
                with _LOCK:
                    _CANCELLED.add(key)
                cancel_event = context.get("cancel_event")
                if isinstance(cancel_event, threading.Event):
                    cancel_event.set()
                if item.council_scope is not None:
                    item.council_scope.cancel_run()
                if action == "local":
                    raise LocalModelRequested("Use a local model instead. Nothing was sent.")
                raise DisclosureCancelled("Nothing was sent.")
            if action == "parts":
                local_context_allowed = False
                indices = choice["indices"]
                messages = body.get("messages")
                if not isinstance(messages, list):
                    raise DisclosureRefused("The selected sections are unavailable. Nothing was sent.")
                sections = choice.get("sections")
                if sections is not None:
                    body["messages"] = _select_sections(body, sections)
                else:
                    if any(n < 0 or n >= len(messages) for n in indices):
                        raise DisclosureRefused("The selected sections are unavailable. Nothing was sent.")
                    body["messages"] = [m for n, m in enumerate(messages) if n in indices]
                if not body["messages"]:
                    raise DisclosureCancelled("Nothing was sent.")
                continue
            if action == "replace":
                local_context_allowed = False
                from core.disclosure.replacer import replace_using_contacts

                paths: list[tuple[list[Any], str]] = []

                def collect_content(value: Any, path: list[Any], paths=paths) -> None:
                    if isinstance(value, str):
                        paths.append((path, value))
                    elif isinstance(value, list):
                        for n, block in enumerate(value):
                            if isinstance(block, dict) and isinstance(block.get("text"), str):
                                paths.append(([*path, n, "text"], block["text"]))

                for n, message in enumerate(body.get("messages", [])):
                    collect_content(message.get("content"), ["messages", n, "content"])
                collect_content(body.get("system"), ["system"])
                if isinstance(body.get("prompt"), str):
                    paths.append((["prompt"], body["prompt"]))
                for n, tool in enumerate(body.get("tools", [])):
                    if not isinstance(tool, dict):
                        continue
                    definition = tool.get("function", tool)
                    path = ["tools", n, "function"] if "function" in tool else ["tools", n]
                    if isinstance(definition, dict) and isinstance(definition.get("description"), str):
                        paths.append(([*path, "description"], definition["description"]))
                replaced = replace_using_contacts([value for _, value in paths])
                for (path, _), text in zip(paths, replaced.texts, strict=True):
                    node = body
                    for part in path[:-1]:
                        node = node[part]
                    node[path[-1]] = text
                from core.disclosure.replacer import PreparedText

                previous = request.metadata.get("_disclosure_restore")
                if isinstance(previous, PreparedText):
                    replaced = PreparedText(replaced.texts, (*previous.replacements, *replaced.replacements))
                request.metadata["_disclosure_restore"] = replaced
                continue
            with _LOCK:
                _REVIEWED.add(key)
        if not local and privacy_scope is not None:
            try:
                privacy_scope.approve_exact(body, all_fields=True)
            except ValueError as exc:
                raise DisclosureRefused(str(exc)) from None
        with _LOCK:
            _APPROVED.setdefault(key, set()).add(binding)
            if context["has_files"]:
                _FILES_APPROVED.add(key[:2])
            item.decision = {"action": "send"}
        request.metadata["_disclosure_preview_id"] = item.preview_id
        if request.metadata.get("_disclosure_restore") is not None:
            request.metadata["_disclosure_restore_sha"] = item.digest
        result = WirePayload(body, wire, url, item.preview_id, encoder=encoder, cloud_destination=not local)
        result.timing = item.timing
        return result


def note_sent(preview_id: str) -> None:
    with _LOCK:
        item = _PREVIEWS.get(preview_id)
        if item is not None:
            item.sent = True


def note_response(request: Any, output_text: str, *, usable: bool, usd_report_complete: bool | None = None) -> None:
    ident = request.metadata.get("_disclosure_preview_id")
    with _LOCK:
        item = _PREVIEWS.get(ident)
    if item is None or not _current(item):
        return
    with _LOCK:
        if item.invalidated or not item.body:
            return
        from core.model_compare.prepared import format_compatible, source_payload
        try:
            body = source_payload(item)
        except ValueError:
            return  # The answer stands; only unsupported comparison is unavailable.
        native = item.request_format == 'ollama'
        if usable and not format_compatible(body, source_native=native, target_native=native):
            from core.turn_model_call_ledger import provider_call_id_for_model
            item.provider_call_id = provider_call_id_for_model(getattr(request, "model_call_id", ""), item.provider)
            item.ready = True
            item.output_text = output_text[:200000]
            item.usd_report_complete = usd_report_complete
