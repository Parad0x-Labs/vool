# VOOL self-contained Windows installer

Builds a double-click `VOOL-Setup.exe` that installs VOOL with **no system Python, Node, or
Ollama required** and no terminal. The product serves its own UI (`/chat`), so the bundle does
not ship or patch OpenClaw.

## What's in the bundle

| Part | Size | Notes |
|---|---|---|
| Embedded Python + lean runtime deps | ~90 MB | `pydantic, cryptography, requests, pynacl, keyring, psutil, pyyaml, starlette, uvicorn, solders`. **No torch/transformers** — those are training-only and never imported by the server. |
| VOOL source (`app/`) | ~15 MB | all runtime packages (verified by running the staged bundle) |
| Ollama | ~3.2 GB with the current official Windows runtime | `ollama.exe` plus the complete `lib\ollama\` runner/DLL support tree, bundled |
| Model (`models/`) | 0 or ~4.7 GB | first-run download by default; embedded with `-IncludeModel` |

Result: a **multi-GB installer** with the current official Ollama runtime (model on first run) or a
larger offline installer (`-IncludeModel`). The exact size follows the official Ollama backend
payloads and selected model; this builder does not download a model unless `-IncludeModel` is used.

## Build (on a Windows machine)

```powershell
# 1. assemble the bundle staging dir
$stage = "C:\vool-build\bundle"
$outDir = "C:\vool-build\dist"
installer\bundle\build_bundle.ps1 -Stage $stage -OllamaExe "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
#    (add -IncludeModel for the fully-offline ~5 GB bundle)

# 2. compile the installer (Inno Setup 6; install once via: winget install JRSoftware.InnoSetup)
$appVersion = & "$stage\python\python.exe" -c "import sys; sys.path.insert(0, r'$stage\app'); from core.app_version import VOOL_VERSION; print(VOOL_VERSION)"
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DStage=$stage" "/DOutDir=$outDir" "/DAppVersion=$appVersion" installer\bundle\vool.iss
#    -> C:\vool-build\dist\VOOL-Setup.exe
```

## Runtime layout (installed)

```
<install>\python\      embedded Python (relocatable)
<install>\app\         VOOL source
<install>\ollama\      ollama.exe + lib\ollama\ runner/DLL support files
%LOCALAPPDATA%\VOOL\models\  persistent Ollama model store (preserved on uninstall)
<install>\VOOL.cmd    launcher: starts Ollama + the server, opens /chat
```

The staged app includes `app\config\build-source.json`, and the root `bundle_manifest.json`
records the app version, exact source commit, dirty-state flag, selected model, and persistent model
store. The installed runtime exposes the platform-tagged identity through `/healthz` and
`/api/runtime/version`.

The launcher runs the server windowless on `127.0.0.1:11435`, pulls the model once if it isn't
present, waits for `/healthz`, then opens `http://127.0.0.1:11435/chat`.

## macOS (`VOOL.app` / `VOOL.dmg`)

`build_macos_app.sh` is the macOS counterpart. It produces a double-clickable **`VOOL.app`** whose
Dock icon is `installer/assets/vool.icns`, starts the runtime when it is not already healthy, and
opens the chat in a native **WKWebView** window (`vool_window.py` via pywebview), falling back to an
app-mode browser window when pywebview is unavailable.

```bash
# wraps an existing local install (repo + .venv) — smallest, good for development
bash installer/bundle/build_macos_app.sh

# fully self-contained: embedded CPython + lean deps + VOOL source + bundled ollama
bash installer/bundle/build_macos_app.sh --self-contained          # -> dist/VOOL.app  (~205 MB)
bash installer/bundle/build_macos_app.sh --self-contained --dmg    # -> dist/VOOL.dmg  (~90 MB)
```

Bundle layout mirrors the Windows one: `Contents/Resources/{python,app,ollama}` plus `vool.icns`,
with the launcher at `Contents/MacOS/VOOL`. It uses the **same lean dependency set** (no
torch/transformers) and the same top-level source packages, with a relocatable CPython from
python-build-standalone (supplied by `uv`). Staging starts from an **empty** `site-packages` on
purpose: the shared uv interpreter may have heavy packages installed into it, which would otherwise
inflate the bundle by more than a gigabyte. Writable state lives in
`~/Library/Application Support/VOOL`, and an existing `~/.ollama/models` store is reused so a first
launch does not re-download several GB.

> **macOS privacy (TCC):** do not install VOOL under `~/Desktop`, `~/Documents` or `~/Downloads`.
> Processes spawned by Finder and launchd cannot read those folders, so the app and the launchd
> keep-alive agent fail with `Operation not permitted` on the venv even though Terminal works. Both
> the installer and this builder warn (the builder refuses unless `ALLOW_TCC_ROOT=1`). Prefer
> `~/Applications/vool`.

## Build provenance (macOS builder, 2026-09-02)

The bundle's bytes must come from exactly one commit. The builder therefore:

