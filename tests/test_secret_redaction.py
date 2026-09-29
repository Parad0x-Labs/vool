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


def test_evm_private_key_shape_is_redacted_but_addresses_and_bare_digests_survive():
    from core.secret_redaction import contains_secret, redact_secrets

    # The wallet's BACKUP_FORMAT_EVM is exactly 0x + 64 hex — the same shape as a public tx hash.
    key = "0x" + "4c0883a694529ec3b3d6d5f0a2e7d9b41c2f8a6d3e5c7b9a1f4d2e8c6b0a3d5f"
    assert len(key) == 66
    out = redact_secrets(f"is this my key {key} or a hash")
    assert key not in out and "[redacted-key]" in out
    assert contains_secret(key) is True
    upper = "0x" + key[2:].upper()  # checksum-style casing is the same shape
    assert upper not in redact_secrets(f"check {upper}")
    # An EVM address (0x + 40 hex) is a different, public shape and stays readable.
    address = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
    assert redact_secrets(f"pay {address}") == f"pay {address}"
    # A bare sha256 digest (64 hex, no 0x) is the runtime's own diagnostic currency. One that
    # contains a '0' splits the base58 run and survives; a no-zero digest was already inside
    # the pre-existing base58 secret class before this rule (the x402 digest-vouch seam covers
    # the digests the wallet itself mints).
    digest = "a0" * 32
    assert redact_secrets(f"digest {digest}") == f"digest {digest}"


def test_a_registered_evm_tx_hash_stays_readable_and_an_unregistered_one_is_masked():
    from core.secret_redaction import (
        clear_exact_secrets_for_tests,
        redact_secrets,
        register_public_identifier,
    )

    clear_exact_secrets_for_tests()
    mine = "0x" + "1" * 64    # a tx hash the wallet itself rendered and registered
    pasted = "0x" + "2" * 64  # the same shape with no provenance: a private key or a third-party hash
    register_public_identifier(mine)
    out = redact_secrets(f"settled {mine} not {pasted}")
    assert mine in out and pasted not in out and out.count("[redacted-key]") == 1
    clear_exact_secrets_for_tests()
    assert mine not in redact_secrets(f"settled {mine}")  # the registry is not permanent state


def test_bip39_recovery_phrase_is_redacted_even_unlabelled():
    from core.secret_redaction import contains_secret, redact_secrets
    from core.wallet.mnemonic import generate_mnemonic

    phrase = generate_mnemonic(strength_bits=128)  # a REAL checksum-valid 12-word phrase
    # unlabelled, mid-sentence, with prose on both sides
    masked = redact_secrets(f"i wrote this down somewhere: {phrase} -- is that safe?")
    assert phrase not in masked
    assert masked == "i wrote this down somewhere: [redacted-mnemonic] -- is that safe?"
    # labelled: the labelled rule alone masked only the FIRST word, leaving eleven readable
    assert redact_secrets(f"mnemonic: {phrase}") == "mnemonic: [redacted]"
    assert contains_secret(phrase) is True


def test_bip39_redaction_takes_the_longest_valid_run_and_tolerates_edge_punctuation():
    from core.secret_redaction import redact_secrets
    from core.wallet.mnemonic import generate_mnemonic

    words24 = generate_mnemonic(strength_bits=256).split()
    masked = redact_secrets(f"backup: {' '.join(words24)} done.")
    assert " ".join(words24) not in masked
    assert masked == "backup: [redacted-mnemonic] done."


def test_a_word_run_without_the_bip39_checksum_is_not_mangled():
    from core.secret_redaction import redact_secrets

    # twelve real wordlist words whose checksum does not validate: ordinary prose, untouched
    run = " ".join(
        ["abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse", "access", "accident"]
    )
    assert redact_secrets(f"the list starts {run} and continues") == f"the list starts {run} and continues"


def test_bip39_redaction_matches_the_wallets_own_accepted_normalization():
    """The wallet validator accepts `_nfkd(phrase).lower().split()`; the redactor must see
    every phrase that validator would accept, in the casing the user typed it."""
    from core.secret_redaction import contains_secret, redact_secrets

    vector = " ".join(["abandon"] * 11 + ["about"])  # BIP-39 zero-entropy test vector
    assert redact_secrets(f"note: {vector} ok") == "note: [redacted-mnemonic] ok"
    upper = vector.upper()
    masked = redact_secrets(f"note: {upper} ok")
    assert upper not in masked and "[redacted-mnemonic]" in masked
    mixed = " ".join(w.capitalize() for w in vector.split())
    masked = redact_secrets(f"note: {mixed} ok")
    assert "Abandon" not in masked and "[redacted-mnemonic]" in masked
    # NFKD-foldable spelling the wallet would accept (fullwidth letters in some words)
    fullwidth = " ".join("ａｂａｎｄｏｎ" if i % 3 == 0 else w for i, w in enumerate(vector.split()))
    masked = redact_secrets(f"x {fullwidth} y")
    assert "[redacted-mnemonic]" in masked and "abandon" not in masked
    assert contains_secret(vector) and contains_secret(upper) and contains_secret(mixed)


