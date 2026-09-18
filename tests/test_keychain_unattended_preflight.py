"""C15 — the ONE bounded unattended preflight: zero credential prompts from unattended runs.

The operator saw repeated macOS dialogs — "A keychain cannot be found to store
node_signing_key" and the same for "Chrome" — triggered by VOOL-owned launches: scratch
test processes, shard children, detached updaters and headless browser renders that
reached an OS credential API (the keyring backend / the `security` CLI / the signed-in
browser's "Chrome Safe Storage" item) before anything pinned a non-interactive policy.

The layers under test, cumulatively with tests/test_keychain_unattended_policy.py:

1. ``core.unattended_preflight.preflight`` is the ONE bounded entrypoint wired into every
   VOOL-owned launcher/test path (see docs/KEYCHAIN_PREFLIGHT_LAUNCHER_MAP_20260903.md).
   Placement is load-bearing: it must run before any runtime import or credential call.
2. In scratch/test mode it pins file-backed isolated synthetic identity (vault credential
   store + file signer, NO Keychain grant — even a hostile inherited one) BEFORE any OS
   credential API can be reached, and selects the bundled Chromium + a fresh profile root
   for browser automation. An established operator home is recorded, never rewritten.
3. Provenance: every decision records the process ancestry chain; unrelated applications
   are recorded, never rewired.
4. Explicit, user-initiated secure storage goes through exactly ONE bounded call and fails
   TYPED with recovery instructions — never a retry loop.
5. Proven with PATH-injected fake ``security``/``keyring``/browser binaries (which record
   argv and fail on unexpected calls): fresh home, restart, parallel test shards, the
   updater and the browser-test bootstrap make ZERO credential CLI calls and ZERO signed-in
   profile accesses.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from tests import _credential_probe as probe

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


@pytest.fixture(autouse=True)
def _preflight_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this family runs against its own isolated synthetic home."""
    home = tmp_path / "preflight-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    # The root conftest's pytest_configure points the session at a shared runtime home via
    # the override, which shadows the env inside this process — point it back at this test's home.
    import core.runtime_paths

    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    return home


@pytest.fixture(autouse=True)
def _reset_keychain_breaker():
    """The timeout-typed test arms the process-wide breaker; it must not leak."""
    import core.bounded_keyring as bounded_keyring

    bounded_keyring._KEYCHAIN_BLOCKED = False
    yield
    bounded_keyring._KEYCHAIN_BLOCKED = False


@pytest.fixture(autouse=True)
def _reset_preflight_memo() -> None:
    import core.unattended_preflight as up

    up._REPORTED_SURFACES.clear()
    yield
    up._REPORTED_SURFACES.clear()


def _minimal_env(bin_dir: Path, log: Path, home: Path) -> dict[str, str]:
    """A bare operator-shell environment: NO inherited session pins.

    The load-bearing half of the zero-call proofs — the pytest session pins its own env
    (root conftest), so copying os.environ would let a sabotaged preflight hide behind it.
    These children start with NOTHING pinned: only what a real shell would carry plus the
    fake-binary PATH and the spy log location.
    """
    return {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "nobody")),
        "C15_FAKE_BIN_LOG": str(log),
    }


def _scratch_env(tmp_path: Path, log: Path, home: Path, *, hostile_grant: bool = True) -> dict[str, str]:
    bindir = probe.fake_bin_dir(tmp_path, python=PY, log=log)
    env = _minimal_env(bindir, log, home)
    env["VOOL_HOME"] = str(home)
    env["VOOL_TEST_MODE"] = "1"  # what ops/pytest_shards.py exports to shard children
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)])
    if hostile_grant:
        env["VOOL_KEYCHAIN_ALLOWED"] = "1"  # hostile: inherited from an operator shell
    return env


# --------------------------------------------------------------------------------------
# 1. Mode decision and provenance (in-process)
# --------------------------------------------------------------------------------------


def test_provenance_chain_records_the_real_ancestry() -> None:
    from core.unattended_preflight import provenance_chain

    chain = provenance_chain()
    assert chain, "the ancestry chain must never be empty"
    assert chain[0].pid == os.getpid()
    assert chain[0].ppid == os.getppid()
    # pytest.main() preserves the host runner's command. Check the actual recorded
    # command rather than requiring a launcher spelling the host need not use.
    actual = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(os.getpid())],
                            check=True, capture_output=True, text=True, timeout=2)
    assert chain[0].argv == tuple(actual.stdout.strip().split())


