# Result: tool-use root cause, round 3 (builder, 2026-10-30 evening)

Branch `delivery/tool-use-root-cause-2-20261030`, from `598dc25a`. Code head measured: `cd904b2c`.
Box: 4 CPUs, CPU Ollama 0.40.13, qwen3:8b (num_thread 2 from VOOL's compute mode), the kit's harness and fake home
read from `origin/audit/tool-use-kit-20261029` (the kit files are not on this branch; the encrypted hidden set was
never extracted). Tests: Python 3.12.3 venv with the CI extras, plus pytest-timeout and pytest-xdist in that venv only.

## What changed, per commit

| Commit | Change | Test (red before, green after) |
|---|---|---|
| `13d31ed3` | On an owner catalog turn the workflow planner leaves research INFERRED from a label or a word (`system_design`, "current", "compare", "docs") to the model; an explicit lookup, a `research` verdict and a web follow-up keep their plan. | `tests/test_owner_tool_catalog.py` (3 new cases red, explicit/off-catalog controls green on both) |
| `4d7a4252` | `pdf.extract_text` / `pdf.ocr` / `machine.read_file` on a path that names no file: `machine.find_file` in the safe folders (name, then stem, same suffix): one match read and named, several listed with "Which one should I read?", none said plainly; a home folder VOOL may not read (Pictures) gets one line. | `tests/test_find_before_reading_a_named_file.py` (5 red, 1 control) |
| `0f573385` | Owner mail/calendar asks in LT, FR, ES, DE, RU, PL (+PT, IT) with nothing connected get the plain "not connected" line before any model (mail: `email_not_connected_reply`; calendar reads: new `calendar_not_connected_reply`). How-to, drafting, calendar writes, remote turns, connected/unknown accounts keep their lanes. | `tests/test_mail_and_calendar_in_any_language.py` (collection error before: new symbols; 40 cases) |
| `71943158` | A native catalog round no longer carries the text copy of the catalog (context message 8.6k -> 6.1k chars on the served rig), and sends `keep_alive: 10m` when none is configured. No model call added. | `tests/test_owner_tool_catalog.py` (3 new, red before) |
| `949495cd` | A model `list_directory` proposal for a home folder the owner named and VOOL may not read yet (Pictures, Music, Movies, Videos) stays refused, in one line naming it. | `tests/test_owner_tool_catalog.py` (1 new, red before) |
| `30c8cb31` | The catalog's seats are added to the words' own seat budget instead of `max()`-ing it away (fixes a red that 598dc25a introduced, see Tests). | `tests/test_final_answer_authorship_served_p0.py::test_served_a_real_tool_turn_is_executed_and_published_untouched` (red on 598dc25a, green after) |
| `cd904b2c` | Find-before-read searches only for a guess inside the owner's home; any other missing path keeps the reader's own refusal (fixes 2 reds `4d7a4252` introduced, found by the -k run). | `tests/test_find_before_reading_a_named_file.py` + the two reds below |

## The red test

`tests/test_answer_integrity_consecutive_turns.py::test_the_logged_consecutive_turn_sequence_through_the_served_door`:
**fixed** in `13d31ed3`, test not edited. Root cause: the history marker was never dropped. On turn 4 the
catalog sent the WoW design question into the tool lane; it classified `system_design`, the workflow planner's
inferred research flow planned `web.search`, which answered "Web lookups are off" with no model call at all, so the
only captured call with the question was the memory-search phrase call. With the planner leaving inferred research
to the model, the catalog round runs and carries `[Failed exchange: ...]` (verified on the wire). Whole file: 32/32.

## Dev run (head `cd904b2c`): NOT finished

Started 21:37Z on the 81-ask set (one daemon, serial turns, nothing else on the CPU; the 15 ids the last session ran
on both sides went first). It stopped at its 22:55Z cutoff after **22 of 81 asks** (16 needs_tool, 3
missing_connection, 3 no_tool). Nothing below covers the other 59 asks. The 15 comparison ids are reconstructed
from the last RESULT (nt-01,04,07,10,13,16,19,22,25; mc-01,04,07; nk-01,04,07); its raw rows are gone, so base and
598dc25a columns are that file's aggregates.

Reader verdict = the right tool really ran, the ask is answered, and nothing is claimed that did not happen.

| Kind (asks done) | Scorer | Reader | False claims | Target |
|---|---|---|---|---|
| needs_tool (16 of 51) | family tool 13/16; timeouts 3 | **6/16** (nt-01,04,05,07,08,25) | 1 (nt-03) | 41/51: not reachable from 16 rows; at this rate no |
| missing_connection (3 of 15) | no tool 3/3 | **3/3** said plainly (mc-01,04,07) | 0 | 14/15: 3/3 so far |
| no_tool (3 of 15) | no tool 3/3 | **3/3** | 0 | 14/15: 3/3 so far |

Same 15 ids, base / 598dc25a (last session) against `cd904b2c`:

| Kind | Base cb4852e5 | 598dc25a | cd904b2c |
|---|---|---|---|
| needs_tool, scorer family tool | 4/9 | 8/9 | 8/9 |
| needs_tool, reader | 4/9 | 5/9 | 4/9 (nt-13 judged a fail here, see below) |
| missing_connection, said plainly | 2/3 (mc-07 false claim) | 2/3 (mc-07) | **3/3** (mc-07 fixed) |
| no_tool, no tool ran | 3/3 | 3/3 | 3/3 |
| timeouts (600 s) | 0 | 1 (nt-10) | 1 (nt-16) |

## Latency (seconds per turn, wall clock at the harness)

| Asks | Median | Max | Turns over 300 s |
|---|---|---|---|
| Same 15, base cb4852e5 (last session, partly overlapped by a second daemon) | 107 | not recorded here | - |
| Same 15, 598dc25a (last session, pytest beside it) | 136 | 600 (nt-10 timeout) | - |
| Same 15, cd904b2c (clean) | **104.4** | **600.1** | 3 (nt-10 490, nt-13 372, nt-16 600) |
| All 23 turns, cd904b2c | 161.0 | 600.1 | 7 |

598dc25a was not re-measured cleanly in this session (the CPU went to the required test runs), so "before" is the
last session's number. Median target (<= 107 s) met on the 15; the max target (no turn over 300 s) is **not met**.
Where the time goes (events of nt-10, nt-13, nt-16): one catalog round is 2.5-6 min at 2 threads (2.5-4.5k tokens);
the conductor splitter (7k-char prompt) and the memory-search call run before it with other prompts, and round 2
of nt-13 offered 20 tools against round 1's 15, so the tool block at the top of the prompt changed and Ollama
re-read the whole prompt (2 min 37 s, then 3 min 26 s). Next latency fixes, not done: keep a catalog turn's offer
identical across its rounds, and skip the conductor's model split on catalog turns. `30c8cb31` (needed to keep a
cb4852e5 test green) adds the words' own seats back, so some catalog rounds carry 17-19 tools instead of 15.

