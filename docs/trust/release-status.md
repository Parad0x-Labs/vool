---
description: What stage VOOL is at, stated plainly.
---

# Release status

This page is the maintained release-status authority. Other documents state release facts
in passing; when they disagree with this page, this page wins.

## Current stage

VOOL is in **beta**. There is **no public installer release**: the supported install path
is from source — see [Install](../getting-started/install.md). Packaged downloads will
appear on the [releases page](https://github.com/Parad0x-Labs/vool/releases) when published.

A macOS arm64 disk image exists only as a **private draft release for owner review**
(`0.6.0-beta-migrated-20260919`): `VOOL-0.6.0-beta-macos-arm64.dmg`, built as a
self-contained bundle (macOS 14+) from the repository's initial public source commit
`e1034c9b`, not from current `main`. It is not publicly downloadable, and a filename alone
is not proof of what any separately distributed build contains.

| | Status |
| --- | --- |
| Public installers | None; source install is the supported route |
| Owner-review draft | macOS arm64 DMG only (private draft release, not published) |
| Published checksums | Draft-release `SHA256SUMS` only; no public checksum page |
| Code signing | Ad-hoc (macOS draft only); not Developer ID |
| macOS notarization | Not notarized |
| Signed update manifest | No configured update feed; automatic updates are not operational |
| API stability | Changing between builds |

Windows and Linux installer artifacts do not exist at any stage of the release path today.
Source installs on those platforms are covered by CI (Windows gauntlet, Linux test shards),
which is source compatibility, not a shipped desktop release.

## What that means for you

* If you run the owner-review draft on macOS, Gatekeeper will refuse it on first launch —
  ad-hoc signing without notarization is expected at this stage, not a sign of a corrupted
  download. Verify the checksum from the draft release before bypassing the warning.
* Configuration formats can change between builds.
* Do not expect automatic updates: no update feed is configured for any artifact.

## Beta and pilot limitations, stated explicitly

| Capability | State in this build |
| --- | --- |
| Wallet (Crypto Pilot) | Optional, off by default; pilot lanes only — see [Wallet](../guides/wallet.md). |
| Mainnet transfers | Native coins only; the sole Mainnet token (USDC on Solana) exists for the x402 payment lane only. |
| Dictation | Temporarily unavailable in this beta build; it returns in an update. |
| Cloud "burst" ask mode | The per-call prompt is not wired yet in this build. |
| Skills/plugins catalogue | The public catalogue and authoring format are still being finalised. |

Signed and notarized builds, published checksums and a configured update feed are the next
steps on the release path; none of them is claimed today.
