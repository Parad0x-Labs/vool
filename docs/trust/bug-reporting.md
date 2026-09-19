---
description: Reporting a bug safely — redacted drafts, exact-byte approval, and what never leaves your machine.
---

# Bug reporting and privacy

## The built-in bug reporter

Use the in-app bug reporter (or the `vool bug-report` CLI). It never sends anything by
itself — the flow is deliberately three-stage:

1. **Draft** — a report is built locally from recent logs and diagnostics, redacted as it is
   built. *"Nothing is sent yet — a local draft is created first."*
2. **Preview** — you see the **exact bytes** that would be sent, with a payload hash and a
   redaction summary listing what was removed. Fields you do not want to send (logs, flags,
   repro notes, attachments) can be removed; any edit resets approval.
3. **Approve** — sending requires your explicit approval of those exact bytes. If the payload
   changes, the approval no longer binds and you must approve again.

A **local copy** needs no approval at all: it writes the same sanitized bytes to a file on
your machine.

## What redaction removes

Deterministically, before you ever see the draft: credentials and auth headers, home paths
and usernames, emails, IP addresses, labelled secrets and high-entropy blobs — layered under
a final secret-scan pass. Stack traces are reconstructed from an allowlist, not pasted raw.
Fault evidence in reports carries **typed codes only** — never internal context or cause
chains.

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
