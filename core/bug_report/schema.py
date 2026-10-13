"""Typed schema for the opt-in, privacy-safe bug reporter.

Conventions follow core/turn_contract.py: frozen dataclasses with JSON-safe ``to_dict()``
egress and lossless ``from_dict()`` reconstruction, plus strict unknown-key refusal so a
draft tampered with on disk (an injected ``conversation`` field, say) fails loudly instead
of silently shipping. The error taxonomy is one root exception with typed children; the
service maps each child to a specific failure mode instead of string-matching messages.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

SCHEMA_VERSION = 2
#: Draft schema versions this build reads. Version 1 drafts (before machine specs, health and
#: answer reports) load with those sections empty; nothing is migrated on disk.
READABLE_SCHEMA_VERSIONS = frozenset({1, 2})
REPORT_KINDS = frozenset({"bug", "wrong_answer"})
#: Why an answer was wrong -- the reasons a user picks from, in this order.
ANSWER_REASONS = ("wrong", "outdated", "not_what_i_asked", "unsafe", "other")
REPORT_ID_PATTERN = r"br_[0-9a-f]{12}"


class BugReportError(Exception):
    """Root of the bug-report error taxonomy."""


class CaptureRefusedError(BugReportError):
    """A capture input violated a bound or a kind restriction (conversation material, caps)."""


class UnsafeReportContentError(BugReportError):
    """Material failed the outbound secret/privacy scan; nothing may leave the machine."""

    def __init__(self, findings: list[tuple[str, int]] | None = None, message: str = ""):
        self.findings = findings or []
        text = message or "material failed the outbound privacy scan"
        if self.findings:
            rules = ", ".join(sorted({rule for rule, _start in self.findings}))
            text = f"{text} (rules: {rules})"
        super().__init__(text)


class DraftNotFoundError(BugReportError):
    """No local draft exists under that id."""


class ApprovalRequiredError(BugReportError):
    """Submission attempted without a human-granted consent manifest."""


class ApprovalMismatchError(BugReportError):
    """Consent exists but does not bind these exact outbound bytes."""


class ConsentInvalidError(BugReportError):
    """A consent manifest failed structural validation (forged or corrupt)."""


def _require_keys(data: dict[str, Any], expected: set[str], *, where: str) -> None:
    unknown = set(data) - expected
    if unknown:
        raise ValueError(f"unknown {where} key(s): {', '.join(sorted(unknown))}")


@dataclass(frozen=True)
class ReportEnvironment:
    version: str
    source_kind: str
    source_sha: str
    source_dirty: bool | None
    os: str
    arch: str
    python: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_kind": self.source_kind,
            "source_sha": self.source_sha,
            "source_dirty": self.source_dirty,
            "os": self.os,
            "arch": self.arch,
            "python": self.python,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportEnvironment:
        _require_keys(data, {"version", "source_kind", "source_sha", "source_dirty", "os", "arch", "python"}, where="environment")
        return cls(
            version=str(data["version"]),
            source_kind=str(data["source_kind"]),
            source_sha=str(data["source_sha"]),
            source_dirty=data["source_dirty"] if data["source_dirty"] is None else bool(data["source_dirty"]),
            os=str(data["os"]),
            arch=str(data["arch"]),
            python=str(data["python"]),
        )


@dataclass(frozen=True)
class StackFrameSanitized:
    file: str
    line: int | None
    function: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "function": self.function}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StackFrameSanitized:
        _require_keys(data, {"file", "line", "function"}, where="stack frame")
        return cls(file=str(data["file"]), line=data["line"] if data["line"] is None else int(data["line"]), function=str(data["function"]))


@dataclass(frozen=True)
class SanitizedError:
    exc_type: str
    message: str
    frames: tuple[StackFrameSanitized, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"exc_type": self.exc_type, "message": self.message, "frames": [f.to_dict() for f in self.frames]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SanitizedError:
        _require_keys(data, {"exc_type", "message", "frames"}, where="error")
        return cls(
            exc_type=str(data["exc_type"]),
            message=str(data["message"]),
            frames=tuple(StackFrameSanitized.from_dict(f) for f in data["frames"]),
        )


@dataclass(frozen=True)
class LogExcerpt:
    source: str
    lines: tuple[str, ...]
    truncated: bool
    original_line_count: int
    dropped_lines: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "lines": list(self.lines),
            "truncated": self.truncated,
            "original_line_count": self.original_line_count,
            "dropped_lines": self.dropped_lines,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogExcerpt:
        _require_keys(
            data, {"source", "lines", "truncated", "original_line_count", "dropped_lines"}, where="log excerpt"
        )
        return cls(
            source=str(data["source"]),
            lines=tuple(str(line) for line in data["lines"]),
            truncated=bool(data["truncated"]),
            original_line_count=int(data["original_line_count"]),
            dropped_lines=int(data["dropped_lines"]),
        )


@dataclass(frozen=True)
class InvolvedComponents:
    lanes: tuple[str, ...]
    tools: tuple[str, ...]
    models: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"lanes": list(self.lanes), "tools": list(self.tools), "models": list(self.models)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvolvedComponents:
        _require_keys(data, {"lanes", "tools", "models"}, where="components")
        return cls(
            lanes=tuple(str(x) for x in data["lanes"]),
            tools=tuple(str(x) for x in data["tools"]),
            models=tuple(str(x) for x in data["models"]),
        )


@dataclass  # mutable on purpose: the capture stages accumulate redaction counts into it
class RedactionSummary:
    rule_counts: dict[str, int] = field(default_factory=dict)
    total_replacements: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"rule_counts": dict(self.rule_counts), "total_replacements": self.total_replacements}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RedactionSummary:
        _require_keys(data, {"rule_counts", "total_replacements"}, where="redaction summary")
        return cls(
            rule_counts={str(k): int(v) for k, v in data["rule_counts"].items()},
            total_replacements=int(data["total_replacements"]),
        )

    def merge(self, other: RedactionSummary) -> RedactionSummary:
        counts = dict(self.rule_counts)
        for rule, count in other.rule_counts.items():
            counts[rule] = counts.get(rule, 0) + count
        return RedactionSummary(rule_counts=counts, total_replacements=self.total_replacements + other.total_replacements)


@dataclass(frozen=True)
class AttachmentPlan:
    name: str
    kind: str  # sanitized_stack | log_excerpt | flags_snapshot
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "size_bytes": self.size_bytes, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttachmentPlan:
        _require_keys(data, {"name", "kind", "size_bytes", "sha256"}, where="attachment")
        return cls(
            name=str(data["name"]),
            kind=str(data["kind"]),
            size_bytes=int(data["size_bytes"]),
            sha256=str(data["sha256"]),
        )


@dataclass(frozen=True)
class ConsentManifest:
    report_id: str
    payload_sha256: str
    fields_included: tuple[str, ...]
    attachments_included: tuple[str, ...]
    destination_repo: str
    approved_at: str
    approver: str
    removed_fields: tuple[str, ...] = ()
    removed_attachments: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "payload_sha256": self.payload_sha256,
            "fields_included": list(self.fields_included),
            "attachments_included": list(self.attachments_included),
            "destination_repo": self.destination_repo,
            "approved_at": self.approved_at,
            "approver": self.approver,
            "removed_fields": list(self.removed_fields),
            "removed_attachments": list(self.removed_attachments),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsentManifest:
        _require_keys(
            data,
            {
                "report_id", "payload_sha256", "fields_included", "attachments_included",
                "destination_repo", "approved_at", "approver", "removed_fields", "removed_attachments",
            },
            where="consent",
        )
        return cls(
            report_id=str(data["report_id"]),
            payload_sha256=str(data["payload_sha256"]),
            fields_included=tuple(str(x) for x in data["fields_included"]),
            attachments_included=tuple(str(x) for x in data["attachments_included"]),
            destination_repo=str(data["destination_repo"]),
            approved_at=str(data["approved_at"]),
            approver=str(data["approver"]),
            removed_fields=tuple(str(x) for x in data["removed_fields"]),
            removed_attachments=tuple(str(x) for x in data["removed_attachments"]),
        )


def _string_map(data: Any, *, where: str, max_items: int = 24) -> dict[str, str]:
    if data is None:
        return {}
    if not isinstance(data, dict) or len(data) > max_items:
        raise ValueError(f"{where} must be a small object of strings")
    return {str(k): str(v) for k, v in data.items()}


@dataclass(frozen=True)
class AnswerAction:
    """One recorded action of the reported turn: what ran and whether it worked. No arguments."""

    name: str
    kind: str
    status: str
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "status": self.status, "ok": self.ok}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerAction:
        _require_keys(data, {"name", "kind", "status", "ok"}, where="answer action")
        return cls(name=str(data["name"]), kind=str(data["kind"]), status=str(data["status"]), ok=bool(data["ok"]))


@dataclass(frozen=True)
class AnswerReceipt:
    """The reported turn's own records, read from the stores that own them (never inferred).

    ``unknown`` means the record was not there; it is never filled with a guess."""

    record_state: str
    state_reasons: tuple[str, ...]
    model: str
    provider: str
    actions: tuple[AnswerAction, ...]
    sources_count: str
    tokens: str
    elapsed_ms: str
    integrity_ok: str
    witness_consistent: str
    fault_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_state": self.record_state,
            "state_reasons": list(self.state_reasons),
            "model": self.model,
            "provider": self.provider,
            "actions": [a.to_dict() for a in self.actions],
            "sources_count": self.sources_count,
            "tokens": self.tokens,
            "elapsed_ms": self.elapsed_ms,
            "integrity_ok": self.integrity_ok,
            "witness_consistent": self.witness_consistent,
            "fault_codes": list(self.fault_codes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerReceipt:
        _require_keys(
            data,
            {
                "record_state", "state_reasons", "model", "provider", "actions", "sources_count",
                "tokens", "elapsed_ms", "integrity_ok", "witness_consistent", "fault_codes",
            },
            where="answer receipt",
        )
        return cls(
            record_state=str(data["record_state"]),
            state_reasons=tuple(str(x) for x in data["state_reasons"]),
            model=str(data["model"]),
            provider=str(data["provider"]),
            actions=tuple(AnswerAction.from_dict(a) for a in data["actions"]),
            sources_count=str(data["sources_count"]),
            tokens=str(data["tokens"]),
            elapsed_ms=str(data["elapsed_ms"]),
            integrity_ok=str(data["integrity_ok"]),
            witness_consistent=str(data["witness_consistent"]),
            fault_codes=tuple(str(x) for x in data["fault_codes"]),
        )


@dataclass(frozen=True)
class AnswerEvidence:
    """What a wrong-answer report carries from the conversation -- and nothing else.

    ``excerpt`` is the part of the answer the user selected (or the whole answer when they
    reported the message), redacted and capped. ``question`` is the user's own message for
    that turn and is EMPTY unless the user ticked "include my question". No other turn, no
    memory and no file ever enters a report."""

    reason: str
    excerpt: str
    excerpt_source: str  # selection | whole_answer
    question: str
    receipt: AnswerReceipt | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "excerpt": self.excerpt,
            "excerpt_source": self.excerpt_source,
            "question": self.question,
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnswerEvidence:
        _require_keys(data, {"reason", "excerpt", "excerpt_source", "question", "receipt"}, where="answer")
        receipt = data["receipt"]
        return cls(
            reason=str(data["reason"]),
            excerpt=str(data["excerpt"]),
            excerpt_source=str(data["excerpt_source"]),
            question=str(data["question"]),
            receipt=AnswerReceipt.from_dict(receipt) if receipt is not None else None,
        )


_DRAFT_KEYS = {
    "report_id", "created_at", "schema_version", "environment", "title", "category",
    "expected", "actual", "repro_steps", "error", "components", "logs", "flags_snapshot",
    "attachments", "fingerprint", "redaction_summary", "destination_repo", "consent",
    "state", "last_error", "last_issue_url",
    # schema 2
    "kind", "machine", "health", "answer", "local_ref",
}


@dataclass(frozen=True)
class BugReportDraft:
    report_id: str
    created_at: str
    schema_version: int
    environment: ReportEnvironment
    title: str
    category: str
    expected: str
    actual: str
    repro_steps: tuple[str, ...]
    error: SanitizedError | None
    components: InvolvedComponents
    logs: tuple[LogExcerpt, ...]
    flags_snapshot: dict[str, bool]
    attachments: tuple[AttachmentPlan, ...]
    fingerprint: str
    redaction_summary: RedactionSummary
    destination_repo: str
    consent: ConsentManifest | None = None
    state: str = "draft"  # draft | submitted
    last_error: str = ""
    last_issue_url: str = ""
    kind: str = "bug"  # bug | wrong_answer
    machine: dict[str, str] = field(default_factory=dict)
    health: dict[str, str] = field(default_factory=dict)
    answer: AnswerEvidence | None = None
    #: Local-only pointers back to the reported chat turn (session id, request id), so "My
    #: reports" can name what was reported. NEVER rendered into the outbound payload.
    local_ref: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "environment": self.environment.to_dict(),
            "title": self.title,
            "category": self.category,
            "expected": self.expected,
            "actual": self.actual,
            "repro_steps": list(self.repro_steps),
            "error": self.error.to_dict() if self.error is not None else None,
            "components": self.components.to_dict(),
            "logs": [log.to_dict() for log in self.logs],
            "flags_snapshot": dict(self.flags_snapshot),
            "attachments": [a.to_dict() for a in self.attachments],
            "fingerprint": self.fingerprint,
            "redaction_summary": self.redaction_summary.to_dict(),
            "destination_repo": self.destination_repo,
            "consent": self.consent.to_dict() if self.consent is not None else None,
            "state": self.state,
            "last_error": self.last_error,
            "last_issue_url": self.last_issue_url,
            "kind": self.kind,
            "machine": dict(self.machine),
            "health": dict(self.health),
            "answer": self.answer.to_dict() if self.answer is not None else None,
            "local_ref": dict(self.local_ref),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BugReportDraft:
        _require_keys(data, _DRAFT_KEYS, where="draft")
        if data["schema_version"] not in READABLE_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported draft schema_version: {data['schema_version']!r}")
        kind = str(data.get("kind", "bug"))
        if kind not in REPORT_KINDS:
            raise ValueError(f"unknown report kind: {kind!r}")
        answer = data.get("answer")
        consent = data["consent"]
        return cls(
            report_id=str(data["report_id"]),
            created_at=str(data["created_at"]),
            schema_version=int(data["schema_version"]),
            environment=ReportEnvironment.from_dict(data["environment"]),
            title=str(data["title"]),
            category=str(data["category"]),
            expected=str(data["expected"]),
            actual=str(data["actual"]),
            repro_steps=tuple(str(s) for s in data["repro_steps"]),
            error=SanitizedError.from_dict(data["error"]) if data["error"] is not None else None,
            components=InvolvedComponents.from_dict(data["components"]),
            logs=tuple(LogExcerpt.from_dict(log) for log in data["logs"]),
            flags_snapshot={str(k): bool(v) for k, v in data["flags_snapshot"].items()},
            attachments=tuple(AttachmentPlan.from_dict(a) for a in data["attachments"]),
            fingerprint=str(data["fingerprint"]),
            redaction_summary=RedactionSummary.from_dict(data["redaction_summary"]),
            destination_repo=str(data["destination_repo"]),
            consent=ConsentManifest.from_dict(consent) if consent is not None else None,
            state=str(data["state"]),
            last_error=str(data["last_error"]),
            last_issue_url=str(data["last_issue_url"]),
            kind=kind,
            machine=_string_map(data.get("machine"), where="machine"),
            health=_string_map(data.get("health"), where="health"),
            answer=AnswerEvidence.from_dict(answer) if answer is not None else None,
            local_ref=_string_map(data.get("local_ref"), where="local_ref", max_items=4),
        )

    def with_updates(self, **changes: Any) -> BugReportDraft:
        return replace(self, **changes)


@dataclass(frozen=True)
class ReportPreview:
    report_id: str
    payload_sha256: str
    total_bytes: int
    issue: dict[str, Any]  # {"title": str, "body": str} -- exactly what leaves the machine
    attachments: tuple[AttachmentPlan, ...]
    attachment_payloads: dict[str, bytes]
    fields_included: tuple[str, ...]
    removed_fields: tuple[str, ...]
    removed_attachments: tuple[str, ...]

    def outbound_bytes(self) -> bytes:
        return json.dumps(self.issue, ensure_ascii=False, sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class SubmissionResult:
    status: str  # submitted | duplicate | failed
    issue_url: str = ""
    issue_number: int | None = None
    duplicate_of: str = ""
    detail: str = ""
    payload_sha256: str = ""
    #: Structured failure fact for the FAILED status, so the HTTP boundary can choose a
    #: standard status from typed data instead of string-matching ``detail``. Empty for
    #: submitted/duplicate. Known values (append-only):
    #:   draft_not_found | approval_required | consent_mismatch | outbound_scan_refused |
    #:   credential_unavailable | upstream_timeout | upstream_unreachable |
    #:   upstream_status | upstream_response_unparseable | invalid_destination
    failure_code: str = ""
    #: The ACTUAL HTTP status the destination returned, when one did. The report's own
    #: response status never claims to BE this number.
    upstream_status: int | None = None


@dataclass(frozen=True)
class SanitizedMaterial:
    """The ONLY input a synthesis model may receive: a payload that passed the scanner.

    Constructing this type re-runs the outbound scan, so handing it unsanitized text fails
    closed with UnsafeReportContentError. The LLM never owns sanitization -- it may only
    restructure material that is already clean by construction.
    """

    payload: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SanitizedMaterial:
        from core.bug_report.scanner import scan_text

        dumped = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        findings = scan_text(dumped)
        if findings:
            raise UnsafeReportContentError(findings=[(f.rule, f.start) for f in findings])
        return cls(payload=dict(payload))


__all__ = [
    "ANSWER_REASONS",
    "READABLE_SCHEMA_VERSIONS",
    "REPORT_KINDS",
    "SCHEMA_VERSION",
    "AnswerAction",
    "AnswerEvidence",
    "AnswerReceipt",
    "ApprovalMismatchError",
    "ApprovalRequiredError",
    "AttachmentPlan",
    "BugReportDraft",
    "BugReportError",
    "CaptureRefusedError",
    "ConsentInvalidError",
    "ConsentManifest",
    "DraftNotFoundError",
    "InvolvedComponents",
    "LogExcerpt",
    "RedactionSummary",
    "ReportEnvironment",
    "ReportPreview",
    "SanitizedError",
    "SanitizedMaterial",
    "StackFrameSanitized",
    "SubmissionResult",
    "UnsafeReportContentError",
]
