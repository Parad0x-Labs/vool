"""DEVNET proof for the wallet lane: one real payment on Solana devnet from a freshly generated,
ephemeral pocket wallet funded by the public faucet, through the production lifecycle.

Run (worktree root, wallet-enabled, ephemeral VOOL_HOME the script creates and destroys):

    VOOL_WALLET_ENABLED=1 .venv/bin/python -m ops.wallet_devnet_proof --out VOOL-DELIVERY/evidence/<lane>/07_devnet_proof.json

What is recorded: network, public transaction signature, explorer URL, status, the destination,
the amount, and the timings. What is never recorded: the recovery phrase, the PIN, the sealed
key. The ephemeral home (the only place the sealed key ever lived) is deleted at the end,
funded or not. When the faucet refuses (rate limit, dry), the record says BLOCKED with the
exact faucet error and nothing else is claimed. Mainnet is impossible by construction
(core.wallet.config.ALLOWED_NETWORKS has no mainnet entry).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEVNET_RPC = "https://api.devnet.solana.com"
EXPLORER = "https://explorer.solana.com/tx/{sig}?cluster=devnet"
AIRDROP_LAMPORTS = 20_000_000  # 0.02 SOL of valueless devnet SOL
PAYMENT_LAMPORTS = 1_000        # 0.000001 SOL back to the system program (a canonical sink; no counterparty)
DESTINATION = "11111111111111111111111111111111"


def _rpc(method: str, params: list[Any]) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(DEVNET_RPC, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"error": {"code": exc.code, "message": (exc.read() or b"")[:300].decode("utf-8", "replace")}}


def _fund(pubkey: str, *, attempts: int = 3) -> tuple[bool, str]:
    last = ""
    for attempt in range(1, attempts + 1):
        answer = _rpc("requestAirdrop", [pubkey, AIRDROP_LAMPORTS])
        if "result" in answer:
            for _ in range(60):
                balance = int(((_rpc("getBalance", [pubkey]).get("result") or {}).get("value")) or 0)
                if balance > 0:
                    return True, f"airdrop {answer['result']} landed: balance {balance} lamports"
                time.sleep(1.0)
            last = "airdrop accepted but never landed"
        else:
            last = json.dumps(answer.get("error"))[:300]
        time.sleep(5.0 * attempt)
    return False, last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="where to write the proof record (json)")
    args = parser.parse_args(argv)
    if os.environ.get("VOOL_WALLET_ENABLED") != "1":
        print("set VOOL_WALLET_ENABLED=1", file=sys.stderr)
        return 2
    # this proof drives the Solana Devnet row, so its run operates on Test networks
    os.environ["VOOL_WALLET_NETWORK_ENVIRONMENT"] = "testnet"
    home = Path(tempfile.mkdtemp(prefix="vool-devnet-proof-"))
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_WALLET_TESTNET_RPC_URL"] = DEVNET_RPC
    os.environ.setdefault("VOOL_KEY_STORAGE_MODE", "file")
    os.environ.setdefault("VOOL_KEY_PASSPHRASE", "devnet-proof-ephemeral")
    os.environ.setdefault("VOOL_CREDENTIAL_STORE", "vault")
    record: dict[str, Any] = {"schema": "vool.wallet.devnet_proof.v1", "started_at": datetime.now(timezone.utc).isoformat(), "network": "solana-devnet", "rpc": DEVNET_RPC, "status": "BLOCKED", "mainnet_possible": False}
    try:
        from core import runtime_paths

        runtime_paths.configure_runtime_home(home)
        from storage.db import configure_default_db_path, reset_default_connection
        from storage.migrations import run_migrations

        configure_default_db_path(home / "devnet-proof.db")
        reset_default_connection()
        run_migrations()
        from core.wallet import approval, config, custody, lifecycle, proposals

        record["allowed_networks"] = list(config.ALLOWED_NETWORKS)
        pin = f"{int.from_bytes(os.urandom(3), 'big') % 1_000_000:06d}"
        created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=pin, label="devnet-proof-ephemeral")
        del created  # the phrase is dropped here, unread: this wallet is never meant to be recovered
        profile = custody.default_wallet()
        record["ephemeral_public_key"] = profile.public_key
        funded, note = _fund(profile.public_key)
        record["faucet"] = note
        if not funded:
            record["status"] = "BLOCKED"
            record["blocked_reason"] = f"devnet faucet did not fund the ephemeral wallet: {note}"
            return 3
        proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=PAYMENT_LAMPORTS, asset="SOL", origin=proposals.ORIGIN_USER, memo="devnet proof")
        engine = lifecycle.default_lifecycle()
        prepared = engine.prepare(proposal.proposal_id)
        record["simulation"] = prepared.simulation
        started = time.monotonic()
        receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(pin))
        record.update({
            "status": "DEVNET-PROVEN" if receipt.state == proposals.STATE_CONFIRMED else receipt.state.upper(),
            "tx_signature": receipt.tx_signature, "explorer": EXPLORER.format(sig=receipt.tx_signature), "state": receipt.state,
            "destination": DESTINATION, "amount_lamports": PAYMENT_LAMPORTS, "elapsed_seconds": round(time.monotonic() - started, 2),
            "chain_status": _rpc("getSignatureStatuses", [[receipt.tx_signature], {"searchTransactionHistory": True}]).get("result"),
            "events": [e["state"] for e in proposals.proposal_events(proposal.proposal_id)],
        })
        return 0
    except Exception as exc:
        record["status"] = "FAILED"
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return 4
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        shutil.rmtree(home, ignore_errors=True)  # the sealed key dies with the ephemeral home
        record["ephemeral_home_destroyed"] = not home.exists()
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
