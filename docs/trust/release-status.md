---
description: What stage VOOL is at, stated plainly.
---

# Release status

## Current stage

VOOL is in **beta**. The macOS build for Apple silicon is available to download;
Windows and Linux builds are coming next.

| | Status |
| --- | --- |
| Installers | macOS beta (Apple silicon, macOS 14+) |
| Code signing | Unsigned |
| macOS notarization | Not notarized |
| API stability | Changing between builds |

## What that means for you

* Your operating system will warn you on first launch. That is expected at this stage.
* Configuration formats can change between builds.
* Verify the published checksum before bypassing the warning — see
  [Install](../getting-started/install.md).

## Beta and pilot limitations, stated explicitly

| Capability | State in this build |
| --- | --- |
| Wallet (Crypto Pilot) | Optional, off by default; pilot lanes only — see [Wallet](../guides/wallet.md). |
| Mainnet transfers | Native coins only; the sole Mainnet token (USDC on Solana) exists for the x402 payment lane only. |
| Dictation | Temporarily unavailable in this beta build; it returns in an update. |
| Cloud "burst" ask mode | The per-call prompt is not wired yet in this build. |
| Skills/plugins catalogue | The public catalogue and authoring format are still being finalised. |

Signed and notarized builds are the next step on the release path.
