#!/bin/sh
# macOS/Linux parity of vool-public-release.ps1. The ONLY path that may push to public. Never force-pushes.
set -e
repo="$(git rev-parse --show-toplevel)"; cd "$repo"
go="$repo/VOOL-DELIVERY/GO_APPROVAL.json"
[ -f "$go" ] || { echo "REFUSED: no VOOL-DELIVERY/GO_APPROVAL.json. A public release requires an explicit GO artifact."; exit 1; }
approved="$(sed -n 's/.*"approved_commit"[^"]*"\([^"]*\)".*/\1/p' "$go")"
head="$(git rev-parse HEAD)"
[ "$approved" = "$head" ] || { echo "REFUSED: approved commit $approved != HEAD $head."; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "REFUSED: working tree not clean."; exit 1; }
sh "$repo/scripts/vool-release-audit.sh" || { echo "REFUSED: release audit failed."; exit 1; }
purl="$(git remote get-url public 2>/dev/null)" || { echo "REFUSED: 'public' remote not configured."; exit 1; }
target="$(sed -n 's/.*"target_branch"[^"]*"\([^"]*\)".*/\1/p' "$go")"; [ -n "$target" ] || target="main"
echo "About to push (single, no force): public ($purl)  HEAD -> refs/heads/$target  commit $head"
printf "Type exactly 'PUBLISH %s' to proceed: " "$head"; read -r confirm
[ "$confirm" = "PUBLISH $head" ] || { echo "REFUSED: confirmation mismatch."; exit 1; }
VOOL_PUBLIC_RELEASE=1 git push public "HEAD:refs/heads/$target"
remote_sha="$(git ls-remote public "refs/heads/$target" | awk '{print $1}')"
[ "$remote_sha" = "$head" ] || { echo "ABORT: remote SHA $remote_sha != $head."; exit 1; }
printf '{"ts":"%s","event":"public_release","commit":"%s","remote_sha":"%s","target":"%s"}\n' "$(date +%Y-%m-%d)" "$head" "$remote_sha" "$target" >> "$repo/VOOL-DELIVERY/TASK_LEDGER.jsonl"
rm -f "$go"
echo "Public release pushed: $head -> public/$target. Approval invalidated."
