"""Fail-closed access policy for provider-bound conversational context."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from core.context_namespace import (
    ContextImportGrant,
    ensure_chat_namespace,
    list_context_imports,
    load_chat_namespace,
)
from core.request_trust import request_is_owner_local

ContextScope = Literal[
    "chat",
    "project",
    "user_profile",
    "action_receipt",
    "secret",
]

_CONTEXT_SCOPES = frozenset(
    {"chat", "project", "user_profile", "action_receipt", "secret"}
)
_GROUP_SURFACES = frozenset(
    {"discord", "telegram", "slack", "whatsapp", "group"}
)
_CANONICAL_PROJECT_SOURCE_TYPES = frozenset(
    {
        "project_glossary",
        "project_knowledge",
        "self_knowledge",
        "operating_doctrine",
        "voolbook_identity",
        "canonical_document",
    }
)
_CHAT_SOURCE_TYPES = frozenset(
    {
        "active_mission",
        "context_understanding",
        "conversation_policy",
        "dialogue_turn",
        "dialogue_continuity",
        "input_quality",
        "persona",
        "policy",
        "recent_dialogue",
        "session_policy",
        "session_state",
        "session_summary",
        "task_constraints",
        "runtime_tool_observation",
        "tool_observation",
        "turn_context_page",
        "shorthand",
        "cold_archive",
    }
)
_CURRENT_REQUEST_SOURCE_TYPES = frozenset(
    {
        "active_mission",
        "cold_archive",
        "context_understanding",
        "conversation_policy",
        "dialogue_continuity",
        "input_quality",
        "persona",
        "policy",
        "recent_dialogue",
        "session_policy",
        "session_state",
        "shorthand",
        "task_constraints",
    }
)
_PROFILE_SOURCE_TYPES = frozenset(
    {
        "user_heuristic",
        "user_preferences",
        "owner_identity",
        "privacy_pact",
        "execution_preferences",
    }
)
_RECEIPT_SOURCE_TYPES = frozenset(
    {"action_receipt", "payment_status", "final_response"}
)
_SECRET_SOURCE_TYPES = frozenset({"credential", "secret", "api_key"})
_SERVER_ASSEMBLED_SOURCE_TYPES = frozenset(
    {
        *_CURRENT_REQUEST_SOURCE_TYPES,
        *_CANONICAL_PROJECT_SOURCE_TYPES,
        *_PROFILE_SOURCE_TYPES,
        "dialogue_turn",
        "session_summary",
        "tool_observation",
        "shorthand",
        "cold_archive",
    }
)
_TRUSTED_PERSISTED_SOURCE_TYPES = frozenset({"runtime_memory"})
_MARKER_TOKEN_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]{1,31}-\d{2,}\b",
    re.IGNORECASE,
)
_MARKER_CORRECTION_RE = re.compile(
    r"\b(?:only\s+valid\s+)?marker\b[^.\n]{0,96}?"
    r"\b(?:is\s+)?(?:now\s+)?(?P<value>[A-Z][A-Z0-9_]{1,31}-\d{2,})\b",
    re.IGNORECASE,
)
_EXPLICIT_FACT_CORRECTION_RE = re.compile(
    r"^\s*(?:correction|actually|update|correct\s+that)\s*[:,]?\s*"
    r"(?P<subject>[a-z0-9][a-z0-9_. -]{0,96}?)\s+"
    r"(?:is|are|was|were|equals?|=|changed\s+to|updated\s+to|is\s+now)\s+"
    r"(?P<value>[^\n.!?]{1,160})",
    re.IGNORECASE,
)
_PREFERENCE_CORRECTION_RE = re.compile(
    r"^\s*(?:i\s+)?(?:changed\s+my\s+mind|actually|correction|update)\s*[:,]?\s*"
    r"(?:i\s+)?(?:now\s+)?prefer\s+(?P<value>[^\n.!?]{1,160})",
    re.IGNORECASE,
)
_PREFERENCE_DECLARATION_RE = re.compile(
    r"\b(?:i|we)\s+prefer\s+(?P<value>[^\n.!?]{1,160})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContextAccessPolicy:
    """Immutable, server-derived grants for one chat namespace."""

    chat_id: str
    project_id: str = ""
    namespace_state: str = "active"
    grants: tuple[ContextImportGrant, ...] = ()

    @classmethod
    def for_request(
        cls,
        *,
        session_id: str,
        source_context: dict[str, Any] | None = None,
    ) -> ContextAccessPolicy:
        context = dict(source_context or {})
        surface = str(context.get("surface") or "").strip().lower()
        platform = str(context.get("platform") or "").strip().lower()
        group_like = bool(
            context.get("is_group")
            or context.get("group_id")
            or context.get("channel_is_group")
            or surface in _GROUP_SURFACES
            or platform in _GROUP_SURFACES
        )
        trusted_private_local = request_is_owner_local(context) and not group_like
        requested_chat = str(session_id or "").strip()
        if not requested_chat:
            raise ValueError("session_id is required for context access")

        existing = load_chat_namespace(requested_chat)
        trusted_project = ""
        if existing is not None:
            trusted_project = existing.project_id
        elif request_is_owner_local(context):
            # In-process callers are trusted to assign the namespace's project
            # once. HTTP callers have project_id stripped before this point.
            trusted_project = str(context.get("_trusted_project_id") or "").strip()
        namespace = ensure_chat_namespace(
            requested_chat,
            project_id=trusted_project,
            grant_confirmed_profile=trusted_private_local,
        )
        return cls(
            chat_id=namespace.chat_id,
            project_id=namespace.project_id,
            namespace_state=namespace.lifecycle_state,
            grants=list_context_imports(namespace.chat_id),
        )

    @property
    def imported_chat_ids(self) -> frozenset[str]:
        active_chat_ids: set[str] = set()
        for grant in self.grants:
            if grant.scope != "chat" or not grant.source_id.startswith("chat:"):
                continue
            source_chat_id = grant.source_id.removeprefix("chat:")
            source_namespace = load_chat_namespace(source_chat_id)
            if (
                source_namespace is not None
                and source_namespace.lifecycle_state == "active"
            ):
                active_chat_ids.add(source_chat_id)
        return frozenset(active_chat_ids)

    @property
    def imported_project_ids(self) -> frozenset[str]:
        values: set[str] = set()
        for grant in self.grants:
            if grant.scope != "project":
                continue
            value = grant.source_project_id or grant.source_id.removeprefix(
                "project:"
            )
            if value:
                values.add(value)
        return frozenset(values)

    @property
    def profile_source_ids(self) -> frozenset[str]:
        return frozenset(
            grant.source_id
            for grant in self.grants
            if grant.scope == "user_profile"
        )

    @property
    def receipt_source_ids(self) -> frozenset[str]:
        return frozenset(
            grant.source_id
            for grant in self.grants
            if grant.scope == "action_receipt"
        )

    def has_grant(self, scope: str, source_id: str) -> bool:
        clean_scope = str(scope or "").strip().lower()
        clean_source = str(source_id or "").strip()
        return any(
            grant.scope == clean_scope and grant.source_id == clean_source
            for grant in self.grants
        )

    @property
    def allow_project_context(self) -> bool:
        return bool(
            self.project_id and self.project_id in self.imported_project_ids
        )

    @property
    def allow_user_profile_context(self) -> bool:
        return bool(self.profile_source_ids)

    @property
    def allow_action_receipts(self) -> bool:
        return f"chat:{self.chat_id}" in self.receipt_source_ids

    @property
    def allow_shared_context(self) -> bool:
        return self.has_grant("chat", "shared:swarm")

    @property
    def allow_cross_chat(self) -> bool:
        return bool(self.imported_chat_ids)

    @property
    def allow_archived_context(self) -> bool:
        return False

    @property
    def allow_cold_context(self) -> bool:
        return self.has_grant("chat", f"cold:{self.chat_id}")

    def allows_metadata(
        self,
        *,
        source_type: str,
        metadata: dict[str, Any],
    ) -> tuple[bool, str]:
        metadata = dict(metadata or {})
        source_type = str(source_type or metadata.get("source") or "").strip().lower()
        scope = str(metadata.get("scope") or "").strip().lower()
        status = str(metadata.get("status") or "").strip().lower()
        origin_chat = str(
            metadata.get("origin_chat_id")
            or metadata.get("chat_id")
            or metadata.get("session_id")
            or ""
        ).strip()
        origin_project = str(
            metadata.get("origin_project_id")
            or metadata.get("project_id")
            or ""
        ).strip()

        missing = [
            field
            for field, value in (
                ("scope", scope),
                ("source", metadata.get("source") or source_type),
                ("status", status),
            )
            if not str(value or "").strip()
        ]
        if missing:
            return False, f"undeclared_context_metadata:{','.join(missing)}"
        provenance = metadata.get("provenance")
        if (
            not isinstance(provenance, dict)
            or not any(
                str(value or "").strip()
                for value in provenance.values()
            )
        ):
            return False, "context_provenance_missing"
        if scope not in _CONTEXT_SCOPES:
            return False, "undeclared_scope_denied"
        if scope == "secret":
            return False, "secret_scope_denied"
        if status != "active":
            return False, f"context_status_{status or 'missing'}_denied"
        if self.namespace_state != "active":
            return False, f"namespace_{self.namespace_state}_denied"

        if scope == "chat":
            if not origin_chat:
                return False, "chat_without_origin_denied"
            if origin_chat == self.chat_id:
                return True, "current_chat_scope"
            if origin_chat == "shared:swarm" and self.has_grant(
                "chat",
                "shared:swarm",
            ):
                return True, "explicit_shared_context_import"
            if origin_chat in self.imported_chat_ids:
                return True, "explicit_chat_import"
            if self.has_grant("chat", f"chat:{origin_chat}"):
                source_namespace = load_chat_namespace(origin_chat)
                source_state = (
                    source_namespace.lifecycle_state
                    if source_namespace is not None
                    else "missing"
                )
                return (
                    False,
                    f"imported_chat_namespace_{source_state}_denied",
                )
            return False, "cross_chat_denied"

        if scope == "project":
            if (
                metadata.get("source_class") == "canonical"
                and source_type in _CANONICAL_PROJECT_SOURCE_TYPES
            ):
                return True, "canonical_project_source"
            if not origin_project:
                return False, "project_without_origin_denied"
            if (
                origin_project == self.project_id
                and origin_project in self.imported_project_ids
            ):
                return True, "explicit_project_import"
            return False, "project_scope_denied"

        if scope == "user_profile":
            source_id = str(
                metadata.get("source_id") or "profile:confirmed"
            ).strip()
            if (
                source_id in self.profile_source_ids
                or "profile:confirmed" in self.profile_source_ids
            ):
                return True, "explicit_profile_import"
            return False, "user_profile_denied"

        if scope == "action_receipt":
            receipt_id = str(
                metadata.get("receipt_id") or metadata.get("source_id") or ""
            ).strip()
            if origin_project != self.project_id:
                return False, "action_receipt_project_mismatch"
            if receipt_id and receipt_id in self.receipt_source_ids:
                return True, "explicit_receipt_import"
            if (
                origin_chat == self.chat_id
                and f"chat:{self.chat_id}" in self.receipt_source_ids
            ):
                return True, "current_chat_receipt_import"
            return False, "action_receipt_denied"

        return False, "undeclared_scope_denied"

    def allows(self, item: Any) -> tuple[bool, str]:
        metadata = dict(getattr(item, "metadata", {}) or {})
        source_type = str(
            getattr(item, "source_type", "") or metadata.get("source") or ""
        ).strip()
        return self.allows_metadata(
            source_type=source_type,
            metadata=metadata,
        )


@dataclass(frozen=True)
class CurrentTurnCorrection:
    """An explicit, typed correction the current user turn authoritatively owns."""

    fact_key: str
    value: str


def current_turn_corrections(user_text: str) -> tuple[CurrentTurnCorrection, ...]:
    """Extract explicit current-turn corrections without guessing from ordinary prose."""
    text = str(user_text or "")
    corrections: list[CurrentTurnCorrection] = []
    preference_match = _PREFERENCE_CORRECTION_RE.search(text)
    if preference_match is not None:
        preference_value = str(preference_match.group("value") or "").strip()
        if preference_value:
            corrections.append(
                CurrentTurnCorrection(
                    fact_key="preference",
                    value=preference_value,
                )
            )
    if re.search(r"\b(?:correction|forget|only\s+valid)\b", text, re.IGNORECASE):
        marker_match = _MARKER_CORRECTION_RE.search(text)
        marker_value = str(
            marker_match.group("value") if marker_match is not None else ""
        ).strip()
        if marker_value:
            corrections.append(
                CurrentTurnCorrection(fact_key="marker", value=marker_value)
            )

    fact_match = _EXPLICIT_FACT_CORRECTION_RE.search(text)
    if fact_match is not None:
        subject = _normalized_fact_key(fact_match.group("subject"))
        value = str(fact_match.group("value") or "").strip()
        # Opaque markers use their dedicated parser above; treating ``marker is
        # now VALUE`` as an ordinary fact would capture ``now VALUE`` as the
        # value and incorrectly hide the current marker.
        if subject and subject != "marker" and value:
            corrections.append(
                CurrentTurnCorrection(fact_key=subject, value=value)
            )

    return tuple(corrections)


def _normalized_fact_value(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalized_fact_key(value: str) -> str:
    normalized = re.sub(
        r"^(?:my|our|the|a|an|operator(?:'s)?)\s+",
        "",
        " ".join(str(value or "").casefold().split()),
    )
    normalized = re.sub(r"[^a-z0-9_. -]+", "", normalized)
    return " ".join(normalized.split())[:100]


def _candidate_conflicts_with_correction(
    item: Any,
    correction: CurrentTurnCorrection,
) -> bool:
    metadata = dict(getattr(item, "metadata", {}) or {})
    candidate_key = str(metadata.get("fact_key") or "").strip().casefold()
    candidate_value = str(metadata.get("fact_value") or "").strip()
    expected = _normalized_fact_value(correction.value)
    if candidate_key == correction.fact_key or candidate_key.endswith(
        f":{correction.fact_key}"
    ):
        return not candidate_value or _normalized_fact_value(candidate_value) != expected
    if correction.fact_key == "preference":
        declared_preferences = {
            _normalized_fact_value(match.group("value"))
            for match in _PREFERENCE_DECLARATION_RE.finditer(
                str(getattr(item, "content", "") or "")
            )
            if str(match.group("value") or "").strip()
        }
        return bool(declared_preferences and expected not in declared_preferences)
    if correction.fact_key != "marker":
        return False
    markers = {
        _normalized_fact_value(value)
        for value in _MARKER_TOKEN_RE.findall(str(getattr(item, "content", "") or ""))
    }
    return bool(markers and (markers - {expected}))


def reconcile_context_candidates(
    items: list[Any],
    corrections: tuple[CurrentTurnCorrection, ...],
) -> tuple[list[Any], list[tuple[Any, str]]]:
    """Exclude stale candidates before ranking and compression.

    Access policy filtering must run first.  This function is deliberately read-only with respect
    to persistent memory: it only removes a conflicting candidate from this prompt and records
    the reason for the context manifest.
    """
    if not corrections:
        return list(items or []), []
    allowed: list[Any] = []
    denied: list[tuple[Any, str]] = []
    for item in list(items or []):
        correction = next(
            (
                entry
                for entry in corrections
                if _candidate_conflicts_with_correction(item, entry)
            ),
            None,
        )
        if correction is None:
            allowed.append(item)
            continue
        denied.append((item, f"superseded_by_current_turn:{correction.fact_key}"))
    return allowed, denied


def infer_scope(
    source_type: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Compatibility classifier used only to annotate known internal sources.

    Unknown sources return an empty scope and are quarantined by the policy.
    """
    source = str(source_type or "").strip().lower()
    details = dict(metadata or {})
    explicit = str(details.get("scope") or "").strip().lower()
    if explicit in _CONTEXT_SCOPES:
        return explicit
    if details.get("secret") or source in _SECRET_SOURCE_TYPES:
        return "secret"
    if source == "runtime_memory":
        if details.get("session_id") or details.get("chat_id"):
            return "chat"
        return ""
    if source in _CHAT_SOURCE_TYPES:
        return "chat"
    if source in _PROFILE_SOURCE_TYPES:
        return "user_profile"
    if source in _RECEIPT_SOURCE_TYPES:
        return "action_receipt"
    if source in _CANONICAL_PROJECT_SOURCE_TYPES:
        return "project"
    if details.get("project_id"):
        return "project"
    return ""


