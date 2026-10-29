#!/bin/bash
# JOB 13g: new order (coordinator 06:22Z on the owner's delegation, "BEAM first"). Replaces the tail of run_13d.sh (its
# bash was stopped after OpenClaw-latest local started; the running OpenClaw child, pid given as $1, finishes on its own),
# run_13f-local.sh and the lock wait of bin_beam128k.sh. Holds MAC-HEAVY.lock throughout:
#   wait for OpenClaw-latest local to finish -> VOOL local fresh-45 under vool.sb -> BEAM 128K (VOOL, OpenClaw, Hermes)
#   -> Hermes-latest local fresh-45 -> real-home fingerprint -> release.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
B=<work>/missions/bench-win-20261028/beam128k
OC_PID=${1:?openclaw-latest local child pid}
cd $M || exit 1
log(){ echo "$*" | tee -a run_13g.log; }
log "start $(date -u +%FT%TZ) waiting for OpenClaw-latest local pid $OC_PID"
while kill -0 $OC_PID 2>/dev/null; do sleep 15; done
sleep 2
log "openclaw local latest done $(date -u +%FT%TZ): $(grep '^openclaw fresh:' run_13d.log | tail -1)"
echo "bench-win-20261028 h2h 13g vool.sb local + BEAM 128K + hermes-latest local (Claude, remote session) since $(date -u +%FT%TZ)" > $L
log "== smoke vool local vool.sb $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system vool --set fresh --ids N01 --tag smoke-vool-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13g.log
if grep -q "vool fresh: ok 1/1" run_13g.log; then
  log "== fresh45 vool local vool.sb $(date -u +%FT%TZ)"
  nice -n 10 python3 -I run_h2h.py --system vool --set fresh --tag fresh-sb --vool-sandbox 2>&1 | tail -1 | tee -a run_13g.log
else
  log "vool local smoke under vool.sb did not pass; full run not started"
fi
log "== BEAM 128K $(date -u +%FT%TZ)"
OWN_LOCK=0 $B/bin_beam128k.sh > /dev/null 2>&1; log "BEAM rc=$? $(date -u +%FT%TZ) ($(tail -1 $B/RUN.log))"
log "== fresh45 hermes local latest $(date -u +%FT%TZ)"
nice -n 10 python3 -I run_h2h.py --system hermes --set fresh --tag fresh-latest --latest 2>&1 | tail -1 | tee -a run_13g.log
log "realhome after $(./realhome_fp.sh $M/realhome-after3 | tr '\n' ' ')"
: > $L; log "lock released $(date -u +%FT%TZ)"