@pytest.mark.parametrize("argv", [
    ("python", "-m", "pytest"),
    ("python", str(REPO / "apps/vool_cli.py")),
])
def test_known_test_and_product_launches_have_owned_provenance(argv):
    from core.unattended_preflight import ProvenanceEntry, classify_provenance

    assert classify_provenance([ProvenanceEntry(pid=500, ppid=1, argv=argv)]) is True


def test_unrelated_provenance_is_classified_not_vool() -> None:
    from core.unattended_preflight import ProvenanceEntry, classify_provenance

    foreign = [
        ProvenanceEntry(pid=500, ppid=1, argv=("/Applications/SomeApp/Contents/MacOS/SomeApp",)),
        ProvenanceEntry(pid=1, ppid=0, argv=("/sbin/launchd",)),
    ]
    assert classify_provenance(foreign) is False


def test_scratch_mode_pins_non_interactive_storage_before_any_credential_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.unattended_preflight as up

    monkeypatch.setenv("VOOL_TEST_MODE", "1")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "auto")
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")  # hostile inherited grant
    report = up.preflight("tests.scratch-pin")
    assert report.mode == "scratch"
    assert "removed inherited VOOL_KEYCHAIN_ALLOWED grant" in report.actions
    assert os.environ.get("VOOL_CREDENTIAL_STORE") == "vault"
    assert os.environ.get("VOOL_KEY_STORAGE_MODE") == "file"
    assert os.environ.get("VOOL_KEYCHAIN_ALLOWED", "") == ""