def normalize_context_metadata(item: Any) -> dict[str, Any]:
    metadata = dict(getattr(item, "metadata", {}) or {})
    source_type = str(getattr(item, "source_type", "") or "").strip().lower()
    scope = infer_scope(source_type, metadata)
    if scope:
        metadata.setdefault("scope", scope)
    metadata.setdefault("source", source_type)
    metadata.setdefault("status", "active")
    if scope == "chat":
        origin_chat = str(
            metadata.get("origin_chat_id")
            or metadata.get("chat_id")
            or metadata.get("session_id")
            or ""
        ).strip()
        if origin_chat:
            metadata["origin_chat_id"] = origin_chat
    if scope == "project":
        origin_project = str(
            metadata.get("origin_project_id")
            or metadata.get("project_id")
            or ""
        ).strip()
        if origin_project:
            metadata["origin_project_id"] = origin_project
        if source_type in _CANONICAL_PROJECT_SOURCE_TYPES:
            metadata.setdefault("source_class", "canonical")
            metadata.setdefault(
                "origin_project_id",
                "canonical:repository",
            )
    provenance = dict(
        getattr(item, "provenance", {}) or {}
    )
    if not provenance and isinstance(metadata.get("provenance"), dict):
        provenance = dict(metadata["provenance"])
    if source_type in _SERVER_ASSEMBLED_SOURCE_TYPES or (
        source_type in _TRUSTED_PERSISTED_SOURCE_TYPES
        and provenance
    ):
        content = str(getattr(item, "content", "") or "")
        source_id = str(
            metadata.get("source_id")
            or getattr(item, "item_id", "")
            or source_type
        ).strip()
        # Context that this process assembles is safe to normalize lazily.  Older
        # on-disk records may carry an otherwise valid provenance dictionary that
        # predates content hashes; the provider manifest must describe the exact
        # rendered item, not reject the whole request for that legacy omission.
        provenance.setdefault("kind", "server_assembled_context")
        provenance.setdefault("source_id", source_id)
        provenance["content_hash"] = hashlib.sha256(
            content.encode()
        ).hexdigest()
    if provenance:
        metadata["provenance"] = provenance
        metadata.setdefault(
            "source_id",
            str(
                provenance.get("source_id")
                or getattr(item, "item_id", "")
                or source_type
            ).strip(),
        )
        if provenance.get("content_hash"):
            metadata.setdefault(
                "content_hash",
                str(provenance["content_hash"]),
            )
    metadata.setdefault("origin_chat_id", "")
    metadata.setdefault("origin_project_id", "")
    return metadata


