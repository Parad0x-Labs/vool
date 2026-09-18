from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

from tests.platform_helpers import bash_path, bash_script_args

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_start_vool_bat_recovers_from_stale_api_process() -> None:
    # A stale apps.vool_api_server process (alive but not listening on 11435) keeps the server log
    # and runtime DB locked, so a fresh start's redirect fails and the launcher retries forever
    # (docs/audits/vool_api_stale_startup_diagnostic_20260711.md). Start_VOOL.bat must skip a
    # duplicate when already healthy, and otherwise kill the stale server before launching.
    script = (PROJECT_ROOT / "Start_VOOL.bat").read_text(encoding="utf-8")
    assert "not starting a duplicate" in script
    assert "/healthz" in script
    assert "Stop-Process" in script
    assert "apps.vool_api_server" in script
    # The kill must gate on the process command line, not just the image name, so unrelated
    # python.exe/pythonw.exe processes are never terminated.
    assert "CommandLine -like '*apps.vool_api_server*'" in script
    assert "CommandLine -like '*vool_api_server.py*'" in script
    # The stale kill must run BEFORE the server launch line.
    assert script.index("Stop-Process") < script.rindex("-m apps.vool_api_server")


def test_xsearch_up_reports_searxng_state_honestly() -> None:
    # xsearch_up.ps1 must not claim "SearXNG up" unless `docker compose` actually succeeded, must probe
    # the engine (docker info) before running compose so a stopped Docker engine does not emit a raw
    # "failed to connect to the Docker API" error, and must degrade to the DuckDuckGo fallback rather
    # than failing the install.
    script = (PROJECT_ROOT / "scripts" / "xsearch_up.ps1").read_text(encoding="utf-8")
    assert "docker info" in script  # engine-liveness probe (added so compose never runs on a dead engine)
    assert "docker compose up -d" in script
    assert "SearXNG up at http://localhost:8080" in script
    assert "DuckDuckGo fallback" in script  # honest graceful degrade
    # The success message must be gated behind the compose exit-code check, not printed unconditionally.
    assert script.rindex("$LASTEXITCODE -ne 0") < script.index("SearXNG up at")
    # The engine probe (the `docker info 2>&1` invocation, not the comment) must precede the compose call.
    assert script.index("docker info 2>&1") < script.index("Set-Location")


def test_shell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_vool.sh").read_text(encoding="utf-8")

    assert 'REPO="${VOOL_GITHUB_REPO:-vool-local}"' in script
    assert 'INSTALL_DIR="${VOOL_INSTALL_DIR:-$HOME/vool-local}"' in script
    assert 'VOOL_GITHUB_REPO:-vool-hive-mind' not in script
    assert "--install-profile <id>" in script
    assert "ollama-only" in script
    assert "ollama-max" in script
    assert "VOOL_INSTALL_PROFILE" in script
    assert "VOOL_BUILD_COMMIT" in script
    assert "VOOL_BUILD_DIRTY_STATE" in script
    assert 'BUILD_COMMIT=""' in script
    assert "SOURCE_COMMIT" in script
    assert "SOURCE_DIRTY_STATE" in script
    assert 'resolve_archive_commit() {' in script
    assert 'write_build_metadata() {' in script
    assert 'json_bool_or_null() {' in script
    assert 'archive_has_common_root() {' in script
    assert 'config/build-source.json' in script
    assert '--source-commit <sha>' in script
    assert '--source-dirty <bool>' in script
    assert '--source-commit)' in script
    assert '--source-dirty)' in script
    assert 'profile_args=(--install-profile "${INSTALL_PROFILE}")' in script
    assert 'exec_with_profile_args() {' in script
    assert 'if [[ ${#profile_args[@]} -gt 0 ]]; then' in script
    assert '${INSTALL_DIR}/install_vool.sh' in script
    assert '${INSTALL_DIR}/installer/install_vool.sh' in script
    assert script.index('${INSTALL_DIR}/installer/install_vool.sh') < script.index('${INSTALL_DIR}/install_vool.sh')
    assert 'exec_with_profile_args "${launcher}"' in script
    assert 'exec_with_profile_args "${canonical}" --yes --start --openclaw default' in script
    assert 'exec_with_profile_args "${canonical}" --yes --openclaw default' in script
    assert 'no usable installer entrypoint was found' in script


