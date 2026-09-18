"""SQLite persistence for the Social Content Manager (P1, 2026-09-04).

Follows the storage/*_store.py convention: thin wrapper over storage.db with
its own tables (the media_project_store pattern — idempotent ``CREATE TABLE IF
NOT EXISTS`` schema, no migration-ledger entry). Records are JSON docs keyed by
record id and scoped by project; draft versions and decisions are INSERT-ONLY
(immutable at decision boundaries). This is a feature store, NOT a second
memory authority: it holds campaign/content state only.

Laws enforced here, not in callers:

- every persisted text is privacy-scanned first; a high-confidence secret
  refuses the write with NOTHING stored (``SecretRefusal``);
- draft versions and decisions are immutable: no UPDATE path exists for them;
- approval binds (draft_id, version, content_hash, platform): a changed byte,
  version or platform simply has no covering decision row — ``approval_for``
  answers None, so editing after approval invalidates approval by construction;
- campaign/brief updates carry a revision CAS (``RevisionConflict``);
- archive is a status change, never a DELETE; restore is explicit;
- ids, timestamps and hashes are computed here — callers cannot supply
  success/result identity fields;
- list results are bounded (<= 200).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from storage.db import execute_query, get_connection

MAX_LIMIT = 200
MAX_TEXT_CHARS = 20_000
MAX_LIST_DEFAULT = 50

_KINDS = (
    "campaign", "brief", "trend_observation", "idea",
    "draft", "decision", "handoff", "metric_observation", "hypothesis",
)

MANAGED_PLATFORMS = ("x", "linkedin", "bluesky", "mastodon", "threads", "telegram", "discord")
DECISION_KINDS = ("approved", "refused", "needs_changes", "NO_POST")
PRESERVATION_MODES = ("verbatim", "meaning_locked", "inspiration_only")
SOURCE_CLASSES = (
    "personal_feed", "platform_trending", "x_search", "named_account", "external_news",
)
METRIC_SOURCES = ("operator_entered", "file_import", "connector_observed")
HYPOTHESIS_OUTCOMES = ("open", "supported", "contradicted", "inconclusive", "expired")

# Privacy-risk codes that mean "high-confidence secret" — these refuse the write.
_HARD_SECRET_RISKS = frozenset(
    {"secret_assignment", "openai_key", "github_token", "aws_access_key", "slack_token"}
)


class SecretRefusal(Exception):
    """A high-confidence secret was found; nothing was written."""

    code = "VOOL_E_SOCIAL_CONTENT_SECRET_REFUSAL"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"secret refusal on field {field!r}: {reason}")
        self.field = field
        self.reason = reason


class RevisionConflict(Exception):
    """Lost-update guard: the stored revision moved on."""

    code = "VOOL_E_SOCIAL_CONTENT_REVISION_CONFLICT"


class UnknownRecord(Exception):
    code = "VOOL_E_SOCIAL_CONTENT_UNKNOWN_RECORD"


class ValidationError(Exception):
    code = "VOOL_E_SOCIAL_CONTENT_VALIDATION"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS social_content_records (
    record_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_social_content_scope
    ON social_content_records (project_id, kind, archived, updated_at DESC);
CREATE TABLE IF NOT EXISTS social_content_draft_versions (
    draft_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    doc TEXT NOT NULL,
    PRIMARY KEY (draft_id, version)
);
CREATE TABLE IF NOT EXISTS social_content_decisions (
    decision_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    draft_version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    platform TEXT NOT NULL,
    decided_at REAL NOT NULL,
    decision TEXT NOT NULL,
    doc TEXT NOT NULL
);
"""


