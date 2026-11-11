#!/bin/bash
# JOB 13i: keep "BEAM first". run_13h.sh's BEAM step failed at the OpenClaw rehearsal (leftover GLM reader ids; fixed),
# so 13h went on to VOOL local (vool.sb v2), which should finish, and would then start Hermes-latest local. This script
# waits for 13h's Hermes step to begin, stops 13h's bash and that just-started Hermes run (both mine, by PID; the Hermes
# run is resumable and has written no rows), then holds MAC-HEAVY.lock for: BEAM 128K -> Hermes-latest local ->
# real-home fingerprint -> release.  usage: run_13i.sh <13h bash pid>
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
B=<work>/missions/bench-win-20261028/beam128k
P13H=${1:?13h bash pid}
cd $M || exit 1
log(){ echo "$*" | tee -a run_13i.log; }
log "start $(date -u +%FT%TZ) waiting for 13h (pid $P13H) to reach its Hermes step"
until grep -q "^== fresh45 hermes local latest" run_13h.log 2>/dev/null; do
  kill -0 $P13H 2>/dev/null || { log "13h ended before its Hermes step"; break; }
  sleep 2
done
kill -0 $P13H 2>/dev/null && {
  CH=$(ps -axo pid=,ppid= | awk -v p=$P13H '$2==p{print $1}')
  kill -TERM $P13H 2>/dev/null
  for c in $CH; do
    for g in $(ps -axo pid=,ppid= | awk -v p=$c '$2==p{print $1}'); do kill -TERM $g 2>/dev/null; done
    kill -TERM $c 2>/dev/null
  done
  sleep 3
  log "stopped 13h bash $P13H and its Hermes children ($CH) $(date -u +%FT%TZ); hermes rows: $(wc -l < runs/fresh-latest/hermes-fresh.jsonl 2>/dev/null || echo 0)"
}
echo "bench-win-20261028 h2h 13i BEAM 128K + hermes-latest local (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log "== BEAM 128K $(date -u +%FT%TZ)"
OWN_LOCK=0 $B/bin_beam128k.sh > /dev/null 2>&1; log "BEAM rc=$? $(date -u +%FT%TZ) ($(tail -1 $B/RUN.log))"
log "== fresh45 hermes local latest $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag fresh-latest --latest 2>&1 | tail -1 | tee -a run_13i.log
log "realhome after $(./realhome_fp.sh $M/realhome-after3 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
