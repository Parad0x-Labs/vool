"""The macOS job sandbox: deny by default, a trusted launcher, and read-only git metadata.

Three defects, each confirmed in `sandbox/job_runner.py` before this change:

1. The Seatbelt profile opened with `(allow default)`, so every operation it did not name was
   allowed: Mach lookups of the keychain and LaunchServices, Apple Events, signals to the
   operator's own processes, preference reads, and hard-link creation.
2. `sandbox-exec` was found with `shutil.which`, so a `sandbox-exec` earlier on PATH ran
   instead of the system one and could ignore the profile entirely.
3. Nothing kept a job from writing `.git`: a planted hook or `core.fsmonitor` command runs
   OUTSIDE the sandbox the next time git runs there, VOOL's own git tools included.

The first group runs everywhere and pins the profile's shape and the launcher check. The
second group is macOS only and drives the real kernel: a mock of Seatbelt would prove nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

import sandbox.job_runner as jr
from sandbox.job_runner import (
    JobRunner,
    KernelIsolationUnavailableError,
    _macos_confined_profile,
    _seatbelt_subpath_literal,
    _trusted_sandbox_exec,
)
from sandbox.resource_limits import ExecutionPolicy

PY = sys.executable or "python3"
_MACOS_ONLY = "Seatbelt is macOS-only; these drive the real kernel boundary."


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    jr.reset_isolation_backend_probe_cache()
    yield
    jr.reset_isolation_backend_probe_cache()


# --- everywhere ---------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="PosixPath cannot be instantiated on Windows")
@pytest.mark.parametrize("deny_network", [True, False])
def test_profile_denies_by_default_in_both_network_modes(deny_network):
    profile = _macos_confined_profile((Path("/tmp/ws"),), deny_network=deny_network)
    assert "(allow default)" not in profile
    assert profile.startswith("(version 1)(deny default)")
    assert ("(deny network*)" in profile) is deny_network
    assert ("(allow network*)" in profile) is not deny_network
    # The keychain daemon is never reachable, network trusted or not.
    assert "com.apple.SecurityServer" not in profile


@pytest.mark.skipif(sys.platform == "win32", reason="PosixPath cannot be instantiated on Windows")
def test_git_metadata_deny_follows_every_write_grant():
    """Seatbelt is last-match-wins: the `.git` deny must come after every write allow,
    including a write root re-allowed inside the protected code root."""
    with tempfile.TemporaryDirectory() as ws:
        root = Path(ws).resolve()
        with patch.object(jr, "_runtime_protection_clauses",
                          return_value=f"(allow file-write* {_seatbelt_subpath_literal(root)})"):
            profile = _macos_confined_profile((root,))
    git_deny = f"(deny file-write* {jr._SEATBELT_GIT_METADATA_FILTER})"
    assert profile.endswith(git_deny)
    assert profile.rfind("(allow file-write*") < profile.rfind(git_deny)


def test_launcher_is_never_looked_up_on_path(tmp_path, monkeypatch):
    """A `sandbox-exec` first on PATH that would run the job bare must never be picked up.
    With the system launcher absent the job is refused, not handed to the shim."""
    shim = tmp_path / "sandbox-exec"
    shim.write_text("#!/bin/sh\nshift 2; shift; exec \"$@\"\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = JobRunner(ExecutionPolicy(workspace_root=ws.resolve(), writable_roots=(ws.resolve(),)))
    with patch.object(jr.sys, "platform", "darwin"), \
            patch.object(jr, "_SANDBOX_EXEC", str(tmp_path / "absent" / "sandbox-exec")):
        assert shutil.which("sandbox-exec") == str(shim)
        with pytest.raises(KernelIsolationUnavailableError):
            runner._with_network_isolation(["true"], (ws.resolve(),))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership and modes")
def test_launcher_must_be_a_root_owned_regular_file_in_a_root_owned_directory(tmp_path):
    # A real root-owned binary in a root-owned, non-writable directory on every POSIX host.
    trusted = "/usr/bin/env"
    with patch.object(jr, "_SANDBOX_EXEC", trusted):
        assert _trusted_sandbox_exec() == trusted

    writable_dir = tmp_path / "bin"
    writable_dir.mkdir()
    writable_dir.chmod(0o777)
    planted = writable_dir / "sandbox-exec"
    planted.write_text("#!/bin/sh\n", encoding="utf-8")
    planted.chmod(0o755)
    link = tmp_path / "link-to-env"
    link.symlink_to(trusted)
    for candidate in (planted, link, tmp_path / "missing"):
        with patch.object(jr, "_SANDBOX_EXEC", str(candidate)):
            assert _trusted_sandbox_exec() is None, candidate

    if os.geteuid() == 0:
        # As root the planted file is root-owned, so only the writable bits refuse it: prove
        # the mode check alone does.
        planted.chmod(0o777)
        writable_dir.chmod(0o755)
        with patch.object(jr, "_SANDBOX_EXEC", str(planted)):
            assert _trusted_sandbox_exec() is None


# --- macOS, real kernel ---------------------------------------------------------------------------


_MACH_PROBE = textwrap.dedent(
    """
    import ctypes, sys
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    port = ctypes.c_uint(0)
    bootstrap = ctypes.c_uint.in_dll(libc, "bootstrap_port")
    kr = libc.bootstrap_look_up(bootstrap, sys.argv[1].encode(), ctypes.byref(port))
    print(kr)
    """
)


@unittest.skipUnless(sys.platform == "darwin", _MACOS_ONLY)
class SeatbeltDenyByDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="hard_ws_")).resolve()
        self.outside = Path(tempfile.mkdtemp(prefix="hard_out_")).resolve()
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.addCleanup(shutil.rmtree, self.outside, True)

    def runner(self, **over) -> JobRunner:
        policy = dict(workspace_root=self.workspace, writable_roots=(self.workspace,), max_seconds=60)
        policy.update(over)
        return JobRunner(ExecutionPolicy(**policy))

    def _mach_lookup(self, service: str, *, confined: bool) -> int:
        script = self.workspace / "mach_probe.py"
        script.write_text(_MACH_PROBE, encoding="utf-8")
        argv = [PY, str(script), service]
        if confined:
            result = self.runner().run(argv, cwd=self.workspace)
        else:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout.strip())

    def test_keychain_and_launchservices_are_unreachable_but_were_reachable_unconfined(self) -> None:
        for service in ("com.apple.SecurityServer", "com.apple.coreservices.launchservicesd"):
            self.assertEqual(self._mach_lookup(service, confined=False), 0,
                             f"control: {service} must be reachable outside the sandbox")
            self.assertNotEqual(self._mach_lookup(service, confined=True), 0,
                                f"{service} was reachable from inside the job")

    def test_a_job_cannot_signal_a_process_outside_its_sandbox(self) -> None:
        sleeper = subprocess.Popen(["/bin/sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        result = self.runner().run(
            [PY, "-c", f"import os, signal; os.kill({sleeper.pid}, signal.SIGTERM)"], cwd=self.workspace
        )
        self.assertNotEqual(result.returncode, 0, "the job signalled a process it did not start")
        self.assertIsNone(sleeper.poll(), "the outside process was killed from inside the job")

    def test_a_job_cannot_hard_link_an_outside_file_into_its_workspace(self) -> None:
        canary = self.outside / "canary.txt"
        canary.write_text("ORIGINAL", encoding="utf-8")
        target = self.workspace / "alias.txt"
        self.runner().run(
            [PY, "-c", f"import os; os.link({str(canary)!r}, {str(target)!r}); "
                       f"open({str(target)!r}, 'w').write('escaped')"],
            cwd=self.workspace,
        )
        self.assertEqual(canary.read_text(encoding="utf-8"), "ORIGINAL")
        self.assertFalse(target.exists())

    def test_git_metadata_is_read_only_and_cannot_be_created(self) -> None:
        git_dir = self.workspace / ".git"
        (git_dir / "hooks").mkdir(parents=True)
        config = git_dir / "config"
        config.write_text("[core]\n", encoding="utf-8")
        attempts = {
            "config": f"open({str(config)!r}, 'a').write('fsmonitor = evil\\n')",
            "hook": f"open({str(git_dir / 'hooks' / 'pre-commit')!r}, 'w').write('evil')",
            "case": f"open({str(self.workspace / '.GIT' / 'config')!r}, 'a').write('evil')",
            "nested": f"import os; os.makedirs({str(self.workspace / 'sub' / '.git' / 'hooks')!r})",
            "rename": f"import os; os.rename({str(git_dir)!r}, {str(self.workspace / 'moved')!r})",
        }
        runner = self.runner()
        for name, code in attempts.items():
            result = runner.run([PY, "-c", code], cwd=self.workspace)
            self.assertNotEqual(result.returncode, 0, f"{name}: the write to git metadata succeeded")
        self.assertEqual(config.read_text(encoding="utf-8"), "[core]\n")
        self.assertFalse((git_dir / "hooks" / "pre-commit").exists())
        self.assertFalse((self.workspace / "sub" / ".git").exists())
        self.assertTrue(git_dir.is_dir())
        # Lookalikes stay ordinary files: only the `.git` component is protected.
        ok = runner.run(
            [PY, "-c", "open('.gitignore','w').write('x'); import os; os.makedirs('.github', exist_ok=True)"],
            cwd=self.workspace,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_toolchain_work_still_runs(self) -> None:
        """The deny-by-default baseline must not cost ordinary jobs: an interpreter, child
        processes, POSIX semaphores (multiprocessing) and workspace writes all still work."""
        code = textwrap.dedent(
            """
            import multiprocessing, subprocess, sys
            if __name__ == "__main__":
                with multiprocessing.Pool(2) as pool:
                    assert pool.map(abs, [-1, -2]) == [1, 2]
                subprocess.run([sys.executable, "-c", "print('child')"], check=True)
                open("out.txt", "w").write("ok")
                print("done")
            """
        )
        script = self.workspace / "work.py"
        script.write_text(code, encoding="utf-8")
        result = self.runner().run([PY, str(script)], cwd=self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("done", result.stdout)
        self.assertEqual((self.workspace / "out.txt").read_text(encoding="utf-8"), "ok")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_reads_still_work_on_a_read_only_repository(self) -> None:
        git = shutil.which("git")
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-q", "--allow-empty", "-m", "base"]):
            subprocess.run([git, *args], cwd=self.workspace, env=env, check=True, timeout=30)
        (self.workspace / "new.txt").write_text("x", encoding="utf-8")
        status = self.runner().run([git, "status", "--porcelain"], cwd=self.workspace)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("new.txt", status.stdout)
        commit = self.runner().run([git, "-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-q", "--allow-empty", "-m", "x"], cwd=self.workspace)
        self.assertNotEqual(commit.returncode, 0, "a job wrote a commit into .git")
