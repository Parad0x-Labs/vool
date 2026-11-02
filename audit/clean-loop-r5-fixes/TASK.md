# TASK CL-R5: fix the clean tool loop's round-5 misses, then switch it on

Task id: CL-R5. Requested by sls_0x on 2026-11-01. Implementer: Codex. One delivery branch, no PR to main.

## 1. Goal

The clean tool loop (`core/clean_loop/`) is the 0.8 computer-task path. In the round-5 blind run
(45 new tasks, same answer model for every arm, blind jury) it scored:

VOOL clean loop @ eb957be2 (cloud brain flag on): 29/45 correct, 2 wrong-sure, 14 hedged, 0 unsafe,
0 files changed unasked, p50 17.6 s, about $0.0007 per answer. The two rival agents on the same
tasks: 24/45 and 21/45, each with 4 unsafe rows. Paired comparisons are direction only, not a firm
win (p 0.13 and 0.30).
Route decisions over the 45 tasks: admitted 40, `not_about_own_files` 7 turns, `red_turn` 2.
7 of the 16 misses never reached file reading at all.

Your job: fix the owning causes of the 16 misses so that more everyday questions get looked up in
the owner's files, without losing any of the safety record (0 unsafe, 0 unasked file changes), then
switch the loop on by default. A non-builder measures the result on a new blind set you never see.

## 2. Start

- Repo `https://github.com/Parad0x-Labs/vool`. Create branch `delivery/clean-loop-08-r5-fixes-20261101`
  from `delivery/clean-loop-08-20261031` at `8c403f0b` (round-5 code `eb957be2` plus one safety
  commit: a change never makes or moves into a hidden name). Check the sha before you start:
  `git fetch origin delivery/clean-loop-08-20261031 && git checkout -b delivery/clean-loop-08-r5-fixes-20261101 8c403f0b`.
  Commit this TASK.md as `audit/clean-loop-r5-fixes/TASK.md` as your first commit.
- Use Python 3.12 (`python3.12`). System Python 3.9 silently disables the paid-step reservation
  check, so any test run on 3.9 proves nothing about money paths.
- Record at start in `audit/clean-loop-r5-fixes/PROGRESS.md`: base sha, `git status`, python version,
  OS. Append a 4-line note every 15 minutes: Doing / Done / Still broken / ETA.
- Hard stop: 2 h 30 min after you start. Commit after each milestone so a stop loses nothing.
  Write the hand-back (section 8) before the stop even if unfinished.

## 3. Read first (all on the branch)

- `core/clean_loop/route.py` (admission: `decide`, `is_red`, `is_question`, `names_owner_file`)
- `core/clean_loop/tools.py` (`file_words`, `find`, `_text_candidates`, `_find_in_text`, `read_file`,
  `_call` and its 3000-char cut, answer gates)
- `core/clean_loop/loop.py` (`run_loop`, step cap 10, look-first nudge)
- `core/clean_loop/hook.py` (`maybe_answer`, `_history`: 6 turns, 800 chars each, text only)
- `core/clean_loop/prompt.py`, `core/clean_loop/brain.py`, `core/pdf_tools.py`
- Tests: `tests/test_clean_loop_*.py` (82 tests across 5 files today).

## 4. The misses, by owning cause (checked against the code at 8c403f0)

Counts per cause below are from the round-5 summary. Where the raw round-5 rows are available to
you (Mac variant of this task), first write a miss table in PROGRESS.md: task id, turn, route
reason, tools run, PDF status if any, final reply in 1 line, cause. Use it to correct the order
below. Where they are not available, say so and work from this section.

### C1. Personal questions never reach the loop (7 turns in 6 tasks: the biggest lever)
`decide()` admits a turn only if it has a topic word (`_TOPIC_RE`), an ownership word (`_OWNED_RE`),
is a short follow-up to a clean-loop turn, or shares a distinctive word with a FILE NAME
(`names_owner_file` / `tools.file_words`). Everyday questions about the owner's life ("when is
<relative>'s appointment", "what's the code for <place>", "when does <thing> run out") carry none of
these, so they go to the old pipeline, which answers without looking. The answer was in a document
whose name did not contain the word.

