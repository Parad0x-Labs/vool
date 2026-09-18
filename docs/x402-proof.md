# x402 payment — mainnet & devnet proven

VOOL's x402 pay path settles real payments on Solana using the **canonical x402
"exact" scheme** against the **PayAI facilitator** (`facilitator.payai.network`).
It is proven end-to-end on **mainnet** and **devnet** through the shipping client.

## The proofs

Both driven by the shipping `X402Client.pay()` (`vool x402-pay`), fee-sponsored
by PayAI (`/verify` → `/settle`):

- **Mainnet** — real USDC: [`37o7An4A…BCMn7F5a`](https://explorer.solana.com/tx/37o7An4AUJDQqyZK2LvdGARHFpEQK3ZJiZSJwwADoeqeMHYUtSv9uddMzCcueBLU5p1S3fWDBb2FoWAMBCMn7F5a) — `success`, v0 tx, USDC moved −0.001 / +0.001, fee paid by PayAI. Proof bundle archived internally (not republished in the public tree).
- **Devnet**: [`4PL49Wio…E35z3e38`](https://explorer.solana.com/tx/4PL49WioTAcQng9HLqYhSb7RUYGu7n8YoHRKW54VNjMkV3Macbh4XTtfoT29peCMPDfrJjFMAuo1FhweE35z3e38?cluster=devnet) — same flow on `solana-devnet`. Proof bundle archived internally (not republished in the public tree); the explorer links above are independently verifiable.

## How it works (canonical x402 "exact" on Solana)

1. Payment requirements (`scheme`, `network`, `maxAmountRequired` in atomic units,
   `payTo`, `asset` mint, `extra.feePayer`) come from a resource server's HTTP 402
   — or are built directly by the client for a client-initiated payment.
2. The client builds a **v0 transaction**: ComputeBudget limit + price, then an SPL
   `TransferChecked` of `asset` from the payer's ATA to `payTo`'s ATA, with the
   facilitator's sponsored `feePayer` as the transaction fee payer. The payer
   **partially signs** (its slot only); the facilitator fills the feePayer
   signature at settle.
3. The base64 transaction is wrapped as the x402 payment payload and POSTed to the
   facilitator `/verify`, then `/settle`. `/settle` returns the on-chain signature.

The builder is `core/x402/client.py::build_solana_x402_payment`; the orchestration
is `X402Client._solana_pay`. The destination ATA must already exist (the
facilitator settles, it does not create accounts). The payer can be a Solana
keypair file or any signer (a VoolWallet is wrapped via `wallet_signer`).

## Run it yourself

```bash
# devnet by default; a dry run unless --allow-spend is passed
vool x402-pay <amount_usdc> <recipient_pubkey> \
  --keypair <payer.json> [--mainnet] [--asset <mint>] --allow-spend
```

- One facilitator host for every network (`PAYAI_FACILITATOR`); the network is a
  field in the payment, not a subdomain.
- The compute-unit limit is kept under the facilitator's sponsored-compute cap.
- The sponsored `feePayer` is read live from `GET /supported` (with a fallback).
- The recipient's associated token account for the asset must already exist.

## Notes

- The devnet proof transfers a self-minted 6-decimal SPL token (Circle's devnet
  USDC faucet is captcha-gated); the mainnet proof transfers real USDC. The code
  path, wire format, transaction, and facilitator flow are identical — only the
  network + `asset` mint differ, both selected by config.
- **`null://` dial:** the dial's pay step uses this client. The full
  dial → reach → pay → unlock round-trip additionally needs a public x402-gated
  receiver (the SSRF guard rejects loopback); that is the next step. The payment
  leg is proven here on both networks.