## Failing dev cases (22 asks run)

- dev-nt-02: list_directory and read_file ran on the Desktop, then the turn timed out at 600 s (no reply).
- dev-nt-03: FALSE CLAIM. The `chat_housekeeping` lane answered "Found it:" with an earlier chat message ("how many photos do i have in Pictures"), not the recipe; no file tool ran (lane predates this branch).
- dev-nt-06: read_file ran on the Q3 report, then the turn timed out at 600 s (no reply).
- dev-nt-09: list_directory proposal refused by the semantic preflight ("what did i download this week?" names no folder); reply "not mentioned in the records"; turn 2 asks what "those" are. No false claim, no answer.
- dev-nt-10: model guessed `/Users/username/Desktop/flat_viewing_notes.txt`; that is outside the real home, so `cd904b2c` left it to read_file's refusal; reply "I can't read the file on your desktop". A fix (rebase a guessed `/Users/<x>/<safe folder>/` path onto the real home; stem match across suffixes for text reads) is written and saved but NOT committed: it could not get its full test gate before the hard stop.
- dev-nt-11: list_directory proposal refused by the semantic preflight ("where did i put my receipts for taxes"); generic refusal, no search. No false claim.
- dev-nt-13: model listed `~/Downloads` with directories only; reply "No visible folders matched", which does not answer what is in the folder (11 files).
- dev-nt-16: find-before-read found `boarding-pass-VNO-LIS.pdf` from the guess `boarding_pass.pdf`; the PDF reader is macOS-only (PDFKit), so the read failed on this Linux box; the turn then timed out at 600 s.
- dev-nt-19: find-before-read found `CV_2026.pdf` from `CV.pdf`; same macOS-only PDF reader; reply "I wasn't able to turn that into a completed action". No false claim.
- dev-nt-22: refused as required, in one line: "VOOL cannot read your Pictures folder yet; it can read Desktop, Downloads, Documents." Counted as a fail for "the right tool ran", by design (the folder is not widened).

Passing with a note: nt-07 found the folder but did not list what is inside it; nt-05 listed the largest files of the
whole home, not only Documents (labelled as such, no false claim).

## Limits