def test_shell_bootstrap_handles_flat_git_archive_without_stripping_root_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "vool-flat-bootstrap.tar.gz"

    source_root.mkdir(parents=True)
    (source_root / "Install_And_Run_VOOL.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (source_root / "Install_And_Run_VOOL.sh").chmod(0o755)
    installer_dir = source_root / "installer"
    installer_dir.mkdir()
    (installer_dir / "install_vool.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "exit 99\n",
        encoding="utf-8",
    )
    (installer_dir / "install_vool.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(source_root / "Install_And_Run_VOOL.sh", arcname="Install_And_Run_VOOL.sh")
        tar.add(installer_dir, arcname="installer")

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_vool.sh",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    assert marker_path.exists()
    assert (install_dir / "Install_And_Run_VOOL.sh").exists()
    assert (install_dir / "installer" / "install_vool.sh").exists()


def test_powershell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_vool.ps1").read_text(encoding="utf-8")

    assert 'if ([string]::IsNullOrWhiteSpace($RepoName)) { $RepoName = "vool-local" }' in script
    assert "function Resolve-DefaultInstallDir" in script
    assert '[System.IO.DriveInfo]::GetDrives()' in script
    assert 'Join-Path $bestDrive.RootDirectory.FullName "VOOL\\vool-local"' in script
    assert 'Join-Path $HOME "vool-hive-mind"' not in script
    assert '[string]$InstallProfile = $env:VOOL_INSTALL_PROFILE' in script
    assert '[string]$SourceCommit = $env:VOOL_BUILD_COMMIT' in script
    assert '[string]$SourceDirtyState = $env:VOOL_BUILD_DIRTY_STATE' in script
    assert 'function Resolve-ArchiveCommit' in script
    assert 'function Resolve-DirtyState' in script
    assert 'function Write-BuildMetadata' in script
    assert 'build-source.json' in script
    assert '$psLauncher = Join-Path $InstallDir "Install_And_Run_VOOL.ps1"' in script
    assert '& powershell -NoProfile -ExecutionPolicy Bypass -File $psLauncher @psArgs' in script
    assert '-SkipBenchmark' not in script
    assert '/INSTALLPROFILE=$InstallProfile' in script
    assert 'install_vool.bat' in script
    assert 'installer\\\\install_vool.bat' in script
    assert script.index('installer\\\\install_vool.bat') < script.index('install_vool.bat')
    assert '& $canonical /Y /START "/OPENCLAW=default" @profileArgs' in script
    assert '& $canonical /Y "/OPENCLAW=default" @profileArgs' in script
    assert 'no usable installer entrypoint was found' in script


def test_shell_bootstrap_executes_launcher_without_profile_override(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    archive_root = source_root / "vool-hive-mind-main"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "vool-bootstrap.tar.gz"

    archive_root.mkdir(parents=True)
    (archive_root / "Install_And_Run_VOOL.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (archive_root / "Install_And_Run_VOOL.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root, arcname=archive_root.name)

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_vool.sh",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    assert marker_path.exists()
    assert marker_path.read_text(encoding="utf-8") == "\n"
    metadata_path = install_dir / "config" / "build-source.json"
    assert metadata_path.exists()
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"ref": "main"' in metadata
    assert f'"source_url": "{archive_path.resolve().as_uri()}"' in metadata


def test_shell_bootstrap_records_explicit_source_commit_for_custom_archive(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    archive_root = source_root / "vool-hive-mind-branch"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "vool-bootstrap.tar.gz"

    archive_root.mkdir(parents=True)
    (archive_root / "Install_And_Run_VOOL.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (archive_root / "Install_And_Run_VOOL.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root, arcname=archive_root.name)

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_vool.sh",
            "--ref",
            "codex/honest-ollama-prewarm-bootstrap",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--source-commit",
            "0123456789abcdef0123456789abcdef01234567",
            "--source-dirty",
            "true",
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    metadata_path = install_dir / "config" / "build-source.json"
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"ref": "codex/honest-ollama-prewarm-bootstrap"' in metadata
    assert '"branch": "codex/honest-ollama-prewarm-bootstrap"' in metadata
    assert '"commit": "0123456789abcdef0123456789abcdef01234567"' in metadata
    assert '"dirty_state": true' in metadata
    assert '"source_kind": "archive"' in metadata
