# Local checkpoint commit on the private working branch (main by default). Never pushes; the
# pre-push hook is what guards the frozen public repo. Updates the task ledger.
param([Parameter(Mandatory = $true)][string]$Message)
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim(); Set-Location $repo
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq 'HEAD') { Write-Host "REFUSED: detached HEAD."; exit 1 }
if (-not (git status --porcelain)) { Write-Host "Nothing to checkpoint (clean tree)."; exit 1 }

Write-Host "Changed files:"; git status --short
git add -A
$staged = git --no-pager diff --staged
$secretHits = $staged | Select-String -Pattern 'AKIA[0-9A-Z]{16}|-----BEGIN|PRIVATE KEY|sk-[a-zA-Z0-9]{20}'
if ($secretHits) { git reset -q; Write-Host "REFUSED: secret-like content staged."; exit 1 }
$big = git diff --staged --name-only | Where-Object { Test-Path $_ } | Where-Object { (Get-Item $_).Length -gt 5MB }
if ($big) { git reset -q; Write-Host "REFUSED: oversized files staged: $big"; exit 1 }
$pyFiles = git diff --staged --name-only -- '*.py'
if ($pyFiles) {
  py -m ruff check $pyFiles 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { git reset -q; Write-Host "REFUSED: ruff failed on changed files."; exit 1 }
}
$ledger = Join-Path $repo 'VOOL-DELIVERY/TASK_LEDGER.jsonl'
if (-not (Test-Path $ledger)) { git reset -q; Write-Host "REFUSED: bookkeeping ledger missing."; exit 1 }
$safeMsg = $Message -replace '"', '\"'
Add-Content $ledger "{`"ts`":`"$(Get-Date -Format yyyy-MM-dd)`",`"event`":`"checkpoint`",`"branch`":`"$branch`",`"summary`":`"$safeMsg`",`"pushed`":false}"
git add VOOL-DELIVERY/TASK_LEDGER.jsonl
git commit -q -m $Message
$sha = (git rev-parse HEAD).Trim()
Write-Host "Checkpoint commit: $sha on $branch (nothing pushed)."
