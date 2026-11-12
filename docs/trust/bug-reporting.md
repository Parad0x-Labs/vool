---
description: Reporting a bug safely — redacted drafts, exact-byte approval, and what never leaves your machine.
---

# Bug reporting and privacy

## The built-in bug reporter

Use the in-app bug reporter (or the `python -m apps.vool_cli bug-report` CLI). It never sends anything by
itself — the flow is deliberately three-stage:

1. **Draft** — a report is built locally from recent logs and diagnostics, redacted as it is
   built. *"Nothing is sent yet — a local draft is created first."*
2. **Preview** — you see the **exact bytes** that would be sent, with a payload hash and a
   redaction summary listing what was removed. Fields you do not want to send (logs, flags,
   repro notes, attachments) can be removed; any edit resets approval.
3. **Approve** — sending requires your explicit approval of those exact bytes. If the payload
   changes, the approval no longer binds and you must approve again.

After you approve, **Open on GitHub** opens a filled-in report page on the public
[vool-reports](https://github.com/Parad0x-Labs/vool-reports) repository in your browser. Read it
there once more and press Submit. VOOL itself sends nothing, and no GitHub token is needed.
**Copy report** and **Save a local copy** need no approval at all: they hand you the same sanitized
bytes and nothing leaves your machine. **My reports** lists your drafts and how each one left.

## Reporting a wrong answer

Press **Report** or 👎 under any answer, or highlight the wrong part and press **Report**. The
report quotes only that part (or the whole answer). Your question is left out unless you tick the
box. The answer's receipt is included: model, provider, tokens, time, and which steps ran, never
their content. An answer you asked VOOL to forget cannot be reported.

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
