"""Keep private Floor content out of public commits."""

import importlib.util
import subprocess
import sys
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
        "core/web_assets/floor-mark.png",
        "tests/test_floor_chat_bridge.py",
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
        "core/blackbox/recorder.py",
    ):
        assert not MODULE.is_private_floor_path(path), path


def test_command_refuses_private_paths_in_the_index(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    public = tmp_path / "core/x_tools.py"
    public.parent.mkdir()
    public.write_text("# synthetic public marker\n")
    subprocess.run(["git", "add", "core/x_tools.py"], cwd=tmp_path, check=True)
    allowed = subprocess.run([sys.executable, str(SOURCE)], cwd=tmp_path, capture_output=True, text=True)
    assert allowed.returncode == 0
    assert "No local-only Floor paths" in allowed.stdout
    for name in ("premium/wallet-lab/private.py", "core/web_assets/floor-mark.png"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic marker only\n")
        subprocess.run(["git", "add", name], cwd=tmp_path, check=True)
    denied = subprocess.run([sys.executable, str(SOURCE)], cwd=tmp_path, capture_output=True, text=True)
    assert denied.returncode == 1
    assert "premium/wallet-lab/private.py" in denied.stderr
    assert "core/web_assets/floor-mark.png" in denied.stderr
