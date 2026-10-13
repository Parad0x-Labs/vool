import hashlib

import pytest

from tests.wallet._rig import rpc, wallet_env
from tests.wallet._rig_evm import evm_rig


@pytest.fixture(autouse=True)
def _status_privacy_mnemonic(request, monkeypatch):
    """Keep the lexical privacy assertion independent of random public-address spelling.

    A valid base58 address can contain "pin". Only this test's BIP39 input is fixed;
    custody, encryption randomness, status and redaction still use their real implementations.
    The collision and actual credential material are covered separately in the lifecycle pack.
    """
    if request.node.nodeid != "tests/wallet/test_wallet_lifecycle.py::test_status_surface_is_public_safe_and_counts_pending_approvals":
        return
    from core.wallet import mnemonic

    def generate_mnemonic(*, strength_bits=128):
        assert strength_bits == 128
        entropy = hashlib.sha256(b"wallet-status-synthetic-entropy:0").digest()[:16]
        return mnemonic.entropy_to_mnemonic(entropy)

    monkeypatch.setattr(mnemonic, "generate_mnemonic", generate_mnemonic)


@pytest.fixture(autouse=True)
def _cold_chain_identity_cache():
    """Chain identity is proven per endpoint and cached in process memory: every wallet test starts cold,
    so no test inherits another test's proof of an endpoint it never probed."""
    from core.wallet import chains

    chains.invalidate_chain_identity()
    yield
    chains.invalidate_chain_identity()
