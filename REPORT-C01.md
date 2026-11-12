# REPORT C01: tool cards falsely said "I haven't looked in your contacts"

Task tool-cards-c01-20261030. Branch `delivery/tool-cards-merged-20261030`, base `195e7249`.

## Numbers

- Pushed: `507ecb9` (M1/M2), `e853a14` (M3), plus this report: see the branch head.
- Focused test `tests/test_a_served_tool_run_reaches_the_claim_gates.py`: 6 tests.
  - 195e7249: **3 failed, 3 passed**
  - 507ecb9: **1 failed, 5 passed** (operator lane still broken)
  - e853a14: **6 passed**
- M4 regression: **0 new reds.** The 3 tool-card files plus 118 test files that import or test the changed code
(`turn_model_call_ledger`, `tool_intent_executor`, the claim gates, `turn_call_accounting`):
121 files. Base 195e7249: 201 failed, 3285 passed, 68 skipped, 3 errors. Head e853a14: identical
(201 failed, 3285 passed, 68 skipped, 3 errors), and the same 204 failing test ids, in the same 27
files with the same count per file (table below; browser, database-plugin and MCP-bridge suites,
not touched by this change). Tool-card files: all green on both sides. Not run: the other 194
files that import `core.web.api.runtime` (the full 315-file set was ~2% done after 6 minutes on
this 4-core box; stopped). CI is the merge gate.
- M5 live (qwen3:8b + nomic-embed-text, CPU Ollama, card vectors built before any timed ask):
  **0 of 10 asks completed within the 600 s harness limit on either side.** Base FU-C01 and
  FU-C02 timed out (contacts.search ran twice on FU-C01). Head FU-C01 timed out too (contacts.search
  ran twice, one model call took 6m20s). The head daemon finished that turn after the harness gave up and committed it at 18:39Z
  (conversation log, `commit_state: committed`): **"Priya Shah's phone number in your contacts is
  +44 7700 900123."** That reply is correct: no false claim and no correction. Note that this
  wording has no "I checked your contacts" sentence, so it does not show the gate standing down
  live. The served test does show that. No other asks ran. Not a live pass; the
  live check has to be re-run on a faster box.

## Cause

`core/web/api/runtime.py::_run_agent_locked` opens the turn's call ledger on its own copy of the
transport's context, and the tool run is recorded in that ledger. The `/api/chat` lanes (buffered
and streamed) finalize with the context they hold, which never got the ledger stamp, so
`gate_action_claims` and `gate_execution_claims` saw no execution and replaced a reply a real
`contacts.search` backed.

Second cause, same class (found in the M3 sweep): `operator.*`, `operator.command.*`, plugin, hive,
payment and MCP intents dispatch through their own lanes in `tool_intent_executor` and never named
the tool in the ledger at all, so `operator.find_notes` that ran still published "I haven't looked
through your notes".

## Fix

- `turn_model_call_ledger.carry_turn_ledger()`: copy the ledger stamp back from the runtime's copy
  to the dict it was given (`_run_agent_locked`), and from `run_agent` to the transport's own
  context, beside the `TurnRequest` it already carries back. Both gates are unchanged.
- `tool_intent_executor._record_lane_dispatch()`: name the intent in the ledger at
  `_dispatch_with_claim` (both variants) and on the MCP lane, before the call and past the
  permission gates, the same rule `_dispatch_runtime_tool` follows. Deliberately not in
  `_maybe_store_tool_receipt`, which also sees runtime refusals.

## Failing-then-passing test

Real daemon (`apps.vool_api_server`) over a socket, its own VOOL_HOME with a seeded contact
"Priya Shah", an Ollama-dialect stub that calls `contacts__search` / `operator__find_notes` and then
answers; buffered and streamed lanes. Guard both ways: the same claims with no tool run are still
corrected (pass on all three commits).

```
195e7249: FAILED test_a_contacts_lookup_that_ran_keeps_its_answer[buffered]
          FAILED test_a_contacts_lookup_that_ran_keeps_its_answer[streamed]
            AssertionError: Removed from this reply: a described run of `contacts.search` that this
            turn did not perform. ... assert "I haven't l...our contacts" not in "Removed fro... I'll check."
          FAILED test_a_notes_search_that_ran_on_the_operator_lane_keeps_its_answer
            AssertionError: I haven't looked through your notes. Ask me to search your notes and I'll look.
          3 failed, 3 passed in 136.87s
507ecb9:  1 failed (notes, operator lane), 5 passed in 141.61s
e853a14:  6 passed in 140.30s
```

