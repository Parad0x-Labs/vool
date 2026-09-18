#!/bin/bash
# Re-sync the shipped app bundle to the worktree HEAD and restart it. The runtime is frozen at the
# SHA it booted with, so a commit does NOT reach the app on its own.
set -uo pipefail
WT="${VOOL_WORKTREE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
APP="${VOOL_APP:-$HOME/Applications/VOOL.app}"
A="$APP/Contents/Resources/app"
SUPPORT=~/Library/Application\ Support/VOOL
PY="${VOOL_PY:-python3}"
P=$(lsof -nP -iTCP:11435 -sTCP:LISTEN 2>/dev/null | tail -1 | awk '{print $2}')
[ -n "${P:-}" ] && kill "$P" 2>/dev/null
sleep 4
for d in core apps tools retrieval storage adapters channels config installer; do
  [ -d "$WT/$d" ] && { rm -rf "$A/$d"; cp -R "$WT/$d" "$A/$d"; }
done
find "$A" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null
rm -f "$A/.vool_stopped"
$PY - "$A/config/build-source.json" "$(git -C "$WT" rev-parse HEAD)" <<'PYEOF'
import json,sys,datetime
p,full=sys.argv[1],sys.argv[2]
json.dump({"source_kind":"git","commit":full[:12],"commit_full":full,"dirty_state":False,
 "built_at":datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
 "branch":"fix/canonical-obligation-floor","ref":"fix/canonical-obligation-floor"},open(p,"w"),indent=1)
PYEOF
( cd "$A" && VOOL_HOME="$SUPPORT/runtime" OLLAMA_MODELS=~/.ollama/models PYTHONPATH="$A" \
    nohup "$APP/Contents/Resources/python/bin/python3" -m apps.vool_api_server \
    >>"$SUPPORT/runtime.log" 2>&1 & )
for i in $(seq 1 30); do
  sleep 4
  C=$(curl -fsS --max-time 4 http://127.0.0.1:11435/healthz 2>/dev/null | $PY -c 'import json,sys; print(json.load(sys.stdin)["runtime"]["commit"])' 2>/dev/null)
  [ -n "$C" ] && { echo "  runtime up at $C  (worktree $(git -C $WT rev-parse --short HEAD))"; exit 0; }
done
echo "  WARNING runtime did not come back"
