"""The .deb around the Linux package, and how a newer system copy reaches a person's own VOOL.

The real apt install, first open, in-app update, apt upgrade and apt remove run in
ops/update_journey.py --platform linux-deb and ops/linux_deb_smoke.py (release-linux.yml).
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "installer/bundle/linux"
sys.path.insert(0, str(ROOT))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


system_copy = load("linux_system_copy", LINUX / "system_copy.py")
deb = load("linux_deb_builder", ROOT / "installer/bundle/build_linux_deb.py")
needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg builds and orders .deb files (Linux)")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def person(tmp_path, monkeypatch):
    """A person whose own copy (0.8.0-beta) is set up in ~/.local/share/vool, and a system copy beside it."""
    home = tmp_path / "home"
    own = home / ".local/share/vool/versions" / ("a" * 40)
    own.mkdir(parents=True)
    (own / "VERSION").write_text("0.8.0-beta\n")
    system = tmp_path / "opt/vool/package"
    system.mkdir(parents=True)
    (system / "install.sh").write_text("exit 0\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home / ".vool_runtime"))
    return own, system


def journal(profile: Path, *steps: str, torn: str = "") -> None:
    folder = profile / "data/update_v2/transactions/1794500000-0.8.1-beta"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"ts": 1.0, "step": step}) for step in steps]
    (folder / "journal.jsonl").write_text("\n".join(lines) + "\n" + torn)


def test_a_newer_system_copy_is_set_up_and_an_older_or_equal_one_never_is(person):
    own, system = person
    quiet = ["--port", str(free_port())]
    for version, expected in (("0.8.1-beta", True), ("0.8.0", True), ("0.8.0-beta", False), ("0.8.0-alpha", False),
                              ("0.7.9", False)):
        (system / "VERSION").write_text(version + "\n")
        assert system_copy.should_set_up(own, quiet, system=system) is expected, version
    # The updater's own order: beta.10 is after beta.2, so an in-app beta.10 is never replaced by beta.2.
    (own / "VERSION").write_text("0.8.1-beta.10\n")
    (system / "VERSION").write_text("0.8.1-beta.2\n")
    assert system_copy.should_set_up(own, quiet, system=system) is False


def test_only_this_persons_own_interactive_copy_is_brought_up(person, tmp_path):
    own, system = person
    (system / "VERSION").write_text("0.8.1-beta\n")
    quiet = ["--port", str(free_port())]
    assert system_copy.should_set_up(own, quiet, system=system) is True
    # The in-app updater restarts VOOL headless and expects the exact build it installed.
    assert system_copy.should_set_up(own, ["--headless", *quiet], system=system) is False
    # A package folder somewhere else (an unpacked tarball) is run as it is.
    elsewhere = tmp_path / "Downloads/vool-0.8.0-beta-linux-x86_64"
    shutil.copytree(own, elsewhere)
    assert system_copy.should_set_up(elsewhere, quiet, system=system) is False
    # No .deb on this computer: nothing to set up.
    (system / "install.sh").unlink()
    assert system_copy.should_set_up(own, quiet, system=system) is False


def test_never_while_vool_runs_or_an_update_has_not_ended(person, tmp_path):
    own, system = person
    (system / "VERSION").write_text("0.8.1-beta\n")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert system_copy.should_set_up(own, ["--port", str(port)], system=system) is False
        assert system_copy.should_set_up(own, [f"--port={port}"], system=system) is False
    quiet = ["--port", str(free_port())]
    profile = Path(os.environ["VOOL_HOME"])
    journal(profile, "created", "snapshot_complete", "swap_planned")
    assert system_copy.should_set_up(own, quiet, system=system) is False
    # A torn last line does not end it: the last complete step counts, as in the updater.
    journal(profile, "created", "swapped", torn='{"step": "finali')
    assert system_copy.should_set_up(own, quiet, system=system) is False
    for ended in ("finalized", "rolled_back", "aborted"):
        journal(profile, "created", "swapped", ended)
        assert system_copy.should_set_up(own, quiet, system=system) is True, ended


def test_the_profile_checked_is_the_one_vool_opens(person, monkeypatch):
    own, system = person
    (system / "VERSION").write_text("0.8.1-beta\n")
    home = Path(os.environ["HOME"])
    monkeypatch.delenv("VOOL_HOME")
    monkeypatch.delenv("NULLA_HOME", raising=False)
    # Only the pre-rename profile exists: VOOL opens that one, so its unfinished update counts.
    journal(home / ".nulla_runtime", "created", "swapped")
    assert system_copy.profile() == home / ".nulla_runtime"
    assert system_copy.should_set_up(own, ["--port", str(free_port())], system=system) is False


def test_the_launcher_sets_up_at_most_once_per_open(tmp_path):
    # Even if the check kept answering "set up", the launcher must open VOOL instead of looping.
    root = tmp_path / "home/.local/share/vool"
    package = root / "versions/abc"
    (package / "python/bin").mkdir(parents=True)
    shutil.copy2(LINUX / "vool", package / "vool")
    calls = tmp_path / "calls.log"
    (package / "python/bin/python3").write_text(
        f'#!/usr/bin/env bash\necho "${{VOOL_SYSTEM_COPY_SET_UP:-unset}} $*" >> "{calls}"\n'
        '[[ "$*" == *system_copy.py* ]] && exit 0\necho opened\n')
    os.chmod(package / "python/bin/python3", 0o755)
    (root / "current").symlink_to("versions/abc")
    result = subprocess.run(["bash", str(root / "current/vool"), "--port", "1"], capture_output=True, text=True,
                            timeout=30, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")})
    assert result.returncode == 0 and result.stdout.strip() == "opened", result.stderr
    first, second = calls.read_text().splitlines()
    assert first.split()[3].endswith("system_copy.py") and first.split()[-2:] == ["--port", "1"]
    # VOOL itself is opened with the person's arguments, and the launcher's own marker is not passed on.
    assert second.split()[0] == "unset" and second.split()[4].endswith("launch.py")
    assert second.split()[5:] == ["--port", "1"]


@needs_dpkg
def test_deb_versions_order_like_the_updater():
    from core.updater.versions import parse_semver

    versions = ["0.7.9", "0.8.0-alpha", "0.8.0-beta", "0.8.0-beta.2", "0.8.0-beta.10", "0.8.0-rc.1", "0.8.0",
                "0.8.1-beta", "0.10.0"]
    assert sorted(versions, key=parse_semver) == versions
    for older, newer in itertools.pairwise(versions):
        compared = subprocess.run(["dpkg", "--compare-versions", deb.deb_version(older), "lt", deb.deb_version(newer)])
        assert compared.returncode == 0, (older, newer)
    assert deb.deb_version("0.8.0-beta") == "0.8.0~beta"
    for unorderable in ("0.8.0-beta-2", "0.8.0+build.5", "v0.8.0", "0.8"):
        with pytest.raises(ValueError):
            deb.deb_version(unorderable)


def seal(package: Path) -> None:
    files = sorted(p for p in package.rglob("*") if p.is_file() and not p.is_symlink() and p.name != "SHA256SUMS")
    (package / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(package).as_posix()}\n" for p in files))


@pytest.fixture
def tarball(tmp_path):
    name = "vool-0.8.0-beta-linux-x86_64"
    package = tmp_path / "build" / name
    shutil.copytree(LINUX, package, ignore=shutil.ignore_patterns("__pycache__"))
    (package / "python/bin").mkdir(parents=True)
    # The package's own interpreter runs its own verification; here it is this test's Python.
    (package / "python/bin/python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    os.chmod(package / "python/bin/python3", 0o755)
    (package / "app/apps").mkdir(parents=True)
    shutil.copy2(ROOT / "apps/vool_api_server.py", package / "app/apps/vool_api_server.py")
    shutil.copy2(ROOT / "LICENSE", package / "app/LICENSE")
    shutil.copy2(ROOT / "installer/assets/vool.png", package / "vool.png")
    (package / "SOURCE_COMMIT").write_text("c" * 40 + "\n")
    (package / "VERSION").write_text("0.8.0-beta\n")
    seal(package)
    archive = tmp_path / "dist" / f"{name}.tar.gz"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(package, arcname=name)
    return archive


def dpkg_field(path: Path, field: str) -> str:
    return subprocess.run(["dpkg-deb", "-f", str(path), field], capture_output=True, text=True, check=True).stdout.strip()


@needs_dpkg
def test_the_deb_carries_the_exact_package_and_one_way_in(tarball, tmp_path):
    built = deb.build(tarball, tarball.parent, epoch=1794500000)
    assert built.name == "vool-0.8.0-beta-linux-x86_64.deb"
    assert Path(f"{built}.sha256").read_text() == f"{hashlib.sha256(built.read_bytes()).hexdigest()}  {built.name}\n"
    assert dpkg_field(built, "Package") == "vool" and dpkg_field(built, "Version") == "0.8.0~beta"
    assert dpkg_field(built, "Architecture") == "amd64"
    depends = dpkg_field(built, "Depends")
    assert depends.startswith("libc6 (>= 2.") and all(window in depends for window in deb.WINDOW)
    extracted = tmp_path / "installed"
    subprocess.run(["dpkg-deb", "-x", str(built), str(extracted)], check=True)
    package = extracted / "opt/vool/package"
    # Byte for byte the tarball's package: its own installer accepts it as is, for every person.
    installer = load("linux_install_bundle", LINUX / "install_bundle.py")
    assert installer.verify_package(package) == "c" * 40
    with tarfile.open(tarball) as tar:
        names = {Path(member.name).relative_to(tarball.name.removesuffix(".tar.gz")).as_posix()
                 for member in tar.getmembers() if member.isfile()}
    assert names == {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()}
    others = {p.relative_to(extracted).as_posix() for p in extracted.rglob("*")
              if p.is_file() and not p.is_relative_to(package)}
    assert others == {"usr/bin/vool", "usr/share/applications/vool.desktop",
                      "usr/share/icons/hicolor/512x512/apps/vool.png", "usr/share/doc/vool/copyright"}
    launcher = (extracted / "usr/bin/vool")
    assert launcher.stat().st_mode & 0o777 == 0o755
    subprocess.run(["bash", "-n", str(launcher)], check=True)
    text = launcher.read_text()
    assert "SYSTEM_PACKAGE=/opt/vool/package\n" in text and 'bash "${SYSTEM_PACKAGE}/install.sh"' in text
    assert 'exec "${OWN_ROOT}/current/vool" "$@"' in text
    desktop = (extracted / "usr/share/applications/vool.desktop").read_text()
    assert "Exec=/usr/bin/vool\n" in desktop and "Icon=vool\n" in desktop
    control = tmp_path / "control"
    subprocess.run(["dpkg-deb", "-e", str(built), str(control)], check=True)
    assert "kept" in (control / "postrm").read_text() and not (control / "postinst").exists()
    assert "rm " not in (control / "postrm").read_text()  # nothing in any person's home is touched
    listing = subprocess.run(["dpkg-deb", "-c", str(built)], capture_output=True, text=True, check=True).stdout
    assert all(line.split()[1] == "root/root" for line in listing.splitlines())


@needs_dpkg
def test_the_deb_is_refused_for_a_package_that_fails_its_own_checksums(tarball, tmp_path):
    with tarfile.open(tarball) as tar:
        tar.extractall(tmp_path / "x", filter="data")
    package = next((tmp_path / "x").iterdir())
    (package / "launch.py").write_text((package / "launch.py").read_text() + "\n# changed after sealing\n")
    tampered = tmp_path / "tampered" / tarball.name
    tampered.parent.mkdir()
    with tarfile.open(tampered, "w:gz") as tar:
        tar.add(package, arcname=package.name)
    with pytest.raises(ValueError, match="own verification"):
        deb.build(tampered, tampered.parent)
    assert not list(tampered.parent.glob("*.deb*"))


def test_release_workflow_builds_smokes_and_attaches_the_deb():
    workflow = yaml.load((ROOT / ".github/workflows/release-linux.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    build = "\n".join(step.get("run", "") for step in jobs["build"]["steps"])
    assert "installer/bundle/build_linux_deb.py" in build
    uploaded = next(step for step in jobs["build"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
    assert "vool-*-linux-x86_64.deb*" in uploaded["with"]["path"]
    smoke = "\n".join(step.get("run", "") for step in jobs["smoke"]["steps"])
    assert "ops/linux_deb_smoke.py" in smoke and "sha256sum -c vool-*-linux-x86_64.deb.sha256" in smoke
    assert "assets/*.deb" in jobs["release"]["steps"][-1]["run"]
