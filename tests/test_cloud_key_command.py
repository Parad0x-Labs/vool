"""`cloud key ...` — BYOK cloud-key onboarding from chat.

The security contract these lock down: the key is consumed at the fast-command layer (so it never
reaches a model or a provider), it is never echoed back, only the owner's own local session may set
or clear it, and the raw value is masked out of anything persisted.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_command_surface import (
    maybe_handle_cloud_key_command,
    maybe_handle_cloud_model_command,
    maybe_handle_openrouter_intent,
)
from core.secret_redaction import contains_secret, redact_secrets

FAKE_KEY = "sk-or-v1-" + "d" * 56
CRED_NAME = "llm.cloud.openrouter"


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for the encrypted credential store; lane activation is stubbed so no
    test can write a real provider manifest."""
    saved: dict[str, str] = {}

    def _store(name, value, *, label=""):
        saved[name] = value

    monkeypatch.setattr("core.credential_store.store_credential", _store)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: name in saved)
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: saved.get(name))
    monkeypatch.setattr("core.credential_store.delete_credential", lambda name: saved.pop(name, None) is not None)
    monkeypatch.setattr("core.runtime_provider_defaults.activate_openrouter_byok", lambda env=None: "openrouter-byok:test")
    monkeypatch.setattr("core.runtime_provider_defaults.deactivate_openrouter_byok", lambda: 1)
    return saved


def test_ignores_messages_that_are_not_the_command(store):
    assert maybe_handle_cloud_key_command("what is the weather", owner_local=True) is None
    assert maybe_handle_cloud_key_command("tell me about cloud keys", owner_local=True) is None
    assert store == {}


def test_remote_surface_cannot_set_the_key(store):
    reply = maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=False)
    assert "local session" in reply
    assert store == {}, "a remote surface must never install the owner's cloud key"


def test_bare_command_explains_both_routes(store):
    reply = maybe_handle_cloud_key_command("cloud key", owner_local=True)
    assert "cloud key <your-key>" in reply and "Settings" in reply
    assert store == {}


def test_short_value_is_rejected_and_not_stored(store):
    reply = maybe_handle_cloud_key_command("cloud key abc", owner_local=True)
    assert "complete API key" in reply
    assert store == {}


def test_valid_key_is_sealed_but_never_echoed(store):
    reply = maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=True)
    assert store[CRED_NAME] == FAKE_KEY, "the key must reach the credential store intact"
    assert FAKE_KEY not in reply, "the key must never be echoed back into the transcript"
    assert FAKE_KEY[-4:] in reply, "the owner still needs a last-4 hint to identify the key"


def test_forget_removes_the_key(store):
    maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=True)
    reply = maybe_handle_cloud_key_command("cloud key forget", owner_local=True)
    assert "removed" in reply.lower()
    assert CRED_NAME not in store


def test_a_failed_save_is_reported_rather_than_claimed(monkeypatch):
    """store_credential can fail soft, so a save is confirmed by re-reading, never assumed."""
    monkeypatch.setattr("core.credential_store.store_credential", lambda *a, **k: None)
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reply = maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=True)
    assert "not stored" in reply


def test_the_raw_key_is_masked_out_of_anything_persisted():
    """persistent_memory redacts the turn before the conversation log and downstream writers."""
    turn = f"cloud key {FAKE_KEY}"
    assert contains_secret(turn)
    assert FAKE_KEY not in redact_secrets(turn)


def test_storing_a_key_activates_the_lane_without_a_restart(store, monkeypatch):
    """Provider registration otherwise happens only at boot; a key stored mid-session must
    register the burst lane immediately, not silently wait for the next restart."""
    activated = []
    monkeypatch.setattr(
        "core.runtime_provider_defaults.activate_openrouter_byok",
        lambda env=None: activated.append(True) or "openrouter-byok:live",
    )
    reply = maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=True)
    assert activated, "storing the key must trigger provider registration"
    assert "live now" in reply


def test_forgetting_the_key_retires_the_lane(store, monkeypatch):
    deactivated = []
    monkeypatch.setattr(
        "core.runtime_provider_defaults.deactivate_openrouter_byok",
        lambda: deactivated.append(True) or 1,
    )
    maybe_handle_cloud_key_command(f"cloud key {FAKE_KEY}", owner_local=True)
    reply = maybe_handle_cloud_key_command("cloud key forget", owner_local=True)
    assert deactivated, "a keyless lane cannot auth, so forget must disable it"
    assert "disabled" in reply


# --- `cloud model` -----------------------------------------------------------------