Owning fix (recommended): admit a question that shares a distinctive word with the TEXT inside the
owner's documents, not only with their names. Build a bounded, cached word index:
- Same readable roots and document types as `find` (`_readable_dirs`, `_TEXT_SUFFIXES`), the same
  skips (hidden entries, links, VOOL's own data folder, Library/AppData), size cap per file.
- Read through the same read tools as `_find_in_text` (so PDFs go through `pdf.extract_text`).
- Keyed by path + size + mtime_ns; held in memory only (never write owner text to disk); total
  byte budget; time budget per turn; a cold build that runs out of time uses what it has and
  finishes on later turns. No new model calls in the route.
- Checked after the RED / write / owner-local / online checks, before the brain is resolved.
- Use the same index in `find` so content search covers every indexed document, not just the 80
  newest (`_TEXT_MAX_FILES`) and not only when name hits are few (`_FEW_NAME_HITS`).
- Report: cold build time and warm route-decision time on a synthetic home with 5,000 documents.
  Ceiling: warm decision adds at most 300 ms at p95; a cold turn is never blocked more than 2 s.

Two regressions to guard:
- General chat must stay out. Write your own dev hold-out FIRST and commit it before the fix:
  `tests/test_clean_loop_admit_by_document_words.py` with 40 general asks (jokes, facts, how-to,
  definitions, maths, advice, coding, weather, news) that must NOT be admitted, and 30 everyday
  personal asks each backed by a document in a temp fake home that must be admitted. Your own
  people, places and topics: see section 5. Bar: 0/40 out, at least 24/30 in. This hold-out is dev
  evidence only (you wrote it); it is not acceptance.
- Facts the owner told VOOL in chat live in memory, which the loop cannot read. A question whose
  answer is only in memory must keep reaching the memory lane. Add a test: a fact stored in memory,
  no document holds its words, the question is not admitted. If your mechanism can admit a turn
  that memory would have answered, say how often in the hand-back and how you prevent it.

### C2. Text PDFs reported as unreadable (several hedged rows)
The model found the right PDF and then said it could not read the amount. Find out which of these
it is, from evidence, before fixing:
- `core/pdf_tools.extract_text` reads PDFs through the macOS system Python (PDFKit). Off macOS, or
  when that helper fails, `read_file` falls back to `machine.read_file`, which hands the model raw
  (often compressed) PDF bytes. Check where the round-5 VOOL arm ran and what status the PDF reads
  returned. Note: a Linux PDF fix exists on `delivery/linux-pdf-read-20261029` (pypdf in a
  bubblewrap sandbox) but has NOT passed its security review: do not merge it; name it in the
  hand-back if it is the cause.
- `_call` cuts every tool result at 3000 chars, and `_find_in_text` reads 5 pages: an amount past
  the cut is invisible.
Owning fix: the loop never shows the model binary noise. A failed PDF read says plainly "found
<name> but could not open its text on this computer (<reason>)"; a PDF with no text layer says it
is scanned, never that it is empty; and the model is told to keep looking in other files. If a long
document is cut, the result says it was cut and `read_file` can read a later part (e.g. a
`from` line or page argument), or the cut keeps the lines that match the ask's words. Test with a
real text PDF you generate in the test (with a compressed content stream, made with the standard
library), a no-text PDF, and a long one with the fact past 3000 chars.

### C3. Confident "it isn't there" for a file with a meaningless name
A scanner-style name (like `SCN_7713.pdf`) held the answer; the reply said nothing was saved. With C1's
index, `find` must search inside documents before the loop may say something is absent. Owning
fix: an absence answer is only allowed after a content search ran this turn; otherwise the loop
takes one more step to search inside. Wording when nothing is found: "I didn't find it in your
files", never "it isn't there" or "you don't have one". Tests for both sides (found by content;
not found worded as above).

### C4. Follow-ups lose the first turn's evidence
`hook._history` passes only the last 6 turns' text (800 chars each). Tool results are gone, so a
second turn ("and when's the next one due", "so how much is that a month") has no evidence and the
loop either re-searches badly or declines. Owning fix: keep the previous clean-loop turn's evidence
(paths read and the matching lines, bounded, e.g. 1,500 chars) per session, owner-local only, same
lifetime as the follow-up window, and pass it into the next turn. Never carry it into a remote or
phone turn or another session. Tests: a two-turn arithmetic follow-up answered from turn 1's
evidence; a follow-up in another session gets nothing.

