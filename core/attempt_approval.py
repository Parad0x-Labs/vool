"""Step 10 correction #7: durable approval continuation for a WAITING_APPROVAL subtask, scoped
narrowly to proving the six required properties against the EXISTING `runtime_attempt_approvals`
table (Step 7's schema) -- this does not redesign `core.mode_permission_policy`'s product-wide
permission matrix, it only makes ONE approval decision durable and re-verifiable across a restart
for the typed-attempt lifecycle.

No LIVE_DATA scenario in the current product reaches WAITING_APPROVAL today: public,
unauthenticated, read-only weather/market retrieval never requires approval under the existing
capability/effect policy (see Wave 2's product decision). This module is exercised by direct unit
tests against synthetic approval-required subtasks, not by a live end-to-end daemon scenario --
disclosed in the delivery report rather than implied by a production drive that doesn't exist yet.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def compute_action_digest(tool_intent: str, arguments: dict[str, Any]) -> str:
    """A stable digest of ONE subtask's action -- its tool intent plus its exact arguments.
    Any change to either changes the digest, which is what makes an approval invalid the moment
    the underlying action it was granted for changes (Step 10: "changed arguments invalidate
    it")."""
    payload = json.dumps(
        {"tool_intent": str(tool_intent or ""), "arguments": arguments or {}},
        sort_keys=True, ensure_ascii=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_plan_digest(subtask_digests: list[str]) -> str:
    """A stable digest of the WHOLE plan an approval was granted against -- Step 10: "WAITING_APPROVAL
    retains the complete immutable plan." An approval for one subtask is scoped not just to that
    subtask's own action but to the plan it was part of, so a plan that gained/lost/reordered
    subtasks between when approval was requested and when it's checked is also a different plan."""
    payload = json.dumps(sorted(subtask_digests), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Final Repair 5 (Mnemosyne final review, 2026-08-07): the "current" policy version this module's
# own approval semantics were defined against -- bump this whenever compute_action_digest/
# compute_plan_digest's hashing scheme, or what "allow" durably means, changes in a way that
# should invalidate every approval granted under the old scheme. Deliberately NOT sourced from
# core.mode_permission_policy (out of scope for this narrow module, see the module docstring).
CURRENT_APPROVAL_POLICY_VERSION = "v1"


def is_approval_valid(
    approval: dict[str, Any] | None,
    *,
    action_digest: str,
    plan_digest: str,
    policy_version: str,
    now_iso: str | None = None,
) -> tuple[bool, str]:
    """(valid, reason). An approval is valid ONLY when it decided "allow" for the EXACT matching
    action_digest AND plan_digest AND policy_version, and has not expired. Everything else -- no
    approval found, a denial, a digest mismatch (changed arguments or changed plan), a
    policy-version mismatch, or an expired grant -- is invalid, and the caller must treat the
    subtask as still requiring a fresh approval decision. Restart never silently grants: this
    function only ever returns True for a decision that was actually durably recorded and still
    matches, never inferred from absence of a denial.

    `policy_version` was persisted from the start but never compared (Final Repair 5) -- an
    approval recorded under a stale policy version silently kept authorizing actions under a
    NEWER one, which is exactly the gap a policy-version bump exists to close. A missing/empty
    STORED policy_version is never reusable either: there is no documented compatibility rule
    that treats an unversioned legacy approval as matching any current version, so it fails
    closed like any other mismatch, not open.
    """
    if approval is None:
        return False, "no approval decision on record"
    if str(approval.get("decision") or "") != "allow":
        return False, f"decision was {approval.get('decision') or 'undecided'!r}, not allow"
    if str(approval.get("action_digest") or "") != action_digest:
        return False, "action_digest mismatch -- the arguments changed since approval was granted"
    if str(approval.get("plan_digest") or "") != plan_digest:
        return False, "plan_digest mismatch -- the plan changed since approval was granted"
    stored_policy_version = str(approval.get("policy_version") or "")
    if not stored_policy_version or stored_policy_version != str(policy_version or ""):
        return False, (
            f"policy_version mismatch -- stored={stored_policy_version!r}, current={policy_version!r} "
            "(no documented compatibility rule permits reuse across policy versions)"
        )
    expires_at = approval.get("expires_at")
    if expires_at:
        now = str(now_iso or _utcnow_iso())
        if str(expires_at) < now:
            return False, f"approval expired at {expires_at}"
    return True, "approval matches the exact action digest, plan digest, and policy version, and has not expired"
