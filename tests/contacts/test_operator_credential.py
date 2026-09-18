"""The operator credential: the PIN or password that protects changes to saved contacts.

What executes: the production credential authority (core.operator_credential) on the suite's migrated SQLite database
(storage.migrations version 8), the production device-secret derivation (network.signer.derive_local_secret) and the
production OS consent gate with its test override. No real prompt, and the one PIN and password are synthetic.
"""
from __future__ import annotations

import pytest

from core import os_consent_gate
from core.operator_credential import CredentialError
from core.operator_credential import authority as credential

PIN = "482913"
PASSWORD = "correct-horse-staple"


@pytest.fixture(autouse=True)
def _fresh():
    credential.reset_for_tests()
    yield
    credential.reset_for_tests()


def _enroll(secret: str = PIN, kind: str = "pin") -> dict:
    os_consent_gate.set_consent_override_for_tests(lambda _reason: True)
    try:
        return credential.enroll(secret, kind=kind)
    finally:
        os_consent_gate.set_consent_override_for_tests(None)


def test_before_enrollment_verify_refuses_and_points_at_setup() -> None:
    assert credential.status()["enrolled"] is False
    with pytest.raises(CredentialError) as refused:
        credential.verify(PIN)
    assert refused.value.reason == "credential_not_enrolled" and refused.value.status == 409
    assert credential.SETUP_PATH in refused.value.message


def test_enrolling_needs_the_system_prompt_and_never_stores_the_secret() -> None:
    os_consent_gate.set_consent_override_for_tests(lambda _reason: False)
    try:
        with pytest.raises(CredentialError) as declined:
            credential.enroll(PIN, kind="pin")
        assert declined.value.reason == "consent_denied" and not credential.status()["enrolled"]
    finally:
        os_consent_gate.set_consent_override_for_tests(None)
    enrolled = _enroll()
    assert enrolled["status"] == "enrolled" and enrolled["generation"] >= 1
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = dict(conn.execute("SELECT * FROM operator_credentials WHERE credential_id = 'operator'").fetchone())
    finally:
        conn.close()
    stored = "|".join(str(v) for v in row.values())
    assert PIN not in stored and enrolled["recovery_code"] not in stored, "no PIN or recovery code is stored in the clear"
    assert row["kdf"] == credential.KDF_NAME
    verification = credential.verify(PIN)
    assert (verification.principal, verification.credential_generation, verification.scope) == (credential.PRINCIPAL, enrolled["generation"], credential.SCOPE_CONTACTS)


def test_consent_unavailable_is_a_setup_path_not_a_silent_grant() -> None:
    def raise_unavailable(_reason: str) -> bool:
        raise os_consent_gate.ConsentUnavailableError("no mechanism")

    os_consent_gate.set_consent_override_for_tests(raise_unavailable)
    try:
        with pytest.raises(CredentialError) as refused:
            credential.enroll(PIN, kind="pin")
    finally:
        os_consent_gate.set_consent_override_for_tests(None)
    assert refused.value.reason == "consent_unavailable" and refused.value.details.get("setup_path") == credential.SETUP_PATH
    assert not credential.status()["enrolled"]


def test_a_wrong_secret_never_verifies_and_a_right_one_clears_the_count() -> None:
    generation = _enroll()["generation"]
    for _ in range(3):
        with pytest.raises(CredentialError) as wrong:
            credential.verify("000000")
        assert wrong.value.reason == "credential_invalid" and wrong.value.status == 403
    assert credential.verify(PIN).credential_generation == generation
    # a success clears the credential-scope count: the next wrong attempt starts from zero again
    with pytest.raises(CredentialError) as after:
        credential.verify("111111")
    assert after.value.details.get("attempts_before_lock") == credential.lock_policy()["credential_lock_after"] - 1


def test_the_durable_throttle_locks_after_the_wallet_threshold_and_survives_a_reload() -> None:
    _enroll()
    threshold = credential.lock_policy()["credential_lock_after"]
    for _ in range(threshold):
        with pytest.raises(CredentialError):
            credential.verify("000000")
    with pytest.raises(CredentialError) as locked:
        credential.verify(PIN)  # even the right PIN is refused while the scope is locked
    assert locked.value.reason == "credential_throttled" and locked.value.status == 429 and locked.value.details["retry_after_seconds"] >= 1
    # the lock is a database row: a fresh status read (as a restart would) still reports it
    assert credential.status()["retry_after_seconds"] >= 1


def test_change_needs_the_current_secret_and_raises_the_generation() -> None:
    base = _enroll()["generation"]
    with pytest.raises(CredentialError):
        credential.change("000000", "918273", kind="pin")
    changed = _change("918273")
    assert changed["generation"] == base + 1
    with pytest.raises(CredentialError):
        credential.verify(PIN)  # the old PIN no longer works
    assert credential.verify("918273").credential_generation == base + 1


def _change(new: str) -> dict:
    os_consent_gate.set_consent_override_for_tests(lambda _reason: True)
    try:
        return credential.change(PIN, new, kind="pin")
    finally:
        os_consent_gate.set_consent_override_for_tests(None)


def test_reset_needs_the_recovery_code_and_a_new_code_replaces_it() -> None:
    recovery = _enroll()["recovery_code"]
    with pytest.raises(CredentialError) as wrong:
        _reset("00000-00000-00000-00000", "639182")  # a valid new PIN, so the recovery code is what fails
    assert wrong.value.reason == "recovery_invalid"
    base = credential.status()["generation"]
    done = _reset(recovery, "639182")
    assert done["status"] == "reset" and done["generation"] == base + 1 and done["recovery_code"] != recovery
    assert credential.verify("639182").credential_generation == base + 1
    # the used recovery code no longer works; the new one does
    with pytest.raises(CredentialError):
        _reset(recovery, "271833")
    _reset(done["recovery_code"], "271833")
    assert credential.verify("271833").credential_generation == base + 2


def _reset(code: str, new: str) -> dict:
    os_consent_gate.set_consent_override_for_tests(lambda _reason: True)
    try:
        return credential.reset_with_recovery(code, new, kind="pin")
    finally:
        os_consent_gate.set_consent_override_for_tests(None)


def test_weak_pins_and_wrong_lengths_are_refused_before_any_consent() -> None:
    asked: list[str] = []
    os_consent_gate.set_consent_override_for_tests(lambda reason: asked.append(reason) or True)
    try:
        for bad in ("123", "1234567890123", "111111", "123456", "abcdef"):
            with pytest.raises(CredentialError) as refused:
                credential.enroll(bad, kind="pin")
            assert refused.value.reason == "credential_weak", bad
        with pytest.raises(CredentialError):
            credential.enroll("short", kind="password")
    finally:
        os_consent_gate.set_consent_override_for_tests(None)
    assert asked == [], "a weak secret is refused before the operating system is asked"


def test_a_password_credential_works_the_same_way() -> None:
    _enroll(PASSWORD, kind="password")
    assert credential.status()["kind"] == "password"
    with pytest.raises(CredentialError):
        credential.verify("wrong-password-entirely")
    assert credential.verify(PASSWORD).scope == credential.SCOPE_CONTACTS


def test_the_credential_scope_is_contacts_only() -> None:
    _enroll()
    with pytest.raises(CredentialError) as refused:
        credential.verify(PIN, scope="wallet:anything")
    assert refused.value.reason == "scope_not_granted" and refused.value.status == 403
