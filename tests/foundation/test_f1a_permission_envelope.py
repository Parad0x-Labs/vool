"""F1-A / K-06 + K-07 permission/effect bypass closure tests."""
from __future__ import annotations

import pytest

from core.orchestration.role_contracts import _ROLE_CONTRACTS
from core.orchestration.task_envelope import task_envelope_from_dict


def test_model_cannot_widen_permissions_beyond_role_contract():
    role = "verifier"  # contract: read/git/validate only — no workspace.write
    envelope = task_envelope_from_dict(
        {
            "task_id": "t-1",
            "role": role,
            "goal": "verify",
            # RED MUTATION: model declares a capability its role does not hold.
            "tool_permissions": ["workspace.read", "workspace.write"],
            "allowed_side_effects": ["workspace_write", "nonexistent_effect"],
        }
    )
    contract = _ROLE_CONTRACTS[role]
    assert "workspace.write" not in envelope.tool_permissions
    assert set(envelope.tool_permissions) <= set(contract.default_tool_permissions)
    assert set(envelope.allowed_side_effects) <= set(contract.default_allowed_side_effects)


def test_payload_may_narrow_but_not_widen():
    envelope = task_envelope_from_dict(
        {
            "task_id": "t-2",
            "role": "coder",
            "goal": "narrow",
            "tool_permissions": ["workspace.read"],
        }
    )
    assert tuple(envelope.tool_permissions) == ("workspace.read",)


def test_empty_payload_falls_back_to_contract_defaults():
    for role, contract in _ROLE_CONTRACTS.items():
        envelope = task_envelope_from_dict({"task_id": f"t-{role}", "role": role, "goal": "g"})
        assert tuple(envelope.tool_permissions) == tuple(contract.default_tool_permissions)
