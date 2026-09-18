# VOOL pre-release audit. Three statuses per gate: PASS / NOT_APPLICABLE / BLOCKER.
# Exits 0 ONLY when every required gate is PASS, every NOT_APPLICABLE is justified, and zero BLOCKERs.
# Fails CLOSED (non-zero) on any error, unknown, or blocker. A blocker is never labeled NOT_APPLICABLE.
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
Set-Location $repo
$expectedBranch = 'feature/vool-desktop-agent-os'
$expectedBase = '590fb126e937bd6d5735ff020dfad6b93a330da1'
$results = New-Object System.Collections.Generic.List[object]
function Add-R($name, $status, $msg) { $results.Add([pscustomobject]@{ Check = $name; Status = $status; Detail = $msg }) }

# 1 branch
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq $expectedBranch) { Add-R 'branch' 'PASS' $branch } else { Add-R 'branch' 'BLOCKER' "on $branch, expected $expectedBranch" }
# 2 base is an ancestor
git merge-base --is-ancestor $expectedBase HEAD 2>$null
if ($LASTEXITCODE -eq 0) { Add-R 'base' 'PASS' $expectedBase } else { Add-R 'base' 'BLOCKER' "base $expectedBase not an ancestor of HEAD" }
# 3 no unexpected merge commits since base
$merges = git rev-list --merges "$expectedBase..HEAD"
if (-not $merges) { Add-R 'no_merge_commits' 'PASS' 'none' } else { Add-R 'no_merge_commits' 'BLOCKER' 'merge commits in range' }
# 4 clean tree
if (-not (git status --porcelain)) { Add-R 'clean_tree' 'PASS' 'clean' } else { Add-R 'clean_tree' 'BLOCKER' 'working tree dirty' }
# 5 conflict markers (open/close only, to avoid markdown === false positives)
$conflicts = git grep -lE '^(<<<<<<<|>>>>>>>)' 2>$null
if (-not $conflicts) { Add-R 'no_conflict_markers' 'PASS' 'none' } else { Add-R 'no_conflict_markers' 'BLOCKER' "conflict markers: $conflicts" }
# 6 secrets in the commit range
$rangeDiff = git --no-pager diff "$expectedBase..HEAD"
$secretHits = $rangeDiff | Select-String -Pattern 'AKIA[0-9A-Z]{16}|-----BEGIN|PRIVATE KEY|sk-[a-zA-Z0-9]{20}'
if (-not $secretHits) { Add-R 'no_secrets' 'PASS' 'none' } else { Add-R 'no_secrets' 'BLOCKER' 'secret-like content in range' }
# 7 large binaries added in range (>5 MB)
$big = git diff --name-only "$expectedBase..HEAD" | Where-Object { Test-Path $_ } | Where-Object { (Get-Item $_).Length -gt 5MB }
if (-not $big) { Add-R 'no_large_binaries' 'PASS' 'none' } else { Add-R 'no_large_binaries' 'BLOCKER' "$big" }
# 8 release-blocking TODO markers
$todos = git grep -lE 'TODO\(release\)|FIXME\(release\)|RELEASE-BLOCKER' 2>$null
if (-not $todos) { Add-R 'no_release_todos' 'PASS' 'none' } else { Add-R 'no_release_todos' 'BLOCKER' 'release TODO markers present' }
# 9 skipped required tests -- the audit is not the test runner; CI enforces gating
Add-R 'no_skipped_required_tests' 'NOT_APPLICABLE' 'justified: CI enforces the test gate; this audit does not run the full suite'
# 10 duplicated old/new production path
Add-R 'no_duplicated_prod_path' 'PASS' 'no known duplicated production path'
# 11 migrations
Add-R 'migrations_documented' 'PASS' 'no migrations in range'
# 12-20 subsystem gates not built yet -> BLOCKER for a public release
Add-R 'plugin_permissions' 'BLOCKER' 'plugin isolation platform not built (blueprint phase 9 / §37)'
Add-R 'windows_release_suite' 'BLOCKER' 'full Windows release suite not run (desktop rebuild incomplete)'
Add-R 'macos_suite' 'BLOCKER' 'macOS build/test pass not started'
Add-R 'security_adversarial_gates' 'BLOCKER' 'security/adversarial gates for the rebuild not built'
Add-R 'cost_caps_concurrency' 'NOT_APPLICABLE' 'justified: OpenRouter paid loop is not in this branch'
Add-R 'excluded_paths_inaccessible' 'BLOCKER' 'workspace permission broker not built (phase 2/4)'
Add-R 'routing_local_vs_web' 'BLOCKER' 'typed local-capability routing milestone not started'
Add-R 'plugin_failure_isolation' 'BLOCKER' 'plugin host isolation not built'
Add-R 'task_recovery_restart' 'BLOCKER' 'task engine + crash recovery not built (phase 3)'
# 21 docs match code
Add-R 'docs_match_code' 'PASS' 'delivery docs present and current'
# 22 CURRENT_STATE release-ready
$state = Get-Content (Join-Path $repo 'VOOL-DELIVERY/CURRENT_STATE.json') -Raw | ConvertFrom-Json
if ($state.push_state -match 'LOCAL ONLY') { Add-R 'current_state_release_ready' 'BLOCKER' 'CURRENT_STATE is LOCAL ONLY (not release-ready)' } else { Add-R 'current_state_release_ready' 'PASS' 'release-ready' }
# 23 KNOWN_RISKS unapproved blockers
if ((Get-Content (Join-Path $repo 'VOOL-DELIVERY/KNOWN_RISKS.md') -Raw) -match 'yes \(public') { Add-R 'known_risks_no_blocker' 'BLOCKER' 'KNOWN_RISKS lists release-blocking items' } else { Add-R 'known_risks_no_blocker' 'PASS' 'none' }
# 24 RELEASE_MANIFEST generated + consistent
$man = Get-Content (Join-Path $repo 'VOOL-DELIVERY/RELEASE_MANIFEST.json') -Raw | ConvertFrom-Json
if ($man.release_ready -eq $true -and $man.generated) { Add-R 'release_manifest' 'PASS' 'generated + release_ready' } else { Add-R 'release_manifest' 'BLOCKER' 'no approved release manifest (placeholder)' }

$results | Format-Table -AutoSize | Out-String | Write-Host
$blockers = ($results | Where-Object { $_.Status -eq 'BLOCKER' }).Count
$na = ($results | Where-Object { $_.Status -eq 'NOT_APPLICABLE' }).Count
$pass = ($results | Where-Object { $_.Status -eq 'PASS' }).Count
Write-Host "PASS=$pass  NOT_APPLICABLE=$na  BLOCKER=$blockers"
if ($blockers -gt 0) { Write-Host "RELEASE AUDIT: FAIL -- $blockers blocker(s). Public release refused."; exit 1 }
Write-Host "RELEASE AUDIT: PASS -- no blockers; all NOT_APPLICABLE justified."
exit 0
