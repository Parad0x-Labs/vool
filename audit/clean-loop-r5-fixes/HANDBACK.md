# CL-R5 hand-back: clean loop round-5 fixes

- Branch `delivery/clean-loop-08-r5-fixes-20261101`. Code head `dcf4e69` (this file is committed on
  top of it). Base `8c403f0b` (checked: an ancestor of `origin/delivery/clean-loop-08-20261031`).
- Case A: I continued from the earlier implementer's saved commit `42626cae` (untested and
  unreviewed), after auditing `8c403f0b..42626cae` (next section).
- Environment: Linux 6.18 x86_64 cloud container, Python 3.12.3. Tests ran in a scratch virtualenv
  with the repo's pinned `[dev]` extras, outside the repository; nothing was installed into the repo.
  There is no macOS PDFKit here.
- Only GitHub was available: no round-4/5 task files and no raw round-5 rows, so there is no miss
  table, and work followed section 4. No spending, no pull request.

## Outcome in one paragraph

C1, C2, C3, C4 and C6 are done, together with a memory fall-through.

**C5 and C7 were dropped.** The independent review's first pass found three problems, all fixed with
tests (`ba74968`). The re-run found a fourth (`99a823c`): a linked `~/Documents` was skipped while the
search still reported "complete". That finding is now fixed too, but the reviewer labelled it (b/c).
I took the safer reading of the rule "still FIX on (a) or (b) -> revert C7 and C5": C7 is reverted
(`db97121`) and C5 is dropped (`dcf4e69`).

Separately, with C7 on, a real default-on regression showed up:
`tests/test_private_chat_leaves_no_trace_v072.py::test_a_private_first_chat_leaves_no_trace_once_its_turns_end`
fails at `c8ec582` and passes at the base and at `dcf4e69`. Both switches are default OFF again, as
in the base.

## Audit of the saved work (42626cae)

| Finding | Action |
|---|---|
| Default-on (C7) was mixed into the same commits as C1-C6 | Backed out in `f58c92d`; re-added as its own commit `c8ec582`, later reverted |
| Hold-out committed first (`1a8939f` came before the fix `33c0dda`: yes). But two general asks were rewritten **after** the fix (in `42626ca`), because the base route admits them | Original wording restored; the real counts are below |
| The hold-out's fake home held only invented names, so no general ask could ever share a word with it | Added a realistic household, committed (`6a5f238`) before the matcher change. It showed the saved any-shared-word matcher added 10 general asks |
| Index read each document through the full authorized boundary (~50 ms per read). It kept the full text, cut at 3000 chars, with line numbers. 5,000 documents never finished in 120 slices | Reworked (C1) |
| One unopenable file (any PDF off macOS) made "content search complete" impossible forever | Fixed (C3) |
| Incomplete search with name hits hid the name hits | Fixed (`ba74968`) |
| `benchmark_index.py` wrote to a machine-local volume path | Now uses a temporary folder. It is a real measurement script, so it is kept |
| Two PDF tests required macOS PDFKit | Gated with the existing `needs_pdfkit` marker; two failure-path tests run everywhere |
| Notes mentioned a "PDF binary-fallback test expectation" and a "legacy no-result phrase" | No existing test was changed for either. `test_a_pdf_falls_back_to_a_plain_read_when_the_pdf_reader_fails` is byte-identical to the base |
| Notes claimed the hold-out went 40/40 out, 30/30 in | Not true with the pre-fix wording: 38/40 out (2 admitted by base rules) |

## Causes and fixes

