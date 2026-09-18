# The ONLY path that may push to the public repo. Refuses unless every gate is satisfied.
# Never force-pushes. Requires an explicit GO_APPROVAL.json created only after sls says GO.
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim(); Set-Location $repo
$goPath = Join-Path $repo 'VOOL-DELIVERY/GO_APPROVAL.json'

if (-not (Test-Path $goPath)) {
  Write-Host "REFUSED: no VOOL-DELIVERY/GO_APPROVAL.json. A public release requires an explicit GO approval artifact."
  exit 1
}
$go = Get-Content $goPath -Raw | ConvertFrom-Json
$head = (git rev-parse HEAD).Trim()
if ($go.approved_commit -ne $head) { Write-Host "REFUSED: approved commit $($go.approved_commit) != HEAD $head."; exit 1 }
if (git status --porcelain) { Write-Host "REFUSED: working tree is not clean."; exit 1 }

Write-Host "Running release audit..."
& (Join-Path $repo 'scripts/vool-release-audit.ps1')
if ($LASTEXITCODE -ne 0) { Write-Host "REFUSED: release audit failed."; exit 1 }

$publicUrl = git remote get-url public 2>$null
if (-not $publicUrl) { Write-Host "REFUSED: 'public' remote is not configured."; exit 1 }
$target = if ($go.target_branch) { $go.target_branch } else { 'main' }
Write-Host "About to push (single, no force):"
Write-Host "  remote : public ($publicUrl)"
Write-Host "  ref    : HEAD -> refs/heads/$target"
Write-Host "  commit : $head"
$confirm = Read-Host "Type exactly 'PUBLISH $head' to proceed"
if ($confirm -ne "PUBLISH $head") { Write-Host "REFUSED: confirmation mismatch."; exit 1 }

$env:VOOL_PUBLIC_RELEASE = '1'
try {
  git push public "HEAD:refs/heads/$target"
  if ($LASTEXITCODE -ne 0) { Write-Host "Push failed."; exit 1 }
  $remoteSha = (git ls-remote public "refs/heads/$target").Split()[0]
  if ($remoteSha -ne $head) { Write-Host "ABORT: remote SHA $remoteSha != local $head."; exit 1 }
  Add-Content (Join-Path $repo 'VOOL-DELIVERY/TASK_LEDGER.jsonl') "{`"ts`":`"$(Get-Date -Format yyyy-MM-dd)`",`"event`":`"public_release`",`"commit`":`"$head`",`"remote_sha`":`"$remoteSha`",`"target`":`"$target`"}"
  Remove-Item $goPath -Force
  Write-Host "Public release pushed: $head -> public/$target. Approval invalidated."
} finally {
  Remove-Item Env:\VOOL_PUBLIC_RELEASE -ErrorAction SilentlyContinue
}
