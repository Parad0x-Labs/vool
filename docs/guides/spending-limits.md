---
description: Spending limits, price guards, and what happens when a paid call is interrupted.
---

# Spending limits and price guards

VOOL never spends money on your behalf without an explicit ceiling you control. This page
describes the layers that guard paid model calls and paid resources, and what happens when a
paid call is interrupted part-way.

## Two prices, one rule

* **Catalog price** — what a provider currently advertises for a model. VOOL discovers it; it
  is information, not permission.
* **Your accepted price** — the ceiling you approved for that provider and model in Settings.
  A discovered price above your ceiling is refused **before** the provider is contacted. The
  refusal says exactly that: the model did not run, nothing was sent, nothing was charged.

## Spend caps

For paid calls, VOOL reserves money against caps before every call:

| Cap | Meaning |
| --- | --- |
| Per call | A single call's projected cost ceiling. |
| Per task | The most one task may spend across its calls. |
| Daily / monthly | Whole-period ceilings on paid usage. |
| Daily call count | A cap on the NUMBER of paid calls, independent of dollars. |

A call that would cross a cap is refused before it is sent. The refusal names the cap that
refused it (`per_task_spend_cap_exceeded` and its siblings) and the wording is deliberately
pre-send: *"running it would exceed your spend cap"* — never a claim that the provider was
contacted.

## A pinned model is never silently substituted

If you explicitly pick a paid model and a guard refuses it, VOOL does **not** quietly answer
with a cheaper model. The turn fails with the model you picked named, the actual gate named,
and the sentence *"no other model was substituted."* Pick a different model yourself, or raise
the cap deliberately.

## Paid, but the answer never arrived

If a paid call was charged but its answer did not arrive, the outcome is **unknown** — VOOL
will not pretend it failed or succeeded:

* No answer is invented in its place, and no refund is assumed.
* The payment is kept on record as unresolved.
* Settings lists it under **Paid calls waiting for an answer**, where **Resume** asks for the
  same answer once, **without paying again**.

This is why retrying blindly is never suggested for these cases: a plain re-send could pay
twice. Resume is the one supported path.

## Automatic wallet payments (x402)

Wallet-paid resources (see [Wallet](../guides/wallet.md)) carry their own automatic-payment
cap. A resource asking for more than the cap is simply not paid; you can raise the cap
deliberately or pay the resource through an explicit proposal with your approval.
