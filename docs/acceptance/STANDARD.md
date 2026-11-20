# VOOL acceptance standard

This is what "done" means for any change a person can see or use in VOOL. It applies to every
fix, every feature and every release, whoever does the work. It sits on top of the *Definition of
done* in [CONTRIBUTING.md](../../CONTRIBUTING.md); where the two differ, the stricter one wins.

## 1. Done means proven on the real app

A change is done only when it works in the **real running VOOL app, through the chat page a person
uses**: typed into the composer, sent with Send, read from the answer on screen. A green unit test,
a helper that returns the right value, a scripted model or a direct API call is not acceptance.
Built but not connected to the app is not done.

Each graded answer is judged on three things, and must pass all three:

| Axis | Passes when |
| --- | --- |
| **Right** | The answer is correct for this person's data: the right value, the right item, the right action taken. Nothing invented. When the thing truly does not exist, it says so. |
| **Helpful** | It answers what was asked without making the person work for it. Not found means it says where it looked and offers the next step (for example, searching other chats or projects). No needless "did you mean…?" when the question is clear. No answer that takes longer than the scenario's time limit. |
| **Plain** | Ordinary words a non-technical person understands. No developer text: no "provided context", no tool or function names, no JSON, error codes, stack traces, file paths or internal ids. |

A short, correct, plain answer passes. A correct answer in developer words fails.

## 2. What a check must cover

One phrasing of the happy path proves almost nothing. Every change is checked with scenarios from
the [scenario library](scenarios/) for each lane it touches, and every scenario covers:

- **Varied phrasings**: at least three ways a person would actually ask, including typos, lower
  case, missing words and short follow-ups ("and where from?").
- **Follow-ups** that depend on the previous answer.
- **Not found**: something that does not exist gets a plain "I couldn't find it" plus where VOOL
  looked and what it can do next. Never "not in the provided context".
- **After forget**: once a person asks VOOL to forget something, it is gone everywhere, and the
  answer says so plainly.
- **Across chats and projects**: what was said or saved in another chat or project is found when
  the person asks for it, and VOOL says where it found it.
- **Edge and failure paths**: stopped or interrupted replies, missing accounts or settings,
  empty results, slow answers. The failure path is part of done.

Fresh values every run. Codes, names and dates in a scenario are generated per run (see
`vars` in the library), so no answer can be learned or hard-coded. A scenario that was used to
find or fix a bug is regression evidence; acceptance of the fix also needs at least one phrasing
nobody has run before. Add it to the library in the same change.

## 3. The runner

[`ops/acceptance/run_acceptance.py`](../../ops/acceptance/run_acceptance.py) drives the library
against a running VOOL through the real chat page (headless Chromium, the same page the app
shows). It types each message into the composer, presses Send, handles approval cards the way the
scenario says, presses Stop when the scenario says, reloads the page, and grades the answer on
screen. Setup that is not part of what is being graded (making a throwaway project folder) may use
the app's own API; every graded turn goes through the chat page.

It writes, per run, into the output folder:

- `table.md`: the graded pass table, per lane and per case, with denominators.
- `raw.jsonl`: every turn as sent and as answered, elapsed time, each axis's verdict and reason.
- `source.json`: the app's build id from `/healthz`, the base URL, profile kind, the runner's git
  commit and the SHA-256 of every scenario file used.
- `shots/`: a screenshot after every graded turn.

Results are never edited by hand. A **REVIEW** cell (a turn whose helpfulness cannot be graded by
pattern) is decided by a reviewer, who records the verdict and a one-line reason in `review.json`;
`--apply-review` regrades the table from it. Until then it counts as not passed.

After each case the runner deletes the chats that case made (and with them what VOOL learned in
them), on either profile, so one case cannot answer the next. It never touches any other chat. A
failed clean-up is written into the case's row.

The runner stops itself early when the setup is wrong: if the first three answers are empty,
errors or "not ready", or if more than a third of any ten answers are, it stops and says so instead
of grading a broken run for an hour.

### Where it runs

- **Throwaway profile (default for fixes)**: a separate VOOL home on its own port, started by the
  runner from the app code under test (`--launch`). The person's own chats and memory are never
  touched. Any scenario may run here.
- **Installed app on the person's real profile** (`--real-profile`): only scenarios marked
  `profile: any` run. Each uses fresh made-up values. Nothing is sent outside the machine: email
  sends, posts and payments are always denied on the real profile.

Scenarios that need something the profile does not have (a connected test mailbox, a chosen
calendar, being online) are reported **BLOCKED**, never PASS. The person running the suite declares
what the profile has with `--have`.

## 4. When it runs

| When | What runs | Gate |
| --- | --- | --- |
| Before calling any fix or feature done | Every scenario in each lane the change touches, plus the regression scenarios for the bug it fixes | All PASS. Any FAIL is fixed and the lane is run again; the earlier failing table is kept. |
| After each fix in a series | The lanes touched so far, together (regression is cumulative) | All PASS |
| Before any release | The **full library** on the release build, on a throwaway profile, on each platform in the release (macOS arm64, macOS Intel, Linux), alongside the in-app update test from the previous release (the update gate in [UPDATE_PROTOCOL.md](../releases/UPDATE_PROTOCOL.md)) | Zero FAIL, zero REVIEW left open. BLOCKED only for a lane the release notes say is not in this release. No publish without both tables. |
| After a person finds a bug by hand | Add the bug's scenario (with new phrasings) to the library in the same change as the fix | The new scenario fails on the old build and passes on the fix |

A run's table is the evidence. A report of "done" links the table, states the build id it ran on
and gives pass/total per lane. FAIL rows stay in the report; they are not dropped from the
denominator.

## 5. Adding a scenario

Scenarios live in [`docs/acceptance/scenarios/`](scenarios/), one YAML file per lane. The format is
described in [scenarios/README.md](scenarios/README.md). Every bug a person finds by hand becomes a
scenario with `from_bug` saying what they saw, and with phrasings beyond the one they typed.