def test_established_unattended_boot_is_recorded_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unattended boot against an operator home keeps its storage policy untouched."""
    import core.unattended_preflight as up

    home = tmp_path / "established-home"
    (home / "data" / "keys").mkdir(parents=True)
    (home / "data" / "credentials.meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_UNATTENDED", "1")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    monkeypatch.delenv("VOOL_TEST_MODE", raising=False)
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    # Inside pytest these markers exist; an unattended PRODUCT boot has none of them.
    for marker in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "PYTEST_XDIST_WORKER", "VOOL_GATE"):
        monkeypatch.delenv(marker, raising=False)

    report = up.preflight("tests.established-unattended")
    assert report.mode == "established-unattended"
    assert report.actions == [], "an established home must never be rewritten"
    assert os.environ.get("VOOL_CREDENTIAL_STORE") == "auto"


def test_preflight_selects_bundled_chromium_and_a_fresh_profile_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import core.unattended_preflight as up

    cache = tmp_path / "ms-playwright"
    binary = cache / "chromium_headless_shell-9999/chrome-headless-shell-test/chrome-headless-shell"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setenv("VOOL_TEST_MODE", "1")
    monkeypatch.delenv("VOOL_BROWSER_BINARY", raising=False)
    monkeypatch.delenv(up.BROWSER_PROFILE_ROOT_ENV, raising=False)
    report = up.preflight("tests.browser-select")
    assert report.browser_binary == str(binary)
    assert report.browser_binary, "a machine with a bundled chromium must select it"
    assert "ms-playwright" in report.browser_binary, (
        f"the selected browser must be the bundled Chromium, not an installed one: {report.browser_binary}"
    )
    lowered = report.browser_binary.lower()
    assert "headless-shell" in lowered or "chrome for testing" in lowered or "chromium" in lowered
    # Measured 2026-09-03: the FULL "Chrome for Testing" build hangs on a fresh profile
    # (first-run/Keychain init); only the headless shell is safe for unattended runs.
    if "headless-shell" in " ".join(report.browser_binary for report in [report]):
        assert lowered.endswith("chrome-headless-shell")
    assert report.browser_profile_root == os.environ[up.BROWSER_PROFILE_ROOT_ENV]
    assert Path(report.browser_profile_root).is_dir()


def test_missing_bundled_browser_does_not_select_an_installed_browser(monkeypatch, tmp_path):
    import core.unattended_preflight as up

    monkeypatch.setenv("VOOL_TEST_MODE", "1")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty-cache"))
    monkeypatch.delenv("VOOL_BROWSER_BINARY", raising=False)
    report = up.preflight("tests.browser-absent")
    assert report.browser_binary == ""
    assert "VOOL_BROWSER_BINARY" not in os.environ


def test_foreign_surface_without_vool_ancestry_is_only_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.unattended_preflight as up
    from core.unattended_preflight import ProvenanceEntry

    monkeypatch.setenv("VOOL_TEST_MODE", "1")

    def foreign_chain(*, max_depth: int = 8):
        return [ProvenanceEntry(pid=os.getpid(), ppid=1, argv=("/opt/otherapp/run",))]

    monkeypatch.setattr(up, "provenance_chain", foreign_chain)
    report = up.preflight("com.acme.embed")
    assert report.vool_owned is False
    assert report.actions == [], "an unrelated application's embed must never be rewired"


def test_preflight_report_is_written_with_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.unattended_preflight as up

    monkeypatch.setenv("VOOL_TEST_MODE", "1")
    report = up.preflight("tests.report-check")
    from core.runtime_paths import active_vool_home
    reports = [p for p in (active_vool_home() / "data" / "preflight").glob("*.json") if "report-check" in p.name]
    assert reports, "the preflight report must be written under the active home"
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["surface"] == "tests.report-check"
    assert payload["pid"] == os.getpid()
    assert isinstance(payload["provenance"], list) and payload["provenance"]
    assert "argv" in payload["provenance"][0]
    assert report.mode == "scratch"


# --------------------------------------------------------------------------------------
# 2. Explicit secure storage: exactly ONE bounded call, typed failure, no retries
# --------------------------------------------------------------------------------------


class _CountingBackend:
    def __init__(self, *, fail: str | None = None, delay: float = 0.0):
        self.store: dict[tuple[str, str], str] = {}
        self.counts = {"set_password": 0, "get_password": 0}
        self.fail = fail
        self.delay = delay

    def set_password(self, service: str, account: str, password: str) -> None:
        self.counts["set_password"] += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail == "locked":
            raise Exception("The keychain is locked: errSecLocked")
        if self.fail == "missing":
            raise Exception("A keychain cannot be found: No such keychain")
        if self.fail == "boom":
            raise RuntimeError("backend refused")
        self.store[(service, account)] = password

    def get_password(self, service: str, account: str) -> str | None:
        self.counts["get_password"] += 1
        return self.store.get((service, account))


def test_explicit_secure_storage_success_is_exactly_one_bounded_call() -> None:
    from core.unattended_preflight import explicit_secure_storage

    backend = _CountingBackend()
    explicit_secure_storage(lambda: backend.set_password("svc", "acct", "v"), what="unit")
    assert backend.counts["set_password"] == 1, "exactly one bounded call — never a retry loop"
    assert backend.counts["get_password"] == 0


@pytest.mark.parametrize(
    ("fail", "code"),
    [
        ("locked", "locked"),
        ("missing", "missing_keychain"),
        ("boom", "backend_error"),
    ],
)
def test_explicit_secure_storage_fails_typed_with_recovery_never_retries(
    fail: str, code: str
) -> None:
    from core.unattended_preflight import SecureStorageError, explicit_secure_storage

    backend = _CountingBackend(fail=fail)
    with pytest.raises(SecureStorageError) as caught:
        explicit_secure_storage(lambda: backend.set_password("svc", "acct", "v"), what="unit")
    assert caught.value.code == code
    assert caught.value.recovery, "every typed outcome carries operator recovery instructions"
    assert "Keychain Access" in caught.value.recovery or "keychain" in caught.value.recovery.lower()
    assert backend.counts["set_password"] == 1, "one bounded attempt; the failure must not retry"


def test_explicit_secure_storage_timeout_types_a_pending_prompt() -> None:
    from core.unattended_preflight import SecureStorageError, explicit_secure_storage

    backend = _CountingBackend(delay=2.0)
    import core.bounded_keyring as bounded_keyring

    old = bounded_keyring.DEFAULT_TIMEOUT_S
    bounded_keyring.DEFAULT_TIMEOUT_S = 0.2
    try:
        with pytest.raises(SecureStorageError) as caught:
            explicit_secure_storage(lambda: backend.set_password("svc", "acct", "v"), what="unit")
    finally:
        bounded_keyring.DEFAULT_TIMEOUT_S = old
    assert caught.value.code == "timeout_prompt_pending"
    assert "pending" in caught.value.recovery.lower()
    assert backend.counts["set_password"] == 1


def test_store_credential_keychain_write_fails_typed_one_call_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit Settings save path: granted, real backend present, backend refuses —
    the operator gets a TYPED failure with recovery instructions after exactly ONE call."""
    from core import credential_store
    from core.unattended_preflight import SecureStorageError

    attempts = {"n": 0}

    class FailingBackend:
        def set_password(self, service: str, account: str, password: str) -> None:
            attempts["n"] += 1
            raise Exception("A keychain cannot be found to store ´svc´")

        def get_password(self, service: str, account: str):
            return None

    fake = mock.Mock(wraps=None)
    fake.get_keyring.return_value = FailingBackend()
    fake.set_password = FailingBackend().set_password
    fake.get_password = lambda s, a: None
    monkeypatch.setattr(credential_store, "_load_keyring", lambda: fake)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")

    with pytest.raises(SecureStorageError) as caught:
        credential_store.store_credential("llm.cloud.openrouter", "skk-typed", label="t")
    assert caught.value.code == "missing_keychain"
    assert "Keychain Access" in caught.value.recovery
    assert attempts["n"] == 1, "exactly one bounded call — never a retry loop"


