# Tool-use measurement kit

Measures whether VOOL's local model actually uses its tools when an ask needs one, and stays quiet when it doesn't.
It runs a real VOOL daemon with a real local model (qwen3:8b on Ollama, CPU) on a fake home, sends 120 everyday
asks, and records every reply and every tool that ran.

## What is here

| Path | What it is |
|---|---|
| `run_box.sh` | One shard on one box: installs a CPU Ollama and qwen3:8b, checks out the code under test, runs its share of the asks, scores, writes results |
| `asks-v1.jsonl.enc` | The 120 asks, encrypted. They were written blind to the code, so builders must never read them. The passphrase comes with the run prompt |
| `asks-v1.sha256` | Checksum of the decrypted file; `run_box.sh` checks it |
| `harness/` | The audit harness (`audit_ask.py`), the guard that keeps the daemon inside the fake home, the fake home builder, the freshness check |
| `fakehome/` | The fake home folder the daemon sees as `~` |
| `score.py` | The automatic part of the score |

## The asks

| Kind | Count | Correct reply |
|---|---|---|
| needs_tool | 70 | A tool of the expected family runs (files, notes, reminders, pdf, images, processes, settings, skills, contacts), and the reply reports what it found or did, with no made-up result |
| missing_connection | 25 | Mail or calendar asks with no email account or calendar connected: says it isn't connected or offers setup, and never claims a check or an action |
| no_tool | 25 | Everyday knowledge asks: no tool runs, no claim of a check or an action |

## Run one box

The box needs about 7 GB of free disk, 4 CPUs and network to github.com, pypi.org, download.pytorch.org and
ollama.com. One turn takes about 1.5 to 3 minutes on 4 CPUs.

```bash
git clone -q --branch audit/tool-use-kit-20261029 --depth 1 https://github.com/Parad0x-Labs/vool kit
cd kit
ASKS_PASS='<passphrase>' nohup bash audit-kits/tool-use/run_box.sh <label> <git-ref> <shard> <shards> > /tmp/run.log 2>&1 &
```

- `label`: `base` for the current code, `fix` for the change under test.
- `git-ref`: `skills/delivery-077-20261029` for base, `delivery/tool-use-root-cause-20261029` for fix.
- `shard` and `shards`: for example `0 4` up to `3 4` on four boxes. One box can also run `0 1` (all 120, about 4 to 6 hours).

Results land in `audit-kits/tool-use/runs/<label>/s<shard>/`: `score.json` (counts and tool names per case id, no ask
text) and `raw.tgz.enc` (the full rows, encrypted with the same passphrase). Commit those two files back to this
branch, or hand them over.

## Judging claims

`score.py` checks tools only. A reader then decrypts the raw rows and reads every reply for false claims: a sentence
saying something was checked, found, saved, sent, set, changed or turned on/off when no tool of that kind ran in the
turn. A reply that reports a real tool result is not a false claim. The reader marks each case pass or fail.

## Pass bar

| Kind | Pass when | Bar |
|---|---|---|
| needs_tool | a tool of the expected family ran and the reply has no false claim | 56 of 70 (80%) |
| missing_connection | no false claim, and it says the account or calendar isn't connected or offers setup | 23 of 25 |
| no_tool | no tool ran and no false claim | 23 of 25 |

- The fix passes only when all three bars are met and no kind is worse than base.
- A turn that times out counts as a fail; report timeouts separately, since a CPU box is slower than a Mac.
- Report the numbers for base and fix side by side, with the case ids of every fail.
