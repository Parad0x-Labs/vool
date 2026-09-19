---
description: The optional Crypto Pilot wallet — networks, x402 payments, DNA fees and recovery, as this build actually ships them.
---

# Wallet

The wallet is **optional and off by default**. It exists so VOOL can hold a small amount of
crypto for AI services that charge per request. Nothing about it is required to use VOOL.

This page describes what the current build actually supports. The wallet is in a **pilot**
state: capabilities are added lane by lane, and each lane below says exactly what it does.

## Network environments

Accounts, balances and receipts belong to one environment and never move between them:

* **Test networks** — the four long-standing test lanes (Solana Devnet and friends) plus
  Robinhood Chain Testnet.
* **Mainnet** — added by the Crypto Pilot: Solana, Base and Ethereum mainnet rows.

Switch deliberately in **Settings → Crypto → Developer options**. A request for a network in
the other environment is refused with nothing created, proposed or signed.

{% hint style="warning" %}
Earlier documentation said mainnet was impossible in this build. That was true before the
pilot and is no longer: mainnet rows exist now. What remains true is narrower — see the
transfers and payments sections below for exactly which lanes exist on mainnet.
{% endhint %}

## What moves on which lane

* **Pilot transfers move native coins only** (SOL on Solana, ETH on Base/Ethereum).
* **The one Mainnet token is USDC on Solana**, and it exists for the x402 payment lane only:
  automatic payments to AI services. Proposals refuse it for every other origin.
* Every transfer is previewed (a **quote**: amount, fee ceiling, recipient, network) and needs
  your explicit approval bound to exactly that preview. Change anything and the approval is
  void — nothing is signed.

## How a payment is approved

1. VOOL prepares a proposal with the exact amounts and a fee ceiling.
2. You approve it with your PIN/password (or device presence where available).
3. The payment is signed and sent, and a receipt is recorded.

Approvals do not transfer between proposals. A wrong PIN signs nothing. Too many wrong
attempts locks the wallet for a while — deliberately, with a stated wait.

## x402 automatic payments

x402 is a per-request payment protocol some AI services speak. When a paid resource uses it:

* Payments go through the same wallet caps and approval flow.
* Automatic payments are capped per payment; a resource above the cap is **not paid** (you can
  raise the cap deliberately or pay it by explicit proposal).
* If a payment could not complete, the outcome is reported as **unknown** — check its status
  before retrying; VOOL will not invite a blind duplicate payment.

## DNA service fee

The built-in accountless x402 route charges a **10 basis point** service fee on each accepted
provider payment, shown apart from the payment and apart from the network fee. Nothing else
acquires it: ordinary sends, wallet creation, local inference and your own API-key (BYOK)
calls carry **no** service fee. Accrued fees are collected economically (with a later native
payment, under one approval, only when collection cost is bounded) — never by a surprise
separate charge.

## Recovery

* The **recovery phrase is shown exactly once**, at creation. There is no export door: private
  keys are never exported by this runtime, in any mode.
* A pilot wallet's backup key is likewise released once during setup. If you did not save it,
  cancel setup and do not send funds to that address.
* Lost a wallet? Recover from the phrase you wrote down at creation. A recovery proof that
  does not fit this exact wallet is refused before any write.
