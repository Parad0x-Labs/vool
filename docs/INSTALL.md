# VOOL Install

This is the canonical install and quickstart doc.

`main` is the current alpha trunk. Use the default `main` installer path unless you are deliberately reproducing an older checkpoint.

## Fast Path

macOS / Linux:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.sh
bash bootstrap_vool.sh
```

If you need a reproducible historical install instead of the latest alpha trunk on `main`, pin an exact ref:

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.sh && bash "$tmp" --ref 2f17895ede500d85372269cb516083abd09c013c --install-profile ollama-max && rm -f "$tmp"
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1; powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Local Windows checkout with guided installer:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install_And_Run_VOOL.ps1
```

Build a checksumed Windows zip package from a checkout:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_windows_package.ps1 -PackageVersion local-test
```

To Authenticode-sign the PowerShell entrypoints during packaging, provide a real certificate thumbprint:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_windows_package.ps1 -SigningCertificateThumbprint "<thumbprint>"
```

Probe the machine and provider reality before or after install:

```bash
bash Probe_VOOL_Stack.sh
```

```powershell
.\Probe_VOOL_Stack.bat
```

Force a supported install profile instead of taking the auto recommendation:

```bash
bash bootstrap_vool.sh --install-profile local-only

bash bootstrap_vool.sh --install-profile local-max
```

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1 -InstallProfile local-max
```

Safe one-line profile shortcuts for macOS / Linux:

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.sh && bash "$tmp" --install-profile ollama-only && rm -f "$tmp"
```

```bash
tmp="$(mktemp)" && curl -fsSLo "$tmp" https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.sh && bash "$tmp" --install-profile ollama-max && rm -f "$tmp"
```

After install, inspect or switch profiles without editing env vars:

```bash
cd ~/vool-local && .venv/bin/python -m apps.vool_cli install-profile
cd ~/vool-local && .venv/bin/python -m apps.vool_cli install-profile --set ollama-only
cd ~/vool-local && .venv/bin/python -m apps.vool_cli install-profile --set ollama-max
```

Recommended profile guidance:

1. `local-only` / `ollama-only` for smaller machines or anyone who wants a strict no-remote default.
2. `local-max` / `ollama-max` for stronger local boxes, roughly 24 GiB+ unified memory or 20+ GiB VRAM / 48 GiB RAM class hardware, and the installer now pulls the local helper model too.

The probe reports:

1. machine hardware summary
2. installed Ollama models
3. whether the machine can reasonably run one local model or a primary/helper local pair
4. whether the stronger optional local verifier lane is supported on the current hardware
5. which install profile those local stacks map to in the shipped runtime

Manual local shortcut:

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool-local
bash Install_And_Run_VOOL.sh
```

## What The Installer Does

1. creates a Python environment and installs dependencies
2. probes hardware and selects an Ollama model tier
3. installs Ollama if it is missing
4. pulls the selected local model
5. installs the OpenClaw bridge and registration path
6. starts the VOOL API server on `127.0.0.1:11435`
7. installs the `Probe_VOOL_Stack` command into the install root so the machine can be re-checked later without guesswork
8. on macOS, hands off the final launch to `OpenClaw_VOOL.command` so the running services are owned by Terminal.app instead of the short-lived installer shell

If you want the shortest user path, this is it.

If you already have a verified archive digest, pass it to the bootstrap script with `--sha256` on macOS/Linux or `-ArchiveSha256` on Windows so the download is checked before extraction.

## First URLs

- VOOL API health: `http://127.0.0.1:11435/healthz`
- VOOL trace rail: `http://127.0.0.1:11435/trace`
- `.null` (web0) browser: `http://127.0.0.1:11435/web0`
- Public Hive / dashboard surface: `/hive` on the configured meet/watch server
- Public feed surface: `/feed` on the configured meet/watch server

