#!/bin/bash
# Hermes cloud rerun: attempt 1 (runs/cloud/hermes-fresh-attempt1-proxy400.jsonl) got HTTP 400 on every call because
# or_proxy.py added reasoning.effort on top of Hermes's own top-level reasoning_effort. The proxy now drops the top-level
# field (same forced settings as every other cloud row). Waits for run_13b2.sh to release the lock, then retakes it.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
cd $M || exit 1
until grep -q "^lock released" run_13b2.log 2>/dev/null; do sleep 10; done
if [ -s $L ]; then echo "lock taken by someone else after 13b2: $(cat $L)" | tee -a run_13b3.log; exit 3; fi
echo "bench-win-20261028 h2h 13b-3 hermes cloud rerun (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log(){ echo "$*" | tee -a run_13b3.log; }
python3 -I or_proxy.py --port 18765 --cap 6.0 --ledger or_ledger.jsonl > or_proxy-13b3.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
SP=$(python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))")
log "== cloud fresh45 hermes rerun $(date -u +%FT%TZ) proxy pid $PROXY spent so far \$$SP"
nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag cloud --cloud 2>&1 | tail -1 | tee -a run_13b3.log
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))")"
: > $L; log "lock released $(date -u +%FT%TZ)"
