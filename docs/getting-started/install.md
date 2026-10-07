---
description: How to install the VOOL AI assistant from source on macOS, Windows or Linux, including system requirements and how to verify a future packaged download.
---

# Install

The release supported by this repository is **from source** with the bootstrap script;
nothing is published on the [releases page](https://github.com/Parad0x-Labs/vool/releases)
beyond a private owner-review draft. A macOS disk image is separately downloadable from
the project website, `vool.dev` — that channel is not built or verified from this
repository's release records, so if you use it, verify its sidecar checksum first (see
[Verify a download](#verify-a-download)) and treat its provenance as unverified beyond
that checksum. See [Release status](../trust/release-status.md) for the authoritative
state of artifacts, signing and updates on every channel.

## Requirements

| | Minimum | Comfortable |
| --- | ---: | ---: |
| Memory | 8 GB | 16 GB or more |
| Disk | 2 GB for the app | 20 GB with local models |
| macOS | 14 Sonoma, Apple silicon or Intel ([limits](#intel-macs)) | 14 Sonoma or newer, Apple silicon |
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

To install a specific release instead of the latest `main`, use the release tag in the script URL and pass it with `--ref v0.7.0-beta` (PowerShell: `-Ref v0.7.0-beta`). The bootstrap accepts a branch name or a release tag, not a commit hash.

The installer prepares the Python environment, checks your hardware, sets up the local
model, and launches the local services. Allow time and disk space for the initial model
download. Local model inference works offline after setup; web tools and cloud models need
a connection.

## Intel Macs

VOOL installs from source on Intel Macs running macOS 14 or newer (for example a 2020
13-inch MacBook Pro). The installer detects the Intel CPU and installs the Intel builds of
every dependency. Three limits are specific to Intel:

* **No local model training.** PyTorch has published no Intel macOS build since 2.3, so
  the installer skips it and LoRA training reports itself unavailable. Chat, memory,
  tools and cloud models are unaffected.
* **Local models run on the CPU only.** Ollama has no GPU acceleration for Intel graphics,
  so local answers are several times slower than on Apple silicon. On an 8 GB Mac, use a
  cloud model for chat; on 16 GB, the installer's default small local model works but is slow.

A packaged Intel app needs its own `--arch x86_64` build; the Apple silicon download does
not run on Intel and says so at launch.

## Packaged builds

No GitHub release is published (only a private owner-review draft), and no Windows or
Linux artifact exists on any verified channel. If you download the macOS DMG from
`vool.dev`:

* Verify its checksum against the `.sha256` file published beside it — a mismatch means
  a corrupted or tampered download: delete the file and download it again.
* The beta build is ad-hoc signed and not notarized. macOS Gatekeeper will refuse a first
  launch: **System Settings → Privacy & Security → Open Anyway** after verifying the
  checksum.
* Do not expect automatic updates: no update feed is configured for beta artifacts.

## Verify a download

When a packaged artifact is published, verify it before running:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Compare the result against the checksum published with that release. If they differ,
delete the file and download it again.

## Next

Continue to [First run](first-run.md).
