---
description: What stage VOOL is at, stated plainly.
---

# Release status

This page is the maintained release-status authority. Other documents state release facts
in passing; when they disagree with this page, this page wins.

## Current stage

VOOL is in **beta**. The current release is
[**v0.7.5-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.5-beta), a GitHub
release built from `main` at `6466e8b`. It has app downloads for three platforms,
each with a published SHA-256 checksum. Installing from source with the bootstrap script
remains supported on macOS, Linux and Windows; see [Install](../getting-started/install.md).

| Download | For |
| --- | --- |
| `VOOL-0.7.5-beta-macos-arm64.dmg` | Apple Silicon Macs, macOS 14 or newer |
| `VOOL-0.7.5-beta-macos-x86_64.dmg` | Intel Macs, macOS 14 or newer |
| `vool-0.7.5-beta-linux-x86_64.tar.gz` | Linux x86_64, tested on Ubuntu 22.04 and 24.04 |

This repository's release records cover the GitHub channel. Other download channels are
operated separately; verify any download against the checksum published on the GitHub
release.

| | Status |
| --- | --- |
| Public downloads | macOS Apple Silicon DMG, macOS Intel DMG, Linux x86_64 tarball on the v0.7.5-beta GitHub release |
| Published checksums | SHA-256 for every download, on the release |
| Code signing | macOS apps are ad-hoc signed, not Developer ID. Signing is added after this beta and does not gate it |
| macOS notarization | Not notarized. macOS asks you to confirm the first open (see below) |
| Windows | No Windows download in this release; Windows installs from source and is experimental |
| Updates | From 0.7.5 on, VOOL updates itself: with "Go online on its own" on, it shows a notice and installs a new version with one press. Updating from 0.7.4 or 0.7.3 on a Mac: turn on "Go online on its own" (Settings > Privacy & Permissions) and press Check for updates once. On Linux, or from 0.7.2, 0.7.1 or 0.7.0: download 0.7.5 and install it over your current version. Your chats and settings live in your profile, outside the app. See [Updates and migration](../reference/updates-migration.md) |
| API stability | Changing between builds |

## What that means for you

* On first launch of a Mac app, macOS refuses to open it because it is not notarized.
  This is expected at this stage, not a sign of a damaged download. Verify the checksum,
  then open **System Settings → Privacy & Security** and click **Open Anyway**. On
  macOS 14 you can instead Control-click the app and choose **Open**.
* Pick the Mac download that matches your chip. The Apple Silicon app does not run on an
  Intel Mac and says so at launch.
* Configuration formats can change between builds.
* Crypto features are off until you turn them on.

## Beta and pilot limitations, stated explicitly

| Capability | State in this build |
| --- | --- |
| Crypto and wallet | Off by default. When you turn it on, VOOL uses mainnet; test networks are in Developer options. Every payment goes through VOOL's wallet, with its limits, your approval and a receipt. See [Wallet](../guides/wallet.md). |
| Mainnet transfers | Native coins, and USDT on Solana, Ethereum, BNB Smart Chain and Arbitrum One, sent from a VOOL Wallet created on this device. USDC on Solana and Base is for payment channels only. A connected or watch-only account can receive and show its balance but cannot send from VOOL. |
| Paying with x402 | Websites that ask for x402 payment in USDC on Base or Base Sepolia can be paid from your Crypto Pilot wallet: one approval with your PIN, within your limits. The website's payment service covers the network fee. **Settings → Crypto → Payment channels** has an on/off switch for each way the wallet can pay a website; a channel that's off refuses before anything is signed. No live mainnet payment has been made with this yet. Paying through Solana pay-kit is not included in the downloads; that channel shows as not usable. |
| X | From any chat: search posts and accounts, Spaces, trends and DMs, and look up past calls on a token. Anything that acts on your account waits for your OK. |
| Dictation | Temporarily unavailable in this beta build; it returns in an update. |
| Cloud "burst" ask mode | The per-call prompt is not wired yet in this build. |
| Local models | Before a local model can answer, open **Settings → Models & Providers → Local models** and press **Certify** for it once. |
| Intel Macs | Local models run on the processor and are slow. Certification allows for slower processors, but a long memory answer can time out, so use a cloud model there for now. |
| Private chat | Say "private chat", "don't record this" or "off the record", and nothing from that chat is saved. The commands work in all 17 app languages. |
| Forget | Forget clears VOOL's memory and the matching run history, so the item isn't recalled or listed again, and erases the forgotten words from VOOL's local memory file and its search index. VOOL keeps the words you asked it to forget in a short list on your computer, so it can keep refusing them. After you forget something in a chat, VOOL won't take that same fact back in that chat; start a new chat to tell it again. |
| Scanned PDFs | Read with the Mac's built-in text recognition, so Macs only. |
| Desktop pet | The pet lives in the VOOL window. Dragging it out of the window does not move it to the desktop. |
| Skill Codex | Browse Skill Codex shows the skills listed for your exact VOOL version, and you review a skill before it installs. Publish to Skill Codex checks a skill you made on your computer, then opens the Skill Codex website with it filled in; you sign in there and press Send, and VOOL sends nothing itself. Only the skill's text goes along, so a skill with extra files cannot be shared yet. |

Developer ID signing and notarization for macOS, and signed checksums for Linux, are the
next steps on the release path; neither is claimed today.
