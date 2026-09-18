"""Natural-language cloud ACTIONS: they really act, and they cannot hijack ordinary chat.

Replays the exact transcript lines where the local model faked outcomes ("Switching to HY3 🚀"
with no switch, "list refreshed just now" with no refresh, "can't confirm the connection") and
pins the new behavior: real switch through the shared helper, real catalog refresh with real
counts, real auth probe — plus a negative battery proving normal chat falls through untouched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.agent_runtime.fast_command_surface import (
    _is_model_shaped,
    maybe_handle_catalog_refresh_intent,
    maybe_handle_cloud_switch_intent,
    maybe_handle_connection_test_intent,
)


def test_is_model_shaped_separates_models_from_english_words():
    for tok in (
        "gemma", "llama", "deepseek", "qwen", "gpt", "claude", "hy3", "gpt-4", "qwen3-coder", "gemini",
        "hermes", "zephyr", "dolphin", "reka", "openchat", "wizardlm", "mythomax", "pixtral", "codestral",
    ):
        assert _is_model_shaped(tok) is True, tok
    for tok in ("command", "sonar", "tools", "think", "one", "menu", "boredom", "useful", "lunch"):
        assert _is_model_shaped(tok) is False, tok

PAYLOAD = {
    "data": [
        {"id": "tencent/hy3:free", "name": "HY3 Free", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "tencent/hy3", "name": "HY3", "context_length": 262144,
         "pricing": {"prompt": "0.0000002", "completion": "0.0000008", "request": "0"}},
        {"id": "tencent/hy3-preview", "name": "HY3 Preview", "context_length": 262144,
         "pricing": {"prompt": "0.00000006", "completion": "0.00000021", "request": "0"}},
        {"id": "google/gemma-4-26b-a4b-it:free", "name": "Gemma 4 26B", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "google/gemma-4-31b-it:free", "name": "Gemma 4 31B", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder Free", "context_length": 1048576,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
    ]
}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)  # re-point the already-resolved home too
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import core.openrouter_catalog as cat

    cache = tmp_path / "catalog_cache.json"
    cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": PAYLOAD}))
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(RuntimeError("no net")))
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: None)
    yield
    runtime_paths.configure_runtime_home(None)


# --- switch: the transcript replays ------------------------------------------------


def test_lets_go_with_hy3_really_switches():
    action = maybe_handle_cloud_switch_intent("ok lets go with HY3", owner_local=True)
    assert action is not None and action["success"] is True and not action["advice_only"]
    assert "tencent/hy3:free" in action["response"], "must pick the FREE variant of the unique family"
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "tencent/hy3:free", "the switch must actually persist"


def test_typo_connect_me_to_hy3_still_switches():
    action = maybe_handle_cloud_switch_intent("conenct me to hy3", owner_local=True)
    assert action is not None and action["success"] is True


def test_ambiguous_family_disambiguates_without_acting():
    action = maybe_handle_cloud_switch_intent("switch to gemma", owner_local=True)
    assert action is not None and action["advice_only"] is True and action["success"] is False
    assert "gemma-4-26b" in action["response"] and "gemma-4-31b" in action["response"]
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "", "disambiguation must never change the model"


def test_remote_surface_cannot_switch():
    action = maybe_handle_cloud_switch_intent("use qwen3-coder", owner_local=False)
    assert action is not None and action["success"] is False and "local session" in action["response"]
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == ""


def test_switch_negatives_fall_through():
    for message in (
        "switch to heavy",                                   # local tier, never cloud
        "lets use that",                                     # pronoun, no candidate
        "write code to switch to openrouter model",          # coding request
        "what should we build today?",                       # ordinary chat
        "x" * 101 + " switch to hy3",                        # too long
    ):
        assert maybe_handle_cloud_switch_intent(message, owner_local=True) is None, message


def test_english_words_inside_model_ids_do_not_trigger_a_switch():
    # Regression: "think" is a real token in allenai/olmo-3-32b-think and "tools" in a gemini
    # name — an ordinary sentence containing them must never switch the cloud model.
    for message in (
        "do you think we should use a different approach?",  # 'think' is a fragment, not a family
        "can you use your own tools if you need one?",       # 'tools'/'one' are not model families
    ):
        assert maybe_handle_cloud_switch_intent(message, owner_local=True) is None, message


def test_switch_offline_with_no_catalog_falls_through(monkeypatch, tmp_path):
    import core.openrouter_catalog as cat

    monkeypatch.setattr(cat, "_cache_path", lambda: tmp_path / "missing.json")
    assert maybe_handle_cloud_switch_intent("lets go with hy3", owner_local=True) is None


# --- refresh -----------------------------------------------------------------------


def test_refresh_now_again_reports_a_real_failure_when_offline():
    action = maybe_handle_catalog_refresh_intent("right so its simple ---- refresh now again!", owner_local=True)
    assert action is not None and action["success"] is False
    assert "could not reach" in action["response"], "a failed refresh must be reported, never claimed fresh"


def test_refresh_success_reports_real_counts(monkeypatch):
    import core.openrouter_catalog as cat

    class _M:
        def __init__(self, free):
            self._free = free
            self.fetched_at = "2026-07-18T00:00:00+00:00"

    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: tuple(_M(i < 3) for i in range(10)))
    monkeypatch.setattr(cat, "model_is_free", lambda m: m._free)
    action = maybe_handle_catalog_refresh_intent("refresh the models list", owner_local=True)
    assert action["success"] is True and "3 free of 10 models" in action["response"]


def test_refresh_negatives_fall_through():
    for message in ("refresh my memory", "refresh the page please", "can you update the code"):
        assert maybe_handle_catalog_refresh_intent(message, owner_local=True) is None, message


# --- connection test ---------------------------------------------------------------


def test_confirm_connection_with_no_key_says_so():
    action = maybe_handle_connection_test_intent("confirm my openrouter connection pls", owner_local=True)
    assert action is not None and action["success"] is False
    assert "no cloud key" in action["response"]


def _fake_probe_door(monkeypatch, *, status):
    """Fake the ONE outbound door (the probe has not used requests for a long time; a requests
    fake let the keyed probe straight through to the real provider). A 200 carries OpenRouter's
    documented /key shape so green genuinely means a verified key."""
    import json as _json
    import types

    body = (
        _json.dumps({"data": {"label": "test", "limit_remaining": None}}).encode("utf-8")
        if status == 200
        else b""
    )

    def fake_open(url, **_kwargs):
        if status != 200:
            import io
            import urllib.error

            raise urllib.error.HTTPError(url, status, "fake", {}, io.BytesIO(body))
        return types.SimpleNamespace(status=status, read=lambda *_a: body, headers={})

    monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_open)


def test_confirm_connection_green_on_200(monkeypatch):
    from core import cloud_connection_state as ccs

    monkeypatch.setattr("core.credential_store.get_credential", lambda name: "sk-or-v1-" + "f" * 56)
    _fake_probe_door(monkeypatch, status=200)
    ccs.reset_probe_rate_limit_for_tests()
    action = maybe_handle_connection_test_intent("test the openrouter connection", owner_local=True)
    assert action["success"] is True and "Connection verified" in action["response"]


def test_confirm_connection_red_on_401(monkeypatch):
    from core import cloud_connection_state as ccs

    monkeypatch.setattr("core.credential_store.get_credential", lambda name: "sk-or-v1-" + "f" * 56)
    _fake_probe_door(monkeypatch, status=401)
    ccs.reset_probe_rate_limit_for_tests()
    action = maybe_handle_connection_test_intent("verify the cloud connection", owner_local=True)
    assert action["success"] is False and "rejected the key" in action["response"]


def test_connection_negatives_fall_through():
    for message in (
        "check my wifi connection", "test the vpn connection", "verify my internet is up",
        "are we connected?",                  # peer/DB connectivity, no cloud subject
        "is our connection working?",         # ditto
        "can you check my connection to the database?",
    ):
        assert maybe_handle_connection_test_intent(message, owner_local=True) is None, message


def test_connection_test_is_owner_gated(monkeypatch):
    # A non-owner surface must never make VOOL probe with the owner's stored key.
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: "sk-or-v1-" + "f" * 56)
    assert maybe_handle_connection_test_intent("test the openrouter connection", owner_local=False) is None


def test_refresh_is_owner_gated():
    # A refresh hits the network + rewrites the on-disk cache — a remote channel must not trigger it.
    assert maybe_handle_catalog_refresh_intent("refresh the models list", owner_local=False) is None


def test_refresh_does_not_fire_on_ordinary_update_list_chat():
    for message in (
        "update the reading list", "update my shopping list", "update the guest list",
        "please update your model of me",
    ):
        assert maybe_handle_catalog_refresh_intent(message, owner_local=True) is None, message


def test_switch_ignores_english_words_that_are_catalog_families():
    # "command" (cohere/command-*) and "sonar" (perplexity/sonar) are ordinary words here.
    for message in ("pick a command from the menu", "can you select the command for that", "use sonar to find it"):
        assert maybe_handle_cloud_switch_intent(message, owner_local=True) is None, message
