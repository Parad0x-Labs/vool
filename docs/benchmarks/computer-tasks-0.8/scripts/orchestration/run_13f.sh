#!/bin/bash
# JOB 13f: every headline row under one read-confined sandbox (rival.sb v3b; VOOL under vool.sb = same rules + its own
# program files). After run_13d.sh releases the lock:
#   - keep 13d's cloud-latest rows (sandbox changed mid-row: v2 -> v3 (broken) -> v3b) as runs/cloud-latest-mixed;
#   - rerun OpenClaw-latest and Hermes-latest cloud under v3b (tag cloud-latest), then VOOL cloud under vool.sb;
#   - VOOL local: 1-ask smoke under vool.sb, then the fresh 45 (tag fresh-sb); real-home fingerprint; release.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
cd $M || exit 1
until grep -q "^lock released" run_13d.log 2>/dev/null; do sleep 10; done
if [ -s $L ]; then echo "lock taken by someone else after 13d: $(cat $L)" | tee -a run_13f.log; exit 3; fi
echo "bench-win-20261028 h2h 13f one-sandbox reruns (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log(){ echo "$*" | tee -a run_13f.log; }
spent(){ python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))"; }
log "start $(date -u +%FT%TZ) rival.sb sha $(shasum -a 256 rival.sb | cut -c1-16) v3b sha $(shasum -a 256 rival-v3b.sb | cut -c1-16) vool.sb sha $(shasum -a 256 vool.sb | cut -c1-16)"
[ -d runs/cloud-latest ] && mv runs/cloud-latest runs/cloud-latest-mixed && log "kept 13d cloud rows as runs/cloud-latest-mixed"
python3 -I or_proxy.py --port 18765 --cap 8.0 --ledger or_ledger.jsonl > or_proxy-13f.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
for s in openclaw hermes; do
  log "== cloud fresh45 $s latest v3b $(date -u +%FT%TZ) proxy pid $PROXY spent so far \$$(spent)"
  nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag cloud-latest --cloud --latest 2>&1 | tail -1 | tee -a run_13f.log
done
log "== cloud fresh45 voolcloud vool.sb $(date -u +%FT%TZ) spent so far \$$(spent)"
nice -n 10 python3 -I run_h2h.py --system voolcloud --set fresh --tag cloud-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f.log
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(spent)"
log "== smoke vool local vool.sb $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system vool --set fresh --ids N01 --tag smoke-vool-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f.log
if grep -q "vool fresh: ok 1/1" run_13f.log; then
  log "== fresh45 vool local vool.sb $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system vool --set fresh --tag fresh-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f.log
else
  log "vool local smoke under vool.sb did not pass; full run not started"
fi
log "realhome after $(./realhome_fp.sh $M/realhome-after4 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
