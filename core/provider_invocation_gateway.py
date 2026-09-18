"""Fail-closed sealing for exact provider-bound JSON payloads."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.provider_execution_boundary import provider_execution_boundary
from core.secret_redaction import redact_secrets
from network import signer
from storage.db import get_connection

_CONSUME_LOCK = threading.Lock()
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,255}")
_SAFE_TOP_LEVEL_IDENTIFIER_RE = re.compile(
    r"[@A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}"
)
_SAFE_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:,-]{0,159}")
_SAFE_HEADER_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}")
_SAFE_HASHED_CODE_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*_sha256:[0-9a-f]{64}"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"authorization|proxy[-_ ]authorization|api[-_ ]?key|apikey|"
    r"access[-_ ]?token|refresh[-_ ]?token|auth[-_ ]?token|token|"
    r"password|passwd|secret|credential|client[-_ ]?secret|"
    r"callback[-_ ]?code|code|state|cookie|set[-_ ]?cookie|headers?"
    r")\b\s*[:=]\s*[^&,\r\n;]+"
)
_TUPLE_MANIFEST_FIELDS = frozenset(
    {
        "selected_sources",
        "excluded_sources",
        "redactions",
        "truncation",
        "header_names",
        "provider_messages",
    }
)


class ProviderInvocationValidationError(ValueError):
    """A local manifest preflight failure, never evidence of provider health."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def _redacted_text(value: Any) -> str:
    text = redact_secrets(str(value or ""))
    return _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}: [redacted]",
        text,
    ).strip()


def _hashed_code(value: str, *, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}_sha256:{digest}"


def _safe_identifier(
    value: Any,
    *,
    prefix: str,
    allow_slash: bool = False,
) -> str:
    text = _redacted_text(value)
    if not text:
        return ""
    pattern = (
        _SAFE_TOP_LEVEL_IDENTIFIER_RE
        if allow_slash
        else _SAFE_IDENTIFIER_RE
    )
    looks_absolute = (
        text.startswith(("/", "\\"))
        or bool(re.match(r"^[A-Za-z]:[/\\]", text))
        or text.lower().startswith("file://")
    )
    if pattern.fullmatch(text) and not looks_absolute:
        return text
    return _hashed_code(text, prefix=prefix)


def _safe_code(value: Any, *, prefix: str) -> str:
    # Idempotence: a value that is ALREADY a canonical hashed code must round-trip
    # unchanged. Re-running the secret redactor over "<name>_sha256:<64 hex>" masks the
    # digest as key-shaped material and re-hashes it, so a manifest verified a second
    # time (persist/reload) would fail its own canonicality check. Only freshly-supplied
    # prose goes through redaction; canonical integrity tags stay stable.
    raw = str(value or "").strip()
    if _SAFE_HASHED_CODE_RE.fullmatch(raw):
        return raw
    text = _redacted_text(raw)
    if not text:
        return ""
    if _SAFE_CODE_RE.fullmatch(text):
        return text
    return _hashed_code(text, prefix=prefix)


