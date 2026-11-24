---
description: How to install the VOOL AI assistant on macOS (Apple Silicon or Intel) or Linux from the release downloads, or from source, including system requirements and how to verify a download.
---

# Install

VOOL v0.8 has app downloads for Apple Silicon Macs, Intel Macs and Linux x86_64. The
installers are on the [GitHub releases page](https://github.com/Parad0x-Labs/vool/releases).
You can also install from source with the bootstrap script on macOS or Linux.
See [Release status](../trust/release-status.md) for signing and update details.

## Requirements

| | Minimum | Comfortable |
| --- | ---: | ---: |
| Memory | 8 GB | 16 GB or more |
| Disk | 2 GB for the app | 20 GB with local models |
| macOS | 14 Sonoma, Apple silicon or Intel ([limits](#intel-macs)) | 14 Sonoma or newer, Apple silicon |
| Linux | Ubuntu 22.04, x86_64 | Ubuntu 24.04 |

Local models need considerably more memory than the app itself. Sizing guidance is in
[Run a local model](../guides/local-models.md).

## Download the app

Open the newest v0.8 release on the [releases page](https://github.com/Parad0x-Labs/vool/releases)
and pick the file for your computer:

| Your computer | File name ends in |
| --- | --- |
| Mac with Apple Silicon (M1 and later) | `-macos-arm64.dmg` |
| Mac with an Intel chip | `-macos-x86_64.dmg` |
| Linux x86_64 (Ubuntu 22.04 / 24.04), installed with apt | `-linux-x86_64.deb` |
| Linux x86_64, installed without sudo | `-linux-x86_64.tar.gz` |

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

There are two Linux downloads of the same app. Pick one:

* **The `.deb`** if you install software with apt. It needs sudo once, and apt also
  installs the system packages VOOL's window uses.
* **The tarball (`.tar.gz`)** if you want to install without sudo. VOOL's window still needs
  three system packages, see below.

Replace `<version>` with the version in the file name.

With the `.deb`, download it and its `.sha256` file, check it, then install it with apt:

```bash
sha256sum -c vool-<version>-linux-x86_64.deb.sha256
sudo apt install ./vool-<version>-linux-x86_64.deb
vool
```

The first time each person on the computer opens VOOL, it sets up that person's own copy.
Removing the `.deb` later leaves each person's copy, chats and settings where they are.

With the tarball, download it and its `.sha256` file, check it, then run the bundled
installer. The install step needs no sudo and no system Python:

```bash
sha256sum -c vool-<version>-linux-x86_64.tar.gz.sha256
tar -xzf vool-<version>-linux-x86_64.tar.gz
bash vool-<version>-linux-x86_64/install.sh
~/.local/bin/vool
```

Either way, VOOL installs into `~/.local/share/vool`, puts the `vool` command in place and adds
an application menu entry. To remove a tarball install, run
`bash ~/.local/share/vool/current/uninstall.sh`. Your chats and settings stay in your profile.

On a desktop, VOOL opens in its own window. Closing the window stops VOOL. The window uses
your system's GTK and WebKitGTK packages. If they are missing, VOOL does not open and tells
you the line to run:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

Without a desktop (for example over SSH), run `vool --headless` and open
`http://127.0.0.1:11435/chat` in a browser.

The Linux app does not include Ollama, the program that runs local models. To use a local
model on Linux, install Ollama from [ollama.com](https://ollama.com) first. Cloud models work
without it.

With **Go online on its own** turned on, the Linux download updates inside VOOL. The new
version is installed next to the old one. If it does not start properly, VOOL switches back
by itself.

### After install

Before a local model can answer, VOOL checks it once on your computer. On Linux, install
Ollama first (see above). Then open
**Settings → Models & Providers**, choose **This Mac** (or **This computer**), and press
**Use this model** next to the model. See [Run a local model](../guides/local-models.md).

VOOL updates itself: with **Go online on its own** on, it shows a notice and installs a new
version with one press. Coming from an older version, see
[Updates and migration](../reference/updates-migration.md). Your chats and settings live in
your profile, outside the app. Crypto features are off by default.

## Install from source

Run the bootstrap script for your platform:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The macOS / Linux commands pin both the bootstrap script and its source download to `v0.7.7-beta`. To choose another release, change both the tag in the script URL and `--ref`. The bootstrap accepts a branch name or a release tag, not a commit hash.

Installing from source needs Python 3.11 or newer. If none is found, the installer
downloads one and sets it up. The app downloads include their own Python.

The installer prepares the Python environment, checks your hardware, sets up the local
model, and launches the local services. Allow time and disk space for the initial model
download. Local model answers work offline after setup; web lookups and cloud models need
a connection.

## Intel Macs

VOOL runs on Intel Macs with macOS 14 or newer (for example a 2020 13-inch MacBook Pro),
from the Intel download or from source. The source installer detects the Intel CPU and
installs the Intel builds of every dependency. Two limits are specific to Intel:

* **No local model training.** PyTorch has published no Intel macOS build since 2.3, so
  the installer skips it and training reports itself unavailable. Chat, memory,
  tools and cloud models are unaffected.
* **Local models run on the CPU only.** Local model runners have no GPU acceleration for
  Intel graphics, so local answers are slower than on Apple silicon. On an 8 GB Mac, use a
  cloud model for chat.

A long memory answer from a local model can time out on an Intel Mac, so use a cloud model
there for now. The local-model check allows for slower processors.

The Apple Silicon download does not run on Intel and says so at launch. If an Apple Silicon
Mac has the Intel version installed, Settings says so: local models run very slowly that way.

## Verify a download

Each download on the release has a SHA-256 checksum next to it. Verify the file before
opening it:

```bash
shasum -a 256 <downloaded-file>    # macOS
sha256sum <downloaded-file>         # Linux
```

Compare the result against the checksum published with that release. If they differ,
delete the file and download it again.

## Next

Continue to [First run](first-run.md).
