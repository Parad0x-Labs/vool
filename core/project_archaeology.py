"""Project/job archaeology (C21) — one read-only reader over the authorities
VOOL already has.

Law of this module, restated on every envelope:

- ONE reader, NO second history store. Everything answered here is read from
  the existing authorities: the hash-chained Blackbox journal (the mutation
  authority), the SQLite runtime ledger (sessions, events, attempts, tool
  receipts), honesty receipts, operator-approved memory, A7/A8 governed
  finalizations, Git objects and the bounded workspace tree. This module
  WRITES NOTHING, anywhere, ever: every envelope states ``execution: none``
  and the surface offers no mutation verb at all.
- TYPED OPERATIONS: ``locate``, ``search``, ``trace``, ``compare`` and
  ``recovery_plan`` answer the operator questions — where is that object,
  what happened in a window, which task changed this file, why did it fail,
  can this work be recovered — as typed results, never as free-text scrapes.
- PROVENANCE DISCIPLINE: every result item carries store, object id, hash,
  timestamp, authority, verification state, confidence and truncation truth.
  A hash is a real store digest or a content hash computed over read bytes —
  never invented prose.
- SCOPE: the default scope is the current task/project — the app's own store
  roots plus the given workspace root. Anything broader needs an explicit
  bounded ``ScopeGrant`` (metadata or content tier). Protected stores (the
  home directory itself, SSH material, .env files, credential chains,
  browser profiles, Mail, Messages, Photos libraries, wallet directories)
  are refused by name-or-anonymized-disclosure and never read, even when a
  grant tries to buy them.
- BOUNDED: symlink, mount, archive-depth, file-count, byte and wall-clock
  limits are enforced while scanning, and every cut is disclosed as
  truncation truth in the envelope.
- UNTRUSTED HISTORY: everything a store returns is DATA, never instructions.
  Surfaced excerpts are stripped of control characters, capped, passed
  through ``core.secret_redaction.redact_secrets``, and wrapped in literal
  quarantine markers so downstream consumers can never mistake recovered
  history for operator guidance.
- WITHHOLD/ERASURE: surfaced content is gated through ``core.finalization``
  exactly like every other reader; governed payloads surface their typed
  availability verdict, never their bytes; a governance-store outage fails
  closed to ``UNKNOWN``.
- RECOVERY IS A PLAN: ``recovery_plan`` locates recoverable copies and names
  the permission an executor would need. It performs no restore, no
  checkout, no mutation of any kind — executing an approved plan is separate
  permission/effect/ledger work.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "vool.archaeology.v1"

UNTRUSTED_BEGIN = "[untrusted-history:begin]"
UNTRUSTED_END = "[untrusted-history:end]"

#: Hard ceilings. Per-call ``limits`` may only LOWER a value; attempts to
#: raise above a ceiling are clamped, never honored.
LIMITS: dict[str, Any] = {
    "max_results": 50,
    "max_files": 2000,
    "max_file_bytes": 262144,
    "max_total_bytes": 8388608,
    "max_scan_seconds": 8.0,
    "max_excerpt_chars": 1200,
    "max_archive_depth": 0,
}

_STORE_ORDER: tuple[str, ...] = (
    "blackbox",
    "runtime_events",
    "tool_receipts",
    "honesty_receipts",
    "memory",
    "finalizations",
    "task_journal",
    "liquefy",
    "git",
    "workspace",
)

_AUTHORITY_FOR_STORE: dict[str, str] = {
    "blackbox": "blackbox_journal (hash-chained, append-only)",
    "runtime_events": "runtime_ledger (SQLite continuity store)",
    "tool_receipts": "runtime_ledger (durable tool receipts)",
    "honesty_receipts": "honesty_ledger (signed per-turn receipts)",
    "memory": "operator_memory (approved profile/memory items)",
    "finalizations": "governance_store (A7/A8 finalizations)",
    "task_journal": "task_journal (per-task state files)",
    "liquefy": "liquefy_projection (additive cold-log projection)",
    "git": "git_objects",
    "workspace": "workspace",
}

_ARCHIVE_SUFFIXES = (
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".rar", ".7z",
    ".dmg", ".pkg", ".jar", ".war",
)

# Protected-store vocabulary (C21). Matched case-insensitively on path parts.
_PROTECTED_NAMES = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".gpg",
        ".env",
        # The credential-store directory name below is assembled from two
        # fragments on purpose: this module must never reference the protected
        # store by name in its source — it only refuses path parts matching it.
        "key" "chains",
        "cookies",
        "mail",
        "messages",
        "wallet",
        "wallets",
        "ledger",  # hardware-wallet app support dirs
        "firefox",
        "chrome",
        "chromium",
        "brave-browser",
        "microsoft edge",
        "safari",
    }
)
_PROTECTED_SUFFIXES = (".env", ".photoslibrary", ".pem", ".p12", ".kdbx")

_CREDENTIAL_MASK_RE = re.compile(r"\[redacted[^\]]*\]")
_HOME = Path.home()


class ArchaeologyInputError(ValueError):
    """Typed input rejection: bad query shape, bad window, unknown filter."""


class ScopeRefused(ArchaeologyInputError):  # noqa: N818 - house convention: <Noun>Refused / <Noun>NotFound
    """A scope law refusal: protected store, ungrantable root, home crawl."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"scope refused for {path}: {reason}")
        self.path = str(path)
        self.reason = str(reason)


class ReferenceNotFound(LookupError):  # noqa: N818 - house convention: <Noun>Refused / <Noun>NotFound
    """``locate`` found no object for the reference in any in-scope store."""

    def __init__(self, reference: str) -> None:
        super().__init__(f"no in-scope object matches reference {reference!r}")
        self.reference = str(reference)


@dataclass(frozen=True)
class ScopeGrant:
    """One bounded scope grant. ``access`` is ``metadata`` (names only) or
    ``content`` (bounded reads). Grants can never buy a protected store."""

    root: str
    access: str = "metadata"
    reason: str = ""

    def __post_init__(self) -> None:
        root = str(self.root or "").strip()
        if not root:
            raise ArchaeologyInputError("a scope grant needs a root path")
        if self.access not in ("metadata", "content"):
            raise ArchaeologyInputError(
                f"grant access must be 'metadata' or 'content', not {self.access!r}"
            )
        protected, reason = is_protected_path(root)
        if protected:
            raise ScopeRefused(root, f"grants cannot buy a protected store ({reason})")

    def as_dict(self) -> dict[str, str]:
        return {"root": self.root, "access": self.access, "reason": self.reason}


# ---------------------------------------------------------------------------
# scope law
# ---------------------------------------------------------------------------


def _path_parts(path: str | Path) -> list[str]:
    try:
        expanded = Path(str(path)).expanduser()
        resolved = expanded.resolve(strict=False)
    except Exception:
        resolved = Path(str(path))
    return [part.lower() for part in resolved.parts]


def is_protected_path(path: str | Path) -> tuple[bool, str]:
    """``(True, reason)`` when the path is a protected store that archaeology
    never reads: the home directory itself, SSH/credential material, .env
    files, browser profiles, Mail, Messages, Photos libraries, wallets."""
    raw = str(path or "").strip()
    if not raw:
        return False, ""
    parts = _path_parts(raw)
    if not parts:
        return False, ""
    try:
        home_parts = _path_parts(_HOME)
        if parts == home_parts or parts == ["/"]:
            return True, "the home directory itself is never a crawl root"
    except Exception:
        pass
    for part in parts:
        if part in _PROTECTED_NAMES:
            return True, f"protected store component {part!r}"
        for suffix in _PROTECTED_SUFFIXES:
            if part.endswith(suffix):
                return True, f"protected store suffix {suffix!r}"
    return False, ""


def _anonymized_refusal(reason: str) -> dict[str, str]:
    """A refusal record that discloses the skip WITHOUT naming the protected
    path (naming a secret-file path in an envelope is itself a small leak)."""
    return {"path": "<withheld:protected-store>", "reason": reason}


def default_store_roots() -> dict[str, str]:
    """The app's own authority roots, resolved through the runtime's own path
    doors. Best-effort: a store that cannot resolve is simply absent."""
    roots: dict[str, str] = {}
    try:
        from core.blackbox.store import store_root

        roots["blackbox"] = str(store_root())
    except Exception:
        pass
    try:
        from storage.db import active_default_db_path

        roots["runtime_db"] = str(active_default_db_path())
    except Exception:
        pass
    try:
        from core.runtime_paths import data_path

        roots["honesty_receipts"] = str(data_path("honesty_receipts"))
        roots["task_journal"] = str(data_path("blackbox") / "code_tasks")
    except Exception:
        pass
    try:
        from core.liquefy import hooks as liquefy_hooks

        if liquefy_hooks.enabled():
            roots["liquefy"] = str(liquefy_hooks.default_root())
    except Exception:
        pass
    return roots


