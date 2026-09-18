"""Multiple identities and resource-scoped permission (Pass #2).

Laws:
* Toolbelt NEVER silently picks "the first credential I found" — every candidate
  identity for a resource is returned explicitly; ambiguity is AMBIGUOUS_IDENTITY.
* CREDENTIAL SCOPE ≠ RESOURCE PERMISSION. Layers:
    AUTHENTICATED IDENTITY  -> TOKEN/OAUTH SCOPE -> provider-wide capability HINT
    -> RESOURCE-SPECIFIC PERMISSION (PROVEN / REFUSED / UNKNOWN, read-only APIs only)
    -> PLATFORM AUTHORIZATION (never here).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.toolbelt.credentials import gh_credential_handle
from core.toolbelt.models import AuthState, CredentialHandle
from core.toolbelt.probes import ProbeRunner

_ALL_ACCOUNTS = re.compile(r"Logged in to (\S+) account (\S+)")
_VIEWER_PERM = re.compile(r'"viewerPermission"\s*:\s*"([A-Z_]+)"')


class ResourcePermission(str, Enum):
    PROVEN = "PROVEN"      # established from a read-only provider metadata answer
    REFUSED = "REFUSED"    # provider explicitly said no / not found for this identity
    UNKNOWN = "UNKNOWN"    # no safe evidence — never guessed


def github_identities(runner: ProbeRunner) -> tuple[CredentialHandle, ...]:
    """ALL authenticated GitHub accounts visible to `gh`, in provider order."""
    result = runner.run(["gh", "auth", "status"])
    text = result.stdout + "\n" + result.stderr
    out = []
    for m in _ALL_ACCOUNTS.finditer(text):
        host, account = m.group(1), m.group(2)
        out.append(CredentialHandle(
            credential_id=f"github:{account}", provider="github", source="gh-store",
            status=AuthState.AUTHENTICATED, identity=f"{account}@{host}",
        ))
    return tuple(out)


@dataclass(frozen=True)
class ResourceResolution:
    capability: str
    resource: str                                  # e.g. "github.com/org/repo"
    candidates: tuple[CredentialHandle, ...]
    selected: CredentialHandle | None              # set ONLY when unambiguous
    ambiguous: bool                                # True -> AMBIGUOUS_IDENTITY, do NOT guess
    resource_permission: ResourcePermission = ResourcePermission.UNKNOWN
    scope_hint: tuple[str, ...] = ()               # token scopes = capability HINT only
    note: str = ""


def resolve_for_resource(capability: str, resource: str,
                         runner: ProbeRunner) -> ResourceResolution:
    """Bind candidate identities to an exact resource; prove resource permission
    ONLY via read-only provider metadata (`gh repo view OWNER/NAME --json viewerPermission`).

    A write capability is never probed by performing it.
    """
    candidates = github_identities(runner)
    if len(candidates) > 1:
        return ResourceResolution(capability=capability, resource=resource,
                                  candidates=candidates, selected=None, ambiguous=True,
                                  note="AMBIGUOUS_IDENTITY: multiple authenticated accounts "
                                       "— caller must bind one before any effect")
    handle = gh_credential_handle(runner)
    if handle is None or handle.status is not AuthState.AUTHENTICATED:
        return ResourceResolution(capability=capability, resource=resource,
                                  candidates=candidates, selected=None, ambiguous=False,
                                  note="no authenticated identity")
    perm, scope_hint, note = ResourcePermission.UNKNOWN, (), ""

    status_text = ""
    if resource.startswith("github.com/"):
        slug = resource[len("github.com/"):].strip("/")
        view = runner.run(["gh", "repo", "view", slug, "--json",
                           "nameWithOwner,viewerPermission"])
        m = _VIEWER_PERM.search(view.stdout)
        if view.ok and m:
            # viewerPermission is provider-asserted metadata about THIS repo.
            level = m.group(1)
            perm = {"ADMIN": ResourcePermission.PROVEN,
                    "MAINTAIN": ResourcePermission.PROVEN,
                    "WRITE": ResourcePermission.PROVEN,
                    "READ": ResourcePermission.PROVEN,
                    }.get(level, ResourcePermission.UNKNOWN)
            if capability in {"github.push", "github.repo.create"} and \
                    level in {"READ", "NONE", "TRIAGE"}:
                perm = ResourcePermission.REFUSED
            note = f"provider viewerPermission={level} (read-only metadata)"
        elif not view.ok:
            err = (view.stderr + view.stdout).lower()
            if "denied" in err or "http 403" in err or "forbidden" in err:
                perm = ResourcePermission.REFUSED
                note = "provider explicitly denied this identity"
            else:
                # "not found" is ambiguous (nonexistent OR private) — never guess.
                note = f"metadata inconclusive ({view.stderr.strip()[:60]})"

    status = runner.run(["gh", "auth", "status"])
    status_text = status.stdout + "\n" + status.stderr
    scopes_m = re.search(r"Token scopes?: (.+)", status_text)
    if scopes_m:
        scope_hint = tuple(s.strip().strip("'\"") for s in scopes_m.group(1).split(","))

    return ResourceResolution(
        capability=capability, resource=resource, candidates=(handle,),
        selected=handle, ambiguous=False, resource_permission=perm,
        scope_hint=scope_hint,
        note=(note + "; " if note else "") +
             "scope is a capability HINT — platform authorization still undecided")


__all__ = ["ResourcePermission", "ResourceResolution", "github_identities",
           "resolve_for_resource"]