- **refuses to build from a dirty tree.** Tracked modifications *and* uncommitted new files gate the
  build; ignored files (build outputs) do not. The 2026-09-02 demo artifact shipped
  `source_tree_clean:false` — uncommitted source riding inside a bundle whose manifest claimed a
  committed SHA. `VOOL_ALLOW_DIRTY_BUILD=1` is the explicit developer override: the build then
  stages the **working tree** and the manifest stamps the artifact **non-release** (`"release": false`).
- **stages release bytes from the commit, not the worktree.** A clean build extracts
  `git archive <sha>` into the bundle, so "bundle bytes == commit bytes" is structural (no probe/copy
  race), and untracked/ignored files cannot leak in.
- **verifies build identity agreement.** For self-contained builds, `config/build-source.json`,
  `config/release/update_channel.json`, and `Contents/Resources/BUILD_MANIFEST.json` must stamp the
  same exact SHA and release version, or the build fails — this is what makes `/healthz` and the
  manifest agree byte-for-byte on SHA/build identity at runtime.
- `BUILD_MANIFEST.json` carries: `exact_sha` (full commit), `version`, `build_id`, `build_time_utc`,
  `bundle_mode`, `platform`, `source_tree_clean`, `release`.

Finished build/test artifacts belong in a **stable gitignored directory** (e.g. `artifacts/app`),
never only in `/tmp`:

```bash
bash installer/bundle/build_macos_app.sh --self-contained --out artifacts/app
```

## Deterministic rebuilds

Two builds of the same SHA are byte-identical except for two known-variable build timestamps: the
manifest's `build_time_utc` and the bundled build-source stamp's `built_at` (identity fields in
both must match byte for byte). `git archive` staging, a timestamp-free Info.plist, static launcher
text, and hash-based (`unchecked-hash`) bytecode compilation — no install-time-mtime `.pyc` — cover
the rest. Compare two builds mechanically:

```bash
python3 installer/bundle/compare_app_bundles.py artifacts/app/VOOL.app dist/VOOL.app
# exit 0 = only known-variable differences; any drift is printed as NONDETERMINISTIC with its path
```

Canonical check procedure: build, move the artifact aside, rebuild into the SAME path, compare —
dependency console scripts (`python/bin/*`) and wheel `RECORD` indexes embed the ABSOLUTE
interpreter path inside the bundle, so builds placed at different output paths differ in exactly
those bytes (path-dependence, not time-dependence).

Out of scope, documented as unavoidably non-byte-identical: directory mtimes, extended attributes,
DMG container UUIDs/timestamps (`hdiutil`), and any code signature.

## Process ownership (single lifetime)

`VOOL.app` runs ONE supervisor lifetime: the native window host (`vool_window.py`) owns the
runtime child (`Start_VOOL.sh` → exec'd server, or the embedded `apps.vool_api_server`), gated on
`/healthz` + exact commit identity. Every teardown path converges on the same cleanup:

| Event | Outcome |
|---|---|
| Normal quit / Cmd+Q | window loop returns → `finally` SIGTERMs the owned process **group**, sweeps the child's `vool_api.pid` |
| Daemon crash or external `kill -9 <daemon>` | watchdog (`assert_alive`, 2s poll, `VOOL_RUNTIME_WATCHDOG_POLL` to tune) closes the window; host exits **3** |
| External `SIGTERM`/`SIGINT`/`SIGHUP` to the host | handler runs the same teardown, then exits — no orphaned daemon/listener |
| Failed boot (child dies pre-readiness / identity mismatch / timeout) | host exits 1 with the reason in the log; nothing left listening |
| Forced window close | window destroy → loop returns → `finally` teardown |
| External `kill -9 <window host>` | the daemon carries `VOOL_OWNED_BY_WINDOW_PID`; its parent-death watch flips `should_exit` so no headless daemon outlives its window |

Stale-pidfile hygiene: boot removes `vool_api.pid` only when it names a provably dead pid (a live
occupant's pidfile is load-bearing for the self-updater and stays). Teardown sweeps the OWNED
child's pidfile, and a signal arriving before ownership capture (or a daemon SIGKILLed mid-graceful
shutdown, which never reaches its own cleanup) falls back to that same dead-pid sweep. Updater
restart/rollback is untouched: the pidfile contract and `mac_update_helper.sh` handoff behave
exactly as before.

## Code signing

The `.exe` is **unsigned** unless compiled on a machine with a code-signing certificate; unsigned
installers trigger SmartScreen until signed (OV/EV cert).

The macOS `.app`/`.dmg` is likewise **unsigned and un-notarized**. It runs when built locally, but a
DMG *downloaded* from the internet carries the quarantine flag and Gatekeeper blocks it ("Apple
cannot check it for malicious software") until it is signed with a **Developer ID Application**
certificate and notarized:

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: <NAME> (<TEAMID>)" dist/VOOL.app
xcrun notarytool submit dist/VOOL.dmg \
  --apple-id <APPLE-ID> --team-id <TEAMID> --password <APP-SPECIFIC-PASSWORD> --wait
xcrun stapler staple dist/VOOL.dmg
```

Both steps need an Apple Developer account. Until then, keep it to internal testing or ship
right-click → Open instructions.
