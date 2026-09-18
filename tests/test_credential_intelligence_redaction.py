# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
"""P0 credential intelligence — secret-exposure redaction across every persistence surface.

THE LAW UNDER TEST
------------------
The raw secret never reaches model prompts, memory, logs, receipts, faults, bug reports or
diagnostics. Pattern redaction (vendor shapes) is the existing layer; this lane adds the
EXACT-MATCH registry — the moment the intake boundary holds a raw secret, the exact value is
registered, so even a format no vendor pattern knows (the odd custom-gateway key) is masked
out of the conversation-log choke point, credential diagnostics, and bug reports.

SABOTAGE: with the registry disabled (a mutation restoring pre-lane behavior), the exact
odd-format key must leak through `redact_secrets` and the named tests go RED — proving the
registry, not luck with entropy heuristics, is what stops the leak.
"""
from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import (  # noqa: F401 (fixtures resolve via module namespace)
    ODD_KEY,
    isolated_home,
    vault_home,
)


@pytest.fixture(autouse=True)
def clean_exact_registry():
    from core import secret_redaction

    secret_redaction.clear_exact_secrets_for_tests()
    yield
    secret_redaction.clear_exact_secrets_for_tests()


def test_registered_exact_secret_is_scrubbed_even_without_a_vendor_shape():
    from core.secret_redaction import contains_secret, redact_secrets, register_exact_secret

    text = f"the gateway key is {ODD_KEY} and it failed"
    register_exact_secret(ODD_KEY)
    scrubbed = redact_secrets(text)
    assert ODD_KEY not in scrubbed
    assert "[redacted" in scrubbed
    assert contains_secret(text) is True


def test_registry_is_bounded_and_ignores_nonsecrets():
    from core.secret_redaction import redact_secrets, register_exact_secret

    register_exact_secret("short")            # too short to be a secret — ignored
    first = "zk9-first-" + "a" * 40
    for i in range(64):                       # push past the bounded window
        register_exact_secret(f"zk9-{i:02d}-" + "b" * 40)
    register_exact_secret(first)
    scrubbed = redact_secrets(f"first {first} short short")
    assert first not in scrubbed, "the most recent registrations must still be scrubbed"
    # the bounded window keeps the process from growing an unbounded plaintext set
    from core import secret_redaction as sr

    assert len(sr._exact_secrets_for_tests()) <= 64


def test_export_diagnostics_never_leaks_the_odd_key(vault_home, monkeypatch):
    from core import credential_store
    from core.secret_redaction import register_exact_secret

    register_exact_secret(ODD_KEY)
    credential_store.store_credential("llm.cloud.provtest", ODD_KEY, label="Odd gateway")
    payload = credential_store.export_diagnostics()
    assert ODD_KEY not in json.dumps(payload)


def test_conversation_log_choke_point_masks_the_registered_secret(vault_home):
    from core import persistent_memory
    from core.secret_redaction import register_exact_secret

    register_exact_secret(ODD_KEY)
    persistent_memory.append_conversation_event(
        session_id="cred-intake-test",
        user_input=f"here is my gateway key {ODD_KEY} please verify it",
        assistant_output="sealed",
        source_context={},
    )
    log_text = persistent_memory.conversation_log_path().read_text(encoding="utf-8")
    assert ODD_KEY not in log_text
    assert "[redacted" in log_text


def test_bug_report_redaction_masks_the_registered_secret():
    from core.bug_report.redaction import redact_text
    from core.secret_redaction import register_exact_secret

    register_exact_secret(ODD_KEY)
    sanitized, _summary = redact_text(f"verification failed for key {ODD_KEY} at provider")
    assert ODD_KEY not in sanitized


def test_sabotage_without_the_registry_the_odd_key_leaks(monkeypatch):
    """SABOTAGE (redaction): simulate the pre-lane behavior — nothing registered. The
    odd-format key (chosen so no vendor pattern claims it) survives `redact_secrets`,
    which is exactly the leak the registry exists to close."""
    from core.secret_redaction import clear_exact_secrets_for_tests, redact_secrets, register_exact_secret

    text = f"the gateway key is {ODD_KEY} and it failed"
    register_exact_secret(ODD_KEY)
    assert ODD_KEY not in redact_secrets(text)
    clear_exact_secrets_for_tests()          # the mutation: registry wiped
    assert ODD_KEY in redact_secrets(text), "sabotage check: an unregistered odd key must leak"


def test_a_registered_public_identifier_keeps_its_key_shape_readable_and_nothing_else():
    from core.secret_redaction import (
        clear_exact_secrets_for_tests,
        contains_secret,
        redact_secrets,
        register_public_identifier,
    )

    clear_exact_secrets_for_tests()
    public = "3" + "m" * 63  # a key-shaped run the runtime minted (a tx signature)
    other = "4" + "n" * 63   # the same shape, never registered
    assert redact_secrets(public) == "[redacted-key]"  # before registration: masked like any key
    register_public_identifier(public)
    out = redact_secrets(f"sig {public} vs {other}")
    assert public in out and other not in out and out.count("[redacted-key]") == 1
    assert contains_secret(public) is False and contains_secret(other) is True
    register_public_identifier("short")  # below the key-shape floor: ignored, never a bypass
    assert redact_secrets("short") == "short"
    clear_exact_secrets_for_tests()
    assert redact_secrets(public) == "[redacted-key]"  # the registry is not permanent state
