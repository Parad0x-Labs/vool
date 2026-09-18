"""Secret-only redaction: masks high-confidence secrets, leaves ordinary prose untouched."""
from __future__ import annotations

from core.secret_redaction import contains_secret, redact_secrets

# --- recall: real secrets get masked ---------------------------------------

def test_openai_anthropic_key_masked() -> None:
    out = redact_secrets("here is the key sk-ant-api03-abcDEF123456789xyz to use")
    assert "sk-ant" not in out and "[redacted-api-key]" in out
    assert contains_secret("sk-ant-api03-abcDEF123456789xyz")


def test_aws_and_github_tokens_masked() -> None:
    assert "[redacted-api-key]" in redact_secrets("AKIAIOSFODNN7EXAMPLE is the id")
    assert "[redacted-api-key]" in redact_secrets("token ghp_" + "a" * 36)


def test_every_byok_provider_key_prefix_is_redacted() -> None:
    # Each direct-BYOK provider prefix must be masked if it is ever pasted into chat, so it never
    # persists in plaintext to the conversation log / memory. Groq's gsk_ was the audit gap.
    samples = {
        "openrouter": "sk-or-v1-" + "a" * 40,
        "openai": "sk-proj-" + "A" * 40,
        "anthropic": "sk-ant-api03-" + "b" * 40,
        "groq": "gsk_" + "C" * 40,
        "google": "AIza" + "D" * 35,
        "openai_bare": "sk-" + "e" * 40,   # bare sk- (OpenAI/DeepSeek/Moonshot share it)
    }
    for provider, key in samples.items():
        assert contains_secret(key), f"{provider} key not detected as a secret"
        assert key not in redact_secrets(f"my key is {key} ok"), f"{provider} key not masked"


def test_jwt_masked() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    out = redact_secrets(f"authorization: Bearer {jwt}")
    assert jwt not in out and "[redacted" in out


def test_pem_private_key_block_masked() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34\nabc/def==\n-----END RSA PRIVATE KEY-----"
    out = redact_secrets(f"my key:\n{pem}\nthanks")
    assert "PRIVATE KEY" not in out and "[redacted-private-key]" in out
    assert out.startswith("my key:") and out.endswith("thanks")


def test_labeled_password_and_apikey_masked() -> None:
    assert redact_secrets("password: hunter2") == "password: [redacted]"
    assert redact_secrets("api_key=abc123def456") == "api_key: [redacted]"
    assert redact_secrets("my passphrase is fine") == "my passphrase is fine"  # no delimiter -> not a secret


def test_long_base58_masked_short_address_preserved() -> None:
    secret = "5" * 70   # long base58 -> likely a Solana secret key / seed
    address = "1" * 44   # public-address length -> must stay readable
    out = redact_secrets(f"seed {secret} sends to {address}")
    assert "[redacted-key]" in out and secret not in out
    assert address in out  # not over-redacted


# --- precision: prose is left alone ----------------------------------------

def test_normal_prose_untouched() -> None:
    text = "Let's meet at 3pm to review the Q4 numbers and the web0 launch on port 8080."
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_email_is_not_a_secret() -> None:
    text = "email me at alice@example.com about the demo"
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_empty_and_none_safe() -> None:
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None  # type: ignore[arg-type]
    assert contains_secret("") is False


def test_conversation_log_masks_pasted_secret(tmp_path, monkeypatch) -> None:
    # A password/key pasted into chat must not land in conversation_log.jsonl in plaintext.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import persistent_memory, runtime_paths

    runtime_paths.configure_runtime_home(tmp_path)
    try:
        persistent_memory.append_conversation_event(
            session_id="s1",
            user_input="set it up, api_key=sk-ant-api03-SECRETVALUE1234567890 thanks",
            assistant_output="stored",
        )
        log = persistent_memory.conversation_log_path().read_text(encoding="utf-8")
        assert "sk-ant-api03-SECRETVALUE1234567890" not in log
        assert "[redacted" in log
    finally:
        runtime_paths.configure_runtime_home(None)


