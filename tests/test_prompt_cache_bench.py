"""The prompt-cache bench reads VOOL's saved OpenRouter key the way the installed app does, read-only.

The bench runs on the operator's Mac from a source clone. A clone's own default profile is empty,
so the key must come from the installed app's profile; the read must never write to that profile,
and a missing key, a missing profile or an unanswered Keychain question must stop it in words.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ops.prompt_cache_bench import bench

REPO = Path(__file__).resolve().parent.parent


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*"))}


def _save_key_like_vool(home: Path, name: str, value: str) -> None:
    """Save a credential through VOOL's own store in a fresh process, as the app does."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from core.runtime_paths import ensure_runtime_dirs; ensure_runtime_dirs();"
        "from core import credential_store; credential_store.store_credential(sys.argv[2], sys.argv[3])"
    )
    env = {**os.environ, "VOOL_HOME": str(home), "VOOL_CREDENTIAL_STORE": "vault"}
    subprocess.run([sys.executable, "-c", code, str(REPO), name, value], env=env, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _no_shell_profile(monkeypatch):
    monkeypatch.delenv("VOOL_HOME", raising=False)
    monkeypatch.delenv("NULLA_HOME", raising=False)
    bench._KEY_CACHE.clear()
    yield
    bench._KEY_CACHE.clear()


def test_the_installed_mac_profile_is_the_one_the_app_uses(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "Library" / "Application Support" / "VOOL" / "runtime"
    (profile / "data").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert bench.installed_profile_home() == profile.resolve()


def test_the_saved_key_is_read_from_the_profile_without_changing_it(tmp_path) -> None:
    profile = tmp_path / "Application Support" / "VOOL" / "runtime"  # a space, as on macOS
    _save_key_like_vool(profile, bench.KEY_NAME, "sk-or-test-roundtrip")
    before = _tree(profile)
    assert bench.resolve_key("vool-store", str(profile)) == "sk-or-test-roundtrip"
    assert _tree(profile) == before


def test_a_profile_without_saved_keys_is_refused_before_anything_is_opened(tmp_path) -> None:
    profile = tmp_path / "runtime"
    (profile / "data").mkdir(parents=True)
    with pytest.raises(bench.HarnessError, match="no saved keys"):
        bench.resolve_key("vool-store", str(profile))
    assert _tree(profile) == {"data": _tree(tmp_path)["runtime/data"]}


def test_a_profile_without_the_openrouter_key_says_so(tmp_path) -> None:
    profile = tmp_path / "runtime"
    _save_key_like_vool(profile, "llm.cloud.someone_else", "sk-other")
    with pytest.raises(bench.HarnessError, match="no saved OpenRouter key"):
        bench.resolve_key("vool-store", str(profile))


def test_no_profile_and_a_shell_vool_home_are_refused(tmp_path, monkeypatch) -> None:
    with pytest.raises(bench.HarnessError, match="no installed VOOL profile"):
        bench.resolve_key("vool-store", str(tmp_path / "missing"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    with pytest.raises(bench.HarnessError, match="VOOL_HOME is set"):
        bench.resolve_key("vool-store", str(tmp_path))


class _Keychain:
    def __init__(self, values: dict[tuple[str, str], str], *, block: threading.Event | None = None) -> None:
        self.values, self.block, self.writes = values, block, []

    def get_password(self, service: str, name: str) -> str | None:
        if self.block is not None:
            self.block.wait(5)
        return self.values.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.writes.append(name)


def _keychain_profile(tmp_path: Path, monkeypatch, keychain: _Keychain) -> Path:
    from core import bounded_keyring, credential_store
    from core.runtime_paths import ensure_runtime_dirs

    profile = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(profile))
    ensure_runtime_dirs()  # the folders the installed app made when it first started
    (profile / "data" / "credentials.meta.json").write_text('{"' + bench.KEY_NAME + '": {"label": ""}}', encoding="utf-8")
    monkeypatch.setenv("VOOL_HOME", str(profile))
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "0")  # what the parent sets for the child
    monkeypatch.delenv("VOOL_CREDENTIAL_STORE", raising=False)  # the child runs VOOL's default (auto)
    monkeypatch.setattr(credential_store, "_load_keyring", lambda: keychain)
    monkeypatch.setattr(bounded_keyring, "_KEYCHAIN_BLOCKED", False)
    monkeypatch.setattr(bounded_keyring, "DEFAULT_TIMEOUT_S", bounded_keyring.DEFAULT_TIMEOUT_S)
    return profile


def test_a_keychain_key_is_read_and_nothing_is_written(tmp_path, monkeypatch, capsys) -> None:
    keychain = _Keychain({("nulla-credentials", bench.KEY_NAME): "sk-or-from-keychain"})
    profile = _keychain_profile(tmp_path, monkeypatch, keychain)
    before = _tree(profile)
    assert bench._child_read_key(bench.KEY_NAME, 5.0, "keychain") == 0
    assert capsys.readouterr().out == "sk-or-from-keychain"
    assert keychain.writes == [] and _tree(profile) == before


def test_an_unanswered_keychain_question_is_an_error_not_a_missing_key(tmp_path, monkeypatch, capsys) -> None:
    release = threading.Event()
    keychain = _Keychain({("nulla-credentials", bench.KEY_NAME): "sk-or-late"}, block=release)
    _keychain_profile(tmp_path, monkeypatch, keychain)
    try:
        assert bench._child_read_key(bench.KEY_NAME, 0.2, "keychain") == 4
    finally:
        release.set()
    captured = capsys.readouterr()
    assert captured.out == "" and "Keychain" in captured.err


def test_claude_code_goes_through_openrouter_with_every_model_slot_pinned(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-shell")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    env = bench._cli_env("openrouter", "sk-or-test", "anthropic/claude-sonnet-4.5")
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test"
    assert env["ANTHROPIC_API_KEY"] == ""  # empty, not unset: unset falls back to Anthropic
    for slot in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        assert env[slot] == "anthropic/claude-sonnet-4.5"
    assert "opus" not in env["ANTHROPIC_SMALL_FAST_MODEL"] and "opus" not in env["ANTHROPIC_DEFAULT_HAIKU_MODEL"]


def test_a_keychain_profile_with_no_keychain_reader_is_named_not_read_as_no_key(tmp_path, monkeypatch, capsys) -> None:
    from core import credential_store

    _keychain_profile(tmp_path, monkeypatch, _Keychain({}))
    monkeypatch.setattr(credential_store, "_load_keyring", lambda: None)
    assert bench._child_read_key(bench.KEY_NAME, 5.0, "keychain") == 5
    assert "cannot open the macOS Keychain" in capsys.readouterr().err


def test_the_claude_code_arm_must_answer_on_the_pinned_model() -> None:
    pinned = "anthropic/claude-sonnet-4.5"
    assert bench._wrong_cli_model({"modelUsage": {"anthropic/claude-sonnet-4.5": {}, "anthropic/claude-haiku-4.5": {}}}, pinned) == ""
    assert bench._wrong_cli_model({"modelUsage": {"anthropic/claude-4.5-sonnet-20250929": {}}}, pinned) == ""
    assert "did not answer on" in bench._wrong_cli_model({"modelUsage": {"anthropic/claude-sonnet-4": {}}}, pinned)
    assert "did not answer on" in bench._wrong_cli_model({"modelUsage": {"claude-opus-5-5": {}}}, pinned)
    assert "opus" in bench._wrong_cli_model({"modelUsage": {"claude-sonnet-4-5": {}, "claude-opus-5-5": {}}}, pinned)


def test_a_call_with_no_reported_cost_is_held_not_counted_as_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bench, "key_usage_usd", lambda key: None)  # the key meter is unreachable
    ledger = bench.SpendLedger(tmp_path, 1.0)
    ledger.record(billed=None, notional=None, unknown=True)
    assert ledger.counted_usd >= bench.INITIAL_TURN_RESERVE_USD
    resumed = bench.SpendLedger(tmp_path, 1.0)  # a crash and resume keeps the hold
    assert resumed.counted_usd == pytest.approx(ledger.counted_usd)
    resumed.check("the second turn")  # $0.40 held + $0.40 reserve fits under $1
    resumed.record(billed=None, notional=None, unknown=True)
    with pytest.raises(bench.CapReached):
        resumed.check("the third turn")


def test_spend_no_event_reported_still_stops_the_run_through_the_key_meter(tmp_path, monkeypatch) -> None:
    usage = {"now": 10.0}
    monkeypatch.setattr(bench, "key_usage_usd", lambda key: usage["now"])
    ledger = bench.SpendLedger(tmp_path, 2.0)
    ledger.attach_key("sk-or-test")
    ledger.check("the first turn")
    usage["now"] = 11.8  # a timed-out turn spent $1.80 that no event reported
    resumed = bench.SpendLedger(tmp_path, 2.0)
    resumed.attach_key("sk-or-test")  # resuming keeps the run's starting meter
    with pytest.raises(bench.CapReached, match=r"counted \$1\.8000"):
        resumed.check("the next turn")
    assert "sk-or-test" not in (tmp_path / "spend.json").read_text(encoding="utf-8")


def test_the_report_counts_a_no_call_turn_as_failed_and_keeps_partial_cache_figures(tmp_path) -> None:
    import json

    def turn(n: int, cached: int | None, answer: str = "ok") -> dict:
        return {"kind": "turn", "label": "vool-x", "arm": "vool", "model": "m", "task": "t", "repeat": 0, "turn": n,
                "attempt": "a1", "answer": answer, "wall_seconds": 1.0, "prompt_tokens": 1000, "output_tokens": 10,
                "usd": 0.01, "calls": 1, "cached_tokens": cached, "cache_write_tokens": None,
                "cache_unreported_calls": 0 if cached is not None else 1}

    rows = [
        turn(0, None),  # a call that reported no cache figure: unknown, never zero
        turn(1, 600),
        turn(2, None, "No. Web lookups are off."),
        {"kind": "no_model_call", "task": "t", "repeat": 0, "turn": 2, "attempt": "a1", "answer": "No. Web lookups are off."},
        {"kind": "chat_complete", "task": "t", "repeat": 0, "attempt": "a1"},
    ]
    (tmp_path / "vool-x").mkdir()
    (tmp_path / "vool-x" / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    row = bench.summarize(tmp_path)["labels"]["vool-x"]
    assert row["failed_turns"] == 1
    assert row["cached_tokens"] == 600 and row["cached_share"] == pytest.approx(0.6)
    assert row["cache_unreported_calls"] == 2