# --------------------------------------------------------------------------------------
# 3. Scratch subprocess: zero credential API calls under a hostile inherited grant
# --------------------------------------------------------------------------------------

_SCRATCH_DRIVER = """
import sys
sys.path.insert(0, {repo!r})

# The wired-launcher shape: preflight FIRST, then runtime credential use.
{preflight_block}
from network.signer import load_or_create_local_keypair, key_storage_mode
kp = load_or_create_local_keypair()

from core import credential_store
credential_store.store_credential("probe.scratch", "probe-value", label="scratch")
value = credential_store.get_credential("probe.scratch")
assert value == "probe-value", value
print("MODE=%s BACKEND=%s" % (key_storage_mode(), credential_store.active_backend()))
"""


def _spy_keyring_shim(tmp_path: Path, spy_log: Path) -> Path:
    """Reuse the existing policy test's spy keyring so a REAL backend appears present."""
    from tests.test_keychain_unattended_policy import _SPY_KEYRING_SOURCE

    shim = tmp_path / "spy-shim"
    shim.mkdir(exist_ok=True)
    (shim / "keyring.py").write_text(_SPY_KEYRING_SOURCE, encoding="utf-8")
    return shim


def test_scratch_driver_through_preflight_makes_zero_credential_calls(tmp_path: Path) -> None:
    spy_log = tmp_path / "fakes.jsonl"
    home = tmp_path / "scratch-home"
    home.mkdir()
    shim = _spy_keyring_shim(tmp_path, spy_log)
    driver = tmp_path / "scratch_driver.py"
    env = _scratch_env(tmp_path, spy_log, home)
    env["PYTHONPATH"] = os.pathsep.join([str(shim), str(REPO)])
    env["VOOL_KEYRING_SPY_LOG"] = str(spy_log.with_suffix(".spykeyring.jsonl"))
    done = probe.run_driver(
        PY,
        _SCRATCH_DRIVER.format(
            repo=str(REPO),
            preflight_block=(
                "from core.unattended_preflight import preflight\n"
                'preflight("tests.scratch-driver")\n'
            ),
        ),
        driver,
        env,
    )
    assert done.returncode == 0, f"driver failed: {done.stdout} {done.stderr}"
    assert "BACKEND=vault" in done.stdout, done.stdout
    assert "MODE=encrypted_file" in done.stdout, done.stdout
    assert probe.credential_calls(spy_log) == [], (
        f"unattended scratch run issued credential CLI calls: {probe.credential_calls(spy_log)[:3]}"
    )
    keyring_spy = spy_log.with_suffix(".spykeyring.jsonl")
    assert not keyring_spy.exists() or keyring_spy.read_text(encoding="utf-8").strip() == "", (
        f"unattended scratch run issued keyring backend calls: {keyring_spy.read_text()[:300]}"
    )
    # provenance of the synthetic attempt was recorded
    report_files = list((home / "data" / "preflight").glob("*.json"))
    assert report_files, "the scratch run must record its preflight report with ancestry"
    payload = json.loads(report_files[0].read_text(encoding="utf-8"))
    assert payload["vool_owned"] is True
    assert any("python" in entry["argv"][0] for entry in payload["provenance"])


