#!/bin/bash
# JOB 13f, cloud part. Started early (the owner 05:53Z "what are u waiting for") alongside 13d's local arm, WITHOUT
# MAC-HEAVY.lock: cloud calls go to OpenRouter through or_proxy.py and never touch the shared Ollama (disclosed: the
# local-arm wall times of 13d's rows during this window share the CPU with these light cloud runs).
#   - keep 13d's cloud-latest rows (sandbox changed mid-row: v2 -> v3 broken -> v3b) as runs/cloud-latest-mixed;
#   - OpenClaw-latest and Hermes-latest cloud under rival.sb v3b (tag cloud-latest); VOOL cloud under vool.sb (tag cloud-sb).
M=<work>/missions/bench-win-20261028/h2h-computer
cd $M || exit 1
log(){ echo "$*" | tee -a run_13f-cloud.log; }
spent(){ python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))"; }
log "start $(date -u +%FT%TZ) rival.sb sha $(shasum -a 256 rival.sb | cut -c1-16) v3b sha $(shasum -a 256 rival-v3b.sb | cut -c1-16) vool.sb sha $(shasum -a 256 vool.sb | cut -c1-16)"
[ -d runs/cloud-latest ] && mv runs/cloud-latest runs/cloud-latest-mixed && log "kept 13d cloud rows as runs/cloud-latest-mixed"
python3 -I or_proxy.py --port 18765 --cap 8.0 --ledger or_ledger.jsonl > or_proxy-13f.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
for s in openclaw hermes; do
  log "== cloud fresh45 $s latest v3b $(date -u +%FT%TZ) proxy pid $PROXY spent so far \$$(spent)"
  nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag cloud-latest --cloud --latest 2>&1 | tail -1 | tee -a run_13f-cloud.log
done
log "== cloud fresh45 voolcloud vool.sb $(date -u +%FT%TZ) spent so far \$$(spent)"
nice -n 10 python3 -I run_h2h.py --system voolcloud --set fresh --tag cloud-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f-cloud.log
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(spent)"
log "cloud part done $(date -u +%FT%TZ)"
