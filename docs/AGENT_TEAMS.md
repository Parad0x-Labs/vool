# Agent teams

VOOL can start a team of agents for a chat and coordinate them. The coordinator is plain code: it
uses no model and spends no tokens. Code lives in `core/agent_team/`.

## What a team does

- **One brief per agent.** Each agent has an objective, an importance (`low`, `normal`, `high`,
  `critical`), the paths it may write (its claim), how it runs (a command, or a model turn on a
  named model) and hard limits.
- **Names say the task and its importance.** An agent is shown as `Login redirect fix · high`.
  Generic names (`agent-2`, `worker`, `B`) are refused. Two agents with the same title get their
  claimed area added: `Docs update (api/) · low`.
- **One writer per file.** Overlapping claims are refused before anything starts, unless one agent
  is the other's sub-agent and its claim sits inside the parent's.
- **Hard limits per agent and per team** on dollars, tokens, calls and running time. Every model
  call is reserved before it is made and refused if it could cross a limit. Paused time does not
  count. A limit hit while running ends the agent with a result labelled partial.
- **Parallel model agents, each on its own model.** The model travels on the request; a cloud model
  is made resolvable for that agent's chat only. Ordinary chats keep choosing exactly as before.
- **Checked, short results.** Each report is checked against what the agent did (files changed in
  its claim, exit code, tool receipts). What goes back to the chat is capped at 400 tokens; the full
  text stays on disk and in the agent's own chat.
- **One question.** Agents that need a decision are batched into one question for the user.
- **Restart recovery.** The registry is SQLite in the team's folder. After a restart the
  coordinator re-adopts each agent only if its process is provably the same (pid and create time),
  reports the rest lost, and never relaunches anything.

## Overlap alerts

The coordinator watches every process an agent starts: children, grandchildren and daemons that
double-forked away (they inherit the agent's run token). If two agents, or any descendant of
theirs, touch the same files, both are frozen at once, then the recommended one resumes and the
user is told why:

> Two agents touched the same files: `src/auth/session.py`.
> - Session store refactor · normal's grandchild process (`pytest -x tests/auth`) had it open: src/auth/session.py
> Paused both, then resumed **Login redirect fix · high** (recommended: Session store refactor reached into files owned by Login redirect fix, which holds src/auth/; 3 of 4 steps done).
> **Session store refactor · normal** stays paused until you decide.
> Options: keep it paused until Login redirect fix finishes (recommended) / swap, run Session store refactor instead / stop Session store refactor.

Which agent keeps running: the one whose files were reached into; else the more important; else
the one further along; else the one that started first.

Detection layers: VOOL's own file tools ask a write gate first; each tick lists every lineage
member's open files and working directory; a change scan diffs every claimed path plus
`.git/hooks` and `.git/config`. A change nobody can be tied to is reported as unattributed, with
any orphaned process seen in that folder named (and left alone).

## Never-do for every agent

No git push (push URLs are rewritten to a transport that does not exist), no owner keys in the
environment, no agents past the depth ceiling (default 2, hard 3), no writes outside the claim, no
writes to git hooks or git config. The user's own constraints ("never push", "no web") are carried
into every model agent's brief verbatim.

## Kill only what it started

The coordinator signals only processes in a registered lineage, re-verified by pid and create time
at the moment of signalling. It never signals its own process or any ancestor, and never anything by
name.

## Commands

`agents.start`, `agents.status`, `agents.stop`, `agents.decide`, `agents.answer`. Only
`agents.status` is offered to the model; starting, stopping and deciding are the operator's.

## Known limits

- On macOS the kernel does not report a file's open mode, so an open file plus a change is treated
  as an overlap (fail closed). A reader can be paused by mistake; the user can swap.
- A one-shot write by a process that holds no file open at any sample, works outside the claim and
  exits before the next tick cannot be tied to an agent without kernel tracing. If the owner is
  running it is presumed the owner's; otherwise it is reported unattributed.
- A model agent's chat turn cannot be re-adopted after a coordinator restart; it is reported lost
  and its chat keeps the record.
- A model agent's reply passes through VOOL's own answer gates, which can withhold statements the
  turn's evidence does not support. If that removes the agent's `RESULT` line, the agent is reported
  unverified, never done; its full turn stays in its chat and on disk.
- Write-mode model agents need VOOL's runtime tool door to ask the gate; until that hook is
  installed in the serving process they are refused, not started.
