---
description: Notable changes to VOOL.
---

# Changelog

Entries are added as builds go out to testers. Newest first.

## 0.7.0-beta — 2026-10-13

Downloads for Apple Silicon Macs, Intel Macs and Linux x86_64 are on the
[v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta).

* **Runs on more computers.** Separate downloads for Apple Silicon Macs, Intel Macs and
  Linux x86_64. The Mac app carries its own local-model runner.
* **Signed updates, starting next release.** This version carries one-press signed updates
  with rollback, and update checks stay off until you allow them. The first update offered
  through it will be 0.7.1; install 0.7.0 itself from the download.
* **Better memory.** VOOL recalls what you told it, forgets on request, and says so when
  there was nothing to forget.
* **Your rules.** Set rules for everything, one chat or one project. Ask "what are my
  rules", and remove one by its number.
* **Personal assistant fixes.** Reminders and calendar use your time zone, past reminder
  times are refused, all-day events land on the right day, and email connections check
  the server's certificate.
* **Chain data in chat.** Add your own Helius, Alchemy or Solscan key for read-only chain
  data in chat. With no key, no network call is made.
* **X.** Draft posts in chat, with a guided setup that checks your keys read-only.
* **Report a wrong answer.** Report and 👎 on every answer; you see the report text, with
  private details removed, before it is sent.
* **Skill Codex** is on the Add-ons page. Browsing is not open yet in this release.
* **More languages.** 16 languages besides English.
* **Crypto stays off by default.** Paying websites with x402 works on test networks only.
  The pay-per-call option in the model marketplace in Settings can pay from the Solana
  Mainnet wallet, one approval per call with the 0.1% fee shown; it has not yet been tested
  on a real chain.

The Mac apps are not notarized yet: see
[first open on a Mac](../getting-started/install.md#first-open-on-a-mac).
