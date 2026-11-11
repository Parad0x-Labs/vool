#!/bin/bash
# JOB 13j (replaces 13i after the BEAM attempt-1 failures): holds MAC-HEAVY.lock for
#   BEAM 128K attempt 2 (bin_beam128k_v2.sh: rehearsal, 5-question real check, full 400 with a sanity watch; v075, openclaw,
#   hermes) -> Hermes-newest local fresh-45 (round 1, rival.sb v4) -> round-2 local arm (VOOL vool.sb v3, OpenClaw and
#   Hermes rival.sb v4) -> real-home fingerprint -> release.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
B=<work>/missions/bench-win-20261028/beam128k
cd $M || exit 1
log(){ echo "$*" | tee -a run_13j.log; }
echo "bench-win-20261028 13j BEAM attempt 2 + hermes-newest local + round-2 local (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log "start $(date -u +%FT%TZ) rival.sb $(shasum -a 256 rival.sb | cut -c1-16) vool.sb $(shasum -a 256 vool.sb | cut -c1-16)"
log "== BEAM attempt 2 $(date -u +%FT%TZ)"
$B/bin_beam128k_v2.sh > /dev/null 2>&1; log "BEAM v2 rc=$? $(date -u +%FT%TZ) ($(tail -1 $B/RUN2.log))"
log "== fresh45 hermes local latest (v4) $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag fresh-latest --latest 2>&1 | tail -1 | tee -a run_13j.log
log "== r2 local vool (vool.sb v3) $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system vool --set r2 --tag r2-local --vool-sandbox 2>&1 | tail -1 | tee -a run_13j.log
for s in openclaw hermes; do
  log "== r2 local $s latest (v4) $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system $s --set r2 --tag r2-local --latest 2>&1 | tail -1 | tee -a run_13j.log
done
log "realhome after $(./realhome_fp.sh $M/realhome-after4 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
