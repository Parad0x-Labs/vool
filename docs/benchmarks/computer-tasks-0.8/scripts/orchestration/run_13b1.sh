#!/bin/bash
# JOB 13b-1: smoke 5 asks x 3 systems (sanity), then fresh 45: VOOL (fix14) then OpenClaw. Holds MAC-HEAVY.lock.
M=<work>/missions/bench-win-20261028/h2h-computer; L=<work>/missions/MAC-HEAVY.lock
cd $M || exit 1
[ -s $L ] && { echo "lock busy: $(cat $L)"; exit 3; }
echo "bench-win-20261028 h2h 13b-1 smoke + fresh45 vool,openclaw (Claude, remote session) since $(date -u +%FT%TZ)" > $L
echo "fresh-asks sha256 $(shasum -a 256 ../fresh-set/fresh-asks.json | cut -c1-64)" | tee -a run_13b1.log
echo "realhome before $(./realhome_fp.sh $M/realhome-before | tr '\n' ' ')" | tee -a run_13b1.log
echo "free RAM $(vm_stat | awk '/Pages free|Pages inactive|Pages speculative/ {gsub("\\.","",$NF); s+=$NF} END {printf "%.1f", s*16384/1e9}') GB; VOOL.app/llama-server: $(pgrep -fl 'VOOL.app|llama-server' | wc -l | tr -d ' ')" | tee -a run_13b1.log
for s in vool openclaw hermes; do echo "== smoke $s $(date -u +%FT%TZ)" | tee -a run_13b1.log; nice -n 10 python3 -I run_h2h.py --system $s --set fset --ids F06,F14,F17,F24 --tag smoke 2>&1 | tail -3 | tee -a run_13b1.log; nice -n 10 python3 -I run_h2h.py --system $s --set hset --ids H01 --tag smoke 2>&1 | tail -2 | tee -a run_13b1.log; done
for s in vool openclaw; do echo "== fresh45 $s $(date -u +%FT%TZ)" | tee -a run_13b1.log; nice -n 10 python3 -I run_h2h.py --system $s --set fresh --tag fresh 2>&1 | tail -2 | tee -a run_13b1.log; done
echo "realhome after $(./realhome_fp.sh $M/realhome-after | tr '\n' ' ')" | tee -a run_13b1.log
: > $L; echo "lock released $(date -u +%FT%TZ)" | tee -a run_13b1.log