- 22 of 81 dev asks; the hidden bar was not run. Nothing here is acceptance.
- PDF reads cannot succeed on this Linux box (macOS system Python), for base and head alike.
- The `cd904b2c` commit message quotes two existing test ids that contain a word the repo scan bans; history was not rewritten (no force push).

## Tests against cb4852e5

Related selection, `pytest -k "offer or seat or tool or operator or finaliz or route" tests -n 4 --timeout=300`
(7,264 collected of 51,362), tree on PYTHONPATH:

| Head | Passed | Failed | Errors | Failing ids that pass on cb4852e5 |
|---|---|---|---|---|
| `13d31ed3` | 7056 | 93 | 5 | 1: `test_final_answer_authorship_served_p0.py::test_served_a_real_tool_turn_is_executed_and_published_untouched` (also red on 598dc25a; fixed in `30c8cb31`) |
| `30c8cb31` | 7060 | 94 | 5 | 2: `test_runtime_execution_tools.py::...::test_machine_read_file_blocked_path_is_explicitly_*` and `test_openclaw_tooling_context.py::...::test_openclaw_blocked_machine_read_then_workspace_recovery_stays_*` (from `4d7a4252`; fixed in `cd904b2c`) |
| `cd904b2c` | 7062 | 92 | 5 | **none**: all 97 failing/erroring ids were re-run by id on cb4852e5 and fail or error there too |

The shared reds are environment or pre-existing (no MCP stub server / OS sandbox / gpg updater fixtures on this box,
`test_runtime_execution_operator_phase1` sandbox runs, cb4852e5's own wording-seat regressions). Also on `cd904b2c`:
`tests/test_owner_tool_catalog.py`, `tests/test_answer_integrity_consecutive_turns.py`, and the two new test files:
103/103 passed. `ruff check .`: 13 findings, the same 13 on 598dc25a, none in touched files.

## Previous round (598dc25a, kept for reference)


Head pushed: see `git log` on `delivery/tool-use-root-cause-2-20261030`; all numbers below are for code commit
`e41b738` (base `cb4852e5`). Box: 4 CPUs, CPU Ollama 0.40.13, qwen3:8b, the kit's harness and fake home.

### Root cause (3 lines)

1. Whether the local model ever saw a tool was decided from the raw words before the model ran
   (`should_keep_ai_first_chat_lane`, `should_attempt_tool_intent`), so fresh phrasings reached qwen3:8b with 0 tools.
2. When it did see tools, Ollama ignores `tool_choice: required`: prose was treated as a failed call, and nothing
   told the model that the owner's files/notes/etc. are only readable through the tools.
3. Pre-model lanes (conductor planner, entity-ambiguity probe) and the list-directory preflight ate or refused
   turns the model would have served.

### What changed

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

### Dev numbers (same 15 ids run on both; subset A was 27 asks, the candidate run was stopped at the 3 h limit)

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

### Latency (contaminated: pytest ran beside most candidate turns)

Median per turn on the 15 ids: base 107 s, candidate 136 s. A catalog round costs 1.5-4 min on this box
(about 3 k prompt tokens; generation at 2 threads). nt-13 took 566 s, nt-16 515 s.

### Tests

Related tests (207 files: the gate/offer files plus files touching the adapter, prompt normalizer, ambiguity
probe, conductor and the retry) on e41b738: 5246 passed, 44 failed, 12 skipped. The same failing ids re-run on base:
43 fail there too (no MCP stub / OS sandbox in this box, plugin dispatch, cb4852e5's wording-seat regressions).
**One new red:** `tests/test_answer_integrity_consecutive_turns.py::test_the_logged_consecutive_turn_sequence_through_the_served_door`
passes on base and fails on e41b738 (reproduced alone). Its served daemon has only a local stub model, so the catalog
applies and turn 4 goes through the tool loop, whose captured model call lacks the failed-exchange history marker.
The brief's full `-k` selection was not completed on either side (base run hung at 91% and was stopped).

### What is left

1. Fix the new red above (history markers on the tool-loop path, or the test's first captured call) before any PR.
2. Latency: drop the duplicated text catalog on native-tools rounds; keep the Ollama prefix cache warm across the
   conductor/probe calls; a turn with two catalog rounds can exceed 600 s on 4 CPUs.
3. PDFs and pictures: the model guesses file names; a find-then-read path for `pdf.extract_text`, and a
   plain answer for `~/Pictures` (outside the safe roots).
4. mc-07 (Lithuanian mail ask) still gets a false "no letter" claim: the mail recognizer is English-only.
5. Run the full dev set (81 asks) and the hidden bar; nothing here is acceptance.
