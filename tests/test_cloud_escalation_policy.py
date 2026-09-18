"""BYOK cloud-escalation policy: decision matrix + daily-cap persistence.

Local-first is the default (mode 'off'); cloud burst is strictly opt-in. auto mode
escalates only up to a daily call cap, then falls back to ask (or local). These tests
pin the decision for each mode and that the daily counter resets across UTC days and
fails safe (to local-only) on a broken store.
"""
from __future__ import annotations

import pytest

import core.cloud_escalation_policy as cep


@pytest.fixture(autouse=True)
def _no_real_os_prompt(monkeypatch):
    # Safety: the ask-mode gate calls the real OS consent gate. Default-deny it here so no test
    # in this file can pop a live OS dialog; tests that need a grant override _TEST_OVERRIDE.
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: False)
from core.cloud_escalation_policy import (
    ACTION_ASK,
    ACTION_CLOUD,
    ACTION_LOCAL,
    CloudEscalationPolicy,
    decide_escalation,
)

# Two timestamps that fall on different UTC calendar days.
DAY1 = 1700000000.0  # 2023-11-14
DAY2 = 1700086400.0  # 2023-11-15


# ── decision matrix (pure) ───────────────────────────────────────────────────

def test_off_stays_local():
    d = decide_escalation(CloudEscalationPolicy(mode="off"), used_today=0)
    assert d.action == ACTION_LOCAL


def test_ask_requires_permission():
    d = decide_escalation(CloudEscalationPolicy(mode="ask"), used_today=0)
    assert d.action == ACTION_ASK


def test_auto_under_cap_goes_cloud():
    d = decide_escalation(CloudEscalationPolicy(mode="auto", daily_cap=5), used_today=3)
    assert d.action == ACTION_CLOUD
    assert "monetary budget" in d.reason


def test_auto_ignores_legacy_count_at_limit():
    d = decide_escalation(CloudEscalationPolicy(mode="auto", daily_cap=5), used_today=5)
    assert d.action == ACTION_CLOUD


def test_auto_ignores_legacy_count_fallback_setting():
    p = CloudEscalationPolicy(mode="auto", daily_cap=5, on_cap_reached="local")
    d = decide_escalation(p, used_today=5)
    assert d.action == ACTION_CLOUD


def test_auto_ignores_legacy_zero_call_cap():
    d = decide_escalation(CloudEscalationPolicy(mode="auto", daily_cap=0), used_today=0)
    assert d.action == ACTION_CLOUD


def test_normalized_fails_safe():
    n = CloudEscalationPolicy(mode="bogus", daily_cap=-3, on_cap_reached="weird").normalized()
    assert n.mode == "off"            # unknown mode -> local-only
    assert n.daily_cap == 0           # negative clamped
    assert n.on_cap_reached == "ask"  # unknown -> ask


# ── persistence + daily reset (isolated store) ───────────────────────────────

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(cep, "_store_path", lambda: tmp_path / "cloud_escalation.json")


