# Tool cards: offered tools get used (job modular-tools-use-20261030)

Branch `delivery/tool-cards-use-20261030`, from `delivery/modular-tools-20261029` at `cd8b7651`.
Head: the commit that adds this report (code head `f57e48f`). The live A/B ran the head at `56fc80f`.
The later commits change only how the folder lanes hand their answer to `_fast_path_result`, and a
real agent gets the same events and receipt.

## Numbers first

Fresh asks `audit-kits/tool-use/fresh-use-20261030.jsonl` (40, written and committed before any code
was read, sha256 `d2f71935…f606`). Box: 4 vCPU Linux, CPU Ollama, qwen3:8b Q4_K_M, nomic-embed-text,
harness `audit_ask.py` unchanged (600 s turn timeout), cards on through a MEASURE-ONLY commit on each
side (never pushed). Prompt eval ran at ~10 tokens/s under load (~21 uncontended), i.e. 2-8 min per
model call, so only a pre-chosen half (odd ids, interleaved by kind) could be run, and only the
cases both sides finished are compared.

| Kind (cases) | Measure | base cd8b7651 + cards on | head (A/B run at 56fc80f) + cards on |
|---|---|---|---|
| needs_tool (8) | right tool **executed** (a `tool_executed` of the expected family) | 4/8 | **5/8** |
| | `score.py` family hit (also counts a selected-then-refused call) | 7/8 | 7/8 |
| | reply actually answers from a real tool result | 2/8 | 2/8 |
| | false action/read claims (reply read by hand) | 0 | 0 |
| | timeouts (600 s) | 0 | **2** (fu01, fu03: right tool ran, then out of time) |
| missing_connection (3) | says not connected, no false claim | 3/3 | 3/3 |
| no_tool (3) | no tool ran, no false claim (kit bar) | 3/3 | 3/3 |
| | reply is a clean answer | 3/3 | **1/3** (fu33 asked back after a cascaded model-call failure; fu37 got wrong correction lines from the claim gate) |
| all (14) | errors / empty replies | 0/14 | 2/14 (the two timeouts) |

Per case, head vs base: fu01 base refused the listing ("not mentioned in the records"), head ran
`list_directory` + `find_file` (4 PDFs found) and timed out on the third model call; fu03 base
"No folder matching 'Lisbon holiday'", head found the folder and timed out on the next call; fu05
base "I did not inspect a local directory…", head asked back because its pre-answer model check
failed while fu03's abandoned turn still held the only Ollama slot (cascade, see limits); fu07 base
"Ich kann nicht direkt auf deine Dateien … zugreifen" (no tool), head called `list_directory` and was
still refused (see still broken); fu09 both: `pdf.extract_text` on a guessed path; fu11, fu15 both
correct; fu13 both: `find_notes` searched the words "any notes" and said none, though fu11 had saved
one. Mail/calendar identical on both sides.

**Verdict on the live run: not proven better.** One more ask got its tool executed, and two asks now
reach the right result, but on this box those turns ran out of time and a timeout then spilled into
the next turns. The fixes are proven by the focused tests and by the traces above. The live
numbers are too few and too box-bound to count as acceptance.

Cases compared: the first 14 of the ordered half (fu01 03 25 33 05 07 27 35 09 11 29 37 13 15): 8 needs_tool, 3 mail/calendar, 3 no_tool. No mail/calendar or no-tool ask beyond those, and none of the two-turn asks, was reached.

## What each cause turned out to be, and the fix

