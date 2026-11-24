---
description: Languages, accessibility, activity history, receipts, and how model answers are verified.
---

# Languages, accessibility and records

## Interface languages

VOOL's screens come in 17 languages: English, Arabic, Chinese (Simplified), French, German,
Hebrew, Hindi, Japanese, Korean, Lithuanian, Polish, Portuguese, Russian, Spanish, Turkish,
Ukrainian and Vietnamese. Choose one with the app language menu at the top of **Settings**.
Translations are machine-made, and newer text may still show in English until a translation
lands.

The app language changes the screens only. The language VOOL answers in is a separate
setting: **Settings → General → Answer language**. Asking in a message ("answer in Polish")
still wins for that message.

Language selection is a presentation concern only: permission decisions, spending authority and error codes are
**language-independent by construction** — a translated error message can never widen or
narrow what the English authority decided.

## Accessibility

* Keyboard shortcuts for the main flows (see [Keyboard shortcuts](shortcuts.md)).
* Light and dark: **Settings → Appearance → Theme and text size** offers Light, Dark or
  "Same as this computer", and a larger text size for the conversation.
* Switches in Settings say **On** or **Off** in words as well as colour.
* Errors are stated as text, not colour alone; destructive actions always require an explicit
  confirmation rather than a single misclick.

## Activity

The activity view is the full history of what VOOL did: tool calls with their inputs and
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