# --------------------------------------------------------------------------------------
# 4. Browser automation: fresh isolated profile, bundled chromium, zero profile access
# --------------------------------------------------------------------------------------


def test_chrome_render_uses_a_fresh_isolated_profile_and_the_pinned_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.browser.browser_render import chrome_render

    log = tmp_path / "fakes.jsonl"
    bindir = probe.fake_bin_dir(tmp_path, python=PY, log=log)
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    monkeypatch.setenv("VOOL_BROWSER_BINARY", str(bindir / probe.FAKE_BROWSER))
    monkeypatch.setenv("VOOL_BROWSER_PROFILE_DIR", str(profile_root))
    monkeypatch.setenv("C15_FAKE_BIN_LOG", str(log))  # the render subprocess inherits this

    result = chrome_render("data:text/html,<title>c15</title>hello")
    assert result["status"] == "ok", result
    calls = probe.browser_calls(log)
    assert calls, "the fake browser must have been invoked"
    argv = calls[0]["argv"]
    assert str(bindir / probe.FAKE_BROWSER) in argv[0]
    user_data = [a for a in argv if a.startswith("--user-data-dir=")]
    assert user_data, f"unattended renders must carry a fresh isolated profile: {argv}"
    profile = user_data[0].split("=", 1)[1]
    assert profile.startswith(str(profile_root)), "the profile must live under the isolated root"
    assert "Google/Chrome" not in profile and "Application Support" not in profile
    assert not Path(profile).exists(), "the fresh profile must be cleaned up after the render"
    assert probe.credential_calls(log) == []


def test_real_bundled_chromium_render_with_isolated_profile_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Served proof: the bundled Chromium renders through the isolated profile seam, offline."""
    from core.unattended_preflight import select_bundled_chromium

    bundled = select_bundled_chromium()
    if not bundled:  # loud, not silent: this machine provisioned one for the lane
        pytest.skip("no bundled chromium in the playwright cache on this machine")

    from tools.browser import browser_render
    from tools.browser.browser_render import chrome_render

    page = tmp_path / "page.html"
    page.write_text("<html><head><title>c15-served</title></head><body>isolated</body></html>", encoding="utf-8")
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    monkeypatch.setenv("VOOL_BROWSER_BINARY", bundled)
    monkeypatch.setenv("VOOL_BROWSER_PROFILE_DIR", str(profile_root))
    browser_render.reset_browser_binary_cache_for_test()  # drop any earlier test's memoized binary
    try:
        result = chrome_render(page.as_uri(), timeout_ms=30_000)
    finally:
        browser_render.reset_browser_binary_cache_for_test()
    assert result["status"] == "ok", result
    assert "isolated" in result["text"], result["text"][:200]
    assert "c15-served" in result["title"]


def test_served_browser_bootstrap_uses_the_bundled_chromium_with_a_fresh_profile(
    tmp_path: Path,
) -> None:
    """The browser-test bootstrap (tests/served_browser.py) never approaches the signed-in profile."""
    log = tmp_path / "fakes.jsonl"
    bindir = probe.fake_bin_dir(tmp_path, python=PY, log=log)
    env = probe.env_for(bindir, log)
    env["PYTHONPATH"] = str(REPO)

    driver = tmp_path / "served_probe.py"
    source = """
