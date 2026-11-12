"""Editorial review, privacy withhold and final-byte identity (Checkpoint 5).

Every draft review surfaces: objective/audience/platform, exact version and
content hash, supported claims with evidence pointers, opinions labeled,
unknown/disputed claims, privacy/secret findings, AI-sludge/repetition/
platform-fit findings, links and media requirements, and the disposition
(clean copy / needs_changes / refused / NO_POST).

Approval here is a LOCAL decision record. It can never mint external effect
success: publication outcomes live in the handoff boundary (Checkpoint 6)
and the canonical finalization/receipt authorities.
"""

from __future__ import annotations

import re
from typing import Any

from core.social_content import adaptation
from core.social_content.platform_policy import VALIDATION_UNKNOWN

# receipt-state vocabulary this lane distinguishes locally; external states
# (dispatch attempted / acknowledged / verified / unknown) belong to the
# handoff boundary and the canonical finalization authority.
LOCAL_RECEIPT_STATES = (
    "draft_saved",
    "draft_approved",
    "draft_refused",
    "handoff_prepared",
    "handoff_refused",
    "publishing_unavailable",
    "scheduling_unavailable",
    "not_attempted",
)


def review_draft(
    draft: dict[str, Any],
    *,
    campaign: dict[str, Any] | None = None,
    research_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one review surface for one draft version. Deterministic."""
    body = str(draft.get("body_bytes", ""))
    claims_ledger = list(draft.get("claim_ledger") or [])
    findings: list[dict[str, str]] = []
    disposition = "clean_copy"

    def _flag(code: str, severity: str, detail: str = "") -> None:
        findings.append({"code": code, "severity": severity, "detail": detail})

    # claims: unknown or disputed can never ride clean copy
    for claim in claims_ledger:
        status = str(claim.get("status", "unknown"))
        if status in ("unknown", "disputed"):
            _flag("claim_not_supported", "failure",
                  f"claim {str(claim.get('text', ''))[:60]!r} is {status}; it may appear "
                  "only as a marked question/opinion")
            disposition = "needs_changes"
        elif status == "opinion":
            pass  # labeled opinions are allowed; the ledger IS the label

    # privacy: hard secrets never reach the store; soft findings surface here
    privacy = list(draft.get("privacy_findings") or [])
    if privacy:
        _flag("privacy_findings", "warning", f"{privacy}")
    if _publish_withhold(body):
        _flag("privacy_withhold", "failure",
              "the A8 availability verdict withholds this text from publication")
        disposition = "refused"

    # AI sludge / repetition / platform fit — reuse the X editorial sludge
    # detector as the shared editorial vocabulary, plus platform-fit checks
    from core.x_editorial import sludge_hits

    sludge = sludge_hits(body)
    if sludge:
        _flag("ai_sludge", "warning", f"{sludge}")

    platform = str(draft.get("platform", ""))
    adapted = adaptation.adapt(
        platform=platform, body=body, format=str(draft.get("format", "post")),
        source_body=None,
    )
    for f in adapted.findings:
        if f.severity == "failure":
            _flag(f"platform_fit_{f.code}", "failure", f.detail)
            disposition = "needs_changes"
        elif f.severity == "unknown":
            _flag(f"platform_fit_{f.code}", "unknown", f.detail)

    links = sorted(set(re.findall(r"https?://\S+", body)))
    if draft.get("media_brief"):
        _flag("media_required", "info",
              "draft carries a media brief; media must come from supplied evidence")

    if research_outcome and research_outcome.get("decision") == "NO_POST":
        disposition = "NO_POST"
        _flag("no_post", "info", research_outcome.get("reason", ""))

    report: dict[str, Any] = {
        "draft_id": draft.get("draft_id"),
        "version": draft.get("version"),
        "content_hash": draft.get("content_hash"),
        "platform": platform,
        "objective": (campaign or {}).get("objective", ""),
        "audience": (campaign or {}).get("audience", ""),
        "claims": [
            {"text": c.get("text", ""), "status": c.get("status", "unknown"),
             "evidence_ids": list(c.get("evidence_ids", []))}
            for c in claims_ledger
        ],
        "privacy_findings": privacy,
        "findings": findings,
        "links": links,
        "disposition": disposition,
        "exact_bytes": body if disposition == "clean_copy" else None,
    }
    return report


def _publish_withhold(body: str) -> bool:
    """The A8 publish-side fence: an ERASE/WITHHELD verdict withholds the text.
    An absent store is ungoverned legacy (documented A8 semantics); a store
    error fails CLOSED."""
    try:
        from core.finalization import writer_may_publish_public_text

        return not writer_may_publish_public_text(body)
    except Exception:
        return True


# ---------------------------------------------------------------- approval


def approve_exact(
    store, *, draft_id: str, draft_version: int, operator: str = "operator",
    expires_at: float | None = None, destination: str = "",
) -> dict[str, Any]:
    """Approval that binds exact bytes/platform/version/expiry, refuses replay,
    and never claims publication. ``store`` is storage.social_content_store."""
    draft = store.get_draft(draft_id, version=int(draft_version))
    if draft is None:
        return {"ok": False, "code": "unknown_draft",
                "summary": f"no draft {draft_id!r} at version {draft_version}"}
    identity = {
        "draft_id": draft_id, "draft_version": int(draft_version),
        "content_hash": draft["content_hash"], "platform": draft["platform"],
    }
    if store.has_decision(decision="approved", **identity):
        return {"ok": False, "code": "approval_replay_refused",
                "summary": "this exact draft identity is already approved"}
    review = review_draft(draft)
    if review["disposition"] == "refused":
        return {"ok": False, "code": "privacy_withhold",
                "summary": "the draft is withheld; it cannot be approved",
                "review": review}
    dec = store.record_decision(
        decision="approved", operator=operator, reason="exact-byte approval",
        destination=destination, expires_at=expires_at, **identity,
    )
    dec["approval_is_not_publication"] = True
    return {"ok": True, "decision": dec,
            "receipt_state": "draft_approved"}


def cancel_approval(
    store, *, draft_id: str, draft_version: int, reason: str = "",
) -> dict[str, Any]:
    """A later refuse decision on the same identity cancels the approval."""
    draft = store.get_draft(draft_id, version=int(draft_version))
    if draft is None:
        return {"ok": False, "code": "unknown_draft"}
    identity = {
        "draft_id": draft_id, "draft_version": int(draft_version),
        "content_hash": draft["content_hash"], "platform": draft["platform"],
    }
    if not store.has_decision(decision="approved", **identity):
        return {"ok": False, "code": "nothing_to_cancel"}
    store.record_decision(decision="refused", reason=reason or "approval cancelled",
                          **identity)
    covered = store.approval_for(
        draft_id=identity["draft_id"], version=identity["draft_version"],
        content_hash=identity["content_hash"], platform=identity["platform"],
    )
    return {"ok": covered is None, "receipt_state": "draft_refused"}


def approval_status(store, *, draft_id: str, version: int | None = None) -> dict[str, Any]:
    """Exact receipt-language status for a draft version."""
    draft = store.get_draft(draft_id, version=version)
    if draft is None:
        return {"ok": False, "code": "unknown_draft"}
    covered = store.approval_for(
        draft_id=draft["draft_id"], version=draft["version"],
        content_hash=draft["content_hash"], platform=draft["platform"],
    )
    if covered is None:
        return {
            "ok": True, "approved": False,
            "receipt_state": "draft_saved",
            "summary": (
                f"draft {draft['draft_id']} v{draft['version']} "
                f"({draft['platform']}, hash {draft['content_hash'][:12]}) is saved and "
                "NOT approved for publication; approval is not publication"
            ),
        }
    return {
        "ok": True, "approved": True, "receipt_state": "draft_approved",
        "approval": covered,
        "summary": (
            f"draft {draft['draft_id']} v{draft['version']} ({draft['platform']}, hash "
            f"{draft['content_hash'][:12]}) is APPROVED as exact bytes; approval is not "
            "publication and no external dispatch has occurred"
        ),
    }
