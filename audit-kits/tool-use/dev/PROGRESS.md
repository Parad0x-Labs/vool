# Progress

- 16:44Z: dev set written (81 asks: 51 needs_tool, 15 missing_connection, 15 no_tool) before any code change. Box setup (CPU Ollama, qwen3:8b, venv) running.
- 17:27Z: base (cb4852e5) on dev subset A (27 asks, every 3rd row) running; 17 rows so far. Root cause confirmed on
  real turns: fresh needs-tool asks reach the model with 0 tools (`model_lane`, offered [0]) or are eaten by a
  pre-model lane (`conductor_multi_intent_plan`, entity-ambiguity probe). Built `core/owner_tool_catalog.py`:
  owner-local turns on a local model release both word gates and get 12 read tools (one per family, short
  descriptions, fixed order). First candidate turn showed the model now calls `machine.find_file` by itself, but
  the turn timed out: I had run two daemons on one Ollama, which evicts its prompt cache. Stopped the second run;
  runs are serial from now on. Base rows 10-46 overlapped that window, so their latency is not clean.
- Tests: new `tests/test_owner_tool_catalog.py` 14/14 pass; gate-related files (76) and the brief's -k selection
  still running for base and candidate. Blocker: none; CPU is the limit (1-6 min per turn).
- 17:53Z: tests on 525b614 (catalog + short descriptions): the 76 test files that drive the two gates or the offer,
  plus `tests/test_owner_tool_catalog.py`: 2574 passed, 34 failed, 4 skipped. The same 34 fail on base cb4852e5
  (re-run by id: 34/34 red there): MCP stub / plugin dispatch tests (no MCP stub server or OS sandbox here) and
  cb4852e5's own wording-seat regressions ("is Sunday free parking in London?" seats calendar tools). No new red.
- 18:30Z: base subset A done (27 asks). Scorer: needs_tool 9/17 family tool, missing_connection 0/5 tools of the
  family (2/5 replies say plainly that mail is not connected), no_tool 0/5 any tool. One base timeout (dev-nt-46),
  in the window where two daemons shared Ollama.
  Found on real candidate turns, fixed in order: (1) a 15-tool catalog round still got prose for "kas yra mano
  Downloads aplanke?"; replaying the exact payload against Ollama showed one short instruction makes qwen3:8b
  call machine.list_directory / machine.list_processes and answer knowledge asks without a tool (e283c8f);
  (2) Ollama ignores tool_choice "required", so a knowledge answer arrived as prose and the lane raised "required
  native tool call is missing" and paid a second call; on catalog rounds that prose is now respond.direct;
  (3) the model's list_directory {path: Downloads} was refused by the semantic preflight (no grammar for the
  Lithuanian sentence): the owner's own words now bind a proposal to a safe folder they name (e41b738);
  (4) the conductor and the entity-ambiguity probe ate catalog turns before the model (992d621, e41b738).
  Slowness: on this box one catalog round is 1.5-4 min, mostly generation at 2 threads; running pytest beside it
  doubled that. The base -k test run hung at 91% and was stopped. Candidate e41b738 on subset A started 18:29.
- 19:22Z: hard stop near. Candidate e41b738 ran 15 of subset A before the stop. Same 15 ids: needs_tool reader
  pass 4/9 -> 5/9 (scorer family tool 4/9 -> 8/9), missing_connection 2/3 -> 2/3, no_tool 3/3 -> 3/3, one timeout.
  Tests on e41b738 (207 related files): 5246 passed, 44 failed; 43 of the 44 fail on base too; one new red
  (test_answer_integrity_consecutive_turns served-door sequence). Details and what is left in RESULT.md.
