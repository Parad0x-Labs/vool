"""Opt-in, privacy-safe bug reporting for VOOL.

Pipeline: capture bounded diagnostics -> deterministic local redaction -> allowlist
reconstruction -> secret scan -> reproduction synthesis (local/model-free; cloud only
restructures already-sanitized material and is OFF by default) -> exact-bytes preview ->
explicit user approval -> GitHub submission adapter -> durable receipt.

Nothing leaves the machine without a consent manifest bound to the exact outbound bytes,
and the LLM never owns sanitization. See docs/SAFE_BUG_REPORTER_2026-09-01.md.
"""
from core.bug_report.github_adapter import submit_to_github
from core.bug_report.schema import (
    ApprovalMismatchError,
    ApprovalRequiredError,
    BugReportDraft,
    BugReportError,
    CaptureRefusedError,
    ConsentManifest,
    DraftNotFoundError,
    ReportPreview,
    SanitizedMaterial,
    SubmissionResult,
    UnsafeReportContentError,
)
from core.bug_report.service import BugReportService, get_service, reset_service_for_tests

__all__ = [
    "ApprovalMismatchError",
    "ApprovalRequiredError",
    "BugReportDraft",
    "BugReportError",
    "BugReportService",
    "CaptureRefusedError",
    "ConsentManifest",
    "DraftNotFoundError",
    "ReportPreview",
    "SanitizedMaterial",
    "SubmissionResult",
    "UnsafeReportContentError",
    "get_service",
    "reset_service_for_tests",
    "submit_to_github",
]
