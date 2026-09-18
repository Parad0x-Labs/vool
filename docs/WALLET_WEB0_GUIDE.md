# Wallet & Web0 guide (optional, disabled by default)

VOOL can hold **one local Solana wallet** (Ed25519 keypair) used for Web0 `.null` name
registration and — in builds where it is enabled — metered x402 spends. In this beta the
wallet is **opt-in and disabled by default**, and the USDC x402 spend lane is **disabled**.

## Enablement

`VOOL_WALLET_ENABLED` opts in. No install, boot, migration or status read ever creates a
wallet on its own. Treasury addresses, chain ids and provider endpoints are untouched by the
VOOL rename.

## Supported chains (honest)

- Solana devnet + the Web0 `null_registrar v2` program: exercised in tests.
- Solana mainnet-beta: **impossible by construction** in the wallet's allowed-network table —
  any name containing "mainnet" is refused before the registry is consulted.
- Other chains (EVM signing helpers exist in `core/wallet/` for verification): not enabled as
  user-facing payment lanes in this build.

## Custody and recovery

- **Crypto Pilot custody**: a backup key is shown **exactly once** at setup; there is no
  second reveal and no export (`wallet_backup_unavailable` fault enforces it).
- **Pocket wallet**: requires a typed confirmation phrase and a PIN.
- **Private keys are never exported** by any door in the runtime (`wallet_export_refused`).

## Spending controls

- Every SOL spend is gated by an OS consent prompt; the model cannot move money alone.
- Per-transaction, daily and weekly caps plus a panic freeze; the policy file is
  HMAC-authenticated so tampering fails closed.
- `/stopx402` freezes the `.null` registration lane instantly; `/stopall` plus the red Stop
  button hard-stop every VOOL process.
- Self-updates swap code only; `data/` (wallet, keys, history, receipts) is preserved by
  construction and proven against adversarial update tests.

## Fees

`.null` registration costs the on-chain fee plus name rent (order of ~0.01 SOL, read live
from the registrar config before you commit; 1–3 character names go through auction).
`vool register <name>.null` previews the exact live cost and asks for explicit approval
before anything is signed.

See also: [SPEND_CAPS](SPEND_CAPS.md), [x402-proof](x402-proof.md), and the wallet faults in
the [Error Book](ERROR_BOOK.md).