def _ensure_schema() -> None:
    get_connection().executescript(_SCHEMA)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _hash_bytes(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _privacy_refusal(field: str, text: str) -> None:
    """Refuse high-confidence secrets outright; softer risks are returned as findings."""
    from core.privacy_guard import text_privacy_risks
    from core.secret_redaction import contains_secret

    value = str(text or "")
    if contains_secret(value):
        raise SecretRefusal(field, "high-confidence secret detected")
    risks = text_privacy_risks(value)
    hard = sorted(_HARD_SECRET_RISKS & set(risks))
    if hard:
        raise SecretRefusal(field, f"privacy risks: {hard}")


def privacy_findings(text: str) -> list[str]:
    """Soft privacy findings (email, path, identity...) — surfaced for review, not refused."""
    from core.privacy_guard import text_privacy_risks

    return sorted(set(text_privacy_risks(str(text or ""))) - _HARD_SECRET_RISKS)


def _bounded_text(field: str, text: str) -> str:
    value = str(text or "")
    if len(value) > MAX_TEXT_CHARS:
        raise ValidationError(f"field {field!r} exceeds {MAX_TEXT_CHARS} chars")
    return value


def _check_choice(field: str, value: str, choices: tuple[str, ...]) -> str:
    v = str(value or "").strip().lower()
    if v not in choices:
        raise ValidationError(f"field {field!r} must be one of {choices}, got {value!r}")
    return v


# ---------------------------------------------------------------- campaigns


def create_campaign(
    *, project_id: str, objective: str, audience: str,
    platforms: tuple[str, ...] = (), timeframe: dict[str, str] | None = None,
    guardrails: tuple[str, ...] = (), operator: str = "operator",
) -> dict[str, Any]:
    project_id = _bounded_text("project_id", project_id).strip()
    if not project_id:
        raise ValidationError("project_id is required")
    _privacy_refusal("objective", objective)
    _privacy_refusal("audience", audience)
    for plat in platforms:
        _check_choice("platforms", plat, MANAGED_PLATFORMS)
    for rail in guardrails:
        _privacy_refusal("guardrails", rail)
    now = time.time()
    doc = {
        "kind": "campaign",
        "campaign_id": _new_id("camp"),
        "project_id": project_id,
        "objective": _bounded_text("objective", objective),
        "audience": _bounded_text("audience", audience),
        "platforms": sorted(set(platforms)),
        "timeframe": dict(timeframe or {}),
        "guardrails": [_bounded_text("guardrails", g) for g in guardrails],
        "status": "draft",
        "operator": operator,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    _put_record(doc)
    return doc


def update_campaign(
    campaign_id: str, *, expected_version: int,
    objective: str | None = None, audience: str | None = None,
    platforms: tuple[str, ...] | None = None, status: str | None = None,
    guardrails: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    doc = get_campaign(campaign_id)
    if doc is None:
        raise UnknownRecord(campaign_id)
    if int(doc["version"]) != int(expected_version):
        raise RevisionConflict(
            f"campaign {campaign_id} is at version {doc['version']}, expected {expected_version}"
        )
    if objective is not None:
        _privacy_refusal("objective", objective)
        doc["objective"] = _bounded_text("objective", objective)
    if audience is not None:
        _privacy_refusal("audience", audience)
        doc["audience"] = _bounded_text("audience", audience)
    if platforms is not None:
        for plat in platforms:
            _check_choice("platforms", plat, MANAGED_PLATFORMS)
        doc["platforms"] = sorted(set(platforms))
    if status is not None:
        _check_choice("status", status, ("draft", "active", "paused", "archived"))
        doc["status"] = status
    if guardrails is not None:
        for rail in guardrails:
            _privacy_refusal("guardrails", rail)
        doc["guardrails"] = [_bounded_text("guardrails", g) for g in guardrails]
    doc["version"] = int(doc["version"]) + 1
    doc["updated_at"] = time.time()
    _put_record(doc)
    return doc


def get_campaign(campaign_id: str) -> dict[str, Any] | None:
    return _get_record("campaign", campaign_id)


def list_campaigns(project_id: str, *, limit: int = MAX_LIST_DEFAULT,
                   include_archived: bool = False) -> list[dict[str, Any]]:
    return _list_kind(project_id, "campaign", limit=limit, include_archived=include_archived)


def archive_campaign(campaign_id: str, *, expected_version: int) -> dict[str, Any]:
    return update_campaign(campaign_id, expected_version=expected_version, status="archived")


def restore_campaign(campaign_id: str, *, expected_version: int) -> dict[str, Any]:
    return update_campaign(campaign_id, expected_version=expected_version, status="paused")


# ---------------------------------------------------------------- briefs / trends / ideas


def create_brief(
    *, campaign_id: str, purpose: str, thesis: str,
    evidence_refs: tuple[str, ...] = (), requested_channels: tuple[str, ...] = (),
    content_mode: str = "evergreen", voice_scope: str = "none",
    deadline: str = "", constraints: tuple[str, ...] = (),
) -> dict[str, Any]:
    _privacy_refusal("purpose", purpose)
    _privacy_refusal("thesis", thesis)
    for c in constraints:
        _privacy_refusal("constraints", c)
    _check_choice("content_mode", content_mode, ("evergreen", "timely", "reactive"))
    for ch in requested_channels:
        _check_choice("requested_channels", ch, MANAGED_PLATFORMS)
    now = time.time()
    doc = {
        "kind": "brief",
        "brief_id": _new_id("brief"),
        "project_id": _project_of_campaign(campaign_id),
        "campaign_id": campaign_id,
        "purpose": _bounded_text("purpose", purpose),
        "thesis": _bounded_text("thesis", thesis),
        "evidence_refs": list(evidence_refs),
        "requested_channels": sorted(set(requested_channels)),
        "content_mode": content_mode,
        "voice_scope": voice_scope or "none",
        "deadline": _bounded_text("deadline", deadline),
        "constraints": [_bounded_text("constraints", c) for c in constraints],
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    _put_record(doc)
    return doc


def get_brief(brief_id: str) -> dict[str, Any] | None:
    return _get_record("brief", brief_id)


def list_briefs(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    return _list_kind(project_id, "brief", limit=limit)


def add_trend_observation(
    *, project_id: str, source_class: str, observed_text: str,
    link: str = "", account: str = "", session_scope: str = "public",
    corroboration: str = "uncorroborated", expires_at: float | None = None,
    relevance_note: str = "", region: str = "", language: str = "",
) -> dict[str, Any]:
    _check_choice("source_class", source_class, SOURCE_CLASSES)
    _privacy_refusal("observed_text", observed_text)
    _privacy_refusal("relevance_note", relevance_note)
    _privacy_refusal("link", link)
    _privacy_refusal("account", account)
    doc = {
        "kind": "trend_observation",
        "observation_id": _new_id("trend"),
        "project_id": _bounded_text("project_id", project_id).strip(),
        "source_class": source_class,
        "observed_text": _bounded_text("observed_text", observed_text),
        "link": _bounded_text("link", link),
        "account": _bounded_text("account", account),
        "region": region,
        "language": language,
        "session_scope": session_scope or "public",
        "corroboration": corroboration or "uncorroborated",
        "observed_at": time.time(),
        "expires_at": expires_at,
        "relevance_note": _bounded_text("relevance_note", relevance_note),
        "saved": True,
    }
    _put_record(doc)
    return doc


def list_trend_observations(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    return _list_kind(project_id, "trend_observation", limit=limit)


def idea_dedup_signature(angle: str, target_reader: str, evidence_ids: tuple[str, ...]) -> str:
    norm = re.sub(r"\s+", " ", f"{angle}|{target_reader}|{sorted(evidence_ids)}").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def add_idea(
    *, campaign_id: str, angle: str, why_now: str, target_reader: str,
    evidence_ids: tuple[str, ...] = (), expected_action: str = "",
    risk_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    _privacy_refusal("angle", angle)
    _privacy_refusal("why_now", why_now)
    _privacy_refusal("target_reader", target_reader)
    project_id = _project_of_campaign(campaign_id)
    signature = idea_dedup_signature(angle, target_reader, evidence_ids)
    existing = list_ideas(project_id, limit=MAX_LIMIT, include_archived=True)
    for other in existing:
        if other.get("dedup_signature") == signature and other.get("campaign_id") == campaign_id:
            raise ValidationError(
                f"duplicate idea signature {signature[:12]} already recorded "
                f"as {other['idea_id']}"
            )
    doc = {
        "kind": "idea",
        "idea_id": _new_id("idea"),
        "project_id": project_id,
        "campaign_id": campaign_id,
        "angle": _bounded_text("angle", angle),
        "why_now": _bounded_text("why_now", why_now),
        "target_reader": _bounded_text("target_reader", target_reader),
        "evidence_ids": list(evidence_ids),
        "expected_action": _bounded_text("expected_action", expected_action),
        "risk_flags": list(risk_flags),
        "dedup_signature": signature,
        "status": "candidate",
        "created_at": time.time(),
    }
    _put_record(doc)
    return doc


def set_idea_status(idea_id: str, status: str) -> dict[str, Any]:
    _check_choice("idea status", status,
                  ("candidate", "rejected_duplicate", "rejected_weak_evidence", "archived"))
    doc = _get_record("idea", idea_id)
    if doc is None:
        raise UnknownRecord(idea_id)
    doc["status"] = status
    doc["revision"] = int(doc.get("revision", 1)) + 1
    doc["updated_at"] = time.time()
    _put_record(doc)
    return doc


def list_ideas(project_id: str, *, limit: int = MAX_LIST_DEFAULT,
               include_archived: bool = False) -> list[dict[str, Any]]:
    return _list_kind(project_id, "idea", limit=limit, include_archived=include_archived)


# ---------------------------------------------------------------- drafts (immutable versions)


def save_draft(
    *, campaign_id: str, platform: str, body: str, format: str = "post",
    title: str = "", thread_parts: tuple[str, ...] = (), media_brief: str = "",
    anchor_lines: tuple[dict[str, str], ...] = (),
    claim_ledger: tuple[dict[str, str], ...] = (),
    source_ledger: tuple[str, ...] = (),
    validation_state: str = "unvalidated", draft_id: str | None = None,
) -> dict[str, Any]:
    _check_choice("platform", platform, MANAGED_PLATFORMS)
    _check_choice("format", format, ("post", "long_post", "thread", "article", "message"))
    _check_choice("validation_state", validation_state,
                  ("unvalidated", "clean", "needs_changes", "refused", "validation_unknown"))
    body = _bounded_text("body", str(body or ""))
    _privacy_refusal("body", body)
    _privacy_refusal("title", title)
    _privacy_refusal("media_brief", media_brief)
    for part in thread_parts:
        _privacy_refusal("thread_parts", part)
    for claim in claim_ledger:
        _privacy_refusal("claim_ledger", str(claim.get("text", "")))
    for anchor in anchor_lines:
        mode = _check_choice("anchor preservation_mode", anchor.get("preservation_mode", ""),
                             PRESERVATION_MODES)
        _privacy_refusal("anchor line", anchor.get("line", ""))
        anchor["preservation_mode"] = mode
    project_id = _project_of_campaign(campaign_id)
    content_hash = _hash_bytes(body)
    findings = privacy_findings(body)

    _ensure_schema()
    if draft_id is not None:
        rows = execute_query(
            "SELECT MAX(version) AS v FROM social_content_draft_versions WHERE draft_id = ?",
            (draft_id,),
        )
        prev = int(rows[0]["v"]) if rows and rows[0]["v"] is not None else 0
    else:
        draft_id = _new_id("draft")
        prev = 0
    version = prev + 1
    now = time.time()
    doc = {
        "kind": "draft",
        "draft_id": draft_id,
        "version": version,
        "project_id": project_id,
        "campaign_id": campaign_id,
        "platform": platform,
        "format": format,
        "body_bytes": body,
        "title": _bounded_text("title", title),
        "thread_parts": [_bounded_text("thread_parts", p) for p in thread_parts],
        "media_brief": _bounded_text("media_brief", media_brief),
        "anchor_lines": [dict(a) for a in anchor_lines],
        "claim_ledger": [dict(c) for c in claim_ledger],
        "source_ledger": list(source_ledger),
        "privacy_findings": findings,
        "validation_state": validation_state,
        "content_hash": content_hash,
        "created_at": now,
    }
    execute_query(
        "INSERT INTO social_content_draft_versions (draft_id, version, project_id,"
        " created_at, doc) VALUES (?, ?, ?, ?, ?)",
        (draft_id, version, project_id, now, json.dumps(doc)),
    )
    return doc


def get_draft(draft_id: str, *, version: int | None = None) -> dict[str, Any] | None:
    _ensure_schema()
    if version is None:
        rows = execute_query(
            "SELECT doc FROM social_content_draft_versions WHERE draft_id = ?"
            " ORDER BY version DESC LIMIT 1",
            (draft_id,),
        )
    else:
        rows = execute_query(
            "SELECT doc FROM social_content_draft_versions WHERE draft_id = ? AND version = ?",
            (draft_id, int(version)),
        )
    if not rows:
        return None
    return json.loads(rows[0]["doc"])


def list_drafts(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    _ensure_schema()
    rows = execute_query(
        "SELECT draft_id, MAX(version) AS version FROM social_content_draft_versions"
        " WHERE project_id = ? GROUP BY draft_id ORDER BY version DESC LIMIT ?",
        (str(project_id), max(1, min(int(limit), MAX_LIMIT))),
    )
    out = []
    for r in rows:
        doc = get_draft(r["draft_id"])
        if doc is not None:
            out.append(doc)
    return out


# ---------------------------------------------------------------- decisions (immutable)


def record_decision(
    *, draft_id: str, draft_version: int, content_hash: str, platform: str,
    decision: str, operator: str = "operator", reason: str = "",
    destination: str = "", expires_at: float | None = None,
) -> dict[str, Any]:
    normalized_decision = decision if str(decision).strip() == "NO_POST" \
        else str(decision or "").strip().lower()
    if normalized_decision not in DECISION_KINDS:
        raise ValidationError(
            f"field 'decision' must be one of {DECISION_KINDS}, got {decision!r}"
        )
    decision = normalized_decision
    _check_choice("platform", platform, MANAGED_PLATFORMS)
    _privacy_refusal("reason", reason)
    draft = get_draft(draft_id, version=int(draft_version))
    if draft is None:
        raise UnknownRecord(f"{draft_id}@{draft_version}")
    if draft["content_hash"] != content_hash or draft["platform"] != platform:
        raise ValidationError(
            "decision must bind the stored draft's exact hash and platform — "
            "caller-supplied identity is refused"
        )
    now = time.time()
    doc = {
        "kind": "decision",
        "decision_id": _new_id("dec"),
        "project_id": draft["project_id"],
        "draft_id": draft_id,
        "draft_version": int(draft_version),
        "content_hash": content_hash,
        "platform": platform,
        "destination": destination,
        "decision": decision,
        "operator": operator,
        "decided_at": now,
        "expires_at": expires_at,
        "reason": _bounded_text("reason", reason),
    }
    execute_query(
        "INSERT INTO social_content_decisions (decision_id, project_id, draft_id,"
        " draft_version, content_hash, platform, decided_at, decision, doc)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (doc["decision_id"], doc["project_id"], draft_id, int(draft_version),
         content_hash, platform, now, decision, json.dumps(doc)),
    )
    return doc


def approval_for(*, draft_id: str, version: int, content_hash: str, platform: str,
                 at: float | None = None) -> dict[str, Any] | None:
    """The approval covering EXACTLY this bytes/version/platform, if one exists,
    is the LATEST decision for that identity, and has not expired.

    Any change to bytes, version or platform yields None (no covering row). A
    later refuse/cancel decision for the same identity overrides the approval.
    """
    _ensure_schema()
    rows = execute_query(
        "SELECT doc FROM social_content_decisions WHERE draft_id = ? AND draft_version = ?"
        " AND content_hash = ? AND platform = ?"
        " ORDER BY decided_at DESC LIMIT 1",
        (draft_id, int(version), content_hash, platform),
    )
    if not rows:
        return None
    doc = json.loads(rows[0]["doc"])
    if doc.get("decision") != "approved":
        return None  # cancelled/refused later - the approval no longer covers
    expires = doc.get("expires_at")
    if expires is not None and (time.time() if at is None else at) > float(expires):
        return None
    return doc


def has_decision(*, draft_id: str, draft_version: int, content_hash: str,
                 platform: str, decision: str) -> bool:
    _ensure_schema()
    rows = execute_query(
        "SELECT 1 FROM social_content_decisions WHERE draft_id = ? AND draft_version = ?"
        " AND content_hash = ? AND platform = ? AND decision = ? LIMIT 1",
        (draft_id, int(draft_version), content_hash, platform, decision),
    )
    return bool(rows)


def list_decisions(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    _ensure_schema()
    rows = execute_query(
        "SELECT doc FROM social_content_decisions WHERE project_id = ?"
        " ORDER BY decided_at DESC LIMIT ?",
        (str(project_id), max(1, min(int(limit), MAX_LIMIT))),
    )
    return [json.loads(r["doc"]) for r in rows]


# ---------------------------------------------------------------- metrics / hypotheses


def add_metric_observation(
    *, project_id: str, metric_name: str, value: float, unit: str = "count",
    content_ref: dict[str, str] | None = None, window_start: float | None = None,
    window_end: float | None = None, source: str = "operator_entered",
    provenance: str = "", denominator_known: bool = False,
    raw_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _check_choice("metric source", source, METRIC_SOURCES)
    _privacy_refusal("provenance", provenance)
    _privacy_refusal("raw_fields", json.dumps(raw_fields or {}, default=str))
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"metric value must be numeric, got {value!r}") from exc
    doc = {
        "kind": "metric_observation",
        "observation_id": _new_id("metric"),
        "project_id": _bounded_text("project_id", project_id).strip(),
        "content_ref": dict(content_ref or {}),
        "metric_name": _bounded_text("metric_name", metric_name),
        "value": value,
        "unit": unit or "count",
        "window_start": window_start,
        "window_end": window_end,
        "observed_at": time.time(),
        "source": source,
        "provenance": _bounded_text("provenance", provenance),
        "denominator_known": bool(denominator_known),
        "raw_fields": dict(raw_fields or {}),
    }
    _put_record(doc)
    return doc


def list_metric_observations(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    return _list_kind(project_id, "metric_observation", limit=limit)


def add_hypothesis(
    *, project_id: str, campaign_id: str = "", prediction: str,
    evidence_ids: tuple[str, ...] = (), window: str = "",
) -> dict[str, Any]:
    _privacy_refusal("prediction", prediction)
    doc = {
        "kind": "hypothesis",
        "hypothesis_id": _new_id("hyp"),
        "project_id": _bounded_text("project_id", project_id).strip(),
        "campaign_id": campaign_id,
        "prediction": _bounded_text("prediction", prediction),
        "evidence_ids": list(evidence_ids),
        "window": _bounded_text("window", window),
        "outcome": "open",
        "confidence": "low",
        "disposition": "",
        "created_at": time.time(),
    }
    _put_record(doc)
    return doc


def close_hypothesis(
    hypothesis_id: str, *, outcome: str, disposition: str = "",
    confidence: str = "low",
) -> dict[str, Any]:
    _check_choice("hypothesis outcome", outcome, HYPOTHESIS_OUTCOMES)
    _check_choice("confidence", confidence, ("low", "medium", "high"))
    _privacy_refusal("disposition", disposition)
    doc = _get_record("hypothesis", hypothesis_id)
    if doc is None:
        raise UnknownRecord(hypothesis_id)
    doc["outcome"] = outcome
    doc["disposition"] = _bounded_text("disposition", disposition)
    doc["confidence"] = confidence
    doc["updated_at"] = time.time()
    _put_record(doc)
    return doc


def list_hypotheses(project_id: str, *, limit: int = MAX_LIST_DEFAULT) -> list[dict[str, Any]]:
    return _list_kind(project_id, "hypothesis", limit=limit)


# ---------------------------------------------------------------- export


_REDACTED_TREND_FIELDS = ("observed_text", "relevance_note", "account")


def export_project(project_id: str) -> tuple[str, dict[str, Any]]:
    """(human_readable_markdown, machine_json) with private evidence redacted.

    Trend excerpts are replaced by hashes in the machine export (the excerpt stays
    in the local store; an export must not leak private feed evidence). Draft
    bodies stay byte-exact — they are the operator's approved content.
    """
    project_id = str(project_id)
    campaigns = list_campaigns(project_id, limit=MAX_LIMIT, include_archived=True)
    briefs = list_briefs(project_id, limit=MAX_LIMIT)
    ideas = list_ideas(project_id, limit=MAX_LIMIT, include_archived=True)
    trends = list_trend_observations(project_id, limit=MAX_LIMIT)
    drafts = list_drafts(project_id, limit=MAX_LIMIT)
    decisions = list_decisions(project_id, limit=MAX_LIMIT)
    metrics = list_metric_observations(project_id, limit=MAX_LIMIT)
    hypotheses = list_hypotheses(project_id, limit=MAX_LIMIT)

    def _redact_trend(t: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(t)
        for field in _REDACTED_TREND_FIELDS:
            if field in redacted:
                redacted[field] = f"<redacted:{_hash_bytes(str(redacted[field]))[:16]}>"
        return redacted

    machine = {
        "schema": "vool.social_content.export.v1",
        "project_id": project_id,
        "campaigns": campaigns,
        "briefs": briefs,
        "ideas": ideas,
        "trend_observations": [_redact_trend(t) for t in trends],
        "drafts": drafts,
        "decisions": decisions,
        "metric_observations": metrics,
        "hypotheses": hypotheses,
    }
    lines = [f"# Social content export — project {project_id}", ""]
    for c in campaigns:
        lines.append(f"- campaign {c['campaign_id']} v{c['version']} [{c['status']}]: {c['objective']}")
    for d in drafts:
        lines.append(
            f"- draft {d['draft_id']} v{d['version']} ({d['platform']}) hash {d['content_hash'][:12]}"
        )
    for dec in decisions:
        lines.append(f"- decision {dec['decision']}: {dec['draft_id']}@{dec['draft_version']}")
    for m in metrics:
        lines.append(f"- metric {m['metric_name']}={m['value']} ({m['source']})")
    for h in hypotheses:
        lines.append(f"- hypothesis {h['hypothesis_id']}: {h['outcome']}")
    return "\n".join(lines) + "\n", machine


# ---------------------------------------------------------------- record plumbing


_ID_FIELD_BY_KIND = {
    "campaign": "campaign_id",
    "brief": "brief_id",
    "idea": "idea_id",
    "trend_observation": "observation_id",
    "metric_observation": "observation_id",
    "hypothesis": "hypothesis_id",
}


def _put_record(doc: dict[str, Any]) -> None:
    _ensure_schema()
    if doc.get("kind") not in _KINDS:
        raise ValidationError(f"unknown record kind {doc.get('kind')!r}")
    record_id = doc.get(_ID_FIELD_BY_KIND.get(doc["kind"], ""))
    if not record_id:
        raise ValidationError("record is missing its typed id")
    archived = 1 if doc.get("status") == "archived" or doc.get("archived") else 0
    doc.setdefault("revision", 1)
    execute_query(
        "INSERT INTO social_content_records (record_id, project_id, kind, revision,"
        " archived, updated_at, doc) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(record_id) DO UPDATE SET revision=excluded.revision,"
        " archived=excluded.archived, updated_at=excluded.updated_at, doc=excluded.doc",
        (record_id, doc["project_id"], doc["kind"], int(doc["revision"]), archived,
         float(doc.get("updated_at") or doc.get("created_at") or time.time()), json.dumps(doc)),
    )


def _get_record(kind: str, record_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    rows = execute_query(
        "SELECT doc FROM social_content_records WHERE kind = ? AND record_id = ?",
        (kind, str(record_id)),
    )
    if not rows:
        return None
    return json.loads(rows[0]["doc"])


def _list_kind(project_id: str, kind: str, *, limit: int,
               include_archived: bool = False) -> list[dict[str, Any]]:
    _ensure_schema()
    if include_archived:
        rows = execute_query(
            "SELECT doc FROM social_content_records WHERE project_id = ? AND kind = ?"
            " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (str(project_id), kind, max(1, min(int(limit), MAX_LIMIT))),
        )
    else:
        rows = execute_query(
            "SELECT doc FROM social_content_records WHERE project_id = ? AND kind = ?"
            " AND archived = 0 ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (str(project_id), kind, max(1, min(int(limit), MAX_LIMIT))),
        )
    return [json.loads(r["doc"]) for r in rows]


def _project_of_campaign(campaign_id: str) -> str:
    doc = get_campaign(campaign_id)
    if doc is None:
        raise UnknownRecord(f"campaign {campaign_id}")
    return doc["project_id"]
