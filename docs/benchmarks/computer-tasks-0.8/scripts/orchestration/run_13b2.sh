#!/bin/bash
# JOB 13b-2 + cloud arm: after run_13b1.sh releases the lock, retake it at once and run
#   Hermes fresh45 (local) -> cloud arm (OpenClaw, Hermes, VOOL-cloud bench adapter via or_proxy.py, $6 cap) -> VOOL fresh45 rerun.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
cd $M || exit 1
until grep -q "^lock released" run_13b1.log 2>/dev/null; do sleep 10; done
if [ -s $L ]; then echo "lock taken by someone else after 13b1: $(cat $L)" | tee -a run_13b2.log; exit 3; fi
echo "bench-win-20261028 h2h 13b-2 hermes local + cloud arm + vool rerun (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log(){ echo "$*" | tee -a run_13b2.log; }
log "start $(date -u +%FT%TZ) fresh sha $(shasum -a 256 ../fresh-set/fresh-asks.json | cut -c1-64) free RAM $(vm_stat | awk '/Pages free|Pages inactive|Pages speculative/ {gsub("\\.","",$NF); s+=$NF} END {printf "%.1f", s*16384/1e9}')GB"
log "== fresh45 hermes local $(date -u +%FT%TZ)"; nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag fresh 2>&1 | tail -1 | tee -a run_13b2.log
# cloud arm
python3 -I or_proxy.py --port 18765 --cap 6.0 --ledger or_ledger.jsonl > or_proxy.log 2>&1 &
PROXY=$!; echo $PROXY > or_proxy.pid; sleep 2
log "proxy pid $PROXY $(date -u +%FT%TZ)"
for s in openclaw hermes voolcloud; do
  SP=$(python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))" 2>/dev/null || echo 0)
  log "== cloud fresh45 $s $(date -u +%FT%TZ) spent so far \$$SP"
  if [ "$s" = voolcloud ] && python3 -I -c "import sys;sys.exit(0 if $SP < 5.0 else 1)"; then :; elif [ "$s" = voolcloud ]; then log "voolcloud skipped: spend \$$SP near cap"; continue; fi
  if [ "$s" = voolcloud ]; then nice -n 10 python3 -I run_h2h.py --system voolcloud --set fresh --tag cloud 2>&1 | tail -1 | tee -a run_13b2.log
  else nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag cloud --cloud 2>&1 | tail -1 | tee -a run_13b2.log; fi
done
kill $PROXY 2>/dev/null; log "proxy stopped $(date -u +%FT%TZ) total spend \$$(python3 -I -c "import json;print(round(sum(float(json.loads(l).get('cost') or 0) for l in open('or_ledger.jsonl')),4))")"
log "== fresh45 vool rerun $(date -u +%FT%TZ)"; nice -n 10 python3 -I run_h2h.py --system vool --set fresh --tag fresh-rerun 2>&1 | tail -1 | tee -a run_13b2.log
log "realhome after $(./realhome_fp.sh $M/realhome-after2 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
