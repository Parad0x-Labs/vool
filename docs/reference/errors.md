---
description: Every error code VOOL can surface, what it means, whether anything already happened, and how to recover.
---

# Errors and error codes

When something fails, VOOL classifies the failure and shows you a message that says what
happened, what did **not** happen, and what to do. Many surfaces also carry the stable
machine code for the failure (in receipts, diagnostics and bug reports) — this page is its
reference.

## Reading an error

Three facts matter before you retry anything:

1. **What happened** — the message. It never contains internal paths or secrets.
2. **What may already have happened** — some refusals stop *before* anything is sent or
   changed; some failures genuinely cannot say whether an action completed. Where the outcome
   is unknown, the message says so and tells you to **check state before retrying** — never
   to blindly re-send (a duplicate payment is exactly the harm this avoids).
3. **The recovery action** — the concrete next step, not a shrug.

## Stable codes

Codes are language-independent and stable across releases: a code is never renamed or reused
for a different meaning. The complete, generated reference — every code with its meaning,
severity, retryability, effect state and recovery — lives in the
[Error Book](../ERROR_BOOK.md), which is generated from the runtime's own fault catalog and
cannot drift from it.

Codes you are most likely to see:

| Code | What it means | What may already have happened |
| --- | --- | --- |
| `permission_denied` | An action was not permitted; nothing was changed. | Nothing. |
| `provider_unavailable` | The model provider could not be reached. | Nothing billable left your machine. |
| `provider_exhausted` | The provider's usage/rate limit answered. | A request reached the provider. |
| `timeout` | Something took too long and was stopped. | Unknown — check state before retrying. |
| `credential_failure` | A stored credential could not be read. | Nothing. |
| `wallet_quote_mismatch` | The approval no longer matches the previewed transfer. | Nothing was signed or sent. |
| `wallet_broadcast_failed` | A payment could not be broadcast. | Unknown — check its status before retrying. |
| `wallet_duplicate_payment` | This payment was already proposed or sent. | An earlier proposal exists on record. |

## Spending refusals are pre-send

When a spend cap or your accepted price refuses a call, the refusal happens **before** the
provider is contacted. The message will never imply the provider was asked — see
[Spending limits](../guides/spending-limits.md).

## Troubleshooting specific symptoms

See [Troubleshooting](../help/troubleshooting.md) for install, model and platform problems.
