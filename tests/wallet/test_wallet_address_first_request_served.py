"""A payment asked for with the recipient first and no "to" ("pay <address> 1 USDC on base") reaches the wallet.

SCRIPTED MODEL, SIMULATED CHAIN, real product, default runtime flags: the daemon is ``apps.vool_api_server`` in its
own process and home; the model is the prompt-routed stand-in, which calls ``wallet.propose`` only when the runtime
offered it; the chain is the loopback Solana-dialect node answering as Devnet. Before the demand rule read this word
order, the turn offered no wallet tool, so the request never reached the wallet at all.
"""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
EVM_TO = "0x5555555555555555555555555555555555555555"


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _pending(daemon) -> dict[str, dict]:
    with urlopen(daemon.base_url + "/api/wallet/status", timeout=30) as response:
        return {row["proposal_id"]: row for row in json.load(response)["status"]["pending"]}


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("address-first") / "home"
    chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    provider = PromptRoutedProvider()
    chain.__enter__()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_WALLET_ENABLED": "1",
        "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
        "VOOL_WALLET_TESTNET_RPC_URL": chain.url,
        "OLLAMA_HOST": provider.base_url,
        "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
        "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        status, created = _door(daemon, "/api/wallet/setup/create", {
            "network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
            "creation_key": f"address-first-{uuid.uuid4().hex}", "label": "Address first",
        })
        assert status == 200, created
        wallet_id = created["setup"]["wallet_id"]
        status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
        ack_token = (revealed.get("backup") or {}).get("ack_token")
        assert status == 200 and ack_token, (status, sorted(revealed.get("backup") or {}))  # key names only
        revealed = None
        status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
        assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
        yield daemon, chain, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        chain.__exit__(None, None, None)


def _offered(provider, message: str) -> list[str]:
    """The tools the runtime offered the model on the turn that carried exactly this message."""
    offers = [call["tools"] for call in provider.calls if call["last_user"] == message and call["tools"]]
    assert offers, f"the model was offered no tools for {message!r}"
    return offers[0]


def test_a_payment_with_the_recipient_first_becomes_one_pending_proposal(served):
    daemon, chain, provider = served
    recipient = _sol_key()
    message = f"pay {recipient} 0.00064 SOL on solana for the hosting bill"
    before = set(_pending(daemon))
    turn = daemon.chat(message, session_id="address-first-1", model=MODEL, mode="auto")
    assert "wallet__propose" in _offered(provider, message)
    created = set(_pending(daemon)) - before
    assert len(created) == 1, (created, (turn.get("message") or {}).get("content"))
    row = _pending(daemon)[created.pop()]
    assert (row["destination"], int(row["amount_minor"]), row["asset"], row["network"], row["state"]) == (recipient, 640_000, "SOL", SOLANA_DEVNET, "pending_approval"), row
    assert chain.send_count() == 0, "a proposal sends nothing"


def test_a_usdc_payment_on_base_with_the_recipient_first_reaches_the_wallet(served):
    """The phrasing as users type it: the wallet decides. This home holds only a Solana Devnet account, so the wallet
    finds no account for Base (wallet_not_found) and nothing is proposed, but the request reached the wallet."""
    daemon, chain, provider = served
    message = f"pay {EVM_TO} 1 USDC on base"
    before = set(_pending(daemon))
    turn = daemon.chat(message, session_id="address-first-2", model=MODEL, mode="auto")
    assert "wallet__propose" in _offered(provider, message)
    answer = str((turn.get("message") or {}).get("content") or "")
    assert "That wallet, proposal or destination was not recognised" in answer and "Nothing was signed and nothing was sent." in answer, answer
    assert set(_pending(daemon)) == before and chain.send_count() == 0
