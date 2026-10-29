#!/bin/bash
# JOB 13h: continues 13g after its two failures (VOOL local smoke under vool.sb v1 died on a denied stat of the Ollama
# model store path; BEAM v075 rehearsal stopped on the data pins). Both fixed. Holds MAC-HEAVY.lock throughout:
#   BEAM 128K (rehearsals, then VOOL, OpenClaw, Hermes) -> VOOL local fresh-45 under vool.sb v2 -> Hermes-latest local
#   fresh-45 (0 rows so far; resumable) -> real-home fingerprint -> release.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
B=<work>/missions/bench-win-20261028/beam128k
cd $M || exit 1
log(){ echo "$*" | tee -a run_13h.log; }
echo "bench-win-20261028 h2h 13h BEAM 128K + vool.sb local + hermes-latest local (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log "== BEAM 128K $(date -u +%FT%TZ)"
OWN_LOCK=0 $B/bin_beam128k.sh > /dev/null 2>&1; log "BEAM rc=$? $(date -u +%FT%TZ) ($(tail -1 $B/RUN.log))"
log "== fresh45 vool local vool.sb $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system vool --set fresh --tag fresh-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13h.log
log "== fresh45 hermes local latest $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag fresh-latest --latest 2>&1 | tail -1 | tee -a run_13h.log
log "realhome after $(./realhome_fp.sh $M/realhome-after3 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
