"""The wallet's own tx hashes stay readable at the REAL status-render seam.

The EVM key-shape rule masks every UNREGISTERED 0x+64hex run; the wallet's publication
answer is that it registers each hash it renders (publish_identifier at the render seam).
That positive is proven here against the actual model-facing surface —
``_wallet_model_tool("wallet.status")`` renders the last receipt's tx_signature into free
text — with a synthetic receipt in a disposable runtime home. No signing, no network, no
eth-account dependency: the render path reads the store and publishes.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from core import runtime_paths
from core.secret_redaction import redact_secrets

TX_HASH = "0x4c0883a694529ec3b3d6d5f0a2e7d9b41c2f8a6d3e5c7b9a1f4d2e8c6b0a3d5f"
UNREGISTERED = "0X" + "2" * 64  # same shape, no provenance: masked even next to the real one


@contextmanager
def _scoped_registry():
    """Snapshot both bounded registries and restore exactly them: the fresh-process
    simulations below must not wipe registrations belonging to other tests, and the hashes
    this file's renders publish must not outlive it either."""
    from core import secret_redaction as _sr

    secrets_before = dict(_sr._exact_secrets)
    public_before = dict(_sr._public_identifiers)
    try:
        yield
    finally:
        with _sr._exact_lock:
            _sr._exact_secrets.clear()
            _sr._exact_secrets.update(secrets_before)
            _sr._public_identifiers.clear()
            _sr._public_identifiers.update(public_before)


@pytest.fixture()
def seeded_home(tmp_path, monkeypatch):
    """A disposable runtime home holding ONE confirmed receipt for a synthetic proposal."""
    from core.wallet import receipts

    prior_override = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(tmp_path / "home")
    try:
        proposal = SimpleNamespace(
            proposal_id="pr-render-1",
            wallet_id="w-render",
            network="eip155:84532",
            asset="USDC",
            amount_minor=5,
            destination="0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            origin="x402",
        )
        receipts.record_receipt(proposal, state="confirmed", tx_signature=TX_HASH)
        monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
        yield
    finally:
        runtime_paths.configure_runtime_home(prior_override)


def _render_status() -> str:
    from core.runtime_execution_tools import _wallet_model_tool

    result = _wallet_model_tool("wallet.status", {}, None)
    assert result.ok, result.response_text
    return result.response_text


def test_the_status_render_publishes_the_hash_and_redaction_keeps_it_readable(seeded_home):
    with _scoped_registry():  # fresh-process simulation: no registrations carried in
        text = _render_status()
        assert f"tx_signature={TX_HASH}" in text  # the seam rendered the stored hash
        masked = redact_secrets(f"{text} also seen {UNREGISTERED}")
        assert TX_HASH in masked  # registered at render: the wallet's own hash survives
        assert UNREGISTERED not in masked and "[redacted-key]" in masked  # the lookalike does not


def test_a_fresh_process_re_render_re_registers_the_hash_from_the_trusted_store(seeded_home):
    """The registry is process-local, so the rehydration path is the seam itself: every render
    re-publishes from the receipt store the wallet owns. After a registry reset (a restart),
    the next render makes the hash readable again — no cross-process state is relied on."""
    with _scoped_registry():
        assert TX_HASH not in redact_secrets(f"x {TX_HASH}")  # pre-registration: masked
        text = _render_status()  # the "restart": re-render from the store
        assert TX_HASH in text
        assert TX_HASH in redact_secrets(text)  # re-registered by the render itself
