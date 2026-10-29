# Tests and benchmarks

Every benchmark VOOL has published, with the release it belongs to. Each page lists every run,
including the ones that went worse for VOOL, the setup, the spend and what limits the numbers.

| Benchmark | What it measures | Release | Page |
| --- | --- | --- | --- |
| Memory (LoCoMo) | How well VOOL remembers a long conversation, against Hermes Agent, OpenClaw and the whole chat pasted into the prompt | Released in 0.7 | [v0.7-memory-locomo.md](v0.7-memory-locomo.md) |
| Prompt caching and cost | What caching saves on Claude, and VOOL's cost and answer quality next to Claude Code on the same messages | Planned for 0.7.7, not released yet | [v0.7.3-prompt-caching.md](v0.7.3-prompt-caching.md) |

Page names start with the VOOL release they test. "Released" means that build is out; "planned" means
the feature is still on its way and the page describes a development build.

## Files

* [`results.json`](results.json) and [`make_charts.py`](make_charts.py): numbers and charts for the memory benchmarks.
* [`prompt-caching-results.json`](prompt-caching-results.json) and
  [`make_prompt_caching_charts.py`](make_prompt_caching_charts.py): numbers and charts for prompt caching.
* Both scripts use the standard library only and write to [`charts/`](charts/), for example
  `python docs/benchmarks/make_prompt_caching_charts.py`.
