---
description: What stage VOOL is at, stated plainly.
---

# Release status

This page is the maintained release-status authority. Other documents state release facts
in passing; when they disagree with this page, this page wins.

## Current stage

VOOL is in **beta**. The release supported by this repository is **from source** — see
[Install](../getting-started/install.md). Nothing is published on the
[releases page](https://github.com/Parad0x-Labs/vool/releases): the only GitHub release is
a **private draft** (`0.6.0-beta-migrated-20260919`) for owner review.

Two distinct macOS artifacts exist across the verified channels (checked 2026-09-28):

1. **GitHub private draft** — `VOOL-0.6.0-beta-macos-arm64.dmg`, a self-contained bundle
   (macOS 14+) built from the repository's initial public source commit `e1034c9b`, not
   from current `main`. Draft-only: not publicly downloadable, with draft `SHA256SUMS`
   and a `BUILD_MANIFEST.json` recording that source.
2. **vool.dev public download** — `https://vool.dev/downloads/VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg`,
   publicly served (HTTP 200, Cloudflare, last modified 2026-09-19) with a sidecar
   `.sha256` (`82d78398…b23e8f7`, which matched the served bytes when checked). Its
   in-bundle `BUILD_MANIFEST.json` self-attests source commit `cffc5c36…` built
   2026-09-19. That manifest is embedded by the build, not signed by a release authority:
   it is provenance as claimed by the artifact, independently verified only down to the
   published checksum. The two channel artifacts differ in name, size and digest — they
   are not the same file.

This repository's release records cover the GitHub channel; the website channel is
operated separately. Source remains the supported install route documented here.

| | Status |
| --- | --- |
| Public installers | macOS DMG publicly downloadable from vool.dev (see above); no published GitHub release, no Windows/Linux artifacts on any verified channel |
| Owner-review draft | macOS arm64 DMG only (private draft release, not published) |
| Published checksums | vool.dev sidecar `.sha256` (verified against served bytes) and draft-release `SHA256SUMS` |
| Code signing | Ad-hoc, both macOS artifacts (verified on the vool.dev artifact: `codesign -dv` reports `Signature=adhoc`, no TeamIdentifier); not Developer ID |
| macOS notarization | Not notarized (the vool.dev artifact is rejected by `spctl -a`; its own page tells users to bypass Gatekeeper) |
| Signed update manifest | No configured update feed in either artifact (empty `manifest_url`, empty trusted-publisher keys ⇒ the signed updater fails closed); automatic updates are not operational |
| API stability | Changing between builds |

Windows and Linux installer artifacts do not exist on any verified channel (the website
marks them "coming soon"). Source installs on those platforms are covered by CI (Windows
gauntlet, Linux test shards), which is source compatibility, not a shipped desktop release.

## What that means for you

* If you run a macOS artifact, Gatekeeper will refuse it on first launch — ad-hoc signing
  without notarization is expected at this stage, not a sign of a corrupted download.
  Verify the checksum published beside the download before bypassing the warning.
* Configuration formats can change between builds.
* Do not expect automatic updates: no update feed is configured for any artifact, and the
  signed-manifest updater correctly reports updates unavailable rather than fetching
  anything.

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