def annotate_and_filter(
    items: list[Any],
    policy: ContextAccessPolicy,
) -> tuple[list[Any], list[tuple[Any, str]]]:
    """Copy, normalize, and reject candidates before rank/compression."""
    allowed: list[Any] = []
    denied: list[tuple[Any, str]] = []
    for original in list(items or []):
        item = copy.copy(original)
        item.metadata = normalize_context_metadata(original)
        item.provenance = dict(
            item.metadata.get("provenance") or {}
        )
        source_type = str(getattr(item, "source_type", "") or "").strip().lower()
        if (
            item.metadata.get("scope") == "chat"
            and not item.metadata.get("origin_chat_id")
            and source_type in _CURRENT_REQUEST_SOURCE_TYPES
        ):
            item.metadata["origin_chat_id"] = policy.chat_id
        ok, reason = policy.allows(item)
        if ok:
            allowed.append(item)
        else:
            denied.append((item, reason))
    return allowed, denied


# Compatibility name for existing imports. The implementation is grant-based.
ContextScopePolicy = ContextAccessPolicy

__all__ = [
    "ContextAccessPolicy",
    "ContextScope",
    "ContextScopePolicy",
    "CurrentTurnCorrection",
    "annotate_and_filter",
    "current_turn_corrections",
    "infer_scope",
    "normalize_context_metadata",
    "reconcile_context_candidates",
]
