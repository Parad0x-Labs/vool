# Result: tool-use root cause, round 2 (builder, 2026-10-30)

Head pushed: see `git log` on `delivery/tool-use-root-cause-2-20261030`; all numbers below are for code commit
`e41b738` (base `cb4852e5`). Box: 4 CPUs, CPU Ollama 0.40.13, qwen3:8b, the kit's harness and fake home.

## Root cause (3 lines)

1. Whether the local model ever saw a tool was decided from the raw words before the model ran
   (`should_keep_ai_first_chat_lane`, `should_attempt_tool_intent`), so fresh phrasings reached qwen3:8b with 0 tools.
2. When it did see tools, Ollama ignores `tool_choice: required`: prose was treated as a failed call, and nothing
   told the model that the owner's files/notes/etc. are only readable through the tools.
3. Pre-model lanes (conductor planner, entity-ambiguity probe) and the list-directory preflight ate or refused
   turns the model would have served.

## What changed

- `core/owner_tool_catalog.py` (new, flag `owner_local_tool_catalog`, default on): on the owner's own turn served by
  a local model, both gates let go and the offer seats 12 read tools (one per family, short descriptions, fixed
  order). Writes are not in the catalog; they keep their recognizers and approval gates. Remote sessions, cloud
  lanes and tools-forbidden turns are unchanged.
- Catalog rounds carry one short instruction (owner data lives on this computer, only tools read it; knowledge is
  answered directly), and a plain-prose reply on such a round is the model's `respond.direct`.
- On a catalog turn: the conductor hands back a turn with an unresolved clause; the ambiguity probe skips
  questions anchored on the speaker or VOOL; a model `machine.list_directory` proposal is admitted when its path is a
  safe folder the owner's own words name (any other proposal is still refused).
- The cb4852e5 retry is kept as is.
- Tests: `tests/test_owner_tool_catalog.py` (23 tests: scope, read-only catalog, flag off, remote/cloud/forbidden,
  probe scope, prose conversion, folder binding).

## Dev numbers (same 15 ids run on both; subset A was 27 asks, the candidate run was stopped at the 3 h limit)

Reader verdict = tool of the right family really ran and the reply has no false claim (folder_audit_fast_path's
real listing counted as a pass although `score.py` does not count it).

| Kind | Base cb4852e5 | Candidate e41b738 |
|---|---|---|
| needs_tool, scorer family tool | 4/9 | 8/9 |
| needs_tool, reader pass | 4/9 (nt-01,04,07,25) | 5/9 (nt-01,04,07,13,25) |
| missing_connection, says not connected, no false claim | 2/3 (fail mc-07) | 2/3 (fail mc-07) |
| no_tool, no tool ran | 3/3 | 3/3 |
| timeouts (600 s) | 0/15 | 1/15 (nt-10) |

Candidate needs_tool fails: nt-10 timeout after `machine.find_file` found the file; nt-16 and nt-19 called
`pdf.extract_text` with a guessed path and reported the failure (no false claim, no answer); nt-22 `Pictures` is not
a safe folder and stays refused. Base-only extra rows (12 more ids, not run on the candidate): base passes 5/8
needs_tool by reader, mc 0/2, no_tool 2/2.

Targets (80% / 90% / 90%) are NOT met and the denominator (15) is too small to claim anything beyond direction.

## Latency (contaminated: pytest ran beside most candidate turns)

Median per turn on the 15 ids: base 107 s, candidate 136 s. A catalog round costs 1.5-4 min on this box
(about 3 k prompt tokens; generation at 2 threads). nt-13 took 566 s, nt-16 515 s.

## Tests

Related tests (207 files: the gate/offer files plus files touching the adapter, prompt normalizer, ambiguity
probe, conductor and the retry) on e41b738: 5246 passed, 44 failed, 12 skipped. The same failing ids re-run on base:
43 fail there too (no MCP stub / OS sandbox in this box, plugin dispatch, cb4852e5's wording-seat regressions).
**One new red:** `tests/test_answer_integrity_consecutive_turns.py::test_the_logged_consecutive_turn_sequence_through_the_served_door`
passes on base and fails on e41b738 (reproduced alone). Its served daemon has only a local stub model, so the catalog
applies and turn 4 goes through the tool loop, whose captured model call lacks the failed-exchange history marker.
The brief's full `-k` selection was not completed on either side (base run hung at 91% and was stopped).

## What is left

1. Fix the new red above (history markers on the tool-loop path, or the test's first captured call) before any PR.
2. Latency: drop the duplicated text catalog on native-tools rounds; keep the Ollama prefix cache warm across the
   conductor/probe calls; a turn with two catalog rounds can exceed 600 s on 4 CPUs.
3. PDFs and pictures: the model guesses file names; a find-then-read path for `pdf.extract_text`, and a
   plain answer for `~/Pictures` (outside the safe roots).
4. mc-07 (Lithuanian mail ask) still gets a false "no letter" claim: the mail recognizer is English-only.
5. Run the full dev set (81 asks) and the hidden bar; nothing here is acceptance.