def test_default_load_is_off(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    policy = cep.load_policy()
    assert policy.mode == "off"
    assert policy.free_cloud_enabled is False
    assert policy.auto_free_model == "auto"


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cep.save_policy(
        CloudEscalationPolicy(
            mode="auto",
            daily_cap=7,
            on_cap_reached="local",
            free_cloud_enabled=True,
            auto_free_model="novel/coder:free",
        )
    )
    got = cep.load_policy()
    assert (got.mode, got.daily_cap, got.on_cap_reached) == ("auto", 7, "local")
    assert got.free_cloud_enabled is True
    assert got.auto_free_model == "novel/coder:free"


def test_record_and_used_today(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert cep.used_today(now=DAY1) == 0
    assert cep.record_escalation(now=DAY1) == 1
    assert cep.record_escalation(now=DAY1) == 2
    assert cep.used_today(now=DAY1) == 2


def test_counter_resets_next_day(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cep.record_escalation(now=DAY1)
    cep.record_escalation(now=DAY1)
    assert cep.used_today(now=DAY1) == 2
    # A new UTC day starts the count fresh.
    assert cep.used_today(now=DAY2) == 0
    assert cep.record_escalation(now=DAY2) == 1


def test_set_mode_preserves_cap(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    cep.save_policy(CloudEscalationPolicy(mode="off", daily_cap=9, on_cap_reached="local"))
    p = cep.set_mode("auto")
    assert p.mode == "auto"
    assert p.daily_cap == 9            # cap preserved across a mode change
    assert p.on_cap_reached == "local"


def test_broken_store_fails_safe_to_local(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "cloud_escalation.json").write_text("{ not json", encoding="utf-8")
    assert cep.load_policy().mode == "off"   # unreadable -> local-only, never cloud
    assert cep.used_today(now=DAY1) == 0


def test_is_configured_flips_after_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert cep.is_configured() is False          # fresh: feature dormant
    cep.save_policy(CloudEscalationPolicy(mode="auto"))
    assert cep.is_configured() is True            # opted in


# ── router integration: the paid-fallback gate ───────────────────────────────

def _gate(**over):
    from core.memory_first_router import _gate_paid_by_cloud_escalation

    kw = dict(resolved_allow_paid=True, allow_paid_fallback=True,
              requested_paid_cloud=False, source_context=None)
    kw.update(over)
    return _gate_paid_by_cloud_escalation(**kw)


def _configured(monkeypatch, policy, used=0):
    """Simulate a user who has opted in with `policy` and used `used` bursts today."""
    monkeypatch.setattr(cep, "is_configured", lambda: True)
    monkeypatch.setattr(cep, "load_policy", lambda: policy)
    monkeypatch.setattr(cep, "used_today", lambda **_: used)


def test_gate_defaults_closed_when_unconfigured(monkeypatch):
    # No policy saved -> loads as off -> the auto paid path stays LOCAL (fail closed). Merely
    # holding a cloud key never authorizes an auto burst; the user must opt in with ask/auto.
    monkeypatch.setattr(cep, "load_policy", lambda: CloudEscalationPolicy(mode="off"))
    monkeypatch.setattr(cep, "used_today", lambda **_: 0)
    assert _gate() == (False, False)


def test_gate_off_blocks_auto_paid(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="off"))
    assert _gate() == (False, False)


def test_gate_auto_under_cap_allows_and_authorizes(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="auto", daily_cap=5), used=2)
    assert _gate() == (True, True)


def test_gate_auto_ignores_legacy_call_quota(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="auto", daily_cap=5), used=500)
    assert _gate() == (True, True)


def test_gate_ask_stays_local_when_os_consent_declined(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="ask"))
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: False)
    assert _gate(source_context={"_owner_local": True}) == (False, False)


def test_gate_ask_allows_with_os_consent_when_owner_local(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="ask"))
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: True)
    assert _gate(source_context={"_owner_local": True}) == (True, True)


def test_gate_ask_ignores_forged_body_approval(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="ask"))
    # A caller-supplied approval flag must NOT authorize a burst — only a real OS consent can.
    # With consent declined, the forged flag changes nothing: it stays local.
    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", lambda _r: False)
    assert _gate(source_context={"_owner_local": True, "cloud_escalation_approved": True}) == (False, False)


def test_gate_ask_never_prompts_a_non_owner(monkeypatch):
    _configured(monkeypatch, CloudEscalationPolicy(mode="ask"))

    def _must_not_prompt(_r):
        raise AssertionError("a non-owner request must never reach the OS consent prompt")

    monkeypatch.setattr("core.os_consent_gate._TEST_OVERRIDE", _must_not_prompt)
    assert _gate(source_context={"_owner_local": False}) == (False, False)


def test_gate_skips_explicit_paid_request():
    # An explicit paid-cloud request from the owner's own session is honored untouched and
    # not counted as an auto burst (default context is treated as in-process owner-local).
    assert _gate(requested_paid_cloud=True) == (True, False)


def test_gate_explicit_paid_honored_for_owner_local():
    assert _gate(requested_paid_cloud=True, source_context={"_owner_local": True}) == (True, False)


