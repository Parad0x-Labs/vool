<p align="center">
  <img src="docs/assets/vool-flag-banner.png" alt="VOOL flag — your machine, your agent" width="100%" />
</p>

<h1 align="center">VOOL</h1>

<p align="center"><img src="docs/assets/vool-tagline.svg" alt="Remembers more. Spends less. Runs on your machine." width="560" /></p>

<p align="center"><strong>The open-source agent that remembers more, spends less, and runs on your machine.</strong><br />Free. MIT-licensed. No account. Local model or your own key.</p>

<p align="center">
  <a href="https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta"><img src="https://img.shields.io/badge/release-0.7.7_beta-eee8d5?style=for-the-badge&labelColor=1a1a1a" alt="Release 0.7.7 beta" /></a>
  <a href="#install"><img src="https://img.shields.io/badge/macOS_·_Linux-download-eee8d5?style=for-the-badge&labelColor=1a1a1a" alt="Download for macOS and Linux" /></a>
  <a href="docs/benchmarks/README.md"><img src="https://img.shields.io/badge/LoCoMo_memory-82.7%25-eee8d5?style=for-the-badge&labelColor=1a1a1a" alt="LoCoMo memory score 82.7 percent" /></a><br />
  <a href="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml"><img src="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml/badge.svg" alt="CI" /></a> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-eee8d5" alt="MIT license" /></a> <a href="https://github.com/Parad0x-Labs/vool/stargazers"><img src="https://img.shields.io/github/stars/Parad0x-Labs/vool?label=stars&color=eee8d5" alt="GitHub stars" /></a> <a href="https://github.com/Parad0x-Labs/vool/releases"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fparad0x-labs.github.io%2Fvool%2Fbadges%2Fapps.json" alt="App installer downloads across all versions" /></a> <a href="https://discord.gg/V9NkjP3Fzz"><img src="https://img.shields.io/badge/community-Discord-eee8d5" alt="Discord community" /></a>
</p>

<p align="center"><a href="#install"><strong>Install</strong></a> · <a href="#why-vool"><strong>Why VOOL</strong></a> · <a href="#the-memory-numbers"><strong>Benchmarks</strong></a> · <a href="docs/README.md"><strong>Docs</strong></a> · <a href="CHANGELOG.md"><strong>Changelog</strong></a></p>

**VOOL is a local-first personal agent from Parad0x Labs.** It reads your projects, edits files, runs tests, researches the web, and remembers what matters for next time. Start on a local model with no cloud key, or bring your own provider.

> **New in 0.7.7:** updates that roll back by themselves if they fail, a wallet that asks for your PIN or Touch ID before anything loosens, a show/hide switch for the pet, and a forget that reaches backups and logs too. [All changes](CHANGELOG.md)

## Install

