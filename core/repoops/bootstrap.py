"""Repository bootstrap: init / attach / clone / create-remote / initial push.

The lifecycle behind "Git Ninja, create a new repo for this project":

    project identity -> init local -> inspect -> propose initial commit
    -> commit (SHA evidence) -> PROPOSE remote creation -> authorization
    -> provider create -> bind CANONICAL identity FROM PROVIDER EVIDENCE
    (numeric id, actual owner/name, canonical URL — never from the request)
    -> configure remote -> push exact SHA with expected-empty precondition
    -> verify remote branch head == pushed SHA -> receipt.

CREATE_REPOSITORY is a remote mutation like any other: proposed, admitted by
the platform broker under 'forge.create_repository', executed by a provider.
Whether some Toolbelt-resolved credential could implement it is NOT this
layer's question — RepoOps reports IMPLEMENTATION AVAILABLE and AUTHORIZATION
NOT GRANTED as separate facts and duplicates no credential authority.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.platform.broker import EffectOutcome, EffectRequest, ExecutionBroker
from core.remote_forge.identity import RepoIdentity, parse_remote_url
from core.repoops.identity import RepositoryWorkspace, WorkspaceError


class BootstrapError(RuntimeError):
    pass


# ---------------------------------------------------------------- local init


def init_local_repo(path: str, *, default_branch: str = "main") -> RepositoryWorkspace:
    """Create a fresh local repository. Refuses to nest inside an existing one."""
    root = Path(os.path.realpath(path))
    if root.exists() and any(root.iterdir()) and not root.joinpath(".git").exists():
        # Non-empty non-repo directory: attaching is the explicit path, not silent init.
        raise BootstrapError(f"{root} is non-empty; use attach(), do not init over it")
    _check_not_inside_repo(root.parent)
    root.mkdir(parents=True, exist_ok=True)
    _git(str(root), "init", "-q", "-b", default_branch)
    return RepositoryWorkspace(root=str(root))


def _check_not_inside_repo(directory: Path) -> None:
    proc = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        raise BootstrapError(
            f"{directory} is inside existing repository {proc.stdout.strip()}"
        )


def _git(target: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", target, *args], capture_output=True,
                          text=True, timeout=60)
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# ---------------------------------------------------------------- attach


def attach(folder: str) -> tuple[RepositoryWorkspace, dict[str, str]]:
    """'Attach this folder' — PROVE which repository it actually belongs to.

    Returns the workspace bound to the parsed remote identity plus the raw
    remotes map. A folder whose remote cannot be exactly resolved is refused:
    we never guess identity from a directory name.
    """
    root = os.path.realpath(folder)
    if not os.path.isdir(root):
        raise BootstrapError(f"{folder} is not a directory")
    try:
        toplevel = _git(root, "rev-parse", "--show-toplevel")
        remotes_raw = _git(root, "remote", "-v")
    except WorkspaceError as exc:
        raise BootstrapError(f"{folder} is not a usable git checkout: {exc}") from exc
    if os.path.realpath(toplevel) != root:
        raise BootstrapError(f"{folder} is a nested checkout of {toplevel}")
    remotes = {}
    for line in remotes_raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "(fetch)" in line:
            remotes[parts[0]] = parts[1]
    origin = remotes.get("origin", "")
    if not origin:
        raise BootstrapError(f"{root} has no origin remote to prove identity from")

    ident = parse_remote_url(origin)
    return RepositoryWorkspace(root=root, repo=ident), remotes


def clone(source_url: str, dest_path: str) -> RepositoryWorkspace:
    """Clone into a NON-EXISTENT destination. Existing/symlinked targets refused."""
    dest = Path(os.path.realpath(dest_path))
    if dest.is_symlink():
        raise BootstrapError(f"{dest} is a symlink; refusing as clone target")
    if dest.exists():
        raise BootstrapError(f"{dest} already exists; use attach() instead")
    proc = subprocess.run(["git", "clone", "-q", source_url, str(dest)],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise BootstrapError(f"clone failed: {proc.stderr.strip()[:200]}")

    return RepositoryWorkspace(root=str(dest), repo=parse_remote_url(source_url))


# ---------------------------------------------------------------- remote create


@dataclass(frozen=True)
class CreateRepoProposal:
    """What Git Ninja may PROPOSE. Nothing here is permission."""

    provider: str                 # 'github'
    requested_owner: str
    requested_name: str
    visibility: str               # private | public
    description: str = ""
    default_branch: str = "main"


class RepoHostProvider:
    """Sandbox stand-in for a repo-hosting API (GitHub first). Mutations here
    are FAKE by construction; the interface is what matters for Toolbelt later."""

    def __init__(self) -> None:
        self.created: dict[str, dict] = {}
        self.heads: dict[tuple, str] = {}

    def supports_create(self) -> bool:
        return True  # IMPLEMENTATION AVAILABLE — separate from authorization

    def create_repository(self, proposal: CreateRepoProposal) -> dict:
        """Returns PROVIDER evidence: numeric id, ACTUAL owner/name (the host
        may normalize), canonical URL, actual default branch."""
        name = proposal.requested_name.lower().replace("_", "-")
        key = f"{proposal.provider}:{name}"
        repo_id = 100000 + len(self.created)
        evidence = {
            "repo_id": repo_id,
            "owner": proposal.requested_owner,
            "name": name,
            "html_url": f"https://{proposal.provider}.com/{proposal.requested_owner}/{name}.git",
            "default_branch": proposal.default_branch,
            "requested_name": proposal.requested_name,
        }
        self.created[key] = evidence
        return evidence

    def branch_head(self, identity: RepoIdentity, branch: str) -> str:
        return self.heads.get((identity.key(), branch), "")

    def set_branch_head(self, identity: RepoIdentity, branch: str, sha: str) -> None:
        self.heads[(identity.key(), branch)] = sha


@dataclass(frozen=True)
class CreatedRepository:
    """Canonical identity BOUND FROM PROVIDER EVIDENCE, not from the request."""

    identity: RepoIdentity
    repo_id: int
    actual_name: str
    actual_default_branch: str
    idempotency_key: str
    receipt_status: str


def create_remote_repo(
    fork,
    broker: ExecutionBroker,
    provider: RepoHostProvider,
    proposal: CreateRepoProposal,
) -> CreatedRepository:
    """Propose + admit + execute remote creation; bind post-create identity."""
    import hashlib
    import json

    key = hashlib.sha256(json.dumps({
        "provider": proposal.provider, "owner": proposal.requested_owner,
        "name": proposal.requested_name, "visibility": proposal.visibility,
    }, sort_keys=True).encode()).hexdigest()[:32]

    def dispatch() -> EffectOutcome:
        evidence = provider.create_repository(proposal)
        return EffectOutcome.from_domain("created", reason="repository created",
                                         evidence=evidence)

    request = EffectRequest(effect_id="forge.repository.create",
                            required_capability="forge.create_repository",
                            params={
                                "provider": proposal.provider,
                                "owner": proposal.requested_owner,
                                "name": proposal.requested_name,
                                "visibility": proposal.visibility,
                            },
                            idempotency_key=key)
    receipt = broker.execute(fork, request, dispatch)
    if receipt.status != "applied":
        return CreatedRepository(
            identity=None, repo_id=-1, actual_name="",          # type: ignore[arg-type]
            actual_default_branch="",
            idempotency_key=key, receipt_status=receipt.status)

    ev = dict(receipt.evidence)
    # Canonical identity comes FROM THE PROVIDER EVIDENCE — numeric id, actual
    # owner/name, canonical URL. The request only shaped the proposal.
    identity = parse_remote_url(str(ev["html_url"]))
    return CreatedRepository(identity=identity, repo_id=int(ev["repo_id"]),
                             actual_name=str(ev["name"]),
                             actual_default_branch=str(ev["default_branch"]),
                             idempotency_key=key, receipt_status="applied")


# ---------------------------------------------------------------- initial push


def initial_push(
    fork,
    broker: ExecutionBroker,
    ws: RepositoryWorkspace,
    provider: RepoHostProvider,
    *,
    branch: str | None = None,
) -> EffectOutcome:
    """Push the local HEAD as the FIRST ref. Expected remote state: EMPTY.
    A non-empty remote returns divergence/conflict for explicit resolution —
    force-push is never an implicit bootstrap behavior."""
    local = ws.read_local_state()
    if local.detached or local.dirty:
        return EffectOutcome(status="refused",
                             reason="bootstrap push needs a clean attached branch")
    target_branch = branch or local.branch
    existing = provider.branch_head(ws.repo, target_branch)
    if existing and existing != local.head_sha:
        return EffectOutcome(
            status="refused", reason="remote_not_empty_divergence",
            evidence={"remote_head": existing, "local_head": local.head_sha},
        )

    def dispatch() -> EffectOutcome:
        provider.set_branch_head(ws.repo, target_branch, local.head_sha)
        return EffectOutcome(status="applied", evidence={
            "branch": target_branch, "sha": local.head_sha,
            "verified": provider.branch_head(ws.repo, target_branch) == local.head_sha,
        })

    import hashlib

    key = hashlib.sha256(
        f"initial_push:{ws.repo.key()}:{target_branch}:{local.head_sha}".encode()
    ).hexdigest()[:32]
    request = EffectRequest(effect_id="forge.initial_push",
                            required_capability="forge.push_branch",
                            params={"branch": target_branch, "sha": local.head_sha,
                                    "expected_remote_state": "empty"},
                            idempotency_key=key)
    receipt = broker.execute(fork, request, dispatch)
    return EffectOutcome(status=receipt.status, reason=receipt.reason,
                         evidence=dict(receipt.evidence))
