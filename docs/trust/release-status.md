---
description: What stage VOOL is at, stated plainly.
---

# Release status

This page is the maintained release-status authority. Other documents state release facts
in passing; when they disagree with this page, this page wins.

## Current stage

VOOL is in **beta**. These docs describe **v0.8**. Its installers are on the
[GitHub releases page](https://github.com/Parad0x-Labs/vool/releases), each with a published
SHA-256 checksum. Installing from source with the bootstrap script is supported on macOS and
Linux; see [Install](../getting-started/install.md).

| Download | For |
| --- | --- |
| `…-macos-arm64.dmg` | Apple Silicon Macs, macOS 14 or newer |
| `…-macos-x86_64.dmg` | Intel Macs, macOS 14 or newer |
| `…-linux-x86_64.tar.gz` | Linux x86_64, tested on Ubuntu 22.04 and 24.04 |

This repository's release records cover the GitHub channel. Other download channels are
operated separately; verify any download against the checksum published on the GitHub
release.

| | Status |
| --- | --- |
| Public downloads | macOS Apple Silicon DMG, macOS Intel DMG, Linux x86_64 tarball on the GitHub release |
| Published checksums | SHA-256 for every download, on the release |
| Code signing | macOS apps are ad-hoc signed, not Developer ID |
| macOS notarization | Not notarized. macOS asks you to confirm the first open (see below) |
| Updates | VOOL updates itself on macOS and Linux when **Go online on its own** is on. Updates are signed and checked before they install. See [Updates and migration](../reference/updates-migration.md) |
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
| Crypto and wallet | A pilot, off by default. Turning it on creates, signs and pays nothing by itself. Every payment goes through VOOL's wallet, with its limits, your approval and a receipt. See [Wallet](../guides/wallet.md). |
| X | From any chat: search posts and accounts and read your own results. Anything that acts on your account waits for your approval. See [X integration](../guides/x-integration.md). |
| Dictation and voice conversation | The buttons are shown but unavailable in this beta build. |
| Local models | Before a local model can answer, VOOL checks it once: **Settings → Models & Providers → This Mac** (or **This computer**), then **Use this model**. |
| Intel Macs | Local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now. |
| Private chat | Say "private chat", "don't record this" or "off the record", and nothing from that chat is saved. |
| Forget | Forget clears VOOL's memory and the matching run history, so the item isn't recalled or listed again, and erases the forgotten words from VOOL's local memory file and its search index. See [Memory](../concepts/memory.md). |
| PDFs | Read in a locked-down sandbox on macOS and Linux. On Linux this needs bubblewrap or a kernel with Landlock. Scanned PDFs are read with the Mac's own text recognition, so Macs only. |
| Desktop pet | In the Mac app the pet can leave the window for the desktop. In a browser window it stays inside the page. |
| Lend a job | An early test version; jobs run on Macs only. See [Lend a job](../guides/lend-a-job.md). |
| Media | Media Studio edits video and audio when FFmpeg is installed. Pictures are made only with your own image-service key. VOOL makes no videos. See [Media Studio](../guides/media-studio.md). |

Developer ID signing and notarization for macOS, and signed checksums for Linux, are the
next steps on the release path; neither is claimed today.
