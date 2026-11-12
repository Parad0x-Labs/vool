# Standing coordinator

The standing coordinator watches a team of helper agents for as long as the task runs.
It nudges agents that stopped making progress, answers what is waiting on it from
decisions you already made, remembers those decisions, enforces the team's spend cap,
and tells you only what changed.

It is plain code in `core/standing_coordinator/`. It makes no model call of its own, so
watching a team costs no model tokens however long the task takes.

## What it does on each tick (every 30 s)

| Situation | What happens |
|---|---|
| Team spend reaches the warning share of its cap (default 80%) | One warning in the chat. |
| Team reaches its cap (cost, tokens, calls or wall clock) | Every unfinished agent is stopped through the team's registry; one notice says so. Work so far is kept and labelled partial. |
| An agent is waiting on a question you already answered and asked to remember | It gets that answer, with the decision it came from. You are not asked again. |
| An agent is waiting on anything else | One question card lists every open question in the team. A new question re-sends the open list; nothing is asked twice. |
| An agent made no progress for 10 min | A status-check message into its own chat, up to 2 times, 5 min apart. |
| Still no progress after that | One notice that it is stuck, with a pause recommendation (or it is paused, if the watch says so). |
| Agents finish | One update listing them and their own reports. |
| Every agent has ended | One final report, labelled partial when any agent did not finish; the watch ends. |
| Otherwise | A progress line at most every 15 min, and only when something changed. |

Questions and stuck notices go to the "Needs you" section of the notification centre,
in the chat the team was started from. Reports go to "Updates".

## Decisions

Answer a waiting agent with `remember`:

- `no` (default): this request only.
- `team`: later agents in the same team asking the same topic get the same answer.
- `always`: any team.

A newer answer to the same topic replaces the old one; the old row is kept with who
replaced it. `forget` retires a decision.

Some requests are always asked, never answered from memory: `git_push`, `deploy`,
`publish`, `external_message`, `payment`, `spend_increase`, `delete`, `credential`,
`merge`, anything the agent flags as irreversible, and any request without a topic.
Your answer to one of those is recorded for that request only.

## What it can and cannot do

It can nudge, answer from your recorded decisions, ask you, pause or stop an agent, and
report. Every effect goes through the team's own registry, which acts only on agents it
started. It cannot approve anything you have not decided, cannot start agents, and never
signals a process by name.

Each action is claimed in the coordinator's database before it runs, so a restart never
repeats a nudge, an answer or a notice. An action a crash interrupted is marked
`uncertain` and is not replayed.

## Surfaces (owner-local)

| Route | Use |
|---|---|
| `GET /api/coordinator/watches` | Teams being watched |
| `GET /api/coordinator/status?team=<id>` | Agents, spend, open questions, recent events |
| `GET /api/coordinator/decisions[?all=1]` | Remembered decisions (`all=1` includes replaced ones) |
| `POST /api/coordinator/watch` | `{"team_id", "policy"?, "session_id"?}` |
| `POST /api/coordinator/unwatch` | `{"team_id"}` |
| `POST /api/coordinator/decide` | `{"team_id", "request_id", "answer", "remember"?, "source"?}` |
| `POST /api/coordinator/forget` | `{"decision_id"}` |

`policy` fields: `stall_after_seconds`, `nudge_gap_seconds`, `max_nudges` (0-5),
`report_every_seconds`, `spend_warn_fraction`, `auto_pause_stalled`, and `ceilings`
(`max_calls`, `max_tokens`, `max_cost_usd`, `wall_clock_seconds`). Unknown fields are
refused.

Team ids name their registry: `team:<id>` for agent teams, `council:<run_id>` for council
runs. A council run is watched as one agent; it can be stopped, while its seat retries and
resumes stay on the council card.

Set `VOOL_STANDING_COORDINATOR=0` to keep the background loop off.

## Adding a registry

A registry implements `TeamPort` (`snapshot`, `nudge`, `answer`, `pause`, `stop`) and
registers it with `register_port("<prefix>", factory)`. A call it cannot honour returns
`unsupported`; the coordinator then asks you instead.