## M3 sweep (served rig, stub model, head e853a14)

| ask | tool run | published reply |
|---|---|---|
| FU-C01 contacts lookup | contacts.search | kept ("I checked your contacts: Priya's phone number is ...") |
| FU-C02 add contact | contacts.save (contact stored) | kept ("I've added Jonas Petrauskas ...") |
| FU-N02 notes search | operator.find_notes | kept (was corrected on 507ecb9) |
| FU-R02 reminders | none offered by the stub run | corrected: true, nothing ran |
| FU-Q01 CPU | deterministic lane | runtime-composed, gate not involved |
| FU-Q02 free RAM | machine.inspect_specs (fast path) | grounding refusal, see below |
| FU-S01 skills | deterministic lane | runtime-composed, gate not involved |
| FU-F01 Downloads | deterministic folder read | runtime-composed, gate not involved |

## What is still broken / not covered

- **Live base vs head not done as specified.** This box (4 CPU, no GPU) could not finish the live
  set on both sides in the time box. A first base run (contended by served tests) timed out at the
  600 s harness limit on FU-C01 (contacts.search ran twice, no reply) and FU-C02. Second runs:
  certification timed out at 300 s under load ("degraded"). I re-certified the head daemon by hand,
  and a kill of mine stopped both run wrappers. Head asks then ran through the kit's own
  `run_case` against the warmed head daemon (results above). No base live reply exists from this
  box; the base behaviour rests on the reviewer's FU-C01 finding and the served test above.
- `web.search` / `web.research` (and the A6 unknown-outcome path) still name nothing in the ledger.
  They probably reach the gates through the grounding lifecycle's bound notes. Not verified.
- FU-Q02 "how much RAM is free right now?" on the served rig: `machine.inspect_specs` ran on a fast
  path, then the grounding gate refused: "This turn retrieved sources, but they never reached the
  model call that wrote the answer". That is a different gate (evidence binding), seen only with
  the stub model. Not fixed.
- A pending proposal counts as a run: any dispatched tool stands both gates down, as the runtime
  lane already did (e.g. a `contacts.save` waiting for the PIN). "Saved" over a pending proposal
  is not corrected. This is the gate's existing rule, unchanged here.
- M4 covered 121 of the 315 files that import a changed module (see above).
- FU-R02 on the stub was offered no tool. That is routing, not this class, and was not looked at.

## M4 red files (identical on base and head)

- tests/code_assistant/test_sabotage.py: base 7 head 7
- tests/plugin_mcp_budgets/test_registered_effect_doors.py: base 11 head 11
- tests/plugin_mcp_budgets/test_sabotage_lane.py: base 1 head 1
- tests/repoops/test_forge_actions.py: base 2 head 2
- tests/test_code_task_model_surface.py: base 7 head 7
- tests/test_live_browser.py: base 8 head 8
- tests/test_live_browser_beta.py: base 45 head 45
- tests/test_mcp_bridge.py: base 4 head 4
- tests/test_native_skill_library.py: base 1 head 1
- tests/test_openclaw_tooling_context.py: base 1 head 1
- tests/test_p0_mcp_trust.py: base 7 head 7
- tests/test_p0_plugin_dispatch_boundaries.py: base 6 head 6
- tests/test_p0_toolchain_authority.py: base 3 head 3
- tests/test_p0_toolchain_sabotage.py: base 4 head 4
- tests/test_runtime_continuity.py: base 2 head 2
- tests/test_runtime_execution_operator_phase1.py: base 16 head 16
- tests/test_runtime_execution_tools.py: base 2 head 2
- tests/test_tool_use_root_owner_live_sources.py: base 1 head 1
- tests/test_vool_browser_chat_journey.py: base 1 head 1
- tests/test_vool_browser_isolation.py: base 3 head 3
- tests/test_vool_browser_product.py: base 16 head 16
- tests/test_vool_browser_shopping.py: base 6 head 6
- tests/test_vool_database_chat_journey.py: base 1 head 1
- tests/test_vool_database_fidelity.py: base 6 head 6
- tests/test_vool_database_plugin.py: base 14 head 14
- tests/test_vool_database_served_proof.py: base 1 head 1
- tests/test_vool_database_transaction_truth.py: base 28 head 28
