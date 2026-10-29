#!/bin/bash
# JOB 13f, local part: after run_13d.sh releases MAC-HEAVY.lock, VOOL local under vool.sb (1-ask smoke, then the
# fresh 45, tag fresh-sb), then the real-home fingerprint, then release.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
cd $M || exit 1
until grep -q "^lock released" run_13d.log 2>/dev/null; do sleep 10; done
if [ -s $L ]; then echo "lock taken by someone else after 13d: $(cat $L)" | tee -a run_13f-local.log; exit 3; fi
echo "bench-win-20261028 h2h 13f vool local under vool.sb (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log(){ echo "$*" | tee -a run_13f-local.log; }
log "== smoke vool local vool.sb $(date -u +%FT%TZ) vool.sb sha $(shasum -a 256 vool.sb | cut -c1-16)"
nice -n 10 python3 -I run_h2h.py --system vool --set fresh --ids N01 --tag smoke-vool-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f-local.log
if grep -q "vool fresh: ok 1/1" run_13f-local.log; then
  log "== fresh45 vool local vool.sb $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system vool --set fresh --tag fresh-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13f-local.log
else
  log "vool local smoke under vool.sb did not pass; full run not started"
fi
log "realhome after $(./realhome_fp.sh $M/realhome-after4 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
