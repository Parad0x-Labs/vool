---
description: What VOOL actually did for an answer, where it ran, and the signed record you can check it against.
---

# Receipts

An answer that says "Done!" is only words. A receipt is what VOOL recorded about the work itself.
It is read from the tool's own result and, where it can, checked against the real thing, never taken
from the answer's text.

VOOL gives you three kinds, from quick to detailed:

1. **Action receipts** under an answer: what each action did.
2. **The proof chip** under an answer: where it ran, what it did, its sources and its cost.
3. **Signed records** in the Activity panel: a tamper-evident record of each turn.

## Action receipts

When an answer changed something, such as setting a reminder, moving a file, sending an email or
saving a memory, a line for each action sits under the answer, always visible. It names the action,
the facts VOOL recorded (the time with its time zone, the file and its folder, who it went to) and
one of these states:

| State | Meaning |
| --- | --- |
| Done, checked | The tool said done, and VOOL read it back: the reminder is in the list, the file is where it should be, the memory is saved. |
| Done | The tool said done, and there was nothing VOOL could read back. |
| Sent | The message was handed to the mail server or chat service. Delivery itself cannot be known. |
| Waiting for your OK | The action is waiting for your approval. Nothing has happened yet. |
| Not done | The action did not happen. The tool's own reason is shown. |
| Reported done, but VOOL could not find it | The tool said done, but the read-back disagrees: the reminder or file is not there. |

Actions that only read something get no receipt line.

## The proof chip

Under each saved answer is a small chip, for example
**On this computer · 2 actions · 1 source · 512 tok · Record verified**. Press it to open the full
record:

* **Where this ran**: each model the answer used and where, how much text was sent, and whether it
  included things VOOL remembers about you, this chat so far, or results of tools. A closing line
  says, for example, **Nothing you wrote left this computer.** See
  [Check your VOOL](../guides/check-your-vool.md#where-each-answer-was-answered).
* **Actions**, **Sources** and **Receipts** for the turn.
* Cost: **charged** when the provider reported a bill, an **estimate** when VOOL priced the words
  itself, or **cost unreported**.
* **Verification scope**: what the checks cover.

The record states are **Record verified**, **Record recorded**, **Record incomplete** and
**Record unverified**. A verified record means the saved answer and its recorded actions are intact.
It does not mean every fact or number in the answer was checked.

When a paid answer brings today or this month near your spending limit, a warning with
**Change limits** appears beside the chip.

Private chat messages have no chip, because nothing about them is saved.

## Signed records

Open the Activity panel and choose **Records**. Each turn has one record that binds:

* what the answer claimed it did, and the tools that really ran;
* a verdict, for example `clean` (every claimed action really ran) or `blocked_false_claim` (a
  claim with nothing behind it was caught and stopped);
* fingerprints (hashes) of the question and the answer, never their text;
* a link to the turn before, so a changed or removed record breaks the chain;
* a signature made with this computer's own key.

Open a record to see **Receipt id**, **Issued**, **Verdict**, **Content hash**, **Signed**,
**Signer**, **Executed tools** and **Claimed actions**. **Copy hash** and **Copy receipt** copy them
for checking elsewhere. The panel says **Receipt chain valid** when the records present agree with
each other. That does not prove the history is complete or that the answer is right.

## Where receipts are kept

Receipts and records are kept on this computer, not in your project folders. A receipt leaves your
computer only inside a bug report that you preview and choose to send, and then only the model,
tokens, time and which steps ran, never their content. See
[Bug reporting and privacy](../trust/bug-reporting.md).
