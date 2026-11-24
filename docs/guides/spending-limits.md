---
description: Spending limits and price guards for paid AI models and wallet payments.
---

# Spending limits and price guards

VOOL never spends money on your behalf without an explicit ceiling you control. This page
describes the layers that guard paid model calls and paid resources.

## Two prices, one rule

* **Catalog price** — what a provider currently advertises for a model. VOOL discovers it; it
  is information, not permission.
* **Your accepted price** — the price you confirmed when you picked that paid model. If the
  provider later advertises a higher price, VOOL stops and asks you again **before** the provider
  is contacted. Nothing is sent and nothing is charged until you decide.

## Spend caps

For paid calls, VOOL reserves money against caps before every call:

| Cap | Meaning |
| --- | --- |
| Per call | A single call's projected cost ceiling. Default 0.25 USD. |
| Per task | The most one task may spend across its calls. Default 1 USD. |
| Daily / monthly | Whole-period ceilings on paid usage. Defaults 5 USD a day, 25 USD a month. |

A call that would cross a cap is refused before it is sent. The message names the cap and says
*running it would exceed your spend cap*: the provider was never contacted. Paid models run only
when you pick one yourself; VOOL never falls back to a paid model on its own.

## A pinned model is never silently substituted

If you explicitly pick a paid model and a guard refuses it, VOOL does **not** quietly answer
with a cheaper model. The turn fails with the model you picked named, the actual gate named,
and the sentence *"no other model was substituted."* Pick a different model yourself, or raise
the cap deliberately.

## Automatic wallet payments (x402)

Websites that ask for x402 payment in USDC on Base can be paid from your wallet, one approval
each, within your wallet's spending caps. No live mainnet payment has been made with this yet.

Wallet payments have their own caps (per payment, per day, per recipient per day), separate from
the model caps above, and a cap on what one paid web request may cost: 0.10 USDC. A request above
it is not paid and no payment is proposed. See [Wallet](wallet.md) and
[Pay for web resources](paid-resources.md).
