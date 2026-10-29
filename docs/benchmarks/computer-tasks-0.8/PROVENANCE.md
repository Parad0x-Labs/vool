# Provenance of the fresh-45 computer-task benchmark

Every claim below has a file, hash, timestamp or request id behind it in this mission folder.

## Timeline (UTC, 2026-10-28/29)
| when | what | proof |
|---|---|---|
| 21:36:25Z | VOOL code frozen: commit 39fc9527 ("clean loop fixes 14"), clean tree | `git log` of the fix14 repo; every VOOL run row records head 39fc9527, dirty=false |
| 22:05:30Z | The 45 tasks and answer keys written by an independent author model (claude-opus-5-5, fresh context) from AUTHOR-PACK.md | helper ledger row, request id req_011CgVPhNZufu42AsAgnDnFA; AUTHOR-PACK.md sha256 b2683c4c… |
| 22:07:19Z | Task file frozen and made read-only | fresh-asks.json sha256 59f36a6f…; FROZEN.txt |
| 23:23:25Z | First VOOL run on the frozen set | runs/fresh/vool-fresh/vool-run/run.json |

The VOOL code that produced every VOOL row was committed 31 minutes before the tasks existed. No VOOL code changed between the freeze and the runs. The fixes made after the jury (fix15-fix17, on the 0.7.x branch) are not in any measured row.

## What the task author saw
- The author pack: the test home folder's file list and file texts, plus 5 tone examples. No VOOL code, no VOOL replies, no scores, no rival output.
- The author's instructions are in fresh-set/SYSTEM.txt: write realistic requests and exact answer keys from the material only.
- validate.py checked each answer key against the files (VALIDATION.txt: 45 of 45 kept, none edited after the freeze).

## Same conditions for every system
- Verbatim tasks, the same 300 s cap per task, and the same scorer.
- No prompt tuning of the rivals. The rivals ran their own default settings; Hermes needs a 64K context.
- Local arm: one model (qwen3:8b). Cloud arm: one model (z-ai/glm-5.3-flashx via OpenRouter, temperature 0, the same forced reasoning setting).
- Each task ran in a fresh copy of the test home.
- Sandbox (round 1): no internet; rivals wrote only into their copy. Round-1 headline rows had reads of the real home and drives locked (rival.sb v3b; VOOL vool.sb v2, built from the same base). Both still allowed system-info commands and the keychain tool; the probe tables (PROBE-*.txt) show exactly what each profile allowed. Round 2 used the deny-by-default profiles (rival.sb v4, vool.sb v3).
- Grading: a blind jury (claude-opus-5-5, one reply at a time, system names and machine paths removed, answer key visible). On a sample of 10 items where the jury and the regex scorer disagreed, a second judge (claude-fable-5-1) agreed with the jury on 10/10 (round 1) and 9/10 (round 2).
- Rival versions: the September builds, then the newest builds (OpenClaw 2026.10.8, Hermes main c73b595). Both are reported.

## Known limits (stated with the results)
- The test home folder is synthetic. It is the same 31-file folder used in VOOL's clean-loop development audits (missions/audit-files-20261027, 58 earlier tasks). The 45 tasks are new and were never seen by VOOL's code or its builders, but VOOL's developers had worked with these files before; the rivals had not. A second set with a new, independently written home folder removes this.
- VOOL cloud row = VOOL's clean loop and its tools called directly (vool_cloud_bench.py); VOOL's routing and answer gates are not in that row.
- Old-version rival rows ran under sandbox v2 (reads open). One rival read real-machine folder names on a disk-space task. This is recorded in h2h-computer/LOG.md; the names are scrubbed from published files.

# Round 2: new home folder, new 45 tasks (removes the "VOOL had seen the room" limit)

| when (UTC 2026-10-29) | what | proof |
|---|---|---|
| 09:03:53Z | VOOL code freeze checked: fix14 39fc9527, clean tree (same build as round 1) | round2/FREEZE-VOOL.txt |
| 09:07:03Z | One fresh-context call to claude-opus-5-5 invents a new person, her home folder (39 files) and 45 tasks with answer keys. Inputs: SYSTEM.txt + BRIEF.md only; no VOOL code, replies, scores, round-1 set or old fixture | helper ledger row, request id req_011CgWG1MiPbvxSmLhEJmHZt; round2/AUTHOR-REPLY.json (raw) |
| 09:07:22Z | Home built from the spec by build_home.py (same file encoders as the old fixture) | round2/fakehome2/MANIFEST.json; manifest hash da4f47ab… |
| 09:07:33Z | Tasks validated (45/45 kept) and frozen | round2/round2-asks.json sha256 050247ec…; round2/FROZEN.txt; VALIDATION.txt |
| 09:08:25Z | First system run on round 2 | h2h-computer/run_r2_cloud.log |

Headline round-2 rows, each run under one sandbox profile: VOOL cloud (vool.sb v3 = rival v4 rules plus VOOL's own program files), OpenClaw 2026.10.8 and Hermes c73b595 cloud (rival.sb v4), all on z-ai/glm-5.3-flashx, graded by the same blind opus-5-5 jury with fable-5-1 tie-breaks (jury-r2/). The earlier mixed-profile rival rows (runs/r2-cloud) are kept as evidence only; the keychain findings (R05, R13) come from them.
