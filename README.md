# VOOL

<p align="center">
  <img src="docs/assets/vool-flag-banner.png" alt="VOOL flag — your machine, your agent" width="100%" />
</p>

<p align="center"><strong>The open-source agent that remembers more, spends less, and runs on your machine.</strong></p>

<p align="center">
  <a href="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml"><img src="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml/badge.svg" alt="CI" /></a> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-eee8d5" alt="MIT license" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-0.7.8_beta-eee8d5" alt="Version 0.7.7 beta" /></a> <a href="https://github.com/Parad0x-Labs/vool/stargazers"><img src="https://img.shields.io/github/stars/Parad0x-Labs/vool?label=stars&color=eee8d5" alt="GitHub stars" /></a><br />
  <a href="https://github.com/Parad0x-Labs/vool/releases"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fparad0x-labs.github.io%2Fvool%2Fbadges%2Fapps.json" alt="App installer downloads across all versions" /></a> <a href="https://github.com/Parad0x-Labs/vool/releases"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fparad0x-labs.github.io%2Fvool%2Fbadges%2Fmacos-arm64.json" alt="macOS Apple silicon app downloads across all versions" /></a> <a href="https://github.com/Parad0x-Labs/vool/releases"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fparad0x-labs.github.io%2Fvool%2Fbadges%2Fmacos-intel.json" alt="macOS Intel app downloads across all versions" /></a> <a href="https://github.com/Parad0x-Labs/vool/releases"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fparad0x-labs.github.io%2Fvool%2Fbadges%2Flinux.json" alt="Linux app downloads across all versions" /></a>
</p>

**VOOL is a local-first personal agent from Parad0x Labs.** It reads your projects, edits files, runs tests, researches the web, and remembers what matters for next time. Free, MIT-licensed, no account. Start on a local model with no cloud key, or bring your own provider.

## Beats OpenClaw and Hermes on memory

