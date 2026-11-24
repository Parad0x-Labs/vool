---
description: Reporting a bug safely — redacted drafts, exact-byte approval, and what never leaves your machine.
---

# Bug reporting and privacy

## The built-in bug reporter

Press **Report a problem** at the bottom of the chat sidebar. The reporter
never sends anything by itself. It works in three stages:

1. **Draft** — a report is built locally from recent logs and diagnostics, redacted as it is
   built. *"Nothing is sent yet — a local draft is created first."*
2. **Preview** — you see the **exact bytes** that would be sent, with a payload hash and a
   redaction summary listing what was removed. Fields you do not want to send (logs, flags,
   repro notes, attachments) can be removed; any edit resets approval.
3. **Approve** — sending requires your explicit approval of those exact bytes. If the payload
   changes, the approval no longer binds and you must approve again.

Press **Approve these exact bytes** to approve, or **Withdraw approval** to take it back. After you
approve, **Open on GitHub** opens a filled-in report page on the public
[vool-reports](https://github.com/Parad0x-Labs/vool-reports) repository in your browser. Read it
there once more and press Submit. This way VOOL itself sends nothing, and no GitHub token is needed. If you
saved a GitHub token in VOOL, **Send with my GitHub token** files the approved bytes for you instead.
**Copy report** and **Save a local copy instead** need no approval at all: they hand you the same
sanitized bytes and nothing leaves your machine. **My reports** lists your drafts and how each one
left. Deleting one there removes the local copy; a report already filed on GitHub stays there.

## Report this answer

Every saved answer has **Report** and a thumbs-down button beside Copy. To quote only part of an
answer, highlight that part and press **Report** in the small bar that appears. Then:

1. Choose **What was wrong?**: **Wrong or made up**, **Out of date**, **Didn't do what I asked**,
   **Remembered or recalled something wrong**, **An action went wrong**, **Poor answer**,
   **Unsafe or rude** or **Other**.
2. Optionally fill in **Tell us more** and **What should it have said?**
3. Press **Build the report**. You then get the same preview and approval as above.

Use **Remembered or recalled something wrong** when VOOL used a wrong memory, or recalled
something from another chat that is not right. To fix the memory itself, edit or forget the item in
**Settings → Memory & Personalisation** (see [Memory](../concepts/memory.md)).

The report quotes only the part you chose, or the whole answer. Your question is left out unless you
tick **Include my question**. **Include the answer's receipt** is ticked to start with: it adds the
model, provider, tokens, time and which steps ran, never their content (see
[Receipts](../concepts/receipts.md)).

An answer you asked VOOL to forget cannot be reported, and answers in a private chat have no
**Report** button, because nothing about them is saved.

## What is added to help investigate

Your computer's shape: OS version, chip, memory, GPU, and the local model server version. Also a
short health check: whether the local model server answers, and free disk space as a range. Never
your computer's name, your user name, serial numbers or network addresses. You can remove any of it
in the preview.

## What redaction removes

Deterministically, before you ever see the draft: credentials and auth headers, home paths
and usernames, your computer's name, emails, IP addresses, PINs and one-time codes, card and
bank (IBAN) numbers, phone numbers, wallet addresses, the personal details saved in your
profile, labelled secrets and high-entropy blobs — layered under a final secret-scan pass.
VOOL can't recognise names or private facts typed in ordinary sentences, so read the preview. Stack traces are reconstructed from an allowlist, not pasted raw.
Fault evidence in reports carries **typed codes only** — never internal context or cause
chains.

Each removed value is replaced by a marker. An email, a person, a home folder, an IP address,
a phone number, a wallet address, your computer's name or a detail from your profile keeps one
numbered marker throughout the report, such as `[EMAIL_1]` and `[EMAIL_2]` for two different
addresses, so the report still reads clearly.
Every removed key, password, PIN, card or bank number shows as `[SECRET_REMOVED]`, never
numbered. The list that links a marker to the real value is kept only in VOOL's memory while
you work on the report, so an edit keeps the same markers. It is never saved or sent, and it
is forgotten when you delete the report, after an hour unused, or when VOOL restarts.

## What VOOL never does

* No automatic external reporting of any kind.
* No telemetry pipeline waiting behind a setting.
* Security issues go through the private channel in the repository's
  [security policy](https://github.com/Parad0x-Labs/vool/security/policy) — never a
  public issue.

## Privacy summary

* With a local model selected, prompts and files are processed on your machine.
* Cloud calls use the API key you supplied, stored locally; keys never ride in reports.
* The full data-flow detail is in [Data handling](../trust/data-handling.md).
