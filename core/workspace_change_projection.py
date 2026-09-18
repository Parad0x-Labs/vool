"""Sanitized browser contract for durable workspace mutations.

``workspace_change_v1`` is deliberately built from the private rollback ledger rather than from
Activity's bounded diff snippets. The ledger owns which changes exist. Activity may add optional
same-mutation correlation metadata, but it cannot manufacture a change.

The ledger stores text, not byte snapshots. This projection distinguishes absent, empty, and
unavailable text state and reports newline-normalization ambiguity instead of fabricating byte
truth the store no longer retains.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any

from core.execution.artifacts import _load_mutation_records
from core.secret_redaction import redact_secrets

WORKSPACE_CHANGE_SCHEMA = "workspace_change_v1"
BROWSER_DIFF_PREVIEW_LIMIT = 1600
_ID_LIMIT = 160
MUTATION_ID_EXACT_LIMIT = 512
PATH_DISPLAY_LIMIT = 500
MAX_FILES_PER_MUTATION = 50
MAX_APPROVAL_AFFECTED_RESOURCES = 12
MAX_APPROVAL_SCOPE_OPTIONS = 8
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,159}$")
_EXACT_ID_RE = re.compile(r"^[A-Za-z0-9._:@+-]+$")
_KNOWN_ACTIONS = frozenset({"created", "updated", "replaced", "deleted", "rolled_back"})
_KNOWN_PERMISSION_AUTHORITIES = frozenset(
    {"explicit_user", "mode_policy", "operator", "project_grant", "runtime_tool_policy"}
)
_USER_APPROVAL_AUTHORITIES = frozenset({"explicit_user"})
_APPROVED_STATES = frozenset({"approved"})


class InvalidWorkspaceChangeSessionIdError(ValueError):
    """Raised before an unsafe or lossy session value can select a ledger path."""


def validate_workspace_change_session_id(value: Any) -> str:
    """Validate a session ID without normalization that could alias two storage keys."""

    if not isinstance(value, str):
        raise InvalidWorkspaceChangeSessionIdError("session_id must be a string")
    if not value:
        raise InvalidWorkspaceChangeSessionIdError("session_id is required")
    if value != value.strip():
        raise InvalidWorkspaceChangeSessionIdError("session_id cannot contain surrounding whitespace")
    if len(value) > _ID_LIMIT:
        raise InvalidWorkspaceChangeSessionIdError("session_id is too long")
    if "\x00" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InvalidWorkspaceChangeSessionIdError("session_id contains a control character")
    if "/" in value or "\\" in value:
        raise InvalidWorkspaceChangeSessionIdError("session_id cannot contain path separators")
    if value in {".", ".."} or ".." in value:
        raise InvalidWorkspaceChangeSessionIdError("session_id cannot contain traversal constructs")
    if _SESSION_ID_RE.fullmatch(value) is None:
        raise InvalidWorkspaceChangeSessionIdError("session_id contains unsupported characters")
    return value


def invalid_workspace_change_v1(reason: str = "invalid_session_id") -> dict[str, Any]:
    return {
        "schema": WORKSPACE_CHANGE_SCHEMA,
        "session_id": None,
        "changes": [],
        "error": {
            "code": "invalid_session_id",
            "message": _safe_text(reason, limit=160) or "invalid session_id",
        },
    }


def build_workspace_change_v1(
    *,
    session_id: str,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    limit: int = 120,
) -> dict[str, Any]:
    """Return the bounded, sanitized change projection for one valid runtime session."""

    try:
        clean_session_id = validate_workspace_change_session_id(session_id)
    except InvalidWorkspaceChangeSessionIdError as exc:
        return invalid_workspace_change_v1(str(exc))

    # A valid, non-empty session ID is already the exact mutation-ledger key. No replacement or
    # path normalization is allowed here: lossy normalization could make two IDs read one ledger.
    records = _dict_items(_load_mutation_records(clean_session_id))
    bounded_limit = max(1, min(_safe_int(limit, default=120), 500))
    event_by_mutation = _completed_event_index(events)

    candidates: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    for record in records:
        mutation_id = _exact_identifier(record.get("mutation_id"))
        raw_changes = _dict_items(record.get("changes"))
        projectable_changes = [
            item for item in raw_changes if _relative_path_projection(item.get("path")) is not None
        ]
        if mutation_id and projectable_changes:
            candidates.append((record, mutation_id, projectable_changes))
    selected = candidates[-bounded_limit:]

    changes: list[dict[str, Any]] = []
    for record, exact_mutation_id, raw_changes in selected:
        mutation_id, mutation_id_representation = _project_identity(exact_mutation_id)
        event = event_by_mutation.get(exact_mutation_id, {})
        rolled_back = bool(record.get("rolled_back_at"))
        files = [
            projected
            for item in raw_changes[:MAX_FILES_PER_MUTATION]
            if (projected := _project_file_change(item)) is not None
        ]
        files_total = len(raw_changes)

        permission_decision, permission_authority = _permission_projection(event)
        changes.append(
            {
                "mutation_id": mutation_id,
                "mutation_id_representation": mutation_id_representation,
                # The existing mutation ID is the only authoritative multi-file group identity.
                "change_group_id": mutation_id,
                "tool_intent": _safe_text(record.get("intent") or event.get("tool_intent"), limit=160),
                "timestamp": _safe_text(
                    event.get("operation_completed_at") or record.get("created_at"), limit=80
                ),
                "client_turn_id": _project_optional_identity(event.get("client_turn_id")),
                "tool_call_id": _project_optional_identity(event.get("tool_call_id")),
                "operation_status": "rolled_back" if rolled_back else "succeeded",
                "result_state": _safe_text(event.get("result_state"), limit=80),
                "files": files,
                "files_returned": len(files),
                "files_total": files_total,
                "files_truncated": files_total > len(files),
                "validation_status": "Unverified",
                "validation_evidence_ids": [],
                # The ledger is session-scoped. It cannot prove this mutation is globally latest
                # for the workspace, so an unrolled mutation must never receive a browser "true".
                "latest_rollback_eligible": False if rolled_back else None,
                "rollback_eligibility_reason": (
                    "already_rolled_back"
                    if rolled_back
                    else "unavailable_session_ledger_cannot_prove_workspace_latest"
                ),
                # Lower-level runtime policy is permission truth, not proof of user approval.
                "permission_decision": permission_decision,
                "permission_authority": permission_authority,
                "user_approval": _user_approval_projection(event),
            }
        )

    return {
        "schema": WORKSPACE_CHANGE_SCHEMA,
        "session_id": clean_session_id,
        "changes": changes,
        "changes_returned": len(changes),
        "changes_total": len(candidates),
        "changes_truncated": len(candidates) > len(selected),
    }


def _project_file_change(change: dict[str, Any]) -> dict[str, Any] | None:
    path_projection = _relative_path_projection(change.get("path"))
    if path_projection is None:
        return None
    path = path_projection["path"]

    before_exists = _optional_bool(change.get("existed_before"))
    after_exists = _optional_bool(change.get("existed_after"))
    before_text = _available_text(change, "before_text", exists=before_exists)
    after_text = _available_text(change, "after_text", exists=after_exists)
    before_for_delta = _text_for_delta(exists=before_exists, text=before_text)
    after_for_delta = _text_for_delta(exists=after_exists, text=after_text)

    normalized_before_ambiguous = bool(
        before_exists is True
        and after_exists is True
        and before_text is not None
        and after_text is not None
        and before_text == after_text
        and ("\n" in before_text or "\r" in before_text)
    )
    line_delta_available = (
        before_for_delta is not None
        and after_for_delta is not None
        and not normalized_before_ambiguous
    )
    if line_delta_available:
        lines_added, lines_removed = _complete_line_counts(
            before=before_for_delta,
            after=after_for_delta,
        )
        preview = _bounded_diff_preview(
            before=before_for_delta,
            after=after_for_delta,
            path=path,
            before_exists=before_exists,
            after_exists=after_exists,
        )
    else:
        lines_added, lines_removed, preview = None, None, ""

    content_changed, content_change_reason = _content_change_truth(
        before_exists=before_exists,
        after_exists=after_exists,
        before_text=before_text,
        after_text=after_text,
        normalized_before_ambiguous=normalized_before_ambiguous,
    )
    before_hash, before_hash_status = _state_hash(
        change.get("before_hash"),
        exists=before_exists,
        content=before_text,
    )
    after_hash, after_hash_status = _state_hash(
        change.get("after_hash"),
        exists=after_exists,
        content=after_text,
    )
    return {
        **path_projection,
        "action": _browser_action(change.get("action")),
        "before_exists": before_exists,
        "after_exists": after_exists,
        "before_content_available": before_text is not None,
        "after_content_available": after_text is not None,
        # These hashes identify the ledger's stored UTF-8 text, not original on-disk bytes. The
        # distinction is load-bearing when universal-newline reads normalized CRLF before record.
        "hash_semantics": "stored_text_sha256",
        "before_hash": before_hash,
        "before_hash_status": before_hash_status,
        "after_hash": after_hash,
        "after_hash_status": after_hash_status,
        "content_changed": content_changed,
        "content_change_reason": content_change_reason,
        "line_delta_available": line_delta_available,
        "line_delta_reason": (
            "available"
            if line_delta_available
            else "normalized_before_state_ambiguous"
            if normalized_before_ambiguous
            else "content_or_existence_unavailable"
        ),
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "diff_preview_available": line_delta_available,
        "diff_preview": preview,
    }


def _content_change_truth(
    *,
    before_exists: bool | None,
    after_exists: bool | None,
    before_text: str | None,
    after_text: str | None,
    normalized_before_ambiguous: bool,
) -> tuple[bool | None, str]:
    if before_exists is None or after_exists is None:
        return None, "existence_unavailable"
    if before_exists != after_exists:
        return True, "existence_changed"
    if before_exists is False:
        return False, "both_absent"
    if before_text is None or after_text is None:
        return None, "content_unavailable"
    if before_text != after_text:
        return True, "stored_text_changed"
    if normalized_before_ambiguous:
        return None, "normalized_before_state_ambiguous"
    return False, "stored_text_equal"


def _complete_line_counts(*, before: str, after: str) -> tuple[int, int]:
    """Count exact stored logical lines, including terminator-only replacements."""

    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(
        a=str(before).splitlines(keepends=True),
        b=str(after).splitlines(keepends=True),
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed += old_end - old_start
        if tag in {"replace", "insert"}:
            added += new_end - new_start
    return added, removed


def _bounded_diff_preview(
    *,
    before: str,
    after: str,
    path: str,
    before_exists: bool | None,
    after_exists: bool | None,
) -> str:
    """Generate at most one browser preview without materializing the complete diff text."""

    # Multiline detectors (PEM blocks, split Bearer credentials, labelled values continued on the
    # next line) must see contiguous source text before the diff is divided into line records.
    # Line counts remain based on the authoritative unredacted stored text above.
    try:
        safe_before = redact_secrets(before)
        safe_after = redact_secrets(after)
    except Exception:
        return ""

    if safe_before == safe_after and before_exists != after_exists:
        label = "empty file created" if after_exists is True else "empty file deleted"
        return _bounded_redacted_text(
            f"--- {'a/' + path if before_exists else '/dev/null'}\n"
            f"+++ {'b/' + path if after_exists else '/dev/null'}\n"
            f"({label})"
        )

    generator = difflib.unified_diff(
        safe_before.splitlines(keepends=True),
        safe_after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=2,
    )
    rendered: list[str] = []
    length = 0
    for records_seen, raw_line in enumerate(generator, start=1):
        if records_seen > 40:
            break
        for line in _render_diff_record(raw_line):
            safe_line = redact_secrets(line)
            separator = "\n" if rendered else ""
            remaining = BROWSER_DIFF_PREVIEW_LIMIT - length - len(separator)
            if remaining <= 0:
                return "\n".join(rendered)
            rendered.append(safe_line[:remaining])
            length += len(separator) + min(len(safe_line), remaining)
            if len(safe_line) > remaining:
                return "\n".join(rendered)
    return "\n".join(rendered)[:BROWSER_DIFF_PREVIEW_LIMIT]


def _render_diff_record(raw_line: str) -> list[str]:
    is_content = (
        bool(raw_line)
        and raw_line[0] in {" ", "+", "-"}
        and not raw_line.startswith("+++")
        and not raw_line.startswith("---")
    )
    if not is_content:
        return [raw_line]
    if raw_line.endswith("\r\n"):
        return [raw_line[:-2], "\\ Line ending: CRLF"]
    if raw_line.endswith("\n"):
        return [raw_line[:-1]]
    if raw_line.endswith("\r"):
        return [raw_line[:-1], "\\ Line ending: CR"]
    return [raw_line, "\\ No newline at end of file"]


def _completed_event_index(events: Any) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for event in _dict_items(events):
        if _safe_text(event.get("event_type"), limit=80) != "workspace_mutation_completed":
            continue
        if event.get("ok") is not True:
            continue
        mutation_id = _exact_identifier(event.get("rollback_id") or event.get("mutation_id"))
        if mutation_id:
            indexed[mutation_id] = event
    return indexed


def _permission_projection(event: dict[str, Any]) -> tuple[str, str]:
    raw_decision = _safe_text(event.get("permission_decision"), limit=32).lower()
    decision = raw_decision if raw_decision in {"allowed", "denied"} else "unknown"
    raw_authority = _safe_text(event.get("permission_authority"), limit=64).lower()
    if raw_authority in _KNOWN_PERMISSION_AUTHORITIES:
        return decision, raw_authority
    if decision != "unknown":
        # This is the defined scope of runtime_execution_tools' mutation event field: its own
        # lower-level tool policy result, not upstream mode consent or a user approval decision.
        return decision, "runtime_tool_policy"
    return decision, "unknown"


def _user_approval_projection(event: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("approval_request")
    if not isinstance(request, dict):
        request = {}
    authority = _safe_text(
        event.get("approval_authority") or request.get("approval_authority") or request.get("authority"),
        limit=64,
    ).lower()
    state = _safe_text(event.get("approval_state") or request.get("approval_state"), limit=32).lower()
    exact_approval_id = _exact_identifier(request.get("approval_id") or event.get("approval_id"))
    if (
        authority not in _USER_APPROVAL_AUTHORITIES
        or state not in _APPROVED_STATES
        or not exact_approval_id
    ):
        return None
    approval_id, approval_id_representation = _project_identity(exact_approval_id)
    affected_resources, affected_resources_total = _bounded_text_collection(
        request.get("affected_resources"),
        item_limit=MAX_APPROVAL_AFFECTED_RESOURCES,
        text_limit=240,
    )
    scope_options, scope_options_total = _bounded_text_collection(
        request.get("scope_options"),
        item_limit=MAX_APPROVAL_SCOPE_OPTIONS,
        text_limit=32,
        allowed=frozenset({"once", "task", "project"}),
    )
    return {
        "status": "approved",
        "authority": authority,
        "approval_id": approval_id,
        "approval_id_representation": approval_id_representation,
        "affected_resources": affected_resources,
        "affected_resources_returned": len(affected_resources),
        "affected_resources_total": affected_resources_total,
        "affected_resources_truncated": affected_resources_total > len(affected_resources),
        "expected_side_effects": _safe_text(request.get("expected_side_effects"), limit=400),
        "scope_options": scope_options,
        "scope_options_returned": len(scope_options),
        "scope_options_total": scope_options_total,
        "scope_options_truncated": scope_options_total > len(scope_options),
        "diff_preview": _bounded_redacted_text(request.get("diff_preview")),
    }


def _available_text(change: dict[str, Any], key: str, *, exists: bool | None) -> str | None:
    if exists is not True or key not in change:
        return None
    value = change.get(key)
    return value if isinstance(value, str) else None


def _text_for_delta(*, exists: bool | None, text: str | None) -> str | None:
    if exists is False:
        return ""
    if exists is True:
        return text
    return None


def _state_hash(
    value: Any,
    *,
    exists: bool | None,
    content: str | None,
) -> tuple[str | None, str]:
    if exists is False:
        return None, "side_absent"
    if exists is not True:
        return None, "existence_unavailable"
    if content is None:
        return None, "content_unavailable"
    if value is None:
        return None, "stored_hash_missing"
    if not isinstance(value, str):
        return None, "stored_hash_invalid"
    candidate = value.strip().lower()
    if _HASH_RE.fullmatch(candidate) is None:
        return None, "stored_hash_invalid"
    try:
        computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None, "content_not_utf8"
    if candidate != computed:
        return None, "stored_hash_mismatch"
    return candidate, "verified"


def _browser_action(value: Any) -> str:
    action = _safe_text(value, limit=48).lower()
    return action if action in _KNOWN_ACTIONS else "unknown"


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _safe_sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _bounded_text_collection(
    value: Any,
    *,
    item_limit: int,
    text_limit: int,
    allowed: frozenset[str] | None = None,
) -> tuple[list[str], int]:
    returned: list[str] = []
    total = 0
    for item in _safe_sequence(value):
        if not isinstance(item, str):
            continue
        safe = _safe_text(item, limit=text_limit)
        if not safe or (allowed is not None and safe not in allowed):
            continue
        total += 1
        if len(returned) < item_limit:
            returned.append(safe)
    return returned, total


def _bounded_redacted_text(value: Any) -> str:
    return _safe_text(value, limit=BROWSER_DIFF_PREVIEW_LIMIT)


def _safe_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, (str, int, float, bool)):
        return ""
    try:
        text = redact_secrets(str(value))
    except Exception:
        return ""
    return text if len(text) <= limit else text[:limit]


def _exact_identifier(value: Any) -> str:
    return value if isinstance(value, str) and value else ""


def _project_identity(value: str) -> tuple[str, str]:
    try:
        contains_secret_material = redact_secrets(value) != value
    except Exception:
        contains_secret_material = True
    if (
        not contains_secret_material
        and len(value) <= MUTATION_ID_EXACT_LIMIT
        and _EXACT_ID_RE.fullmatch(value) is not None
    ):
        return value, "exact"
    digest = _opaque_text_digest(value)
    return f"sha256:{digest}", "sha256"


def _project_optional_identity(value: Any) -> str:
    exact = _exact_identifier(value)
    return _project_identity(exact)[0] if exact else ""


def _relative_path_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    candidate = value
    if not candidate or any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        return None
    if "\\" in candidate:
        return None
    if candidate.startswith("/") or re.match(r"^[A-Za-z]:/", candidate):
        return None
    raw_parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    try:
        safe_candidate = redact_secrets(candidate)
    except Exception:
        return None
    redacted = safe_candidate != candidate
    truncated = len(safe_candidate) > PATH_DISPLAY_LIMIT
    if truncated:
        separator = "…"
        prefix_length = (PATH_DISPLAY_LIMIT - len(separator)) // 2
        suffix_length = PATH_DISPLAY_LIMIT - len(separator) - prefix_length
        display_path = f"{safe_candidate[:prefix_length]}{separator}{safe_candidate[-suffix_length:]}"
    else:
        display_path = safe_candidate
    path_digest = _opaque_text_digest(candidate)
    return {
        "path": display_path,
        "path_id": f"sha256:{path_digest}",
        "path_id_semantics": "sha256_utf8_relative_path",
        "path_length": len(candidate),
        "path_truncated": truncated,
        "path_redacted": redacted,
    }


def _opaque_text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "BROWSER_DIFF_PREVIEW_LIMIT",
    "MAX_APPROVAL_AFFECTED_RESOURCES",
    "MAX_APPROVAL_SCOPE_OPTIONS",
    "MAX_FILES_PER_MUTATION",
    "MUTATION_ID_EXACT_LIMIT",
    "PATH_DISPLAY_LIMIT",
    "WORKSPACE_CHANGE_SCHEMA",
    "InvalidWorkspaceChangeSessionIdError",
    "build_workspace_change_v1",
    "invalid_workspace_change_v1",
    "validate_workspace_change_session_id",
]
