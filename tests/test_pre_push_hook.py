"""Public-push accident guard: scripts/hooks/pre-push.

The hook is run directly the way git runs it: remote name and URL as arguments,
one "<local ref> <local sha> <remote ref> <remote sha>" line per ref on stdin.
Nothing is pushed anywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "pre-push"
SHA = "1" * 40
ZERO = "0" * 40

VOOL_HTTPS = "https://github.com/Parad0x-Labs/vool.git"
VOOL_URLS = [
    VOOL_HTTPS,
    "https://github.com/Parad0x-Labs/vool",
    "https://github.com/parad0x-labs/VOOL/",
    "git@github.com:Parad0x-Labs/vool.git",
    "ssh://git@github.com/Parad0x-Labs/vool.git",
    "git@github.com-work:Parad0x-Labs/vool.git",
]


def _line(remote_ref: str, local_sha: str = SHA, local_ref: str | None = None) -> str:
    local_ref = local_ref if local_ref is not None else remote_ref
    if local_sha == ZERO:
        local_ref = "(delete)"
    return f"{local_ref} {local_sha} {remote_ref} {SHA}\n"


def _run(url: str, stdin: str, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX sh")
    env = {k: v for k, v in os.environ.items() if k not in {"VOOL_PUBLIC_RELEASE", "NULLA_PUBLIC_RELEASE"}}
    env.update(env_extra or {})
    return subprocess.run(
        [sh, str(HOOK), "origin", url],
        input=stdin,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _go(cwd: Path) -> None:
    (cwd / "VOOL-DELIVERY").mkdir(exist_ok=True)
    (cwd / "VOOL-DELIVERY" / "GO_APPROVAL.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("url", VOOL_URLS)
def test_vool_feature_branch_passes(tmp_path: Path, url: str) -> None:
    assert _run(url, _line("refs/heads/fix/something"), tmp_path).returncode == 0


@pytest.mark.parametrize("url", VOOL_URLS)
def test_vool_main_blocked_without_release(tmp_path: Path, url: str) -> None:
    result = _run(url, _line("refs/heads/main"), tmp_path)
    assert result.returncode == 1
    assert "BLOCKED" in result.stderr


def test_vool_main_blocked_with_env_but_no_go_file(tmp_path: Path) -> None:
    assert _run(VOOL_HTTPS, _line("refs/heads/main"), tmp_path, {"VOOL_PUBLIC_RELEASE": "1"}).returncode == 1


def test_vool_main_blocked_with_go_file_but_no_env(tmp_path: Path) -> None:
    _go(tmp_path)
    assert _run(VOOL_HTTPS, _line("refs/heads/main"), tmp_path).returncode == 1


def test_vool_main_passes_on_release_path(tmp_path: Path) -> None:
    _go(tmp_path)
    assert _run(VOOL_HTTPS, _line("refs/heads/main"), tmp_path, {"VOOL_PUBLIC_RELEASE": "1"}).returncode == 0


def test_legacy_nulla_release_names_do_not_open_main(tmp_path: Path) -> None:
    (tmp_path / "NULLA-DELIVERY").mkdir()
    (tmp_path / "NULLA-DELIVERY" / "GO_APPROVAL.json").write_text("{}", encoding="utf-8")
    result = _run(VOOL_HTTPS, _line("refs/heads/main"), tmp_path, {"NULLA_PUBLIC_RELEASE": "1"})
    assert result.returncode == 1


def test_vool_main_delete_blocked(tmp_path: Path) -> None:
    assert _run(VOOL_HTTPS, _line("refs/heads/main", local_sha=ZERO), tmp_path).returncode == 1


def test_vool_tag_blocked_without_release(tmp_path: Path) -> None:
    assert _run(VOOL_HTTPS, _line("refs/tags/v0.7.0-beta"), tmp_path).returncode == 1


def test_vool_tag_passes_on_release_path(tmp_path: Path) -> None:
    _go(tmp_path)
    result = _run(VOOL_HTTPS, _line("refs/tags/v0.7.0-beta"), tmp_path, {"VOOL_PUBLIC_RELEASE": "1"})
    assert result.returncode == 0


def test_vool_mixed_push_blocked_when_any_ref_is_main(tmp_path: Path) -> None:
    stdin = _line("refs/heads/fix/a") + _line("refs/heads/main", local_ref="refs/heads/fix/a")
    assert _run(VOOL_HTTPS, stdin, tmp_path).returncode == 1


def test_vool_branch_named_like_main_passes(tmp_path: Path) -> None:
    assert _run(VOOL_HTTPS, _line("refs/heads/main-fix"), tmp_path).returncode == 0
    assert _run(VOOL_HTTPS, _line("refs/heads/fix/main"), tmp_path).returncode == 0


def test_vool_empty_stdin_passes(tmp_path: Path) -> None:
    assert _run(VOOL_HTTPS, "", tmp_path).returncode == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Parad0x-Labs/nulla-local.git",
        "https://github.com/Parad0x-Labs/nulla-local",
        "git@github.com:Parad0x-Labs/nulla-local.git",
    ],
)
def test_retired_nulla_local_always_refused(tmp_path: Path, url: str) -> None:
    _go(tmp_path)
    result = _run(url, _line("refs/heads/fix/x"), tmp_path, {"VOOL_PUBLIC_RELEASE": "1"})
    assert result.returncode == 1
    assert "retired" in result.stderr


def test_nulla_local_product_still_allowed(tmp_path: Path) -> None:
    url = "https://github.com/Parad0x-Labs/nulla-local-product.git"
    assert _run(url, _line("refs/heads/main"), tmp_path).returncode == 0


def test_lookalike_repo_names_are_not_vool(tmp_path: Path) -> None:
    for url in (
        "https://github.com/Parad0x-Labs/vool-backup.git",
        "https://github.com/Parad0x-Labs/VOOL-Lookalike-Example.git",
        "https://github.com/someone/vool.git",
    ):
        assert _run(url, _line("refs/heads/main"), tmp_path).returncode == 0, url


@pytest.mark.parametrize("url", ["", "https://gitlab.com/Parad0x-Labs/vool.git", "/srv/git/vool.git"])
def test_unknown_or_empty_remote_fails_closed(tmp_path: Path, url: str) -> None:
    assert _run(url, _line("refs/heads/fix/x"), tmp_path).returncode == 1
