---
description: How to install the VOOL AI assistant from source on macOS, Windows or Linux, including system requirements and how to verify a future packaged download.
---

# Install

VOOL has **no public installer release** yet. The supported install route is from source
with the bootstrap script; packaged downloads will appear on the
[releases page](https://github.com/Parad0x-Labs/vool/releases) when published. This page
keeps the verification steps you will need when a packaged build is published — see
[Release status](../trust/release-status.md) for the authoritative state of artifacts,
signing and updates.

## Requirements

| | Minimum | Comfortable |
| --- | ---: | ---: |
| Memory | 8 GB | 16 GB or more |
| Disk | 2 GB for the app | 20 GB with local models |
| macOS | 14 Sonoma, Apple silicon | 14 Sonoma or newer |
| Windows | 10 (64-bit) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 or newer |

Local models need considerably more memory than the app itself. Sizing guidance is in
[Run a local model](../guides/local-models.md).

## Install from source

Run the bootstrap script for your platform:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.sh
bash bootstrap_vool.sh
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The installer prepares the Python environment, checks your hardware, sets up the local
model, and launches the local services. Allow time and disk space for the initial model
download. Local model inference works offline after setup; web tools and cloud models need
a connection.

## Packaged builds (not yet published)

There is nothing to download yet. When a packaged build is published:

* Verify its checksum against the checksums published beside it on the release — a
  mismatch means a corrupted or tampered download: delete the file and download it again.
* Beta builds are ad-hoc signed and not notarized. macOS Gatekeeper will refuse a first
  launch: **System Settings → Privacy & Security → Open Anyway** after verifying the
  checksum. Windows SmartScreen behaves the same way (**More info → Run anyway**).
* Do not expect automatic updates: an update feed is not configured for beta artifacts.

## Verify a download

When a packaged artifact is published, verify it before running:

```bash
shasum -a 256 <downloaded-artifact>
```

Compare the result against the checksum published with that release. If they differ,
delete the file and download it again.

## Next

Continue to [First run](first-run.md).