import subprocess, sys
sys.path.insert(0, {repo!r})
from tests.served_browser import launch_chromium
manager, browser = launch_chromium()
# While the browser is open, find its live process: the chromium-family binary from the
# playwright cache carrying a fresh --user-data-dir.
out = subprocess.run(["/bin/ps", "ax", "-o", "command="], capture_output=True, text=True)
hits = [
    line.strip()
    for line in (out.stdout or "").splitlines()
    if "ms-playwright" in line and "--user-data-dir=" in line
]
print("LAUNCH_ARGV=" + (hits[0] if hits else ""))
browser.close()
manager.stop()
"""
    done = probe.run_driver(PY, source.format(repo=str(REPO)), driver, env, timeout=180)
    if "no chromium build available" in (done.stdout + done.stderr):
        pytest.skip("no bundled chromium for the served lane on this machine")
    assert done.returncode == 0, f"served bootstrap failed: {done.stdout} {done.stderr}"
    line = next((l for l in done.stdout.splitlines() if l.startswith("LAUNCH_ARGV=")), "")
    argv = line.removeprefix("LAUNCH_ARGV=")
    assert "ms-playwright" in argv, f"bootstrap must use the bundled chromium: {argv}"
    assert "--user-data-dir=" in argv, f"bootstrap must launch a fresh isolated profile: {argv}"
    profile = next(p for p in argv.split() if p.startswith("--user-data-dir=")).split("=", 1)[1]
    assert "Application Support/Google/Chrome" not in profile, "never the signed-in profile"
    assert probe.credential_calls(log) == []


# --------------------------------------------------------------------------------------
# 5. Launcher E2E: startup, restart, parallel shards, updater — zero credential calls
# --------------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _boot_api_server(home: Path, log: Path, spy_shim: Path, *, extra_env: dict[str, str]) -> subprocess.Popen:
    bindir = probe.fake_bin_dir(home.parent, python=PY, log=log)
    env = _minimal_env(bindir, log, home)
    env.update(
        {
            "VOOL_HOME": str(home),
            "PYTHONPATH": os.pathsep.join([str(spy_shim), str(REPO)]),
            "VOOL_KEYRING_SPY_LOG": str(log.with_suffix(".spykeyring.jsonl")),
        }
    )
    env.update(extra_env)
    return subprocess.Popen(
        [PY, "-m", "apps.vool_api_server", "--port", str(_free_port()), "--bind", "127.0.0.1"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _wait_health(proc: subprocess.Popen[bytes], port: int, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            raise AssertionError(f"server exited early rc={proc.returncode}: {out[-500:]!r} {err[-500:]!r}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(f"server did not become healthy within {timeout}s: last error {last_error}")


def _stop_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), 9)
        proc.wait(timeout=10)


def _proc_port(proc: subprocess.Popen) -> int:
    argv = list(proc.args)
    return int(argv[argv.index("--port") + 1])


def test_clean_fresh_home_boot_then_restart_makes_zero_credential_calls(tmp_path: Path) -> None:
    log = tmp_path / "fakes.jsonl"
    spy_log = log.with_suffix(".spykeyring.jsonl")
    shim = _spy_keyring_shim(tmp_path, log)
    home = tmp_path / "fresh-home"
    home.mkdir()

    # Boot 1 — clean fresh home, no grant anywhere.
    proc = _boot_api_server(home, log, shim, extra_env={})
    try:
        _wait_health(proc, _proc_port(proc))
    finally:
        _stop_proc(proc)
    assert probe.credential_calls(log) == [], (
        f"fresh-home boot issued credential CLI calls: {probe.credential_calls(log)[:3]}"
    )
    assert not spy_log.exists() or spy_log.read_text(encoding="utf-8").strip() == "", (
        "fresh-home boot issued keyring backend calls"
    )

    # Boot 2 — RESTART against the same home. Still zero credential API calls.
    proc2 = _boot_api_server(home, log, shim, extra_env={})
    try:
        _wait_health(proc2, _proc_port(proc2))
    finally:
        _stop_proc(proc2)
    assert probe.credential_calls(log) == [], "restart issued credential CLI calls"
    assert not spy_log.exists() or spy_log.read_text(encoding="utf-8").strip() == "", (
        "restart issued keyring backend calls"
    )

    # The preflight recorded the boots' provenance under the home.
    reports = list((home / "data" / "preflight").glob("*.json"))
    assert reports, "both boots must record preflight provenance"


def test_unattended_boot_with_hostile_inherited_grant_makes_zero_credential_calls(
    tmp_path: Path,
) -> None:
    """The exact C15 spam scenario: an operator shell exported VOOL_KEYCHAIN_ALLOWED=1 and an
    unattended launcher (VOOL_UNATTENDED=1, as spawn_detached_update exports) boots a fresh
    home. Only the preflight's scratch pins stand between that grant and a Keychain dialog."""
    log = tmp_path / "fakes-unattended.jsonl"
    spy_log = log.with_suffix(".spykeyring.jsonl")
    shim = _spy_keyring_shim(tmp_path, log)
    home = tmp_path / "unattended-home"
    home.mkdir()

    proc = _boot_api_server(home, log, shim, extra_env={"VOOL_UNATTENDED": "1", "VOOL_KEYCHAIN_ALLOWED": "1"})
    try:
        _wait_health(proc, _proc_port(proc))
    finally:
        _stop_proc(proc)
    assert probe.credential_calls(log) == [], (
        f"unattended boot with hostile grant issued credential CLI calls: {probe.credential_calls(log)[:3]}"
    )
    assert not spy_log.exists() or spy_log.read_text(encoding="utf-8").strip() == "", (
        "unattended boot with hostile grant issued keyring backend calls"
    )


