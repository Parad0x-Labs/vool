<#
Assembles a self-contained VOOL bundle staging directory: a relocatable embedded Python with
the lean runtime deps (no heavy ML training stack -- that is training-only), the VOOL source,
the bundled Ollama binary, the launcher, and optionally the local model. Feed the resulting
$Stage directory to Inno Setup (vool.iss) to produce VOOL-Setup.exe.

Layout produced:
  <Stage>\python\        embedded Python + Lib\site-packages (lean deps)
  <Stage>\app\           VOOL source (apps, core, adapters, config)
  <Stage>\ollama\        ollama.exe + lib\ollama\ runner support files
  <Stage>\models\        Ollama model store (populated only with -IncludeModel)
  <Stage>\VOOL.cmd      launcher

Usage:
  pwsh installer\bundle\build_bundle.ps1 -Stage G:\vool-build\bundle
  pwsh installer\bundle\build_bundle.ps1 -Stage G:\vool-build\bundle -IncludeModel   # multi-GB offline bundle
#>
param(
  [string]$Stage = "G:\vool-build\bundle",
  [string]$PythonVersion = "3.12.7",
  [string]$OllamaExe = "G:\Ollama\ollama.exe",
  [string]$Model = "qwen2.5:7b",
  [switch]$IncludeModel
)
$ErrorActionPreference = "Stop"
# `Resolve-Path`.Path can be provider-qualified when the checkout is reached through a WSL UNC
# path (for example, a PowerShell provider prefix before a UNC path). Embedded Python and the
# Windows file APIs need the provider path itself, so normalize through Convert-Path.
$RepoRoot = Convert-Path (Join-Path $PSScriptRoot "..\..")
Write-Host "Repo:  $RepoRoot"
Write-Host "Stage: $Stage"

# --- 1. embedded Python -------------------------------------------------------------------
$pyDir = Join-Path $Stage "python"
if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
New-Item -ItemType Directory -Force $pyDir | Out-Null
$embedZip = Join-Path $Stage "python-embed.zip"
$embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
Write-Host "Downloading embeddable Python $PythonVersion ..."
Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
Expand-Archive -Path $embedZip -DestinationPath $pyDir -Force
Remove-Item $embedZip -Force
# enable site-packages so pip-installed deps resolve
$pth = (Get-ChildItem "$pyDir\python*._pth" | Select-Object -First 1).FullName
$lines = Get-Content $pth | ForEach-Object { if ($_ -match '^\s*#\s*import site\s*$') { 'import site' } else { $_ } }
if ($lines -notcontains 'Lib\site-packages') { $lines += 'Lib\site-packages' }
Set-Content -Path $pth -Value $lines -Encoding ascii

