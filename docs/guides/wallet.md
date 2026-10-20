---
description: The optional Crypto Pilot wallet — networks, x402 payments, DNA fees and recovery, as this build actually ships them.
---

# Wallet

The wallet is **optional and off by default**. It exists so VOOL can hold a small amount of
crypto for AI services that charge per request. Nothing about it is required to use VOOL.

This page describes what the current build actually supports. The wallet is in a **pilot**
state: capabilities are added lane by lane, and each lane below says exactly what it does.

## Network environments

When Crypto is on, VOOL runs on **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB and
Arbitrum One.
Normal setup never asks you to pick a network environment.

* **Mainnet** — the default for every install, including installs upgraded with older
  test-network accounts. Every spending cap, approval and receipt applies as described below.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet and the other test lanes. For
  developers only: switch in **Settings → Crypto → Developer options**.

Accounts, balances and receipts belong to one environment and never move between them; accounts
on the other environment stay listed on their own network. A request for a network in the other
environment is refused with nothing created, proposed or signed.

{% hint style="warning" %}
Earlier documentation said mainnet was impossible in this build. That was true before the
pilot and is no longer: mainnet rows exist now. What remains true is narrower — see the
transfers and payments sections below for exactly which lanes exist on mainnet.
{% endhint %}

## What moves on which lane

* **Transfers move native coins** (for example SOL on Solana, ETH on Base, Ethereum or
  Arbitrum One), and **USDT on Solana, Ethereum, BNB Smart Chain and Arbitrum One**.
  The chain is part of each saved contact address and each approval. Token transfers also
  need that chain's native coin for fees; the quote shows both amounts within your limits.
* **USDC on Solana and Base is available for payment channels only.** It is not offered
  for ordinary sends. The wallet's main balance display shows native coins, not a complete
  token portfolio.
* Every transfer is previewed (a **quote**: amount, fee ceiling, recipient, network) and needs
  your explicit approval bound to exactly that preview. Change anything and the approval is
  void — nothing is signed.
* A name matching several contacts or wallet addresses asks you to choose. Review the exact
  address and chain. A changed address or renamed contact invalidates the old review.
* On mainnet, only a VOOL Wallet created on this device sends. An external wallet you connected
  or a watch-only address can receive and show its balance; a send or a payment-channel
  payment from it is refused before any approval is asked, and nothing is signed.
* Activity records payments made through VOOL and their receipts. It is not a complete
  incoming-payment history. No swap or bridge action is offered by this wallet.

## How a payment is approved

1. VOOL prepares a proposal with the exact amounts and a fee ceiling.
2. You approve it with your PIN/password (or device presence where available).
3. The payment is signed and sent, and a receipt is recorded.

Approvals do not transfer between proposals. A wrong PIN signs nothing. Too many wrong
attempts locks the wallet for a while — deliberately, with a stated wait.

## x402 automatic payments

x402 is a per-request payment protocol some websites and AI services speak. **Websites that
ask for x402 payment in USDC on Base** (or Base Sepolia, in Developer options) can be paid
from your Crypto Pilot wallet: one approval with your PIN, within your limits. The website's
payment service covers the network fee. **No live mainnet payment has been made with this
yet.** The other test networks (Solana Devnet, Ethereum Sepolia and BNB testnet) stay in
Developer options. **Settings → Crypto → Payment channels** lists every way the wallet can
pay a website, each with its own on/off switch; a channel that's off refuses before anything
is signed. Paying through Solana pay-kit is not included in the downloads, so that channel
shows as not usable. **Model marketplace:** the pay-per-call option in the model marketplace in Settings can pay from the Solana Mainnet
Crypto Pilot wallet in USDC or SOL. Each call waits for your approval within your wallet
limits and shows the 0.1% VOOL fee. That path has not yet been tested on a real chain.
When a paid resource uses x402:

* Payments go through the same wallet caps and approval flow.
* Automatic payments are capped per payment; a resource above the cap is **not paid** (you can
  raise the cap deliberately or pay it by explicit proposal).
* If a payment could not complete, the outcome is reported as **unknown** — check its status
  before retrying; VOOL will not invite a blind duplicate payment.

## DNA service fee

The model marketplace's pay-per-call x402 route charges a **0.1% (10 basis point)** service fee on each accepted
provider payment, shown apart from the payment and apart from the network fee. Nothing else
acquires it: ordinary sends, wallet creation, local inference and your own API-key (BYOK)
calls carry **no** service fee. Accrued fees are collected economically (with a later native
payment, under one approval, only when collection cost is bounded) — never by a surprise
separate charge.

## Recovery

* The **recovery phrase is shown exactly once**, at creation.
* A pilot wallet's backup key is likewise released once during setup, and there is no later
  export for it. If you did not save it, cancel setup and do not send funds to that address.
* An older VOOL Wallet, made before Crypto Pilot, can export its private key once for import into
  another wallet app. The export opens only with your Mac's own sign-in (Touch ID or your Mac password),
  never with the wallet PIN, and the key is shown once without being stored.
* Lost a wallet? Recover from the phrase you wrote down at creation. A recovery proof that
  does not fit this exact wallet is refused before any write.
