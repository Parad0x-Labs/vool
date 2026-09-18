"""The bug-report pipeline orchestrator.

capture (bounded) -> deterministic redaction -> allowlist reconstruction -> fingerprint ->
local draft persisted -> preview (exact outbound bytes) -> explicit consent-bound
approval -> submission (exact bytes only, dedup first) -> durable receipt.

Invariants enforced HERE, not at the callers:

- nothing is sent without a consent manifest whose payload hash matches the freshly
  rebuilt preview byte-for-byte;
- the outbound bytes are re-scanned immediately before the transport call;
- a failed submission keeps the local draft (retry just calls submit again);
- dedup consults the local receipt store before the network;
- the receipt records metadata only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import runtime_flags
from core.bug_report.allowlist import parse_stack, require_identifier
from core.bug_report.capture import (
    MAX_PROSE_CHARS,
    capture_environment,
    capture_logs,
    validate_repro_steps,
)
from core.bug_report.fingerprint import compute_fingerprint
from core.bug_report.github_adapter import default_credential_lookup, submit_to_github
from core.bug_report.preview import attachment_plans, build_preview
from core.bug_report.receipts import ReceiptStore
from core.bug_report.schema import (
    ApprovalMismatchError,
    BugReportDraft,
    CaptureRefusedError,
    ConsentManifest,
    DraftNotFoundError,
    InvolvedComponents,
    RedactionSummary,
    ReportPreview,
    SubmissionResult,
)
from core.bug_report.store import DraftStore
from core.runtime_paths import PROJECT_ROOT as _DEFAULT_PROJECT_ROOT
from core.runtime_paths import data_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BugReportService:
    def __init__(self, *, store_dir: Path | None = None, project_root: Path | None = None):
        root = Path(store_dir) if store_dir is not None else data_path("bug_reports")
        self.store = DraftStore(root)
        self.receipt_store = ReceiptStore(root / "receipts.jsonl")
        # Local exports keep their OWN ledger: an export never left the machine, so it
        # must not make a later GitHub submit of the same fingerprint read as a
        # "duplicate" of an issue that does not exist.
        self.export_store = ReceiptStore(root / "exports.jsonl")
        self.project_root = Path(project_root) if project_root is not None else Path(_DEFAULT_PROJECT_ROOT)

    # -- stage 1-4: capture -> redact -> reconstruct -> fingerprint -> persist --------

    def create_draft(
        self,
        *,
        expected: str,
        actual: str,
        repro_steps,
        error_text: str = "",
        category: str = "",
        lanes=(),
        tools=(),
        models=(),
        log_sources=(),
        destination_repo: str = "",
        title: str = "",
    ) -> BugReportDraft:
        from core.bug_report.destination import default_destination, is_valid_destination

        resolved_destination = str(destination_repo or "").strip()
        if not resolved_destination:
            # An empty destination means "the configured default" -- the owner-local
            # choice in core.bug_report.destination, resolved server-side so the page
            # cannot smuggle in its own value. Consent still binds the resolved name.
            resolved_destination = default_destination()
        if not is_valid_destination(resolved_destination):
            raise CaptureRefusedError(f"invalid destination repo: {destination_repo!r}")

        environment = capture_environment(self.project_root)
        total_summary = RedactionSummary()
        clean_expected, _expected_summary = _prose(expected, summary=total_summary)
        clean_actual, _actual_summary = _prose(actual, summary=total_summary)
        clean_title, _title_summary = _prose(title, max_chars=160, summary=total_summary)
        clean_category, _cat_summary = _prose(category, max_chars=64, summary=total_summary)
        clean_steps = validate_repro_steps(repro_steps, summary=total_summary)
        error = parse_stack(error_text, summary=total_summary)
        components = InvolvedComponents(
            lanes=tuple(require_identifier(x, field="lane") for x in lanes),
            tools=tuple(require_identifier(x, field="tool") for x in tools),
            models=tuple(require_identifier(x, field="model") for x in models),
        )
        logs = capture_logs(log_sources, summary=total_summary)
        try:
            flags_snapshot = {name: bool(state) for name, state in runtime_flags.flag_state().items()}
        except Exception:
            flags_snapshot = {}

        fingerprint = compute_fingerprint(
            category=clean_category,
            error=error,
            components=components,
            repro_steps=clean_steps,
            title=clean_title,
        )
        draft = BugReportDraft(
            report_id="br_" + uuid.uuid4().hex[:12],
            created_at=_utc_now(),
            schema_version=1,
            environment=environment,
            title=clean_title or clean_category or "untitled issue",
            category=clean_category,
            expected=clean_expected,
            actual=clean_actual,
            repro_steps=tuple(clean_steps),
            error=error,
            components=components,
            logs=logs,
            flags_snapshot=flags_snapshot,
            attachments=(),
            fingerprint=fingerprint,
            redaction_summary=total_summary,
            destination_repo=resolved_destination,
        )
        draft = draft.with_updates(attachments=attachment_plans(draft))
        self.store.save(draft)
        return draft

    # -- stage 5: local draft access ------------------------------------------------

    def get_draft(self, report_id: str) -> BugReportDraft:
        return self.store.load(report_id)

    def update_draft(
        self,
        report_id: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
        repro_steps=None,
        title: str | None = None,
    ) -> BugReportDraft:
        """Edit draft fields AFTER creation. Any edit invalidates approval: the consent
        manifest is cleared, because it binds the exact bytes of a payload that no
        longer exists. Fingerprint and attachments are recomputed from the new fields."""
        draft = self.store.load(report_id)
        summary = RedactionSummary(
            rule_counts=dict(draft.redaction_summary.rule_counts),
            total_replacements=draft.redaction_summary.total_replacements,
        )
        changes: dict = {"consent": None}
        if expected is not None:
            changes["expected"], _expected_summary = _prose(expected, summary=summary)
        if actual is not None:
            changes["actual"], _actual_summary = _prose(actual, summary=summary)
        if title is not None:
            changes["title"], _title_summary = _prose(title, max_chars=160, summary=summary)
        if repro_steps is not None:
            changes["repro_steps"] = tuple(validate_repro_steps(repro_steps, summary=summary))
        updated = draft.with_updates(**changes)
        changes["fingerprint"] = compute_fingerprint(
            category=updated.category,
            error=updated.error,
            components=updated.components,
            repro_steps=updated.repro_steps,
        )
        changes["redaction_summary"] = summary
        final = draft.with_updates(**changes)
        final = final.with_updates(attachments=attachment_plans(final))
        self.store.save(final)
        return final

    def list_drafts(self) -> list[dict]:
        summaries = []
        for report_id in self.store.ids():
            try:
                draft = self.store.load(report_id)
            except (DraftNotFoundError, ValueError):
                continue
            summaries.append(
                {
                    "report_id": draft.report_id,
                    "created_at": draft.created_at,
                    "title": draft.title,
                    "category": draft.category,
                    "fingerprint": draft.fingerprint,
                    "state": draft.state,
                    "destination_repo": draft.destination_repo,
                }
            )
        return summaries

    # -- stage 6: exact preview ------------------------------------------------------

    def preview(
        self,
        report_id: str,
        *,
        remove_fields=(),
        remove_attachments=(),
    ) -> ReportPreview:
        from core.bug_report.scanner import assert_clean

        draft = self.store.load(report_id)
        preview = build_preview(
            draft,
            remove_fields=tuple(remove_fields or ()),
            remove_attachments=tuple(remove_attachments or ()),
        )
        assert_clean(preview.outbound_bytes())
        return preview

    # -- stage 7: explicit approval ----------------------------------------------------

    def approve(
        self,
        report_id: str,
        *,
        payload_sha256: str,
        remove_fields=(),
        remove_attachments=(),
        confirm: bool = False,
        approver: str = "local-user",
    ) -> ConsentManifest:
        if not confirm:
            raise ValueError("approval requires confirm=True — consent must be explicit")
        draft = self.store.load(report_id)
        preview = build_preview(
            draft,
            remove_fields=tuple(remove_fields or ()),
            remove_attachments=tuple(remove_attachments or ()),
        )
        if payload_sha256 != preview.payload_sha256:
            raise ApprovalMismatchError(
                "consent does not bind these bytes: approved hash does not match the current payload"
            )
        consent = ConsentManifest(
            report_id=draft.report_id,
            payload_sha256=preview.payload_sha256,
            fields_included=preview.fields_included,
            attachments_included=tuple(p.name for p in preview.attachments),
            destination_repo=draft.destination_repo,
            approved_at=_utc_now(),
            approver=approver,
            removed_fields=tuple(remove_fields or ()),
            removed_attachments=tuple(remove_attachments or ()),
        )
        self.store.save(draft.with_updates(consent=consent))
        return consent

    # -- stage 8-9: submit + receipt ------------------------------------------------------

    def submit(
        self,
        report_id: str,
        *,
        transport=None,
        credential_lookup=None,
        dedup_search=None,
        actor: str = "local-user",
    ) -> SubmissionResult:
        from core.bug_report.scanner import assert_clean

        try:
            draft = self.store.load(report_id)
        except DraftNotFoundError:
            return SubmissionResult(
                status="failed", detail=f"draft not found: {report_id}", failure_code="draft_not_found"
            )

        if draft.state == "submitted" and draft.last_issue_url:
            return SubmissionResult(
                status="duplicate",
                duplicate_of=draft.last_issue_url,
                detail="this draft was already submitted",
            )

        if draft.consent is None:
            return SubmissionResult(
                status="failed",
                detail="approval required before submission",
                failure_code="approval_required",
            )
        try:
            _validate_consent(draft)
        except ApprovalMismatchError as exc:
            return SubmissionResult(status="failed", detail=str(exc), failure_code="consent_mismatch")

        preview = build_preview(
            draft,
            remove_fields=draft.consent.removed_fields,
            remove_attachments=draft.consent.removed_attachments,
        )
        if preview.payload_sha256 != draft.consent.payload_sha256:
            return SubmissionResult(
                status="failed",
                detail="consent mismatch: the draft changed after approval (payload hash differs)",
                failure_code="consent_mismatch",
            )
        try:
            assert_clean(preview.outbound_bytes())
        except Exception as exc:
            return SubmissionResult(
                status="failed",
                detail=f"outbound scan refused: {exc}",
                failure_code="outbound_scan_refused",
            )

        prior = self.receipt_store.find_by_fingerprint(draft.fingerprint)
        if prior is not None:
            return SubmissionResult(
                status="duplicate",
                duplicate_of=str(prior.get("issue_url", "")),
                detail="a report with this fingerprint was already submitted",
            )

        lookup = credential_lookup or default_credential_lookup
        token, credential_source = lookup()

        result = submit_to_github(
            preview,
            destination=draft.destination_repo,
            credential_lookup=(lambda: (token, credential_source)) if credential_lookup is not None else None,
            dedup_search=dedup_search,
            transport=transport,
        )

        if result.status == "submitted":
            self.receipt_store.record_submission(
                report_id=draft.report_id,
                fingerprint=draft.fingerprint,
                payload_sha256=preview.payload_sha256,
                destination=draft.destination_repo,
                issue_url=result.issue_url,
                issue_number=result.issue_number,
                total_bytes=preview.total_bytes,
                field_names=list(preview.fields_included),
                attachment_names=[p.name for p in preview.attachments],
                credential_source=credential_source,
            )
            self.store.save(
                draft.with_updates(state="submitted", last_issue_url=result.issue_url, last_error="")
            )
        else:
            self.store.save(draft.with_updates(last_error=result.detail))
        return result

    # -- stage 10: receipts ------------------------------------------------------------

    def receipts(self) -> list[dict]:
        return self.receipt_store.rows()

    def verify_receipts(self) -> bool:
        return self.receipt_store.verify_chain()

    def export_receipts(self) -> list[dict]:
        return self.export_store.rows()

    # -- local export: the no-GitHub path ------------------------------------------------

    def export(
        self,
        report_id: str,
        *,
        remove_fields=(),
        remove_attachments=(),
    ) -> dict:
        """Save the EXACT previewed bytes to a local file. Nothing leaves the machine.

        This is the path for a user with no GitHub access (or a destination they cannot
        reach): the same sanitized, scanner-clean payload they previewed is written to
        ``exports/<report_id>.json`` under the bug-report store, and a receipt records
        that a local export happened (hashes and sizes only, like every receipt). A
        local export never satisfies or blocks a later GitHub submission -- it has its
        own ledger -- and it does not require consent because it is equivalent to
        reading the preview: the bytes stay on this machine.
        """
        from core.bug_report.scanner import assert_clean

        draft = self.store.load(report_id)
        preview = build_preview(
            draft,
            remove_fields=tuple(remove_fields or ()),
            remove_attachments=tuple(remove_attachments or ()),
        )
        assert_clean(preview.outbound_bytes())
        export_dir = self.store.root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        target = export_dir / f"{draft.report_id}.json"
        target.write_bytes(preview.outbound_bytes())
        self.export_store.record_submission(
            report_id=draft.report_id,
            fingerprint=draft.fingerprint,
            payload_sha256=preview.payload_sha256,
            destination="local-export",
            issue_url=str(target),
            issue_number=None,
            total_bytes=preview.total_bytes,
            field_names=list(preview.fields_included),
            attachment_names=[p.name for p in preview.attachments],
            credential_source="none:local-export",
        )
        return {
            "report_id": draft.report_id,
            "path": str(target),
            "payload_sha256": preview.payload_sha256,
            "total_bytes": preview.total_bytes,
        }

    # -- explicit consent revocation ------------------------------------------------------

    def revoke(self, report_id: str) -> BugReportDraft:
        """Withdraw approval. The consent manifest is destroyed, not marked: a later
        submit demands a fresh decision on the then-current bytes."""
        draft = self.store.load(report_id)
        if draft.consent is None:
            return draft
        cleared = draft.with_updates(consent=None)
        self.store.save(cleared)
        return cleared

    def status(self, report_id: str) -> dict:
        draft = self.store.load(report_id)
        return {
            "report_id": draft.report_id,
            "created_at": draft.created_at,
            "title": draft.title,
            "category": draft.category,
            "state": draft.state,
            "fingerprint": draft.fingerprint,
            "destination_repo": draft.destination_repo,
            "consent": draft.consent.to_dict() if draft.consent is not None else None,
            "last_error": draft.last_error,
            "last_issue_url": draft.last_issue_url,
        }


def _prose(text: str, *, max_chars: int = MAX_PROSE_CHARS, summary: RedactionSummary | None = None) -> tuple[str, RedactionSummary]:
    from core.bug_report.redaction import redact_text

    sanitized, text_summary = redact_text(str(text or ""))
    if summary is not None:
        for rule, count in text_summary.rule_counts.items():
            summary.rule_counts[rule] = summary.rule_counts.get(rule, 0) + count
        summary.total_replacements += text_summary.total_replacements
    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars].rstrip() + "…[truncated]"
    return sanitized, text_summary


def _validate_consent(draft: BugReportDraft) -> None:
    consent = draft.consent
    if consent.report_id != draft.report_id or consent.destination_repo != draft.destination_repo:
        raise ApprovalMismatchError("consent does not bind this draft (id or destination differs)")


def get_service() -> BugReportService:
    """Fresh service bound to the CURRENT runtime home (tests re-home per test; a cached
    singleton would silently point at a previous test's directory)."""
    return BugReportService()


def reset_service_for_tests() -> None:
    """Retained for API compatibility; get_service() no longer caches."""
    return None


__all__ = ["BugReportService", "get_service", "reset_service_for_tests"]