def test_gate_explicit_paid_gated_when_not_owner_local(monkeypatch):
    # A remote/non-owner caller cannot bypass the policy by naming a paid model: it falls
    # through to the policy, which is off by default -> local.
    monkeypatch.setattr(cep, "load_policy", lambda: CloudEscalationPolicy(mode="off"))
    monkeypatch.setattr(cep, "used_today", lambda **_: 0)
    assert _gate(requested_paid_cloud=True, source_context={"_owner_local": False}) == (False, False)


def test_gate_untouched_when_already_disallowed():
    # resolved_allow_paid already False (e.g. local_only_mode) -> gate does nothing.
    assert _gate(resolved_allow_paid=False) == (False, False)


# ── chat command: cloud off | ask | auto | cap N | status ────────────────────

def _cloud_cmd(text):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_command

    # The owner's own local session; mutation is allowed.
    return maybe_handle_cloud_command(text, owner_local=True)


def test_cloud_command_ignores_non_commands(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert _cloud_cmd("what is cloud computing?") is None
    assert _cloud_cmd("hello") is None


def test_cloud_command_sets_auto_and_opts_in(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    out = _cloud_cmd("cloud auto")
    assert out is not None and "AUTO" in out
    assert cep.load_policy().mode == "auto"
    assert cep.is_configured() is True   # setting via chat opts the user in


def test_cloud_command_cannot_reintroduce_call_quota(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _cloud_cmd("cloud auto")
    before = cep.load_policy()
    out = _cloud_cmd("cloud cap 7")
    assert "removed" in out
    assert cep.load_policy() == before


def test_cloud_command_off_and_status_and_slash(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _cloud_cmd("cloud off")
    assert cep.load_policy().mode == "off"
    assert "OFF" in _cloud_cmd("cloud")            # bare -> status
    assert _cloud_cmd("/cloud status") is not None  # slash form works


# ── adapter: BYOK key resolves env-first then the encrypted vault ─────────────

def test_adapter_resolves_key_env_then_vault(monkeypatch):
    from types import SimpleNamespace

    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    stub = SimpleNamespace(
        manifest=SimpleNamespace(runtime_config={"api_key_env": "MY_LLM_KEY", "credential_key": "llm.cloud.x"})
    )
    # Environment wins when present.
    monkeypatch.setenv("MY_LLM_KEY", "env-key")
    assert OpenAICompatibleAdapter._resolve_api_key(stub) == "env-key"
    # No env var -> fall back to the encrypted credential store (BYOK).
    monkeypatch.delenv("MY_LLM_KEY", raising=False)
    monkeypatch.setattr(
        "core.credential_store.get_credential",
        lambda name: "vault-key" if name == "llm.cloud.x" else None,
    )
    assert OpenAICompatibleAdapter._resolve_api_key(stub) == "vault-key"
    # Neither configured -> empty (no Authorization header added).
    bare = SimpleNamespace(manifest=SimpleNamespace(runtime_config={}))
    assert OpenAICompatibleAdapter._resolve_api_key(bare) == ""


# ── provider registration: BYOK OpenRouter burst lane ────────────────────────

class _FakeRegistry:
    def __init__(self):
        self.registered = []

    def get_manifest(self, provider, model):
        return None

    def register_manifest(self, manifest):
        self.registered.append(manifest)


def test_openrouter_provider_dormant_without_any_key(monkeypatch):
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reg = _FakeRegistry()
    assert _ensure_openrouter_byok_provider(reg, env={}) == ""   # not registered
    assert reg.registered == []


def _pin_runtime_home(monkeypatch, tmp_path):
    """Point VOOL_HOME and runtime_paths at a scratch dir for the duration of one test.

    Setting the env var alone leaves runtime_paths resolved to the real home, so persisted cloud
    policy leaks between tests and registration sees a model an earlier test chose. monkeypatch
    restores the module attribute at teardown, so the real home is put back automatically.
    """
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", tmp_path, raising=False)


def test_openrouter_provider_registers_from_env_key(monkeypatch, tmp_path):
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    # Pin the runtime home: registration reads the persisted cloud policy, so without this the
    # test inherits whatever model an earlier test left behind and the manifest count changes.
    _pin_runtime_home(monkeypatch, tmp_path)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reg = _FakeRegistry()
    pid = _ensure_openrouter_byok_provider(reg, env={"OPENROUTER_API_KEY": "sk-or-xxx"})
    assert pid and len(reg.registered) == 1
    rc = reg.registered[0].runtime_config
    assert rc["credential_key"] == "llm.cloud.openrouter"
    assert rc["api_key_env"] == "OPENROUTER_API_KEY"
    assert "openrouter.ai" in rc["base_url"]


def test_openrouter_provider_registers_from_vault_key(monkeypatch, tmp_path):
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    _pin_runtime_home(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "core.credential_store.has_credential",
        lambda name: name == "llm.cloud.openrouter",
    )
    reg = _FakeRegistry()
    pid = _ensure_openrouter_byok_provider(reg, env={})
    assert pid and len(reg.registered) == 1
    rc = reg.registered[0].runtime_config
    assert rc["credential_key"] == "llm.cloud.openrouter"
    assert "api_key_env" not in rc   # vault-only, no env key leaked in as the source


def test_byok_manifest_classifies_as_paid_cloud(monkeypatch):
    # The root fix: the BYOK lane must be paid_cloud so the daily cap meters it AND the
    # off/cap paid-fallback exclusion binds it (it uses the generic openai_compatible adapter).
    from core.model_selection_policy import provider_cost_class
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reg = _FakeRegistry()
    _ensure_openrouter_byok_provider(reg, env={"OPENROUTER_API_KEY": "x"})
    assert provider_cost_class(reg.registered[0]) == "paid_cloud"


def test_openrouter_lane_carries_attribution_headers(monkeypatch):
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reg = _FakeRegistry()
    _ensure_openrouter_byok_provider(reg, env={"OPENROUTER_API_KEY": "x"})
    headers = reg.registered[0].runtime_config.get("headers") or {}
    # Current OpenRouter attribution header names (X-Title is legacy). HTTP-Referer is the
    # required primary key that creates the app page; categories claim the leaderboard slot.
    assert headers.get("X-OpenRouter-Title") == "VOOL"
    assert headers.get("HTTP-Referer") == "https://vool.dev"
    assert headers.get("X-OpenRouter-Categories") == "personal-agent,programming-app"
    # All three are env-overridable so the referer/title/categories can point anywhere.
    reg2 = _FakeRegistry()
    _ensure_openrouter_byok_provider(
        reg2,
        env={
            "OPENROUTER_API_KEY": "x",
            "VOOL_OPENROUTER_REFERER": "https://mysite.example",
            "VOOL_OPENROUTER_TITLE": "MyApp",
            "VOOL_OPENROUTER_CATEGORIES": "ide-extension",
        },
    )
    h2 = reg2.registered[0].runtime_config["headers"]
    assert h2["HTTP-Referer"] == "https://mysite.example"
    assert h2["X-OpenRouter-Title"] == "MyApp"
    assert h2["X-OpenRouter-Categories"] == "ide-extension"


def test_openrouter_attribution_headers_reach_the_outbound_request(monkeypatch):
    # Attack-plan §5.9 acceptance: the attribution must be on the request that actually reaches
    # OpenRouter, not merely present in the manifest config.
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider

    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reg = _FakeRegistry()
    _ensure_openrouter_byok_provider(reg, env={"OPENROUTER_API_KEY": "sk-or-test"})
    adapter = OpenAICompatibleAdapter(reg.registered[0])

    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
    req = ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}])
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        adapter.run_text_task(req)

    sent = post.call_args.kwargs["headers"]
    assert sent["HTTP-Referer"] == "https://vool.dev"
    assert sent["X-OpenRouter-Title"] == "VOOL"
    assert sent["X-OpenRouter-Categories"] == "personal-agent,programming-app"
    assert "openrouter.ai" in post.call_args.args[0]  # and it went to OpenRouter


def test_local_lane_never_carries_openrouter_attribution(monkeypatch):
    # Attack-plan §5.4: attribution must never be injected into unrelated (local) providers.
    from types import SimpleNamespace
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    local = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434/v1", "timeout_seconds": 5.0},
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "ok"}}
    req = ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}])
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post, mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ):
        local.run_text_task(req)

    sent = post.call_args.kwargs["headers"]
    assert "HTTP-Referer" not in sent
    assert "X-OpenRouter-Title" not in sent
    assert "X-OpenRouter-Categories" not in sent


