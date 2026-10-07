"""`--ref v0.7.0` fetches the v0.7.0 tag; a branch name still fetches the branch.

Both bootstraps built `.../archive/refs/heads/<ref>` for every ref. GitHub answers that URL with 404 for a
tag (refs/heads/<tag> does not exist; refs/tags/<tag> does), so installing a release by its tag failed
at the download. The shell bootstrap is run for real with `curl` replaced by a recorder on PATH; the
PowerShell bootstrap is read, since this host has no PowerShell.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.platform_helpers import bash_path, bash_script_args

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = "https://github.com/Parad0x-Labs/vool/archive"


def _run_shell_bootstrap(tmp_path: Path, *args: str, env_ref: str | None = None) -> tuple[list[str], dict]:
    """Run the real shell bootstrap; a recording `curl` serves a minimal archive for any URL."""
    source = tmp_path / "source"
    (source / "installer").mkdir(parents=True)
    launcher = source / "Install_And_Run_VOOL.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    archive = tmp_path / "served.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(launcher, arcname="vool-x/Install_And_Run_VOOL.sh")
        tar.add(source / "installer", arcname="vool-x/installer")
    calls = tmp_path / "curl-calls.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_curl = bindir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{bash_path(calls)}"\n'
        'out=""\n'
        'while [[ $# -gt 0 ]]; do if [[ "$1" == "-o" ]]; then out="$2"; shift; fi; shift; done\n'
        f'if [[ -n "$out" ]]; then cp "{bash_path(archive)}" "$out"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    install_dir = tmp_path / "install"
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    env.pop("VOOL_ARCHIVE_URL", None)
    env.pop("VOOL_GITHUB_REF", None)
    if env_ref is not None:
        env["VOOL_GITHUB_REF"] = env_ref
    subprocess.run(
        bash_script_args(PROJECT_ROOT / "installer" / "bootstrap_vool.sh", *args, "--dir", str(install_dir)),
        check=True, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )
    downloads = [line for line in calls.read_text(encoding="utf-8").splitlines() if "/archive/" in line]
    record = json.loads((install_dir / "config" / "build-source.json").read_text(encoding="utf-8"))
    return downloads, record


@pytest.mark.parametrize("ref", ["v0.7.0", "v0.7.0-beta", "0.7.0", "v1.12.3"])
def test_a_release_tag_is_fetched_from_the_tags_archive(tmp_path, ref):
    downloads, record = _run_shell_bootstrap(tmp_path, "--ref", ref)
    assert downloads and f"{BASE}/refs/tags/{ref}.tar.gz" in downloads[0], downloads
    assert record["ref"] == ref and record["source_url"] == f"{BASE}/refs/tags/{ref}.tar.gz"
    # A tag is not a branch: readers fall back to "ref" when "branch" is empty.
    assert record["branch"] == ""


@pytest.mark.parametrize("ref", ["main", "release/0.7", "fix/version-bump", "v0.7-wip"])
def test_a_branch_is_still_fetched_from_the_heads_archive(tmp_path, ref):
    downloads, record = _run_shell_bootstrap(tmp_path, "--ref", ref)
    assert downloads and f"{BASE}/refs/heads/{ref}.tar.gz" in downloads[0], downloads
    assert record["branch"] == ref


def test_the_default_ref_from_the_environment_follows_the_same_rule(tmp_path):
    downloads, _record = _run_shell_bootstrap(tmp_path, env_ref="v0.7.0")
    assert downloads and f"{BASE}/refs/tags/v0.7.0.tar.gz" in downloads[0], downloads


def _ps1() -> str:
    return (PROJECT_ROOT / "installer" / "bootstrap_vool.ps1").read_text(encoding="utf-8")


def test_the_powershell_bootstrap_builds_the_tags_archive_for_a_release_tag():
    script = _ps1()
    assert "archive/refs/tags/$Ref.zip" in script
    # The default archive URL is chosen by the tag rule, not hard-wired to refs/heads.
    default_line = next(line for line in script.splitlines() if "IsNullOrWhiteSpace($ArchiveUrl)" in line)
    assert "$RefIsReleaseTag" in default_line and "refs/tags" in default_line, default_line
    match = re.search(r"\$Ref -match '([^']+)'", script)
    assert match, "the tag rule is missing"
    rule = re.compile(match.group(1))
    for tag in ("v0.7.0", "v0.7.0-beta", "0.7.0"):
        assert rule.search(tag), tag
    for branch in ("main", "release/0.7", "v0.7-wip"):
        assert not rule.search(branch), branch


def test_the_powershell_bootstrap_records_no_branch_for_a_tag():
    script = _ps1()
    block = script[script.index("function Write-BuildMetadata"):script.index("function Run-Installer")]
    assert "branch = $Ref" not in block, block
