"""A missing EVM stack is a typed refusal, never an ImportError.

``core/wallet/lifecycle.py`` imports ``core.wallet.evm`` unconditionally, and
``evm`` used to import ``eth_abi``/``eth_utils`` at module level. On a machine
without those wheels the whole wallet package was unimportable, so the Solana
lane, custody, spend ceilings and the mainnet-impossible guard raised
``ModuleNotFoundError`` — a plain-text HTTP 500 on the served door and a
collapsed model turn, not a refusal. 95 of 182 wallet tests were down on it.

Absence here is REAL, not mocked. ``block_evm_imports`` installs a
``sys.meta_path`` finder that refuses those module names and evicts them from
``sys.modules``, so ``importlib.util.find_spec`` genuinely returns ``None`` and
the production guard runs against a true absence. Patching
``missing_evm_dependencies`` instead would have replaced the seam under test
with a double, and the proof would hold whatever the code did.

These tests therefore assert the same thing on a machine that HAS the EVM
stack and one that does not.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from core.wallet.errors import WalletFault

_BLOCKED = ("eth_abi", "eth_utils", "eth_account")
#: modules that cache an EVM import decision and must be re-imported under the block
_EVM_DEPENDENT_MODULES = ("core.wallet.evm", "core.wallet.lifecycle", "core.wallet.x402_v2")


class _RefusingFinder:
    """A real import-system finder that makes the EVM wheels genuinely absent."""

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in _BLOCKED:
            raise ModuleNotFoundError(f"No module named {root!r}", name=root)
        return None


@pytest.fixture()
def block_evm_imports():
    """Make the EVM stack absent for the duration of one test, on any machine.

    Only the three modules whose IMPORT behaviour depends on the EVM wheels are
    evicted and re-imported. Evicting the whole ``core.wallet`` package instead
    re-creates ``WalletFault`` and every other class in it, and then two things
    break at once: this module's collection-time ``WalletFault`` no longer
    matches what the fresh modules raise, and ``custody``'s ``except
    WalletFault`` stops catching ``chains``' — turning a healthy
    ``require_network("eip155:84532")`` into a spurious refusal. Same class
    identity trap the sabotage harness hit; the fix is to move as little as
    possible.

    Teardown restores the original module objects.
    """
    saved = {
        name: sys.modules[name]
        for name in (*_EVM_DEPENDENT_MODULES, *(n for n in sys.modules if n.split(".", 1)[0] in _BLOCKED))
        if name in sys.modules
    }
    finder = _RefusingFinder()
    original_meta_path = list(sys.meta_path)
    sys.meta_path.insert(0, finder)
    try:
        for name in list(saved):
            sys.modules.pop(name, None)
        evm = importlib.import_module("core.wallet.evm")
        assert evm.missing_evm_dependencies() == _BLOCKED, evm.missing_evm_dependencies()
        assert evm.evm_dependencies_available() is False
        yield evm
    finally:
        sys.meta_path[:] = original_meta_path
        for name in _EVM_DEPENDENT_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# ── the import boundary ──────────────────────────────────────────────────────


def test_the_wallet_package_imports_with_no_evm_stack(block_evm_imports) -> None:
    """Every wallet module imports. This is the regression that took 95 tests down."""
    for name in _EVM_DEPENDENT_MODULES:
        module = importlib.import_module(name)
        assert module is not None, name
    # lifecycle's public entry points exist and are callable objects, not stubs.
    lifecycle = importlib.import_module("core.wallet.lifecycle")
    for attr in ("PaymentLifecycle", "RpcClient", "default_lifecycle"):
        assert hasattr(lifecycle, attr), attr


def test_app_boot_pulls_in_no_wallet_module_at_all(block_evm_imports) -> None:
    """Boot must not depend on the wallet lane, with or without the EVM stack.

    This one has to evict the WHOLE wallet package to ask its question, so it
    restores it too: leaving the package unloaded re-creates ``WalletFault`` for
    every later test in this file, and a correct raise then sails through
    ``pytest.raises``.
    """
    names = [n for n in sys.modules if n == "core.wallet" or n.startswith("core.wallet.")]
    saved = {name: sys.modules[name] for name in names}
    try:
        for name in names:
            sys.modules.pop(name, None)
        importlib.import_module("core.web.api.service")
        pulled = sorted(
            n for n in sys.modules if n == "core.wallet" or n.startswith("core.wallet.")
        )
        assert pulled == [], f"app boot imported wallet modules: {pulled}"
    finally:
        for name in [
            n for n in sys.modules if n == "core.wallet" or n.startswith("core.wallet.")
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# ── EVM-only operations are typed, not ImportError ───────────────────────────


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        (
            "eip712_domain_separator",
            lambda evm: evm.domain_separator(
                chain_id=84532,
                token="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                token_name="USDC",
                token_version="2",
            ),
        ),
        (
            "eip712_typed_data_digest",
            lambda evm: evm.typed_data_digest(
                {
                    "domain": {
                        "chainId": 84532,
                        "verifyingContract": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        "name": "USDC",
                        "version": "2",
                    },
                    "message": {
                        "from": "0x1111111111111111111111111111111111111111",
                        "to": "0x2222222222222222222222222222222222222222",
                        "value": 1,
                        "validAfter": 0,
                        "validBefore": 1,
                        "nonce": "0x" + "00" * 32,
                    },
                }
            ),
        ),
        (
            "eip712_recover_signer",
            lambda evm: evm.recover_authorization_signer({"domain": {}, "message": {}}, "0x" + "11" * 65),
        ),
        (
            "evm_address_for_private_key",
            lambda evm: evm.address_for_private_key("0x" + "22" * 32),
        ),
    ],
)
def test_every_evm_only_operation_returns_a_typed_dependency_refusal(
    block_evm_imports, operation, call
) -> None:
    evm = block_evm_imports
    with pytest.raises(WalletFault) as caught:
        call(evm)
    fault = caught.value
    assert fault.code == "wallet_dependency_unavailable", fault.code
    # The refusal names WHAT is missing and WHICH operation stayed undone — an
    # operator can act on it without reading a traceback.
    assert fault.context.get("reason") == "missing_dependency", fault.context
    assert fault.context.get("operation") == operation, fault.context
    for name in _BLOCKED:
        assert name in str(fault.context.get("missing") or ""), fault.context
    assert "eth" in fault.user_message.lower() or "ethereum" in fault.user_message.lower()


def test_the_refusal_is_raised_before_any_key_material_is_touched(block_evm_imports) -> None:
    """No hidden fallback: the refusal precedes signing, and nothing is produced."""
    evm = block_evm_imports
    with pytest.raises(WalletFault) as caught:
        evm.sign_authorization("0x" + "33" * 32, {"domain": {}, "message": {}})
    assert caught.value.code == "wallet_dependency_unavailable"
    # And no stand-in signer, no generated key, no broadcast surface appeared in
    # place of the real one.
    assert not hasattr(evm, "_fallback_sign")
    assert not hasattr(evm, "_software_signer")


# ── the non-EVM lanes are untouched ──────────────────────────────────────────


def test_mainnet_stays_impossible_without_the_evm_stack(block_evm_imports) -> None:
    config = importlib.import_module("core.wallet.config")
    assert config.mainnet_enabled() is False
    assert config.network_allowed("solana:mainnet") is False
    assert config.network_allowed("eip155:137") is False
    assert config.rpc_url_allowed("https://api.mainnet-beta.solana.com") is False
    assert config.network_allowed(config.NETWORK_SOLANA_DEVNET_CAIP2) is True


def test_the_construction_time_mainnet_refusal_is_reachable(block_evm_imports) -> None:
    """RpcClient refuses mainnet at construction — the guard that used to be
    unreachable because its module could not import."""
    lifecycle = importlib.import_module("core.wallet.lifecycle")
    with pytest.raises(WalletFault) as caught:
        lifecycle.RpcClient("https://api.mainnet-beta.solana.com", network="solana:mainnet")
    assert caught.value.code == "wallet_network_disabled", caught.value.code


def test_chain_identity_and_custody_policy_answer_without_the_evm_stack(block_evm_imports) -> None:
    chains = importlib.import_module("core.wallet.chains")
    custody = importlib.import_module("core.wallet.custody")
    for network in ("eip155:84532", "eip155:11155111", "eip155:97"):
        spec = chains.resolve_network(network)
        assert spec is not None and getattr(spec, "testnet", False) is True, network
    # EVM chain IDENTITY is dependency-free; only ABI encoding and recovery are not.
    assert custody.require_network("eip155:84532") == "eip155:84532"
    with pytest.raises(WalletFault):
        custody.require_network("eip155:137")


def test_wallet_status_reads_without_the_evm_stack(block_evm_imports) -> None:
    status = importlib.import_module("core.wallet.status")
    payload = status.wallet_status()
    assert isinstance(payload, dict) and payload, payload


# ── the guard is load-bearing ────────────────────────────────────────────────


def test_sabotage_removing_the_dependency_guard_restores_the_untyped_crash(
    block_evm_imports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neutralize require_evm_dependencies and the ImportError comes straight back.

    A guard whose removal changes nothing is not load-bearing. With it disabled,
    the very call that returns a typed refusal above raises ModuleNotFoundError —
    which no wallet caller handles, because they all catch WalletFault.
    """
    evm = block_evm_imports
    monkeypatch.setattr(evm, "require_evm_dependencies", lambda *a, **k: None)
    with pytest.raises(ModuleNotFoundError) as caught:
        evm.domain_separator(
            chain_id=84532,
            token="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            token_name="USDC",
            token_version="2",
        )
    assert caught.value.name in _BLOCKED, caught.value.name
    assert not isinstance(caught.value, WalletFault)
