---
description: A receipt is the record of what a run actually did.
---

# Receipts

A receipt is the durable record of a run: which tools were called, with what inputs, and
what each returned.

## Why they exist

An agent's summary of its own work is a claim. The receipt is the record you can check
that claim against.

## What is in one

| Field | Meaning |
| --- | --- |
| `tool` | Which action ran |
| `input` | What it was given |
| `result` | What it returned |
| `time` | When it ran |

Receipts are kept in VOOL's runtime home on this computer (see
[Configuration](../reference/configuration.md)), not in your project folders. A receipt leaves
your machine only inside a bug report that you preview and choose to send (see
[Bug reporting and privacy](../trust/bug-reporting.md)).