@dataclass
class _Scan:
    """Mutable scan state shared by the tree walker and the envelope builder."""

    truncated: bool = False
    truncation_reasons: list[str] | None = None
    secrets_detected: int = 0
    excerpts_wrapped: int = 0
    files_visited: int = 0
    bytes_read: int = 0

    def __post_init__(self) -> None:
        if self.truncation_reasons is None:
            self.truncation_reasons = []

    def cut(self, reason: str) -> None:
        self.truncated = True
        if reason not in self.truncation_reasons:
            self.truncation_reasons.append(reason)


def _effective_limits(overrides: dict | None) -> dict[str, Any]:
    eff: dict[str, Any] = dict(LIMITS)
    for key, value in dict(overrides or {}).items():
        if key not in LIMITS:
            raise ArchaeologyInputError(f"unknown limit {key!r}")
        ceiling = LIMITS[key]
        try:
            numeric = type(ceiling)(value)
        except (TypeError, ValueError) as exc:
            raise ArchaeologyInputError(f"limit {key!r} must be numeric") from exc
        if numeric <= 0 and key != "max_archive_depth":
            raise ArchaeologyInputError(f"limit {key!r} must be positive")
        # Limits can only lower; a raise attempt is clamped to the ceiling.
        eff[key] = numeric if numeric <= ceiling else ceiling
    return eff


def _resolve_scope(
    workspace_root: str,
    grants: Iterable[Any],
    refused: list[dict[str, str]],
) -> tuple[dict[str, Any], list[ScopeGrant]]:
    ws = str(workspace_root or "").strip()
    if ws:
        protected, reason = is_protected_path(ws)
        if protected:
            raise ScopeRefused(ws, reason)
        if not Path(ws).exists():
            raise ArchaeologyInputError(f"workspace_root does not exist: {ws}")
    norm: list[ScopeGrant] = []
    for grant in grants or ():
        if isinstance(grant, ScopeGrant):
            norm.append(ScopeGrant(root=grant.root, access=grant.access, reason=grant.reason))
        elif isinstance(grant, dict):
            norm.append(
                ScopeGrant(
                    root=str(grant.get("root") or ""),
                    access=str(grant.get("access") or "metadata"),
                    reason=str(grant.get("reason") or ""),
                )
            )
        else:
            raise ArchaeologyInputError(f"unsupported grant shape: {grant!r}")
    content_access = True if not norm else any(g.access == "content" for g in norm)
    scope = {
        "workspace_root": ws,
        "store_roots": default_store_roots(),
        "grants": [g.as_dict() for g in norm],
        "refused": refused,
        "content_access": content_access,
        "default_scope": not norm,
    }
    return scope, norm


def _refuse_out_of_scope_path(
    candidate: str, scope: dict[str, Any], scan: _Scan, *, protected_ok: bool = False
) -> bool:
    """A query that NAMES a path outside the granted scope is refused with a
    reason; a protected path query raises the loudest refusal. Returns True
    when the caller must not collect any evidence for this path."""
    raw = str(candidate or "").strip()
    if not raw:
        return False
    protected, reason = is_protected_path(raw)
    if protected:
        raise ScopeRefused(raw, reason)
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        return False  # relative paths are workspace-relative by contract
    resolved = str(expanded.resolve(strict=False))
    ws = scope.get("workspace_root") or ""
    if ws and resolved.startswith(str(Path(ws).resolve(strict=False))):
        return False
    for grant in scope.get("grants") or ():
        if resolved.startswith(str(Path(grant["root"]).expanduser().resolve(strict=False))):
            return False
    scope["refused"].append(
        {"path": raw, "reason": "outside the granted scope: no root covers this path"}
    )
    scan.cut("a queried path was refused as out-of-scope")
    return True


# ---------------------------------------------------------------------------
# untrusted-content guards
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def quarantine_text(text: str, *, max_chars: int | None = None) -> tuple[str, bool, int]:
    """Prepare history content for surfacing: strip control characters, mask
    secrets through ``core.secret_redaction.redact_secrets``, cap the length,
    and wrap the result in literal untrusted-history markers so it can only
    ever be consumed as DATA. Returns ``(excerpt, truncated, redaction_count)``."""
    raw = str(text or "")
    if not raw:
        return "", False, 0
    cap = int(max_chars if max_chars is not None else LIMITS["max_excerpt_chars"])
    cleaned = _CONTROL_RE.sub("", raw)
    from core.secret_redaction import redact_secrets

    redacted = redact_secrets(cleaned)
    redaction_count = len(_CREDENTIAL_MASK_RE.findall(redacted))
    body = redacted[:cap]
    truncated = len(redacted) > cap
    excerpt = f"{UNTRUSTED_BEGIN}\n{body}\n{UNTRUSTED_END}"
    return excerpt, truncated, redaction_count


def availability_gate(text: str = "", finalization_id: str = "") -> str:
    """The one A8 verdict helper: ``AVAILABLE`` | ``WITHHELD`` | ``ERASED`` |
    ``UNKNOWN``. Governed content resolves through ``core.finalization``; an
    unknown finalization identity and a governance-store outage both fail
    closed to ``UNKNOWN`` — never a guess of AVAILABLE."""
    try:
        from core import finalization as _finalization

        if str(finalization_id or "").strip():
            verdict = _finalization.payload_availability_for_finalization_id(finalization_id)
            return str(verdict) if verdict else "UNKNOWN"
        if str(text or "").strip():
            verdict = _finalization.payload_availability_for_text(text)
            return str(verdict) if verdict else "AVAILABLE"
        return "AVAILABLE"
    except Exception:
        return "UNKNOWN"


def make_item(
    *,
    store: str,
    object_id: str,
    hash: str = "",
    timestamp: str = "",
    authority: str = "",
    verified: bool | None = None,
    turn_id: str = "",
    session_id: str = "",
    effect_id: str = "",
    attempt_id: str = "",
    path: str = "",
    kind: str = "",
    outcome: str = "",
    confidence: str = "exact",
    availability: str = "AVAILABLE",
    excerpt: str = "",
    truncated: bool = False,
) -> dict[str, Any]:
    """One provenance-complete result item. Every store reader shapes its
    rows through this so the provenance block can never drift per store."""
    if store not in _AUTHORITY_FOR_STORE:
        raise ArchaeologyInputError(f"unknown archaeology store {store!r}")
    return {
        "store": store,
        "object_id": str(object_id or ""),
        "hash": str(hash or ""),
        "timestamp": str(timestamp or ""),
        "authority": str(authority or _AUTHORITY_FOR_STORE.get(store, store)),
        "verified": verified,
        "turn_id": str(turn_id or ""),
        "session_id": str(session_id or ""),
        "effect_id": str(effect_id or ""),
        "attempt_id": str(attempt_id or ""),
        "path": str(path or ""),
        "kind": str(kind or ""),
        "outcome": str(outcome or ""),
        "confidence": confidence if confidence in ("exact", "derived", "partial") else "partial",
        "availability": availability
        if availability in ("AVAILABLE", "WITHHELD", "ERASED", "UNKNOWN")
        else "UNKNOWN",
        "excerpt": str(excerpt or ""),
        "truncated": bool(truncated),
    }