# ── robustness fixes (audit follow-ups) ──────────────────────────────────────

def _cloud_cmd_owner(text, owner_local):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_command

    return maybe_handle_cloud_command(text, owner_local=owner_local)


def test_is_configured_fails_closed_on_corrupt_store(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "cloud_escalation.json").write_text("{ corrupt json", encoding="utf-8")
    assert cep.is_configured() is True        # present-but-corrupt -> configured (fail closed)
    assert cep.load_policy().mode == "off"    # ...and defaults to off -> gate enforces local


def test_non_owner_cannot_mutate_policy(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # A non-owner-local request (a remote channel, or a forged surface over HTTP) may read
    # status but must not flip the owner's cloud spend policy.
    out = _cloud_cmd_owner("cloud auto", owner_local=False)
    assert "local session" in (out or "").lower()
    assert cep.load_policy().mode == "off"    # policy unchanged by the non-owner message
    assert cep.is_configured() is False       # and it did not opt the user in
    # status is readable from anywhere; only the owner's local session can mutate
    assert _cloud_cmd_owner("cloud status", owner_local=False) is not None
    _cloud_cmd_owner("cloud auto", owner_local=True)
    assert cep.load_policy().mode == "auto"


def test_cap_rejects_oversized_value_without_crashing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    out = _cloud_cmd("cloud cap " + "9" * 5000)   # would blow int() str-digit limit
    assert "removed" in (out or "").lower()
    assert "removed" in _cloud_cmd("cloud cap 1000")  # cannot reinstate the retired quota


def test_write_raw_fails_safe_without_raising(monkeypatch, tmp_path):
    # Parent is a file, so mkdir/write fails; _write_raw must return False, never raise,
    # and the higher-level calls must not abort a turn.
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cep, "_store_path", lambda: afile / "sub" / "cloud_escalation.json")
    assert cep._write_raw({"policy": {"mode": "auto"}}) is False
    cep.save_policy(CloudEscalationPolicy(mode="auto"))   # must not raise
    cep.record_escalation(now=DAY1)                       # must not raise