def test_a_labelled_uppercase_phrase_masks_every_word():
    from core.secret_redaction import redact_secrets

    vector = " ".join(["abandon"] * 11 + ["about"])
    out = redact_secrets(f"mnemonic: {vector.upper()}")
    # the label rule may re-mask the marker ([redacted-mnemonic] -> [redacted]); no PHRASE
    # word may survive — the pre-fix behavior left ten of twelve words readable
    assert "ABANDON" not in out and "ABOUT" not in out


def test_every_valid_word_count_is_redacted():
    from core.secret_redaction import redact_secrets
    from core.wallet.mnemonic import generate_mnemonic

    for bits in (128, 160, 192, 224, 256):
        phrase = generate_mnemonic(strength_bits=bits)
        out = redact_secrets(f"kept: {phrase} end")
        assert phrase not in out and "[redacted-mnemonic]" in out, bits


def test_overlapping_valid_phrases_leave_no_recoverable_residue():
    """Two checksum-valid windows overlapping by one word: masking the first must not leave
    eleven consecutive words of the second — that residue is brute-forceable through the
    checksum. At most EIGHT consecutive wordlist words may survive (four unknown words)."""
    from core.secret_redaction import redact_secrets
    from core.wallet.mnemonic import WORDS, generate_mnemonic, validate_mnemonic

    phrase_a = generate_mnemonic(strength_bits=128).split()
    overlap = None
    for word in WORDS:  # ~1/16 of candidates validate; bounded scan
        candidate = [phrase_a[11], "zone", "yellow", "wolf", "video", "vintage", "turtle", "tunnel", "tiger", "thunder", "trade", word]
        if validate_mnemonic(" ".join(candidate)):
            overlap = candidate
            break
    assert overlap is not None
    text = " ".join(phrase_a + overlap[1:])
    assert validate_mnemonic(" ".join(text.split()[11:23]))  # the second window is a REAL phrase
    out = redact_secrets(f"backup {text} end")
    run = best = 0
    for token in out.split():
        run = run + 1 if token.strip(".,:!?") in WORDS else 0
        best = max(best, run)
    assert best <= 8, best


def test_wordlist_heavy_prose_without_a_valid_checksum_is_untouched():
    from core.secret_redaction import redact_secrets
    from core.wallet.mnemonic import WORDS

    glue = next(w for w in ("ok", "um", "said") if w not in WORDS)  # membership-verified absent
    listed = list(WORDS[:40])
    prose_words = [w if i % 5 else glue for i, w in enumerate(listed)]
    run = 0
    for w in [*prose_words, glue]:  # membership-based guard: no 12-word wordlist run can exist
        run = run + 1 if w in WORDS else 0
        assert run < 12
    prose = " ".join(prose_words)
    assert redact_secrets(f"reading notes: {prose} -- filed") == f"reading notes: {prose} -- filed"


def test_unavailable_wordlist_is_visible_and_never_silently_off():
    import pytest

    import core.secret_redaction as sr

    def broken_read():
        raise OSError("wordlist absent (broken install)")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sr, "_read_bip39_wordlist", broken_read)
        mp.setattr(sr, "_BIP39_INDEX", None)
        assert sr.mnemonic_redaction_available() is False
        vector = " ".join(["abandon"] * 11 + ["about"])
        # the redactor keeps its never-raise law — which is exactly why the availability
        # flag exists: callers that PERSIST text must refuse rather than store plaintext
        assert sr.redact_secrets(f"note: {vector} ok") == f"note: {vector} ok"
        mp.setattr(sr, "_BIP39_INDEX", None)  # drop any cache before the real loader returns
    assert sr.mnemonic_redaction_available() is True  # failures are not cached: it recovers


def test_a_malformed_wordlist_is_unavailable_not_silently_partial():
    import pytest

    import core.secret_redaction as sr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sr, "_read_bip39_wordlist", lambda: ["abandon"] * 10)  # wrong shape
        mp.setattr(sr, "_BIP39_INDEX", None)
        assert sr.mnemonic_redaction_available() is False
        mp.setattr(sr, "_BIP39_INDEX", None)
    assert sr.mnemonic_redaction_available() is True
