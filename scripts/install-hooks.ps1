# Install the versioned pre-push guard into this clone's .git/hooks.
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
$src = Join-Path $repo 'scripts/hooks/pre-push'
$hooksDir = (git rev-parse --git-path hooks).Trim()
if (-not [System.IO.Path]::IsPathRooted($hooksDir)) { $hooksDir = Join-Path $repo $hooksDir }
$dst = Join-Path $hooksDir 'pre-push'
New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
Copy-Item $src $dst -Force
Write-Host "Installed pre-push guard -> $dst"
Write-Host "Reminder: this hook is an accident guard only; GitHub branch protection is the backstop."