def test_cloud_model_persists_and_reregisters(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    store[CRED_NAME] = FAKE_KEY  # key present -> re-registration expected
    reply = maybe_handle_cloud_model_command(
        "cloud model deepseek/deepseek-chat-v3-0324:free", owner_local=True
    )
    assert "deepseek/deepseek-chat-v3-0324:free" in reply
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "deepseek/deepseek-chat-v3-0324:free"
    assert "spend your credits" not in reply, "a :free id must not get the paid warning"


def test_cloud_model_rejects_garbage_and_remote(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core.cloud_escalation_policy import load_policy

    before = load_policy().model
    # A bare id ("notamodel") is now valid (a direct provider's model); a malformed one with
    # illegal characters is still rejected.
    assert "does not look like" in maybe_handle_cloud_model_command("cloud model bad!id", owner_local=True)
    assert "local session" in maybe_handle_cloud_model_command("cloud model a/b", owner_local=False)
    assert load_policy().model == before, "neither attempt may change the persisted model"


def test_cloud_model_paid_id_refuses_unconfirmed_then_warns_when_confirmed(store, monkeypatch, tmp_path):
    """A11 pass002: a paid pin is refused until THIS switch explicitly confirms spend;
    once confirmed through the same command, the honest per-token warning still ships."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    reply = maybe_handle_cloud_model_command("cloud model openai/gpt-4.1", owner_local=True)
    assert "MODEL_COST_UNKNOWN:" in reply, reply
    assert "spend provider credits" in reply and "this switch only" in reply
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model != "openai/gpt-4.1"
    confirmed = maybe_handle_cloud_model_command("cloud model openai/gpt-4.1 --paid", owner_local=True)
    assert "Cloud model set to" in confirmed, confirmed
    assert "spend your credits" in confirmed


# --- natural-language onboarding intent ---------------------------------------------


def test_intent_catches_the_connect_request(store):
    """The exact phrasing that previously fell through to the local model and got hallucinated
    CLI advice must now get the deterministic onboarding flow."""
    reply = maybe_handle_openrouter_intent(
        "i need you to connect to  openrouter api so you can use the api", owner_local=True
    )
    assert reply is not None and "cloud key <your-OpenRouter-key>" in reply
    assert "environment variable" not in reply.lower() or "no environment variables" in reply.lower()


def test_intent_catches_the_do_it_all_followup(store):
    reply = maybe_handle_openrouter_intent(
        "yeah i have api key ready, i wnat you to do all and we willuse your interface to work with openrouter api",
        owner_local=True,
    )
    assert reply is not None


def test_intent_reports_state_when_a_key_is_already_set(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    store[CRED_NAME] = FAKE_KEY
    reply = maybe_handle_openrouter_intent("connect to openrouter please", owner_local=True)
    assert "Already connected" in reply


def test_intent_reports_explicit_chat_pin_without_bursting_advice(store, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    store[CRED_NAME] = FAKE_KEY
    reply = maybe_handle_openrouter_intent(
        "can you stay with openrouter please?",
        owner_local=True,
        requested_model="nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    assert reply is not None
    assert "this chat is pinned" in reply.lower()
    assert "does not disable this explicit" in reply.lower()
    assert "turn it on with `cloud ask`" not in reply.lower()


def test_intent_leaves_coding_and_unrelated_messages_to_the_model(store):
    for message in (
        "write me a python script that calls the openrouter api",
        "will openrouter see the vool in their rankings",
        "what is the weather today",
        "x " * 300 + "connect openrouter",  # long real task
    ):
        assert maybe_handle_openrouter_intent(message, owner_local=True) is None


# --- bare-pasted secrets -------------------------------------------------------------


def test_bare_openrouter_key_paste_is_sealed_not_forwarded(store):
    """Users answer 'paste your key' with just the key. It must be sealed exactly like
    `cloud key <key>` and never fall through to a model (which previously lied 'key is set')."""
    from core.agent_runtime.fast_command_surface import maybe_handle_bare_secret

    reply = maybe_handle_bare_secret(f"ok  {FAKE_KEY}", owner_local=True)
    assert reply is not None and "saved (ends" in reply
    assert store[CRED_NAME] == FAKE_KEY
    assert FAKE_KEY not in reply


def test_bare_key_from_remote_surface_is_refused(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_bare_secret

    reply = maybe_handle_bare_secret(FAKE_KEY, owner_local=False)
    assert "local session" in reply
    assert store == {}


def test_other_bare_secrets_are_stopped_without_echo(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_bare_secret

    token = "ghp_" + "a" * 30
    reply = maybe_handle_bare_secret(token, owner_local=True)
    assert reply is not None and token not in reply
    assert store == {}, "a non-OpenRouter secret must not be stored as the cloud key"


def test_long_messages_and_plain_chat_are_left_alone(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_bare_secret

    assert maybe_handle_bare_secret("what about lunch?", owner_local=True) is None
    assert maybe_handle_bare_secret("please review this config " + "word " * 20, owner_local=True) is None


# --- key-status questions ------------------------------------------------------------


def test_key_status_answers_from_the_store(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent as status

    reply = status("we have api key set alr for Openrouter?", owner_local=True)
    assert reply is not None and "no cloud key is stored" in reply
    store[CRED_NAME] = FAKE_KEY
    reply = status("we have api key set alr for Openrouter?", owner_local=True)
    assert "yes" in reply and FAKE_KEY[-4:] in reply and FAKE_KEY not in reply


def test_bare_check_phrase_gets_a_hedged_store_answer(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent as status

    reply = status("check if this already set", owner_local=True)
    assert reply is not None and reply.startswith("If you mean your OpenRouter cloud key")
    assert status("what is the weather today", owner_local=True) is None


def test_opaque_custom_key_sealed_via_explicit_provider(store, monkeypatch):
    # Opaque custom (OpenAI-compatible) keys have no detectable prefix, so the chat-safe onboarding
    # is the EXPLICIT `cloud key <key> custom` form: the key is consumed at the fast-command layer
    # (never sent to a model), sealed under the custom slot, and never echoed back.
    monkeypatch.setattr("core.runtime_provider_defaults.activate_provider_byok", lambda pid, env=None: "")
    monkeypatch.setattr("core.runtime_provider_defaults.retire_nonactive_provider_lanes", lambda pid: 0)
    opaque = "my-opaque-proxy-key-abc123XYZ"
    reply = maybe_handle_cloud_key_command(f"cloud key {opaque} custom", owner_local=True)
    assert reply is not None
    assert opaque not in reply                        # never echoed
    assert store.get("llm.cloud.custom") == opaque     # sealed under the custom slot
    assert "custom" in reply.lower()


def test_unknown_explicit_provider_is_rejected(store):
    reply = maybe_handle_cloud_key_command("cloud key sk-whatever-000000000000 notaprovider", owner_local=True)
    assert "not a known provider" in reply.lower()
    assert store == {}


# --- non-owner key disclosure --------------------------------------------------------
#
# The audited regression: these handlers took `owner_local` and never read it, so a channel
# message — never owner-local even over loopback — got the key's last four characters and a
# confirmation that a key existed.


@pytest.mark.parametrize(
    "question",
    [
        "do we have the api key set?",
        "is the openrouter key configured?",
        "have we saved the cloud key?",
        "we have api key set alr for Openrouter?",
        "check if this already set",
    ],
)
def test_key_status_never_leaks_the_key_to_a_remote_surface(store, question):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent as status

    store[CRED_NAME] = FAKE_KEY
    reply = status(question, owner_local=False)
    assert reply is not None, "must not fall through to the model, which would guess an answer"
    assert FAKE_KEY not in reply and FAKE_KEY[-4:] not in reply
    assert "yes" not in reply.lower().split("—")[0]
    assert "owner-local" in reply


def test_key_status_reply_to_a_remote_surface_is_not_an_existence_oracle(store):
    """The refusal must be byte-identical with and without a stored key: a non-owner must not
    learn even that one exists."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent as status

    without = status("do we have the api key set?", owner_local=False)
    store[CRED_NAME] = FAKE_KEY
    with_key = status("do we have the api key set?", owner_local=False)
    assert without == with_key


def test_openrouter_intent_does_not_disclose_key_state_remotely(store):
    """Same class as the key-status leak: both branches are keyed on whether a key is stored, so
    answering a non-owner at all discloses its existence."""
    without = maybe_handle_openrouter_intent("connect to openrouter please", owner_local=False)
    store[CRED_NAME] = FAKE_KEY
    with_key = maybe_handle_openrouter_intent("connect to openrouter please", owner_local=False)
    assert without == with_key
    assert with_key is not None and "owner-local" in with_key
    assert "Already connected" not in with_key and FAKE_KEY[-4:] not in with_key


def test_owner_local_still_gets_the_real_answers(store):
    """The gates must not cost the owner the deterministic answers these handlers exist for."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_status_intent as status

    store[CRED_NAME] = FAKE_KEY
    reply = status("do we have the api key set?", owner_local=True)
    assert reply is not None and FAKE_KEY[-4:] in reply and FAKE_KEY not in reply