## Manual Developer Setup

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool-local
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,runtime]"
```

Start the local API:

```bash
python -m apps.vool_api_server
```

Optional local surfaces:

```bash
python -m apps.vool_agent --interactive
python -m apps.meet_and_greet_server
python -m apps.brain_hive_watch_server
```

## MLX Inference Lane (Apple Silicon)

Optional high-throughput local inference via MLX. Requires Apple Silicon (M1 or later). Delivers 30+ t/s on a typical M-series chip.

Set the following env vars before starting the VOOL server, or add them to your shell profile / `~/.vool_local/config/provider-env.sh`:

```bash
# MLX inference lane (optional — Apple Silicon, 30+ t/s)
MLX_BASE_URL=http://127.0.0.1:8096/v1
VOOL_MLX_MODEL=mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit
VOOL_MLX_CONTEXT_WINDOW=32768
```

Start the MLX server separately (requires `mlx-lm`):

```bash
mlx_lm.server --model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit --port 8096
```

VOOL will detect `MLX_BASE_URL` at boot and route eligible requests through the MLX lane automatically.

## Optional Public Hive Write Auth

Public Hive reads can exist without write auth, but signed write hydration is a separate step.

If your runtime reports that Public Hive writes are not hydrated yet, run:

```bash
python -m ops.ensure_public_hive_auth --watch-host hive.parad0xlabs.com --remote-config-path /etc/vool-hive-mind/watch-config.json
```

What this does:

1. reads the watcher config over SSH with your existing VOOL key
2. hydrates the local runtime `agent-bootstrap.json` with the real auth token and seed URLs
3. keeps the write step explicit instead of making runtime boot reach out on its own

Related env vars if you want to wire this once and keep the command shorter:

- `VOOL_PUBLIC_HIVE_WATCH_HOST`
- `VOOL_PUBLIC_HIVE_REMOTE_CONFIG`
- `VOOL_PUBLIC_HIVE_SSH_KEY_PATH`

## OpenClaw

The installer registers VOOL as an OpenClaw agent automatically. After install, the expected local VOOL API port is `11435`.

The convenience launchers (on macOS every `*.sh` launcher also gets a double-clickable `*.command` twin, and the installer drops branded **OpenClaw + VOOL** and **Stop VOOL** app icons on the Desktop):

- Start VOOL + open OpenClaw — macOS / Linux: `OpenClaw_VOOL.sh` · Windows: `OpenClaw_VOOL.bat`
- Open the `.null` (web0) browser — macOS / Linux: `Open_Web0.sh` · Windows: `Open_Web0.bat`
- Stop every VOOL process + disable auto-restart — macOS / Linux: `Stop_VOOL.sh` · Windows: `Stop_VOOL.bat`
- Machine/provider probe — macOS / Linux: `Probe_VOOL_Stack.sh` · Windows: `Probe_VOOL_Stack.bat`

The launcher resolves the gateway token from the strongest available state source in this order:

1. `OPENCLAW_CONFIG_PATH`
2. `OPENCLAW_HOME`
3. `OPENCLAW_STATE_DIR`
4. the macOS launchd gateway state dir when that service is installed
5. local home fallbacks like `.openclaw` and `.openclaw-default`

## Stopping VOOL

VOOL installs a keep-alive supervisor (a launchd `KeepAlive` agent on macOS, a logon task on Windows), so closing a window does not stop it. To hard-stop every VOOL process — API, OpenClaw gateway, agent workers — and disable auto-restart:

- macOS / Linux: run `bash Stop_VOOL.sh`, double-click `Stop_VOOL.command`, or click the red **Stop VOOL** icon on the Desktop. Equivalent one-liner: `.venv/bin/python -m installer.vool_stop --project-root "$(pwd)"`.
- Windows: run `Stop_VOOL.bat` or double-click the red **Stop VOOL** shortcut.

On macOS the Stop control boots out the launchd keep-alive agent and frees ports `11435` (API) and `18789` (gateway); relaunching from the OpenClaw launcher or the Desktop icon starts it again. Ollama, the shared local model server on `11434`, is left running on purpose.

If you deliberately run OpenClaw from a custom home, set `OPENCLAW_STATE_DIR` or `OPENCLAW_HOME` before opening VOOL so the launcher does not guess the wrong gateway token.
If you deliberately run VOOL from a custom runtime home, set `VOOL_HOME` before opening the launcher so the OpenClaw bridge points at the runtime you actually want to test.

## Web Access (opt-in)

VOOL is local-first. Live web lookup (`web.search`, `web.fetch`, `web.research`, browser render) is opt-in and OFF by default, so a fresh runtime never reaches out to the network for answers unless you deliberately turn it on.

**Local-only profile:** the local-only profile (`VOOL_INSTALL_PROFILE=local-only`) is a hard "nothing leaves the box" guarantee. It keeps live web lookup OFF and that guarantee **overrides** the opt-in — `VOOL_ENABLE_WEB=1` will not turn web on while local-only is active. To use live web lookup, run a **non-local-only profile** (and/or set `VOOL_ENABLE_WEB=1` when not local-only).

On a non-local-only profile, enable web for a session with either environment variable:

```bash
VOOL_ENABLE_WEB=1 .venv/bin/python -m apps.vool_cli web "latest qwen release notes"
# VOOL_ALLOW_WEB=1 is accepted as an alias
```

To make it persistent, export the flag in your shell profile or set `system.allow_web_fallback: true` in `config/default_policy.yaml`.

Drive web directly from the CLI or chat once it is enabled:

```bash
.venv/bin/python -m apps.vool_cli web "telegram bot api docs"     # search
.venv/bin/python -m apps.vool_cli web --fetch https://example.com  # fetch one URL
.venv/bin/python -m apps.vool_cli web --browse https://example.com # render JS-heavy page
```

In chat, use `/web <query>`. While web is off, both surfaces print a clear note pointing you to `VOOL_ENABLE_WEB=1`, and `GET /api/runtime/capabilities` reports `web.live_lookup` and `browser_render` as unsupported with that same enable hint.

## Remote dial (opt-in)

A `null://` request normally runs LOCALLY. Remote dial lets a request instead reach the named `.null` agent's on-chain x402 endpoint, hand it the task, and return that agent's result — falling back to the local run on any miss. It is opt-in and OFF by default; network egress AND spend are both separately gated.

