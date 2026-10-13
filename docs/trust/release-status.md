---
description: What stage VOOL is at, stated plainly.
---

# Release status

This page is the maintained release-status authority. Other documents state release facts
in passing; when they disagree with this page, this page wins.

## Current stage

VOOL is in **beta**. The current release is
[**v0.7.0-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta), a GitHub
pre-release built from `main` at `ef99b3e`. It has app downloads for three platforms,
each with a published SHA-256 checksum. Installing from source with the bootstrap script
remains supported on macOS, Linux and Windows; see [Install](../getting-started/install.md).

| Download | For |
| --- | --- |
| `VOOL-0.7.0-macos-arm64.dmg` | Apple Silicon Macs, macOS 14 or newer |
| `VOOL-0.7.0-macos-x86_64.dmg` | Intel Macs, macOS 14 or newer |
| `vool-0.7.0-beta-linux-x86_64.tar.gz` | Linux x86_64, tested on Ubuntu 22.04 and 24.04 |

This repository's release records cover the GitHub channel. Other download channels are
operated separately; verify any download against the checksum published on the GitHub
release.

| | Status |
| --- | --- |
| Public downloads | macOS Apple Silicon DMG, macOS Intel DMG, Linux x86_64 tarball on the v0.7.0-beta GitHub release |
| Published checksums | SHA-256 for every download, on the release |
| Code signing | macOS apps are ad-hoc signed, not Developer ID. Signing is added after this beta and does not gate it |
| macOS notarization | Not notarized. macOS asks you to confirm the first open (see below) |
| Windows | No Windows download in this release; Windows installs from source and is experimental |
| Updates | 0.7.0 is installed by download. It carries one-press signed updates with rollback; the first update offered through it will be 0.7.1. Update checks stay off until you allow them, and an update installs only when it is signed by the pinned publisher key. See [Updates and migration](../reference/updates-migration.md) |
| API stability | Changing between builds |

## What that means for you

* On first launch of a Mac app, macOS refuses to open it because it is not notarized.
  This is expected at this stage, not a sign of a damaged download. Verify the checksum,
  then open **System Settings → Privacy & Security** and click **Open Anyway**. On
  macOS 14 you can instead Control-click the app and choose **Open**.
* Pick the Mac download that matches your chip. The Apple Silicon app does not run on an
  Intel Mac and says so at launch.
* Configuration formats can change between builds.
* Nothing goes online on a fresh install until you turn it on, update checks included.

## Beta and pilot limitations, stated explicitly

| Capability | State in this build |
| --- | --- |
| Crypto and wallet | Off by default. When you turn it on, VOOL uses mainnet; test networks are in Developer options. Every payment goes through VOOL's wallet, with its limits, your approval and a receipt. See [Wallet](../guides/wallet.md). |
| Paying for web content (x402) | Test networks only in this release: Solana Devnet, Base Sepolia, Ethereum Sepolia and BNB testnet. Mainnet is planned for 0.7.1. |
| X | Drafting posts in chat, plus a guided setup that checks your keys read-only. No posting from VOOL in this release. |
| Dictation | Temporarily unavailable in this beta build; it returns in an update. |
| Cloud "burst" ask mode | The per-call prompt is not wired yet in this build. |
| Desktop pet | Dragging the pet out of the VOOL window does not move it to the desktop. Use right-click > **Move to desktop**, or the button in Settings. A fix is planned for 0.7.1. |
| Skill Codex | The tab is on the Add-ons page. Browsing is not open yet in this release, and the tab says so. |

Developer ID signing and notarization for macOS, and signed checksums for Linux, are the
next steps on the release path; neither is claimed today.
