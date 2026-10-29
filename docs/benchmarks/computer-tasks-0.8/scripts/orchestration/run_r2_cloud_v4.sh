#!/bin/bash
# ROUND 2 cloud: one profile per row (coordinator 10:00Z). After run_r2_cloud.sh ends (its OpenClaw row ran under v3b,
# its Hermes row under v3b/v3c/v4, kept as disclosed evidence in runs/r2-cloud), rerun both newest rivals fully under
# rival.sb v4 into runs/r2-cloud-v4. VOOL's round-2 cloud row (runs/r2-cloud) ran under vool.sb v3 (= v4 rules + its own
# program files). Same proxy, model, cap and asks. No Ollama, so no lock.
M=<work>/missions/bench-win-20261028/h2h-computer
cd $M || exit 1
log(){ echo "$*" | tee -a run_r2_cloud_v4.log; }
spent(){ python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))"; }
until grep -q "^r2 cloud done" run_r2_cloud.log 2>/dev/null; do sleep 15; done
log "start $(date -u +%FT%TZ) rival.sb sha $(shasum -a 256 rival.sb | cut -c1-16) (v4 $(shasum -a 256 rival-v4.sb | cut -c1-16))"
python3 -I or_proxy.py --port 18765 --cap 8.0 --ledger or_ledger.jsonl > or_proxy-r2v4.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
for s in openclaw hermes; do
  log "== r2 cloud $s latest v4 $(date -u +%FT%TZ) spent so far \$$(spent)"
  nice -n 10 python3 -I run_h2h.py --system $s --set r2 --tag r2-cloud-v4 --cloud --latest 2>&1 | tail -1 | tee -a run_r2_cloud_v4.log
done
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(spent)"
log "r2 cloud v4 done $(date -u +%FT%TZ)"
