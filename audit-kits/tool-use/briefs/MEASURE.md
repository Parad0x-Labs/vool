# Brief: measure tool use on the hidden 120 asks (measurement session)

You are the measurer. You build nothing and change no VOOL code. Repo: Parad0x-Labs/vool, kit branch
`audit/tool-use-kit-20261029`, folder `audit-kits/tool-use/`. Read its README.md first. It has the steps, the
judging rule and the pass bar.

## Run

1. Clone the kit branch and run `run_box.sh` with label `fix2`, the git ref from your prompt, and shard `0 1` (all
   120 asks on this box). Start it with nohup and check on it every few minutes. It takes about 4 to 6 hours on 4
   CPUs. The passphrase is in your prompt only: never write it to a file, a commit or a message.
2. If setup fails (disk, pip, download), fix the environment only, retry once, and say what you did.

## Judge

- Decrypt the raw rows in /tmp only, never inside the repo. Read every reply.
- Mark each case pass or fail by README "Judging claims" and "Pass bar".
- Compare with the base numbers on the same asks: the right tool ran on 17/70 needs_tool, 25/25 no_tool had no
  tool, and missing_connection was about 7/25 by a rough check.

## Report

- Commit `runs/fix2/s0/score.json`, `runs/fix2/s0/raw.tgz.enc` and a `runs/fix2/JUDGED.md` to the kit branch and push.
- JUDGED.md holds: totals per kind against the bar, base against fix2 on the same denominator, timeouts, median
  seconds per turn, and the case ids of every fail with one line each. Write no ask text in the clear.
- Every 15 minutes, append a short progress line to `runs/fix2/PROGRESS.md` and push.
- Never use the word "honest".
