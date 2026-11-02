"""The downloads carry the x402 Foundation SDK, so a Crypto Pilot wallet in the shipped app pays x402 offers in USDC on
Base (core/wallet/x402_sdk.py) instead of that payment channel answering "not usable on this install".

Both bundles install from the lock's export of the project's extras: both must carry the `x402` extra at the version
pyproject pins, and the lock must carry web3 with it (the SDK's EVM package imports it at import time).
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


def test_the_macos_bundle_installs_the_x402_extra_from_the_lock() -> None:
    from ops.macos_locked_requirements import EXTRAS

    assert "x402" in EXTRAS, "the Mac bundle dropped the x402 SDK: the Base x402 channel would be unusable in the app"
    assert _x402_pin() == "x402[evm]==2.25.0" and _locked("x402") == "2.25.0"
    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    x402 = next(package for package in lock["package"] if package["name"] == "x402")
    assert any(dep["name"] == "web3" for dep in x402["optional-dependencies"]["evm"]), "the lock lost x402's web3"
    assert _locked("web3") == "8.0.0"
    # The build installs exactly that export (tests/test_macos_locked_requirements.py pins the wiring).
    assert "ops/macos_locked_requirements.py\" export" in MACOS_BUILD.read_text(encoding="utf-8")


def test_the_linux_bundle_exports_the_x402_extra_from_the_lock() -> None:
    script = LINUX_BUILD.read_text(encoding="utf-8")
    export = script.split("uv export", 1)[1].split("--output-file", 1)[0]
    assert re.search(r"--extra x402\b", export), "the Linux bundle dropped the x402 extra: the Base x402 channel would be unusable in the app"
