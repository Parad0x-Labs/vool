#!/bin/bash
# JOB 13d: rivals at their latest versions (OpenClaw 2026.10.8 on node 26.12.1, Hermes main c73b595), installed side by side.
# After run_13b3.sh releases the lock: 3-ask smoke (fset, local) + sandbox network check, then the cloud arm through
# or_proxy.py (cap $8 total for the night), then the local arm, then the real-home fingerprint. Same sandbox, config,
# 300 s cap and scorer as the old-version rows; outputs under runs/*-latest/.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
C=<work>/cache/memory-h2h-20261005
cd $M || exit 1
until grep -q "^lock released" run_13b3.log 2>/dev/null; do sleep 10; done
if [ -s $L ]; then echo "lock taken by someone else after 13b3: $(cat $L)" | tee -a run_13d.log; exit 3; fi
echo "bench-win-20261028 h2h 13d rivals-latest smoke + cloud + local (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log(){ echo "$*" | tee -a run_13d.log; }
spent(){ python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))"; }
log "start $(date -u +%FT%TZ) openclaw $(PATH=$C/node-v26.12.1-darwin-arm64/bin:/usr/bin:/bin $C/openclaw-2026.10.8/node_modules/.bin/openclaw --version 2>/dev/null | tail -1) hermes $(git -C $C/hermes-agent-c73b595 rev-parse --short HEAD)"
# sandbox network check with the new node and curl (expect both refused)
T=$M/runs/netcheck; mkdir -p $T/home $T/state
sandbox-exec -f rival.sb -D HOME_CLONE=$T/home -D STATE_DIR=$T/state /usr/bin/curl -s -m 8 -o /dev/null https://example.com; log "netcheck curl internet rc $?"
sandbox-exec -f rival.sb -D HOME_CLONE=$T/home -D STATE_DIR=$T/state $C/node-v26.12.1-darwin-arm64/bin/node -e "fetch('https://example.com').then(()=>process.exit(0),()=>process.exit(9))"; log "netcheck node26.12.1 internet rc $? (9 = refused)"
sandbox-exec -f rival.sb -D HOME_CLONE=$T/home -D STATE_DIR=$T/state /usr/bin/curl -s -m 8 -o /dev/null http://127.0.0.1:11434/api/tags; log "netcheck curl ollama localhost rc $?"
for s in openclaw hermes; do
  log "== smoke fset 3 $s latest $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system $s --set fset --ids F06,F14,F17 --tag smoke-latest --latest 2>&1 | tail -1 | tee -a run_13d.log
done
# cloud arm
python3 -I or_proxy.py --port 18765 --cap 8.0 --ledger or_ledger.jsonl > or_proxy-13d.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
for s in openclaw hermes; do
  log "== cloud fresh45 $s latest $(date -u +%FT%TZ) proxy pid $PROXY spent so far \$$(spent)"
  nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag cloud-latest --cloud --latest 2>&1 | tail -1 | tee -a run_13d.log
done
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(spent)"
# local arm
for s in openclaw hermes; do
  log "== fresh45 $s local latest $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag fresh-latest --latest 2>&1 | tail -1 | tee -a run_13d.log
done
log "realhome after $(./realhome_fp.sh $M/realhome-after3 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
