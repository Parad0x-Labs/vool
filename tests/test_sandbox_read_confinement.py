"""The macOS sandbox profile must deny reads of secret dirs, closing the runtime-open exfil that
the argv path-guard cannot see (audit finding #3)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from sandbox.job_runner import (
    _macos_confined_profile,
    _private_read_roots,
    _seatbelt_subpath_literal,
    _sensitive_read_deny_roots,
)


def test_profile_denies_reads_of_secret_dirs():
    prof = _macos_confined_profile((Path("/tmp/ws"),))
    assert "(deny file-read*" in prof
    home = Path.home()
    for secret in (home / ".ssh", home / ".aws", home / ".gnupg", home / ".config" / "solana"):
        # Compare against the profile's own encoding: it renders each root through
        # _seatbelt_subpath_literal, which escapes separators, so a raw str() of a Windows path
        # never matches even when the root is present and denied.
        assert _seatbelt_subpath_literal(secret) in prof, secret


def test_sensitive_roots_include_the_named_exfil_targets():
    # Compare resolved Paths rather than interpolated strings: f"{home}/.ssh" builds a
    # forward-slash path that never equals the backslash form on Windows.
    roots = set(_sensitive_read_deny_roots())
    home = Path.home()
    for target in (home / ".ssh", home / ".aws", home / ".vool_runtime"):
        assert target in roots, target


def test_private_read_roots_include_mounted_volumes():
    """/Volumes is every mounted external volume on macOS: it belongs to the private trees a
    confined job cannot read unless the policy names a root there."""
    if not Path("/Volumes").is_dir():
        pytest.skip("no /Volumes mount point on this host")
    assert Path("/Volumes") in _private_read_roots()


def test_profile_denies_external_volume_reads_and_restores_named_workspaces():
    """The layer-1 deny names /Volumes, and a workspace the policy NAMES on an external volume
    is re-allowed AFTER the deny — Seatbelt is last-match-wins, so supported external
    workspaces keep working while every sibling volume stays unreadable."""
    prof = _macos_confined_profile((Path("/tmp/ws"),), read_roots=(Path("/Volumes/MyExternalWS"),))
    volumes_deny = _seatbelt_subpath_literal(Path("/Volumes"))
    external_ws_allow = _seatbelt_subpath_literal(Path("/Volumes/MyExternalWS"))
    assert volumes_deny in prof, "the profile must deny reads of mounted volumes"
    assert external_ws_allow in prof, "a named external workspace must be re-allowed"
    assert prof.find(volumes_deny) < prof.find(external_ws_allow), \
        "the allow must follow the deny (Seatbelt is last-match-wins)"


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS-only")
def test_live_seatbelt_blocks_reading_a_secret_file(tmp_path):
    # Real kernel enforcement: an allowed interpreter's runtime open() of a denied path fails.
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        pytest.skip("no ~/.ssh on this host")
    canary = ssh_dir / "vool_pytest_canary"
    try:
        canary.write_text("CANARY")
    except OSError:
        pytest.skip("cannot write a canary into ~/.ssh")
    try:
        prof = _macos_confined_profile((tmp_path,))
        with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
            f.write(prof)
            sbf = f.name
        r = subprocess.run(
            ["sandbox-exec", "-f", sbf, "python3", "-c", f"open({str(canary)!r}).read()"],
            capture_output=True, text=True, timeout=20,
        )
        assert r.returncode != 0
        assert "not permitted" in (r.stderr or "").lower()
    finally:
        canary.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS-only")
def test_live_seatbelt_blocks_listing_external_volumes(tmp_path):
    """Real kernel enforcement of the volume deny: a confined child cannot list /Volumes'
    contents (reading nothing of the operator's disks), while listing its own workspace still
    works under the same profile."""
    if not Path("/Volumes").is_dir():
        pytest.skip("no /Volumes mount point on this host")
    prof = _macos_confined_profile((tmp_path.resolve(),))
    with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
        f.write(prof)
        sbf = f.name
    denied = subprocess.run(
        ["sandbox-exec", "-f", sbf, "python3", "-c", "import os; os.listdir('/Volumes')"],
        capture_output=True, text=True, timeout=20,
    )
    assert denied.returncode != 0
    assert "not permitted" in (denied.stderr or "").lower()
    allowed = subprocess.run(
        ["sandbox-exec", "-f", sbf, "python3", "-c", f"import os; os.listdir({str(tmp_path.resolve())!r})"],
        capture_output=True, text=True, timeout=20,
    )
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS-only")
def test_live_named_root_is_restored_inside_a_denied_tree_and_siblings_stay_denied():
    """Executable deny-then-allow proof with CONTROLLED INTERNAL fixtures — the same
    layer-1-deny plus named-root-allow mechanism an external-volume workspace goes through,
    without touching any owner's data: a read root named inside a denied tree is readable
    again (the supported external-workspace positive), while a SIBLING directory under the
    same deny stays unreadable (the sibling-volume refusal)."""
    import shutil

    denied_root = Path(tempfile.gettempdir()).resolve()  # a private read root in the profile
    control = Path(tempfile.mkdtemp(prefix="vool-deny-allow-", dir=str(denied_root)))
    try:
        named = control / "named-workspace"
        sibling = control / "sibling-secret"
        named.mkdir()
        sibling.mkdir()
        (named / "readme.txt").write_text("ALLOWED")
        (sibling / "secret.txt").write_text("DENIED")
        prof = _macos_confined_profile((control / "job-ws",), read_roots=(named,))
        with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
            f.write(prof)
            sbf = f.name
        script = (
            "import sys\n"
            f"assert open({str(named / 'readme.txt')!r}).read() == 'ALLOWED'\n"
            "try:\n"
            f"    open({str(sibling / 'secret.txt')!r}).read()\n"
            "except PermissionError:\n"
            "    print('SIBLING_DENIED')\n"
            "    sys.exit(0)\n"
            "print('SIBLING_READ')\n"
            "sys.exit(3)\n"
        )
        r = subprocess.run(
            ["sandbox-exec", "-f", sbf, "python3", "-c", script],
            capture_output=True, text=True, timeout=20,
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "SIBLING_DENIED" in r.stdout, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(control, ignore_errors=True)
