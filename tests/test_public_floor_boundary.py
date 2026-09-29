"""Keep private Floor content out of public commits."""

import importlib.util
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "tools/check_public_floor_boundary.py"
SPEC = importlib.util.spec_from_file_location("public_floor_boundary", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_private_floor_paths_are_rejected():
    for path in (
        "premium/wallet-lab/src/wallet_lab/autopilot.py",
        "fixture-plugins/plugins/wallet-lab/src/wallet_lab/social.py",
        "core/wallet_lab/fragment.py",
        "core/wallet_lab.py",
        "core/wallet_lab_setup.py",
        "docs/guides/wallet-lab-showcase.md",
        "tests/test_wallet_lab_browser.py",
        "installer/bundle/lab_drag.py",
        "installer/bundle/desktop_blocks.py",
        "artifacts/tapes/rh-migrated-targets/chunk.jsonl.gz",
        "profiles/floor-tape-bank/manifest.json",
    ):
        assert MODULE.is_private_floor_path(path), path


def test_public_work_remains_allowed():
    for path in (
        "core/x_tools.py",
        "core/eyebrow_client.py",
        "core/i18n/catalogs/lt.json",
        "installer/bundle/pet_native.py",
        "tests/test_obligation_floor_families.py",
    ):
        assert not MODULE.is_private_floor_path(path), path
