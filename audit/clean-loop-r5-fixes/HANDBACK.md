# CL-R5 hand-back: clean loop round-5 fixes

- Branch: `delivery/clean-loop-08-r5-fixes-20261101`. Head: the commit that adds this file (code head
  `__CODEHEAD__`). Base: `8c403f0b` (ancestor of `origin/delivery/clean-loop-08-20261031`, checked).
- Case A: continued from the earlier implementer's saved commit `42626cae` (untested, unreviewed),
  after auditing `8c403f0b..42626cae` (section "Audit of the saved work").
- Environment: Linux 6.18 x86_64 cloud container, Python 3.12.3 (scratch venv with the repo's pinned
  `[dev]` extras; nothing installed into the repository). No macOS PDFKit here. GitHub only: the
  round-4/5 task files and raw round-5 rows were not available, so there is no miss table; work
  followed section 4. No spending, no PR.

## Audit of the saved work (42626cae)

| Finding | Action |
|---|---|
| Default-on (C7) was mixed into the same commits as C1-C6 | Backed out in `f58c92d`; returns as its own commit `c8ec582` |
| Hold-out committed first (`1a8939f` before the fix `33c0dda`: yes), but two general asks were rewritten **after** the fix (`42626ca`) because the base route admits them | Original wording restored. Real count reported below |
| The hold-out's fake home held only invented names, so no general ask could share a word with it | Added a realistic household, committed (`6a5f238`) before the matcher change; it showed the saved any-shared-word matcher added 10 general asks |
| Index read every document through the full authorized boundary (~50 ms per read: a 5,000-document cold build never finished in 120 slices) and kept full text, cut at 3000 chars, with line numbers | Reworked (C1 below) |
| An unopenable file (any PDF off macOS) made "content search complete" impossible, so every absence answer became "I can't say" forever | Fixed (C3 below) |
| `benchmark_index.py` wrote to a machine-local volume path | Uses a temporary folder; it is a real measurement script, kept |
| Two PDF tests required macOS PDFKit and failed here | Gated with the same `needs_pdfkit` marker the existing PDF tests use; two failure-path tests run everywhere |
| Its notes: "PDF binary-fallback test expectation" and "one legacy no-result phrase" | No existing test was changed for these: `test_a_pdf_falls_back_to_a_plain_read_when_the_pdf_reader_fails` is byte-identical to base; the no-result text keeps "nothing found for:" after the new "I didn't find it in your files." |
| Its notes: hold-out 40/40 out, 30/30 in | Not reproducible as stated: with the pre-fix wording, 38/40 out (base rules); see below |

## Causes and fixes

| Cause | Fix | Files | Tests (fail before on 8c403f0 / pass after) | Safety risk |
|---|---|---|---|---|
| C1 personal questions never reach the loop | Bounded in-memory document index; a question is admitted when it names something the owner's documents write as a name (capitalised at least as often as not away from sentence starts, in at most 30% of documents) or asks for one specific fact sharing two neighbouring words with a document. Checked last, after RED/write/owner-local/online, before the brain. `find` searches every indexed document, also when names match | route.py, tools.py, hook.py | `test_clean_loop_admit_by_document_words.py`, `test_clean_loop_r5_document_index.py`. Before: document-only question not admitted; a doc past the 80 newest, a fact past 3000 chars, content when 3+ names match: not found. After: all found/admitted | Index reads: see "Document index" |
| C2 PDFs reported unreadable | A failed PDF read says "I found X but could not open its text on this computer (reason). Keep looking in other files."; no text layer says "appears scanned … not empty"; raw PDF syntax is never shown; a cut result says it was cut and `read_file` takes `start_line` / `from_page` + `max_pages` | tools.py, pdf_tools.py, runtime_execution_tools.py, runtime_tool_contracts.py | `test_clean_loop_r5_pdf_and_evidence.py` (2 of 8 need PDFKit: skipped here). Before: "not available: Could not read …" with no name/next step | None found |
| C3 confident "it isn't there" | An absence answer needs a complete content search this turn (one extra step is nudged first); otherwise "I have not completed a search inside your files…". After a complete search "isn't there / you don't have one" becomes "I didn't find it in your files." (+ the model's question). Unopenable / left-out documents are reported, not waited on | loop.py, tools.py | Before: answer "It isn't there." After: "I didn't find it in your files." | Extra model call only when the model claims absence without searching |
| C4 follow-ups lose evidence | Previous clean-loop turn's tool results (latest first, ≤1,500 chars) per session, owner-local only, follow-up window (900 s), passed into the next turn | hook.py, loop.py | `test_clean_loop_r5_follow_up_evidence.py`: monthly figure answered only from turn 1's read; other session and non-owner turn get nothing | Evidence never leaves the owner's session |
| Memory questions | Memory-only fact (real memory store) with no document words: not admitted. A turn admitted only by document words whose loop ends in absence falls through to the normal pipeline (memory lane) | route.py, hook.py | same file, 3 tests | — |
| C5 read + "can I email it" | (saved work) Admitted only when every RED match is the owner's own "can/could I email/send it" and the rest is a read question; the answer ends "Sending needs you to ask for it and approve it first." | route.py, hook.py | `test_clean_loop_r5_switches_and_mixed_read.py`, 16 other forms pinned RED | **Needs money/safety review** |
| C6 archive name taken as content | One prompt rule | prompt.py | prompt test | — |
| C7 switch on | `VOOL_CLEAN_LOCAL_LOOP`, `VOOL_CLEAN_LOOP_CLOUD` default ON; 0/false/no/off off; `VOOL_CLEAN_LOOP_CHANGES` stays OFF | route.py, brain.py | switches tests | **Needs review**; separate commit `c8ec582` |

## Dev hold-out (my own; not acceptance)

- Fragment home (committed first, original wording): general **38/40 kept out**, personal **26/30 admitted**.
  The 2 general asks admitted are admitted by the **base** route rules, unchanged since round 5:
  "…what is my speed?" (ownership word "my") and "…a list and a tuple?" (topic word "list").
  Document words add 0.
- Realistic household (24 everyday documents, read through the real boundary): general **35/40 kept
  out** — all 5 by base rules (the 2 above plus file-name words "form", "work" and folder words);
  document words add 0 (the saved any-shared-word matcher added 10). Personal **25/30 admitted**
  (base rules alone: 14). Misses: names only at a line start or in a short title line, and pairs with a
  short word ("spare key").
- Bar "0/40 out" is **not met**, because of base rules; changing them was out of scope (left for sls).
- Memory: a memory-only question is not admitted (pass). A memory fact whose name also appears in a
  document IS admitted; if the loop finds nothing it falls through to the pipeline (tested). How often
  this happens in real use is unknown (no memory data here).

## Document index

- 5,000 synthetic documents (`audit/clean-loop-r5-fixes/benchmark_index.py`, this container):
  cold build **8.1 s over 5 turns** (max slice **1.71 s**, under the 2 s ceiling), warm route
  decision p95 **95 ms** (admitted) / **89 ms** (general, not admitted), ceiling 300 ms. Byte budget
  used **5.97 MB of 48 MB**.
- What it reads: the readable roots (`_readable_machine_roots`), no hidden entries, no links (files or
  folders), no Library/AppData, not VOOL's own data folder, ≤2 MB per file, the same suffixes as
  `find`. Each read re-checks the machine reader's own confinement (`_resolve_machine_directory`,
  which also refuses VOOL's runtime state) and binary sniff; PDFs go through `pdf_tools.extract_text`.
- Permission: one decision per slice per reader from VOOL's authority (`authorize_runtime_tool`, the
  same decision every boundary read takes; read permission depends on mode and action class, not path).
  Anything but a plain allow stops the index; a parked approval request is withdrawn. Why not the full
  boundary per file: ~50 ms per read, so 5,000 documents would be a ~4-minute cold build. **Reviewer:
  please check this choice.**
