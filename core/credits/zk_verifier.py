"""
ZK verification is the strongest anti-cheat layer.
A Groth16 proof cryptographically guarantees computation was performed correctly
WITHOUT revealing the private inputs (the actual result).
Verification is designed to happen on-chain via the dark_bn254_gate Groth16
verifier: a worker cannot fake a ZK proof without actually doing the computation.

ZK Verifier — VOOL x dark_bn254_gate Bridge
=============================================
This module is the bridge between VOOL task results and the dark_bn254_gate
Groth16 verifier.

STATUS — stub bridge, gate not live.  Local verification (verify_locally) is not
yet implemented, and the on-chain gate is NOT currently deployed to a live
cluster: the previous dark_bn254_gate program id was part of the set seized in
the 2026-06-14 security incident and is pending a clean redeploy.  Do not treat
the on-chain path as live mainnet verification until a clean gate is redeployed.

On-chain program:
  dark_bn254_gate  — program id supplied via the DARK_BN254_GATE_PROGRAM_ID
                     environment variable (cluster / config source of truth),
                     not hardcoded here.

Instruction layout (512 bytes):
  [0..7]    magic      b"GROTH16\\x00"  (8 bytes)
  [8..263]  proof      raw Groth16 proof (256 bytes)
  [264..295] vk_hash   sha256 of the verification key used (32 bytes)
  [296..327] inputs_hash sha256(abi-packed public inputs) (32 bytes)
  [328..359] task_id   utf-8, zero-padded to 32 bytes
  [360..391] worker_id utf-8, zero-padded to 32 bytes
  [392..399] credits    uint64 little-endian (8 bytes)
  [400..511] reserved  zero-padded
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# dark_bn254_gate program id
# ---------------------------------------------------------------------------
# Sourced from the environment / cluster config — never hardcoded.  The prior
# on-chain id was part of the set seized in the 2026-06-14 incident and the gate
# is pending a clean redeploy, so there is no live default here.  Set
# DARK_BN254_GATE_PROGRAM_ID once a clean gate has been deployed.
DARK_BN254_GATE_PROGRAM_ID = os.environ.get("DARK_BN254_GATE_PROGRAM_ID", "").strip()

# Expected Groth16 proof byte length
GROTH16_PROOF_BYTES = 256

# Total instruction data length sent to dark_bn254_gate
GATE_INSTRUCTION_BYTES = 512

# ---------------------------------------------------------------------------
# Supported circuits
# ---------------------------------------------------------------------------
SUPPORTED_CIRCUITS: dict[str, dict] = {
    "range_proof": {
        "description": "Proves result value is in range [min, max] without revealing it",
        "use_case": "Prove score >= threshold without revealing score",
        "public_inputs": ["commitment", "threshold", "nullifier"],
    },
    "hash_preimage": {
        "description": "Proves knowledge of preimage of a hash",
        "use_case": "Prove you computed something that hashes to X",
        "public_inputs": ["hash_output"],
    },
    "merkle_membership": {
        "description": "Proves an item is in a set without revealing which item",
        "use_case": "Prove you belong to an authorized set",
        "public_inputs": ["root", "nullifier"],
    },
}

# ---------------------------------------------------------------------------
# Task-type → circuit mapping
# ---------------------------------------------------------------------------
_TASK_CIRCUIT_MAP: dict[str, str] = {
    # Inference tasks — prove score is above threshold without leaking score
    "inference": "range_proof",
    "llm_inference": "range_proof",
    "image_classification": "range_proof",
    "score_threshold": "range_proof",
    # Computation integrity — prove output hash matches claimed hash
    "hash_verification": "hash_preimage",
    "compute_verification": "hash_preimage",
    "data_transform": "hash_preimage",
    # Membership / authorization tasks
    "set_membership": "merkle_membership",
    "allowlist_check": "merkle_membership",
    "authorization": "merkle_membership",
}


# ---------------------------------------------------------------------------
# ZKComputationProof dataclass
# ---------------------------------------------------------------------------

@dataclass
class ZKComputationProof:
    """
    A Groth16 ZK proof attesting that a VOOL computation was performed
    correctly, without revealing the private inputs (actual result).

    Fields
    ------
    task_id          : Unique identifier of the completed task.
    worker_node_id   : Identity of the worker that produced the proof.
    circuit_type     : Which circuit was used ('range_proof' | 'hash_preimage'
                       | 'merkle_membership').
    proof_bytes_hex  : 256-byte Groth16 proof encoded as hex (512 hex chars).
    public_inputs    : List of public inputs to the circuit (hex strings or
                       decimal strings — circuit-specific).
    vk_hash          : Hex-encoded sha256 of the verification key used.
                       Pinning this prevents VK substitution attacks.
    verified_on_chain: True once dark_bn254_gate has accepted the proof.
    solana_tx        : Solana tx signature from dark_bn254_gate, or None.
    credits_earned   : VOOL credits awarded for this verified computation.
    """

    task_id: str
    worker_node_id: str
    circuit_type: str
    proof_bytes_hex: str        # 256 bytes → 512 hex chars
    public_inputs: list[str]
    vk_hash: str
    verified_on_chain: bool
    solana_tx: Optional[str]
    credits_earned: int

    # -----------------------------------------------------------------------
    # Validation helpers
    # -----------------------------------------------------------------------

    def validate(self) -> None:
        """Raise ValueError if the proof fields look obviously wrong."""
        if self.circuit_type not in SUPPORTED_CIRCUITS:
            raise ValueError(
                f"Unknown circuit_type '{self.circuit_type}'. "
                f"Supported: {list(SUPPORTED_CIRCUITS)}"
            )
        if len(self.proof_bytes_hex) != GROTH16_PROOF_BYTES * 2:
            raise ValueError(
                f"proof_bytes_hex must be {GROTH16_PROOF_BYTES * 2} hex chars "
                f"({GROTH16_PROOF_BYTES} bytes); got {len(self.proof_bytes_hex)}"
            )
        if len(self.vk_hash) != 64:
            raise ValueError(
                f"vk_hash must be 64 hex chars (sha256); got {len(self.vk_hash)}"
            )

    # -----------------------------------------------------------------------
    # Serialisation
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> ZKComputationProof:
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> ZKComputationProof:
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# ZKVerifier
# ---------------------------------------------------------------------------

class ZKVerifier:
    """
    Bridge between VOOL task results and the dark_bn254_gate Groth16
    verifier on Solana.

    Typical usage
    -------------
    1. Worker completes a task and generates a Groth16 proof off-chain.
    2. Call build_zk_work_proof() to package the proof into a
       ZKComputationProof.
    3. Call verify_on_chain() to submit to dark_bn254_gate for final
       verification and credit award.
    """

    # ------------------------------------------------------------------
    # Local verification (stub)
    # ------------------------------------------------------------------

    def verify_locally(self, proof: ZKComputationProof) -> bool:
        """
        Stub for local Groth16 pairing-check verification.

        Full local verification requires a BN254 pairing implementation
        (e.g. py_ecc or arkworks-rs via FFI) which is not bundled here.
        Use verify_on_chain() for cryptographically binding verification.

        Returns False and logs a warning.
        """
        logger.warning(
            "verify_locally: local ZK verification not yet implemented — "
            "use verify_on_chain() for cryptographically binding verification. "
            "task_id=%s circuit=%s",
            proof.task_id,
            proof.circuit_type,
        )
        return False

    # ------------------------------------------------------------------
    # On-chain verification via dark_bn254_gate
    # ------------------------------------------------------------------

    def verify_on_chain(self, proof: ZKComputationProof, rpc_url: str) -> str:
        """
        Submit the Groth16 proof to dark_bn254_gate on Solana.

        Builds a 512-byte instruction payload and sends it as a transaction
        to the dark_bn254_gate program.  On success the program emits a
        program log confirming the proof verified, which is captured in the
        transaction.

        Returns the Solana transaction signature string.

        Falls back to a deterministic DRY_RUN:<hash> string when
        SOLANA_DEPLOYER_KEYPAIR is not set in the environment.

        Raises
        ------
        ValueError  : If proof fields are invalid.
        RuntimeError: If the dark_bn254_gate program id is not configured, or
                      if the RPC call fails (real submission path only).
        """
        proof.validate()

        instruction_data = self._build_gate_instruction(proof)
        assert len(instruction_data) == GATE_INSTRUCTION_BYTES, (
            f"Instruction must be exactly {GATE_INSTRUCTION_BYTES} bytes; "
            f"got {len(instruction_data)}"
        )

        keypair_b58 = os.environ.get("SOLANA_DEPLOYER_" + "KEYPAIR")

        if keypair_b58:
            tx_sig = self._send_gate_tx(rpc_url, keypair_b58, instruction_data)
        else:
            logger.info(
                "verify_on_chain: SOLANA_DEPLOYER_KEYPAIR not set — running in "
                "DRY_RUN mode.  Set the env var to submit a real transaction."
            )
            tx_sig = "DRY_RUN:" + _sha256_hex(instruction_data)

        proof.verified_on_chain = not tx_sig.startswith("DRY_RUN:")
        proof.solana_tx = tx_sig
        return tx_sig

    # ------------------------------------------------------------------
    # Circuit selection
    # ------------------------------------------------------------------

    def select_circuit(self, task_type: str) -> str:
        """
        Map a VOOL task type to the most appropriate ZK circuit.

        Parameters
        ----------
        task_type : A VOOL task type string (e.g. 'inference', 'hash_verification').

        Returns
        -------
        circuit_type string (key in SUPPORTED_CIRCUITS).

        Raises
        ------
        ValueError if task_type is not mapped to any circuit.
        """
        circuit = _TASK_CIRCUIT_MAP.get(task_type)
        if circuit is None:
            raise ValueError(
                f"No circuit mapped for task_type '{task_type}'. "
                f"Known task types: {list(_TASK_CIRCUIT_MAP)}. "
                f"Add a mapping to _TASK_CIRCUIT_MAP or pass circuit_type directly."
            )
        return circuit

    # ------------------------------------------------------------------
    # Proof builder
    # ------------------------------------------------------------------

    def build_zk_work_proof(
        self,
        task_id: str,
        worker_id: str,
        circuit_type: str,
        proof_bytes: bytes,
        public_inputs: list[str],
        credits: int,
        vk_hash: Optional[str] = None,
    ) -> ZKComputationProof:
        """
        Package a raw Groth16 proof into a ZKComputationProof.

        Parameters
        ----------
        task_id      : Unique task identifier.
        worker_id    : Node that performed the computation.
        circuit_type : One of SUPPORTED_CIRCUITS.
        proof_bytes  : Raw 256-byte Groth16 proof (A, B, C points encoded).
        public_inputs: Public inputs matching the circuit's expected inputs.
        credits      : Credits to award upon successful verification.
        vk_hash      : Optional hex sha256 of the verification key.
                       Derived from circuit_type if not provided.

        Returns
        -------
        ZKComputationProof (not yet verified — call verify_on_chain() next).

        Raises
        ------
        ValueError if circuit_type is not supported or proof_bytes length
        is wrong.
        """
        if circuit_type not in SUPPORTED_CIRCUITS:
            raise ValueError(
                f"Unknown circuit_type '{circuit_type}'. "
                f"Supported: {list(SUPPORTED_CIRCUITS)}"
            )
        if len(proof_bytes) != GROTH16_PROOF_BYTES:
            raise ValueError(
                f"proof_bytes must be exactly {GROTH16_PROOF_BYTES} bytes; "
                f"got {len(proof_bytes)}"
            )

        if vk_hash is None:
            # Deterministic VK hash stub: sha256("vk:" + circuit_type).
            # In production this would be the hash of the actual proving-key's
            # verification component published by the circuit developer.
            vk_hash = _sha256_hex(b"vk:" + circuit_type.encode())

        return ZKComputationProof(
            task_id=task_id,
            worker_node_id=worker_id,
            circuit_type=circuit_type,
            proof_bytes_hex=proof_bytes.hex(),
            public_inputs=public_inputs,
            vk_hash=vk_hash,
            verified_on_chain=False,
            solana_tx=None,
            credits_earned=credits,
        )

    # ------------------------------------------------------------------
    # Private: instruction builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_gate_instruction(proof: ZKComputationProof) -> bytes:
        """
        Build the 512-byte instruction payload for dark_bn254_gate.

        Layout
        ------
        [0..7]    magic        b"GROTH16\\x00"           8 bytes
        [8..263]  proof_bytes  raw Groth16 proof          256 bytes
        [264..295] vk_hash     sha256 of the VK           32 bytes
        [296..327] inputs_hash sha256(packed public inputs) 32 bytes
        [328..359] task_id     utf-8, zero-padded          32 bytes
        [360..391] worker_id   utf-8, zero-padded          32 bytes
        [392..399] credits     uint64 LE                   8 bytes
        [400..511] reserved    zero-padded                 112 bytes
        """
        magic = b"GROTH16\x00"                                    # 8 bytes
        proof_bytes = bytes.fromhex(proof.proof_bytes_hex)        # 256 bytes
        vk_hash_bytes = bytes.fromhex(proof.vk_hash)              # 32 bytes

        # Pack public inputs: sha256(len_prefix + each input as utf-8)
        inputs_blob = b"".join(
            inp.encode() for inp in proof.public_inputs
        )
        inputs_hash = hashlib.sha256(inputs_blob).digest()        # 32 bytes

        task_id_bytes = proof.task_id.encode()[:32].ljust(32, b"\x00")   # 32 bytes
        worker_id_bytes = proof.worker_node_id.encode()[:32].ljust(32, b"\x00")  # 32 bytes
        credits_bytes = struct.pack("<Q", proof.credits_earned)   # 8 bytes

        payload = (
            magic           # 8
            + proof_bytes   # 256  → 264
            + vk_hash_bytes # 32   → 296
            + inputs_hash   # 32   → 328
            + task_id_bytes # 32   → 360
            + worker_id_bytes  # 32 → 392
            + credits_bytes    # 8  → 400
        )

        # Pad to exactly 512 bytes
        payload = payload.ljust(GATE_INSTRUCTION_BYTES, b"\x00")
        return payload

    # ------------------------------------------------------------------
    # Private: transaction submission
    # ------------------------------------------------------------------

    @staticmethod
    def _send_gate_tx(self, *args, **kwargs):
        """RETIRED: gate transactions are never signed or broadcast from here."""
        return send_gate_tx(b"", "")

    def _send_via_solana_sdk(self, *args, **kwargs):
        """RETIRED: gate transactions are never signed or broadcast from here."""
        return send_gate_tx(b"", "")

    def _send_via_raw_rpc(self, *args, **kwargs):
        """RETIRED: gate transactions are never signed or broadcast from here."""
        return send_gate_tx(b"", "")

def _sha256_hex(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else part.encode())
    return h.hexdigest()


def _b58decode(s: str) -> bytes:
    ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = 0
    for ch in s.encode():
        n = n * 58 + ALPHABET.index(ch)
    result = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + result


def _encode_compact_u16(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            b |= 0x80
        out.append(b)
        if not v:
            break
    return bytes(out)


def _run_tests() -> None:
    import traceback

    verifier = ZKVerifier()
    passed = 0
    failed = 0

    def ok(name: str) -> None:
        nonlocal passed
        print(f"  PASS  {name}")
        passed += 1

    def fail(name: str, exc: Exception) -> None:
        nonlocal failed
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc()
        failed += 1

    # ------------------------------------------------------------------
    # T1: ZKComputationProof builds correctly
    # ------------------------------------------------------------------
    try:
        dummy_proof_bytes = bytes(range(256))  # 256-byte dummy proof
        zk_proof = verifier.build_zk_work_proof(
            task_id="task-zk-001",
            worker_id="worker-alpha",
            circuit_type="range_proof",
            proof_bytes=dummy_proof_bytes,
            public_inputs=["0xdeadbeef", "100", "0xcafe"],
            credits=42,
        )
        assert zk_proof.task_id == "task-zk-001"
        assert zk_proof.worker_node_id == "worker-alpha"
        assert zk_proof.circuit_type == "range_proof"
        assert zk_proof.proof_bytes_hex == dummy_proof_bytes.hex()
        assert len(zk_proof.proof_bytes_hex) == 512
        assert zk_proof.credits_earned == 42
        assert zk_proof.verified_on_chain is False
        assert zk_proof.solana_tx is None
        assert len(zk_proof.vk_hash) == 64
        ok("T1: ZKComputationProof builds correctly")
    except Exception as exc:
        fail("T1: ZKComputationProof builds correctly", exc)

    # ------------------------------------------------------------------
    # T2: verify_on_chain builds correct instruction bytes (dry run)
    # ------------------------------------------------------------------
    try:
        dummy_proof_bytes = b"\xab" * 256
        zk_proof2 = verifier.build_zk_work_proof(
            task_id="task-zk-002",
            worker_id="worker-beta",
            circuit_type="hash_preimage",
            proof_bytes=dummy_proof_bytes,
            public_inputs=["0x" + "ff" * 32],
            credits=10,
        )

        # Manually build instruction and check length
        ix = ZKVerifier._build_gate_instruction(zk_proof2)
        assert len(ix) == GATE_INSTRUCTION_BYTES, (
            f"Expected {GATE_INSTRUCTION_BYTES} bytes, got {len(ix)}"
        )

        # Magic bytes correct
        assert ix[:8] == b"GROTH16\x00", f"Bad magic: {ix[:8]!r}"

        # Proof bytes embedded correctly
        assert ix[8:264] == dummy_proof_bytes, "Proof bytes mismatch in instruction"

        # verify_on_chain dry run returns DRY_RUN prefix
        # (SOLANA_DEPLOYER_KEYPAIR not set in test environment)
        tx_sig = verifier.verify_on_chain(zk_proof2, rpc_url="https://api.devnet.solana.com")
        assert tx_sig.startswith("DRY_RUN:"), f"Expected DRY_RUN prefix, got: {tx_sig}"
        assert len(tx_sig) > len("DRY_RUN:"), "DRY_RUN signature must contain hash"
        ok("T2: verify_on_chain builds correct instruction bytes (dry run)")
    except Exception as exc:
        fail("T2: verify_on_chain builds correct instruction bytes (dry run)", exc)

    # ------------------------------------------------------------------
    # T3: circuit selection maps correctly
    # ------------------------------------------------------------------
    try:
        assert verifier.select_circuit("inference") == "range_proof"
        assert verifier.select_circuit("llm_inference") == "range_proof"
        assert verifier.select_circuit("hash_verification") == "hash_preimage"
        assert verifier.select_circuit("compute_verification") == "hash_preimage"
        assert verifier.select_circuit("set_membership") == "merkle_membership"
        assert verifier.select_circuit("allowlist_check") == "merkle_membership"
        ok("T3: circuit selection maps correctly")
    except Exception as exc:
        fail("T3: circuit selection maps correctly", exc)

    # ------------------------------------------------------------------
    # T4: invalid circuit type raises clear error
    # ------------------------------------------------------------------
    try:
        raised = False
        try:
            verifier.build_zk_work_proof(
                task_id="t",
                worker_id="w",
                circuit_type="NOT_A_REAL_CIRCUIT",
                proof_bytes=bytes(256),
                public_inputs=[],
                credits=1,
            )
        except ValueError as exc:
            assert "NOT_A_REAL_CIRCUIT" in str(exc), (
                f"Error message should mention the bad circuit type: {exc}"
            )
            raised = True
        assert raised, "Expected ValueError for unknown circuit_type"

        # Also check select_circuit raises for unknown task type
        raised2 = False
        try:
            verifier.select_circuit("completely_unknown_task")
        except ValueError as exc:
            assert "completely_unknown_task" in str(exc)
            raised2 = True
        assert raised2, "Expected ValueError for unknown task_type in select_circuit"

        ok("T4: invalid circuit type raises clear error")
    except Exception as exc:
        fail("T4: invalid circuit type raises clear error", exc)

    # ------------------------------------------------------------------
    # T5: verify_locally returns False and does not raise
    # ------------------------------------------------------------------
    try:
        dummy = verifier.build_zk_work_proof(
            task_id="t5",
            worker_id="w5",
            circuit_type="merkle_membership",
            proof_bytes=bytes(256),
            public_inputs=["0xroot", "0xnull"],
            credits=5,
        )
        result = verifier.verify_locally(dummy)
        assert result is False, f"verify_locally should return False, got {result}"
        ok("T5: verify_locally returns False with stub message")
    except Exception as exc:
        fail("T5: verify_locally returns False with stub message", exc)

    # ------------------------------------------------------------------
    # T6: ZKComputationProof serialises round-trip correctly
    # ------------------------------------------------------------------
    try:
        original = verifier.build_zk_work_proof(
            task_id="task-rt",
            worker_id="worker-rt",
            circuit_type="range_proof",
            proof_bytes=bytes(range(256)),
            public_inputs=["0xabcd", "50", "0x1234"],
            credits=99,
        )
        json_str = original.to_json()
        restored = ZKComputationProof.from_json(json_str)
        assert restored.task_id == original.task_id
        assert restored.proof_bytes_hex == original.proof_bytes_hex
        assert restored.credits_earned == original.credits_earned
        assert restored.public_inputs == original.public_inputs
        ok("T6: ZKComputationProof serialises round-trip correctly")
    except Exception as exc:
        fail("T6: ZKComputationProof serialises round-trip correctly", exc)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("Running ZK Verifier self-tests...\n")
    _run_tests()


def send_gate_tx(raw: bytes, rpc_url: str) -> str:
    """RETIRED module-level door: typed, receipt-backed refusal."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("credits.zk_verifier.send_gate_tx")
