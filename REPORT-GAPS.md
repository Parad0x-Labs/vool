# Tool cards: the three gaps left open (job tool-cards-gaps-20261030)

Branch `delivery/tool-cards-gaps-20261030`, from `delivery/tool-cards-use-20261030` at `ef76cd47`
(checked before starting). No PR. No model calls and no API keys: every model turn in the tests is
scripted. Python 3.12.3 venv with pytest, zstandard, psutil and pynacl, plus the runtime packages
from `pyproject.toml` `dependencies` that the agent needs to import (pyyaml, cryptography, pydantic
and the others). Not the full requirements.txt. Ruff 0.17.2 passes on every changed file.

| Fix | Commit(s) |
|---|---|
| 1 folder names in every app language | `bfd2ed2`, follow-up `825c2ec` |
| 2 notes query scaffolding | `21f6798` |
| 3 guessed file path | `c9bc2c2`, follow-up `f168fa2` |
| this report | the commit that adds it |

Two fixes have a second commit, so "one commit per fix" holds for 2 only. The follow-ups were found
after the first commits had been pushed. No force-push was used, so they are separate commits:
- `825c2ec`: on a Linux box with localized XDG folders, `~/Desktop/<sub>` (the English spelling
  the lanes and the model use) now lands in the localized root.
- `f168fa2`: when the lookup finds a file the reader then fails to open, the reply no longer
  says "and read that".