| Cause | Fix | Files | Fail before (8c403f0) -> pass after (dcf4e69) | Safety risk |
|---|---|---|---|---|
| C1: personal questions never reach the loop | A bounded, in-memory document index (details in "Document index" below). A question is admitted when either: (1) it names something the owner's documents write as a name (capitalised at least as often as not, away from sentence starts, and in at most 30% of documents); or (2) it asks for one specific fact and shares two neighbouring words with a document. Checked last, after RED/write/owner-local/online, before the brain. `find` searches every indexed document, also when file names match | route.py, tools.py, hook.py, runtime_execution_tools.py | A document-only personal question: not admitted -> admitted. A document past the 80 newest: not found -> found. A fact past 3000 chars: not found -> found and shown at its line. Content search when 3+ names match: not searched -> searched | See "Document index" |
| C2: PDFs reported as unreadable | A failed PDF read now says "I found X but could not open its text on this computer (reason). Keep looking in other files." A PDF with no text layer is called scanned, never empty. Raw PDF syntax is never shown. A cut result says it was cut, and `read_file` takes `start_line`, or `from_page` + `max_pages` | tools.py, pdf_tools.py, runtime_execution_tools.py, runtime_tool_contracts.py | Before: "not available: Could not read …", with no file name or next step. After: the plain message. 6 tests; 2 more need PDFKit and are skipped here | None found |
| C3: confident "it isn't there" | An absence answer needs a complete content search in this turn; the loop nudges one more step first. Otherwise the reply is "I have not completed a search inside your files…". After a complete search, "isn't there" or "you don't have one" becomes "I didn't find it in your files." plus the model's own question. Unopenable, left-out, too-deep and linked folders make the search incomplete, and the reply says which | loop.py, tools.py | Before: "It isn't there." After: "I didn't find it in your files." | One extra model call, only when the model claims absence without searching |
| C4: follow-ups lose evidence | The previous clean-loop turn's tool results (latest first, at most 1,500 chars), per session, owner-local only, kept for the 900 s follow-up window | hook.py, loop.py | The base passes only the history text. After: a monthly figure is answered only from turn 1's read; another session and a non-owner turn get nothing | Evidence never leaves the owner's session |
| Memory questions | A memory-only fact (real memory store) with no document words is not admitted. A turn admitted only by document words that ends in an absence answer falls through to the normal pipeline (memory lane) | route.py, hook.py | 3 tests | — |
| C5: read + "can I email it" | **Dropped** after the review. The base rule stands: these asks stay RED. 20 mixed forms are pinned as RED | route.py, hook.py back to base | — | — |
| C6: archive name taken as its content | One prompt rule | prompt.py | prompt test | Prompt bytes changed (see below) |
| C7: switch on | **Reverted** (`db97121`) | — | private-chat test fails with C7 on | — |

## Independent review (fresh read-only subagent, twice)

**First pass: FIX ×3.** None of the three were on (a).
1. A media folder linking to a hidden home folder (`Pictures -> ~/.config`) was a read root, and the
   index showed a token from it to the model. The owning fix is in `_readable_machine_roots`: the link
   test now runs before resolving.
2. A walk cut short by depth or entry count reported "complete". The cut is now recorded and reported.
3. With the index cold, ordinary questions paid 1.7 s per turn. The route now spends at most 0.25 s
   and reuses a walk up to 20 s old; the rest is built off the turn.

**Re-run: FIX ×1 (b/c).** A linked `~/Documents` was skipped while the search said "complete". Fixed
in `99a823c`. Per the rule, C7 and C5 were dropped anyway (see the outcome paragraph above).

The re-run also confirmed: the earlier three are fixed; no bypass on (a) in 26 variants; hidden
folders, links, Library/AppData and VOOL's own data are skipped; the background thread starts only
after the owner-local check and never creates an approval request.

## Dev hold-out (my own; not acceptance)

**Fragment home** (committed first, original wording):
- General: **38/40 kept out**. The 2 admitted come from the **base** route rules, unchanged since
  round 5: "…what is my speed?" (ownership word "my") and "…a list and a tuple?" (topic word "list").
  Document words add 0.
- Personal: **26/30 admitted**.

