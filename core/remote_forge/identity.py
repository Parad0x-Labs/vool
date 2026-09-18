"""Repository identity: WHICH repo, exactly — never a name that looks close.

The failure this closes: an agent asked to act on "acme/api" resolving to
"acme2/api" or "acme/api-internal" because a search returned a near name.
Resolution here is constructive, not fuzzy: identity comes from parsing the
remote URL (or an explicit owner/repo pair), and every downstream action
carries the full identity so a similar-named repo can never be substituted.
Fork vs upstream is represented too — they are different identities even when
one is a fork of the other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# git@host:owner/repo.git  |  https://host/owner/repo.git | .../owner/repo
_SSH_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?/?$")
_HTTP_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>.+?)(?:\.git)?/?$"
)

_KNOWN_PROVIDERS = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "gitea": "gitea",
}


class IdentityError(ValueError):
    """The remote URL or coordinates do not resolve to one exact repository."""


@dataclass(frozen=True)
class RepoIdentity:
    """provider + owner + repo + remote URL. Frozen and hashable so it can be
    carried inside every action and receipt without drifting."""

    provider: str
    host: str
    owner: str
    repo: str
    remote_url: str

    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.owner.lower(), self.repo.lower())


def parse_remote_url(url: str) -> RepoIdentity:
    """Resolve a git remote URL to an exact identity, or raise IdentityError."""
    text = str(url or "").strip()
    if not text:
        raise IdentityError("empty remote URL")
    match = _SSH_RE.match(text) or _HTTP_RE.match(text)
    if match is None:
        raise IdentityError(f"unparseable remote URL: {url!r}")
    host = match.group("host").lower()
    provider = _KNOWN_PROVIDERS.get(host, host)
    if provider == "gitea":
        provider = host  # self-hosted gitea has no canonical host name
    return RepoIdentity(
        provider=provider,
        host=host,
        owner=match.group("owner"),
        repo=match.group("repo"),
        remote_url=text,
    )


def explicit_identity(provider: str, owner: str, repo: str, *, host: str = "") -> RepoIdentity:
    """Identity from explicit coordinates. The caller asserts these exactly;
    nothing here guesses between candidates."""
    clean_owner, clean_repo = str(owner).strip(), str(repo).strip()
    if not clean_owner or not clean_repo:
        raise IdentityError(f"incomplete identity: owner={owner!r} repo={repo!r}")
    resolved_host = host.strip() or {"github": "github.com", "gitlab": "gitlab.com"}.get(
        provider.strip().lower(), ""
    )
    if not resolved_host:
        raise IdentityError(f"no host for provider {provider!r}")
    url = f"https://{resolved_host}/{clean_owner}/{clean_repo}"
    return RepoIdentity(
        provider=provider.strip().lower(),
        host=resolved_host,
        owner=clean_owner,
        repo=clean_repo,
        remote_url=url,
    )