# --- 2. lean runtime deps (no heavy ML training stack) ------------------------------------
$env:PIP_CACHE_DIR = Join-Path $Stage "..\pip-cache"
$py = Join-Path $pyDir "python.exe"
Write-Host "Bootstrapping pip + lean deps ..."
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $Stage "get-pip.py") -UseBasicParsing
& $py (Join-Path $Stage "get-pip.py") --no-warn-script-location -q
if ($LASTEXITCODE -ne 0) { throw "Embedded Python pip bootstrap failed." }
# setuptools+wheel must be present before dependency installation so pywebview's build backend
# is available in the embedded Python. pywebview+pythonnet drive the native WebView2 app window
# (vool_window.py). Still no heavy ML training stack.
& $py -m pip install --no-warn-script-location -q `
    setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Embedded Python setuptools/wheel bootstrap failed." }
& $py -m pip install --no-build-isolation --no-warn-script-location -q `
    pydantic cryptography requests pynacl keyring psutil pyyaml "starlette>=0.37,<2.0" "uvicorn>=0.30,<1.0" solders `
    pywebview pythonnet "pypdf==6.19.0" "xlrd==2.0.1"
if ($LASTEXITCODE -ne 0) { throw "Embedded Python runtime dependency installation failed." }
& $py -m pip check
if ($LASTEXITCODE -ne 0) { throw "Embedded Python dependency check failed." }
Remove-Item (Join-Path $Stage "get-pip.py") -Force -ErrorAction SilentlyContinue

# --- 3. VOOL source ----------------------------------------------------------------------
$appDir = Join-Path $Stage "app"
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
New-Item -ItemType Directory -Force $appDir | Out-Null
# All top-level runtime packages the server imports (verified by running the staged bundle),
# plus config data. Excludes tests and dev-only trees to keep the bundle lean. `skills` and
# `plugins` are runtime CONTENT (the native skill library and the bundled first-party packs,
# e.g. vool-database) resolved from the app root at run time -- a bundle without them ships a
# runtime with an empty library and catalog. `channels` was deleted from the tree (e7b1bd31)
# and is removed here with them added, mirroring build_macos_app.sh's SRC_PACKAGES.
foreach ($d in @("apps", "core", "adapters", "storage", "network", "relay", "retrieval", "sandbox", "tools", "ops", "installer", "config", "skills", "plugins")) {
  $src = Join-Path $RepoRoot $d
  if (-not (Test-Path $src)) { throw "staging list names a package absent from the repo: $d" }
  Copy-Item $src (Join-Path $appDir $d) -Recurse -Force
}
# Canonical project grounding reads only this small allowlist. Ship the same sources in the
# installed bundle so a release build has the grounding that a source checkout has.
foreach ($relativePath in @("AGENT_HANDOVER.md", "README.md", "docs\SYSTEM_SPINE.md", "docs\STATUS.md", "docs\PROOF_PATH.md")) {
  $src = Join-Path $RepoRoot $relativePath
  if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
    throw "Canonical grounding source missing: $relativePath"
  }
  $dest = Join-Path $appDir $relativePath
  New-Item -ItemType Directory -Force (Split-Path -Parent $dest) | Out-Null
  Copy-Item -LiteralPath $src -Destination $dest -Force
}
# drop bytecode caches to keep the bundle clean
Get-ChildItem $appDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# A bundle has no .git directory at runtime. Stamp the exact source revision into the staged app
# so /healthz, /api/runtime/version, and the chat footer can distinguish this installer from an
# older build. The shared helper must read the commit from the checkout itself. If Windows Git
# cannot inspect the source checkout (for example, a WSL UNC worktree), build from a clean native
# checkout instead of accepting caller-supplied provenance.
$buildSourcePath = Join-Path $appDir "config\build-source.json"
Push-Location $RepoRoot
try {
  & $py (Join-Path $RepoRoot "installer\stamp_build_source.py") --root $RepoRoot --out $buildSourcePath
  if ($LASTEXITCODE -ne 0) { throw "Failed to stamp bundle source provenance." }
}
finally {
  Pop-Location
}
$buildSource = Get-Content -Raw $buildSourcePath | ConvertFrom-Json
if ([string]$buildSource.source_kind -ne "git" -or [string]$buildSource.commit_full -notmatch '^[0-9a-f]{40}$') {
  throw "Bundle source provenance is not an exact Git commit. Build from a native Git checkout."
}
if ([bool]$buildSource.dirty_state) {
  throw "Bundle source checkout is dirty. Commit or remove local changes before building."
}
$appVersion = (& $py -c "import sys; sys.path.insert(0, r'$appDir'); from core.app_version import VOOL_VERSION; print(VOOL_VERSION)").Trim()
if (-not $appVersion) { throw "Failed to resolve the staged VOOL version." }

# --- 4. Ollama + launcher -----------------------------------------------------------------
New-Item -ItemType Directory -Force (Join-Path $Stage "ollama") | Out-Null
Copy-Item $OllamaExe (Join-Path $Stage "ollama\ollama.exe") -Force
$ollamaInstallRoot = Split-Path -Parent $OllamaExe
$ollamaSupportSource = Join-Path $ollamaInstallRoot "lib\ollama"
$ollamaRunner = Join-Path $ollamaSupportSource "llama-server.exe"
if (-not (Test-Path -LiteralPath $ollamaSupportSource -PathType Container)) {
  throw "Ollama runtime support directory not found: $ollamaSupportSource. Use a complete official Ollama installation."
}
if (-not (Test-Path -LiteralPath $ollamaRunner -PathType Leaf)) {
  throw "Ollama runner missing from the installation: $ollamaRunner. Install or repair Ollama before building the bundle."
}
Copy-Item $ollamaSupportSource (Join-Path $Stage "ollama\lib\ollama") -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $Stage "models") | Out-Null
Copy-Item (Join-Path $PSScriptRoot "vool-launch.cmd") (Join-Path $Stage "VOOL.cmd") -Force
Copy-Item (Join-Path $PSScriptRoot "vool.vbs") (Join-Path $Stage "vool.vbs") -Force
Copy-Item (Join-Path $PSScriptRoot "vool-open.ps1") (Join-Path $Stage "vool-open.ps1") -Force
Copy-Item (Join-Path $PSScriptRoot "vool_window.py") (Join-Path $Stage "vool_window.py") -Force
Copy-Item (Join-Path $PSScriptRoot "bundle_supervisor.py") (Join-Path $Stage "bundle_supervisor.py") -Force
Copy-Item (Join-Path $PSScriptRoot "vool_protocol_handler.py") (Join-Path $Stage "vool_protocol_handler.py") -Force

# The bundle manifest is the single model-selection source for the launcher, runtime, model
# store, and doctor. It is deliberately written before an optional model pull so a failed pull
# cannot change the model the installed bundle claims to ship.
$manifest = [ordered]@{
  schema = "vool.bundle_manifest.v1"
  app_version = $appVersion
  source_commit = [string]$buildSource.commit
  source_dirty_state = [bool]$buildSource.dirty_state
  selected_model = $Model
  model_store = "%LOCALAPPDATA%\VOOL\models"
  api_url = "http://127.0.0.1:11435"
  ollama_url = "http://127.0.0.1:11434"
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $Stage "bundle_manifest.json") -Encoding utf8

# --- 5. optional model embed (offline bundle) ---------------------------------------------
if ($IncludeModel) {
  Write-Host "Embedding model $Model into the bundle (offline mode) ..."
  $env:OLLAMA_MODELS = Join-Path $Stage "models"
  & $OllamaExe pull $Model
}

$sz = (Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("Bundle staged at {0} -- {1:N0} MB" -f $Stage, ($sz/1MB))