**Realistic household** (24 everyday documents, read through the real boundary):
- General: **35/40 kept out**. All 5 admitted come from base rules: the 2 above, plus file-name and
  folder words "form" and "work". Document words add 0. The saved any-shared-word matcher had added 10.
- Personal: **25/30 admitted** (base rules alone: 14). The misses are names that only open a line or
  sit in a short title line, and word pairs that include a short word ("spare key").

**Bar check.** The 0/40 "kept out" bar is **not met**, because of base rules. Changing those rules was
outside this task; listed for sls.

**Memory.** A memory-only question is not admitted (pass). A memory fact whose name also appears in a
document IS admitted; if the loop finds nothing, the turn falls through to the pipeline (tested). How
often this happens in real use is unknown: there is no real memory data here.

## Document index

**Measured on 5,000 synthetic documents** (`audit/clean-loop-r5-fixes/benchmark_index.py` at
`dcf4e69`; the CPU was shared with other test runs):

| Measure | Result | Ceiling |
|---|---|---|
| Cold first turn, route time | **252 ms** | 2 s |
| Full build (route slice + background thread) | **9.5 s** | — |
| Warm decision p95, document-backed ask | **14.7 ms** | 300 ms |
| Warm decision p95, general ask | **8.7 ms** | 300 ms |
| Byte budget used | **2.6 MB of 48 MB** | — |

Limits: the walk itself has no time limit. The reviewer measured about 1 s at roughly 57k entries, and
0.34 s for a fresh walk 20 s after the last one.

**What it reads.**
- Only roots directly in the home: not hidden, not a link, not Library/AppData, not VOOL's own data.
  A visible home folder that is a link is reported, never walked.
