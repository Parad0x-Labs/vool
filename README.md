# VOOL

<p align="center">
  <img src="docs/assets/vool-flag-banner.png" alt="VOOL flag — your machine, your agent" width="100%" />
</p>

<p align="center"><strong>Your machine. Your models. An agent that gets things done.</strong></p>

<p align="center">
  <a href="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml"><img src="https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-eee8d5" alt="MIT license" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/version-0.7.0_beta-eee8d5" alt="Version 0.7.0 beta" /></a>
</p>

**VOOL is a local-first personal agent from Parad0x Labs.** It connects conversation to action: read a project, edit files, run tests, research the web, and carry useful context into the next session.

Start with a local model and no VOOL account or cloud API key. Connect your own cloud provider when you want more model choice. Your workspace, memory, and action history stay under your control.

**[Get started](#install) · [Documentation](docs/README.md) · [Explore the code](docs/REPO_MAP.md) · [Community](https://discord.gg/V9NkjP3Fzz)**

## Why VOOL

### A conversation that can do the work

VOOL can read, plan, call tools, inspect results, and continue. Give it a repository to investigate, a file to produce, or a research question to work through. The runtime connects the model to tools for code, files, commands, documents, and the web.

### Memory beyond one chat

Keep useful facts and decisions across sessions. Local memory, context management, and retrieval help the agent pick up where you left off. You can inspect and manage what it remembers. [How memory works →](docs/concepts/memory.md)

### Your choice of models

Run local inference through Ollama or supported local runtimes, or connect a cloud model with your own credentials. Installation profiles adapt the local setup to your hardware. Cloud use is optional; provider charges apply when you enable it. [Local and cloud →](docs/concepts/local-and-cloud.md)

### Results you can inspect

Tool activity records what was requested, what executed, and what came back. Signed honesty receipts add tamper-evident records you can verify offline. You can inspect the work behind an answer—not just read its summary. [Receipts →](docs/concepts/receipts.md)

### Useful access, with boundaries

Workspace scopes and operating modes control what the agent can do. Choose how everyday actions are approved; sensitive operations retain their permission checks. Extend the runtime with plugins and MCP connections, with explicit control over the environment passed to external tools. [Tools and permissions →](docs/concepts/tools.md)

## Install

**Current version: 0.7.0-beta.** Download the app for your computer from the [v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta), or install from source with the bootstrap script below.

### Downloads

| Your computer | Download |
| --- | --- |
| **Mac with Apple Silicon** (M1 and later) | [`VOOL-0.7.0-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-arm64.dmg) |
| **Mac with an Intel chip** | [`VOOL-0.7.0-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-x86_64.dmg) |
| **Linux x86_64** (Ubuntu 22.04 / 24.04) | [`vool-0.7.0-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/vool-0.7.0-beta-linux-x86_64.tar.gz) |

Each download has a SHA-256 checksum on the release page. Check it before you open the file ([how](docs/getting-started/install.md#verify-a-download)). Not sure which Mac you have? Open the Apple menu and choose **About This Mac**: "Apple M…" means Apple Silicon, "Intel" means Intel. The macOS app needs macOS 14 Sonoma or newer.

**First open on a Mac.** This beta is not notarized by Apple yet, so macOS asks you to confirm the first time:

1. Open the DMG and drag **VOOL** into Applications.
2. Double-click VOOL. macOS says it can't check the app; click **Done** (or **Cancel**).
3. Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to VOOL. Confirm with your password. (On macOS 14 you can instead Control-click VOOL and choose **Open**.) You only do this once.

**Linux.** Unpack the tarball and run its installer; no sudo is needed. Full steps are in the [install guide](docs/INSTALL.md#linux-x86_64-tarball).

**Using a local model?** Before a local model can write answers, open **Settings → Models & Providers → Local models** and press **Certify** for it once. This runs a short check on your machine; a clearer prompt for it comes in 0.7.1.

Nothing goes online on a fresh install until you turn it on. Release notes: [CHANGELOG](CHANGELOG.md). Release state, signing and updates: [release status](docs/trust/release-status.md).

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
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.0-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.0-beta
```

### From source: Windows PowerShell

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

The installer prepares the Python environment, checks your hardware, sets up the local model, and launches the local services. Allow time and disk space for the initial model download. Local model inference works offline after setup; web tools and cloud models need a connection. Legacy integration cleanup is tracked in the [repository scope map](docs/REPOSITORY_SCOPE.md#retired-openclaw-integration).

<details>
<summary><strong>Choose a local profile or run from a checkout</strong></summary>

The default profile is selected from your hardware. To choose explicitly:

```bash
bash bootstrap_vool.sh --ref v0.7.0-beta --install-profile local-only
# Or use the larger local model profile:
bash bootstrap_vool.sh --ref v0.7.0-beta --install-profile local-max
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

## Explore further

Meet the [related ecosystem projects](docs/SYSTEM_SPINE.md#related-ecosystem-projects), including Dark Null Protocol for privacy-preserving settlement.

VOOL also includes optional wallet/payment integrations and bounded local helper orchestration. Peer mesh, DHT and Brain Hive are retained experimental research, outside normal production networking. See the [repository scope map](docs/REPOSITORY_SCOPE.md) for current runtime, integration, research and compatibility boundaries. Wallet and spending features require their own setup and authorization.

| Explore | Start here |
| --- | --- |
| First session and model setup | [First run](docs/getting-started/first-run.md) · [Connect a model](docs/getting-started/connect-a-model.md) |
| Workspace, memory, and control | [Workspaces](docs/concepts/workspaces.md) · [Memory](docs/concepts/memory.md) · [Spending limits](docs/guides/spending-limits.md) |
| Development and contribution | [Repository map](docs/REPO_MAP.md) · [Contributing](CONTRIBUTING.md) |
| Reviewing VOOL | [Reviewer guide](REVIEW.md) · [Evidence registry](docs/review/EVIDENCE.md) |
| Build status and troubleshooting | [CI](https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml) · [Engineering status](docs/STATUS.md) · [Error reference](docs/ERROR_BOOK.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Security and licensing | [Security policy](SECURITY.md) · [MIT license](LICENSE) · [Third-party notices](third_party/NOTICES/README.md) |

Built by **[Parad0x Labs](https://github.com/Parad0x-Labs)**. Contributor: **sls_0x**.
