#!/bin/bash
# Overnight orchestrator. ONE job touches the daemon at a time; a landed fix triggers a regression.
#
# The rule this exists to enforce: after any root fix lands, at least half the previous tests re-run
# against the NEW code before anything else proceeds. Two things make that non-trivial and both are
# handled here:
#   1. the app runtime is frozen at the SHA it booted with, so a fresh commit does NOT reach it --
#      the bundle must be re-synced and the runtime restarted, or the "regression" tests old code
#   2. two suites hitting one daemon interleave sessions and corrupt both results, so every job
#      takes a lock
#
# Ordering: a regression triggered by a new commit JUMPS THE QUEUE. Knowing a fix broke something
# matters more than finishing the next exploratory set.

set -uo pipefail
N="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETS="$N/sets"
WT="${VOOL_WORKTREE:-$(cd "$N/../.." && pwd)}"
PY="${VOOL_PY:-python3}"
APP="${VOOL_APP:-$HOME/Applications/VOOL.app}"
SUPPORT=~/Library/Application\ Support/VOOL
LOCK="$N/.daemon.lock"
STATE="$N/.last_sha"
LOG="$N/orchestrator.log"

say() { echo "[orch $(date +%H:%M:%S)] $*"; }   # stdout is already redirected to $LOG

lock()   { while ! mkdir "$LOCK" 2>/dev/null; do sleep 10; done; }
unlock() { rmdir "$LOCK" 2>/dev/null || true; }

runtime_up() { curl -fsS --max-time 4 http://127.0.0.1:11435/healthz >/dev/null 2>&1; }

# Re-sync the app bundle to the current worktree and restart, so tests exercise the NEW code.
resync_runtime() {
  local sha; sha=$(git -C "$WT" rev-parse --short HEAD)
  say "re-syncing app bundle to $sha"
  local P; P=$(lsof -nP -iTCP:11435 -sTCP:LISTEN 2>/dev/null | tail -1 | awk '{print $2}')
  [ -n "${P:-}" ] && kill "$P" 2>/dev/null
  sleep 4
  local A="$APP/Contents/Resources/app"
  for d in core apps tools retrieval storage adapters channels config installer; do
    [ -d "$WT/$d" ] && { rm -rf "$A/$d"; cp -R "$WT/$d" "$A/$d"; }
  done
  find "$A" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null
  rm -f "$A/.vool_stopped"
  "$PY" - "$A/config/build-source.json" "$(git -C "$WT" rev-parse HEAD)" <<'PYEOF'
import json,sys,datetime
p,full=sys.argv[1],sys.argv[2]
json.dump({"source_kind":"git","commit":full[:12],"commit_full":full,"dirty_state":False,
 "built_at":datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
 "branch":"fix/canonical-obligation-floor","ref":"fix/canonical-obligation-floor"},open(p,"w"),indent=1)
PYEOF
  ( cd "$A" && VOOL_HOME="$SUPPORT/runtime" OLLAMA_MODELS=~/.ollama/models PYTHONPATH="$A" \
      nohup "$APP/Contents/Resources/python/bin/python3" -m apps.vool_api_server \
      >>"$SUPPORT/runtime.log" 2>&1 & )
  for _ in $(seq 1 30); do sleep 4; runtime_up && break; done
  runtime_up && say "runtime back up at $sha" || say "WARNING runtime did not come back at $sha"
}

run_job() {  # run_job <name> <set.json...>
  local name="$1"; shift
  lock
  say "START $name"
  ( cd "$N" && "$PY" overnight.py "$name" "$@" ) >>"$LOG" 2>&1
  say "DONE  $name"
  unlock
}

# HALF the previous corpus -- the operator's rule. set11 (99) + set_typos + set_travel ~= 123 prompts,
# roughly half of everything driven so far, and it spans every lane a root fix could plausibly break.
regression() {
  local why="$1"
  say "REGRESSION triggered by: $why"
  lock; resync_runtime; unlock
  run_job "regr-$(git -C "$WT" rev-parse --short HEAD)-$(date +%H%M)" \
          "$SETS/set11.json" "$SETS/set_typos.json" "$SETS/set_travel.json"
}

# Any suite started OUTSIDE this orchestrator holds no lock, so wait it out before claiming the
# daemon. Without this the orchestrator races the run that is already in flight, two suites
# interleave sessions on one daemon, and BOTH sets of results are garbage. Observed immediately on
# first start, which is exactly why it is here.
while pgrep -f "overnight.py night" >/dev/null 2>&1; do
  say "waiting: a suite started outside the orchestrator is still driving the daemon"
  sleep 30
done

git -C "$WT" rev-parse HEAD > "$STATE"
say "orchestrator started at $(cat "$STATE" | cut -c1-12)"

# ---- queue of exploratory work; a new commit preempts it with a regression -----------------------
# Both exploratory sets have run (run-night2-noise, run-night3-context). The standing job
# now is the operator rule: a landed fix triggers a resync + half-corpus regression.
# A REGRESSION PROVES NOTHING BROKE. It does not prove the fix WORKS: measured 2026-08-18,
# of the 123 regression prompts only ~7 touch the three code changes in the run they were
# added for -- 4 SSRF-download, 2 unit-as-place, 1 blocked-live-info. `set_fixes.json` is the
# other half: one lane per landed fix, driven end to end through the app, plus the must-keep
# control beside each so a fix that over-corrects is caught in the same run.
QUEUE=("fixes:$SETS/set_fixes.json")

while :; do
  # 1. did a fix land? that outranks everything queued.
  NOW=$(git -C "$WT" rev-parse HEAD)
  if [ "$NOW" != "$(cat "$STATE")" ]; then
    echo "$NOW" > "$STATE"
    regression "new commit $(echo "$NOW" | cut -c1-12): $(git -C "$WT" log --format=%s -1)"
    continue
  fi
  # 2. otherwise take the next exploratory job
  if [ ${#QUEUE[@]} -gt 0 ]; then
    entry="${QUEUE[0]}"; QUEUE=("${QUEUE[@]:1}")
    run_job "${entry%%:*}" "${entry#*:}"
    continue
  fi
  # 3. nothing queued -- idle, still watching for commits
  sleep 60
done
