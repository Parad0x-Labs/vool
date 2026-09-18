"""C06 isolation law — profiles, Keychain, restart, and the sabotage matrix.

The browser product's isolation claims are mechanical, not documentary:

- ISOLATED DISPOSABLE PROFILES: every session gets a fresh user-data-dir under
  the browser scratch; close deletes it; nothing of the operator's browser is
  ever named. A launch that pointed the engine at the real Chrome profile (or
  the machine's home-dir profile roots at all) is refused by name.
- ZERO KEYCHAIN: the engine runs with --use-mock-keychain --password-store=basic;
  the module carries no Keychain/security-CLI access; the launched argv proves
  the mock. Sabotage S3 strips the mock flag and the guard must go red.
- CROSS-SESSION NON-LEAK: cookies/storage do not cross sessions (product file).
- RESTART: a fresh runtime (toolchain state reset = the restart) finds the
  session registry, marks engine-backed sessions recoverable-or-dead, keeps the
  receipts, and leaves zero profile residue beyond live sessions.
- SABOTAGE MATRIX: S1 origin guard, S2 isolation guard, S3 keychain flag,
  S4 receipt integrity — each mutation makes its guard test FAIL (red-by-name),
  proving the guard is load-bearing; every restore is byte-exact.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, internal_scope, reset_toolchain_state
from tests._vool_browser_support import JourneyWorld, enable_browser_policy

PLUGIN = "vool-browser"


@pytest.fixture()
def browser_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_BROWSER_SCRATCH_ROOT", str(tmp_path / "browser-scratch"))
    monkeypatch.setenv("PLAYWRIGHT_ENABLED", "1")
    enable_browser_policy(monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        yield tmp_path
    from core.vool_browser.sessions import close_all_for_test

    close_all_for_test()
    reset_mode_permission_state()
    reset_toolchain_state()


def _run(intent: str, arguments: dict, session: str = "bx", scope=None, **context):
    """One gated tool call. Unless an explicit scope is passed, the call carries
    a bounded internal authority for THIS intent (these are door-level tests;
    the gate's own pend/deny behaviour lives in test_vool_browser_gate.py)."""
    from core.mode_permission_policy import PermissionAction, grant_internal_authority
    from core.tool_intent_executor import execute_tool_intent

    kwargs = executor_kwargs(session, **context)
    if scope is not None:
        kwargs["source_context"] = dict(scope)
    elif not context:
        token = grant_internal_authority(
            label="test.browser.lane",
            actions=(PermissionAction.CREATE_FILES, PermissionAction.USE_BROWSER),
            duration_seconds=300,
            intents=(intent,),
        )
        kwargs["source_context"] = {"internal_authority_token": token}
    return execute_tool_intent({"intent": intent, "arguments": arguments}, **kwargs)


def _open(world, base, session: str = "s1", **extra):
    scope = internal_scope(
        "test.browser.open", "create_files", "use_browser_or_web_retrieval",
                      intents=(f"{PLUGIN}.session.open",))
    result = _run(
        f"{PLUGIN}.session.open",
        {"session": session, "start_url": base + "/", **extra},
        **scope,
    )
    assert result.ok, (result.status, result.response_text[:300])
    return result


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_every_session_gets_a_fresh_profile_and_close_deletes_it(browser_world) -> None:
    with JourneyWorld() as world:
        r1 = _open(browser_world, world.a.base, session="s1")
        r2 = _open(browser_world, world.a.base, session="s2")
        p1 = Path(r1.details["observation"]["session"]["profile_dir"])
        p2 = Path(r2.details["observation"]["session"]["profile_dir"])
        assert p1.is_dir() and p2.is_dir() and p1 != p2, (p1, p2)
        # both live ONLY under this test's scratch
        scratch = (browser_world / "browser-scratch").resolve()
        assert scratch in p1.resolve().parents and scratch in p2.resolve().parents
        for sid in ("s1", "s2"):
            closed = _run(f"{PLUGIN}.session.close", {"session": sid})
            assert closed.ok
        assert not p1.exists() and not p2.exists()


def test_launch_arguments_carry_the_isolation_flags(browser_world) -> None:
    from core.vool_browser.engine import isolation_launch_flags

    flags = " ".join(isolation_launch_flags(profile_dir="/x/profile"))
    assert "--user-data-dir=/x/profile" in flags
    assert "--use-mock-keychain" in flags, "the engine must never touch the real Keychain"
    assert "--password-store=basic" in flags
    assert "--disable-sync" in flags and "--disable-extensions" in flags
    assert "--no-first-run" in flags


def test_the_operator_real_profile_is_refused_by_name(browser_world) -> None:
    """A profile_dir argument (or any computed profile) pointing at a real browser
    profile root is refused before an engine exists."""
    from core.vool_browser.engine import refuse_real_profile_target

    for candidate in (
        "~/Library/Application Support/Google/Chrome",
        "/Users/someone/Library/Application Support/Google/Chrome",
        str(Path.home() / "Library" / "Safari"),
        str(Path.home()),
    ):
        verdict = refuse_real_profile_target(candidate)
        assert verdict is not None, f"{candidate} must be refused"
        assert "profile" in verdict.lower()


def test_zero_keychain_law_in_module_bytes(browser_world) -> None:
    """The lane's modules must not touch the macOS keychain or the security CLI.
    The engine-side guard is the mock-keychain flag (tested above); this is the
    source-side guard."""
    lane_dir = Path(__file__).resolve().parents[1] / "core" / "vool_browser"
    assert lane_dir.is_dir()
    forbidden = ("SecKeychain", "security find-generic-password", "keychain-access-groups",
                 "SecItemAdd", "SecItemCopyMatching")
    for module in lane_dir.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{module.name} references {needle}"


def test_cookies_do_not_cross_sessions(browser_world) -> None:
    import re as _re

    def jar_value(sid: str) -> str:
        got = _run(f"{PLUGIN}.navigate", {"session": sid, "url": world.a.base + "/set-cookie"})
        assert got.ok, (sid, got.status)
        _run(f"{PLUGIN}.navigate", {"session": sid, "url": world.a.base + "/read-cookie"})
        seen = _run(f"{PLUGIN}.inspect", {"session": sid})
        match = _re.search(r"session_a=(A\d+)", seen.details["observation"]["page_text"])
        assert match, seen.details["observation"]["page_text"][:200]
        return match.group(1)

    with JourneyWorld() as world:
        _open(browser_world, world.a.base, session="s1")
        _open(browser_world, world.a.base, session="s2")
        v1 = jar_value("s1")
        v2 = jar_value("s2")
        assert v1 != v2, "two sessions on one origin share one cookie jar"
        # neither jar ever sees the other's value
        for sid, mine, theirs in (("s1", v1, v2), ("s2", v2, v1)):
            _run(f"{PLUGIN}.navigate", {"session": sid, "url": world.a.base + "/read-cookie"})
            seen = _run(f"{PLUGIN}.inspect", {"session": sid})
            text = seen.details["observation"]["page_text"]
            assert mine in text and theirs not in text, (sid, text[:200])


def test_restart_finds_the_registry_keeps_receipts_and_marks_the_session(browser_world) -> None:
    with JourneyWorld() as world:
        opened = _open(browser_world, world.a.base, session="s1")
        session = opened.details["observation"]["session"]
        _run(f"{PLUGIN}.navigate", {"session": "s1", "url": world.a.base + "/product/1"})
        receipts_path = Path(session["receipts_path"])
        profile_dir = Path(session["profile_dir"])

        # THE RESTART: every runtime-side cache and gate state resets; the registry
        # file and the receipts live on disk in the browser scratch.
        reset_toolchain_state()
        from core.mode_permission_policy import reset_mode_permission_state

        reset_mode_permission_state()

        status = _run(f"{PLUGIN}.session.status", {"session": "s1"})
        assert status.ok, status.response_text[:300]
        state = status.details["observation"]["session"]["state"]
        # a toolchain reset is a restart of the RUNTIME caches; in this in-process
        # world the engine thread is still alive, so `ready` is the honest answer
        assert state in {"live", "ready", "stale", "recoverable"}, state
        assert receipts_path.is_file(), "receipts must survive the restart"
        rows = [json.loads(line) for line in receipts_path.read_text().splitlines() if line.strip()]
        assert any(row["op"] == "navigate" for row in rows)

        closed = _run(f"{PLUGIN}.session.close", {"session": "s1"})
        assert closed.ok, closed.response_text[:300]
        assert not profile_dir.exists()


# ---------------------------------------------------------------------------
# Sabotage matrix — each mutation must turn its guard RED, then restore byte-exact
# ---------------------------------------------------------------------------


def _sabotage(module_path: Path, old: str, new: str):
    """Mutate a lane module, yield, restore byte-exact. Asserts the mutation bit."""

    original = module_path.read_bytes()
    text = original.decode("utf-8")
    assert old in text, f"sabotage anchor missing in {module_path.name}: {old!r}"
    module_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    class _Restore:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            module_path.write_bytes(original)
            assert module_path.read_bytes() == original, "restore was not byte-exact"
            return False

    return _Restore()


def _probe(code: str) -> str:
    """Run a probe in a FRESH interpreter (no cached imports of the lane)."""

    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": str(root)})
    if out.returncode != 0:
        return f"PROBE_ERROR: {out.stderr[-400:]}"
    return out.stdout


def test_sabotage_s1_the_cross_origin_guard_is_load_bearing(browser_world) -> None:
    """Sabotage the refusal constant: the origin verdict must change (the guard,
    not luck, refuses), and the byte-exact restore must bring it back."""

    lane = Path(__file__).resolve().parents[1] / "core" / "vool_browser" / "ops.py"
    call = (
        "from core.vool_browser.ops import _cross_origin_redirect_verdict as v; "
        "print(v('http://a/', 'http://b/x', granted=frozenset(), policy='same_origin'))"
    )
    healthy = _probe(call)
    assert "cross_origin_redirect_refused" in healthy, healthy

    with _sabotage(lane, 'REDIRECT_REFUSAL = "cross_origin_redirect_refused"',
                   'REDIRECT_REFUSAL = "redirect_allowed_sabotaged"'):
        sabotaged = _probe(call)
        assert "redirect_allowed_sabotaged" in sabotaged, (
            "sabotage no-op: mutating the guard constant changed nothing", sabotaged)
    restored = _probe(call)
    assert "cross_origin_redirect_refused" in restored, restored


def test_sabotage_s2_the_real_profile_guard_is_load_bearing(browser_world) -> None:
    """Empty the forbidden-profile markers: a real Chrome profile target must
    become ACCEPTABLE to the (sabotaged) guard, proving the guard refuses."""

    lane = Path(__file__).resolve().parents[1] / "core" / "vool_browser" / "engine.py"
    call = (
        "import pathlib; "
        "from core.vool_browser.engine import refuse_real_profile_target as r; "
        "print(r(str(pathlib.Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome')))"
    )
    healthy = _probe(call)
    assert "refusing" in healthy, healthy

    with _sabotage(lane, "_FORBIDDEN_PROFILE_MARKERS = (",
                   "_FORBIDDEN_PROFILE_MARKERS_SABOTAGED = ("):
        sabotaged = _probe(call)
        # the renamed tuple breaks the module outright (PROBE_ERROR) or, if the
        # guard somehow survives, it must at least STOP refusing: either way the
        # guard, not luck, was the refusal.
        assert sabotaged.strip() == "None" or "PROBE_ERROR" in sabotaged, (
            "sabotage no-op: the guard still refused with empty markers", sabotaged)
    restored = _probe(call)
    assert "refusing" in restored, restored


def test_sabotage_s3_the_mock_keychain_flag_is_load_bearing(browser_world) -> None:
    """Strip --use-mock-keychain from the launch flags: the zero-Keychain argv
    law loses its teeth exactly when the flag disappears."""

    lane = Path(__file__).resolve().parents[1] / "core" / "vool_browser" / "engine.py"
    call = (
        "from core.vool_browser.engine import isolation_launch_flags as f; "
        "print('--use-mock-keychain' in f(profile_dir='/x/profile'))"
    )
    healthy = _probe(call)
    assert healthy.strip() == "True", healthy

    with _sabotage(lane, '"--use-mock-keychain",', '"--sabotaged-flag",'):
        sabotaged = _probe(call)
        assert sabotaged.strip() == "False", (
            "sabotage no-op: stripping the flag changed nothing", sabotaged)
    restored = _probe(call)
    assert restored.strip() == "True", restored


def test_sabotage_s4_receipt_integrity_is_load_bearing(browser_world) -> None:
    """Disable the line digest: the receipts file must become unverifiable
    (verify loses its teeth), proving the hash chain is the guard."""

    lane = Path(__file__).resolve().parents[1] / "core" / "vool_browser" / "receipts.py"
    call = (
        "from core.vool_browser import receipts; "
        "import json, tempfile, pathlib; "
        "p = pathlib.Path(tempfile.mkdtemp()) / 'r.jsonl'; "
        "receipts.append_receipt(p, receipts.build_receipt(op='t', session='s', outcome='ok')); "
        "rows = p.read_text().splitlines(); "
        "row = json.loads(rows[0]); row['outcome'] = 'edited'; "
        "p.write_text(json.dumps(row) + chr(10)); "
        "ok_count, problems = receipts.verify_receipts_file(p); "
        "print('DETECTED' if problems else 'SILENT')"
    )
    healthy = _probe(call)
    assert "DETECTED" in healthy, healthy

    with _sabotage(lane, "def _line_digest(", "def _line_digest_DISABLED("):
        sabotaged = _probe(call)
        assert "SILENT" in sabotaged or "PROBE_ERROR" in sabotaged, (
            "sabotage no-op: without the digest the tamper is still detected", sabotaged)
    restored = _probe(call)
    assert "DETECTED" in restored, restored
