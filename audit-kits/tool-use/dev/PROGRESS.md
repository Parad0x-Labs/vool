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
