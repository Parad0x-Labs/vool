"""Exact-outbound-bytes preview with field and attachment removal.

The preview IS the submission payload: the bytes it hashes are byte-for-byte what the
GitHub adapter later sends, so "what you approved is what left the machine" is checkable
by hash. Removing a field or attachment rebuilds the payload, which changes the hash and
therefore invalidates any earlier approval -- consent binds the exact bytes.
"""
from __future__ import annotations

import hashlib
import json

from core.bug_report.schema import (
    AttachmentPlan,
    BugReportDraft,
    ConsentManifest,
    ReportPreview,
    SanitizedMaterial,
)
from core.bug_report.synthesis import synthesize_local

# Fields a user may drop from the outbound report. Core identity fields (environment,
# expected, actual, fingerprint) are not removable: without them the report is not a
# reproducible bug report, and the fingerprint is what dedup depends on.
REMOVABLE_FIELDS = frozenset({"logs", "flags", "repro", "components", "error", "attachments"})
MAX_OUTBOUND_BYTES = 256 * 1024


def attachment_contents(draft: BugReportDraft) -> dict[str, bytes]:
    """Deterministically rendered attachment payloads derived from sanitized draft fields."""
    contents: dict[str, bytes] = {}
    if draft.error is not None:
        lines = ["Traceback (most recent call last):"]
        for frame in draft.error.frames:
            lines.append(f"  File \"{frame.file}\", line {frame.line}, in {frame.function}")
        lines.append(f"{draft.error.exc_type}: {draft.error.message}".rstrip(": "))
        contents["sanitized-stack.txt"] = ("\n".join(lines) + "\n").encode("utf-8")
    for excerpt in draft.logs:
        contents[f"logs-{excerpt.source}"] = ("\n".join(excerpt.lines) + "\n").encode("utf-8")
    if draft.flags_snapshot:
        contents["flags.json"] = (
            json.dumps(draft.flags_snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
    return contents


def attachment_plans(draft: BugReportDraft) -> tuple[AttachmentPlan, ...]:
    plans: list[AttachmentPlan] = []
    for name, payload in attachment_contents(draft).items():
        if name.startswith("sanitized-stack"):
            kind = "sanitized_stack"
        elif name.startswith("logs-"):
            kind = "log_excerpt"
        else:
            kind = "flags_snapshot"
        plans.append(
            AttachmentPlan(
                name=name,
                kind=kind,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return tuple(plans)


def build_preview(
    draft: BugReportDraft,
    *,
    remove_fields: tuple[str, ...] | list[str] = (),
    remove_attachments: tuple[str, ...] | list[str] = (),
    include_fault_evidence: bool = False,
    fault_turn_key: str = "",
    fault_session_id: str = "",
) -> ReportPreview:
    removals = set(remove_fields or ())
    unknown = removals - REMOVABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown removable field(s): {', '.join(sorted(unknown))}")
    removed_attachments = tuple(remove_attachments or ())

    # FAULT PLANE — the typed fault codes this turn's boundaries filed, via the plane's
    # privacy-safe export (codes, severities, operator actions; never internal context).
    # The export self-scans through the same outbound gate every payload passes, and the
    # section rides INSIDE the scanned material, so "what you approved" stays exact.
    fault_export: dict | None = None
    if include_fault_evidence:
        from core.faults.bug_export import fault_evidence_export

        fault_export = fault_evidence_export(
            turn_key=str(fault_turn_key or ""), session_id=str(fault_session_id or "")
        )

    payload: dict = {
        "title": draft.title or "untitled issue",
        "category": draft.category,
        "expected": draft.expected,
        "actual": draft.actual,
        "environment": draft.environment.to_dict() if draft.environment else {},
        "fingerprint": draft.fingerprint,
    }
    if "repro" not in removals:
        payload["repro_steps"] = list(draft.repro_steps)
    if "error" not in removals and draft.error is not None:
        payload["error"] = draft.error.to_dict()
    if "components" not in removals:
        payload["components"] = draft.components.to_dict()
    if "logs" not in removals:
        payload["logs"] = [log.to_dict() for log in draft.logs]
    if "flags" not in removals:
        payload["flags"] = dict(draft.flags_snapshot)

    title, body = synthesize_local(SanitizedMaterial.from_payload(payload))

    if fault_export is not None and fault_export.get("fault_count"):
        lines = ["", "### Fault evidence (typed codes only; internals stay local)", ""]
        for fault in fault_export["faults"]:
            lines.append(
                f"- {fault['code']} ({fault['severity']}, {fault['lifecycle']}) — {fault['operator_action']}"
            )
        body = body + "\n".join(lines) + "\n"

    fields_included = ["version", "source_sha", "os", "arch", "expected", "actual", "fingerprint"]
    if fault_export is not None and fault_export.get("fault_count"):
        fields_included.append("fault_evidence")
    if "repro" not in removals:
        fields_included.append("repro")
    if "error" not in removals:
        fields_included.append("error")
    if "components" not in removals:
        fields_included.append("components")
    if "logs" not in removals:
        fields_included.append("logs")
    if "flags" not in removals:
        fields_included.append("flags")

    included_attachments: tuple[AttachmentPlan, ...] = ()
    payloads: dict[str, bytes] = {}
    if "attachments" not in removals:
        kept_lines: list[str] = [""]
        for plan in draft.attachments:
            if plan.name in removed_attachments:
                continue
            content = attachment_contents(draft).get(plan.name, b"")
            payloads[plan.name] = content
            included_attachments = (*included_attachments, plan)
            kept_lines.append(f"### attachment: {plan.name} ({plan.size_bytes} bytes, sha256 {plan.sha256[:12]})")
            kept_lines.append("```")
            kept_lines.append(content.decode("utf-8", errors="replace").rstrip("\n"))
            kept_lines.append("```")
            kept_lines.append("")
        if kept_lines != [""]:
            body = body + "\n".join(kept_lines)

    issue = {"title": title, "body": body}
    outbound = json.dumps(issue, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(outbound) > MAX_OUTBOUND_BYTES:
        from core.bug_report.schema import CaptureRefusedError

        raise CaptureRefusedError(f"outbound report exceeds {MAX_OUTBOUND_BYTES} bytes; remove fields or attachments")

    return ReportPreview(
        report_id=draft.report_id,
        payload_sha256=hashlib.sha256(outbound).hexdigest(),
        total_bytes=len(outbound),
        issue=issue,
        attachments=included_attachments,
        attachment_payloads=payloads,
        fields_included=tuple(fields_included),
        removed_fields=tuple(sorted(removals)),
        removed_attachments=removed_attachments,
    )


def consent_matches(consent: ConsentManifest, preview: ReportPreview) -> bool:
    return consent.payload_sha256 == preview.payload_sha256


__all__ = ["MAX_OUTBOUND_BYTES", "REMOVABLE_FIELDS", "attachment_contents", "attachment_plans", "build_preview", "consent_matches"]