- No hidden entries and no links anywhere in the walk.
- Files of at most 2 MB, with the same suffixes as `find`; depth 10; 60,000 entries.
- Every read is re-checked against those roots, against the machine reader's own confinement
  (`_resolve_machine_directory`, which also refuses VOOL's runtime state), and against its binary
  sniff. PDFs go through `pdf_tools.extract_text`.

**Permission.**
- One decision per slice per reader, from VOOL's authority (`authorize_runtime_tool`). This is the same
  decision every boundary read takes; read permission depends on the mode and the action class, not
  the path.
- Anything but a plain allow stops the index, and any parked approval request is withdrawn.
- Why not the full boundary per file: about 50 ms per read, so a 5,000-document cold build would take
  about 4 minutes. **This design choice is for the money/safety reviewer to confirm.**

**What it keeps.**
- Compact word strings only, never the text, in process memory. Nothing is written to disk (tested).
- At the budget it stops growing; it never evicts and re-reads.
- A document's text reaches the model only when `find` or `read_file` reads it through the boundary
  (receipted).
- A daemon thread finishes the build in 0.25 s slices, started only from an owner-local turn.
- Turns that are not owner-local, project turns, sub-turns, browser turns and RED turns never reach
  the index (tested).

## Tests and results (all at the stated commit, Python 3.12.3, this container)

**Fail-before / pass-after scenarios** (same script, base `8c403f0` vs head): six scenarios, all six
fail at the base and pass at the head:
1. A document past the 80 newest
2. A fact past 3000 chars
3. Content search when names match
4. The unopenable-PDF message
5. Absence wording
6. Document-only admission

**Test runs:**

| Command | Commit | Result |
|---|---|---|
| `python -m pytest -q -p no:cacheprovider $(grep -rl --include=*.py clean_loop tests)` | `dcf4e69` | **317 passed, 2 skipped** (the 2 need macOS PDFKit) |
| Read-root tests (13 files that reference `_readable_machine_roots` or the media folders) | `ba74968` | **476 passed** |
| Money and paid-step tests: the 39 files that import `core.clean_loop.brain` or the spend ledger, incl. `test_clean_loop_paid_step_ledger.py` | `1724b77` (C7 on) | **716 passed, 37 failed, 3 skipped** |
| Default-on regression: the 95 agent-turn test files (`make_agent`), 4 parallel chunks | `c8ec582` (C7 on) | Chunks 1-3: **1,906 passed, 8 failed**. Chunk 0 stalled at 11% (one test hung for ~25 min) and was stopped: its 24 files are untested |
| `uvx ruff@0.17.2 check core tests/test_clean_loop_*.py tests/test_clean_local_loop*.py audit/clean-loop-r5-fixes` | `dcf4e69` | **All checks passed** |

**The 37 money-test failures:**
- 28 are EVM libraries not installed here (`eth_account`; the `[evm]` extra).
- 3 are contacts checksum errors, also from the missing EVM extra.
- The same `test_r6_flows.py` ×3 and wallet failures also fail at base `8c403f0` (run: 10 failed,
  33 passed on those five files).
- `test_private_chat_leaves_no_trace_v072.py` ×1 is the **default-on regression**: it fails at
  `c8ec582` and passes at the base and at `dcf4e69` (4 passed).

**The 8 regression-run failures** are all network-seal tests in `tests/semantic_phase0/` (this
container's proxy). The same files fail at base `8c403f0`: 12 failed.

**Not run.** No full suite (CI's ten shards take up to 150 min each); an attempt on 795 files was
stopped at 1-2% after about 5 minutes. Known environment failures named in the task
(`solders`-dependent) were not separately re-checked.

## Tests changed mechanically (file, test, old -> new)

- `tests/test_clean_loop_speed_20261028.py::test_the_prompt_is_unchanged_by_the_speed_work`: pinned
  length 2336 -> 2564.
  - +125 is the C6 archive rule.
  - +103 is the `find` and `read_file` tool lines, which now name the repeat for an incomplete search
    and the `start_line` / `from_page` arguments that C2 needs.
  - That test's comment says an earlier prompt change cost accuracy (26/30 -> 24/30). **No model was
    available here to re-measure.** Round 6 (or a Mac re-run of that measurement) has to show
    whether this costs anything.
- The four default-off tests that C7 had changed are byte-identical to the base again after the C7
  revert.
- No other existing test changed (`git diff 8c403f0b..HEAD --stat -- tests`).

## Needs the money/safety review

- C5 and C7 are not on the branch (dropped and reverted).
- Still to review: the index's permission decision per slice, and its direct reads under the machine
  reader's confinement (above).
- The `_readable_machine_roots` link fix in `core/runtime_execution_tools.py` (it narrows read roots).

## Left for sls

- **Switching on (C7).** Before C7 can come back, the private-chat default-on regression needs a fix.
  A full CI run is also needed, which this session could not do.
- **C5** (read + "can I email it"), if wanted later: the code is in `42626ca`.
- **General hold-out 0/40.** Base route rules admit "my …" maths questions, the topic word "list",
  and file/folder-name words. Narrowing those changes round-5 admission, so it was not done here.
- **Codes.** Password, PIN and card-code questions stay RED (unchanged). A door/gate "code" question
  is not RED in the base (only password, PIN and card/security codes are) and was already admitted
  with "my"; document words can now admit it too. Making it RED is a rule change.
- **Write-root links.** `machine.read_file` / `list_dir` still follow a link from `~/Desktop`,
  `~/Documents` or `~/Downloads` into a hidden home folder. `_safe_machine_roots` has no link check;
  this is older than this branch, the write roots were not touched, and the index refuses such roots.
- **PDFs off macOS.** Every PDF is "could not open its text on this computer". The Linux reader on
  `delivery/linux-pdf-read-20261029` has not passed its security review and was not merged. If
  round 5 ran off macOS, that is the C2 cause; this session could not tell where round 5 ran.
- **C2 "keep the matching lines".** Not done; the cut says so, and `read_file` can continue.
- **Very large homes.** In a home bigger than the 48 MB budget, or deeper or wider than the walk
  bounds, the index is never complete, and absence answers say the search was not completed.
