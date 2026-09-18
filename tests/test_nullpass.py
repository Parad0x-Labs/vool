"""Portable .nullpass credential — build + offline verification, with a forgery matrix.

verify_nullpass must FAIL CLOSED on any tamper. Two defence layers:
  * the issuer signature catches any change to a signed receipt (an attacker has
    no issuer key), and
  * the recomputed proof/payment hashes catch a SELF-SIGNED forgery — an attacker
    who re-signs a doctored receipt with their own key still can't make the
    internal hashes lie.
"""
from __future__ import annotations

import base64
import copy
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.vool_wallet import b58encode
from core.nullpass import NULLPASS_VERSION, _canonical, build_nullpass, verify_nullpass
from core.web0_work_receipt import issue_work_receipt


class _Signer:
    """Ed25519 signer with a Solana-style base58 pubkey (mirrors VoolWallet)."""

    def __init__(self) -> None:
        self._sk = Ed25519PrivateKey.generate()
        self._pub = self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def pubkey(self) -> str:
        return b58encode(self._pub)

    def sign(self, payload: bytes) -> bytes:
        return self._sk.sign(bytes(payload))


def _receipt():
    return issue_work_receipt(task_id="task-1", result="hello web0", worker_id="vool-test")


def _pass(signer=None):
    return build_nullpass(_receipt(), signer=signer or _Signer())


def _resign(bundle: dict, signer: _Signer) -> dict:
    bundle["issuer"] = {
        "alg": "ed25519", "pubkey": signer.pubkey(),
        "signature": base64.b64encode(signer.sign(_canonical(bundle["receipt"]))).decode(),
    }
    return bundle


# ── the CLI issue door is retired (verification stays) ───────────────────────

def test_cli_nullpass_issue_refuses_typed_and_writes_no_bundle(tmp_path, capsys):
    from apps.vool_cli import cmd_nullpass

    out_path = tmp_path / "cred.nullpass"
    with mock.patch("core.nullpass.build_nullpass", side_effect=AssertionError("issue reached the builder")):
        rc = cmd_nullpass("issue", "task-1", result="hello web0", out=str(out_path))
    assert rc == 2
    printed = capsys.readouterr().out
    assert "wallet_legacy_surface_retired" in printed and "fault-" in printed
    assert not out_path.exists()
    assert not list(tmp_path.rglob("solana_wallet.enc"))
    from core.faults.recorder import list_faults

    surfaces = [f.context.get("surface") for f in list_faults(limit=50) if f.code == "wallet_legacy_surface_retired"]
    assert "cli.nullpass-issue" in surfaces


def test_cli_nullpass_verify_still_works_offline(tmp_path, capsys):
    import json

    from apps.vool_cli import cmd_nullpass

    bundle_path = tmp_path / "ok.nullpass"
    bundle_path.write_text(json.dumps(_pass()), encoding="utf-8")
    assert cmd_nullpass("verify", str(bundle_path), json_mode=True) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


# ── happy path ──────────────────────────────────────────────────────────────

def test_build_and_verify_roundtrip():
    s = _Signer()
    v = verify_nullpass(build_nullpass(_receipt(), signer=s))
    assert v["valid"] is True
    assert all(v["checks"][k] for k in ("proof_hash", "payment_receipt_hash", "signature", "result_binding"))
    assert v["issuer"] == s.pubkey()
    assert v["version"] == NULLPASS_VERSION


def test_bundle_is_json_serializable_and_survives_roundtrip():
    import json
    s = _Signer()
    b = build_nullpass(_receipt(), signer=s)
    b2 = json.loads(json.dumps(b))           # serialize → ship → parse
    assert verify_nullpass(b2)["valid"] is True


# ── tamper detected by the signature (attacker has no issuer key) ─────────────

def test_tampered_signed_field_breaks_signature():
    b = _pass()
    b = copy.deepcopy(b)
    b["receipt"]["worker_id"] = "attacker"
    v = verify_nullpass(b)
    assert v["valid"] is False
    assert v["checks"]["signature"] is False