Enable remote dial for a session:

```bash
VOOL_ENABLE_NULL_DIAL=1 .venv/bin/python -m apps.vool_cli dial web0.null "summarize this page"
# VOOL_ALLOW_NULL_DIAL=1 is accepted as an alias
```

To make it persistent, export the flag or set `system.allow_null_dial: true` in `config/default_policy.yaml`.

Payment is a second, independent opt-in. If the endpoint answers with HTTP 402 (payment required), VOOL pays only when you pass `--allow-spend`, and always within a cap (`--max-spend <usdc>`, clamped to a 1.0 USDC ceiling). Without `--allow-spend` you get a no-spend preview of what the endpoint is asking for.

```bash
VOOL_ENABLE_NULL_DIAL=1 .venv/bin/python -m apps.vool_cli dial web0.null "embed this" --allow-spend --max-spend 0.05
```

In chat, use `/dial <name>.null "<task>"`. While dial is off, the CLI prints how to enable it and makes zero network calls. The SSRF guard rejects any endpoint that resolves to a private, loopback, link-local, or otherwise internal address.

## Common Notes

- VOOL is alpha. Read [STATUS.md](STATUS.md) before assuming a surface is production-ready.
- `main` is the current alpha truth; do not keep reading the repo as if the real runtime lives on an unmerged side branch.
- The strongest current lane is local-first runtime plus Hive/public-web/OpenClaw surfaces.
- The strongest default install lane is still accurate auto selection from current hardware and configured providers.
- A configured Kimi lane is now a real first-class supported profile through the shared OpenAI-compatible runtime bootstrap, but it is still optional rather than the default local-first path.
- Tether and QVAC are still not first-class supported stacks yet.
- Safe machine reads are intentionally narrow: Desktop, Downloads, and Documents are supported; arbitrary filesystem reads outside the active workspace are not.
- Broader WAN hardening and some payment/economy claims are still partial or simulated.

## Troubleshooting

- If install succeeded but the local API is missing, verify `http://127.0.0.1:11435/healthz`.
- If OpenClaw does not see VOOL, restart the launcher once after install.
- If OpenClaw shows `gateway token mismatch`, you are almost always pointing the launcher at the wrong OpenClaw home. Export `OPENCLAW_STATE_DIR` or `OPENCLAW_HOME` for the gateway you actually started, then reopen the launcher.
- If you need the broader maturity picture, read [STATUS.md](STATUS.md).
