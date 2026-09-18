"""Layered executable identity (Pass #3).

Retraction: realpath + reported --version does NOT prove the same executable is
still present — bytes can be replaced at the same path while printing the same
version. This module adds the smallest useful identity layers:

FAST FRESHNESS (default, no content reads):
    dev / inode / size / mtime_ns of the final target + symlink target path.
    Any same-path replacement, atomic rename-over, or symlink swap changes at
    least one of these. Cheap enough for every revalidation.

STRONG EXECUTABLE IDENTITY (opt-in, where justified):
    sha256 content digest. Only computed on request — never blindly hashed for
    every giant binary on every probe.

This is drift DETECTION, not attestation.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path  # noqa: F401  (kept for type references in docs)


@dataclass(frozen=True)
class ExecIdentity:
    """Typed identity of one executable file. Secretless by construction."""

    requested_path: str
    resolved_target: str                 # realpath (symlinks followed)
    symlink_target: str | None = None    # direct readlink when requested_path is a link
    device: int | None = None
    inode: int | None = None
    size: int | None = None
    mtime_ns: int | None = None
    sha256: str | None = None            # ONLY when strong identity was requested

    @classmethod
    def capture(cls, path: str | os.PathLike, strong: bool = False) -> "ExecIdentity | None":
        """Capture layered identity; None when the file does not exist."""
        p = str(path)
        try:
            st = os.stat(p)                      # follows symlinks -> final target
        except OSError:
            return None
        link_target = None
        try:
            if os.path.islink(p):
                link_target = os.readlink(p)
        except OSError:
            pass
        digest = None
        if strong:
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        return cls(requested_path=p,
                   resolved_target=os.path.realpath(p),
                   symlink_target=link_target,
                   device=st.st_dev, inode=st.st_ino,
                   size=st.st_size, mtime_ns=st.st_mtime_ns,
                   sha256=digest)

    def facts(self, prefix: str) -> tuple[tuple[str, str], ...]:
        """(property, value) pairs under ``prefix`` (e.g. ``execid:/usr/bin/git``).
        Only observed fields are returned — absent fields are simply not claimed.
        The caller converts these into typed EvidenceFact objects."""
        out = [("resolved_target", self.resolved_target)]
        pairs = (("device", self.device), ("inode", self.inode),
                 ("size", self.size), ("mtime_ns", self.mtime_ns))
        out += [(name, str(val)) for name, val in pairs if val is not None]
        if self.symlink_target is not None:
            out.append(("symlink_target", self.symlink_target))
        if self.sha256 is not None:
            out.append(("sha256", self.sha256))
        return tuple(out)


def fast_identity(path: str | os.PathLike) -> ExecIdentity | None:
    return ExecIdentity.capture(path, strong=False)


def strong_identity(path: str | os.PathLike) -> ExecIdentity | None:
    return ExecIdentity.capture(path, strong=True)


__all__ = ["ExecIdentity", "fast_identity", "strong_identity"]
