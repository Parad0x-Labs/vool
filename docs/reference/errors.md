---
description: How to read a VOOL error, whether anything already happened, and where every code is explained.
---

# Errors and error codes

When something fails, VOOL says in plain words what happened, what may already have happened,
and what to do next. Each failure also has a stable code. You find it in the technical details
under an error, in the command line (`wallet_export_refused: …`), and in API responses. This
page explains how to read an error. The [Error Book](../ERROR_BOOK.md) explains every code.

## Reading an error

Check three things before you try again:

1. **What happened.** The message. It never contains internal paths or secrets.
2. **What may already have happened.** Most refusals stop before anything is sent or changed,
   and the message says so. Some failures cannot know whether an action finished, such as a
   timeout after a request went out. Then the message says the outcome is unknown and asks you
   to check before you retry. A payment is never sent twice by accident: a second send of the
   same payment is refused with
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **What to do next.** One concrete step.

The code carries the meaning. An HTTP status such as 403 or 429 only says how a request fared
on the way, and two different refusals can share one status. Read the code, not the status.

## Codes and the Error Book

A code is never renamed and never reused for a different meaning. The
[Error Book](../ERROR_BOOK.md) is generated from the app's own error catalog, so the codes,
messages and next steps there are exactly what the app shows. Every code has its own section:
search the page for the code as you were given it, or link to the section with the book's
address, `#`, and the code with underscores as hyphens, such as
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

The codes you are most likely to see:

| Code | Title | What may already have happened |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Permission denied | Nothing was changed. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Provider unreachable | Nothing was sent. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Provider limit reached | The request reached the provider. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Timed out | Unknown: check before you retry. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Cancelled | Unknown: part of it may already have run. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Stored credential could not be read | Nothing was sent or changed. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Unexpected error | Unknown: check before you retry. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Approval no longer matches the preview | Nothing was signed or sent. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Payment could not be broadcast | Unknown: check the payment's status before you retry. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Payment already proposed or sent | Nothing new was sent; the earlier payment is on record. |

## Errors and other outcomes

VOOL keeps these apart, and so does its wording:

* **An error.** Something failed. The code and the message say what, and what to do.
* **Waiting for you.** A permission request or a payment approval is open. Nothing has run,
  and nothing will until you answer. See [Permissions](../concepts/permissions.md).
* **Stopped by a spend cap or price guard.** The refusal happens before the provider is
  contacted, and the message never suggests the provider was asked. See
  [Spending limits and price guards](../guides/spending-limits.md).
* **Cancelled.** You, or VOOL, stopped it before it finished. Part of it may already have run,
  so check what changed before you start it again.
* **Not possible here.** This build or this machine cannot do it, and the message names what
  can: a payment offer whose scheme VOOL cannot check from end to end, or an optional extra that
  is not installed.
* **Outcome unknown.** The action may or may not have finished, such as a payment whose
  broadcast never confirmed. Check its status first. VOOL never pays again blindly.

## Errors with their own codes

A few parts of VOOL explain their failures with their own short codes, on the screen where the
failure happens, and are not in the Error Book: attachments (for example a file that is too
large, or a credential found in a pasted file), app updates, project folders, model selection,
spend caps, contacts, plugins and skills, voice input, stored data, and bug reports. Their
messages say what happened and what to do in the same way. See
[Chats and attachments](../concepts/chats-and-attachments.md),
[Updates and migration](updates-migration.md) and
[Bug reporting and privacy](../trust/bug-reporting.md).

## A code you don't recognise

A code that is not in the Error Book did not come from this version of VOOL. Check which
version showed it, then search the [Error Book](../ERROR_BOOK.md) of that version. If it did
come from this version, report it with the code exactly as written, and with the reference
(`fault-` and twelve characters) when the response included one. A failure VOOL cannot
classify is shown as [`unknown`](../ERROR_BOOK.md#unknown), never as a guessed cause.
See [Bug reporting and privacy](../trust/bug-reporting.md).

## Troubleshooting specific symptoms

See [Troubleshooting](../help/troubleshooting.md) for install, model and platform problems,
and [Backup and recovery](../help/backup-and-recovery.md) for getting your chats, memory and
wallet back.