def _safe_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider manifest timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("provider manifest timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _wire_reserved_output_tokens(payload: dict[str, Any], fallback: Any) -> int:
    """Record the output allowance in the exact provider-bound payload."""
    options = payload.get("options")
    candidates = (
        payload.get("max_tokens"),
        payload.get("max_completion_tokens"),
        options.get("num_predict") if isinstance(options, dict) else None,
        fallback,
    )
    for candidate in candidates:
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return 0


def _content_hash(value: Any) -> str:
    """Hash provider-bound content without retaining the content itself."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _provider_message_trace(payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """Describe the exact message payload structurally, never verbatim."""
    messages = payload.get("messages")
    trace: list[dict[str, str]] = []
    if isinstance(messages, list):
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            trace.append(
                {
                    "role": _safe_code(raw.get("role") or "unknown", prefix="role"),
                    "content_hash": _content_hash(raw.get("content", "")),
                }
            )
    if trace:
        return tuple(trace)
    for key in ("prompt", "input"):
        if key in payload:
            return (
                {
                    "role": "user",
                    "content_hash": _content_hash(payload.get(key)),
                },
            )
    return ()


def _safe_provider_message_trace(values: Any) -> tuple[dict[str, str], ...]:
    """Validate a persisted message trace without accepting message content."""
    safe: list[dict[str, str]] = []
    for raw in list(values or []):
        if not isinstance(raw, dict):
            continue
        role = _safe_code(raw.get("role") or "unknown", prefix="role")
        content_digest = str(raw.get("content_hash") or "").strip().lower()
        if not _SHA256_RE.fullmatch(content_digest):
            content_digest = _content_hash(raw.get("content_hash"))
        safe.append({"role": role, "content_hash": content_digest})
    return tuple(safe)


def _safe_context_items(items: Any) -> tuple[dict[str, Any], ...]:
    selected: list[dict[str, Any]] = []
    for raw in list(items or []):
        if not isinstance(raw, dict):
            continue
        metadata = (
            dict(raw.get("metadata") or {})
            if isinstance(raw.get("metadata"), dict)
            else {}
        )
        provenance = (
            dict(raw.get("provenance") or {})
            if isinstance(raw.get("provenance"), dict)
            else {}
        )
        record = {
            "item_id": _safe_identifier(
                raw.get("item_id"),
                prefix="item_id",
            ),
            "source_type": _safe_code(
                raw.get("source_type"),
                prefix="source_type",
            ),
            "scope": _safe_code(
                raw.get("scope") or metadata.get("scope"),
                prefix="scope",
            ),
            "source_id": _safe_identifier(
                raw.get("source_id")
                or metadata.get("source_id")
                or provenance.get("source_id")
                or raw.get("item_id"),
                prefix="source_id",
            ),
            "content_hash": _safe_identifier(
                raw.get("content_hash")
                or metadata.get("content_hash")
                or provenance.get("content_hash"),
                prefix="content_hash",
            ),
            "reason": _safe_code(
                raw.get("reason"),
                prefix="reason",
            ),
        }
        optional_codes = {
            "status": (
                raw.get("status")
                or metadata.get("status")
                or provenance.get("status")
            ),
            "source_class": (
                raw.get("source_class")
                or metadata.get("source_class")
                or provenance.get("source_class")
            ),
            "source": (
                raw.get("source")
                or metadata.get("source")
                or provenance.get("source")
            ),
            "provenance_kind": (
                raw.get("provenance_kind")
                or provenance.get("kind")
            ),
        }
        for key, value in optional_codes.items():
            cleaned = _safe_code(value, prefix=key)
            if cleaned:
                record[key] = cleaned
        receipt_id = _safe_identifier(
            raw.get("receipt_id")
            or metadata.get("receipt_id")
            or provenance.get("receipt_id"),
            prefix="receipt_id",
        )
        if receipt_id:
            record["receipt_id"] = receipt_id
        selected.append(record)
    return tuple(selected)


def _validate_selected_sources(
    selected_sources: tuple[dict[str, Any], ...],
) -> None:
    for index, source in enumerate(selected_sources):
        missing = [
            key
            for key in (
                "item_id",
                "source_type",
                "scope",
                "source_id",
                "content_hash",
                "reason",
            )
            if not str(source.get(key) or "").strip()
        ]
        if missing:
            raise ProviderInvocationValidationError(
                "selected context source "
                f"{index} is missing {', '.join(missing)}"
            )


def _safe_markers(values: Any, *, prefix: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                cleaned
                for value in list(values or [])
                if (cleaned := _safe_code(value, prefix=prefix))
            }
        )
    )


def _safe_header_names(values: Any) -> tuple[str, ...]:
    safe_names: set[str] = set()
    for value in list(values or []):
        text = _redacted_text(value)
        if not text:
            continue
        if (
            _SAFE_HEADER_NAME_RE.fullmatch(text)
            or _SAFE_HASHED_CODE_RE.fullmatch(text)
        ):
            safe_names.add(text)
        else:
            safe_names.add(_hashed_code(text, prefix="header"))
    return tuple(sorted(safe_names))


@dataclass(frozen=True)
class ProviderInvocationManifest:
    manifest_id: str
    request_id: str
    chat_id: str
    project_id: str
    provider_id: str
    model_id: str
    operation: str
    payload_hash: str
    context_manifest_id: str
    context_trace_id: str
    prompt_profile: str
    provider_messages: tuple[dict[str, str], ...]
    response_control_state: str
    selected_sources: tuple[dict[str, Any], ...]
    excluded_sources: tuple[dict[str, Any], ...]
    capsule_version: str
    history_used: bool
    project_context_used: bool
    user_profile_used: bool
    semantic_retrieval_used: bool
    action_receipts_used: bool
    input_tokens: int
    reserved_output_tokens: int
    redactions: tuple[str, ...]
    truncation: tuple[str, ...]
    header_names: tuple[str, ...]
    signature: str
    created_at: str


def _canonical_manifest_fields(raw: dict[str, Any]) -> dict[str, Any]:
    clean_payload_hash = str(raw.get("payload_hash") or "").strip().lower()
    if not _SHA256_RE.fullmatch(clean_payload_hash):
        raise ValueError("provider manifest payload hash is invalid")
    try:
        input_tokens = max(0, int(raw.get("input_tokens") or 0))
        reserved_output_tokens = max(
            0,
            int(raw.get("reserved_output_tokens") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("provider manifest token counts are invalid") from exc
    selected_sources = _safe_context_items(raw.get("selected_sources"))
    _validate_selected_sources(selected_sources)
    return {
        "manifest_id": _safe_identifier(
            raw.get("manifest_id"),
            prefix="manifest",
        ),
        "request_id": _safe_identifier(
            raw.get("request_id"),
            prefix="request",
        ),
        "chat_id": _safe_identifier(
            raw.get("chat_id"),
            prefix="chat",
        ),
        "project_id": _safe_identifier(
            raw.get("project_id"),
            prefix="project",
        ),
        "provider_id": _safe_identifier(
            raw.get("provider_id"),
            prefix="provider",
            allow_slash=True,
        ),
        "model_id": _safe_identifier(
            raw.get("model_id"),
            prefix="model",
            allow_slash=True,
        ),
        "operation": _safe_code(
            raw.get("operation"),
            prefix="operation",
        ),
        "payload_hash": clean_payload_hash,
        # This is the immutable selection manifest that produced the context fed into
        # the provider-bound payload. It is an identifier only: raw prompt or memory
        # text never enters this provider receipt.
        "context_manifest_id": _safe_identifier(
            raw.get("context_manifest_id"),
            prefix="context_manifest",
        ),
        "context_trace_id": _safe_identifier(
            raw.get("context_trace_id"),
            prefix="context_trace",
        ),
        "prompt_profile": _safe_code(
            raw.get("prompt_profile") or "unknown",
            prefix="prompt_profile",
        ),
        "provider_messages": _safe_provider_message_trace(
            raw.get("provider_messages")
        ),
        "response_control_state": _safe_code(
            raw.get("response_control_state") or "none",
            prefix="response_control",
        ),
        "selected_sources": selected_sources,
        "excluded_sources": _safe_context_items(
            raw.get("excluded_sources")
        ),
        "capsule_version": _safe_identifier(
            raw.get("capsule_version") or "none",
            prefix="capsule",
        ),
        "history_used": bool(raw.get("history_used")),
        "project_context_used": bool(
            raw.get("project_context_used")
        ),
        "user_profile_used": bool(raw.get("user_profile_used")),
        "semantic_retrieval_used": bool(
            raw.get("semantic_retrieval_used")
        ),
        "action_receipts_used": bool(
            raw.get("action_receipts_used")
        ),
        "input_tokens": input_tokens,
        "reserved_output_tokens": reserved_output_tokens,
        "redactions": _safe_markers(
            raw.get("redactions"),
            prefix="redaction",
        ),
        "truncation": _safe_markers(
            raw.get("truncation"),
            prefix="truncation",
        ),
        "header_names": _safe_header_names(
            raw.get("header_names")
        ),
        "created_at": _safe_timestamp(raw.get("created_at")),
    }


def _unsigned_manifest_payload(
    manifest: ProviderInvocationManifest,
) -> dict[str, Any]:
    return {
        key: getattr(manifest, key)
        for key in ProviderInvocationManifest.__dataclass_fields__
        if key != "signature"
    }


def _manifest_signature_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=list,
    ).encode("utf-8")


def _provider_manifest_storage_dict(
    manifest: ProviderInvocationManifest,
) -> dict[str, Any]:
    """Return a canonical verified manifest or fail closed."""
    unsigned = _unsigned_manifest_payload(manifest)
    canonical = _canonical_manifest_fields(unsigned)
    mismatched = [
        key
        for key, value in canonical.items()
        if getattr(manifest, key) != value
    ]
    if mismatched:
        raise ValueError(
            "provider manifest contains non-canonical persistence data: "
            + ", ".join(mismatched)
        )
    if not canonical["manifest_id"]:
        raise ValueError("provider manifest id is required")
    if not canonical["request_id"]:
        raise ValueError("provider manifest request id is required")
    if not all(
        canonical[key]
        for key in ("provider_id", "model_id", "operation")
    ):
        raise ValueError(
            "provider manifest provider, model, and operation are required"
        )
    signature = str(manifest.signature or "")
    peer_id = signer.get_local_peer_id()
    signature_valid = signer.verify(
        _manifest_signature_bytes(canonical),
        signature,
        peer_id,
    )
    legacy_link_fields = (
        "context_manifest_id",
        "context_trace_id",
        "prompt_profile",
        "provider_messages",
        "response_control_state",
    )
    # Existing installations have signed manifests from before trace linkage was
    # introduced. Read those receipts without rewriting or weakening new writes.
    if not signature_valid and not canonical["context_manifest_id"]:
        legacy_canonical = dict(canonical)
        for field in legacy_link_fields:
            legacy_canonical.pop(field, None)
        signature_valid = signer.verify(
            _manifest_signature_bytes(legacy_canonical),
            signature,
            peer_id,
        )
    elif (
        not signature_valid
        and not canonical["context_trace_id"]
        and canonical["prompt_profile"] == "unknown"
        and not canonical["provider_messages"]
        and canonical["response_control_state"] == "none"
    ):
        legacy_canonical = dict(canonical)
        for field in legacy_link_fields[1:]:
            legacy_canonical.pop(field, None)
        signature_valid = signer.verify(
            _manifest_signature_bytes(legacy_canonical),
            signature,
            peer_id,
        )
    if not signature_valid:
        raise ValueError("provider manifest signature verification failed")
    return {
        **canonical,
        "signature": manifest.signature,
    }


def _manifest_from_storage_dict(
    loaded: dict[str, Any],
) -> ProviderInvocationManifest:
    expected = set(ProviderInvocationManifest.__dataclass_fields__)
    missing_legacy_fields = expected - set(loaded)
    if missing_legacy_fields and missing_legacy_fields <= {
        "context_manifest_id",
        "context_trace_id",
        "prompt_profile",
        "provider_messages",
        "response_control_state",
    }:
        loaded = {
            **loaded,
            "context_manifest_id": "",
            "context_trace_id": "",
            "prompt_profile": "unknown",
            "provider_messages": (),
            "response_control_state": "none",
        }
    elif set(loaded) != expected:
        raise ValueError("stored provider manifest schema is invalid")
    normalized = dict(loaded)
    for key in _TUPLE_MANIFEST_FIELDS:
        value = normalized.get(key)
        if not isinstance(value, list):
            raise ValueError("stored provider manifest shape is invalid")
        if key in {"selected_sources", "excluded_sources"} and not all(
            isinstance(item, dict)
            for item in value
        ):
            raise ValueError("stored provider source shape is invalid")
        normalized[key] = tuple(value)
    try:
        return ProviderInvocationManifest(**normalized)
    except TypeError as exc:
        raise ValueError("stored provider manifest shape is invalid") from exc


@dataclass
class DirectProviderRequest:
    """Minimal request envelope for legacy provider call sites."""

    trace_id: str
    context: dict[str, Any]
    metadata: dict[str, Any]
    max_output_tokens: int = 0
    model_call_id: str = ""


class ProviderInvocationPermit:
    """One-use binding between a manifest and one immutable payload snapshot."""

    def __init__(
        self,
        *,
        manifest: ProviderInvocationManifest,
        payload: dict[str, Any],
        budget_reservation_id: str = "",
        budget_effect_id: str = "",
        money_liability_id: str = "",
    ) -> None:
        self.manifest = manifest
        self._payload = copy.deepcopy(payload)
        self._used = False
        # P3 — the paid call's monetary liability, reserved at the seal; its
        # dispatch claim is persisted in consume() before the payload leaves
        self._money_liability_id = str(money_liability_id or "")
        self._money_claim: Any = None
        # P1 AMENDMENT — the provider-call budget: the permit carries its
        # reservation from the seal (where it was RESERVED before any I/O)
        # to `consume()` (where it is CONSUMED immediately before transport).
        # A permit sealed before any budget was configured carries none and
        # consume stays a no-op for it — unbudgeted is an honest pass.
        self._budget_reservation_id = str(budget_reservation_id or "")
        self.budget_effect_id = str(budget_effect_id or "")

    @property
    def budget_reservation_id(self) -> str:
        return self._budget_reservation_id

    def consume(self) -> dict[str, Any]:
        with _CONSUME_LOCK:
            if self._used:
                raise RuntimeError("provider invocation permit already consumed")
            if payload_hash(self._payload) != self.manifest.payload_hash:
                raise RuntimeError("provider payload changed after sealing")
            claim = None
            if self._money_liability_id:
                # P3 — monetary dispatch ownership is persisted BEFORE the
                # payload is handed to transport: one claimant per paid call,
                # and a revoked, expired or frozen authority stops it here.
                from core.effect_budget_money import claim_dispatch

                claim = claim_dispatch(
                    self._money_liability_id,
                    executor=f"provider_invocation_gateway:{self.manifest.manifest_id}",
                )
                self._money_claim = claim
            if self._budget_reservation_id:
                # immediately before execution: the unit is spent the moment
                # the transport handoff happens. A released reservation (the
                # authorization was rolled back before this call ran) is a
                # typed refusal — the model call is prevented completely.
                from core.effect_budget import consume_reservation

                try:
                    consume_reservation(self._budget_reservation_id)
                except BaseException:
                    if claim is not None:
                        with contextlib.suppress(Exception):
                            from core.effect_budget_money import (
                                UNSENT_LOCAL_REFUSAL,
                                UnsentEvidence,
                                record_unsent,
                            )

                            record_unsent(
                                self._money_liability_id,
                                claim.claim_token,
                                evidence=UnsentEvidence(
                                    proof_kind=UNSENT_LOCAL_REFUSAL,
                                    evidence_id=f"{self.manifest.manifest_id}:unit-refused",
                                    source="mechanical",
                                ),
                            )
                    raise
            self._used = True
            return copy.deepcopy(self._payload)

    def settle_budget_cost(self, *, actual_units: int) -> dict[str, Any]:
        """Post-call spend truth for the budget authority: reconcile the
        MEASURED cost against the reserved estimate. Overages are charged and
        flagged `over_limit` durably — an operator ceiling can be exceeded by
        a call that already happened, but NEVER silently. The spend lane
        calls this at convergence; it is the same typed primitive the
        authority exposes (`reconcile_effect_cost`)."""
        if not (self.budget_effect_id or self._budget_reservation_id):
            return {}
        from core.effect_budget import reconcile_effect_cost

        return reconcile_effect_cost(
            self.budget_effect_id,
            actual_units=actual_units,
            reservation_id=self._budget_reservation_id,
        )

    # -- P3: the paid call's monetary lifecycle -------------------------------
    @property
    def money_liability_id(self) -> str:
        return self._money_liability_id

    def _money(self) -> Any:
        from core import effect_budget_money as ebm
        from core.effect_budget import EffectBudgetRefusedError

        if not self._money_liability_id:
            raise EffectBudgetRefusedError(
                ebm.MONEY_STATE_ERROR, "this permit carries no monetary liability"
            )
        return ebm

    def _money_token(self) -> str:
        return self._money_claim.claim_token if self._money_claim is not None else ""

    def money_dispatched(self, *, evidence_id: str = "") -> str:
        """The request left (response headers arrived, the stream opened): the
        liability is pending and its maximum stays held until settlement."""
        ebm = self._money()
        return ebm.record_dispatched(self._money_liability_id, self._money_token(), evidence_id=evidence_id)

    def money_unsent(self, *, proof_kind: str, evidence_id: str, source: str = "mechanical") -> str:
        """Positive proof the request never left: the maximum is released."""
        ebm = self._money()
        return ebm.record_unsent(
            self._money_liability_id,
            self._money_token(),
            evidence=ebm.UnsentEvidence(proof_kind=proof_kind, evidence_id=evidence_id, source=source),
        )

    def money_unknown(self, *, reason: str) -> str:
        """A lost body, a timeout after send, a stream that died mid-way:
        UNKNOWN, maximum held — never a release."""
        ebm = self._money()
        return ebm.record_unknown(self._money_liability_id, self._money_token(), reason=reason)

    def settle_money(self, evidence: Any) -> dict[str, Any]:
        ebm = self._money()
        return ebm.settle_liability(self._money_liability_id, evidence)

    def abandon_money(self, *, reason: str = "sealed paid call abandoned before transport") -> bool:
        """A permit that will never be consumed returns its never-claimed maximum."""
        ebm = self._money()
        if self._used:
            return False
        return ebm.release_unclaimed(self._money_liability_id, reason=reason)


def ensure_provider_manifest_schema() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_invocation_manifests (
                manifest_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                chat_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_provider_manifest_request
                ON provider_invocation_manifests(request_id, created_at);

            CREATE TRIGGER IF NOT EXISTS provider_manifest_no_update
            BEFORE UPDATE ON provider_invocation_manifests
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'provider invocation manifests are immutable'
                );
            END;

            CREATE TRIGGER IF NOT EXISTS provider_manifest_no_delete
            BEFORE DELETE ON provider_invocation_manifests
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'provider invocation manifests are immutable'
                );
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()


def store_provider_manifest(
    manifest: ProviderInvocationManifest,
) -> None:
    payload = _provider_manifest_storage_dict(manifest)
    ensure_provider_manifest_schema()
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO provider_invocation_manifests (
                manifest_id, request_id, chat_id, project_id,
                provider_id, model_id, operation, payload_hash,
                manifest_json, signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["manifest_id"],
                payload["request_id"],
                payload["chat_id"],
                payload["project_id"],
                payload["provider_id"],
                payload["model_id"],
                payload["operation"],
                payload["payload_hash"],
                encoded,
                payload["signature"],
                payload["created_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_provider_manifest(
    manifest_id: str,
) -> dict[str, Any] | None:
    ensure_provider_manifest_schema()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT manifest_id, request_id, chat_id, project_id,
                   provider_id, model_id, operation, payload_hash,
                   manifest_json, signature, created_at
            FROM provider_invocation_manifests
            WHERE manifest_id = ?
            LIMIT 1
            """,
            (str(manifest_id or "").strip(),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        loaded = json.loads(str(row["manifest_json"]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("stored provider manifest JSON is invalid") from exc
    if not isinstance(loaded, dict):
        raise ValueError("stored provider manifest JSON is invalid")
    manifest = _manifest_from_storage_dict(loaded)
    canonical = _provider_manifest_storage_dict(manifest)
    for key in (
        "manifest_id",
        "request_id",
        "chat_id",
        "project_id",
        "provider_id",
        "model_id",
        "operation",
        "payload_hash",
        "signature",
        "created_at",
    ):
        if str(row[key]) != str(canonical[key]):
            raise ValueError(
                "stored provider manifest columns do not match manifest"
            )
    return canonical


@provider_execution_boundary
def seal_provider_invocation(
    *,
    request: Any,
    provider_id: str,
    model_id: str,
    operation: str,
    payload: dict[str, Any],
    header_names: list[str] | tuple[str, ...] = (),
    monetary: Any = None,
    require_monetary_authority: bool = False,
) -> ProviderInvocationPermit:
    metadata = dict(getattr(request, "metadata", None) or {})
    context = dict(
        getattr(request, "context", None)
        or metadata.get("context_manifest")
        or {}
    )
    prompt_budget = dict(metadata.get("prompt_budget") or {})
    request_id = str(
        getattr(request, "model_call_id", "")
        or getattr(request, "trace_id", "")
        or getattr(request, "task_id", "")
        or f"request-{uuid.uuid4().hex}"
    )
    clean_provider_id = str(provider_id or "").strip()
    clean_model_id = str(model_id or "").strip()
    clean_operation = str(operation or "").strip()
    if not clean_provider_id or not clean_model_id or not clean_operation:
        raise ProviderInvocationValidationError(
            "provider_id, model_id, and operation are required"
        )
    if require_monetary_authority and monetary is None:
        # P3 — a caller that knows this surface bills (a prepaid balance, an
        # x402 cap) must bring the monetary authority; without it the call is
        # refused before anything is sealed, never sent uncapped
        from core.effect_budget import EffectBudgetRefusedError
        from core.effect_budget_money import MONEY_AUTHORITY_REQUIRED

        raise EffectBudgetRefusedError(
            MONEY_AUTHORITY_REQUIRED,
            f"{clean_provider_id} {clean_operation} is a paid call and no monetary "
            "authority was supplied; an uncapped paid call is never sealed",
        )
    context_manifest_id = str(context.get("context_manifest_id") or "").strip()
    context_trace_id = str(
        context.get("context_manifest_trace_id")
        or context.get("trace_id")
        or getattr(request, "trace_id", "")
        or ""
    ).strip()
    context_items = list(context.get("items_included") or [])
    excluded_items = list(context.get("items_excluded") or [])
    candidate_items = list(context.get("candidate_items") or [])
    selected_sources = _safe_context_items(context_items)
    _validate_selected_sources(selected_sources)
    excluded_sources = _safe_context_items(excluded_items)
    if not context_manifest_id:
        # Direct/legacy provider callers may have no pre-existing selection receipt,
        # but they still need an auditable proof that the exact payload was evaluated.
        # Seal the supplied selection, or an explicit empty selection, before I/O.
        from core.context_manifest import build_context_manifest
        from core.provenance_store import store_manifest

        empty_manifest = build_context_manifest(
            task_id=request_id,
            trace_id=context_trace_id or request_id,
            evidence_items=[],
            source_metadata=[],
            redaction_markers=[
                "context_manifest_auto_empty"
                if not selected_sources and not excluded_sources
                else "context_manifest_auto_sealed"
            ],
            truncation_markers=[],
            chat_id=str(context.get("chat_id") or ""),
            project_id=str(context.get("project_id") or ""),
            context_scopes=[],
            selected_items=list(selected_sources),
            excluded_items=list(excluded_sources),
            capsule_version="none",
            input_tokens=0,
            reserved_output_tokens=0,
            provider=clean_provider_id,
            model=clean_model_id,
            access_policy=dict(context.get("access_policy") or {}),
            candidate_items=list(candidate_items),
        )
        store_manifest(empty_manifest)
        context_manifest_id = empty_manifest.manifest_id
        context_trace_id = empty_manifest.trace_id
    if not context_manifest_id or not context_trace_id:
        raise ProviderInvocationValidationError(
            "required context manifest link is missing"
        )
    if context_manifest_id:
        from core.provenance_store import load_manifest

        context_manifest = load_manifest(context_manifest_id)
        if context_manifest is None:
            raise ProviderInvocationValidationError(
                "required context manifest was not persisted"
            )
        if str(context_manifest.get("trace_id") or "") != context_trace_id:
            raise ProviderInvocationValidationError(
                "context manifest trace id does not match provider request"
            )
    selected_scopes = {
        str(source.get("scope") or "").strip()
        for source in selected_sources
    }
    selected_types = {
        str(source.get("source_type") or "").strip()
        for source in selected_sources
    }
    selected_reasons = {
        str(source.get("reason") or "").strip().lower()
        for source in selected_sources
    }
    manifest_id = f"provider-manifest-{uuid.uuid4().hex}"
    unsigned = _canonical_manifest_fields({
        "manifest_id": manifest_id,
        "request_id": request_id,
        "chat_id": str(context.get("chat_id") or ""),
        "project_id": str(context.get("project_id") or ""),
        "provider_id": clean_provider_id,
        "model_id": clean_model_id,
        "operation": clean_operation,
        "payload_hash": payload_hash(payload),
        "context_manifest_id": context_manifest_id,
        "context_trace_id": context_trace_id,
        "prompt_profile": str(
            metadata.get("system_prompt_profile") or "unknown"
        ),
        "provider_messages": _provider_message_trace(payload),
        # The final response-control decision occurs after provider I/O. The
        # completed turn event records that outcome against this immutable trace.
        "response_control_state": (
            "pending"
            if isinstance(metadata.get("response_constraint"), dict)
            else "none"
        ),
        "selected_sources": selected_sources,
        "excluded_sources": excluded_sources,
        "capsule_version": str(
            context.get("capsule_version") or "none"
        ),
        "history_used": bool(
            selected_types
            & {
                "active_mission",
                "dialogue_continuity",
                "dialogue_turn",
                "recent_dialogue",
                "session_state",
            }
        ),
        "project_context_used": "project" in selected_scopes,
        "user_profile_used": "user_profile" in selected_scopes,
        "semantic_retrieval_used": bool(
            selected_types
            & {
                "runtime_memory",
                "semantic_memory",
                "session_summary",
            }
            or any(
                "semantic" in reason or "retrieval" in reason
                for reason in selected_reasons
            )
        ),
        "action_receipts_used": "action_receipt" in selected_scopes,
        "input_tokens": int(
            prompt_budget.get("final_input_tokens")
            or context.get("input_tokens")
            or 0
        ),
        "reserved_output_tokens": _wire_reserved_output_tokens(
            payload,
            getattr(request, "max_output_tokens", 0)
            or context.get("reserved_output_tokens")
            or 0,
        ),
        "redactions": context.get("redaction_markers"),
        "truncation": [
                *list(context.get("trimming_decisions") or []),
                *(
                    ["prompt_budget_trimmed"]
                    if prompt_budget.get("status") == "trimmed"
                    else []
                ),
            ],
        "header_names": header_names,
        "created_at": _utcnow(),
    })
    signature = signer.sign(
        _manifest_signature_bytes(unsigned)
    )
    manifest = ProviderInvocationManifest(
        **unsigned,
        signature=signature,
    )
    store_provider_manifest(manifest)
    request_metadata = getattr(request, "metadata", None)
    if isinstance(request_metadata, dict):
        request_metadata["provider_manifest_id"] = manifest.manifest_id
        request_metadata["provider_payload_hash"] = manifest.payload_hash
    # P1 AMENDMENT — the provider-call budget gate, at the seal (before any
    # I/O): an authorized model call RESERVES here; the typed refusal raises
    # before a socket exists, so an exhausted budget prevents the model call
    # completely. Inside a turn the reservation rides the turn's ledger
    # (same receipts as every other effect class); a background caller
    # reserves directly under the window/project dimensions (turn/session
    # rules honestly do not bind a call with no turn identity).
    budget_reservation_id, budget_effect_id = _reserve_provider_call_budget(
        provider_id=clean_provider_id,
        model_id=clean_model_id,
        operation=clean_operation,
    )
    money_liability_id = ""
    if monetary is not None:
        try:
            money_liability_id = _reserve_provider_call_money(
                monetary,
                provider_id=clean_provider_id,
                model_id=clean_model_id,
                manifest=manifest,
                budget_effect_id=budget_effect_id,
            )
        except BaseException:
            if budget_reservation_id:
                with contextlib.suppress(Exception):
                    from core.effect_budget import release_reservation

                    release_reservation(
                        budget_reservation_id,
                        reason="money authority refused the provider call at the seal",
                    )
            raise
    return ProviderInvocationPermit(
        manifest=manifest,
        payload=payload,
        budget_reservation_id=budget_reservation_id,
        budget_effect_id=budget_effect_id,
        money_liability_id=money_liability_id,
    )


def _reserve_provider_call_budget(
    *, provider_id: str, model_id: str, operation: str
) -> tuple[str, str]:
    """Reserve one provider_call unit at the one budget authority.

    Returns (reservation_id, effect_id) — ("", "") when the class is
    unbudgeted (the honest pass: no rows, no events, no behavior change).
    Raises the typed `EffectBudgetRefusedError` on refusal; the seal never
    catches it, so the model call cannot proceed.
    """
    from core import effect_budget

    if not effect_budget.gateway_budget_class("provider_call"):
        return "", ""
    if not effect_budget.active_budgets("provider_call"):
        return "", ""
    ledger = None
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
    except Exception:
        ledger = None
    if ledger is not None:
        from core.effect_gateway import (
            DECISION_ALLOWED,
            LIFECYCLE_AUTHORIZED,
            EffectReceipt,
        )

        lifecycle = ledger.open_effect(
            EffectReceipt(
                effect_class="provider_call",
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason=f"provider call {provider_id}/{model_id}/{operation}",
                provider_id=str(provider_id or ""),
                decided_by="provider_invocation_gateway.seal",
            )
        )
        return lifecycle_effect_reservation(lifecycle.effect_id), lifecycle.effect_id
    receipt = effect_budget.reserve_effect_units(
        "provider_call",
        owner_ref="provider_invocation_gateway",
        project_key="default",
    )
    return receipt.reservation_id, receipt.effect_id


def _reserve_provider_call_money(
    monetary: Any,
    *,
    provider_id: str,
    model_id: str,
    manifest: ProviderInvocationManifest,
    budget_effect_id: str,
) -> str:
    """P3 — reserve a paid call's full monetary maximum at the seal, before any
    I/O, under the owning turn's identity when a turn is active. The sealed
    provider and model ARE the liability's provider and model (a request that
    names others is refused), and the sealed payload digest and manifest id
    ride along as public correlation."""
    from dataclasses import replace as _replace

    from core import effect_budget_money as ebm
    from core.effect_budget import EffectBudgetRefusedError

    if not isinstance(monetary, ebm.LiabilityRequest):
        raise EffectBudgetRefusedError(
            ebm.MONEY_INVALID_REQUEST, "monetary authority must be a LiabilityRequest"
        )
    if monetary.model_id and monetary.model_id != model_id:
        raise EffectBudgetRefusedError(
            ebm.MONEY_IDENTITY_CONFLICT,
            f"the liability names model {monetary.model_id!r} but the sealed call is {model_id!r}",
        )
    ledger = None
    with contextlib.suppress(Exception):
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
    owned: dict[str, str] = {}
    owner_ref = str(monetary.owner_ref or "provider_invocation_gateway")
    if ledger is not None:
        owned.update(ledger._budget_identity())
        if owned.get("project_key") == "default":
            owned["project_key"] = ""
        owner_ref = ledger.ledger_id
    owned["provider_id"] = provider_id
    request = ebm.bind_owned_identity(
        monetary, owned=owned, owner_ref=owner_ref, effect_id=budget_effect_id
    )
    correlation = dict(request.correlation or {})
    correlation["provider_manifest_id"] = manifest.manifest_id
    correlation["sealed_payload_sha256"] = str(manifest.payload_hash)
    request = _replace(request, model_id=model_id, correlation=correlation)
    receipt = ebm.reserve_liability(request)
    if receipt.state != ebm.LIABILITY_RESERVED:
        raise EffectBudgetRefusedError(
            ebm.MONEY_CLAIM_CONFLICT,
            f"operation {receipt.operation_id} is already {receipt.state}; a paid call is "
            "never re-sealed onto a claimed, unknown or settled payment",
        )
    return receipt.liability_id


def lifecycle_effect_reservation(effect_id: str) -> str:
    """The OPEN reservation id bound to a gateway effect (the permit needs
    the id itself; the ledger keeps the lifecycle)."""
    from core import effect_budget

    for row in effect_budget.reservation_rows(state=effect_budget.RESERVATION_RESERVED):
        if str(row.get("effect_id") or "") == str(effect_id or ""):
            return str(row["reservation_id"])
    return ""


@provider_execution_boundary
def seal_direct_provider_invocation(
    *,
    provider_id: str,
    model_id: str,
    operation: str,
    payload: dict[str, Any],
    request_id: str,
    context_manifest: dict[str, Any] | None = None,
    max_output_tokens: int = 0,
    header_names: list[str] | tuple[str, ...] = (),
    monetary: Any = None,
    require_monetary_authority: bool = False,
) -> ProviderInvocationPermit:
    """Seal a direct provider call that has not yet migrated to ModelRequest."""
    request = DirectProviderRequest(
        trace_id=str(request_id or ""),
        context=dict(context_manifest or {}),
        metadata={},
        max_output_tokens=max(0, int(max_output_tokens or 0)),
    )
    return seal_provider_invocation(
        request=request,
        provider_id=provider_id,
        model_id=model_id,
        operation=operation,
        payload=payload,
        header_names=header_names,
        monetary=monetary,
        require_monetary_authority=require_monetary_authority,
    )


__all__ = [
    "DirectProviderRequest",
    "ProviderInvocationManifest",
    "ProviderInvocationPermit",
    "ProviderInvocationValidationError",
    "canonical_payload_bytes",
    "ensure_provider_manifest_schema",
    "load_provider_manifest",
    "payload_hash",
    "seal_direct_provider_invocation",
    "seal_provider_invocation",
    "store_provider_manifest",
]