def test_summarizer_output_is_redacted(monkeypatch) -> None:
    # Even if the local model ignores the "redact" instruction and echoes a secret, the summary that
    # gets persisted must not contain it.
    from core import conversation_summarizer

    # Pin the picked model: the summarizer declines to call one at all when none qualifies, and
    # conftest blocks live Ollama here, so without this the extractive fallback answers and the
    # echoed secret never reaches the redaction this test exists to prove.
    monkeypatch.setattr(conversation_summarizer, "_pick_model_uncached", lambda: "qwen2.5:7b")
    monkeypatch.setattr(
        conversation_summarizer, "_call_ollama",
        lambda *_a, **_k: "## Key Facts\n- api_key=sk-ant-api03-LEAKEDVALUE1234567890\n",
    )
    out = conversation_summarizer.summarize_messages([{"role": "user", "content": "hi"}])
    assert "sk-ant-api03-LEAKEDVALUE1234567890" not in out and "[redacted" in out


def test_dialogue_turn_masks_secret_at_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    from storage import dialogue_memory

    runtime_paths.configure_runtime_home(tmp_path)
    try:
        secret = "api_key=sk-ant-api03-DIALOGSECRET1234567890"
        dialogue_memory.record_dialogue_turn(
            "s1", raw_input=secret, normalized_input=secret, reconstructed_input=secret,
            speaker_role="user", topic_hints=[], reference_targets=[],
            understanding_confidence=1.0, quality_flags=[],
        )
        conn = dialogue_memory.get_connection()
        rows = conn.execute(
            "SELECT raw_input, normalized_input, reconstructed_input FROM dialogue_turns"
        ).fetchall()
        conn.close()
        blob = " ".join(str(cell) for row in rows for cell in row)
        assert "sk-ant-api03-DIALOGSECRET1234567890" not in blob and "[redacted" in blob
    finally:
        runtime_paths.configure_runtime_home(None)


def test_carried_verbatim_context_is_redacted(monkeypatch) -> None:
    """The unanchored remainder rides into the summary turn verbatim, so it is a second path a
    secret can reach the model's context by -- one the summarizer's own prompt never sees."""
    from core import conversation_summarizer

    secret = "sk-ant-api03-CARRIEDLEAK9876543210"
    monkeypatch.setattr(conversation_summarizer, "_pick_model_uncached", lambda: "qwen2.5:7b")
    monkeypatch.setattr(
        conversation_summarizer,
        "_call_ollama",
        lambda *_a, **_k: "## Key Facts\n- ok\n## Decisions Made\n- none\n"
        "## Open Questions\n- none\n## Context Summary\nfine.",
    )

    # 30 messages: the compressed prefix is 22, anchored to 16, so 16..21 ride verbatim.
    history = [{"role": "user", "content": f"message number {i} about deployment"} for i in range(30)]
    history[18] = {"role": "user", "content": f"my api_key={secret} keep it safe"}

    compressed, fired = conversation_summarizer.compress_if_needed(history)
    summary = "".join(
        str(message.get("content"))
        for message in compressed
        if "<context_summary>" in str(message.get("content"))
    )

    assert fired is True
    assert "## Recent Context (verbatim)" in summary
    assert secret not in summary
    assert "[redacted" in summary


# --- audit hardening: Bearer-with-space + WIF private keys (2026-07-19) ---
def test_bearer_token_with_space_is_redacted():
    from core.secret_redaction import contains_secret, redact_secrets

    txt = "here you go Authorization: Bearer abcDEF123456ghijklmnop and thanks"
    out = redact_secrets(txt)
    assert "abcDEF123456ghijklmnop" not in out
    assert "Bearer [redacted]" in out
    assert contains_secret(txt) is True


def test_bitcoin_wif_private_key_is_redacted_but_addresses_survive():
    from core.secret_redaction import contains_secret, redact_secrets

    wif = "5Kb8kLf9zgWQnogidDA76MzPL6TsZZY36hWXMssSzNydYXYB9KF"  # 51-char WIF
    assert redact_secrets(f"key is {wif}") == "key is [redacted-key]"
    assert contains_secret(wif) is True
    # A normal Solana public address (32-44 chars) must stay readable.
    addr = "9M949Afyfrobert5tZ1Xg8g2Nq1r9dQh2Y6b3c4d5e6"
    assert addr in redact_secrets(f"pay {addr}")