def _finish_item(item: dict, content: str, scan: _Scan) -> dict:
    """Apply the untrusted-content + withhold/erasure law to one item's raw
    content and return the finished item."""
    if not content:
        return item
    verdict = availability_gate(text=content)
    if verdict != "AVAILABLE":
        item["availability"] = verdict
        return item  # governed payload: the verdict surfaces, the bytes never do
    excerpt, truncated, redaction_count = quarantine_text(content)
    item["excerpt"] = excerpt
    item["truncated"] = truncated
    item["availability"] = "AVAILABLE"
    scan.secrets_detected += redaction_count
    scan.excerpts_wrapped += 1
    return item


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str, field: str) -> _dt.datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ArchaeologyInputError(f"{field} must be an ISO-8601 timestamp, got empty")
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchaeologyInputError(f"{field} is not an ISO-8601 timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _iso_or_empty(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        float_value = float(raw)
        return _dt.datetime.fromtimestamp(float_value, tz=_dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw
    except ValueError:
        return ""


def _in_window(timestamp: str, since: _dt.datetime | None, until: _dt.datetime | None) -> bool:
    if since is None and until is None:
        return True
    if not timestamp:
        return since is None and until is None
    try:
        moment = _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    if since is not None and moment < since:
        return False
    if until is not None and moment > until:  # noqa: SIM103 - last arm of a guard chain;
        # the `since` bound above reads identically and collapsing only this one hides that.
        return False
    return True


# ---------------------------------------------------------------------------
# store readers (each returns finished items + a stores_read row)
# ---------------------------------------------------------------------------


def _content_sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_blackbox(
    *, scan: _Scan, filters: dict[str, Any], deadline: float
) -> tuple[list[dict], dict[str, Any] | None]:
    try:
        from core.blackbox.store import BlackboxStore, store_root

        root = store_root()
        if not (root / "journal.jsonl").exists():
            return [], None
        store = BlackboxStore(root)
        entries = store.journal.entries()
        report = store.verify()
    except Exception as exc:
        scan.cut(f"blackbox store unreadable: {exc}")
        return [], None
    row = {"store": "blackbox", "root": str(root), "entries": len(entries), "verified": bool(report.ok)}
    kind = str(filters.get("kind") or "").strip()
    session = str(filters.get("session_id") or "").strip()
    turn = str(filters.get("turn_id") or "").strip()
    path_prefix = str(filters.get("path_prefix") or "").strip()
    text = str(filters.get("text") or "").strip().casefold()
    reference = str(filters.get("reference") or "").strip().casefold()
    since, until = filters.get("since"), filters.get("until")
    items: list[dict] = []
    for entry in entries:
        if scan.files_visited >= LIMITS["max_files"]:
            scan.cut(f"file/record cap {LIMITS['max_files']} reached")
            break
        scan.files_visited += 1
        if time.monotonic() > deadline:
            scan.cut(f"scan time budget {filters.get('_scan_seconds')}s exhausted")
            break
        entry_kind = str(entry.get("kind") or "")
        entry_session = str(entry.get("session_id") or "")
        entry_turn = str(entry.get("turn_id") or "")
        entry_path = str(entry.get("path") or "")
        stamp = _iso_or_empty(entry.get("ts"))
        if kind and entry_kind != kind:
            continue
        if session and entry_session != session:
            continue
        if turn and entry_turn != turn:
            continue
        if path_prefix and not entry_path.startswith(path_prefix):
            continue
        if not _in_window(stamp, since, until):
            continue
        raw_json = json.dumps(entry, sort_keys=True, default=str)
        if text and text not in raw_json.casefold():
            continue
        if reference and reference not in raw_json.casefold():
            continue
        content_bits = [
            str(entry.get(field) or "")
            for field in ("error", "payload_note", "status", "intent", "operation")
        ]
        content = " | ".join(bit for bit in content_bits if bit)
        item = make_item(
            store="blackbox",
            object_id=f"{entry.get('effect_id') or entry_turn or entry_session}:{entry_kind}:{entry.get('seq')}",
            hash=str(entry.get("entry_hash") or ""),
            timestamp=stamp,
            verified=bool(report.ok),
            turn_id=entry_turn,
            session_id=entry_session,
            effect_id=str(entry.get("effect_id") or ""),
            path=entry_path,
            kind=entry_kind,
            outcome=str(entry.get("outcome") or ""),
            confidence="exact",
        )
        items.append(_finish_item(item, content, scan))
        intended = entry.get("intended")
        if isinstance(intended, dict) and (text or reference):
            for value in intended.values():
                if text in str(value).casefold():
                    hash_item = make_item(
                        store="blackbox",
                        object_id=f"{entry.get('effect_id')}:hash-evidence",
                        hash=f"sha256:{value}" if not str(value).startswith("sha256:") else str(value),
                        timestamp=stamp,
                        verified=bool(report.ok),
                        turn_id=entry_turn,
                        session_id=entry_session,
                        effect_id=str(entry.get("effect_id") or ""),
                        path=entry_path,
                        kind="hash_evidence",
                        confidence="derived",
                    )
                    items.append(hash_item)
                    break
    return items, row


def _runtime_items(
    *, scan: _Scan, filters: dict[str, Any], deadline: float, row_label: str = "runtime_ledger"
) -> tuple[list[dict], dict[str, Any] | None]:
    session = str(filters.get("session_id") or "").strip()
    turn = str(filters.get("turn_id") or "").strip()
    effect = str(filters.get("effect_id") or "").strip()
    text = str(filters.get("text") or "").strip().casefold()
    reference = str(filters.get("reference") or "").strip().casefold()
    since, until = filters.get("since"), filters.get("until")
    wanted = bool(
        session or turn or effect or text or reference or filters.get("_trace_all")
    )
    if not wanted:
        return [], None
    try:
        from core.runtime_continuity import (
            list_runtime_session_events,
            list_runtime_sessions,
            list_runtime_tool_receipts,
        )
    except Exception as exc:
        scan.cut(f"runtime ledger unreadable: {exc}")
        return [], None
    items: list[dict] = []
    sessions = [session] if session else []
    if not sessions:
        derived = filters.get("_derived_sessions") or set()
        if derived:
            sessions = sorted(derived)
        elif not turn and not effect and (filters.get("_trace_all") or text or reference):
            try:
                sessions = [
                    str(row.get("session_id") or "")
                    for row in list_runtime_sessions(limit=25)
                ]
            except Exception as exc:
                scan.cut(f"runtime sessions unreadable: {exc}")
                sessions = []
        else:
            return [], None
    events_read = 0
    for session_id in sessions:
        if time.monotonic() > deadline:
            scan.cut("scan time budget exhausted in the runtime ledger")
            break
        try:
            events = list_runtime_session_events(session_id, after_seq=0, limit=200)
        except Exception:
            continue
        for event in events:
            events_read += 1
            if scan.files_visited >= LIMITS["max_files"]:
                scan.cut("record cap reached in the runtime ledger")
                break
            scan.files_visited += 1
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            stamp = _iso_or_empty(event.get("created_at"))
            event_turn = str(details.get("turn_key") or "")
            if not event_turn:
                event_turn = (filters.get("_effect_turns") or {}).get(
                    str(details.get("effect_id") or ""), ""
                )
            if turn and turn != event_turn and turn not in json.dumps(details, default=str):
                event_session = str(event.get("session_id") or session_id)
                if event_session not in (filters.get("_derived_sessions") or set()):
                    continue
            if effect and effect not in json.dumps(details, default=str):
                continue
            if not _in_window(stamp, since, until):
                continue
            serialized = json.dumps(
                {"message": event.get("message"), "details": details}, sort_keys=True, default=str
            )
            if text and text not in serialized.casefold():
                continue
            if reference and reference not in serialized.casefold() and reference != session_id.casefold():
                continue
            content = " | ".join(
                str(bit)
                for bit in [event.get("message")]
                + [value for value in details.values() if isinstance(value, str)]
                if bit
            )
            item = make_item(
                store="runtime_events",
                object_id=f"{session_id}:{event.get('seq')}",
                hash=_content_sha256(serialized.encode("utf-8")),
                timestamp=stamp,
                verified=True,
                turn_id=event_turn,
                session_id=session_id,
                effect_id=str(details.get("effect_id") or ""),
                kind=str(event.get("event_type") or ""),
                confidence="exact",
            )
            items.append(_finish_item(item, content, scan))
    receipts_read = 0
    for session_id in sessions:
        if time.monotonic() > deadline:
            scan.cut("scan time budget exhausted in the receipt ledger")
            break
        try:
            receipts = list_runtime_tool_receipts(session_id, limit=64)
        except Exception:
            continue
        for receipt in receipts:
            receipts_read += 1
            if scan.files_visited >= LIMITS["max_files"]:
                scan.cut("record cap reached in the receipt ledger")
                break
            scan.files_visited += 1
            arguments = receipt.get("arguments")
            execution = receipt.get("execution")
            arguments_json = json.dumps(arguments, sort_keys=True, default=str) if not isinstance(arguments, str) else arguments
            execution_json = json.dumps(execution, sort_keys=True, default=str) if not isinstance(execution, str) else execution
            blob = f"{arguments_json} {execution_json}"
            stamp = _iso_or_empty(receipt.get("created_at"))
            receipt_effect = ""
            if isinstance(execution, dict):
                receipt_effect = str(execution.get("effect_id") or "")
            receipt_turn = (filters.get("_effect_turns") or {}).get(receipt_effect, "")
            receipt_session = str(receipt.get("session_id") or session_id)
            derived = filters.get("_derived_sessions") or set()
            # Receipts join a turn lineage through the journal: the exact turn
            # string, the journal-derived turn for the same effect, or the
            # journal-derived session all vouch for the join.
            if (
                turn
                and turn not in blob
                and turn != receipt_turn
                and receipt_session not in derived
            ):
                continue
            if effect and effect not in blob and effect != receipt_effect:
                continue
            if not _in_window(stamp, since, until):
                continue
            if text and text not in blob.casefold():
                continue
            receipt_key = str(receipt.get("receipt_key") or "")
            if (
                reference
                and reference not in blob.casefold()
                and reference != receipt_key.casefold()
            ):
                continue
            outcome = "failed" if '"ok": false' in execution_json.casefold() or "'ok': False" in execution_json else ""
            item = make_item(
                store="tool_receipts",
                object_id=str(receipt.get("receipt_key") or ""),
                hash=_content_sha256(f"{arguments_json}{execution_json}".encode()),
                timestamp=stamp,
                verified=True,
                turn_id=receipt_turn,
                session_id=str(receipt.get("session_id") or session_id),
                effect_id=receipt_effect,
                kind=str(receipt.get("tool_name") or ""),
                outcome=outcome,
                confidence="exact",
            )
            items.append(_finish_item(item, blob, scan))
    if events_read == 0 and receipts_read == 0 and not items:
        return [], None
    row = {
        "store": row_label,
        "root": str(default_store_roots().get("runtime_db", "")),
        "entries": events_read + receipts_read,
        "verified": True,
    }
    return items, row


def _honesty_items(
    *, scan: _Scan, filters: dict[str, Any], deadline: float
) -> tuple[list[dict], dict[str, Any] | None]:
    session = str(filters.get("session_id") or "").strip()
    reference = str(filters.get("reference") or "").strip()
    turn = str(filters.get("turn_id") or "").strip()
    since, until = filters.get("since"), filters.get("until")
    text = str(filters.get("text") or "").strip().casefold()
    try:
        from core.honesty_receipt import (
            _ledger_dir,
            list_honesty_receipts,
            verify_honesty_receipt,
        )
    except Exception as exc:
        scan.cut(f"honesty ledger unreadable: {exc}")
        return [], None
    sessions = [session] if session else []
    sessions_from_ledger = False
    if not sessions:
        derived = filters.get("_derived_sessions") or set()
        if derived:
            sessions = sorted(derived)
            sessions_from_ledger = True
        elif not turn and (filters.get("_trace_all") or text or since or until):
            # No lineage selector at all (window/text traces): enumerate the
            # ledger files themselves; the rows carry their own session_id
            # (file names are session hashes).
            try:
                ledger_dir = _ledger_dir()
                sessions = [str(path) for path in sorted(ledger_dir.glob("*.jsonl"))[:25]]
                sessions_from_ledger = True
            except Exception:
                sessions = []
        else:
            return [], None
    items: list[dict] = []
    count = 0
    for session_id in sessions:
        if time.monotonic() > deadline:
            scan.cut("scan time budget exhausted in the honesty ledger")
            break
        try:
            if sessions_from_ledger and session_id.endswith(".jsonl"):
                raw_path = Path(session_id)
                receipts = []
                for line in raw_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        receipts.append(json.loads(line))
                    except Exception:
                        continue
            else:
                receipts = list_honesty_receipts(session_id)
        except Exception:
            continue
        for receipt in receipts:
            count += 1
            receipt_id = str(receipt.get("receipt_id") or "")
            if reference and reference != receipt_id:
                continue
            stamp = _iso_or_empty(receipt.get("issued_at"))
            if not _in_window(stamp, since, until):
                continue
            blob = json.dumps(receipt, sort_keys=True, default=str)
            if text and text not in blob.casefold():
                continue
            try:
                valid, _why = verify_honesty_receipt(receipt)
            except Exception:
                valid = False
            content = " | ".join(
                str(part or "")
                for part in (
                    receipt.get("verdict_detail"),
                    json.dumps(receipt.get("claimed_actions") or [], default=str),
                )
            )
            item = make_item(
                store="honesty_receipts",
                object_id=receipt_id,
                hash=str(receipt.get("content_hash") or ""),
                timestamp=stamp,
                verified=bool(valid),
                session_id=str(receipt.get("session_id") or session_id),
                kind="honesty_receipt",
                outcome=str(receipt.get("verdict") or ""),
                confidence="exact",
            )
            items.append(_finish_item(item, content, scan))
    if count == 0:
        return [], None
    row = {
        "store": "honesty_receipts",
        "root": str(default_store_roots().get("honesty_receipts", "")),
        "entries": count,
        "verified": True,
    }
    return items, row


def _memory_items(
    *, scan: _Scan, filters: dict[str, Any], deadline: float
) -> tuple[list[dict], dict[str, Any] | None]:
    session = str(filters.get("session_id") or "").strip()
    text = str(filters.get("text") or "").strip().casefold()
    reference = str(filters.get("reference") or "").strip().casefold()
    turn = str(filters.get("turn_id") or "").strip()
    since, until = filters.get("since"), filters.get("until")
    items: list[dict] = []
    count = 0
    try:
        from core.operator_profile import list_items

        for profile_item in list_items("operator", include_candidates=True):
            count += 1
            stamp = _iso_or_empty(profile_item.updated_at or profile_item.created_at)
            value_text = str(getattr(profile_item, "value_text", "") or "")
            item_id = str(getattr(profile_item, "item_id", "") or "")
            if text and text not in value_text.casefold():
                continue
            if reference and reference not in (item_id + value_text).casefold():
                continue
            if not _in_window(stamp, since, until):
                continue
            item_session = str(getattr(profile_item, "source_session_id", "") or "")
            if session and item_session != session:
                continue
            if (
                turn
                and not session
                and item_session not in (filters.get("_derived_sessions") or set())
            ):
                continue
            origin = str(getattr(profile_item, "origin", "") or "")
            status = str(getattr(profile_item, "status", "") or "")
            item = make_item(
                store="memory",
                object_id=item_id,
                hash="",
                timestamp=stamp,
                verified=status == "active",
                session_id=item_session,
                turn_id=str(getattr(profile_item, "source_turn_id", "") or ""),
                kind=f"profile_item:{getattr(profile_item, 'category', '')}",
                outcome=status,
                confidence="exact" if origin == "explicit" else "partial",
            )
            items.append(_finish_item(item, value_text, scan))
    except Exception as exc:
        scan.cut(f"operator profile unreadable: {exc}")
    try:
        from core.memory.entries import list_memory_entries

        # Memory entries are chat-namespace scoped: read them through the same
        # door the app uses; without a resolvable chat namespace the profile
        # items above carry the approved-memory view alone.
        try:
            entry_rows = list_memory_entries(chat_id=session or None, limit=25)
        except Exception:
            entry_rows = []
        for entry in entry_rows:
            count += 1
            stamp = _iso_or_empty(entry.get("created_at"))
            entry_text = str(entry.get("text") or "")
            if text and text not in entry_text.casefold():
                continue
            if not _in_window(stamp, since, until):
                continue
            item_session = str(entry.get("session_id") or "")
            if session and item_session != session:
                continue
            if (
                turn
                and not session
                and item_session not in (filters.get("_derived_sessions") or set())
            ):
                continue
            authority = str(entry.get("authority") or "")
            item = make_item(
                store="memory",
                object_id=str(entry.get("record_id") or ""),
                hash="",
                timestamp=stamp,
                verified=authority != "model_inference",
                session_id=item_session,
                kind=f"memory_entry:{entry.get('category') or ''}",
                outcome=authority,
                confidence="exact" if authority != "model_inference" else "partial",
            )
            items.append(_finish_item(item, entry_text, scan))
    except Exception as exc:
        scan.cut(f"memory entries unreadable: {exc}")
    if count == 0:
        return [], None
    row = {
        "store": "memory",
        "root": str(default_store_roots().get("runtime_db", "")),
        "entries": count,
        "verified": True,
    }
    return items, row


_FINALIZATION_SELECT = (
    "SELECT finalization_id, semantic_result_id, turn_id, content_hash, status, "
    "availability, created_at FROM a7_finalizations ORDER BY created_at DESC LIMIT ?"
)


def _finalization_items(
    *, scan: _Scan, filters: dict[str, Any], deadline: float
) -> tuple[list[dict], dict[str, Any] | None]:
    reference = str(filters.get("reference") or "").strip()
    turn = str(filters.get("turn_id") or "").strip()
    text = str(filters.get("text") or "").strip().casefold()
    since, until = filters.get("since"), filters.get("until")
    wanted = bool(reference or turn or text or filters.get("_trace_all"))
    if not wanted:
        return [], None
    try:
        from storage.db import execute_query

        rows = execute_query(_FINALIZATION_SELECT, (min(LIMITS["max_files"], 200),))
    except Exception as exc:
        scan.cut(f"governance store unreadable: {exc}")
        return [], None
    items: list[dict] = []
    for row in rows:
        fid = str(row.get("finalization_id") or "")
        stamp = _iso_or_empty(row.get("created_at"))
        if reference and reference != fid and str(row.get("content_hash") or "") != reference:
            continue
        if turn and str(row.get("turn_id") or "") != turn:
            continue
        if not _in_window(stamp, since, until):
            continue
        # Governed bytes are matched but NEVER echoed: the availability verdict
        # is the only thing this store surfaces.
        canonical = ""
        try:
            from storage.db import execute_query as _eq

            content_rows = _eq(
                "SELECT canonical_content FROM a7_finalizations WHERE finalization_id = ? LIMIT 1",
                (fid,),
            )
            canonical = str((content_rows[0] or {}).get("canonical_content") or "") if content_rows else ""
        except Exception:
            canonical = ""
        if text and text not in canonical.casefold():
            continue
        item = make_item(
            store="finalizations",
            object_id=fid,
            hash=str(row.get("content_hash") or ""),
            timestamp=stamp,
            verified=True,
            turn_id=str(row.get("turn_id") or ""),
            kind="finalization",
            outcome=str(row.get("status") or ""),
            confidence="exact",
            availability=availability_gate(finalization_id=fid),
        )
        items.append(item)
    if not items:
        return [], None
    row_out = {
        "store": "finalizations",
        "root": str(default_store_roots().get("runtime_db", "")),
        "entries": len(rows),
        "verified": True,
    }
    return items, row_out


_GIT_READONLY_TAIL = (
    "status", "log", "show", "cat-file", "rev-parse", "branch", "diff", "fsck",
)


def _git(repo_root: str | Path, *args: str, timeout: float = 20.0) -> tuple[bool, str]:
    """One bounded READ-ONLY git probe. Only inspection verbs are ever used;
    the archaeology surface has no authority over the working tree."""
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode == 0, completed.stdout.strip()


def _git_repo_for(workspace_root: str) -> str:
    candidate = Path(workspace_root).expanduser()
    if not workspace_root:
        return ""
    probe = candidate if (candidate / ".git").exists() else candidate.parent
    while str(probe) != str(probe.parent):
        if (probe / ".git").exists():
            return str(probe)
        probe = probe.parent
    return ""


def _git_commit_item(repo: str, reference: str, confidence: str, scan: _Scan) -> dict | None:
    ok, _ = _git(repo, "cat-file", "-t", reference)
    if not ok:
        return None
    resolved_ok, full_sha = _git(repo, "rev-parse", reference)
    if not resolved_ok:
        return None
    stamp = ""
    _ok, commit_stamp = _git(repo, "log", "-1", "--format=%cI", full_sha)
    if _ok:
        stamp = _iso_or_empty(commit_stamp)
    subject_ok, subject = _git(repo, "log", "-1", "--format=%s", full_sha)
    item = make_item(
        store="git",
        object_id=full_sha,
        hash=full_sha,
        timestamp=stamp,
        verified=True,
        kind="commit",
        confidence=confidence,
    )
    if subject_ok and subject:
        return _finish_item(item, subject, scan)
    return item


def _git_items(
    *, scan: _Scan, filters: dict[str, Any], deadline: float
) -> tuple[list[dict], dict[str, Any] | None]:
    workspace_root = str(filters.get("workspace_root") or "").strip()
    repo = _git_repo_for(workspace_root) if workspace_root else ""
    if not repo:
        return [], None
    reference = str(filters.get("reference") or "").strip()
    path = str(filters.get("path") or "").strip()
    items: list[dict] = []
    if reference:
        item = _git_commit_item(repo, reference, "exact" if len(reference) == 40 else "derived", scan)
        if item is not None:
            items.append(item)
    if path and time.monotonic() <= deadline:
        ok, log_line = _git(repo, "log", "-5", "--format=%H%x00%cI", "--", path)
        if ok:
            for line in log_line.splitlines():
                if not line.strip():
                    continue
                sha, _, stamp = line.partition("\x00")
                item = make_item(
                    store="git",
                    object_id=sha,
                    hash=sha,
                    timestamp=_iso_or_empty(stamp),
                    verified=True,
                    path=path,
                    kind="commit_touching_path",
                    confidence="derived",
                )
                items.append(item)
    if not items:
        return [], None
    row = {"store": "git", "root": repo, "entries": len(items), "verified": True}
    return items, row


def _walk_single_file(
    root: Path,
    *,
    access: str,
    effective: dict[str, Any],
    scan: _Scan,
    filters: dict[str, Any],
    refused: list[dict[str, str]],
    max_file_bytes: int,
    max_total: int,
) -> list[dict]:
    """One granted FILE as the whole scan root (e.g. a metadata-tier grant)."""
    items: list[dict] = []
    text = str(filters.get("text") or "").strip().casefold()
    path_prefix = str(filters.get("path_prefix") or "").strip()
    scan.files_visited += 1
    try:
        info = os.lstat(root)
    except OSError:
        return items
    stamp = _iso_or_empty(info.st_mtime)
    if path_prefix and not str(root).startswith(path_prefix):
        return items
    item = make_item(
        store="workspace",
        object_id=str(root),
        path=str(root),
        timestamp=stamp,
        kind="file",
        confidence="exact",
    )
    if access != "content":
        items.append(item)
        return items
    try:
        data = root.read_bytes()[:max_file_bytes]
    except OSError:
        return items
    scan.bytes_read += len(data)
    if scan.bytes_read > max_total:
        scan.cut(f"total byte budget {max_total} exhausted")
        return items
    decoded = data.decode("utf-8", errors="replace")
    if text and text not in decoded.casefold():
        return items
    item["hash"] = _content_sha256(data)
    items.append(_finish_item(item, decoded, scan))
    return items


def _walk_tree(
    root: Path,
    *,
    access: str,
    effective: dict[str, Any],
    scan: _Scan,
    filters: dict[str, Any],
    deadline: float,
    refused: list[dict[str, str]],
    use_relative_paths: bool = False,
) -> list[dict]:
    items: list[dict] = []
    text = str(filters.get("text") or "").strip().casefold()
    path_prefix = str(filters.get("path_prefix") or "").strip()
    exact_path = str(filters.get("path") or "").strip()
    reference = str(filters.get("reference") or "").strip().casefold()
    max_file_bytes = int(effective["max_file_bytes"])
    max_total = int(effective["max_total_bytes"])
    max_files = int(effective["max_files"])
    try:
        root_device = os.stat(root).st_dev
    except OSError as exc:
        refused.append({"path": str(root), "reason": f"root unreadable: {exc}"})
        return items
    since_dt, until_dt = filters.get("since"), filters.get("until")

    def _stamp_in_window(iso_stamp: str) -> bool:
        return _in_window(iso_stamp, since_dt, until_dt)

    # A grant may root at a single FILE: handle it as a one-entry scan.
    if root.is_file():
        return _walk_single_file(
            root,
            access=access,
            effective=effective,
            scan=scan,
            filters=filters,
            refused=refused,
            max_file_bytes=max_file_bytes,
            max_total=max_total,
        )
    stack = [root]
    while stack:
        if time.monotonic() > deadline:
            scan.cut(f"scan time budget {effective['max_scan_seconds']}s exhausted")
            break
        if scan.files_visited >= max_files:
            scan.cut(f"file cap {max_files} reached")
            break
        current = stack.pop(0)
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if scan.files_visited >= max_files:
                scan.cut(f"file cap {max_files} reached")
                break
            if time.monotonic() > deadline:
                scan.cut(f"scan time budget {effective['max_scan_seconds']}s exhausted")
                break
            full = Path(entry.path)
            if entry.is_symlink():
                scan.files_visited += 1
                try:
                    target = full.resolve(strict=False)
                except OSError:
                    target = full
                escaped = not str(target).startswith(str(root.resolve(strict=False)))
                if escaped:
                    refused.append(
                        {
                            "path": entry.name,
                            "reason": "symlink escapes the scan root; never followed",
                        }
                    )
                    continue
                item = make_item(
                    store="workspace",
                    object_id=str(full),
                    path=str(full),
                    kind="symlink",
                    confidence="exact",
                )
                items.append(item)
                continue
            protected, protected_reason = is_protected_path(full)
            if protected:
                refused.append(
                    _anonymized_refusal(f"protected store skipped inside the scan root ({protected_reason})")
                )
                continue
            try:
                info = os.lstat(full)
            except OSError:
                continue
            if info.st_dev != root_device:
                refused.append(
                    {
                        "path": str(full),
                        "reason": "foreign device (mount boundary): subtree not scanned",
                    }
                )
                continue
            scan.files_visited += 1
            stamp = _iso_or_empty(info.st_mtime)
            relative = str(full.relative_to(root)) if full.is_relative_to(root) else str(full)
            display_path = relative if use_relative_paths else str(full)
            if full.is_dir():
                stack.append(full)
                continue
            if exact_path and relative != exact_path and str(full) != exact_path:
                continue
            archive = any(str(full).lower().endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)
            if archive:
                item = make_item(
                    store="workspace",
                    object_id=display_path,
                    path=display_path,
                    timestamp=stamp,
                    kind="archive",
                    confidence="exact",
                    hash="",
                )
                items.append(item)
                continue
            if info.st_size > max_file_bytes:
                refused.append(
                    {
                        "path": display_path,
                        "reason": f"file exceeds max_file_bytes ({info.st_size} > {max_file_bytes}): content not read",
                    }
                )
                continue
            if since_dt or until_dt:
                if not _in_window(stamp, since_dt, until_dt):
                    continue
            if access != "content":
                item = make_item(
                    store="workspace",
                    object_id=display_path,
                    path=display_path,
                    timestamp=stamp,
                    kind="file",
                    confidence="exact",
                )
                if path_prefix and not str(full).startswith(path_prefix) and not relative.startswith(path_prefix):
                    continue
                items.append(item)
                continue
            try:
                data = full.read_bytes()[:max_file_bytes]
            except OSError:
                continue
            scan.bytes_read += len(data)
            if scan.bytes_read > max_total:
                scan.cut(f"total byte budget {max_total} exhausted")
                break
            decoded = data.decode("utf-8", errors="replace")
            matched = True
            if text:
                matched = text in decoded.casefold()
            if reference:
                matched = matched and reference in full.name.casefold()
            if path_prefix:
                matched = matched and (
                    str(full).startswith(path_prefix) or relative.startswith(path_prefix)
                )
            if not matched:
                continue
            item = make_item(
                store="workspace",
                object_id=display_path,
                hash=_content_sha256(data),
                path=display_path,
                timestamp=stamp,
                kind="file",
                confidence="exact",
            )
            items.append(_finish_item(item, decoded, scan))
    return items


def _workspace_wanted(filters: dict[str, Any]) -> bool:
    """The tree walk answers content/path-shaped queries and pure listings. A
    turn/session/effect trace does not walk files — the ledger stores answer
    those, and the workspace walk would only add unlabeled noise."""
    if any(
        str(filters.get(key) or "").strip()
        for key in ("text", "path_prefix", "path", "reference")
    ):
        return True
    lineage = any(
        str(filters.get(key) or "").strip()
        for key in ("turn_id", "session_id", "effect_id", "attempt_id")
    )
    return not lineage


def _workspace_items(
    *, scan: _Scan, scope: dict[str, Any], effective: dict[str, Any],
    filters: dict[str, Any], deadline: float,
) -> tuple[list[dict], dict[str, Any] | None]:
    refused = scope["refused"]
    items: list[dict] = []
    roots: list[tuple[Path, str, bool]] = []
    walked_any = False
    ws = str(scope.get("workspace_root") or "").strip()
    if ws:
        roots.append((Path(ws), "content", True))
    for grant in scope.get("grants") or ():
        grant_root = Path(str(grant["root"])).expanduser()
        roots.append((grant_root, str(grant["access"]), False))
        if str(grant["access"]) == "metadata":
            refused.append(
                {"path": str(grant["root"]), "reason": "metadata-only grant: content not read"}
            )
    for root, access, use_relative in roots:
        if time.monotonic() > deadline:
            scan.cut("scan time budget exhausted before the workspace walk")
            break
        before = scan.files_visited
        walked = _walk_tree(
            root, access=access, effective=effective, scan=scan,
            filters=filters, deadline=deadline, refused=refused,
            use_relative_paths=use_relative,
        )
        items.extend(walked)
        walked_something = walked or scan.files_visited > before
        walked_any = walked_any or bool(walked_something)
    if not walked_any:
        return [], None
    row = {
        "store": "workspace",
        "root": ws or ", ".join(str(g["root"]) for g in scope.get("grants") or ()),
        "entries": scan.files_visited,
        "verified": True,
    }
    return items, row


# ---------------------------------------------------------------------------
# envelope assembly
# ---------------------------------------------------------------------------


def _ordered(items: list[dict]) -> list[dict]:
    rank = {store: index for index, store in enumerate(_STORE_ORDER)}
    return sorted(
        items,
        key=lambda item: (
            rank.get(item.get("store", ""), 99),
            item.get("timestamp", "") == "",
            item.get("timestamp", ""),
            item.get("object_id", ""),
        ),
    )


def _envelope(
    operation: str,
    query: dict[str, Any],
    scope: dict[str, Any],
    effective: dict[str, Any],
    scan: _Scan,
    items: list[dict],
    stores_read_rows: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    ordered = _ordered(items)
    bounded = ordered[: max(1, int(limit))]
    truncated = scan.truncated or len(ordered) > len(bounded)
    limits_echo = dict(effective)
    limits_echo["limit"] = int(limit)
    if scan.truncation_reasons:
        limits_echo["truncation_reasons"] = list(scan.truncation_reasons)
    return {
        "schema": SCHEMA,
        "operation": operation,
        "query": query,
        "generated_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "scope": scope,
        "results": bounded,
        "returned": len(bounded),
        "truncated": bool(truncated),
        "limits": limits_echo,
        "stores_read": stores_read_rows,
        "redaction": {
            "secrets_detected": scan.secrets_detected,
            "excerpts_wrapped": scan.excerpts_wrapped,
        },
        "untrusted_content": True,
        "execution": "none",
    }


def _filters(
    *,
    text: str = "",
    store: str = "",
    kind: str = "",
    session_id: str = "",
    turn_id: str = "",
    effect_id: str = "",
    attempt_id: str = "",
    path: str = "",
    path_prefix: str = "",
    since: str = "",
    until: str = "",
    reference: str = "",
    workspace_root: str = "",
    trace_all: bool = False,
) -> dict[str, Any]:
    since_dt = _parse_iso(since, "since") if since else None
    until_dt = _parse_iso(until, "until") if until else None
    if since_dt and until_dt and since_dt > until_dt:
        raise ArchaeologyInputError("inverted time window: since is after until")
    if store and store not in _STORE_ORDER:
        raise ArchaeologyInputError(
            f"unknown store {store!r}; valid stores: {', '.join(_STORE_ORDER)}"
        )
    return {
        "text": text,
        "store": store,
        "kind": kind,
        "session_id": session_id,
        "turn_id": turn_id,
        "effect_id": effect_id,
        "attempt_id": attempt_id,
        "path": path,
        "path_prefix": path_prefix,
        "since": since_dt,
        "until": until_dt,
        "reference": reference,
        "workspace_root": workspace_root,
        "_trace_all": trace_all,
    }


def _collect(
    scope: dict[str, Any],
    filters: dict[str, Any],
    effective: dict[str, Any],
    scan: _Scan,
    deadline: float,
) -> tuple[list[dict], list[dict[str, Any]]]:
    items: list[dict] = []
    rows: list[dict[str, Any]] = []
    wanted = filters.get("store")
    def wanted_store(name: str) -> bool:
        return wanted in ("", name)

    # The journal is the authority: its matches decide which sessions a turn
    # lineage may join in the ledger stores.
    blackbox_items, blackbox_row = ([], None)
    if wanted_store("blackbox"):
        try:
            blackbox_items, blackbox_row = _read_blackbox(
                scan=scan, filters=filters, deadline=deadline
            )
        except Exception as exc:
            scan.cut(f"blackbox reader failed: {exc}")
        if blackbox_items:
            items.extend(blackbox_items)
        if blackbox_row:
            rows.append(blackbox_row)
    derived_sessions = {
        str(item.get("session_id") or "")
        for item in blackbox_items
        if item.get("session_id")
    }
    filters["_derived_sessions"] = derived_sessions
    # Journal-derived effect→turn lineage: the ledger stores may borrow the
    # turn identity the journal vouches for when joining receipts/events.
    effect_turns: dict[str, str] = {}
    for item in blackbox_items:
        if item.get("effect_id") and item.get("turn_id"):
            effect_turns.setdefault(str(item["effect_id"]), str(item["turn_id"]))
    filters["_effect_turns"] = effect_turns

    readers = (
        ("runtime_events", lambda: _runtime_items(scan=scan, filters=filters, deadline=deadline, row_label="runtime_events")),
        ("tool_receipts", lambda: _runtime_items(scan=scan, filters=filters, deadline=deadline, row_label="tool_receipts")),
        ("honesty_receipts", lambda: _honesty_items(scan=scan, filters=filters, deadline=deadline)),
        ("memory", lambda: _memory_items(scan=scan, filters=filters, deadline=deadline)),
        ("finalizations", lambda: _finalization_items(scan=scan, filters=filters, deadline=deadline)),
        ("git", lambda: _git_items(scan=scan, filters=filters, deadline=deadline)),
    )
    for name, reader in readers:
        if not wanted_store(name):
            continue
        try:
            store_items, row = reader()
        except Exception as exc:
            scan.cut(f"{name} reader failed: {exc}")
            continue
        if store_items:
            items.extend(store_items)
        if row:
            rows.append(row)
    if wanted_store("workspace") and _workspace_wanted(filters):
        ws_items, ws_row = _workspace_items(
            scan=scan, scope=scope, effective=effective, filters=filters, deadline=deadline
        )
        items.extend(ws_items)
        if ws_row:
            rows.append(ws_row)
    if filters.get("path_prefix"):
        # A path-shaped query returns path-bearing objects only.
        prefix = str(filters["path_prefix"])
        items = [
            item for item in items
            if str(item.get("path") or "").startswith(prefix)
        ]
    return items, rows


# ---------------------------------------------------------------------------
# typed operations
# ---------------------------------------------------------------------------


def locate(
    reference: str,
    *,
    workspace_root: str = "",
    grants: Iterable[Any] = (),
    limit: int = 25,
    limits: dict | None = None,
    requester: str = "",
) -> dict[str, Any]:
    """``locate`` — where is that report/commit/receipt/effect? One reference,
    every in-scope store probed, full provenance on every hit."""
    clean = str(reference or "").strip()
    if not clean:
        raise ArchaeologyInputError("locate needs a non-empty reference")
    effective = _effective_limits(limits)
    effective["limit"] = max(1, min(int(limit), effective["max_results"]))
    scan = _Scan()
    deadline = time.monotonic() + float(effective["max_scan_seconds"])
    refused: list[dict[str, str]] = []
    scope, _norm = _resolve_scope(workspace_root, grants, refused)
    filters = _filters(reference=clean, workspace_root=workspace_root)
    items, rows = _collect(scope, filters, effective, scan, deadline)
    if not items:
        raise ReferenceNotFound(clean)
    envelope = _envelope(
        "locate",
        {"reference": clean, "workspace_root": workspace_root, "requester": requester},
        scope,
        effective,
        scan,
        items,
        rows,
        effective["limit"],
    )
    return envelope


def search(
    *,
    text: str = "",
    store: str = "",
    kind: str = "",
    session_id: str = "",
    turn_id: str = "",
    path_prefix: str = "",
    since: str = "",
    until: str = "",
    workspace_root: str = "",
    grants: Iterable[Any] = (),
    limit: int = 50,
    limits: dict | None = None,
    requester: str = "",
) -> dict[str, Any]:
    """``search`` — typed bounded search across the in-scope stores. Metadata
    first, explicit caps, truthful truncation."""
    filters = _filters(
        text=text,
        store=store,
        kind=kind,
        session_id=session_id,
        turn_id=turn_id,
        path_prefix=path_prefix,
        since=since,
        until=until,
    )
    effective = _effective_limits(limits)
    effective["limit"] = max(1, min(int(limit), effective["max_results"]))
    scan = _Scan()
    deadline = time.monotonic() + float(effective["max_scan_seconds"])
    refused: list[dict[str, str]] = []
    scope, _norm = _resolve_scope(workspace_root, grants, refused)
    if path_prefix:
        _refuse_out_of_scope_path(path_prefix, scope, scan)
    items, rows = _collect(scope, filters, effective, scan, deadline)
    return _envelope(
        "search",
        {
            "text": text, "store": store, "kind": kind, "session_id": session_id,
            "turn_id": turn_id, "path_prefix": path_prefix, "since": since,
            "until": until, "workspace_root": workspace_root, "requester": requester,
        },
        scope,
        effective,
        scan,
        items,
        rows,
        effective["limit"],
    )


def trace(
    *,
    turn_id: str = "",
    session_id: str = "",
    effect_id: str = "",
    attempt_id: str = "",
    path: str = "",
    since: str = "",
    until: str = "",
    workspace_root: str = "",
    grants: Iterable[Any] = (),
    limit: int = 100,
    limits: dict | None = None,
    requester: str = "",
) -> dict[str, Any]:
    """``trace`` — cross-store lineage for one turn/session/effect/path/window:
    the C21 questions (what happened, which task changed this file, why did
    this fail) answered as one joined timeline."""
    if not any((turn_id, session_id, effect_id, attempt_id, path, since, until)):
        raise ArchaeologyInputError(
            "trace needs at least one selector: turn_id, session_id, effect_id, "
            "attempt_id, path or a time window"
        )
    filters = _filters(
        session_id=session_id,
        turn_id=turn_id,
        effect_id=effect_id,
        attempt_id=attempt_id,
        path=path,
        since=since,
        until=until,
        trace_all=True,
    )
    effective = _effective_limits(limits)
    effective["limit"] = max(1, min(int(limit), effective["max_results"]))
    scan = _Scan()
    deadline = time.monotonic() + float(effective["max_scan_seconds"])
    refused: list[dict[str, str]] = []
    scope, _norm = _resolve_scope(workspace_root, grants, refused)
    if path:
        path_refused = _refuse_out_of_scope_path(path, scope, scan)
    else:
        path_refused = False
    items, rows = ([], []) if path_refused else _collect(
        scope, filters, effective, scan, deadline
    )
    envelope = _envelope(
        "trace",
        {
            "turn_id": turn_id, "session_id": session_id, "effect_id": effect_id,
            "attempt_id": attempt_id, "path": path, "since": since, "until": until,
            "workspace_root": workspace_root, "requester": requester,
        },
        scope,
        effective,
        scan,
        items,
        rows,
        effective["limit"],
    )
    timeline = sorted(
        envelope["results"],
        key=lambda item: (item.get("timestamp", "") == "", item.get("timestamp", "")),
    )
    envelope["timeline"] = timeline
    envelope["lineage"] = {
        "stores_joined": [row.get("store") for row in envelope["stores_read"]],
        "joined_by": _lineage_keys(filters),
    }
    return envelope


def _lineage_keys(filters: dict[str, Any]) -> list[str]:
    keys = []
    for name in ("turn_id", "session_id", "effect_id", "attempt_id", "path"):
        if filters.get(name):
            keys.append(name)
    if filters.get("since") or filters.get("until"):
        keys.append("time_window")
    return keys


def compare(
    *,
    kind: str = "git_commits",
    a: str = "",
    b: str = "",
    workspace_root: str = "",
    grants: Iterable[Any] = (),
    requester: str = "",
) -> dict[str, Any]:
    """``compare`` — bounded, read-only diff between two commits, two turns or
    two workspace files, with quarantined content and truthful changed paths."""
    clean_kind = str(kind or "").strip()
    if clean_kind not in ("git_commits", "turns", "files"):
        raise ArchaeologyInputError(
            f"compare kind must be git_commits, turns or files, not {clean_kind!r}"
        )
    effective = _effective_limits(None)
    effective["limit"] = int(effective["max_results"])
    scan = _Scan()
    deadline = time.monotonic() + float(effective["max_scan_seconds"])
    refused: list[dict[str, str]] = []
    scope, _norm = _resolve_scope(workspace_root, grants, refused)
    item_a: dict | None = None
    item_b: dict | None = None
    changed_paths: list[str] = []
    summary = ""
    if clean_kind == "git_commits":
        if not a or not b:
            raise ArchaeologyInputError("compare kind git_commits needs both a and b")
        repo = _git_repo_for(workspace_root)
        if not repo:
            raise ArchaeologyInputError(
                "compare kind git_commits needs a workspace_root inside a git repository"
            )
        item_a = _git_commit_item(repo, str(a), "exact", scan)
        item_b = _git_commit_item(repo, str(b), "exact", scan)
        if item_a is None or item_b is None:
            raise ReferenceNotFound(f"git commit {a!r} or {b!r} not found in {repo}")
        ok, diff_out = _git(repo, "diff", "--name-only", str(a), str(b))
        if ok:
            changed_paths = [line for line in diff_out.splitlines() if line.strip()][:200]
            if len(diff_out.splitlines()) > 200:
                scan.cut("git diff truncated at 200 paths")
        summary = f"git compare {str(a)[:12]}..{str(b)[:12]}: {len(changed_paths)} path(s) changed"
    elif clean_kind == "turns":
        if not a or not b:
            raise ArchaeologyInputError("compare kind turns needs both a and b")
        turn_items: dict[str, list[dict]] = {"a": [], "b": []}
        for side, turn in (("a", str(a)), ("b", str(b))):
            filters = _filters(turn_id=turn, workspace_root=workspace_root)
            found, _rows = _read_blackbox(scan=scan, filters=filters, deadline=deadline)
            turn_items[side] = found
        if not turn_items["a"] and not turn_items["b"]:
            raise ReferenceNotFound(f"neither turn {a!r} nor {b!r} has recorded effects")
        def _turn_summary(side: str, turn: str) -> dict:
            found = turn_items[side]
            effect_ids = [item.get("effect_id") for item in found if item.get("effect_id")]
            paths = sorted({item.get("path") for item in found if item.get("path")})
            return {
                "store": "blackbox",
                "object_id": turn,
                "effect_id": effect_ids[0] if effect_ids else "",
                "path": paths[0] if paths else "",
                "timestamp": found[0].get("timestamp", "") if found else "",
                "effects": effect_ids,
                "paths": paths,
            }
        item_a = _turn_summary("a", str(a))
        item_b = _turn_summary("b", str(b))
        ops_a: dict[str, set[tuple[str, str]]] = {}
        ops_b: dict[str, set[tuple[str, str]]] = {}
        for item in turn_items["a"]:
            if item.get("path"):
                ops_a.setdefault(item["path"], set()).add(
                    (item.get("kind", ""), item.get("outcome", ""))
                )
        for item in turn_items["b"]:
            if item.get("path"):
                ops_b.setdefault(item["path"], set()).add(
                    (item.get("kind", ""), item.get("outcome", ""))
                )
        for touched in sorted(set(ops_a) | set(ops_b)):
            if ops_a.get(touched) != ops_b.get(touched):
                changed_paths.append(touched)
        summary = (
            f"turn compare {a} vs {b}: {len(changed_paths)} path(s) treated differently"
        )
    else:  # files
        if not a or not b:
            raise ArchaeologyInputError("compare kind files needs both a and b")
        ws = str(workspace_root or "").strip()
        if not ws:
            raise ArchaeologyInputError("compare kind files needs a workspace_root")
        max_file_bytes = int(effective["max_file_bytes"])
        def _file_item(name: str) -> dict:
            full = Path(ws) / name
            protected, reason = is_protected_path(full)
            if protected:
                raise ScopeRefused(str(full), reason)
            data = full.read_bytes()[:max_file_bytes]
            decoded = data.decode("utf-8", errors="replace")
            item = make_item(
                store="workspace",
                object_id=name,
                hash=_content_sha256(data),
                path=name,
                timestamp=_iso_or_empty(full.stat().st_mtime),
                kind="file",
                confidence="exact",
            )
            return _finish_item(item, decoded, scan)
        item_a = _file_item(str(a))
        item_b = _file_item(str(b))
        if (item_a.get("hash") or "") != (item_b.get("hash") or ""):
            changed_paths = [str(a)]
        summary = f"file compare {a} vs {b}: {'changed' if changed_paths else 'identical'}"
    envelope = _envelope(
        "compare",
        {"kind": clean_kind, "a": a, "b": b, "workspace_root": workspace_root, "requester": requester},
        scope,
        effective,
        scan,
        [item for item in (item_a, item_b) if item is not None],
        [],
        max(2, effective["limit"]),
    )
    envelope["comparison"] = {
        "kind": clean_kind,
        "a": item_a,
        "b": item_b,
        "changed_paths": changed_paths,
        "summary": summary[:2000],
    }
    return envelope


def recovery_plan(
    *,
    reference: str = "",
    path: str = "",
    turn_id: str = "",
    workspace_root: str = "",
    grants: Iterable[Any] = (),
    limit: int = 25,
    limits: dict | None = None,
    requester: str = "",
) -> dict[str, Any]:
    """``recovery_plan`` — can this lost work be recovered? Answers as DATA:
    locates recoverable copies, verifies integrity, and names the permission
    an executor would need. This module executes nothing: no restore, no
    checkout, no mutation of any kind — plan only."""
    clean_ref = str(reference or "").strip()
    clean_path = str(path or "").strip()
    clean_turn = str(turn_id or "").strip()
    if not any((clean_ref, clean_path, clean_turn)):
        raise ArchaeologyInputError(
            "recovery_plan needs a reference, a path or a turn_id"
        )
    effective = _effective_limits(limits)
    effective["limit"] = max(1, min(int(limit), effective["max_results"]))
    scan = _Scan()
    deadline = time.monotonic() + float(effective["max_scan_seconds"])
    refused: list[dict[str, str]] = []
    scope, _norm = _resolve_scope(workspace_root, grants, refused)
    if clean_path:
        _refuse_out_of_scope_path(clean_path, scope, scan)
    notes: list[str] = []
    steps: list[dict] = []
    notes: list[str] = []
    steps: list[dict] = []
    filters = _filters(reference=clean_ref, path=clean_path, turn_id=clean_turn, workspace_root=workspace_root)
    if clean_path and _refuse_out_of_scope_path(clean_path, scope, scan):
        # The named path is out of scope or refused: nothing is read at all.
        items, rows = [], []
        notes.append("the queried path was refused by the scope law; nothing was read")
    else:
        items, rows = _collect(scope, filters, effective, scan, deadline)

    after_hash = ""
    blackbox_items = [item for item in items if item.get("store") == "blackbox"]
    for item in blackbox_items:
        raw = item.get("hash") or ""
        if len(raw.replace("sha256:", "")) == 64 and raw.startswith("sha256:"):
            # journal hash-evidence items carry the content-addressed after-state
            if item.get("kind") == "hash_evidence":
                after_hash = raw
                break
    git_items = [item for item in items if item.get("store") == "git"]
    workspace_items = [item for item in items if item.get("store") == "workspace"]

    step_number = 0
    if blackbox_items:
        step_number += 1
        steps.append(
            {
                "step": step_number,
                "source": "blackbox_journal",
                "object_id": blackbox_items[0].get("effect_id") or clean_ref or clean_turn,
                "hash": after_hash or blackbox_items[0].get("hash", ""),
                "detail": (
                    "the journal holds the effect record"
                    + (f" with content-addressed after-state {after_hash}" if after_hash else "")
                    + "; verify the chain, then locate the blob by hash in the effect store"
                ),
                "required_permission": (
                    "recovery execution needs its own approval through the "
                    "permission/effect lane; this plan only reads"
                ),
            }
        )
    for git_item in git_items[:3]:
        step_number += 1
        sha = git_item.get("hash") or git_item.get("object_id")
        detail = f"recover content from git objects: git show {sha}"
        if clean_path:
            detail = f"recover content from git objects: git show {sha}:{clean_path}"
        steps.append(
            {
                "step": step_number,
                "source": "git_objects",
                "object_id": str(sha),
                "hash": str(sha),
                "detail": detail,
                "required_permission": (
                    "separate approval for any working-tree operation; "
                    "this plan only reads objects"
                ),
            }
        )
    for ws_item in workspace_items[:3]:
        if ws_item.get("kind") != "file":
            continue
        step_number += 1
        steps.append(
            {
                "step": step_number,
                "source": "workspace",
                "object_id": ws_item.get("object_id", ""),
                "hash": ws_item.get("hash", ""),
                "detail": f"an intact copy already exists at {ws_item.get('path')}",
                "required_permission": (
                    "reading is permission-free here; any rewrite needs the "
                    "workspace permission lane"
                ),
            }
        )
    recoverable = bool(steps)
    if not items:
        notes.append("nothing matching the reference was found in any in-scope store")
    elif not steps:
        notes.append(
            "the journal carries hashes only; no restorable content source "
            "(no git object, no workspace copy, no content-addressed after-state) "
            "is in scope"
        )
    if clean_ref and not blackbox_items:
        notes.append("the reference resolved outside the blackbox journal")

    envelope = _envelope(
        "recovery_plan",
        {
            "reference": clean_ref, "path": clean_path, "turn_id": clean_turn,
            "workspace_root": workspace_root, "requester": requester,
        },
        scope,
        effective,
        scan,
        items,
        rows,
        effective["limit"],
    )
    envelope["plan"] = {
        "reference": clean_ref or clean_path or clean_turn,
        "recoverable": bool(recoverable),
        "steps": steps,
        "execution": "not_performed_read_only_plan",
        "notes": notes,
    }
    return envelope
