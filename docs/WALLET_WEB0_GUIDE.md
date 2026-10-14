# Wallet & Web0 guide (optional, disabled by default)

Crypto is **off by default**. When you turn it on, VOOL's wallet covers the mainnet chains
listed below, with caps, your approval and a receipt for every payment. In 0.7.0, x402
for websites works on test networks only. One exception: the pay-per-call option in the
model marketplace in Settings can pay from the Solana Mainnet Crypto Pilot wallet; each call
waits for your approval within your limits and shows the 0.1% VOOL fee, and it has not yet
been tested on a real chain.

## Enablement

`VOOL_WALLET_ENABLED` opts in. No install, boot, migration or status read ever creates a
wallet on its own. Treasury addresses, chain ids and provider endpoints are untouched by the
VOOL rename.

## Supported chains

- Mainnet is the default once Crypto is on: Solana, Robinhood Chain, Base, Ethereum, BNB and
  Arbitrum One mainnet rows. See [the wallet guide](guides/wallet.md) for what each lane moves.
- Test networks (Solana devnet and the EVM testnets) are reachable only in
  **Settings → Crypto → Developer options**.
- The Web0 `null_registrar v2` program: exercised in tests.

## Custody and recovery

- **Crypto Pilot custody**: a backup key is shown **exactly once** at setup; there is no
  second reveal and no export (`wallet_backup_unavailable` fault enforces it).
- **Pocket wallet**: requires a typed confirmation phrase and a PIN.
- **Private keys are never exported** by any door in the runtime (`wallet_export_refused`).

## Spending controls

- Every payment is a proposal that waits for your approval; the model cannot move money alone.
- Per-transaction, daily and weekly caps plus a panic freeze; the policy file is
  HMAC-authenticated so tampering fails closed.
- `/stopx402` freezes the `.null` registration lane instantly; `/stopall` plus the red Stop
  button hard-stop every VOOL process.
- Self-updates swap code only; `data/` (wallet, keys, history, receipts) is preserved by
  construction and proven against adversarial update tests.

## Fees

`.null` registration costs the on-chain fee plus name rent (order of ~0.01 SOL, read live
from the registrar config before you commit; 1–3 character names go through auction).
`vool register <name>.null` previews the exact live cost. Signing a registration is not
available in 0.7.0: the command shows the preview and stops.

See also: [SPEND_CAPS](SPEND_CAPS.md), [x402-proof](x402-proof.md), and the wallet faults in
the [Error Book](ERROR_BOOK.md).
