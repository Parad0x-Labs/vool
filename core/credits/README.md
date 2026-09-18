# VOOL Credit System — Proof-of-Work Credits

## THREE-LAYER ANTI-CHEAT

Layer 1 — Challenge-Response: can't fake result without computing it
Layer 2 — NULL Staking: economic punishment for cheating costs more than cheating
Layer 3 — ZK Proof: cryptographic proof computation was done correctly

---

### Layer 1: Challenge-Response (`proof_of_work.py`)

`ProofOfWorkMinter` uses a two-phase commit/reveal protocol:

1. Issuer publishes `challenge_hash = sha256(nonce)` — nonce stays secret.
2. Worker commits to `result_hash = sha256(result_bytes)`.
3. Issuer reveals nonce; worker produces `challenge_response = sha256(nonce + result_bytes)`.
4. `verify_proof_with_challenge()` confirms the worker had **both** the nonce
   AND the real result bytes — neither alone is sufficient.

Replay attacks are blocked by tracking `canonical_id()` of every accepted proof.
Expired challenges are rejected.

---

### Layer 2: NULL Staking (`staking_guard.py`)

`StakingGuard` requires workers to lock NULL tokens before submitting work.

| Complexity | Min stake | Credits earned | Cheat cost |
|------------|-----------|----------------|------------|
| simple     | 10 NULL   | 2              | 10 NULL    |
| medium     | 50 NULL   | 10             | 50 NULL    |
| complex    | 200 NULL  | 50             | 200 NULL   |
| expert     | 1 000 NULL| 200            | 1 000 NULL |

Slash conditions:
- `wrong_result` → 100% slashed
- `challenge_fail` → 100% slashed
- `timeout` → 50% slashed
- `spam` → 25% slashed

High-reputation workers (≥ 85% clean completions) earn a 20% stake discount.
`SlashEvidence` records are keyed by `sha256(evidence_bytes)` for on-chain anchoring.

---

### Layer 3: ZK Proof (`zk_verifier.py`)

`ZKVerifier` bridges VOOL task results to the `dark_bn254_gate` Groth16
verifier. The program id is supplied via the `DARK_BN254_GATE_PROGRAM_ID`
environment variable (cluster / config source of truth) — it is **not** hardcoded.

> **Status — gate not live.** This is a stub bridge: `verify_locally` is not yet
> implemented and `dark_bn254_gate` is **not currently deployed** to a live
> cluster. The previous program id was part of the set seized in the 2026-06-14
> incident and is pending a clean redeploy, so there is no live mainnet gate yet.

A Groth16 proof cryptographically guarantees computation was performed correctly
**without revealing the private inputs** (the actual result).

Supported circuits:
- `range_proof` — proves result value is in range without revealing it (inference tasks)
- `hash_preimage` — proves knowledge of preimage (compute integrity)
- `merkle_membership` — proves set membership without revealing which item

Instruction layout sent to `dark_bn254_gate` (512 bytes):
```
[0..7]    magic        b"GROTH16\x00"
[8..263]  proof_bytes  raw 256-byte Groth16 proof
[264..295] vk_hash     sha256 of verification key (VK pinning prevents substitution)
[296..327] inputs_hash sha256(packed public inputs)
[328..359] task_id     utf-8 zero-padded 32 bytes
[360..391] worker_id   utf-8 zero-padded 32 bytes
[392..399] credits     uint64 little-endian
[400..511] reserved    zero-padded
```

---

## How the layers compose

```
Worker starts task
  → Layer 2: StakingGuard.require_stake() locks NULL
  → Layer 1: Issuer issues challenge (nonce secret)
  → Worker computes result
  → Worker commits result_hash
  → Issuer reveals nonce
  → Worker produces challenge_response
  → Layer 1: verify_proof_with_challenge() confirms authenticity
  → Layer 3: ZKVerifier.verify_on_chain() submits Groth16 proof to dark_bn254_gate
  → Layer 2: StakingGuard.release_stake() returns stake + awards credits
              OR slashes stake if any layer rejects
```

---

## How it works

### 1. Complete a task → earn a WorkProof

When a VOOL node finishes a task (inference, routing, data relay, etc.) the
`ProofOfWorkMinter` issues a `WorkProof` object:

```
task_id             — what was done
worker_node_id      — who did it
result_hash         — sha256(result_bytes)
challenge_response  — sha256(challenge_nonce + result_bytes)
credits_earned      — how many VOOL credits this work is worth
timestamp           — when the proof was minted
solana_anchor_tx    — None until anchored on-chain
is_valid            — set True by verify_proof_with_challenge()
```

---

### 2. WorkProof anchored on Solana = permanent proof you did the work

Call `ProofOfWorkMinter.anchor_proof(proof, rpc_url)` to push the proof
on-chain using the receipt_anchor memo pattern:

```
[0x01][0x00][32 bytes — sha256(proof canonical fields)]
```

Set the `SOLANA_DEPLOYER_KEYPAIR` environment variable (base58 private key)
to submit real transactions.  Without it the minter runs in dry-run mode and
returns a deterministic `DRY_RUN:<hash>` identifier — useful for testing.

---

### 3. Sell your WorkProof = sell your work history / credits

`CreditMarket` lets nodes list their WorkProof objects for sale at a USDC price.
Ownership transfer preserves the original `result_hash` and `challenge_response`
so the provenance chain is never erased.

---

### 4. Buyers use credits for priority routing in the mesh

- Priority job dispatch — tasks routed first.
- Discounted compute — workers may offer lower rates to high-credit nodes.
- Reputation score — on-chain anchor history is public and queryable.

---

## Quick start

```python
from core.credits.proof_of_work import ProofOfWorkMinter, CreditMarket
from core.credits.staking_guard import StakingGuard
from core.credits.zk_verifier import ZKVerifier

# --- Layer 2: stake before work ---
guard = StakingGuard()
guard.deposit("my-node", 1000)
stake = guard.require_stake("task-001", "my-node", "expert")

# --- Layer 1: challenge-response ---
minter = ProofOfWorkMinter()
issued = minter.issue_task_challenge("task-001", "issuer-A", credits_offered=200)
nonce = minter.reveal_challenge("task-001")

result_bytes = b"<model output>"
proof = minter.mint_proof_with_challenge("task-001", result_bytes, nonce, "my-node", 200)
valid = minter.verify_proof_with_challenge(proof, nonce, result_bytes)

# --- Layer 3: ZK proof ---
verifier = ZKVerifier()
zk_proof = verifier.build_zk_work_proof(
    task_id="task-001",
    worker_id="my-node",
    circuit_type="range_proof",
    proof_bytes=bytes(256),   # replace with real Groth16 proof
    public_inputs=["0xcommitment", "100", "0xnullifier"],
    credits=200,
)
tx = verifier.verify_on_chain(zk_proof, rpc_url="https://api.devnet.solana.com")  # dark_bn254 gate is devnet-only

# --- Layer 2: release stake + earn credits ---
result = guard.release_stake(stake, work_proof_valid=valid)
print(f"Released: {result['released_null']} NULL, earned: {result['credits_earned']} credits")
```

---

## File layout

```
core/credits/
  proof_of_work.py   — WorkProof, ProofOfWorkMinter (Layer 1), CreditMarket
  staking_guard.py   — StakingGuard, StakeRecord, SlashEvidence (Layer 2)
  zk_verifier.py     — ZKVerifier, ZKComputationProof, dark_bn254_gate bridge (Layer 3)
  README.md          — this file
```