- Keeps compact word strings only (no text) in process memory; nothing is written to disk (tested);
  stops growing at the budget instead of evicting and re-reading; a document's text reaches the model
  only when `find`/`read_file` reads it through the boundary (receipted).
- Not owner-local, project, sub-turn, browser and RED turns never reach the index (tested).

## Tests and results

__RESULTS__

## Tests changed mechanically (file, test, old -> new)

- `tests/test_clean_local_loop.py::test_route_is_off_unless_the_flag_is_set` -> renamed
  `test_route_is_off_when_the_flag_is_explicitly_disabled`: `env={}` -> `env={FLAG_ENV: "0"}` (C7).
- `tests/test_clean_local_loop.py::test_the_hook_is_inert_with_the_flag_off`: `delenv(FLAG_ENV)` ->
  `setenv(FLAG_ENV, "0")` (C7).
- `tests/test_clean_local_loop.py::test_the_cloud_brain_is_off_by_default` -> renamed
  `test_the_cloud_brain_is_off_when_explicitly_disabled`: `delenv(CLOUD_ENV)` -> `setenv(CLOUD_ENV, "0")` (C7).
- `tests/test_clean_local_loop_turn.py::test_with_the_flag_off_the_turn_never_reaches_the_loop`:
  `delenv(VOOL_CLEAN_LOCAL_LOOP)` -> `setenv(VOOL_CLEAN_LOCAL_LOOP, "0")` (C7).
- `tests/test_clean_loop_speed_20261028.py::test_the_prompt_is_unchanged_by_the_speed_work`: pinned
  length 2336 -> 2564 (+125 the C6 archive rule, +103 the `find` and `read_file` tool lines, which now
  name the incomplete-search repeat and the `start_line` / `from_page` arguments C2 needs).
- No other existing test changed (checked: `git diff 8c403f0b..HEAD --stat -- tests`).

## Needs the money/safety review

- C5 (`_read_plus_send_permission` in route.py, the fixed line in hook.py), from the saved work.
- C7 `c8ec582` (default ON).
- The index's permission-per-slice reads (above).

## Left for sls

- General hold-out bar 0/40: base route rules admit "my …" maths questions, the topic word "list",
  and file/folder-name words ("form", "work"). Narrowing them changes round-5 admission; not done.
- Password/PIN/card-code questions stay RED (unchanged); any miss caused by that is left as is.
  Note: a door/gate "code" question is not RED in the base (only password, PIN and card/security
  codes are) and was already admitted with "my"; document words can now admit it too ("what's the
  gate code at <place in a document>"). Making it RED is a rule change for sls to decide.
- A Linux PDF reader: off macOS every PDF is "could not open its text on this computer". The fix on
  `delivery/linux-pdf-read-20261029` has not passed its security review and was not merged. If round 5
  ran off macOS, that is the C2 cause; this session could not tell where it ran.
- C2 "keep the lines that match": not done; the cut says so and `read_file` can continue.
- In a home bigger than the 48 MB budget the index stops growing; absence answers there say the search
  was not completed. No full-suite run (see results).
