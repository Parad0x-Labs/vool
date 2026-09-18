"""High-risk path detection for local mutations: credentials, Git metadata, startup files,
configuration and release keys.

Classification reads PATHS ONLY -- names and directory components -- never file content, so the
detector itself can never leak secret bytes. A mutation whose declared or observed target is
high-risk is refused at the coverage gate unless the operator explicitly allowed it for that
turn; the refusal text names the KIND and the PATH, nothing else.

The tables are deliberately name-shaped, not heuristic: a file is high-risk because of what it
IS (an SSH key, a launch agent, a release signing key), not because it looks exciting.
"""
from __future__ import annotations

from pathlib import Path

KIND_CREDENTIALS = "credentials"
KIND_GIT_METADATA = "git_metadata"
KIND_STARTUP = "startup"
KIND_CONFIGURATION = "configuration"
KIND_RELEASE_KEYS = "release_keys"

HIGH_RISK_KINDS = (
    KIND_CREDENTIALS,
    KIND_GIT_METADATA,
    KIND_STARTUP,
    KIND_CONFIGURATION,
    KIND_RELEASE_KEYS,
)

#: Exact file names that are credentials by existing (SSH keys, env-carried secrets).
_CREDENTIAL_NAMES = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "authorized_keys",
        "credentials.json",
        "credentials",
        ".netrc",
        ".env",
        ".htpasswd",
        "config.json",  # docker credentials live here
        "auth.json",
        "keychain",
        "legacy_credentials",
    }
)
#: Substrings that make a NAME a credential (kept tight: "key"/"token" alone are not credentials).
_CREDENTIAL_SUBSTRINGS = ("secret", "credential", "password", "apikey", "api_key")
_CREDENTIAL_SUFFIXES = (".pem", ".p12", ".pfx", ".key", ".kdbx", ".keystore", ".jks")
_CREDENTIAL_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".docker", ".kube", ".vault", ".secrets"})

_GIT_METADATA_DIRS = frozenset({".git"})

_STARTUP_NAMES = frozenset(
    {
        ".zshrc",
        ".zprofile",
        ".zshenv",
        ".zlogin",
        ".zlogout",
        ".bashrc",
        ".bash_profile",
        ".bash_login",
        ".bash_logout",
        ".profile",
        ".kshrc",
        ".login",
        ".logout",
        "rc.local",
        "com.apple.loginitems",
    }
)
_STARTUP_DIRS = frozenset({"LaunchAgents", "LaunchDaemons", "StartupItems"})

#: Exact names that are configuration an attacker would love to rewrite.
_CONFIGURATION_NAMES = frozenset(
    {
        ".gitconfig",
        ".hg/hgrc",
        "config",
        "ssh_config",
        "sshd_config",
        "hosts",
        "hosts.equiv",
        "npmrc",
        "yarnrc",
        "pip.conf",
        "pip.ini",
        "tmux.conf",
        "curlrc",
        "wgetrc",
        "gitconfig",
        "sudoers",
    }
)
_CONFIGURATION_DIRS = frozenset({".config", ".ssh", "Preferences"})

#: Release / notarization / code-signing key material.
_RELEASE_KEY_NAMES = frozenset(
    {
        "minisign.key",
        "signing.key",
        "release.key",
        "release_key",
        "trusted-release.pub",
        "app-store-key",
    }
)
_RELEASE_KEY_SUFFIXES = (".p8", ".mobileprovision", ".provisionprofile")
_RELEASE_KEY_PREFIXES = ("AuthKey_", "app-store-key", "signing-key", "release-key")


def _name_matches(name: str, *, lower: str) -> str | None:
    if name in _CREDENTIAL_NAMES:
        return KIND_CREDENTIALS
    if name in _STARTUP_NAMES:
        return KIND_STARTUP
    if name in _CONFIGURATION_NAMES:
        return KIND_CONFIGURATION
    if name in _RELEASE_KEY_NAMES:
        return KIND_RELEASE_KEYS
    for piece in _CREDENTIAL_SUBSTRINGS:
        if piece in lower:
            return KIND_CREDENTIALS
    for suffix in _CREDENTIAL_SUFFIXES:
        if lower.endswith(suffix):
            return KIND_CREDENTIALS
    for suffix in _RELEASE_KEY_SUFFIXES:
        if lower.endswith(suffix):
            return KIND_RELEASE_KEYS
    for prefix in _RELEASE_KEY_PREFIXES:
        if lower.startswith(prefix.lower()):
            return KIND_RELEASE_KEYS
    return None


def classify_path(path: Path | str) -> str | None:
    """The high-risk KIND for a path, or None. Path names and components only -- never content."""
    text = str(path or "")
    if not text:
        return None
    parts = [piece for piece in text.split("/") if piece]
    if not parts:
        return None
    name = parts[-1]
    # Directory components outrank names: ".git/config" is Git metadata, not configuration, and
    # a launch-agent dir makes anything under it startup regardless of its file name.
    for component in parts[:-1]:
        if component in _GIT_METADATA_DIRS:
            return KIND_GIT_METADATA
        if component in _STARTUP_DIRS:
            return KIND_STARTUP
    for component in parts[:-1]:
        if component in _CREDENTIAL_DIRS:
            return KIND_CREDENTIALS
    matched = _name_matches(name, lower=name.lower())
    if matched:
        return matched
    for component in parts[:-1]:
        if component in _CONFIGURATION_DIRS and name.endswith(".plist"):
            return KIND_CONFIGURATION
    if name.endswith(".plist") and any(piece in parts for piece in ("Library",)):
        return KIND_STARTUP
    if name.endswith(".plist"):
        return KIND_CONFIGURATION
    return None


def high_risk_for_paths(paths: list[str]) -> dict[str, str]:
    """{path: kind} for every path that classifies high-risk."""
    out: dict[str, str] = {}
    for raw in paths:
        kind = classify_path(raw)
        if kind:
            out[str(raw)] = kind
    return out


__all__ = ["HIGH_RISK_KINDS", "classify_path", "high_risk_for_paths"]
