---
description: How to download and install the VOOL AI assistant on macOS, Windows or Linux, including system requirements.
---

# Install

## Requirements

| | Minimum | Comfortable |
| --- | --- | ---: |
| Memory | 8 GB | 16 GB or more |
| Disk | 2 GB for the app | 20 GB with local models |
| macOS | 14 Sonoma, Apple silicon | 14 Sonoma or newer |
| Windows | 10 (64-bit) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 or newer |

Local models need considerably more memory than the app itself. Sizing guidance is in
[Run a local model](../guides/local-models.md).

## macOS

1. Download `VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg` from [vool.dev](https://vool.dev/#cta). It runs on Apple silicon
   (M1 or newer) with macOS 14 Sonoma or later.
2. Open it and drag **VOOL** into `Applications`.
3. On first launch macOS will refuse to open it, because the build is not notarized.
   Open **System Settings → Privacy & Security**, find the VOOL entry, and choose
   **Open Anyway**.

{% hint style="warning" %}
Beta builds are unsigned and not notarized. The Gatekeeper warning is expected
for this stage, not a sign of a corrupted download. Verify the checksum published beside
the installer before you bypass the warning.
{% endhint %}

## Windows

1. Download the `.exe` installer.
2. SmartScreen will show **Windows protected your PC**, because the build is unsigned.
   Choose **More info → Run anyway** after checking the checksum.
3. Follow the installer.

## Linux

```bash
tar -xzf vool-linux-x86_64.tar.gz
cd vool
./vool
```

## Verify the download

```bash
shasum -a 256 VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg
```

Compare the result against the checksum published beside the installer,
[VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256](https://vool.dev/downloads/VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256). If they differ, delete the file and download it again.

## Next

Continue to [First run](first-run.md).