### C5. A read question blocked because it also mentions sending (1-2 turns)
"how big is <file>, can I email it" is RED as a whole (`_OUTWARD_RE` in any form), so the size
part is never answered. Allowed fix, and only this: when every RED match in a turn is an outward
verb inside an ability/permission question about the owner themself ("can I email it", "could I
send it"), and the rest is a read question, admit the turn; the loop answers the read part, has no
send tool, and the answer ends with a fixed line that sending needs the owner to ask for it and
approve it. Everything else stays RED exactly as today: money words, destructive verbs, "can you
send", orders after a question, permission forms aimed at VOOL. Every existing RED test must still
pass unchanged. Mark this commit "needs money/safety review" in the hand-back. If you are not sure
a case is safe, leave it RED and list it.
Secrets stay out: password, PIN and code questions stay RED (they are never read into a model).
List any miss caused by that under "left for sls"; do not change it.

### C6. Archive name taken as its content
The model described a zip's contents from its name. One rule in `prompt.py`: an archive's name is not
its content; list it or say it was not opened. `tests/test_clean_loop_speed_20261028.py` pins the
prompt bytes: update only its expected bytes/length and say so in the commit message.

### C7. Switch on (last, separate commit)
`VOOL_CLEAN_LOCAL_LOOP` (route.py `FLAG_ENV`) and `VOOL_CLEAN_LOOP_CLOUD` (brain.py `CLOUD_ENV`)
default ON when unset; `0/false/no/off` turns each off. `VOOL_CLEAN_LOOP_CHANGES` stays default OFF.
Tests for each default and each explicit off. Existing tests that asserted the old default: fix
mechanically (set the env explicitly), keep what they check, list each one (file, test, old -> new).
This commit stays separate so it can be dropped if the review or round 6 says no.

## 5. Rules (any breach rejects the delivery)

- No word from any evaluation task in code, tests, fixtures or prompts. You may read the round-4
  and round-5 task files to diagnose causes (Mac variant), but your tests use your own invented
  people, places, file names and amounts. The reviewer greps your diff against the round-4 and
  round-5 task texts and fixture names; any distinctive hit rejects the delivery.
- Never open the round-6 set (`missions/bench-win-20261028/round6-set/` or anything named round6):
  it is the acceptance set.
- No topic lists or regex additions built from the misses ("appointment", "code", "expiry" style
  word lists). The fix is a mechanism (document index, evidence carry, PDF status), not words.
- Money and file changes stay gated: nothing in the loop pays, sends, deletes or changes a file
  without VOOL's card and the owner's yes. Do not loosen `_MONEY_RE`, `_DESTRUCTIVE_RE`, the
  permission forms, the order-after-question rule, the paid-step reservation (brain.py
  `_reserve_step`) or the hidden-name refusal in `changes.py`. If an existing safety test fails,
  your change is wrong.
- Never weaken, skip or delete a test. Only the mechanical updates named in C6 and C7.
- No stubs or mocks standing in for the behaviour you claim. The fake home in tests is real files.
- Every change works with a local model too (no cloud-only path). No extra model calls per turn
  unless a test shows it is needed, and then report the token and latency cost.
- No new dependency, no installs, no network calls in tests.
- Commit as `git -c commit.gpgsign=false -c user.name=sls_0x -c user.email=240776969+Parad0x-Labs@users.noreply.github.com commit`.
  No AI attribution or Co-Authored-By lines. No machine-local paths, keys, tokens or personal names
  in anything you commit.
- No push to main, no force-push, no PR to main, no branch deletes, no spending.

## 6. Tests to run

1. Each fix: a test that fails before and passes after (show both), plus the touched modules' tests.
2. `python3.12 -m pytest -q -p no:cacheprovider $(grep -rl --include=*.py clean_loop tests)`
3. Money and paid-step tests: `tests/test_clean_loop_paid_step_ledger.py` plus any test file that
   imports `core.clean_loop.brain` or the spend ledger.
4. Lint: `uvx ruff@0.17.2 check core tests` (or the repo's pinned ruff if uvx is not available).
5. Full suite only if your environment allows it (Mac: no full suite; 20 min cap on shards).
   Known environment failures on the base: `test_final_answer_authorship_served_p0` and
   `test_media_product_served` (missing `solders`). Prove any other failure is pre-existing by
   running it on 8c403f0.

## 7. Acceptance (decided by others, after you hand back)

- An independent read-only safety review of `git diff 8c403f0..<your head>`: any way a money
  action, a send, a delete or a file change passes without VOOL's card; any way the document index
  reads outside the readable home, follows links, reads VOOL's data, writes owner text to disk, or
  leaks it to a remote/phone turn or a non-owner; any default-on regression. A FIX on these drops
  C7 (and C5 if it is the cause).
- Blind round 6 (new household, 45 tasks, written by someone who never saw VOOL code) run by a
  non-builder on BOTH 8c403f0 (control) and your head, same model, same jury. Bars: unsafe 0, files
  changed unasked 0, your head beats the control on paired rows, p50 latency at most +25%, cost per
  answer at most 1.5x.
- Round 5 re-run on your head is regression only (must not drop below 29/45 or add an unsafe row).
- Your own hold-out numbers are reported but never count as acceptance.

## 8. Hand-back

`audit/clean-loop-r5-fixes/HANDBACK.md`, committed on the branch (also in the mission folder on the
Mac variant). Short and plain:
- branch, head sha, base sha 8c403f0, python version, OS; bundle path + sha256 (Mac variant)
- table: cause, fix, files, tests added (fail-before / pass-after evidence), safety risk
- dev hold-out: x/40 general kept out, y/30 personal admitted; memory-question test result
- document index: cold build time and warm decision p95 on 5,000 documents, byte budget used
- test commands and results with counts; which failures are pre-existing (with proof)
- tests changed mechanically (file, test, old -> new)
- commits that need the money/safety review (C5 if done, C7)
- left for sls: anything that would need a safety rule loosened, plus what you could not finish
