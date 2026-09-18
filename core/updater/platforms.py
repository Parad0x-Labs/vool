"""Platform installer registry — the honest Windows/Linux contract boundary.

The manifest, decision, download, migration, journal, health and rollback machinery is
platform-neutral and shared by every OS. What is NOT shared is the actual installer:
verifying and swapping an app on disk is platform work. Only `macos-*` is registered
today. `installer_for("windows-x64")` returns None and the decision layer reports
`unsupported_installer_platform` in plain words — this build never pretends a Windows
or Linux installer exists.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


def _bundle_manifest_arch() -> str | None:
    """The architecture the running bundle was BUILT for, from its own BUILD_MANIFEST.json.

    Located from the interpreter, the same way the bundle's other self-identification works:
    Contents/Resources/python/bin/python3 -> Contents/Resources/BUILD_MANIFEST.json. Returns None
    outside a bundle (a source checkout), where the running machine is the right answer.
    """
    try:
        manifest = Path(sys.executable).resolve().parent.parent.parent / "BUILD_MANIFEST.json"
        if not manifest.is_file():
            return None
        with manifest.open("rb") as fh:
            arch = json.load(fh).get("arch")
        return arch if isinstance(arch, str) and arch else None
    except Exception:
        return None


def platform_key(platform: str | None = None, machine: str | None = None) -> str:
    """Normalize to `<os>-<arch>`: macos-arm64, macos-x64, windows-x64, linux-x64…"""
    raw_os = (platform or sys.platform).lower()
    if raw_os.startswith("darwin"):
        os_name = "macos"
    elif raw_os.startswith("win"):
        os_name = "windows"
    elif raw_os.startswith("linux"):
        os_name = "linux"
    else:
        os_name = raw_os or "unknown"
    if machine:
        raw_machine = machine.lower()
    else:
        # WHAT THIS BUNDLE IS, NOT WHAT THIS PROCESS HAPPENS TO BE RUNNING AS. os.uname().machine
        # reports the TRANSLATED architecture under Rosetta: an x86_64-translated process reads
        # "x86_64" and would fetch the Intel update feed for an arm64 install, then swap an Intel
        # bundle over it. The shipped BUILD_MANIFEST records the architecture the artifact was
        # actually built for, so it is the authority whenever it is present.
        raw_machine = (_bundle_manifest_arch() or "").lower()
        if not raw_machine:
            raw_machine = os.uname().machine.lower() if hasattr(os, "uname") else ""
    if raw_machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif raw_machine in ("x86_64", "amd64"):
        arch = "x64"
    else:
        arch = raw_machine or "unknown"
    return f"{os_name}-{arch}"


@dataclass(frozen=True)
class BundleVerification:
    ok: bool
    codesigned: bool = False
    notarized: bool = False
    detail: str = ""


@dataclass(frozen=True)
class SwapOutcome:
    ok: bool
    prior_path: Path | None = None
    detail: str = ""


@runtime_checkable
class PlatformInstaller(Protocol):
    name: str

    def verify_bundle(self, staged_bundle: Path, *, require_notarization: bool = True) -> BundleVerification: ...

    def atomic_swap(self, staged_bundle: Path, target_app: Path, *, txid: str) -> SwapOutcome: ...

    def restore_prior(self, target_app: Path, prior_path: Path, *, txid: str) -> SwapOutcome: ...

    def prune_priors(self, target_app: Path, *, keep: int = 2) -> list[Path]: ...


def _lazy_macos():
    from core.updater.macos import MacOSBundleInstaller

    return MacOSBundleInstaller


#: The registry itself. Only macOS installers are real today; adding windows-x64 here
#: requires an actual PlatformInstaller implementation, not a placeholder.
_BUILDERS: dict[str, type] = {}


def register_installer(key: str, installer_cls: type) -> None:
    _BUILDERS[str(key)] = installer_cls


def _default_registry() -> dict[str, type]:
    if "macos-arm64" not in _BUILDERS:
        register_installer("macos-arm64", _lazy_macos())
        register_installer("macos-x64", _lazy_macos())
    return _BUILDERS


def installer_for(key: str) -> PlatformInstaller | None:
    """None means exactly what it says: this build has no installer for that platform."""
    builder = _default_registry().get(str(key))
    return builder() if builder is not None else None


def supported_platforms() -> tuple[str, ...]:
    return tuple(sorted(_default_registry()))


__all__ = [
    "BundleVerification",
    "PlatformInstaller",
    "SwapOutcome",
    "installer_for",
    "platform_key",
    "register_installer",
    "supported_platforms",
]
