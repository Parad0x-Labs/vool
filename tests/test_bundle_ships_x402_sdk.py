"""The downloads carry the x402 Foundation SDK, so a Crypto Pilot wallet in the shipped app pays x402 offers in USDC on
Base (core/wallet/x402_sdk.py) instead of that payment channel answering "not usable on this install".

The macOS bundle installs a hand-listed lean set and the Linux bundle exports extras from the lock: both must carry the
`x402` extra at the version pyproject pins, the Mac list with web3 at the lock's version (the SDK's EVM package imports
it at import time).
"""
from __future__ import annotations

import re
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[1]
MACOS_BUILD = REPO / "installer" / "bundle" / "build_macos_app.sh"
LINUX_BUILD = REPO / "installer" / "bundle" / "build_linux_bundle.sh"


def _x402_pin() -> str:
    extra = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["x402"]
    assert len(extra) == 1, extra
    return extra[0]


def _locked(name: str) -> str:
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    return next(package["version"] for package in lock["package"] if package["name"] == name)


def test_the_macos_lean_install_ships_the_x402_sdk_at_the_pinned_and_locked_versions() -> None:
    script = MACOS_BUILD.read_text(encoding="utf-8")
    lean = script.split("lean dependency install failed", 1)[0].rsplit("uv pip install", 1)[-1]
    assert f'"{_x402_pin()}"' in lean, "the Mac bundle dropped the x402 SDK: the Base x402 channel would be unusable in the app"
    assert f'"web3=={_locked("web3")}"' in lean, "the Mac bundle's web3 left the lock's version"


def test_the_linux_bundle_exports_the_x402_extra_from_the_lock() -> None:
    script = LINUX_BUILD.read_text(encoding="utf-8")
    export = script.split("uv export", 1)[1].split("--output-file", 1)[0]
    assert re.search(r"--extra x402\b", export), "the Linux bundle dropped the x402 extra: the Base x402 channel would be unusable in the app"