**Current version: 0.7.7-beta.** Download the app for your computer, open it, done. Source installs are [further down](#from-source).

| Your computer | Download |
| --- | --- |
| **Mac with Apple Silicon** (M1 and later) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| **Mac with an Intel chip** | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| **Linux x86_64** (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Each download has a SHA-256 checksum on the [release page](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta); check it before you open the file ([how](docs/getting-started/install.md#verify-a-download)). Not sure which Mac you have? Apple menu > **About This Mac**: "Apple M…" means Apple Silicon. The Mac app needs macOS 14 Sonoma or newer.

**First open on a Mac.** This beta is not notarized by Apple yet, so macOS asks once:

1. Open the DMG and drag **VOOL** into Applications.
2. Double-click VOOL. macOS says it can't check the app; click **Done** (or **Cancel**).
3. Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to VOOL. Confirm with your password. (On macOS 14 you can instead Control-click VOOL and choose **Open**.)

**Linux.** Unpack the tarball and run its installer; no sudo needed. Steps: [install guide](docs/INSTALL.md#linux-x86_64-tarball).

**Using a local model?** Open **Settings → Models & Providers → Local models** and press **Certify** once. It runs a short check on your machine.

<details>
<summary><strong>Updating from an older version</strong></summary>

From 0.7.5 on, VOOL updates itself: with "Go online on its own" on, it shows a notice and installs a new version with one press. If an update fails, the old version comes back.

- **From 0.7.4 or 0.7.3 on a Mac:** turn on "Go online on its own" (Settings > Privacy & Permissions) and press Check for updates once.
- **From 0.7.4 or older on Linux, or 0.7.2 and older on a Mac:** download 0.7.7 and install it over your current version.

Your chats and settings live in your profile, outside the app. Release state, signing and updates: [release status](docs/trust/release-status.md).

</details>

<details>
<summary><strong>Platform support</strong></summary>

| Platform | Getting started |
| --- | --- |
| **macOS / Apple Silicon** | App download (`macos-arm64` DMG) or the source installer; macOS CI coverage. |
| **macOS / Intel** | Separate app download (`macos-x86_64` DMG) or the source installer. Local models run on the CPU and may be too slow to certify or answer in time, so use a cloud model there for now; see [Intel Macs](docs/getting-started/install.md#intel-macs). |
| **Linux** | Self-contained x86_64 tarball, tested on Ubuntu 22.04 and 24.04, or the source installer; Linux test shards cover the source installer. |
| **Windows** | Experimental desktop path from source; PowerShell installer and Windows gauntlet coverage. No Windows download in this release. Use WSL2/Linux for jobs requiring a Linux kernel sandbox. |

Model choices, system requirements and troubleshooting: [install guide](docs/INSTALL.md) and [Windows capability matrix](docs/WINDOWS_CAPABILITY_MATRIX.md).

</details>

<details id="from-source">
<summary><strong>From source (macOS, Linux, Windows)</strong></summary>

macOS / Linux: download the bootstrap script, then run it.

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The installer prepares the Python environment, checks your hardware, sets up the local model and starts VOOL. It needs Python 3.11 or newer and tries to set one up if none is found (the app downloads bring their own). Allow time and disk space for the first model download. Local models work offline after setup; web tools and cloud models need a connection.

The default profile is picked from your hardware. To choose it yourself:

```bash
bash bootstrap_vool.sh --ref v0.7.7-beta --install-profile local-only
# Or use the larger local model profile:
bash bootstrap_vool.sh --ref v0.7.7-beta --install-profile local-max
```

Windows accepts `-InstallProfile local-only` or `-InstallProfile local-max`. Use `--no-start` with the shell bootstrap to install without launching. From a checkout, run `bash Install_And_Run_VOOL.sh` on macOS/Linux or double-click `Install_And_Run_VOOL.bat` on Windows.

To change the profile later, run this from your installation directory with its Python environment active, then restart VOOL:

```bash
python -m apps.vool_cli install-profile --set local-max
```

For a manual Windows API launch, activate the installed virtual environment and run `py -m apps.vool_api_server`. Quote any path with spaces. More options: [install guide](docs/INSTALL.md). Legacy integration cleanup is tracked in the [repository scope map](docs/REPOSITORY_SCOPE.md#retired-openclaw-integration).

</details>

## Why VOOL

<table>
  <tr>
    <td width="33%" valign="top"><strong>It does the work</strong><br />Reads, plans, calls tools, checks results and keeps going: code, files, commands, documents and the web.<br /><a href="docs/concepts/tools.md">Tools and permissions</a></td>
    <td width="33%" valign="top"><strong>It remembers</strong><br />Facts and decisions carry across sessions, kept as readable files you can inspect and forget on demand.<br /><a href="docs/concepts/memory.md">How memory works</a></td>
    <td width="33%" valign="top"><strong>Your models</strong><br />Local models through Ollama and supported runtimes, offline after setup, or a cloud model with your own key.<br /><a href="docs/concepts/local-and-cloud.md">Local and cloud</a></td>
  </tr>
  <tr>
    <td valign="top"><strong>Proof, not promises</strong><br />Every tool call records what was asked, what ran and what came back. Signed receipts verify offline.<br /><a href="docs/concepts/receipts.md">Receipts</a></td>
    <td valign="top"><strong>You stay in charge</strong><br />Workspace scopes, approval modes and spending limits decide what the agent may do. Money moves only with your OK.<br /><a href="docs/guides/spending-limits.md">Spending limits</a></td>
    <td valign="top"><strong>Skills</strong><br />Comes with its own skill library, and you can add more. A skill teaches a task; it never grants permissions. Plugins are planned for 0.8, not released yet.<br /><a href="https://github.com/Parad0x-Labs/vool-skills">VOOL skills</a></td>
  </tr>
</table>

## The memory numbers

Same 150 never-used [LoCoMo](https://github.com/snap-research/locomo) questions. Same model answering for all three agents. Same GPT-4o grader.

| | **VOOL** | OpenClaw | Hermes |
| --- | --- | --- | --- |
| **Right answers** | **82.7%** (124 / 150) | 71.3% (107) | 62.7% (94) |
| Confidently wrong | **18** | 31 | 35 |
| Tokens per question | **9.6k** | 51.8k | 33.5k |
| Cost per question | **$0.0033** | $0.0100 | $0.0046 |
| Median answer time | **11.2 s** | 15.6 s | 16.2 s |

<img src="docs/benchmarks/charts/head-to-head-rounds.svg" alt="LoCoMo questions answered right, VOOL vs OpenClaw vs Hermes: official 150, 124 vs 107 vs 94; round 3 of 130, 102 vs 93 vs 95; pilot of 200, 163 vs 142 vs 139" width="100%" />

- **+11 points over OpenClaw, +20 over Hermes.** Not luck: p = 0.005 and p < 0.001 on matched questions.
- **5.4x fewer tokens than OpenClaw, 3.5x fewer than Hermes.** A third of OpenClaw's cost per answer.
- **In the app today.** The memory shipped in VOOL 0.7 scored 123 / 150 on the same questions.
- **Every run is public,** including the round where VOOL's lead was too small to count, and the weak spots: VOOL is slowest to ingest a conversation (it embeds every turn on your machine), and multi-hop questions are hard for every agent tested. [**See the proof**](docs/benchmarks/v0.7-memory-locomo.md)

## Give it something real to do

- **Code:** “Find why this test fails, fix the cause, and run the affected tests.”
- **Research:** “Compare these options using current sources and save the findings.”
- **Documents:** “Read these PDFs and summarize the differences with references.”
- **Memory:** “Remember this project decision so we can pick it up next session.”

Starting prompts, not fixed demos: the tools you get depend on your setup and permissions, and answer quality depends on the model you choose. Web research needs web access turned on.

<details>
<summary><strong>Check the receipts yourself</strong></summary>

From your installed environment, run the offline receipt demonstration:

```bash
python -m core.honesty_receipt demo
```

It creates and verifies sample receipts, then shows tamper detection. To inspect the most recent real session ledger:

```bash
python -m core.honesty_receipt verify-last
```

A signature proves the signed record was not changed; it does not prove every model answer is correct. The local service also shows its state at [`/healthz`](http://127.0.0.1:11435/healthz), its activity at [`/trace`](http://127.0.0.1:11435/trace) and its features at [`/api/runtime/capabilities`](http://127.0.0.1:11435/api/runtime/capabilities).

</details>

## Explore

| Topic | Start here |
| --- | --- |
| **Get going** | [First run](docs/getting-started/first-run.md) · [Connect a model](docs/getting-started/connect-a-model.md) · [Workspaces](docs/concepts/workspaces.md) |
| **Control** | [Memory](docs/concepts/memory.md) · [Spending limits](docs/guides/spending-limits.md) · [Wallet](docs/guides/wallet.md) · [Benchmarks](docs/benchmarks/README.md) |
| **Build on it** | [Repository map](docs/REPO_MAP.md) · [Contributing](CONTRIBUTING.md) · [Repository scope](docs/REPOSITORY_SCOPE.md) |
| **Review it** | [Reviewer guide](REVIEW.md) · [Evidence registry](docs/review/EVIDENCE.md) · [CI](https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml) · [Engineering status](docs/STATUS.md) |
| **When stuck** | [Error reference](docs/ERROR_BOOK.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Discord](https://discord.gg/V9NkjP3Fzz) |
| **Trust** | [Security policy](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](third_party/NOTICES/README.md) |

Wallet and payment features are optional, off until you set them up, and every payment needs your approval.

---

<p align="center">Built by <a href="https://github.com/Parad0x-Labs"><strong>Parad0x Labs</strong></a> · Contributors: <a href="https://github.com/Parad0x-Labs"><strong>sls_0x</strong></a> · <a href="https://github.com/KAS-049"><strong>KAS</strong></a> · <a href="https://github.com/ermitdab"><strong>ermitdab</strong></a> · <strong>SteveON</strong> · <strong>P3dr0</strong></p>