Same 150 never-used [LoCoMo](https://github.com/snap-research/locomo) questions. Same model answering for all three agents. Same GPT-4o grader.

| | **VOOL** | OpenClaw | Hermes |
| --- | --- | --- | --- |
| **Right answers** | **82.7%** (124 / 150) | 71.3% (107) | 62.7% (94) |
| Tokens per question | **9.6k** | 51.8k | 33.5k |
| Cost per question | **$0.0033** | $0.0100 | $0.0046 |
| Median answer time | **11.2 s** | 15.6 s | 16.2 s |

- **+11 points over OpenClaw, +20 over Hermes,** and the fewest confidently wrong answers (18 vs 31 and 35). Not luck: p = 0.005 and p < 0.001 on matched questions.
- **5.4x fewer tokens than OpenClaw, 3.5x fewer than Hermes.** A third of OpenClaw's cost per answer.
- **In the app today.** The memory shipped in VOOL 0.7 scored 123 / 150 on the same questions, level with the build above.
- **Every run is public,** including the round where VOOL's lead was too small to count. [**See the proof**](docs/benchmarks/v0.7-memory-locomo.md)

## Why VOOL

- **It does the work.** Reads, plans, calls tools, checks results, and keeps going: code, files, commands, documents and the web. [Tools and permissions](docs/concepts/tools.md)
- **It remembers.** Facts and decisions carry across sessions, stored as readable files you can inspect and forget on demand. [How memory works](docs/concepts/memory.md)
- **Your models.** Local models through Ollama and supported runtimes, offline after setup, or a cloud model with your own key. [Local and cloud](docs/concepts/local-and-cloud.md)
- **Proof, not promises.** Every tool call is recorded with what was asked, what ran and what came back; signed receipts verify offline. [Receipts](docs/concepts/receipts.md)
- **You stay in charge.** Workspace scopes, approval modes and spending limits decide what the agent may do. [Spending limits](docs/guides/spending-limits.md)

**[Get started](#install) · [Documentation](docs/README.md) · [Benchmarks](docs/benchmarks/README.md) · [Explore the code](docs/REPO_MAP.md) · [Community](https://discord.gg/V9NkjP3Fzz)**

## Install

**Current version: 0.7.8-beta.** Download the app for your computer from the [v0.7.8-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.8-beta), or install from source with the bootstrap script below.

### Downloads

| Your computer | Download |
| --- | --- |
| **Mac with Apple Silicon** (M1 and later) | [`VOOL-0.7.8-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.8-beta/VOOL-0.7.8-beta-macos-arm64.dmg) |
| **Mac with an Intel chip** | [`VOOL-0.7.8-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.8-beta/VOOL-0.7.8-beta-macos-x86_64.dmg) |
| **Linux x86_64** (Ubuntu 22.04 / 24.04) | [`vool-0.7.8-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.8-beta/vool-0.7.8-beta-linux-x86_64.tar.gz) |

Each download has a SHA-256 checksum on the release page. Check it before you open the file ([how](docs/getting-started/install.md#verify-a-download)). Not sure which Mac you have? Open the Apple menu and choose **About This Mac**: "Apple M…" means Apple Silicon, "Intel" means Intel. The macOS app needs macOS 14 Sonoma or newer.

**First open on a Mac.** This beta is not notarized by Apple yet, so macOS asks you to confirm the first time:

1. Open the DMG and drag **VOOL** into Applications.
2. Double-click VOOL. macOS says it can't check the app; click **Done** (or **Cancel**).
3. Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to VOOL. Confirm with your password. (On macOS 14 you can instead Control-click VOOL and choose **Open**.) You only do this once.

**Linux.** Unpack the tarball and run its installer; no sudo is needed. Full steps are in the [install guide](docs/INSTALL.md#linux-x86_64-tarball).

**Using a local model?** Before a local model can write answers, open **Settings → Models & Providers → Local models** and press **Certify** for it once. This runs a short check on your machine.

From 0.7.5 on, VOOL updates itself: with "Go online on its own" on, it shows a notice and installs a new version with one press. Updating from 0.7.4 or 0.7.3 on a Mac: turn on "Go online on its own" (Settings > Privacy & Permissions) and press Check for updates once. From 0.7.4 or older on Linux, or 0.7.2 and older on a Mac: download 0.7.8 and install it over your current version. Your chats and settings live in your profile, outside the app. Release notes: [CHANGELOG](CHANGELOG.md). Release state, signing and updates: [release status](docs/trust/release-status.md).

| Platform | Getting started |
| --- | --- |
| **macOS / Apple Silicon** | App download (`macos-arm64` DMG) or the source installer; macOS CI coverage. |
| **macOS / Intel** | Separate app download (`macos-x86_64` DMG) or the source installer. Local models run on the CPU and may be too slow to certify or answer in time, so use a cloud model there for now; see [Intel Macs](docs/getting-started/install.md#intel-macs). |
| **Linux** | Self-contained x86_64 tarball, tested on Ubuntu 22.04 and 24.04, or the source installer; Linux test shards cover the source installer. |
| **Windows** | Experimental desktop path from source; PowerShell installer and Windows gauntlet coverage. No Windows download in this release. Use WSL2/Linux for jobs requiring a Linux kernel sandbox. |

For model choices, system requirements, checksums, and troubleshooting, see the [install guide](docs/INSTALL.md) and [Windows capability matrix](docs/WINDOWS_CAPABILITY_MATRIX.md).

### From source: macOS / Linux

Download the bootstrap script, then run it:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.8-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.8-beta
```

### From source: Windows PowerShell

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The installer prepares the Python environment, checks your hardware, sets up the local model, and launches the local services. Installing from source needs Python 3.11 or newer. If none is found, the installer tries to set one up. The app downloads include their own Python. Allow time and disk space for the initial model download. Local model inference works offline after setup; web tools and cloud models need a connection. Legacy integration cleanup is tracked in the [repository scope map](docs/REPOSITORY_SCOPE.md#retired-openclaw-integration).

<details>
<summary><strong>Choose a local profile or run from a checkout</strong></summary>

The default profile is selected from your hardware. To choose explicitly:

```bash
bash bootstrap_vool.sh --ref v0.7.8-beta --install-profile local-only
# Or use the larger local model profile:
bash bootstrap_vool.sh --ref v0.7.8-beta --install-profile local-max
```

Windows accepts `-InstallProfile local-only` or `-InstallProfile local-max`.
Use `--no-start` with the shell bootstrap to install without launching.

From an existing checkout, run `bash Install_And_Run_VOOL.sh` on macOS/Linux or double-click `Install_And_Run_VOOL.bat` on Windows.

To change the profile later, run this from your installation directory with its Python environment active, then restart VOOL:

```bash
python -m apps.vool_cli install-profile --set local-max
```

For a manual Windows API launch, activate the installed virtual environment and run:

```powershell
py -m apps.vool_api_server
```

Quote any path with spaces. Manual setup and launch options are in the [install guide](docs/INSTALL.md).

</details>

## Give it something real to do

Once your model and workspace are configured, try:

- **Code:** “Find why this test fails, fix the cause, and run the affected tests.”
- **Research:** “Compare these options using current sources and save the findings.”
- **Documents:** “Read these PDFs and summarize the differences with references.”
- **Memory:** “Remember this project decision so we can pick it up next session.”

These are starting prompts, not fixed demos. Available tools depend on your setup and permissions; task quality depends on the model you choose. Web research requires web access to be enabled.

Inspect the local service at [`/healthz`](http://127.0.0.1:11435/healthz), its activity at [`/trace`](http://127.0.0.1:11435/trace), and configured features at [`/api/runtime/capabilities`](http://127.0.0.1:11435/api/runtime/capabilities).

## Check the receipts

From your installed environment, run the offline receipt demonstration:

```bash
python -m core.honesty_receipt demo
```

It creates and verifies sample receipts, then demonstrates tamper detection. To inspect the most recent real session ledger:

```bash
python -m core.honesty_receipt verify-last
```

A signature verifies the signed record's integrity; it is not a guarantee that every model answer is correct.

## Memory benchmarks

The full write-up has every round, the setup, the spend and the weak spots: VOOL is slowest to ingest a conversation (it embeds every turn on your machine), and multi-hop questions that join facts from several sessions are hard for every agent tested. [Memory benchmarks](docs/benchmarks/README.md).

<img src="docs/benchmarks/charts/head-to-head-rounds.svg" alt="LoCoMo questions answered right, VOOL vs OpenClaw vs Hermes: official 150, 124 vs 107 vs 94; round 3 of 130, 102 vs 93 vs 95; pilot of 200, 163 vs 142 vs 139" width="100%" />

## Explore further

Meet the [related ecosystem projects](docs/SYSTEM_SPINE.md#related-ecosystem-projects), including Dark Null Protocol for privacy-preserving settlement.

VOOL also includes optional wallet/payment integrations and bounded local helper orchestration. Peer mesh, DHT and Brain Hive are retained experimental research, outside normal production networking. See the [repository scope map](docs/REPOSITORY_SCOPE.md) for current runtime, integration, research and compatibility boundaries. Wallet and spending features require their own setup and authorization.

| Explore | Start here |
| --- | --- |
| First session and model setup | [First run](docs/getting-started/first-run.md) · [Connect a model](docs/getting-started/connect-a-model.md) |
| Workspace, memory, and control | [Workspaces](docs/concepts/workspaces.md) · [Memory](docs/concepts/memory.md) · [Spending limits](docs/guides/spending-limits.md) · [Memory benchmarks](docs/benchmarks/README.md) |
| Development and contribution | [Repository map](docs/REPO_MAP.md) · [Contributing](CONTRIBUTING.md) |
| Reviewing VOOL | [Reviewer guide](REVIEW.md) · [Evidence registry](docs/review/EVIDENCE.md) |
| Build status and troubleshooting | [CI](https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml) · [Engineering status](docs/STATUS.md) · [Error reference](docs/ERROR_BOOK.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Security and licensing | [Security policy](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](third_party/NOTICES/README.md) |

Built by **[Parad0x Labs](https://github.com/Parad0x-Labs)**. Contributor: **sls_0x**.
