"""Final Repair 5 (Mnemosyne final review, 2026-08-07): `policy_version` was persisted on every
approval decision from the start but never checked when deciding whether a stored approval remains
valid -- an approval recorded under a stale policy version silently kept authorizing actions under
a newer one. Fixed: `is_approval_valid` now requires `policy_version` as an explicit argument and
compares it against the stored value; reuse requires action digest match, plan digest match, policy
version match, the approval not expired, and the decision being "allow" -- all five, not four.

A missing/empty STORED policy_version fails closed (not reusable) -- no documented compatibility
rule treats an unversioned legacy approval as matching any current version.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.agent_runtime.attempt_approval import (
    CURRENT_APPROVAL_POLICY_VERSION,
    compute_action_digest,
    compute_plan_digest,
    is_approval_valid,
)

_DIGEST = compute_action_digest("web.research", {"asset_key": "gold"})
_PLAN_DIGEST = compute_plan_digest([_DIGEST])


def _approval(**overrides) -> dict:
    base = {
        "decision": "allow",
        "action_digest": _DIGEST,
        "plan_digest": _PLAN_DIGEST,
        "policy_version": CURRENT_APPROVAL_POLICY_VERSION,
        "expires_at": None,
    }
    base.update(overrides)
    return base


class PolicyVersionEnforcementTests(unittest.TestCase):
    def test_same_policy_version_is_reusable(self) -> None:
        valid, _ = is_approval_valid(
            _approval(), action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertTrue(valid)

    def test_changed_policy_version_is_not_reusable(self) -> None:
        approval = _approval(policy_version="v1")
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version="v2",
        )
        self.assertFalse(valid)
        self.assertIn("policy_version", reason)

    def test_missing_stored_policy_version_is_not_reusable(self) -> None:
        """No documented compatibility rule permits reuse of an unversioned legacy approval."""
        approval = _approval(policy_version="")
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("policy_version", reason)

    def test_missing_stored_policy_version_key_entirely_is_not_reusable(self) -> None:
        approval = _approval()
        del approval["policy_version"]
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("policy_version", reason)

    def test_changed_action_digest_remains_invalid_regardless_of_policy_version(self) -> None:
        approval = _approval()
        other_digest = compute_action_digest("web.research", {"asset_key": "silver"})
        valid, reason = is_approval_valid(
            approval, action_digest=other_digest, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("action_digest", reason)

    def test_changed_plan_remains_invalid_regardless_of_policy_version(self) -> None:
        approval = _approval()
        other_plan_digest = compute_plan_digest([_DIGEST, "extra"])
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=other_plan_digest, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("plan_digest", reason)

    def test_expired_approval_remains_invalid_even_with_matching_policy_version(self) -> None:
        approval = _approval(expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("expired", reason)

    def test_denied_approval_remains_invalid_even_with_matching_policy_version(self) -> None:
        approval = _approval(decision="deny")
        valid, reason = is_approval_valid(
            approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(valid)
        self.assertIn("deny", reason)

    def test_restart_does_not_alter_the_result(self) -> None:
        """A "restart" here is simply re-invoking the pure function with the SAME persisted dict
        (as a fresh process reading the same durable row would) -- the result must be identical
        every time, proving there is no hidden in-memory state influencing the outcome."""
        approval = _approval()
        results = [
            is_approval_valid(approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION)
            for _ in range(3)
        ]
        self.assertTrue(all(r[0] for r in results))
        self.assertEqual(len({r[1] for r in results}), 1, "the reason string must also be stable across repeated calls")

        mismatched = _approval(policy_version="v0")
        mismatched_results = [
            is_approval_valid(mismatched, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version=CURRENT_APPROVAL_POLICY_VERSION)
            for _ in range(3)
        ]
        self.assertFalse(any(r[0] for r in mismatched_results))


class SabotagePolicyVersionComparisonTests(unittest.TestCase):
    """Sabotage: remove the policy-version comparison -- the changed-policy-version test must
    execute and fail (reproduce the wrongly-authorized outcome)."""

    @staticmethod
    def _sabotaged_is_approval_valid(approval, *, action_digest, plan_digest, policy_version, now_iso=None):
        if approval is None:
            return False, "no approval decision on record"
        if str(approval.get("decision") or "") != "allow":
            return False, "not allow"
        if str(approval.get("action_digest") or "") != action_digest:
            return False, "action_digest mismatch"
        if str(approval.get("plan_digest") or "") != plan_digest:
            return False, "plan_digest mismatch"
        # Sabotage: the policy_version comparison block is simply absent.
        expires_at = approval.get("expires_at")
        if expires_at:
            now = str(now_iso or datetime.now(timezone.utc).isoformat())
            if str(expires_at) < now:
                return False, "expired"
        return True, "sabotaged: policy_version never compared"

    def test_sabotage_missing_policy_version_check_wrongly_authorizes_a_stale_approval(self) -> None:
        stale_approval = _approval(policy_version="v1-deprecated")

        sabotaged_valid, _ = self._sabotaged_is_approval_valid(
            stale_approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version="v2-current",
        )
        self.assertTrue(sabotaged_valid, "sabotage should have wrongly authorized a stale-policy-version approval")

        # Control: the REAL function correctly rejects it.
        real_valid, reason = is_approval_valid(
            stale_approval, action_digest=_DIGEST, plan_digest=_PLAN_DIGEST, policy_version="v2-current",
        )
        self.assertFalse(real_valid)
        self.assertIn("policy_version", reason)


if __name__ == "__main__":
    unittest.main()
