#!/bin/sh
# macOS/Linux parity of vool-checkpoint.ps1. Local checkpoint on the private working branch (main by
# default); never pushes -- the pre-push hook guards the frozen public repo.
set -e
[ -n "$1" ] || { echo "usage: vool-checkpoint <message>"; exit 1; }
msg="$1"
repo="$(git rev-parse --show-toplevel)"; cd "$repo"
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "HEAD" ] && { echo "REFUSED: detached HEAD."; exit 1; }
[ -n "$(git status --porcelain)" ] || { echo "Nothing to checkpoint (clean tree)."; exit 1; }
git status --short
git add -A
if git --no-pager diff --staged | grep -Eq 'AKIA[0-9A-Z]{16}|-----BEGIN|PRIVATE KEY|sk-[a-zA-Z0-9]{20}'; then git reset -q; echo "REFUSED: secret-like content staged."; exit 1; fi
big="$(git diff --staged --name-only | while IFS= read -r f; do [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 5242880 ] && echo "$f"; done)"
[ -n "$big" ] && { git reset -q; echo "REFUSED: oversized files staged: $big"; exit 1; }
pyf="$(git diff --staged --name-only -- '*.py')"
if [ -n "$pyf" ]; then python3 -m ruff check $pyf >/dev/null 2>&1 || { git reset -q; echo "REFUSED: ruff failed on changed files."; exit 1; }; fi
ledger="$repo/VOOL-DELIVERY/TASK_LEDGER.jsonl"
[ -f "$ledger" ] || { git reset -q; echo "REFUSED: bookkeeping ledger missing."; exit 1; }
esc="$(printf '%s' "$msg" | sed 's/"/\\"/g')"
printf '{"ts":"%s","event":"checkpoint","branch":"%s","summary":"%s","pushed":false}\n' "$(date +%Y-%m-%d)" "$branch" "$esc" >> "$ledger"
git add VOOL-DELIVERY/TASK_LEDGER.jsonl
git commit -q -m "$msg"
echo "Checkpoint commit: $(git rev-parse HEAD) on $branch (nothing pushed)."
