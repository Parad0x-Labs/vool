"""Crypto Pilot, stage 5: the network and environment matrix through the product's doors, one served daemon.

SCRIPTED MODEL, SIMULATED CHAINS, real daemon. Every declared row is driven: a pilot wallet is created through the
setup doors, a chat request proposes a native transfer (the model's own tool call, resolved to the row by chain name,
asset and recipient shape), the quote is minted, the approval sends exactly once, and the receipt carries the row's own
explorer link. Test networks are driven first, then the owner switches to Mainnet through the environment door and the
Mainnet rows are driven; a transfer sent on one environment keeps its record when the owner switches away.

The browser sheet is proven row-independently in test_crypto_pilot_transfer_served.py; here the approve door carries
the same quote binding. Public testnet or mainnet acceptance is a separate gate: these chains are loopback nodes
answering as each row.
"""
from __future__ import annotations

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.wallet._rig import DEVNET_GENESIS, MAINNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
#: (network, chain key, asset, chat amount, fee model, chain id / genesis, explorer host, badge)
ROWS = [
    ("solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", "solana", "SOL", "0.0005", "svm", DEVNET_GENESIS, "https://solscan.io/tx/", "DEVNET"),
    ("eip155:84532", "base", "ETH", "0.001", "op_stack", 84532, "https://sepolia.basescan.org/tx/", "TESTNET"),
    ("eip155:11155111", "ethereum", "ETH", "0.002", "eip1559", 11155111, "https://sepolia.etherscan.io/tx/", "TESTNET"),
    ("eip155:97", "bnb", "BNB", "0.003", "bsc", 97, "https://testnet.bscscan.com/tx/", "TESTNET"),
    ("eip155:46630", "robinhood", "ETH", "0.004", "arbitrum", 46630, "https://explorer.testnet.chain.robinhood.com/tx/", "TESTNET"),
    ("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp", "solana", "SOL", "0.0006", "svm", MAINNET_GENESIS, "https://solscan.io/tx/", "MAINNET"),
    ("eip155:8453", "base", "ETH", "0.0011", "op_stack", 8453, "https://basescan.org/tx/", "MAINNET"),
    ("eip155:1", "ethereum", "ETH", "0.0021", "eip1559", 1, "https://etherscan.io/tx/", "MAINNET"),
    ("eip155:56", "bnb", "BNB", "0.0031", "bsc", 56, "https://bscscan.com/tx/", "MAINNET"),
    ("eip155:4663", "robinhood", "ETH", "0.0041", "arbitrum", 4663, "https://robinhoodchain.blockscout.com/tx/", "MAINNET"),
]


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(daemon, path: str) -> dict:
    with urlopen(daemon.base_url + path, timeout=30) as response:
        return json.load(response)


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _evm_recipient() -> str:
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("matrix") / "home"
    chains: dict[str, object] = {}
    urls: dict[str, str] = {}
    for network, _key, _asset, _amount, model, identity, _explorer, _badge in ROWS:
        if model == "svm":
            node = ScriptedRpc(genesis_hash=identity)
            node.real_signature = True
            node.status_keyed = True
        else:
            node = ScriptedEvmNativeChain(chain_id=int(identity), fee_model=model, l1_fee=40_000_000_000_000 if model == "op_stack" else 0)
        node.__enter__()
        chains[network] = node
        urls[network] = node.url
    provider = PromptRoutedProvider()
    provider.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WALLET_ENABLED": "1",
        "VOOL_WALLET_RPC_URLS": json.dumps(urls),
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
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
        yield daemon, chains, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        for node in chains.values():
            node.__exit__(None, None, None)


def _ready_pilot_wallet(daemon, network: str, label: str) -> dict:
    status, created = _door(daemon, "/api/wallet/setup/create", {
        "network": network, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"matrix-{uuid.uuid4().hex}", "label": label,
    })
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200 and (revealed.get("backup") or {}).get("ack_token"), (status, sorted(revealed.get("backup") or {}))
    ack_token = revealed["backup"]["ack_token"]
    revealed = None
    status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


def _drive_row(daemon, chains, provider, row, session: str) -> dict:
    network, chain_key, asset, amount, model, _identity, explorer, badge = row
    node = chains[network]
    wallet = _ready_pilot_wallet(daemon, network, f"{chain_key} {badge.lower()}")
    if model != "svm":
        node.fund(wallet["address"], 10**18)
    recipient = _sol_key() if model == "svm" else _evm_recipient()
    turn = daemon.chat(f"Send {amount} {asset} on {chain_key} to {recipient} for the {chain_key} {badge.lower()} proof", session_id=session, model=MODEL, mode="auto")
    text = str((turn.get("message") or {}).get("content") or "")
    assert "pending_approval" in text and "pay-" in text, text
    proposal_id = next(word.strip(".,:") for word in text.split() if word.startswith("pay-"))
    pending = {p["proposal_id"]: p for p in _get(daemon, "/api/wallet/status")["status"]["pending"]}
    assert pending[proposal_id]["network"] == network and pending[proposal_id]["pilot_transfer"] is True, pending[proposal_id]
    status, quoted = _door(daemon, "/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, quoted
    quote = quoted["quote"]
    fields = quote["fields"]
    assert (fields["network"], fields["badge"], fields["amount_human"], fields["to_address"], fields["from_address"]) == (network, badge, amount, recipient, wallet["address"])
    sends_before = len(node.sent)
    status, wrong = _door(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": "000000"})
    assert status != 200 and wrong.get("error") == "wallet_pin_invalid", wrong
    assert len(node.sent) == sends_before
    status, answer = _door(daemon, "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
    assert status == 200, answer
    transfer = answer["transfer"]
    assert transfer["state"] == "confirmed", transfer
    assert len(node.sent) == sends_before + 1
    assert transfer["explorer_url"].startswith(explorer) and transfer["tx_id"] in transfer["explorer_url"] and transfer["badge"] == badge
    from core.wallet import chains

    spec = chains.resolve_network(network)
    assert transfer["display_symbol"] == (spec.native_display_symbol or asset) and transfer["amount_human"] == amount  # BSC testnet shows tBNB
    return {"proposal_id": proposal_id, "transfer": transfer, "network": network}


def test_every_row_sends_once_and_settles_with_its_own_explorer_link_in_both_environments(served):
    daemon, chains, provider = served
    status, switched = _door(daemon, "/api/wallet/environment", {"environment": "testnet"})
    assert status == 200, switched
    results = {}
    for row in ROWS[:5]:
        results[row[0]] = _drive_row(daemon, chains, provider, row, session=f"matrix-{row[1]}-testnet")
    status, switched = _door(daemon, "/api/wallet/environment", {"environment": "mainnet"})
    assert status == 200, switched
    for row in ROWS[5:]:
        results[row[0]] = _drive_row(daemon, chains, provider, row, session=f"matrix-{row[1]}-mainnet")
    # every record kept, each on its own row, whatever the environment shows now
    listed = {t["proposal_id"]: t for t in _get(daemon, "/api/wallet/transfers?limit=50")["transfers"]}
    for network, result in results.items():
        assert listed[result["proposal_id"]]["network"] == network and listed[result["proposal_id"]]["state"] == "confirmed"
    assert len(results) == len(ROWS)
    prompts = " ".join(str(call.get("prompt") or "") for call in provider.calls)
    assert PIN not in prompts
