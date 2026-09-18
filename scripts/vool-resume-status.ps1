# Print the current delivery state so work can resume without chat history. Read-only.
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
$statePath = Join-Path $repo 'VOOL-DELIVERY/CURRENT_STATE.json'
$state = if (Test-Path $statePath) { Get-Content $statePath -Raw | ConvertFrom-Json } else { $null }

Write-Host "== VOOL resume status =="
Write-Host ("Branch     : {0}" -f (git rev-parse --abbrev-ref HEAD).Trim())
Write-Host ("HEAD       : {0}" -f (git rev-parse HEAD).Trim())
if ($state) {
  Write-Host ("Base       : {0}  ({1})" -f $state.base_commit, $state.base_ref)
  Write-Host ("Active     : {0}" -f $state.active_milestone)
  Write-Host ("Last gate  : {0}" -f $state.last_full_gate)
  Write-Host ("Push state : {0}" -f $state.push_state)
  Write-Host  "Blockers   :"; foreach ($b in $state.blocked_work) { Write-Host "  - $b" }
  Write-Host ("Next       : {0}" -f $state.next_action)
} else {
  Write-Host "CURRENT_STATE.json not found."
}
$dirty = git status --porcelain
$treeState = if ($dirty) { 'DIRTY' } else { 'clean' }
Write-Host ("Working tree: {0}" -f $treeState)
if ($dirty) { $dirty | ForEach-Object { Write-Host "  $_" } }
Write-Host "Remotes:"; git remote -v | ForEach-Object { Write-Host "  $_" }
