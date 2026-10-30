# Brief: make VOOL's local model use its tools (build session)

You are the builder. Repo: Parad0x-Labs/vool. Start from branch `delivery/tool-use-root-cause-20261029` (cb4852e5).
Push your work to a NEW branch `delivery/tool-use-root-cause-2-20261030`. No PR, no CI dispatch, never push to main
or to any other branch. Commit as `sls_0x <240776969+Parad0x-Labs@users.noreply.github.com>` with
`git -c commit.gpgsign=false`; no AI attribution lines; never the word "honest" (the repo scan bans it).

## The problem

When an ask needs this computer (files, notes, reminders, calendar, contacts, processes, settings, skills, PDFs,
pictures), the local model (qwen3:8b on Ollama) mostly answers without running any tool, and then guesses or makes up a
result. A blind 120-ask measurement on the base code ran the right tool on 17 of 70 asks that need one (bar: 56).
The 4 commits on cb4852e5 only moved half of those asks from 10/36 to 12/36. They are not enough.

What is known (verify it yourself, it may be wrong):
- Most turns end on the route `ordinary_plain_text_chat`, and the model is offered no tools at all.
- Two word gates decide whether the model ever sees tools: `should_keep_ai_first_chat_lane`
  (core/runtime_checkpoint_lane_policy.py) and `should_attempt_tool_intent` (core/execution/planner.py). The flags
  `always_on_tool_catalog` and `plain_text_tool_catalog` default to off.
- When qwen3:8b is handed a small tool family (about 8 tools, think off), it calls the right tool natively.
- cb4852e5 added a recognizer for "owner live-source" asks plus one retry. On fresh asks it rarely fired: most
  needs-tool turns still had 0 tools offered.
- Some reads happen through `machine_read_fast_path` and work; keep them.

## What to build

Make the model get a small, relevant tool set on every turn that could need this computer, and use it. Prefer
general mechanisms over word lists. Candidates to try and measure, in this order:
1. A compact always-on owner catalog (one read tool per family, under about 12 tools, with short descriptions) for
   owner turns on the local model, instead of keyword gates deciding.
2. Family selection by the model itself in a cheap first step, then the full family seated.
3. The one retry from cb4852e5 when the model answers in prose on a needs-tool turn.

Never: weaken an approval gate, route anything around the wallet or its caps, run a write or send without the
existing approval, or let a tool run on a pure knowledge ask ("how long to boil an egg"). Never skip or weaken tests.

## How to measure while building

- Write your OWN fresh dev set of at least 80 asks first, before you change code: about 50 that need a tool, 15 about
  mail or calendar with nothing connected, and 15 knowledge asks that need no tool. Write them like real people
  (typos, follow-ups, about 15% non-English). Save it on your branch under `audit-kits/tool-use/dev/`.
- Do not try to read `audit-kits/tool-use/asks-v1.jsonl.enc`. It is the hidden bar, and you have no key.
- Use the kit to run real turns: `audit-kits/tool-use/harness/audit_ask.py` with the fake home in
  `audit-kits/tool-use/fakehome/` and a CPU Ollama with qwen3:8b. Read `audit-kits/tool-use/run_box.sh`, which shows
  every setup step, and reuse them. A turn takes about 1.5 to 3 minutes on 4 CPUs, so use small batches.
- Score with `audit-kits/tool-use/score.py`, then read the replies yourself for false claims.
- Target on your dev set: the right tool ran on 80% or more of the asks that need one, nothing connected is said
  plainly on 90% or more of the mail and calendar asks, and no tool ran on 90% or more of the knowledge asks.

## Rules

- Report every 15 minutes: append a few lines (UTC time, what changed, dev numbers, blockers) to
  `audit-kits/tool-use/dev/PROGRESS.md` on your branch and push.
- Run the related tests (`pytest -k "offer or seat or tool or operator or finaliz or route"` with the tree on
  PYTHONPATH) before each push. Compare reds with the base, and say which reds come from the environment (no OS
  sandbox, no playwright).
- Hard stop at 3 hours. Commit and push what works, and write `audit-kits/tool-use/dev/RESULT.md`: the root cause in
  3 lines, what changed, dev numbers before and after, tests, and what is left.
