# Clean local loop

An opt-in, parallel path for local-model turns about the owner's own files or computer. Instead of
the front-door fast-path stack, the turn runs one plain tool loop over VOOL's existing authorized
machine tools. It exists to be measured against the current pipeline first. No fast path was
removed.

## Why

On Oct 28 the same local model, qwen3:8b, answered the same 54 file asks over the audit's fake
home twice:

- in a 300-line JSON tool loop it used a tool on 51 of 54 asks and got 23 of 30 checkable asks right;
- in VOOL's daemon it used a tool on 13 of 54, got 5 of 30 right, and gave 19 confident wrong answers.

The causes were in the pipeline, not the model. They were canned "I can't access" and "I can only
answer that by inspecting..." replies, canned ambiguity questions, an arbiter fast path that called
`find_folder` with empty arguments, model rounds that offered no tools, and a first model lane that
failed. The plain loop is `ai-os-spine-20261028/live/live_door.py`. This package is that loop,
running on VOOL's own boundary, tools and model seam.

## When it runs

It is hooked in `AgentRuntime._run_once_inner`, after the secret intake, attempt follow-ups,
receipt-location follow-ups and the browser refusal, and before the currency, conductor, planner,
ambiguity and front-door lanes. `core/clean_loop/route.py` admits a turn only when all of these
hold, checked in this order:

1. `VOOL_CLEAN_LOCAL_LOOP` is `1`, `true`, `yes` or `on`. It is off by default.
2. The turn is not RED. VOOL has no text-level risky-action classifier to reuse, because its
   consent gates sit at the effect. So a small verb floor (pay, send, transfer, buy, delete,
   remove, clear out, get rid of, post, share, upload, cancel, sign, install, kill and similar,
   plus money and wallet words) keeps those turns on the normal ceremony.
3. Nothing else owns the turn. It is not a planned sub-turn, a routing retry, a browser workflow,
   a project-bound chat or a chat with an active goal workflow, and it is the owner's own local
   session.
4. It asks about the owner's files or computer, or it is a short follow-up to a clean-loop turn in
   the same session. A bare "yes" or "go ahead" does not count as a follow-up. A live or online
   question (weather, news, prices, the web) is left to the online lanes.
5. The brain is local. That means one of three things. The owner pinned a local model. Or the
   turn is Auto, no saved cloud default takes it, local models are enabled and running, and a
   registered local model has passed its final-answer check. Or `VOOL_CLEAN_LOOP_MODEL` names a
   registered local model.

Any other turn falls through unchanged. If the model never answers at all, the turn also falls
through, so the normal pipeline and its failover still get their chance.

## How it works

| file | job |
|---|---|
| `route.py` | the admit decision above |
| `brain.py` | picks the local manifest and calls it through `MemoryFirstRouter._invoke_manifest`, the seam every provider call crosses (local-only backstop, authorship fence, sealed invocation, call ledger, deadline). Temperature 0, `reasoning_mode="disabled"` (`think: false` on Ollama). No new HTTP client. |
| `loop.py` | one JSON action per model call, at most 6 calls; the parser tolerates prose, code fences and `<think>` blocks around the JSON; on the last step the model is told to answer, so a raw tool request is never served |
| `tools.py` | `list_dir`, `find`, `read_file`, `largest`, `disk_usage`, `system_info` map onto `machine.list_directory`, `machine.find_file`, `machine.read_file` / `pdf.extract_text`, `machine.find_largest`, `machine.disk_usage`, `machine.inspect_specs` + `machine.list_processes`, each through `execute_authorized_runtime_tool` |
| `prompt.py` | a short, static system prompt (no clock), so the prefix cache holds |
| `registry.py` | the plug-in seams |
| `hook.py` | `maybe_answer`, the one runtime entry |

Paths are resolved inside the home folder before any tool runs. A home that resolves to a
filesystem root is refused, so nothing here ever walks `/`. `machine.find_folder`, the whole-disk
walk, is not used. The runtime's handlers then apply their own narrower rules (today Desktop,
Downloads and Documents), and their refusals reach the model as plain "not available" lines with
internal intent names removed.

The result is the agent's normal fast-path envelope with `route_reason: clean_local_loop`, plus
these fields:

- `model_execution` (provider, model, number of model calls) and `model_selected`;
- `tool_receipts`, also appended to the turn's `source_context["tool_receipts"]`, in the shape the
  machine fast paths and the honesty validator already use;
- one `tool_executed` / `tool_failed` runtime event per tool call, which the execution-truth
  ledger records as tool facts;
- `clean_local_loop`, the step trace: tools used, arguments, statuses, stop reason;
- `pending_proposals` when the model proposed a change.

The boundary also names each intent in the turn's call ledger.

## Plug-in seams

New research wins are added by registering on `default_registry()` (or on a private `Registry`
handed to `run_loop`). The loop itself is not edited.

- `register_tool(name, doc, handler)`: a new tool. The doc line goes into the prompt in
  registration order. A handler that touches the machine must use the authorized boundary
  (`tools._call` does this).
- `register_pre_step(name, fn)`: read-only evidence staged before the first model call, for
  example future "trays".
- `register_answer_gate(name, fn)`: may rewrite the final answer from the trace, for example a
  receipt gate. The built-in `pending_proposal_notice` is one. It makes sure a turn that proposed
  a change says nothing has changed yet.

## What it deliberately does not do

- It never writes, moves, renames or deletes. `propose_change` records a pending proposal and the
  answer says it is waiting for approval. VOOL has no clean proposal API this loop could hand the
  change to, so the change is not carried forward. A later "yes" goes to the normal pipeline.
- It never runs money or irreversible actions. RED turns are not admitted at all.
- It never goes online. There is no web tool, and online asks are not admitted.
- It never answers from a cloud model, and it does not replace the front door for other turns.
- It does not widen what the machine tools may read. Folders outside the runtime's safe roots,
  such as Pictures and Movies today, stay refused by the runtime.
- It does not use the conductor, planner, intent arbiter, canned ambiguity replies or the
  multi-intent appendix.

## Turning it on (Mac audit run)

```sh
export VOOL_CLEAN_LOCAL_LOOP=1
# optional: pin the model by name (must be a registered local model)
export VOOL_CLEAN_LOOP_MODEL=qwen3:8b
```

Set these in the environment of the VOOL daemon before it starts, then restart it. The model must
be ready, meaning Settings shows it as ready (its final-answer check passed). Without that, the
router's authorship fence refuses the call and the turn falls through to the normal pipeline.
Each turn's result carries `route_reason: clean_local_loop` and the `clean_local_loop` trace. A
turn the route declined says why in `source_context["clean_local_loop_route"]`.
