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
    ANSWER_REASONS,
    SCHEMA_VERSION,
    AnswerEvidence,
    ApprovalMismatchError,
    ApprovalRequiredError,
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
from core.bug_report.synthesis import ANSWER_REASON_LABELS
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
        # Browser / clipboard hand-offs keep a third ledger: VOOL sent nothing itself, the
        # person carried the approved bytes to GitHub (or their clipboard) by hand.
        self.handoff_store = ReceiptStore(root / "handoffs.jsonl")
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
        machine, health = _machine_and_health(models)
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
            schema_version=SCHEMA_VERSION,
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
            machine=machine,
            health=health,
        )
        draft = draft.with_updates(attachments=attachment_plans(draft))
        self.store.save(draft)
        return draft

    def create_answer_draft(
        self,
        *,
        session_id: str,
        request_id: str,
        reason: str,
        excerpt: str = "",
        what_was_wrong: str = "",
        should_have_said: str = "",
        include_question: bool = False,
        include_receipt: bool = True,
        title: str = "",
        destination_repo: str = "",
    ) -> BugReportDraft:
        """A wrong-answer report on ONE served answer.

        The answer (and, only when asked, the question) is read server-side from the
        persisted transcript for this exact (session, request) pair -- never taken from the
        page -- and a selected excerpt must be a piece of that answer. Everything quoted is
        redacted, masked for this machine's identifiers, capped, and shown in the preview."""
        from core.bug_report.answer import (
            MAX_EXCERPT_CHARS,
            MAX_QUESTION_CHARS,
            AnswerNotFoundError,
            build_receipt,
            excerpt_belongs_to,
            load_turn,
        )
        from core.bug_report.destination import default_destination, is_valid_destination

        clean_reason = str(reason or "").strip()
        if clean_reason not in ANSWER_REASONS:
            raise CaptureRefusedError(f"unknown reason: {reason!r}")
        resolved_destination = str(destination_repo or "").strip() or default_destination()
        if not is_valid_destination(resolved_destination):
            raise CaptureRefusedError(f"invalid destination repo: {destination_repo!r}")

        question, answer_text = load_turn(session_id, request_id)
        selected = str(excerpt or "").strip()
        if selected and not excerpt_belongs_to(selected, answer_text):
            raise AnswerNotFoundError("the selected text is not part of this answer")
        source = "selection" if selected else "whole_answer"

        total_summary = RedactionSummary()
        clean_excerpt, _s = _prose(selected or answer_text, max_chars=MAX_EXCERPT_CHARS, summary=total_summary)
        clean_question = ""
        if include_question and question.strip():
            clean_question, _s = _prose(question, max_chars=MAX_QUESTION_CHARS, summary=total_summary)
        clean_wrong, _s = _prose(what_was_wrong, summary=total_summary)
        clean_should, _s = _prose(should_have_said, summary=total_summary)
        clean_title, _s = _prose(title, max_chars=160, summary=total_summary)

        receipt = build_receipt(session_id, request_id) if include_receipt else None
        models = (receipt.model,) if receipt is not None and receipt.model != "unknown" else ()
        components = InvolvedComponents(
            lanes=(),
            tools=tuple(dict.fromkeys(a.name for a in receipt.actions if a.name != "unnamed")) if receipt else (),
            models=models,
        )
        environment = capture_environment(self.project_root)
        machine, health = _machine_and_health(models)
        try:
            flags_snapshot = {name: bool(state) for name, state in runtime_flags.flag_state().items()}
        except Exception:
            flags_snapshot = {}
        evidence = AnswerEvidence(
            reason=clean_reason,
            excerpt=clean_excerpt,
            excerpt_source=source,
            question=clean_question,
            receipt=receipt,
        )
        category = "wrong-answer"
        draft = BugReportDraft(
            report_id="br_" + uuid.uuid4().hex[:12],
            created_at=_utc_now(),
            schema_version=SCHEMA_VERSION,
            environment=environment,
            title=clean_title or ANSWER_REASON_LABELS.get(clean_reason, "Wrong answer"),
            category=category,
            expected=clean_should,
            actual=clean_wrong,
            repro_steps=(),
            error=None,
            components=components,
            logs=(),
            flags_snapshot=flags_snapshot,
            attachments=(),
            fingerprint="",
            redaction_summary=total_summary,
            destination_repo=resolved_destination,
            kind="wrong_answer",
            machine=machine,
            health=health,
            answer=evidence,
            local_ref={"session_id": str(session_id), "request_id": str(request_id)},
        )
        draft = draft.with_updates(fingerprint=_fingerprint_of(draft))
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
        changes["fingerprint"] = _fingerprint_of(updated)
        changes["redaction_summary"] = summary
        final = draft.with_updates(**changes)
        final = final.with_updates(attachments=attachment_plans(final))
        self.store.save(final)
        return final

    def delete_draft(self, report_id: str) -> bool:
        return self.store.delete(report_id)

    def list_reports(self) -> list[dict]:
        """My reports: every local draft, newest first, with how (if at all) it left."""
        left: dict[str, list[dict]] = {}
        for ledger, how in (
            (self.receipt_store, "sent_with_token"),
            (self.handoff_store, ""),
            (self.export_store, "saved_locally"),
        ):
            try:
                rows = ledger.rows()
            except Exception:
                rows = []
            for row in rows:
                left.setdefault(str(row.get("report_id")), []).append({
                    "how": how or str(row.get("destination", "")).split(":", 1)[0],
                    "at": row.get("submitted_at", ""),
                    "issue_url": row.get("issue_url", "") if how == "sent_with_token" else "",
                })
        reports = []
        for report_id in self.store.ids():
            try:
                draft = self.store.load(report_id)
            except (DraftNotFoundError, ValueError):
                continue
            reports.append({
                "report_id": draft.report_id,
                "created_at": draft.created_at,
                "kind": draft.kind,
                "title": draft.title,
                "fingerprint": draft.fingerprint,
                "state": draft.state,
                "destination_repo": draft.destination_repo,
                "issue_url": draft.last_issue_url,
                "approved": draft.consent is not None,
                "handoffs": left.get(draft.report_id, []),
            })
        reports.sort(key=lambda row: row["created_at"], reverse=True)
        return reports

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

    # -- hand-off: the person carries the approved bytes to GitHub themselves ---------------

    def handoff(
        self,
        report_id: str,
        *,
        method: str,
        remove_fields=(),
        remove_attachments=(),
    ) -> dict:
        """``browser``: a prefilled GitHub new-issue link built from the APPROVED bytes (consent
        required, exactly as for a token submit). ``clipboard``: the previewed bytes as text
        (no consent needed -- like a local copy, it does not leave the machine by itself).
        VOOL opens no connection in either case; a metadata-only receipt records the hand-off."""
        from core.bug_report.handoff import new_issue_link, search_link
        from core.bug_report.scanner import assert_clean

        draft = self.store.load(report_id)
        if method == "browser":
            if draft.consent is None:
                raise ApprovalRequiredError("approve the exact report before opening it on GitHub")
            _validate_consent(draft)
            removals, attachment_removals = draft.consent.removed_fields, draft.consent.removed_attachments
        elif method == "clipboard":
            removals, attachment_removals = tuple(remove_fields or ()), tuple(remove_attachments or ())
        else:
            raise ValueError(f"unknown hand-off method: {method!r}")
        preview = build_preview(draft, remove_fields=removals, remove_attachments=attachment_removals)
        if method == "browser" and preview.payload_sha256 != draft.consent.payload_sha256:
            raise ApprovalMismatchError("the report changed after approval; approve the new version")
        assert_clean(preview.outbound_bytes())
        title, body = str(preview.issue["title"]), str(preview.issue["body"])
        result: dict = {
            "report_id": draft.report_id,
            "method": method,
            "payload_sha256": preview.payload_sha256,
            "total_bytes": preview.total_bytes,
            "search_url": search_link(destination=draft.destination_repo, fingerprint=draft.fingerprint),
        }
        if method == "browser":
            link = new_issue_link(destination=draft.destination_repo, title=title, body=body, kind=draft.kind)
            result["url"] = link["url"]
            result["complete"] = link["complete"]
            # When the link could not carry the report, the page puts these bytes on the
            # clipboard for pasting -- the same approved bytes, nothing re-rendered.
            result["clipboard_text"] = "" if link["complete"] else body
        else:
            result["clipboard_text"] = f"# {title}\n\n{body}"
        self.handoff_store.record_submission(
            report_id=draft.report_id,
            fingerprint=draft.fingerprint,
            payload_sha256=preview.payload_sha256,
            destination=f"{method}:{draft.destination_repo}",
            issue_url="",
            issue_number=None,
            total_bytes=preview.total_bytes,
            field_names=list(preview.fields_included),
            attachment_names=[p.name for p in preview.attachments],
            credential_source="none:handoff",
        )
        return result

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
    from core.bug_report.scanner import mask_local_identifiers

    sanitized, text_summary = redact_text(str(text or ""))
    sanitized, local_hits = mask_local_identifiers(sanitized)
    if local_hits:
        text_summary.rule_counts["local_identity"] = text_summary.rule_counts.get("local_identity", 0) + local_hits
        text_summary.total_replacements += local_hits
    if summary is not None:
        for rule, count in text_summary.rule_counts.items():
            summary.rule_counts[rule] = summary.rule_counts.get(rule, 0) + count
        summary.total_replacements += text_summary.total_replacements
    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars].rstrip() + "…[truncated]"
    return sanitized, text_summary


def _machine_and_health(models) -> tuple[dict[str, str], dict[str, str]]:
    """Machine shape + local health snapshot; a probe that breaks yields nothing, never a guess."""
    from core.bug_report.machine import capture_health, capture_machine

    try:
        machine = capture_machine()
    except Exception:
        machine = {}
    try:
        health = capture_health(models=tuple(models or ()))
    except Exception:
        health = {}
    return machine, health


def _fingerprint_of(draft: BugReportDraft) -> str:
    """Dedup key. A wrong-answer report also keys on WHAT was quoted (as a digest of its
    letters and digits) and why, so two different wrong answers from the same model are two
    reports, while the same answer reported twice is one."""
    import hashlib

    steps = list(draft.repro_steps)
    if draft.answer is not None:
        quoted = "".join(ch for ch in draft.answer.excerpt.lower() if ch.isalnum())
        steps.append(f"answer:{draft.answer.reason}:{hashlib.sha256(quoted.encode('utf-8')).hexdigest()[:16]}")
    return compute_fingerprint(
        category=draft.category,
        error=draft.error,
        components=draft.components,
        repro_steps=steps,
    )


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
