"""The private-key reveal door is closed, not consent-gated.

`reveal_wallet_secret_key_base58` used to decrypt the legacy key after a live OS consent prompt.
The whole subject of the old tests (a 64-byte secret after consent, a PermissionError on decline,
the gate running before decryption, the reason reaching the prompt) is the removed authority.
What replaces them: the reveal refuses with ``wallet_export_refused`` BEFORE any consent prompt is
consulted, whatever the prompt would have answered, files a fault receipt plus a security
observation, and leaves no key file behind.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.vool_wallet import WALLET_FILENAME, VoolWallet, reveal_wallet_secret_key_base58
from core.os_consent_gate import set_consent_override_for_tests
from core.wallet.errors import WalletFault

EXPORT = "wallet_export_refused"
_B58_KEY = re.compile(r"[1-9A-HJ-NP-Za-km-z]{80,}")


@pytest.fixture(autouse=True)
def _clear_override():
    set_consent_override_for_tests(None)
    yield
    set_consent_override_for_tests(None)


def _no_key_file(home: Path) -> None:
    assert not list(home.rglob(WALLET_FILENAME))
    assert not home.exists(), "a refusal must not create the runtime home"


def _assert_export_refusal(exc: WalletFault, surface: str) -> None:
    assert exc.code == EXPORT
    assert exc.fault_id.startswith("fault-")
    assert exc.context["surface"] == surface and exc.context["reason"] == "no_export_door"
    assert _B58_KEY.search(exc.user_message) is None
    from core.faults.recorder import fault_by_id

    record = fault_by_id(exc.fault_id)
    assert record is not None and record.code == EXPORT


@pytest.mark.parametrize("answer", [True, False])
def test_reveal_refuses_whatever_the_prompt_would_answer(tmp_path: Path, answer: bool) -> None:
    home = tmp_path / "runtime"
    prompts: list[str] = []

    def _prompt(reason: str) -> bool:
        prompts.append(reason)
        return answer

    set_consent_override_for_tests(_prompt)
    with pytest.raises(WalletFault) as exc:
        reveal_wallet_secret_key_base58(runtime_home=str(home))
    _assert_export_refusal(exc.value, "vool_wallet.reveal_wallet_secret_key_base58")
    assert prompts == [], "the refusal precedes the consent gate"
    _no_key_file(home)


def test_reveal_never_consults_the_gate_or_the_legacy_loader(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "runtime"

    def _must_not_be_called(*_a, **_k):
        raise AssertionError("the closed export door must not reach consent or key loading")

    set_consent_override_for_tests(_must_not_be_called)
    import core.vool_wallet as wallet_mod
    import core.os_consent_gate as gate_mod

    monkeypatch.setattr(wallet_mod, "get_or_create_wallet", _must_not_be_called)
    monkeypatch.setattr(gate_mod, "require_os_user_consent", _must_not_be_called)
    with pytest.raises(WalletFault) as exc:
        reveal_wallet_secret_key_base58(runtime_home=str(home), reason="Back up wallet for cold storage")
    assert exc.value.code == EXPORT
    _no_key_file(home)


def test_reveal_reason_never_reaches_a_prompt_and_never_enters_the_receipt(tmp_path: Path) -> None:
    seen: list[str] = []
    set_consent_override_for_tests(lambda reason: seen.append(reason) or True)
    with pytest.raises(WalletFault) as exc:
        reveal_wallet_secret_key_base58(runtime_home=str(tmp_path / "runtime"), reason="Back up wallet for cold storage")
    assert seen == []
    assert "cold storage" not in str(exc.value.to_dict())


def test_reveal_with_a_stale_legacy_file_leaves_it_untouched_and_unread(tmp_path: Path) -> None:
    home = tmp_path / "runtime"
    stale = home / "data" / "keys" / WALLET_FILENAME
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'{"version": 1, "pubkey": "stale", "ciphertext_b64": "AAAA"}')
    before = stale.stat()
    set_consent_override_for_tests(lambda reason: True)
    with pytest.raises(WalletFault) as exc:
        reveal_wallet_secret_key_base58(runtime_home=str(home))
    assert exc.value.code == EXPORT
    assert stale.read_bytes() == b'{"version": 1, "pubkey": "stale", "ciphertext_b64": "AAAA"}'
    assert stale.stat().st_mtime == before.st_mtime


def test_instance_export_refuses_the_same_way(tmp_path: Path) -> None:
    set_consent_override_for_tests(lambda reason: True)
    with pytest.raises(WalletFault) as exc:
        VoolWallet(runtime_home=tmp_path / "runtime").export_secret_key_base58()
    _assert_export_refusal(exc.value, "vool_wallet.export_secret_key_base58")
    from core.security_events.store import list_security_events

    assert "SEC_WALLET_EXPORT_REFUSED" in [e.sec_code for e in list_security_events(limit=100)]
    _no_key_file(tmp_path / "runtime")
