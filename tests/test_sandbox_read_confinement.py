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