def test_counter_file_lock_roundtrips(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # The cross-process lock acquires + releases cleanly (msvcrt on Windows, fcntl on POSIX),
    # twice in a row, and the counter still increments correctly while it is held.
    with cep._counter_file_lock():
        pass
    with cep._counter_file_lock():
        pass
    assert cep.record_escalation(now=DAY1) == 1
    assert cep.record_escalation(now=DAY1) == 2
    assert cep.used_today(now=DAY1) == 2
    assert (tmp_path / "cloud_escalation.json.lock").exists()


# --- provider field (multi-provider BYOK) ---
def test_provider_field_round_trips_and_validates():
    p = cep.CloudEscalationPolicy(mode="auto", model="gpt-4.1-mini", provider="openai").normalized()
    assert p.provider == "openai" and p.model == "gpt-4.1-mini"
    d = p.to_dict()
    assert d["provider"] == "openai"
    assert cep.CloudEscalationPolicy.from_dict(d).provider == "openai"


def test_unknown_provider_fails_safe_to_empty():
    assert cep.CloudEscalationPolicy(provider="bogus").normalized().provider == ""
    assert cep.CloudEscalationPolicy(provider="OpenAI").normalized().provider == "openai"  # case-insensitive


def test_old_store_without_provider_loads_as_legacy():
    # A policy JSON written before the provider field existed must load with provider "".
    legacy = {"mode": "auto", "daily_cap": 25, "model": "deepseek/deepseek-chat-v3:free"}
    p = cep.CloudEscalationPolicy.from_dict(legacy)
    assert p.provider == "" and p.model == "deepseek/deepseek-chat-v3:free" and p.mode == "auto"
