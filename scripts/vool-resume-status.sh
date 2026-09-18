#!/bin/sh
# Print the current delivery state so work can resume without chat history. Read-only.
set -e
repo="$(git rev-parse --show-toplevel)"
state="$repo/VOOL-DELIVERY/CURRENT_STATE.json"
echo "== VOOL resume status =="
echo "Branch     : $(git rev-parse --abbrev-ref HEAD)"
echo "HEAD       : $(git rev-parse HEAD)"
if [ -f "$state" ]; then
  if command -v jq >/dev/null 2>&1; then
    echo "Base       : $(jq -r .base_commit "$state")  ($(jq -r .base_ref "$state"))"
    echo "Active     : $(jq -r .active_milestone "$state")"
    echo "Last gate  : $(jq -r .last_full_gate "$state")"
    echo "Push state : $(jq -r .push_state "$state")"
    echo "Next       : $(jq -r .next_action "$state")"
  else
    echo "(install jq for parsed fields) $state"
  fi
else
  echo "CURRENT_STATE.json not found."
fi
if [ -n "$(git status --porcelain)" ]; then echo "Working tree: DIRTY"; git status --short; else echo "Working tree: clean"; fi
echo "Remotes:"; git remote -v
