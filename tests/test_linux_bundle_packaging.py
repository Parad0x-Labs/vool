"""Installer ownership/rollback proof; actual Linux runtime proof runs in release-linux.yml."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "installer/bundle/linux"
spec = importlib.util.spec_from_file_location("linux_install", LINUX / "install_bundle.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def seal(package: Path) -> None:
    files = sorted(p for p in package.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (package / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(package).as_posix()}\n" for p in files))


@pytest.fixture
def package(tmp_path):
    package = tmp_path / "package with spaces"
    shutil.copytree(LINUX, package)
    (package / "python/bin").mkdir(parents=True)
    shutil.copy2(Path(sys.executable).resolve(), package / "python/bin/python3")
    (package / "app/apps").mkdir(parents=True)
    shutil.copy2(ROOT / "apps/vool_api_server.py", package / "app/apps/vool_api_server.py")
    (package / "SOURCE_COMMIT").write_text("a" * 40 + "\n")
    (package / "vool.png").write_bytes((ROOT / "installer/assets/vool.png").read_bytes())
    seal(package)
    return package


@pytest.fixture
def home(tmp_path):
    home = tmp_path / 'home with spaces and $dollar'
    home.mkdir()
    return home


def test_install_reinstall_upgrade_and_uninstall_preserve_profile(package, home):
    profile = home / ".vool_runtime"
    profile.mkdir()
    (profile / "saved-chat.txt").write_text("keep this")
    installer.install(package, home)
    root, binary, desktop = installer.layout(home)
    assert binary.resolve() == root / "versions" / ("a" * 40) / "vool"
    assert installer.DESKTOP_MARKER in desktop.read_text()
    assert 'Exec="' in desktop.read_text() and r'\\$dollar' in desktop.read_text()
    installer.install(package, home)
    (package / "SOURCE_COMMIT").write_text("b" * 40 + "\n")
    seal(package)
    installer.install(package, home)
    assert (root / "current/SOURCE_COMMIT").read_text().strip() == "b" * 40
    (root / "user-note.txt").write_text("preserve unrelated content")
    installer.uninstall(home)
    installer.uninstall(home)
    assert not os.path.lexists(binary)
    assert not desktop.exists()
    assert (profile / "saved-chat.txt").read_text() == "keep this"
    assert (root / "user-note.txt").read_text() == "preserve unrelated content"


@pytest.mark.parametrize("collision", ["root", "binary", "desktop", "parent-symlink"])
def test_install_refuses_foreign_content(package, home, collision):
    root, binary, desktop = installer.layout(home)
    target = {"root": root, "binary": binary, "desktop": desktop, "parent-symlink": home / ".local"}[collision]
    if collision == "parent-symlink":
        foreign = home / "foreign"
        foreign.mkdir()
        target.symlink_to(foreign)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("foreign content")
    with pytest.raises(ValueError):
        installer.install(package, home)
    if collision != "parent-symlink":
        assert target.read_text() == "foreign content"
    else:
        assert not any(foreign.iterdir())


def test_tampered_package_refuses_without_touching_existing_install(package, home):
    installer.install(package, home)
    root, binary, _ = installer.layout(home)
    previous = binary.resolve()
    (package / "launch.py").write_text("tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        installer.install(package, home)
    assert binary.resolve() == previous
    assert (root / "current/launch.py").read_text() != "tampered"


def test_failed_upgrade_retains_previous_version_and_entrypoints(package, home):
    installer.install(package, home)
    root, binary, desktop = installer.layout(home)
    previous, entry = binary.resolve(), desktop.read_bytes()
    (package / "SOURCE_COMMIT").write_text("c" * 40 + "\n")
    seal(package)
    # An unexplained staging link is a real filesystem refusal, not an injected mock.
    (root / ".next-current").write_text("foreign content")
    with pytest.raises(FileExistsError):
        installer.install(package, home)
    assert binary.resolve() == previous and desktop.read_bytes() == entry
    assert not (root / "versions" / ("c" * 40)).exists()
    assert not (root / ".install-lock").exists()
    assert (root / ".next-current").read_text() == "foreign content"


def test_install_rejects_new_files_and_escaping_symlinks(package, home):
    extra = package / "not-in-checksums.txt"
    extra.write_text("unexpected payload")
    with pytest.raises(ValueError, match="unverified"):
        installer.install(package, home)
    extra.unlink()
    (package / "escaping-link").symlink_to(home)
    with pytest.raises(ValueError, match="external symlink"):
        installer.install(package, home)
    assert not (home / ".local").exists()


def test_uninstall_refuses_unowned_root_and_unknown_version_content(package, home):
    root, _, _ = installer.layout(home)
    root.mkdir(parents=True)
    (root / "user-data").write_text("keep")
    with pytest.raises(ValueError, match="unowned"):
        installer.uninstall(home)
    (root / "user-data").unlink()
    root.rmdir()
    installer.install(package, home)
    unknown = root / "versions/user-folder"
    unknown.mkdir()
    with pytest.raises(ValueError, match="unknown content"):
        installer.uninstall(home)
    assert (root / "current/vool").exists()


def test_bundle_scripts_parse_and_wrong_host_build_refuses_without_staging(tmp_path):
    scripts = [ROOT / "installer/bundle/build_linux_bundle.sh", LINUX / "install.sh", LINUX / "uninstall.sh", LINUX / "vool"]
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
    if sys.platform != "linux":
        result = subprocess.run(["bash", str(scripts[0]), "--out", str(tmp_path / "output")], capture_output=True, text=True)
        assert result.returncode == 1 and "Build on Linux x86_64" in result.stderr
        assert not (tmp_path / "output").exists()


def test_builder_uses_standalone_locked_wheels_and_exact_source():
    script = (ROOT / "installer/bundle/build_linux_bundle.sh").read_text()
    for contract in ("cpython-3.12.13-linux-x86_64-gnu", "--require-hashes", "--no-build", "--locked", 'git archive "${SHA}"', "pyvenv.cfg", "preflight_served_imports_or_exit", "refusing to overwrite"):
        assert contract in script
    assert "vool_install_linux_bundle" in (LINUX / "install.sh").read_text()


def test_workflow_builds_once_smokes_both_targets_and_gates_release():
    workflow = yaml.load((ROOT / ".github/workflows/release-linux.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "pull_request", "push"}
    assert workflow["on"]["push"]["tags"] == ["v*"]
    jobs = workflow["jobs"]
    assert jobs["build"]["runs-on"] == "ubuntu-22.04"
    assert jobs["smoke"]["needs"] == "build"
    assert jobs["smoke"]["strategy"]["matrix"]["os"] == ["ubuntu-22.04", "ubuntu-24.04"]
    assert jobs["release"]["needs"] == ["build", "smoke"]
    assert workflow["permissions"]["contents"] == "read"
    assert jobs["release"]["permissions"]["contents"] == "write"
    release_step = jobs["release"]["steps"][-1]["run"]
    assert "--clobber" not in release_step and "--draft" in release_step
    smoke_step = jobs["smoke"]["steps"][-2]["run"]
    assert "sha256sum -c" in smoke_step and "--expected-commit" in smoke_step


def test_bootstrap_explicit_beta_pin_and_trunk_override_resolve_correct_archives(home):
    bootstrap = ROOT / "installer/bootstrap_vool.sh"
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(["bash", str(bootstrap), "--ref", "v0.7.0-beta", "--help"], env=environment, capture_output=True, text=True, check=True)
    assert "default: v0.7.0-beta" in result.stdout
    definitions = bootstrap.read_text().rsplit('main "$@"', 1)[0]
    result = subprocess.run(["bash"], input=definitions + '\nparse_args --ref v0.7.0-beta\nprintf "%s\\n" "$ARCHIVE_URL"\n', env=environment, capture_output=True, text=True, check=True)
    assert result.stdout.strip().endswith("/archive/refs/tags/v0.7.0-beta.tar.gz")
    environment["VOOL_GITHUB_REF"] = "main"
    result = subprocess.run(["bash"], input=definitions + '\nprintf "%s\\n" "$ARCHIVE_URL"\n', env=environment, capture_output=True, text=True, check=True)
    assert result.stdout.strip().endswith("/archive/refs/heads/main.tar.gz")
    assert not (home / "vool-local").exists()


def test_user_install_docs_pin_shell_script_and_source_to_the_beta_tag():
    for name in ("README.md", "docs/INSTALL.md", "docs/getting-started/install.md"):
        content = (ROOT / name).read_text()
        assert "/vool/main/installer/bootstrap_vool.sh" not in content
        assert "/vool/main/installer/bootstrap_vool.ps1" in content
        for line in content.splitlines():
            if "bash bootstrap_vool.sh" in line or 'bash "$tmp"' in line:
                assert "--ref v0.7.0-beta" in line
            if "bootstrap_vool.ps1" in line and " -File " in line:
                assert "-Ref" not in line