def test_random_signature_rejected():
    b = copy.deepcopy(_pass())
    b["issuer"]["signature"] = base64.b64encode(b"\x00" * 64).decode()
    assert verify_nullpass(b)["valid"] is False
    assert verify_nullpass(b)["checks"]["signature"] is False


def test_swapped_pubkey_rejected():
    b = copy.deepcopy(_pass())
    b["issuer"]["pubkey"] = _Signer().pubkey()  # different key, original sig
    assert verify_nullpass(b)["checks"]["signature"] is False


# ── self-signed forgeries caught by the recomputed hashes ─────────────────────

def test_self_signed_forgery_caught_by_proof_hash():
    b = copy.deepcopy(_pass())
    b["receipt"]["proof"]["proof_hash"] = "ff" * 32  # lie about the proof
    _resign(b, _Signer())                            # attacker signs their forgery
    v = verify_nullpass(b)
    assert v["checks"]["signature"] is True           # validly signed (by the attacker)
    assert v["checks"]["proof_hash"] is False          # ...but the hash betrays it
    assert v["valid"] is False


def test_self_signed_forgery_caught_by_payment_hash():
    b = copy.deepcopy(_pass())
    b["receipt"]["payment"]["amount_usdc"] = 999.0     # inflate the payment
    _resign(b, _Signer())
    v = verify_nullpass(b)
    assert v["checks"]["signature"] is True
    assert v["checks"]["payment_receipt_hash"] is False
    assert v["valid"] is False


def test_result_binding_mismatch_rejected():
    b = copy.deepcopy(_pass())
    b["receipt"]["result_hash"] = "ab" * 32            # != proof.result_hash
    _resign(b, _Signer())
    v = verify_nullpass(b)
    assert v["checks"]["result_binding"] is False
    assert v["valid"] is False


# ── shape / version guards ────────────────────────────────────────────────────

def test_wrong_version_rejected():
    b = copy.deepcopy(_pass())
    b["version"] = "nullpass/999"
    assert verify_nullpass(b)["valid"] is False


def test_missing_receipt_rejected():
    b = copy.deepcopy(_pass())
    b.pop("receipt")
    assert verify_nullpass(b)["valid"] is False


def test_non_dict_rejected():
    assert verify_nullpass("not a bundle")["valid"] is False
    assert verify_nullpass({})["valid"] is False


# ── optional on-chain settlement confirmation ─────────────────────────────────

def _devnet_pass():
    """A valid pass whose payment looks like a real devnet settlement (re-signed)."""
    from core.x402.client import X402Receipt
    b = copy.deepcopy(_pass())
    pay = X402Receipt(session_id="task-1", payment_tx="DEVNETSIG123", amount_usdc=0.001,
                      recipient_wallet="Recipient111", facilitator_sig="", timestamp=1.0,
                      mode="devnet")
    b["receipt"]["payment"] = pay.to_dict()
    return _resign(b, _Signer())


def test_confirm_onchain_stub_payment_is_not_confirmable():
    # offline checks pass, but a stub payment can't be confirmed on-chain
    b = _pass()
    v = verify_nullpass(b, confirm_onchain=True)
    assert v["checks"]["settlement"] is False
    assert v["valid"] is False


def test_confirm_onchain_success_with_mock_rpc():
    b = _devnet_pass()
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return {"meta": {"err": None}}

    v = verify_nullpass(b, confirm_onchain=True, rpc_call=rpc)
    assert v["checks"]["settlement"] is True
    assert v["valid"] is True
    assert calls and calls[0][0] == "getTransaction"


def test_confirm_onchain_failed_tx_rejected():
    b = _devnet_pass()
    v = verify_nullpass(b, confirm_onchain=True, rpc_call=lambda m, p: {"meta": {"err": "InstructionError"}})
    assert v["checks"]["settlement"] is False
    assert v["valid"] is False
