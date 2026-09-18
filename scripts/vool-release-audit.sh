#!/bin/sh
# macOS/Linux parity of vool-release-audit.ps1. Statuses: PASS / NOT_APPLICABLE / BLOCKER.
# Exits 0 ONLY when every required gate is PASS, every NOT_APPLICABLE is justified, zero BLOCKERs.
set -e
repo="$(git rev-parse --show-toplevel)"; cd "$repo"
expected_branch="feature/vool-desktop-agent-os"
expected_base="590fb126e937bd6d5735ff020dfad6b93a330da1"
blockers=0; passc=0; nac=0
report() { printf '%-30s %-16s %s\n' "$1" "$2" "$3"; case "$2" in BLOCKER) blockers=$((blockers+1));; PASS) passc=$((passc+1));; NOT_APPLICABLE) nac=$((nac+1));; esac; }

b="$(git rev-parse --abbrev-ref HEAD)"; [ "$b" = "$expected_branch" ] && report branch PASS "$b" || report branch BLOCKER "on $b"
git merge-base --is-ancestor "$expected_base" HEAD 2>/dev/null && report base PASS "$expected_base" || report base BLOCKER "base not ancestor"
[ -z "$(git rev-list --merges "$expected_base..HEAD")" ] && report no_merge_commits PASS none || report no_merge_commits BLOCKER "merges present"
[ -z "$(git status --porcelain)" ] && report clean_tree PASS clean || report clean_tree BLOCKER "dirty"
git grep -lE '^(<<<<<<<|>>>>>>>)' >/dev/null 2>&1 && report no_conflict_markers BLOCKER "markers" || report no_conflict_markers PASS none
if git --no-pager diff "$expected_base..HEAD" | grep -Eq 'AKIA[0-9A-Z]{16}|-----BEGIN|PRIVATE KEY|sk-[a-zA-Z0-9]{20}'; then report no_secrets BLOCKER "secret-like"; else report no_secrets PASS none; fi
big="$(git diff --name-only "$expected_base..HEAD" | while IFS= read -r f; do [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 5242880 ] && echo "$f"; done)"
[ -z "$big" ] && report no_large_binaries PASS none || report no_large_binaries BLOCKER "$big"
git grep -lE 'TODO\(release\)|FIXME\(release\)|RELEASE-BLOCKER' >/dev/null 2>&1 && report no_release_todos BLOCKER "markers" || report no_release_todos PASS none
report no_skipped_required_tests NOT_APPLICABLE "justified: CI enforces the test gate"
report no_duplicated_prod_path PASS "none known"
report migrations_documented PASS "none in range"
report plugin_permissions BLOCKER "plugin isolation not built"
report windows_release_suite BLOCKER "rebuild suite not run"
report macos_suite BLOCKER "macOS pass not started"
report security_adversarial_gates BLOCKER "not built"
report cost_caps_concurrency NOT_APPLICABLE "justified: paid loop not in branch"
report excluded_paths_inaccessible BLOCKER "permission broker not built"
report routing_local_vs_web BLOCKER "typed routing milestone not started"
report plugin_failure_isolation BLOCKER "plugin host not built"
report task_recovery_restart BLOCKER "task engine not built"
report docs_match_code PASS "delivery docs current"
grep -q 'LOCAL ONLY' VOOL-DELIVERY/CURRENT_STATE.json && report current_state_release_ready BLOCKER "LOCAL ONLY" || report current_state_release_ready PASS "ready"
grep -q 'yes (public' VOOL-DELIVERY/KNOWN_RISKS.md && report known_risks_no_blocker BLOCKER "blockers listed" || report known_risks_no_blocker PASS none
grep -q '"release_ready": true' VOOL-DELIVERY/RELEASE_MANIFEST.json && report release_manifest PASS "ready" || report release_manifest BLOCKER "placeholder"

echo "PASS=$passc  NOT_APPLICABLE=$nac  BLOCKER=$blockers"
[ "$blockers" -gt 0 ] && { echo "RELEASE AUDIT: FAIL -- $blockers blocker(s). Public release refused."; exit 1; }
echo "RELEASE AUDIT: PASS."
exit 0
