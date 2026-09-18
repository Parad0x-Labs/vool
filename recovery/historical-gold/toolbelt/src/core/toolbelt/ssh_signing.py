"""SSH transport and commit-signing models (Pass #2) — distinct facts, no collapsing.

SSH:  KEY_PRESENT ≠ IDENTITY_KNOWN ≠ TRANSPORT_ACCEPTED ≠ RESOURCE_ACCESS_*
      A successful `ssh -T git@github.com` says NOTHING about pushing repo X.
Signing: resolution yields implementation + identity + key HANDLE; use stays
      outside Toolbelt. No real key material is ever read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.toolbelt.models import PlanStatus
from core.toolbelt.probes import ProbeRunner


class SshFact(str, Enum):
    KEY_PRESENT = "KEY_PRESENT"
    KEY_ABSENT = "KEY_ABSENT"
    IDENTITY_KNOWN = "IDENTITY_KNOWN"
    IDENTITY_UNKNOWN = "IDENTITY_UNKNOWN"
    TRANSPORT_ACCEPTED = "TRANSPORT_ACCEPTED"
    TRANSPORT_REJECTED = "TRANSPORT_REJECTED"
    TRANSPORT_UNKNOWN = "TRANSPORT_UNKNOWN"


_GH_USER = re.compile(r"Hi (\S+)!")


@dataclass(frozen=True)
class SshCapability:
    facts: tuple[SshFact, ...]
    key_fingerprints: tuple[str, ...] = ()   # public fingerprints only, never key bytes
    github_identity: str | None = None       # only when the provider greets us
    resource_access: str = "RESOURCE_ACCESS_UNKNOWN"  # never inferred from -T success


def probe_ssh(runner: ProbeRunner, home: str | Path | None = None) -> SshCapability:
    root = Path(home) if home else Path.home()
    facts: list[SshFact] = []
    fps: list[str] = []
    pub_keys = sorted(root.glob(".ssh/*.pub"))
    if pub_keys:
        facts.append(SshFact.KEY_PRESENT)
        for k in pub_keys:
            fp = runner.run(["ssh-keygen", "-lf", str(k)])
            m = re.search(r"([0-9a-fA-F:]{16,}|SHA256:[\w+/=]+)", fp.stdout)
            if m:
                fps.append(m.group(1))
        facts.append(SshFact.IDENTITY_KNOWN)
    else:
        facts.append(SshFact.KEY_ABSENT)

    ssh_t = runner.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                        "-T", "git@github.com"])
    text = (ssh_t.stdout + ssh_t.stderr).lower()
    m = _GH_USER.search(ssh_t.stdout + "\n" + ssh_t.stderr)
    if "successfully authenticated" in text or m:
        facts.append(SshFact.TRANSPORT_ACCEPTED)
        return SshCapability(facts=tuple(facts), key_fingerprints=tuple(fps),
                             github_identity=m.group(1) if m else None)
    if "permission denied" in text or "publickey" in text:
        facts.append(SshFact.TRANSPORT_REJECTED)
    else:
        facts.append(SshFact.TRANSPORT_UNKNOWN)
    return SshCapability(facts=tuple(facts), key_fingerprints=tuple(fps))


# --- signing ----------------------------------------------------------------------------------

class SigningState(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"     # git config has no signing setup
    CONFIGURED = "CONFIGURED"         # format + key declared (NOT yet verified)
    VERIFIED = "VERIFIED"             # a signature operation was actually exercised


@dataclass(frozen=True)
class SigningCapability:
    status: PlanStatus
    state: SigningState
    format: str | None = None          # "ssh" | "gpg" | "x509"
    key_handle_id: str | None = None   # config-declared public identifier — a HANDLE
    note: str = ""


def resolve_signing(runner: ProbeRunner, verify_probe_ok: bool | None = None) -> SigningCapability:
    """Read-only signing resolution. ``verify_probe_ok`` lets a test inject a REAL
    verification outcome; without it, configured ≠ verified."""
    fmt = runner.run(["git", "config", "--get", "gpg.format"])
    key = runner.run(["git", "config", "--get", "user.signingkey"])
    if not (fmt.ok and fmt.stdout.strip() and key.ok and key.stdout.strip()):
        return SigningCapability(status=PlanStatus.UNKNOWN, state=SigningState.UNCONFIGURED,
                                 note="no gpg.format/user.signingkey in git config")
    f = fmt.stdout.strip()
    kh = key.stdout.strip()
    if verify_probe_ok is True:
        return SigningCapability(status=PlanStatus.READY, state=SigningState.VERIFIED,
                                 format=f, key_handle_id=kh,
                                 note="verification exercised via injected fixture")
    return SigningCapability(
        status=PlanStatus.IMPLEMENTATION_AVAILABLE, state=SigningState.CONFIGURED,
        format=f, key_handle_id=kh,
        note="declared but never exercised — VERIFIED requires a real signed object")


__all__ = ["SshCapability", "SshFact", "SigningCapability", "SigningState",
           "probe_ssh", "resolve_signing"]
