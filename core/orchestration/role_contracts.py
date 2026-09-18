from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskRole = Literal["queen", "coder", "verifier", "researcher", "memory_clerk", "narrator"]


@dataclass(frozen=True)
class RoleContract:
    role: TaskRole
    provider_role: str
    default_tool_permissions: tuple[str, ...]
    default_allowed_side_effects: tuple[str, ...]
    can_merge_results: bool
    can_mutate_workspace: bool


_ROLE_CONTRACTS: dict[TaskRole, RoleContract] = {
    "queen": RoleContract(
        role="queen",
        provider_role="queen",
        default_tool_permissions=("plan", "merge", "summarize"),
        default_allowed_side_effects=(),
        can_merge_results=True,
        can_mutate_workspace=False,
    ),
    "coder": RoleContract(
        role="coder",
        provider_role="drone",
        default_tool_permissions=("workspace.read", "workspace.write", "workspace.git", "workspace.validate"),
        default_allowed_side_effects=("workspace_write",),
        can_merge_results=False,
        can_mutate_workspace=True,
    ),
    "verifier": RoleContract(
        role="verifier",
        provider_role="drone",
        default_tool_permissions=("workspace.read", "workspace.git", "workspace.validate"),
        default_allowed_side_effects=(),
        can_merge_results=False,
        can_mutate_workspace=False,
    ),
    "researcher": RoleContract(
        role="researcher",
        provider_role="auto",
        default_tool_permissions=("workspace.read", "web.search", "web.research"),
        default_allowed_side_effects=(),
        can_merge_results=False,
        can_mutate_workspace=False,
    ),
    "memory_clerk": RoleContract(
        role="memory_clerk",
        provider_role="auto",
        default_tool_permissions=("workspace.read", "memory.write", "proof.pack"),
        default_allowed_side_effects=("memory_write",),
        can_merge_results=False,
        can_mutate_workspace=False,
    ),
    "narrator": RoleContract(
        role="narrator",
        provider_role="queen",
        default_tool_permissions=("summarize", "format"),
        default_allowed_side_effects=(),
        can_merge_results=False,
        can_mutate_workspace=False,
    ),
}


def get_role_contract(role: TaskRole) -> RoleContract:
    return _ROLE_CONTRACTS[role]


def provider_role_for_task_role(role: TaskRole) -> str:
    return get_role_contract(role).provider_role


def all_role_contracts() -> tuple[RoleContract, ...]:
    return tuple(_ROLE_CONTRACTS.values())
