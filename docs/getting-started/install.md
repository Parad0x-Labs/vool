---
description: How to install the VOOL AI assistant on macOS (Apple Silicon or Intel) or Linux from the release downloads, or from source on macOS, Windows or Linux, including system requirements and how to verify a download.
---

# Install

VOOL 0.7.0-beta has app downloads for Apple Silicon Macs, Intel Macs and Linux x86_64 on
the [v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta).
You can also install from source with the bootstrap script on macOS, Linux or Windows.
See [Release status](../trust/release-status.md) for signing and update details.

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

## Download the app

| Your computer | Download |
| --- | --- |
| Mac with Apple Silicon (M1 and later) | [`VOOL-0.7.0-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-arm64.dmg) |
| Mac with an Intel chip | [`VOOL-0.7.0-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.0-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/vool-0.7.0-beta-linux-x86_64.tar.gz) |

To find your Mac's chip, open the Apple menu and choose **About This Mac**. "Apple M…"
means Apple Silicon; "Intel" means Intel. The Apple Silicon app does not run on an Intel
Mac, and says so when you open it.

Check the download's SHA-256 before you open it (see [Verify a download](#verify-a-download)).

### First open on a Mac

The beta app is not notarized by Apple yet, so macOS asks you to confirm the first time
you open it. This is expected and happens once.

1. Open the DMG and drag **VOOL** into **Applications**.
2. Double-click VOOL. macOS says it cannot check the app for malicious software. Click
   **Done** (or **Cancel**).
3. Open **System Settings → Privacy & Security**. Scroll down to the message about VOOL
   and click **Open Anyway**. Enter your password to confirm.

On macOS 14 Sonoma you can instead Control-click VOOL in Applications, choose **Open**,
then click **Open** again.

### Linux

Download the tarball and its `.sha256` file, check it, then run the bundled installer.
No sudo or system Python is needed. The full steps, the install locations and how to
uninstall are in the [install guide](../INSTALL.md#linux-x86_64-tarball).

### After install

Before a local model can answer, open **Settings → Models & Providers → Local models** and press **Certify** for it once. It runs a short check on your machine; a clearer prompt for it comes in 0.7.1.

Nothing goes online on a fresh install until you turn it on. Update checks stay off until
you allow them. Updates are signed and checked before they install, and you can roll one
back; the first update offered this way will be 0.7.1. Crypto features are off by default.

## Install from source

Run the bootstrap script for your platform:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.0-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.0-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The macOS / Linux commands pin both the bootstrap script and its source download to `v0.7.0-beta`. To choose another release, change both the tag in the script URL and `--ref`. The bootstrap accepts a branch name or a release tag, not a commit hash.

The installer prepares the Python environment, checks your hardware, sets up the local
model, and launches the local services. Allow time and disk space for the initial model
download. Local model inference works offline after setup; web tools and cloud models need
a connection.

## Intel Macs

VOOL runs on Intel Macs with macOS 14 or newer (for example a 2020 13-inch MacBook Pro),
from the Intel download or from source. The source installer detects the Intel CPU and
installs the Intel builds of every dependency. Two limits are specific to Intel:

* **No local model training.** PyTorch has published no Intel macOS build since 2.3, so
  the installer skips it and LoRA training reports itself unavailable. Chat, memory,
  tools and cloud models are unaffected.
* **Local models run on the CPU only.** Local model runners have no GPU acceleration for Intel graphics,
  so local answers are several times slower than on Apple silicon. On an 8 GB Mac, use a
  cloud model for chat; on 16 GB, the installer's default small local model works but is slow.

On Intel Macs, local models run on the CPU and may be too slow to pass the one-time Certify check or to answer in time, so use a cloud model there for now. We tested the Intel build under Rosetta on an Apple Silicon Mac, not yet on an Intel Mac.

Intel Macs have their own download, `VOOL-0.7.0-beta-macos-x86_64.dmg`. The Apple Silicon
download does not run on Intel and says so at launch.

## Verify a download

Each download on the release has a SHA-256 checksum next to it. Verify the file before
opening it:

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
