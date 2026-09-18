"""The external-funding devnet proof lane, and the commitment alignment it exposed.

Two things are pinned here. First, ``core.wallet.lifecycle.RpcClient`` must evaluate simulation
and preflight against the SAME bank its blockhash came from; leaving preflight on the RPC default
(finalized) asks a bank behind the tip about a blockhash it has not seen, and a healthy payment is
refused as BlockhashNotFound. Second, :mod:`ops.wallet_devnet_external_proof` must never claim more
than the endpoint it drove, must persist its resume state privately, and must have a leak scan that
actually bites.

Nothing here touches a real chain: the RPC is the pack's scripted loopback stub, and the two
subprocess tests run against a home in ``tmp_path`` with no network reachable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.wallet._rig import DESTINATION

pytestmark = [pytest.mark.safety]

REPO = Path(__file__).resolve().parents[2]


def _module():
    from ops import wallet_devnet_external_proof

    return wallet_devnet_external_proof


# --- the runtime fix: one commitment for blockhash, simulation and preflight ---------------------

def _params(rpc, method: str) -> dict:
    for call in rpc.calls:
        if call.get("method") == method:
            options = (call.get("params") or [])[-1]
            return options if isinstance(options, dict) else {}
    raise AssertionError(f"{method} was never called")


def test_broadcast_preflight_uses_the_same_commitment_as_the_blockhash(wallet_env):
    """The regression: a confirmed blockhash checked by a finalized preflight is BlockhashNotFound."""
    from core.wallet import config, lifecycle

    client = lifecycle.RpcClient(config.testnet_rpc_url(), network=config.NETWORK_SOLANA_DEVNET)
    blockhash = client.latest_blockhash()
    message = lifecycle._build_message(
        _proposal_like(destination=DESTINATION, amount=1_000_000),
        "11111111111111111111111111111111",
        blockhash,
    )
    raw = lifecycle._serialize(message, lifecycle.ZERO_SIGNATURE)
    client.simulate(raw)
    client.broadcast(raw)

    rpc = wallet_env["rpc"]
    assert _params(rpc, "getLatestBlockhash")["commitment"] == "confirmed"
    assert _params(rpc, "simulateTransaction")["commitment"] == "confirmed"
    assert _params(rpc, "sendTransaction")["preflightCommitment"] == "confirmed", (
        "sendTransaction must name the preflight commitment; the RPC default is finalized, which "
        "lags the confirmed bank the blockhash came from"
    )


def test_every_broadcast_read_agrees_on_one_commitment(wallet_env):
    """A single knob, so the three calls cannot drift apart in a later edit."""
    from core.wallet import config, lifecycle

    client = lifecycle.RpcClient(config.testnet_rpc_url(), network=config.NETWORK_SOLANA_DEVNET)
    assert client.COMMITMENT == "confirmed"
    client.latest_blockhash()
    rpc = wallet_env["rpc"]
    assert _params(rpc, "getLatestBlockhash")["commitment"] == client.COMMITMENT


def _proposal_like(*, destination: str, amount: int):
    from core.wallet import proposals

    return proposals.TransactionProposal(
        proposal_id="pay-test", wallet_id="wallet-test", network="solana-devnet", asset="SOL",
        amount_minor=amount, destination=destination, memo="", origin=proposals.ORIGIN_USER,
        idempotency_key="", state=proposals.STATE_PROPOSED, created_at="", updated_at="",
    )


# --- the lane never claims more than the chain it drove -------------------------------------------

def test_the_verdict_comes_from_the_chain_genesis_not_the_url(monkeypatch):
    """A URL string is not chain identity: a keyed provider serving devnet must qualify, and a
    local validator must not, however it is addressed."""
    module = _module()
    monkeypatch.setattr(module, "RPC_URL", "https://private-provider.example/v1/SECRETKEY")

    monkeypatch.setattr(module, "rpc", lambda *a, **k: {"result": module.DEVNET_GENESIS_HASH})
    target = module._target()
    assert target["is_public_devnet"] is True
    assert target["claimable_verdict"] == "DEVNET-PROVEN"
    assert target["rpc_is_the_default_public_endpoint"] is False

    monkeypatch.setattr(module, "rpc", lambda *a, **k: {"result": "SomeLocalValidatorGenesisHash11111111111111"})
    monkeypatch.setattr(module, "RPC_URL", module.DEVNET_RPC)
    downgraded = module._target()
    assert downgraded["is_public_devnet"] is False, "the canonical URL must not buy a claim the chain does not back"
    assert downgraded["claimable_verdict"] == "LOCAL-VALIDATOR-REHEARSAL"

    monkeypatch.setattr(module, "rpc", lambda *a, **k: {"error": {"code": -1, "message": "unreachable"}})
    assert module._target()["claimable_verdict"] == "LOCAL-VALIDATOR-REHEARSAL"


def test_a_keyed_endpoint_never_reaches_a_record(monkeypatch):
    """Whatever is recorded must be a public fact. Scheme and host are; a key in the path is not."""
    module = _module()
    assert module._public_endpoint("https://devnet.example-provider.com/v1/EXAMPLE-KEY-NOT-A-REAL-ONE") == "https://devnet.example-provider.com"
    assert module._public_endpoint("https://rpc.example.com:8899/?api-key=SECRET") == "https://rpc.example.com:8899"
    assert module._public_endpoint(module.DEVNET_RPC) == "https://api.devnet.solana.com"

    monkeypatch.setattr(module, "RPC_URL", "https://devnet.example-provider.com/v1/EXAMPLE-KEY-NOT-A-REAL-ONE")
    monkeypatch.setattr(module, "rpc", lambda *a, **k: {"result": module.DEVNET_GENESIS_HASH})
    assert "SECRET" not in json.dumps(module._target())


def test_the_faucet_door_is_shut_against_public_devnet(monkeypatch):
    """The whole point of the two-phase lane is external funding; the airdrop helper is rehearsal-only."""
    module = _module()
    monkeypatch.setattr(module, "RPC_URL", module.DEVNET_RPC)
    asked: list[str] = []

    def only_genesis(method, *_a, **_k):
        asked.append(method)
        return {"result": module.DEVNET_GENESIS_HASH}

    monkeypatch.setattr(module, "rpc", only_genesis)
    with pytest.raises(RuntimeError, match="refused against public devnet"):
        module._local_airdrop("E3XvQKMYY3thesznhuqMGAgszTHSLDwUhe16NVuJk7X3", 1)
    assert asked == ["getGenesisHash"], "the refusal must land before any airdrop request is made"


def test_mainnet_assertion_is_not_decorative(monkeypatch):
    """If a mainnet entry ever appears in ALLOWED_NETWORKS the lane must refuse to run at all."""
    module = _module()
    from core.wallet import config

    assert module._assert_devnet_only()["ok"] is True
    monkeypatch.setattr(config, "ALLOWED_NETWORKS", ("solana-devnet", "solana-mainnet"))
    with pytest.raises(RuntimeError, match="devnet-only assertion failed"):
        module._assert_devnet_only()


# --- balance deltas are read off chain meta, not trusted from the receipt --------------------------

def test_account_delta_reads_the_transaction_meta():
    module = _module()
    transaction = {
        "meta": {"preBalances": [200_000_000, 0], "postBalances": [198_995_000, 1_000_000]},
        "transaction": {"message": {"accountKeys": ["PAYER", "RECEIVER"]}},
    }
    assert module._account_delta(transaction, "RECEIVER") == 1_000_000
    assert module._account_delta(transaction, "PAYER") == -1_005_000
    assert module._account_delta(transaction, "STRANGER") is None
    assert module._account_delta({}, "RECEIVER") is None


# --- the leak scan bites --------------------------------------------------------------------------

def _home_with_receipt(tmp_path: Path, payload: str) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    connection = sqlite3.connect(str(home / "devnet-proof.db"))
    connection.execute("CREATE TABLE wallet_receipts (receipt_id TEXT, payload_json TEXT)")
    connection.execute("INSERT INTO wallet_receipts VALUES (?, ?)", ("wrcpt-1", payload))
    connection.execute("CREATE TABLE wallet_profiles (wallet_id TEXT, sealed_blob TEXT)")
    connection.execute("INSERT INTO wallet_profiles VALUES (?, ?)", ("wallet-1", "SEALEDCIPHERTEXT"))
    connection.commit()
    connection.close()
    return home


def test_leak_scan_catches_a_pin_that_reached_a_receipt(tmp_path):
    module = _module()
    home = _home_with_receipt(tmp_path, json.dumps({"state": "confirmed", "pin": "246810"}))
    verdict = module._scan_for_secrets(home=home, secrets={"payer_pin": "246810"}, extra_texts={})
    assert verdict["ok"] is False
    assert {"origin": "db:wallet_receipts.payload_json", "secret": "payer_pin"} in verdict["findings"]


def test_leak_scan_catches_a_recovery_phrase_it_was_never_told(tmp_path):
    """The phrase is dropped unread at creation, so the scan must find one structurally."""
    module = _module()
    phrase = "legal winner thank year wave sausage worth useful legal winner thank yellow"
    home = _home_with_receipt(tmp_path, json.dumps({"memo": phrase}))
    verdict = module._scan_for_secrets(home=home, secrets={}, extra_texts={})
    assert verdict["ok"] is False
    assert any(f["secret"] == "bip39_run" for f in verdict["findings"])


def test_leak_scan_passes_a_clean_home_but_still_reads_the_sealed_store(tmp_path):
    """Sealed ciphertext is allowed in its own column and file, and nowhere else."""
    module = _module()
    home = _home_with_receipt(tmp_path, json.dumps({"state": "confirmed", "tx_signature": "5" * 88}))
    clean = module._scan_for_secrets(home=home, secrets={"payer_pin": "246810", "sealed_ciphertext_0": "SEALEDCIPHERTEXT"}, extra_texts={})
    assert clean["ok"] is True and clean["surfaces_scanned"] > 0

    leaked = module._scan_for_secrets(
        home=home, secrets={"sealed_ciphertext_0": "SEALEDCIPHERTEXT"},
        extra_texts={"record:somewhere_else": "the blob is SEALEDCIPHERTEXT here"},
    )
    assert leaked["ok"] is False
    assert {"origin": "record:somewhere_else", "secret": "sealed_ciphertext_0"} in leaked["findings"]


# --- the two-phase seam, driven as two real processes ----------------------------------------------

def _run(args: list[str], *, home: Path) -> subprocess.CompletedProcess:
    environment = {
        **os.environ, "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "PYTHONPATH": str(REPO),
        # An unroutable endpoint: prepare must not need the network, and must not silently use one.
        "VOOL_WALLET_TESTNET_RPC_URL": "http://127.0.0.1:1",
    }
    return subprocess.run(
        [sys.executable, "-m", "ops.wallet_devnet_external_proof", *args, "--home", str(home), "--rpc-url", "http://127.0.0.1:1"],
        cwd=str(REPO), env=environment, capture_output=True, text=True, timeout=300, check=False,
    )



def test_prepare_then_selfcheck_across_two_processes(tmp_path):
    """The burner minted in one process must unseal in the next, with nothing sensitive on stdout."""
    home = tmp_path / "isolated-home"
    prepared = _run(["prepare"], home=home)
    assert prepared.returncode == 0, prepared.stderr[-2000:]
    assert "FUND THIS ADDRESS" in prepared.stdout

    resume = json.loads((home / "resume.json").read_text())
    assert oct(home.stat().st_mode & 0o777) == "0o700"
    assert oct((home / "resume.json").stat().st_mode & 0o777) == "0o600"
    for secret in (resume["payer_pin"], resume["key_passphrase"]):
        assert secret not in prepared.stdout and secret not in prepared.stderr, "a secret reached the console"

    checked = _run(["selfcheck"], home=home)
    assert checked.returncode == 0, checked.stderr[-2000:]
    record = json.loads(checked.stdout[checked.stdout.index("{"):checked.stdout.rindex("}") + 1])
    assert record["ok"] is True
    assert record["pin_verifies_after_restart"] is True and record["wrong_pin_rejected"] is True
    assert record["signature_length"] == 64
    assert record["public_key"] == resume["payer_public_key"]
    assert resume["payer_pin"] not in checked.stdout



def test_prove_on_an_unfunded_burner_refuses_and_keeps_the_home(tmp_path):
    """No funds, no payment, and the home survives so nothing that was funded could be stranded."""
    home = tmp_path / "isolated-home"
    assert _run(["prepare"], home=home).returncode == 0
    proved = _run(["prove"], home=home)
    assert proved.returncode == 3
    record = json.loads(proved.stdout[proved.stdout.index("{"):proved.stdout.rindex("}") + 1])
    assert record["status"] == "AWAITING_FUNDING"
    assert record["verdict"] == "NOT-PROVEN"
    assert record["faucet_requested"] is False
    assert record["home_preserved"] is True and record["home_still_exists"] is True
    assert (home / "resume.json").exists()


# --- the duplicate fence, layer by layer ------------------------------------------------------------

class _CountingApprover:
    """Records whether the lifecycle ever asked. The outer door must refuse before it does."""

    def __init__(self, pin: str) -> None:
        self.pin = pin
        self.asked = 0

    def approve(self, challenge):
        from core.wallet import approval

        self.asked += 1
        return approval.ApprovalDecision(approved=True, method=approval.METHOD_PIN, challenge_digest=challenge.digest, pin_unlock=self.pin)


PIN = "246810"


def _confirmed_payment(wallet_env):
    from core.wallet import approval, custody, lifecycle, proposals

    profile = custody.create_pocket_wallet(
        acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN,
    ).profile
    engine = lifecycle.default_lifecycle()
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=700, asset="SOL", origin=proposals.ORIGIN_USER,
    )
    engine.prepare(proposal.proposal_id)
    receipt = engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert receipt.state == proposals.STATE_CONFIRMED and wallet_env["rpc"].send_count() == 1
    return engine, proposal, receipt


def _rewind_to_pending(proposal_id: str, *, keep_signature: bool = True) -> None:
    """Force the outer door open the way stale state would: the row says awaiting approval again.

    ``keep_signature=False`` also erases the broadcast evidence, which is the only way to reach the
    fences BEHIND the signature check -- otherwise that check answers first and the layer under test
    is never exercised.
    """
    from core.wallet import proposals
    from core.wallet.store import connection

    signature_clause = "" if keep_signature else ", tx_signature = ''"
    with connection() as conn:
        conn.execute(
            f"UPDATE wallet_proposals SET state = ?{signature_clause} WHERE proposal_id = ?",
            (proposals.STATE_PENDING_APPROVAL, proposal_id),
        )


def test_duplicate_approval_is_refused_at_the_door_before_the_approver_is_asked(wallet_env):
    """Fence 1: a proposal that is not awaiting approval never reaches the approver or the PIN."""
    from core.wallet.errors import WalletFault

    engine, proposal, _ = _confirmed_payment(wallet_env)
    approver = _CountingApprover(PIN)
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approver)
    assert exc.value.code == "wallet_duplicate_payment"
    assert exc.value.context.get("reason") == "not_awaiting_approval"
    assert approver.asked == 0, "the door must refuse before any approval or PIN is consulted"
    assert wallet_env["rpc"].send_count() == 1


def test_the_claim_cas_is_the_fence_behind_the_signature_check(wallet_env, monkeypatch):
    """Fence 3: with BOTH the state column rewound and the signature erased, the compare-and-set is
    what is left, and only one approval can win it."""
    from core.wallet import approval, proposals
    from core.wallet.errors import WalletFault

    engine, proposal, _receipt = _confirmed_payment(wallet_env)
    _rewind_to_pending(proposal.proposal_id, keep_signature=False)

    # Land a competing approval in the instant BETWEEN this approval passing the door and reaching
    # the compare-and-set. Claiming earlier would move the state column and the door would answer
    # first, so the CAS -- the layer under test -- would never run.
    real_transition = proposals.transition
    stolen: list[str] = []

    def steal_then_call(proposal_id, new_state, **kwargs):
        if not stolen and new_state == proposals.STATE_APPROVED and kwargs.get("expected_state") == proposals.STATE_PENDING_APPROVAL:
            stolen.append(proposal_id)
            assert real_transition(proposal_id, proposals.STATE_APPROVED, expected_state=proposals.STATE_PENDING_APPROVAL) is not None
        return real_transition(proposal_id, new_state, **kwargs)

    monkeypatch.setattr(proposals, "transition", steal_then_call)
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_duplicate_payment"
    assert exc.value.context.get("reason") == "already_claimed_by_another_approval"
    assert stolen, "the competing claim must actually have landed, or the CAS was never the fence"
    assert wallet_env["rpc"].send_count() == 1, "a lost CAS must not reach the broadcaster"


def test_the_recorded_signature_is_the_second_fence_when_the_state_column_lies(wallet_env):
    """Fence 3: door and CAS both read the state column. When that column is rewound they both
    open, and the only thing left that cannot be rewritten by a stale row is the signature this
    proposal already put on chain. Without this fence a restored database re-pays a real payment."""
    from core.wallet import approval, proposals
    from core.wallet.errors import WalletFault

    engine, proposal, receipt = _confirmed_payment(wallet_env)
    _rewind_to_pending(proposal.proposal_id)
    assert proposals.get_proposal(proposal.proposal_id).tx_signature == receipt.tx_signature
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_duplicate_payment"
    assert exc.value.context.get("reason") == "already_broadcast"
    assert wallet_env["rpc"].send_count() == 1, "zero second broadcasts"


def test_the_signature_fence_also_covers_the_external_signer_lane(wallet_env):
    """The same choke point, reached from the other custody mode: no second signing request either."""
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = custody.register_external_signer_wallet("11111111111111111111111111111111", label="external")
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=700, asset="SOL", origin=proposals.ORIGIN_USER,
    )
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    # stamp a signature the way a completed broadcast would, then rewind the state column
    from core.wallet.store import connection

    with connection() as conn:
        conn.execute(
            "UPDATE wallet_proposals SET tx_signature = ?, state = ? WHERE proposal_id = ?",
            ("5" * 88, proposals.STATE_PENDING_APPROVAL, proposal.proposal_id),
        )
    with pytest.raises(WalletFault) as exc:
        engine.request_external_signature(proposal.proposal_id)
    assert exc.value.code == "wallet_duplicate_payment"
    assert exc.value.context.get("reason") == "already_broadcast"
    assert wallet_env["rpc"].send_count() == 0
