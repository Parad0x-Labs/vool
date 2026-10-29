"""Three money-adjacent findings from the 2026-10-29 fresh-eyes test.

1. A key saved "for later (unverified)" was stored, but API Keys said "No keys yet" above it.
2. The chat command ``cloud model auto`` turned the free cloud lane on without saying chats would
   leave the computer, and ``cloud off`` left that lane on.
3. Settings called a pinned paid model "ready" while its provider had no key.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core import runtime_paths

ROOT = Path(__file__).resolve().parents[1]

CATALOG_PAYLOAD = {
    "data": [
        {"id": "deepseek/deepseek-chat-v3:free", "name": "DeepSeek V3 Free", "context_length": 163840,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder Free", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "openai/gpt-5", "name": "GPT-5", "context_length": 400000,
         "pricing": {"prompt": "0.00000125", "completion": "0.00001", "request": "0"}},
    ]
}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    import core.openrouter_catalog as cat

    cache = tmp_path / "catalog_cache.json"
    cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": CATALOG_PAYLOAD}))
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")),
    )
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: "")
    yield tmp_path
    runtime_paths.configure_runtime_home(None)


def _policy():
    from core.cloud_escalation_policy import load_policy

    return load_policy()


# ---- 2. free cloud is said before it goes on, and `cloud off` turns it off -----------------------


def test_cloud_model_auto_asks_first_and_changes_nothing(home, monkeypatch):
    import core.openrouter_catalog as cat
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    fetched: list[bool] = []
    real_pick = cat.pick_auto_free_models
    monkeypatch.setattr(cat, "pick_auto_free_models", lambda **kw: fetched.append(True) or real_pick(**kw))

    reply = maybe_handle_cloud_model_command("cloud model auto", owner_local=True)

    assert "leave this computer" in reply and "Nothing changed" in reply
    assert "`cloud model auto --online`" in reply
    policy = _policy()
    assert policy.free_cloud_enabled is False and policy.model == ""
    assert not fetched, "the ask comes before anything is read from the online catalog"


def test_cloud_model_auto_online_turns_it_on_and_says_so(home):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    reply = maybe_handle_cloud_model_command("cloud model auto --online", owner_local=True)

    assert reply.startswith("Cloud model set to auto")
    assert "leave this computer" in reply and "`cloud off`" in reply
    assert "Nothing goes out until you add an OpenRouter key." in reply
    policy = _policy()
    assert policy.free_cloud_enabled is True and policy.model == "auto"


def test_cloud_model_auto_does_not_ask_again_once_free_cloud_is_on(home):
    from core import cloud_escalation_policy as cep
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    cep.set_free_cloud_enabled(True)
    reply = maybe_handle_cloud_model_command("cloud model auto", owner_local=True)

    assert reply.startswith("Cloud model set to auto")
    assert _policy().model == "auto"


def test_naming_a_free_cloud_model_says_the_free_lane_went_on(home):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    reply = maybe_handle_cloud_model_command("cloud model deepseek/deepseek-chat-v3:free", owner_local=True)

    assert "Cloud model set to" in reply and "leave this computer" in reply
    assert _policy().free_cloud_enabled is True


def test_cloud_off_turns_the_free_lane_off_too(home):
    from core import cloud_escalation_policy as cep
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_command

    cep.set_free_cloud_enabled(True)
    reply = maybe_handle_cloud_command("cloud off", owner_local=True)

    assert reply.startswith("Cloud escalation set to off.")
    policy = _policy()
    assert policy.mode == cep.MODE_OFF and policy.free_cloud_enabled is False


def test_settings_asks_before_let_vool_choose_turns_free_cloud_on():
    page = (ROOT / "core" / "vool_settings_page.py").read_text(encoding="utf-8")
    ask = page.index("turns on free OpenRouter models. Chats that use the cloud lane will leave this computer")
    post = page.index("let r = await fetch('/api/cloud/model'", ask)
    guard = page[page.rindex("if (clearing && provider === 'openrouter' && !m.free_cloud_enabled", 0, ask):ask]
    assert guard and ask < post


# ---- 3. a pin without a key is not "ready" ------------------------------------------------------


def _current():
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/cloud/model", query={}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host="127.0.0.1",
    )
    assert res.status == 200
    return json.loads(res.body)


def test_pinned_model_without_a_key_reads_as_not_ready(home):
    from core.cloud_model_control import set_cloud_model

    ok, _message, _chosen = set_cloud_model("openai/gpt-5", owner_local=True, confirm_paid=True)
    assert ok

    body = _current()
    assert body["model"] == "openai/gpt-5" and body["cost_state"] == "paid"
    assert body["key_ready"] is False and body["provider_label"] == "OpenRouter"


def test_pinned_model_with_a_key_reads_as_ready(home, monkeypatch):
    from core.cloud_model_control import set_cloud_model

    ok, _message, _chosen = set_cloud_model("openai/gpt-5", owner_local=True, confirm_paid=True)
    assert ok
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-placeholder")

    assert _current()["key_ready"] is True


def test_settings_says_a_keyless_pin_cannot_answer_instead_of_ready():
    page = (ROOT / "core" / "vool_settings_page.py").read_text(encoding="utf-8")
    overview = page[page.index("function widgetModelsOverview"):]
    overview = overview[: overview.index("mpDrawDetail(root")]
    assert "reads.pin.key_ready === false" in overview
    assert "cannot answer yet: add a " in overview
    # "ready for answers" is only reached when the key check did not fail.
    assert overview.index("cannot answer yet") < overview.index("ready for answers once")
    model_widget = page[page.index("function widgetModel(stack)"):]
    assert "m.key_ready === false" in model_widget[:4000]


# ---- 1. a key saved for later is shown with its Remove ------------------------------------------


def test_saved_for_later_key_is_not_reported_as_no_keys():
    page = (ROOT / "core" / "vool_settings_page.py").read_text(encoding="utf-8")
    widget = page[page.index("function widgetKeys(stack)"):]
    widget = widget[: widget.index("\n}\n")]
    assert "if (!yours.length && !savedForLater.length) list.appendChild(el('div', 'empty', T('keys.empty_plain'" in widget
    assert "saved for later and unused" in widget
    # The saved-for-later list sits under the key form, ahead of the X setup, and offers Remove.
    assert widget.index("const quarantine = el('div', 'stack');") < widget.index("const xRow = el('div', 'inline x-setup-entry');")
    assert "'btn danger key-quarantine-delete', T('keys.remove', 'Remove')" in widget



def test_usepod_pin_is_not_called_keyless(home):
    from core import cloud_escalation_policy as cep
    from dataclasses import replace

    cep.save_policy(replace(cep.load_policy(), model="some/route", provider="usepod"))
    body = _current()
    assert body["model"] == "some/route"
    assert "key_ready" not in body, "the wallet lane can answer without a token"


def test_cloud_off_says_which_part_did_not_save(home, monkeypatch):
    from core import cloud_escalation_policy as cep
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_command

    cep.set_free_cloud_enabled(True)
    monkeypatch.setattr(cep, "set_free_cloud_enabled", lambda enabled: cep.load_policy())
    reply = maybe_handle_cloud_command("cloud off", owner_local=True)

    assert "free cloud lane off" in reply and "can still answer" in reply
    assert "unchanged" not in reply
