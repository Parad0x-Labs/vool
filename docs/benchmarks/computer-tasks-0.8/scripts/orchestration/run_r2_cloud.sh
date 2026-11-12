#!/bin/bash
# ROUND 2 cloud arm: new home (round2/fakehome2) + new 45 asks (round2/round2-asks.json, frozen 09:07:33Z), same headline
# conditions as round 1: z-ai/glm-5.3-flashx via or_proxy.py (temperature 0, cap $8 total for the night), newest rival
# builds under rival.sb v3b, VOOL cloud adapter under vool.sb v2, 300 s cap, verbatim asks. Cloud calls never touch the
# shared Ollama, so this runs beside BEAM without MAC-HEAVY.lock (as 13f did; disclosed).
M=<work>/missions/bench-win-20261028/h2h-computer
cd $M || exit 1
log(){ echo "$*" | tee -a run_r2_cloud.log; }
spent(){ python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))"; }
log "start $(date -u +%FT%TZ) r2 asks sha $(shasum -a 256 ../round2/round2-asks.json | cut -c1-64) rival.sb $(shasum -a 256 rival.sb | cut -c1-16) vool.sb $(shasum -a 256 vool.sb | cut -c1-16)"
python3 -I or_proxy.py --port 18765 --cap 8.0 --ledger or_ledger.jsonl > or_proxy-r2.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
for s in openclaw hermes; do
  log "== r2 cloud $s latest v3b $(date -u +%FT%TZ) spent so far \$$(spent)"
  nice -n 10 python3 -I run_h2h.py --system $s --set r2 --tag r2-cloud --cloud --latest 2>&1 | tail -1 | tee -a run_r2_cloud.log
done
log "== r2 cloud voolcloud vool.sb $(date -u +%FT%TZ) spent so far \$$(spent)"
nice -n 10 python3 -I run_h2h.py --system voolcloud --set r2 --tag r2-cloud --vool-sandbox 2>&1 | tail -1 | tee -a run_r2_cloud.log
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(spent)"
log "r2 cloud done $(date -u +%FT%TZ)"