def test_parallel_test_shard_children_make_zero_credential_calls(tmp_path: Path) -> None:
    """Two parallel pytest children with the exact env ops/pytest_shards.py exports."""
    # The shard runner exports the scratch marker its children's preflight keys on.
    shards_source = (REPO / "ops" / "pytest_shards.py").read_text(encoding="utf-8")
    assert 'env["VOOL_TEST_MODE"] = "1"' in shards_source, (
        "ops/pytest_shards.py must export VOOL_TEST_MODE=1 so shard children preflight as scratch"
    )

    log = tmp_path / "fakes.jsonl"
    shim = _spy_keyring_shim(tmp_path, log)
    # A real test file under tests/, so each child boots through the SAME conftest +
    # preflight path a genuine shard child gets (a probe outside the repo loads no
    # conftest and would prove nothing about the harness).
    shard_test = REPO / "tests" / "test_c15_shard_probe.py"
    assert shard_test.exists()

    procs = []
    for index in range(2):
        home = tmp_path / f"shard-home-{index}"
        home.mkdir()
        env = _scratch_env(tmp_path, log, home)
        env["PYTHONPATH"] = os.pathsep.join([str(shim), str(REPO)])
        env["VOOL_KEYRING_SPY_LOG"] = str(log.with_suffix(".spykeyring.jsonl"))
        env["VOOL_TEST_SHARD"] = str(index)
        procs.append(
            subprocess.Popen(
                [PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", str(shard_test)],
                cwd=str(REPO),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        assert proc.returncode == 0, f"shard child failed: {out[-800:]} {err[-800:]}"
    assert probe.credential_calls(log) == [], (
        f"parallel shard children issued credential CLI calls: {probe.credential_calls(log)[:3]}"
    )
    spy = log.with_suffix(".spykeyring.jsonl")
    assert not spy.exists() or spy.read_text(encoding="utf-8").strip() == "", (
        "parallel shard children issued keyring backend calls"
    )


def test_updater_run_makes_zero_credential_calls(tmp_path: Path) -> None:
    """`installer.update_cli status` — the offline updater surface — stays credential-clean."""
    log = tmp_path / "fakes.jsonl"
    shim = _spy_keyring_shim(tmp_path, log)
    home = tmp_path / "updater-home"
    home.mkdir()
    env = _scratch_env(tmp_path, log, home, hostile_grant=True)
    env["PYTHONPATH"] = os.pathsep.join([str(shim), str(REPO)])
    env["VOOL_KEYRING_SPY_LOG"] = str(log.with_suffix(".spykeyring.jsonl"))
    env.pop("VOOL_TEST_MODE", None)
    env["VOOL_UNATTENDED"] = "1"  # what spawn_detached_update exports to the updater child
    done = subprocess.run(
        [PY, "-m", "installer.update_cli", "status", "--data-dir", str(home)],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, f"updater status failed: {done.stdout} {done.stderr}"
    assert probe.credential_calls(log) == [], (
        f"updater run issued credential CLI calls: {probe.credential_calls(log)[:3]}"
    )
    spy = log.with_suffix(".spykeyring.jsonl")
    assert not spy.exists() or spy.read_text(encoding="utf-8").strip() == "", (
        "updater run issued keyring backend calls"
    )


# --------------------------------------------------------------------------------------
# 6. Placement guard: the conftest preflight precedes every runtime import
# --------------------------------------------------------------------------------------


def test_conftest_preflight_call_precedes_all_runtime_imports() -> None:
    source = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    preflight_at = source.index('_unattended_preflight("pytest-conftest")')
    for import_line in (
        "from apps.vool_agent import VoolAgent",
        "from core import local_ollama_inventory",
        "from storage.db import active_default_db_path",
        "from tests import _network_seal",
    ):
        found_at = source.index(import_line)
        assert found_at > preflight_at, (
            f"conftest preflight placement is load-bearing: it must run before {import_line!r}"
        )
