"""Typed proposed mutations. A model PROPOSES; the gate decides; the adapter executes.

Each action is a frozen dataclass carrying:
- its exact target identity and SHAs (no ambient "current repo"),
- an idempotency key derived from canonical content, so a duplicate retry of
  the SAME intent is recognisable to the adapter — and a DIFFERENT intent can
  never collide with it.

Actions are data, not calls: they can be shown to a user for approval, journaled,
and replayed without executing anything.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.remote_forge.identity import RepoIdentity

# The permission vocabulary. One token per remote/local capability; there is no
# wildcard and no prefix implication (kernel Law 3). READ_* are broadly grantable
# when authenticated; every WRITE-class token is separately granted.
PERMISSIONS: tuple[str, ...] = (
    "read_repo",
    "read_pr",
    "read_issue",
    "read_ci",
    "write_local",
    "create_branch",
    "push_branch",
    "create_pr",
    "update_pr",
    "comment",
    "review",
    "merge",
    "delete_remote",
    "change_repo_settings",
)

READ_PERMISSIONS = frozenset({"read_repo", "read_pr", "read_issue", "read_ci"})
MUTATING_PERMISSIONS = frozenset(set(PERMISSIONS) - READ_PERMISSIONS)


class UnknownPermission(ValueError):
    pass


def capability_token(permission: str) -> str:
    """`forge.read_pr`-style kernel Law 3 token for a forge permission."""
    if permission not in PERMISSIONS:
        raise UnknownPermission(
            f"{permission!r} is not a forge permission; expected one of {PERMISSIONS}"
        )
    return f"forge.{permission}"


@dataclass(frozen=True, kw_only=True)
class ForgeAction:
    """Base shape only — construct the concrete actions below."""

    kind: str
    permission: str
    identity: RepoIdentity

    def payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def idempotency_key(self) -> str:
        """SHA-256 over kind + full identity + canonical payload.

        Two identical proposals share a key (the adapter dedups them); changing
        ANY field — target SHA, branch, title — changes the key.
        """
        material = {
            "kind": self.kind,
            "provider": self.identity.provider,
            "owner": self.identity.owner,
            "repo": self.identity.repo,
            "remote_url": self.identity.remote_url,
            "payload": self.payload(),
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class CreateBranch(ForgeAction):
    branch: str
    from_sha: str  # the EXACT commit to branch from, never "whatever main is now"
    kind: str = "create_branch"
    permission: str = "create_branch"

    def payload(self) -> dict[str, Any]:
        return {"branch": self.branch, "from_sha": self.from_sha}


@dataclass(frozen=True, kw_only=True)
class PushBranch(ForgeAction):
    branch: str
    head_sha: str  # local commit being pushed, bound at proposal time
    force: bool = False
    kind: str = "push_branch"
    permission: str = "push_branch"

    def payload(self) -> dict[str, Any]:
        return {"branch": self.branch, "head_sha": self.head_sha, "force": self.force}


@dataclass(frozen=True, kw_only=True)
class CreatePR(ForgeAction):
    title: str
    head_branch: str
    base_branch: str
    body: str = ""
    head_fork_owner: str = ""  # non-empty when head lives on a fork
    kind: str = "create_pr"
    permission: str = "create_pr"

    def payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "head_branch": self.head_branch,
            "base_branch": self.base_branch,
            "body": self.body,
            "head_fork_owner": self.head_fork_owner,
        }


@dataclass(frozen=True, kw_only=True)
class UpdatePR(ForgeAction):
    number: int
    expected_head_sha: str  # refuse update against a PR that moved underneath
    new_body: str | None = None
    new_title: str | None = None
    kind: str = "update_pr"
    permission: str = "update_pr"

    def payload(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "expected_head_sha": self.expected_head_sha,
            "new_title": self.new_title,
            "new_body": self.new_body,
        }


@dataclass(frozen=True, kw_only=True)
class Comment(ForgeAction):
    number: int
    body: str
    expected_head_sha: str = ""
    kind: str = "comment"
    permission: str = "comment"

    def payload(self) -> dict[str, Any]:
        return {"number": self.number, "body": self.body}


@dataclass(frozen=True, kw_only=True)
class MergePR(ForgeAction):
    number: int
    expected_head_sha: str  # merge ONLY this exact head; a moved PR refuses
    merge_method: str = "merge"
    kind: str = "merge"
    permission: str = "merge"

    def payload(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "expected_head_sha": self.expected_head_sha,
            "merge_method": self.merge_method,
        }