Acceptance purity: fu07, fu09 and fu13 were read only to know what to keep out. Their wording
("Welche Dateien liegen auf meinem Schreibtisch", "boarding pass", "do I have any notes saved at
all") is not in any test or word list. The phrasings in the three new test files were written for
those files.

## 1. Folder asks in other languages

**Cause** (file:line at `ef76cd47`). Each lane had its own copy of the English names, and nothing
read any other language:
- `core/runtime_execution_tools.py:164` and `:4775`: the roots were `home / "Desktop"|"Downloads"|"Documents"`.
  The alias map at `:4817` had English names only, so a model argument like `Schreibtisch` or
  `~/Schreibtisch` was refused.
- `core/agent_runtime/agent.py:1340`: `_owner_folder_demand`, the admission proof. It reads only
  the English live-source recognizer (`core/tool_demand_signals.py:1908`), the folder grammar
  and the cards.
- `core/tool_card_router.py:100`: `_stems`. The stop-word list (`core/plugin_skills.py`) is English
  only. **This is why a plain listing ask matched `find_largest`:** "Welche Dateien sind auf dem
  Desktop" shares `welch` + `dateien` + `sind` with find_largest's example "welche Dateien sind am
  größten" (score 0.62–0.66). Two of those three words are function words. The listing card shared
  only "desktop", and one shared word scores 0.
- `core/agent_runtime/intent_claims.py:90`, `core/agent_runtime/fast_paths_machine.py:465` and
  `:1326`/`:1351`, the write-root module's `_SAFE_MACHINE_ROOT_NAMES` (line 117, read at 162),
  `core/execution/planner.py:1413`:
  the English names were hardcoded in each of these.

**Fix.** One table in `core/safe_folder_names.py`, now read by all of the places above:
- **Spoken names** for the three roots in all 17 app languages (the `core/i18n/catalogs` set, and
  a test enforces that). It includes inflected forms, plus spellings without accents where people
  type them.
- **Disk folders.** macOS and Windows keep the English folders. Linux reads `XDG_DESKTOP_DIR`,
  `XDG_DOWNLOAD_DIR` and `XDG_DOCUMENTS_DIR` from `~/.config/user-dirs.dirs` and falls back to the
  English name.
- **The roots never grow.** An XDG entry is ignored (English fallback) if it is any of these:
  `$HOME` itself, outside home, a hidden folder, a relative path, or a symlink out of home.
- **Card match.** Folder words fold to one token. Function words from the other languages no
  longer count as content.
- **Admission and live-source seat.** A root named in any language counts when it comes with a
  question or a file/folder noun.
- **English-grammar lanes** (fast path, planner, intent claims) leave out words that are also
  English words ("bureau", "pulpit").
- **Replies.** A listing named in another language names the folder as the person said it, and
  never prints a full path. Remote turns are refused as before.

**Old → new** (same probe script, base worktree vs head):
- `_owner_folder_demand("Pokaż pliki na pulpicie", owner)`: `False` → `True`.
- `match_cards("Welche Dateien sind auf dem Schreibtisch")`: `[find_largest 0.638]` → `[]`. The
  listing is seated by the owner live-source "files" arm.
- `machine.list_directory {"path": "Schreibtisch"}`: `not_allowed: I can only inspect safe local
  directories…` → ``Visible entries under `Schreibtisch`: - reise-plan.txt``.
- Full scripted turn: the model calls `machine.list_directory {"path": "Schreibtisch"}` for a German
  ask. The reply is the real listing. Before, it was the executor refusal.

**Tests**: `tests/test_safe_folders_in_every_app_language.py`
- 134 passed on head.
- On `ef76cd47` with only the table module copied in, 65 of 133 fail: every wiring test. The
  table tests and controls pass there.
- Coverage: 29 own asks across all 16 non-English languages; 5 control sentences that must not
  change; 7 widening XDG entries; the remote-turn refusal; English controls; one full turn.

**Not covered**
- Other-language asks that name no file/folder noun and are not a question are not seated (a
  German imperative with no noun). The admission proof also accepts a bare question.
- French "bureau" and Polish "pulpit" count only next to a word of their own language ("sur mon
  bureau", "na pulpit", a French/Polish file noun).
- An advice question in another language (how do I organise …) is seated with the file tools.
  The English advice veto does not read it. The model still decides.
- Write-lane grammar ("create a folder X on my desktop") is still English-verb only.
- The write-root module's `_is_reachable` judges `~/Desktop/x` literally. On a localized Linux box that
  path is now not under the roots.
- Windows redirected known folders (OneDrive) are not read.
- With the embedder up, meaning scores were not measured: all card tests here are word-only
  (`semantic="off"`).

## 2. "Do I have any notes" searched the words

**Cause**:
- `core/operator/notes.py:257`: `search_notes` requires every term.
- `core/operator/notes.py:676`: `find_notes_query` returned a model's `query` argument
  (`core/execution/operator_tools.py:164` → `target_label`) untouched.
- `core/operator/parser.py:700`: `_note_query` kept the filler.
- So `{"query": "any notes"}` looked for "any" and "notes" together.
- The empty-query listing (`notes.py:697`) was sorted by file name and gave no total beyond the
  rows shown.

**Fix**:
- `notes_search_topic` drops the words of the ask itself: note nouns, quantifiers, articles, the
  asking frame. A quoted topic is kept as quoted.
- One reading for the model argument, the request text and the chat parser's query. Dropping a
  filler word can only widen an all-terms search.
- No topic: the newest 10 notes first, with the total ("Notes in this workspace (14), newest 10
  first: … …and 4 older.").
- A topic searches that topic alone, and a miss names the topic.
- Read only: no saving, no memory store, no memory laws touched.

**Old → new**:
- `operator.find_notes {"query": "any notes"}`: `No notes in this workspace match 'any notes'.` →
  `Notes in this workspace (1), newest first: - Plumber […]`.
- `{"query": "any notes about the dentist"}`: `…match 'any notes about the dentist'.` →
  `…match 'dentist'.`

**Tests**: `tests/test_notes_ask_words_are_not_the_topic.py`
- 77 passed on head; 73 fail on `ef76cd47`.
- The 4 that pass on base are controls: empty workspace, remote turn, nothing written, and a
  bare-topic call.
- Coverage: 21 no-topic phrasings and 10 topic phrasings, each through the real executor; the
  parser; the remote-turn refusal; a check that the search writes nothing.

**Not covered**
- English only: a notes ask in another language keeps its words.
- A time window ("notes from last week") is read as the topic word "week". The search is by
  text, not by date.
- The listing line still shows each note's file path, as before.

## 3. A guessed file path

**Cause**:
- `core/runtime_execution_tools.py:3454` / `:3465` / `:3606`: `machine.read_file`, `pdf.*` and
  `workspace.read_file` each stat one path and stop.
- `core/pdf_tools.py:159`: answers "no file at …".
- The `machine.find_file` walk existed (`runtime_execution_tools.py:1224`), but only inline.

**Fix**:
- The walk is now one function, `_walk_safe_roots_for_files`. `machine.find_file` uses it
  unchanged.
- `_guessed_path_lookup` runs in the dispatch, after every fence, for `pdf.extract_text`,
  `pdf.ocr`, `machine.read_file` and `workspace.read_file`, when the path does not exist the way
  that reader resolves it.
- The guessed name's words go through the walk: same safe roots, skip list, ceilings, hidden and
  other-project rules. The remote-turn fence is checked again for the lookup.
- A match must have the guessed extension and really sit inside a root, so a symlink out is
  dropped.
- **One match:** read and named home-relative. A text file goes to the safe-root reader.
- **Several:** up to 5 listed, then a question.
- **None:** the reader's own answer, plus where it looked.
- A path that exists reads exactly as before. The PDF backend is unchanged.

**Old → new**:
- `pdf.extract_text {"path": "workspace/car_insurance_policy.pdf"}`, file at
  `~/Downloads/Car-Insurance-Policy-2026.pdf`: ``not_found: Could not read
  `workspace/car_insurance_policy.pdf`: no file at …`` → ``There is no
  `car_insurance_policy.pdf`; I found `~/Downloads/Car-Insurance-Policy-2026.pdf`, but could not
  read it.`` On this Linux box the PDF backend is missing (`missing_dependency`).
- A text guess (`wedding_guest_list.txt` → `~/Documents/Wedding-Guest-List-Final.txt`) is read
  and named.

**Tests**: `tests/test_a_guessed_read_path_is_looked_up.py`
- 34 passed on head; 28 fail on `ef76cd47`.
- The 6 that pass on base are controls: existing path, symlink out, hidden folder, three remote
  turns.
- Coverage: 22 guessed paths over 4 readers; several matches, and more than 5; none; wrong
  extension; the remote-turn refusal at the door and inside the lookup.

**Not covered**
- PDF text is not proven on Linux: the backend belongs to the other thread. The PDF cases check
  which file the reader was pointed at.
- The lookup searches the three roots only, not the bound workspace.
- No live model turn was run to see whether a model, given the candidate list, picks one and
  calls again.

## Tests: BASE `ef76cd47` vs HEAD

Each test file was run in its own pytest process. BASE ran in a clean worktree at `ef76cd47`.
HEAD ran in two clean worktrees working from the two ends of one list: `c9bc2c2` (fixes 1–3) and
`825c2ec` (plus the fix-1 follow-up). `f168fa2` changes one reply string in the lookup; on it,
the fix-3 file passes 34/34, and the other files were not re-run. The list holds 109 files:
- the 3 new files;
- 17 notes/PDF files;
- 89 related folder, find-file, notes, intent-claims, remote-fence, card, read-file and
  write-root files: chosen by file name, plus test files that grep shows referencing the changed
  functions or modules.

BASE numbers for the new files come from the checks per fix above, not from this table.

| file | BASE ef76cd47 | HEAD | head commit |
|---|---|---|---|
| `tests/test_safe_folders_in_every_app_language.py` | not run | 133 passed | c9bc2c2 |
| `tests/test_notes_ask_words_are_not_the_topic.py` | not run | 77 passed | c9bc2c2 |
| `tests/test_a_guessed_read_path_is_looked_up.py` | not run | 34 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_c3_notes_request_identity.py` | 7 passed | 7 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_notes_vertical.py` | 7 passed, 1 skipped | 7 passed, 1 skipped | c9bc2c2 |
| `tests/pa_beta_gate/test_pc_notes_completion.py` | 3 passed | 3 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_pc_notes_identity.py` | 4 passed, 1 skipped | 4 passed, 1 skipped | c9bc2c2 |
| `tests/pa_beta_gate/test_served_calendar_notes_workflows.py` | 3 passed | 3 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_served_notes_folder_scope_routing.py` | 26 passed | 26 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_served_notes_scope_between_requests.py` | 15 passed | 15 passed | c9bc2c2 |
| `tests/pa_beta_gate/test_served_notes_scope_reading.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v5_notes_delivery_operations.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v6_native_notes_outcome_certainty.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v6_notes_journal_semantics.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v7_notes_receipt_identity.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v7_notes_receipt_restart.py` | not run | not run |  |
| `tests/pa_beta_gate/test_v7_served_notes_receipt_identity.py` | not run | not run |  |
| `tests/test_a_named_pdf_reaches_the_pdf_tools.py` | not run | not run |  |
| `tests/test_bound_attachment_pdf_routing.py` | not run | not run |  |
| `tests/test_pdf_scanned_multipage.py` | not run | not run |  |
| `tests/effect_budget/test_door_command_machine.py` | 3 failed, 3 passed | not run |  |
| `tests/foundation/test_r9c_fetch_gate.py` | 2 passed | not run |  |
| `tests/gauntlet/test_contract_map_golden.py` | 5 passed | not run |  |
| `tests/gauntlet/test_memory_and_needle.py` | 22 passed | not run |  |
| `tests/pa_beta_gate/test_calendar_notes_intent_parsing.py` | 40 passed | not run |  |
| `tests/pa_beta_gate/test_served_notes_precedence_residuals.py` | 54 passed, 3 xfailed | not run |  |
| `tests/pa_beta_gate/test_v5_ingress_ownership_units.py` | 21 passed | not run |  |
| `tests/pa_beta_gate/test_v5_request_words_not_path_words.py` | 15 passed | not run |  |
| `tests/test_a_file_can_be_found_by_name.py` | 10 passed | not run |  |
| `tests/test_a_hardware_word_is_not_a_hardware_question.py` | 57 passed | not run |  |
| `tests/test_a_machine_clause_binds_the_machine_capability.py` | 7 passed, 4 warnings | not run |  |
| `tests/test_a_machine_clause_is_visible_to_the_arbitration.py` | 6 passed | not run |  |
| `tests/test_a_near_miss_never_guesses_a_machine_read.py` | 51 passed | not run |  |
| `tests/test_a_specs_word_is_not_a_specs_question.py` | 38 passed | not run |  |
| `tests/test_a_topic_word_is_not_a_runtime_question.py` | 42 passed | not run |  |
| `tests/test_a_typo_still_reaches_the_tool.py` | 128 passed | not run |  |
| `tests/test_a_url_is_not_a_local_file_path.py` | 52 passed | not run |  |
| `tests/test_archive_counts_files_not_folders.py` | 2 passed, 1 skipped | not run |  |
| `tests/test_audit_machine_tools.py` | 3 passed | not run |  |
| `tests/test_bare_machine_specs_reaches_the_tool.py` | 39 passed | not run |  |
| `tests/test_bound_attachment_display_arbitration.py` | 11 passed | not run |  |
| `tests/test_build_lane_qa_gauntlet.py` | 63 passed | not run |  |
| `tests/test_chat_mutation_endpoints_owner_local.py` | 7 passed | not run |  |
| `tests/test_effect_gate_a1.py` | 17 passed | not run |  |
| `tests/test_email_revision5_corrections.py` | 1 failed, 84 passed | not run |  |
| `tests/test_expand_family_offer_order.py` | 5 passed | not run |  |
| `tests/test_expansion_drain_is_owner_local.py` | 3 passed | not run |  |
| `tests/test_expansion_seats_the_family_in_full.py` | 1 failed, 3 passed | not run |  |
| `tests/test_explicit_no_action_policy.py` | 12 passed | not run |  |
| `tests/test_fast_path_gate_parity.py` | 26 passed | not run |  |
| `tests/test_find_named_file.py` | 120 passed | not run |  |
| `tests/test_folder_audit.py` | 6 passed | not run |  |
| `tests/test_folder_overview.py` | 11 passed | not run |  |
| `tests/test_general_knowledge_is_not_a_workspace_read.py` | 62 passed | not run |  |
| `tests/test_history_machine_request_targets.py` | 6 passed | not run |  |
| `tests/test_history_reported_instruction_boundaries.py` | 12 passed | not run |  |
| `tests/test_history_request_clause_limits.py` | 6 passed | not run |  |
| `tests/test_history_requested_action_grammar.py` | 76 passed | not run |  |
| `tests/test_intent_arbiter_dispatch.py` | 10 passed | not run |  |
| `tests/test_intent_claims.py` | 5 passed | not run |  |
| `tests/test_live_data_coverage_note.py` | 12 passed | not run |  |
| `tests/test_machine_fast_path_events.py` | 5 passed | not run |  |
| `tests/test_machine_find_folder_paths.py` | 11 passed | not run |  |
| `tests/test_machine_find_folder_phrasings.py` | 23 passed | not run |  |
| `tests/test_machine_followup_routing.py` | 7 passed | not run |  |
| `tests/test_machine_list_directory_phrasings.py` | 67 passed | not run |  |
| `tests/test_machine_question_fast_paths.py` | 39 passed | not run |  |
| `tests/test_machine_read_file_phrasings.py` | 27 passed | not run |  |
| `tests/test_machine_read_memory.py` | 4 passed | not run |  |
| `tests/test_machine_result_agent_contract.py` | 5 passed | not run |  |
| `tests/test_machine_tools.py` | 25 passed | not run |  |
| `tests/test_machine_tools_adversarial.py` | 6 passed | not run |  |
| `tests/test_media_editor_is_owner_local.py` | 5 passed | not run |  |
| `tests/test_named_file_build_reaches_the_builder.py` | 67 passed | not run |  |
| `tests/test_note_tool_calls_reach_their_handlers.py` | 5 passed | not run |  |
| `tests/test_note_tool_calls_served.py` | 15 errors | not run |  |
| `tests/test_notes_scope_reader.py` | 71 passed | not run |  |
| `tests/test_openclaw_tooling_context.py` | 1 failed, 142 passed | not run |  |
| `tests/test_operator_wording_seats_owner_local.py` | 140 passed | not run |  |
| `tests/test_paid_gate_owner_local_hardening.py` | 2 passed | not run |  |
| `tests/test_path_beats_keyword_routing.py` | 34 passed | not run |  |
| `tests/test_pdf_and_skill_tools.py` | 27 passed, 6 skipped | not run |  |
| `tests/test_planner_named_folder_listing.py` | 20 passed | not run |  |
| `tests/test_remote_turn_research_requires_the_owner.py` | 9 passed | not run |  |
| `tests/test_remote_turn_tool_fence.py` | 25 passed, 8 warnings | not run |  |
| `tests/test_request_after_plain_statement_grammar.py` | 29 passed | not run |  |
| `tests/test_routing_reliability_acceptance.py` | 100 passed, 19 warnings | not run |  |
| `tests/test_save_and_picture_routing_20261029.py` | 70 passed | not run |  |
| `tests/test_served_remote_turn_refusals.py` | 5 passed | not run |  |
| `tests/test_simple_file_write_routing.py` | 68 passed, 3 warnings | not run |  |
| `tests/test_skill_narrowing_keeps_wording_and_expanded_seats.py` | 2 passed | 2 passed | 825c2ec |
| `tests/test_the_download_lane_is_gated_like_the_read_lane.py` | 11 passed | 11 passed | 825c2ec |
| `tests/test_the_download_lane_refuses_internal_addresses.py` | 13 passed | 13 passed | 825c2ec |
| `tests/test_the_machine_read_lane_starts_each_test_clean.py` | 17 passed | 17 passed | 825c2ec |
| `tests/test_the_users_own_past_is_not_a_reading_of_this_host.py` | 38 passed | 38 passed | 825c2ec |
| `tests/test_tool_cards.py` | 16 passed | 16 passed | 825c2ec |
| `tests/test_tool_cards_use.py` | 31 passed | 31 passed | 825c2ec |
| `tests/test_tool_required_gate.py` | 10 passed | 10 passed | 825c2ec |
| `tests/test_toolsmith_project_bound_reads.py` | 46 passed | 46 passed | 825c2ec |
| `tests/test_toolsmith_write_root_path_extraction.py` | 51 passed | 51 passed | 825c2ec |
| `tests/test_turn_isolation.py` | 20 passed | 20 passed | 825c2ec |
| `tests/test_v050_fast_path_costs_no_speculative_inference.py` | not run | 26 passed | 825c2ec |
| `tests/test_v2_pack_root_cause_repairs.py` | not run | 20 passed | 825c2ec |
| `tests/test_wording_seats_need_an_ask_and_the_owners_own_object.py` | 11 failed, 210 passed | 11 failed, 210 passed | 825c2ec |
| `tests/test_workspace_read_binding.py` | not run | 24 passed | 825c2ec |
| `tests/test_workspace_read_is_auditable_in_activity.py` | not run | 4 passed, 1 skipped | 825c2ec |
| `tests/test_workspace_read_probe_recursion.py` | not run | 9 passed | 825c2ec |
| `tests/test_writing_a_command_is_not_running_one.py` | not run | 36 passed | 825c2ec |
| `tests/test_writing_a_note_for_someone_is_not_saving_one.py` | not run | 53 passed | 825c2ec |

Files run on both sides: 19. BASE passed/failed/skipped [530, 11, 2], HEAD [530, 11, 2].
New failures on HEAD (not failing on BASE): none
Failures fixed on HEAD: none
Same failures on both: 11

**Denominator:** 19 of 109 files ran on both sides when the time budget ran out:
- 530 passed, 11 failed and 2 skipped on BASE; the same on HEAD.
- 0 new failures on HEAD.
- The 11 failures are in `test_wording_seats_need_an_ask_and_the_owners_own_object`. The same 11
  fail on BASE (calendar wording asks), so they are not new.

Failures seen on BASE in files that HEAD (`c9bc2c2`/`825c2ec`) did not reach in time (not a
comparison):
- `test_door_command_machine` 3
- `test_openclaw_tooling_context` 1
- `test_email_revision5_corrections` 1
- `test_expansion_seats_the_family_in_full` 1
- `test_note_tool_calls_served` 15 errors (its served-daemon rig does not start in this container)

An earlier HEAD run on fix 1 alone (`bfd2ed2`, stopped once the final head replaced it) finished
55 of the 89 related files. All 55 matched BASE apart from timing, including the same failures
in `test_door_command_machine` (3), `test_email_revision5_corrections` (1) and
`test_expansion_seats_the_family_in_full` (1). That run reached neither `test_openclaw_tooling_context`
nor `test_note_tool_calls_served`.

No existing test was skipped, loosened or deleted. A file marked "not run" was not reached before
the time budget ran out: a pass for it is unknown, not assumed. CI (verify, ten shards, macOS) has
not run on this head.