| Class | Owning cause found | Fix (commit) |
|---|---|---|
| (a) tool ran, reply says it could not look | The executor's list-directory admission proof read only the list-directory word probe, while the offer seated `machine.list_directory` from the owner live-source recognizer and the cards. Base fu01 ("which PDFs are sitting in my Downloads") and fu05 ("what's in the garden-planner project folder"): the model called the tool, the executor refused with "I did not inspect a local directory because this turn did not establish a machine or path target", and that became the answer. | Admission also accepts the owner's own folder demand (live source "files", the folder grammar, or a card release for `list_directory`), owner-local only, semantic frame check unchanged (`a4588e3`, `9c651db`). Head fu01: `list_directory` and `find_file` now execute. |
| (a') right tool ran on a wrong result | `machine.find_folder` matched names by substring, so "Lisbon holiday" missed "Holiday Lisbon 2026" (base fu03: "No folder matching 'Lisbon holiday' found"). | Two or more named words, all present as whole words in any order, match (`dfd1017`). Head fu03: "Found 1 folder". |
| (b) tool seated, model answered in prose | The one-shot prose retry (`needed_tool_intents`) only knew the hand-written recognizers, so a tool seated by its card was never retried. | The retry also takes card matches, but only near-certain ones: meaning cosine >= 0.85 (0/12 no-tool dev asks) or words >= 0.75 (`a4588e3`, `56fc80f`). A merely released card (cosine 0.70) still only offers. |
| (c) reply listed folder contents, no receipt | The folder-audit lane ("what's in my reports folder") and the workspace overview lane read the folder in-process (`build_folder_overview`) and answered through the bare `_fast_path_result`: no tool event, no observation, no receipt. | Both record the read beside their unchanged answer call: `tool_selected`/`tool_executed`, the turn's observation, a tool receipt (`241e19b`, `f57e48f`). |
| (d) "saved / I'll remember that" with no receipt | The claim gate only corrects a sentence that names a live source, and none covered contacts or keeping something for later, so "Got it! I'll remember that…", "Saved! …" and "I've added Rasa to your contacts" passed as true. | New `contacts` and `memory` sources, a memory-claim form (the present "I remember…" stays recall), correction lines in en/fr/es/de; the request's keeping source picks the line (`9effd9a`). The gate still stands down when any tool ran. |

M1 threshold (`85405c3`): band `SEM_ZERO 0.55 -> 0.50` puts the lane release at cosine 0.70 (was
0.725), from `python -m tools.tool_card_calibrate` on the dev asks: 0.75 = needs 15/26, no-tool 2/12;
0.70 = 18/26, 4/12; 0.65 = 21/26, 7/12 (full table in the commit message).

## Tests (M4)

130 test files touch the changed modules. 60 were run per side, one file per pytest, base on a clean
`cd8b7651` and head on a clean checkout. The 34 most direct files run first, and those 34 were re-run
on the final head `f57e48f`. The other 70 were not reached in time.

| | files | passed | failed | new reds vs base |
|---|---|---|---|---|
| base `cd8b7651` | 60 | 2220 | 14 | n/a |
| head, first pass (commits `56fc80f`-`fdc18e2`) | 60 | 2246 | 19 | 5: 1 in test_code_task_model_surface, 1 in test_coding_independent_review, 3 in test_folder_overview, all from (c) passing new arguments to a caller's `_fast_path_result` |
| head final `f57e48f` (34 direct files re-run) | 34 | all others pass | 14 | **0** (same 14 tests as base: code_task_model_surface 7, coding_independent_review 4, exact_count_survives_races 2, email_revision5 1) |

The 5 first-pass reds were fixed at the cause (`fdc18e2`, `f57e48f`: the lane keeps its own answer
call and records the read beside it). The tests were not edited. Ruff 0.17.2 on the whole repo: the
same 14 pre-existing findings on base and head, 0 new.

New focused tests: `tests/test_tool_cards_use.py` (30), each pinned to one cause on the real code
path; the cause tests fail with the matching core change reverted (11 of 14 for (a)/(b), the (c)
receipt test, the any-order find_folder test).

## Still broken / limits

- **Non-English folder asks are still refused.** fu07 (German "files on my Desktop"): the model now
  calls `machine.list_directory`, but no recognizer reads "Schreibtisch", and the card that matched
  was `find_largest`, so the admission proof refuses it. Left open on purpose: fu07 is an acceptance
  ask, so tuning on it here would not count as a fix.
- **Notes "any at all" searches the words.** fu13 (both sides): `find_notes` searched "any notes"
  and answered "No notes … match", though a note had been saved in the same home. The right tool
  ran and the answer is still wrong.
- **PDF on Linux and guessed paths.** `pdf.extract_text` reads through macOS PDFKit, so it cannot
  succeed on this box. fu09 also called it on a guessed path (`workspace/boarding_pass.pdf`) instead
  of finding `~/Downloads/boarding-pass-VNO-LIS.pdf` first.
- **Claim gate over-correction on a no-tool explanation (fu37, head).** The reply got "No calendar
  is configured…" and "No reminder … was set" lines. On the base reply and on three similar
  wordings, base and head gates behave identically: only a sentence like "guests can put it in their
  calendar and set a reminder" trips the existing calendar/reminder forms. The original model text
  was not retained, so I cannot rule out that my new memory form also took part.
- **More tool rounds cost time this box does not have.** Fixed folder asks now take 2-3 model calls
  (2-5 min each here), so fu01 and fu03 timed out after the right tool ran. The daemon keeps
  working after the harness gives up, and Ollama serves one request at a time, so the next turns
  queued behind it. That is how fu33 and fu05 failed their pre-answer model check on head. Base had
  no timeout, so no cascade. On a faster box (the kit expects 1.5-3 min per turn) these turns should
  fit, but that is not measured.
- **Acceptance purity.** fu01, fu03 and fu05 were diagnosed from base replies on the fresh set
  (the brief asks to read real replies first), so their head results are regression evidence, not
  blind acceptance. The (d) memory/contacts claims and the (c) receipt were found from code and the
  brief, and no reached fresh ask exercised them live.
- **Coverage.** 14 of 40 fresh asks per side, 0 two-turn asks, and none of the contacts or skills
  asks were reached. Run the remaining 26 on a faster box before trusting any rate. Box setup used
  on both sides: second Ollama stopped, nomic-embed-text pinned to 1 thread (`num_thread 1`), a
  first base attempt discarded after CPU contention from my own calibration (kept on file with
  its timeouts).
- **Record correction.** Commit `56fc80f` calls fu37 a "how do you say … in Lithuanian" ask; fu37
  is "what does 'save the date' mean on an invitation?" (the Lithuanian one is fu39, which releases
  no card). The fix and its numbers are unchanged.
- Not pushed anywhere but this branch; no PR; the MEASURE-ONLY commits stay local.
