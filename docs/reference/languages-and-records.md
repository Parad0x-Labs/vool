---
description: Languages, accessibility, activity history, receipts, and how model answers are verified.
---

# Languages, accessibility and records

## Interface languages

VOOL's interface is available in multiple languages. Language selection is a presentation
concern only: permission decisions, spending authority and error codes are
**language-independent by construction** — a translated error message can never widen or
narrow what the English authority decided.

## Accessibility

* Keyboard shortcuts for the main flows (see [Keyboard shortcuts](shortcuts.md)).
* Light and dark presentation in the docs and app surfaces, following your system preference
  with a manual override.
* Errors are stated as text, not colour alone; destructive actions always require an explicit
  confirmation rather than a single misclick.

## Activity

The activity view is the honest history of what VOOL did: tool calls with their inputs and
results, approvals you granted or refused, and payments with their receipts. A turn that was
stopped by a guard shows the stop and the guard — a refusal is a recorded event, not an
absence.

## Receipts

Money and spend-relevant actions write **receipts**: what was proposed, what was approved,
what was sent, and what settled. Unknown outcomes are recorded as unknown — never narrated
into success or failure. This is what makes *"check its status before retrying"* actionable:
the receipt tells you which state the payment is actually in.

## Model-verification status

VOOL distinguishes what a model **said** from what the runtime **proved**:

* Tool results are recorded with their execution records; an answer's claims about files,
  payments or data are bound to those records, and unsupported claims are withheld.
* Receipts carry a verification status; where a lane is experimental or unverifiable in this
  build, the status says so rather than implying proof.

See [Receipts](../concepts/receipts.md) for the concept and [Errors](errors.md) for how
failures are classified.
