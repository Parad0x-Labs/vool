# Tool-use dev set (builder's own)

`asks-dev-v1.jsonl` is the builder's dev set for the tool-use work on `delivery/tool-use-root-cause-2-20261030`.
It was written before any code change on this branch, blind to the hidden set (`asks-v1.jsonl.enc` was never
decrypted). Same row shape as the hidden set, so `harness/audit_ask.py` and `score.py` from the kit run it as is.

| Kind | Count | Correct reply |
|---|---|---|
| needs_tool (`dev-nt-*`) | 51 | a tool of a family in `expect.tool_any` runs and the reply reports it without a made-up result |
| missing_connection (`dev-mc-*`) | 15 | mail or calendar with nothing connected: says so plainly or offers setup, claims no check or action |
| no_tool (`dev-nk-*`) | 15 | knowledge ask: no tool runs, no claim of a check |

About 14% of the asks are not English (Lithuanian, Spanish, Portuguese, German, French); three are two-turn
follow-ups. Results and notes are in `PROGRESS.md` and `RESULT.md`.
