# AGENTS.md

Instructions for any automated agent or contributor working in this repository. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first: its *Definition of done*, *Test discipline* and
*CI evidence and failures* sections are binding and are not repeated here.

## Working rules

1. Keep going until the authorized work is complete; spending limits stay as set.
2. Verify claims (handovers, audits, READMEs, earlier agents) against source and raw evidence.
3. Confirm the execution environment before preparing it or running tests.
4. Identify the exact source: commit, working-tree changes, relevant untracked files,
   configuration and hashes. A commit hash alone does not identify a dirty candidate.
5. Work in the maintainer-designated workspace; no silent fallback to other storage.
6. Preserve existing work: original candidates, frozen controls, evidence and active workers.
   Repairs go in an isolated copy that carries the complete candidate bytes.
7. Fix the owning cause. Never suppress a symptom, weaken a protection or change an
   expectation only to get green.
8. Prove the actual user workflow, not only a helper.
9. Regression is cumulative: validate each step together with earlier affected behavior and
   finish with the combined end-to-end flow.
10. No tests for tests' sake: every check resolves a concrete uncertainty or protects
    required behavior.
11. Re-running a known failing question is regression evidence, not fresh acceptance.
12. Keep the complete truth: failed first attempts, errors, partial denominators and raw
    output. Missing telemetry is unknown; an untested baseline is not a baseline pass.
13. Compare fairly over the same valid denominator and expose every difference in setup.
14. Measure quality, tokens, latency and cost together when model behavior changes.
15. Make long runs recoverable and resumable without duplicate calls or spending.
16. Report progress plainly, including stalls.
17. Stay within authorization: publishing, deployment, external messages and process changes
    need the maintainer's explicit approval. Protect secrets and private profiles.
18. Report honestly; no unsupported success claims.

## Hard limits

Any agent may only work inside its own assigned task, its own isolated copy and the files it has
claimed. It fixes the owning cause and proves it on the real user workflow, failure paths
included. Any breach below means the delivery is rejected:

- Test counts are not proof. Thousands of passing tests are worthless if the next new question fails.
- No tests written to raise numbers; every test pins real behavior or a real failure.
- No happy-path-only work. The failure path is part of done; unknown states fail closed.
- No corner cutting: no stubs, mocks or skips standing in for real behavior, no weakened
  assertions, changed expectations, hardcoded evaluation answers, or TODOs passed off as done.
- Known or previously seen questions are regression checks, never acceptance.
- No edits outside the claimed files; no exceeding the spend or resource budget.
- No success claim without raw evidence: exact commit, command and output.
- Reports are short: what changed, evidence, pass/fail over the denominator, blockers.

## Roles

Work moves through three roles, and only the last one touches the remote:

- **Implementer**: builds the change and its tests in an isolated local candidate and hands
  over the exact head, evidence and limits. Never pushes.
- **Reviewer**: reviews, diagnoses and writes briefs. Never pushes, opens, merges or closes
  pull requests, and never deletes branches or unmerged work.
- **Publisher**: independently re-checks the exact final bytes, then performs the authorized
  git work (commit, push, pull request). The only role that writes to the remote.

## Branches

- `main` is protected. Never push to it directly.
- `delivery/<topic>-<yyyymmdd>`: branches pushed for review (pull request heads).
- `repair/<job>`: local candidates, one per job (job names are unique and dated); pushed only when they become a
  pull request head or replace one by fast-forward.
- Do not create other long-lived prefixes. Do not rewrite history on a branch someone else
  pushed: merge the base branch in instead of rebasing or force-pushing.

## Commit identity

Every commit is authored and committed as:

    git config user.name  "sls_0x"
    git config user.email "240776969+Parad0x-Labs@users.noreply.github.com"

Do not use `sls_0x@local` or any machine-local address (those commits are not linked to the
GitHub account). Do not add tool, model or assistant attribution to commits, pull requests,
release notes or source. Do not bypass identity or pre-push hooks. A publisher refuses to push
a range containing any other identity.

## Parallel work

Many implementers may run at the same time. To keep them from colliding:

- **One mission, one job name.** Each job gets its own mission folder, its own isolated
  candidate checkout, its own scratch/cache/profile directories and its own branch
  `repair/<job>` (the job name already carries the date). Never work in another job's checkout.
- **Declare owned files before editing.** Each mission lists the paths it may change. Two active
  missions never own the same file. If a change needs a file another active mission owns, stop
  and report it; do not edit it. Shared hot spots (`tests/conftest.py`, `pyproject.toml`,
  `uv.lock`, workflow files) are owned by at most one active mission at a time.
- **Base on a recorded commit.** Record the base commit at start. Integrate newer `main` by a
  merge commit before handover, then re-run the affected scope.
- **Leave a status file.** Each job keeps a machine-readable status (state, base and head commit,
  owned files, checks run with pass/fail counts, blockers) and updates it at each step, so a
  reviewer can check progress without reading logs.
- **Hand over, don't publish.** Implementers stop at a clean, committed local head plus evidence.
  Only the publisher pushes, and it publishes one mission per pull request.

## Fast local checks

Use Python 3.12.13 and the exact tool pins in `pyproject.toml` (pytest, ruff 0.16.9).

    python -m pip install -e ".[dev,browser,companion,evm]"   # same extras CI installs
    python -m ruff check .                                    # pinned ruff, whole repo
    python ops/pytest_manifest.py --repo-root . --output .verification-logs/pytest-manifest.json -- -q
                                                              # canonical collection, as CI runs it
    python3.12 ops/verify.py --workers 4 --tail-lines=200     # the lint/collection/stable-shard gate

To find which CI shard holds a file, run `ops/shard_resolver.py` the way `ci.yml` does
(`--shard N --shards 10 --files <collected list> --weights ops/shard_weights.json`).

Then run the affected test files plus the previously working affected scope (rule 9). Run a
full shard or the whole suite only when a cross-cutting dependency or a required gate calls
for it. CI (verify, ten test shards, macOS, build, CodeQL) remains the merge gate; a local pass
never replaces it, and an old head's green never covers a new head.

## Evidence

Every pull request names the exact tested commit, the commands run, pass/fail denominators,
what user workflow was proven and the remaining limits. Keep raw logs, failed attempts and
baselines in the maintainer's workspace; do not commit machine-local paths, private notes or
generated runtime data.
