# Wait for the VOOL server to be ready, then open it in a chromeless APP WINDOW (Edge --app):
# no tabs, no address bar, its own taskbar entry -- so VOOL looks like a native desktop app
# (Codex/Claude/Cursor style) while still being the /chat UI our own server renders.
#
# The dedicated --user-data-dir is essential: without it, if the user already has Edge open,
# "--app=URL" attaches to the running Edge and opens a TAB instead of a standalone app window.
# A separate profile forces a fresh Edge instance in app mode -> a real app window every time.
# Falls back to the default browser if Edge is not installed.
$ErrorActionPreference = 'SilentlyContinue'
$url = 'http://127.0.0.1:11435/chat'
$health = 'http://127.0.0.1:11435/healthz'
for ($i = 0; $i -lt 90; $i++) {
  try { if ((Invoke-WebRequest $health -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) { break } } catch { Start-Sleep -Seconds 1 }
}
$edge = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
  $profileDir = Join-Path $env:LOCALAPPDATA 'VOOL\browser'
  Start-Process -FilePath $edge -ArgumentList `
    "--app=$url", "--user-data-dir=`"$profileDir`"", "--no-first-run", "--no-default-browser-check", "--window-size=1220,860"
} else {
  Start-Process $url
}
