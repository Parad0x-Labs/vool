# Tests and benchmarks

Every benchmark VOOL has published, with the release it belongs to. Each page lists every run,
including the ones that went worse for VOOL, the setup, the spend and what limits the numbers.

## Headline: memory

On 150 never-used [LoCoMo](https://github.com/snap-research/locomo) questions, same reader model for
every agent, same GPT-4o grader:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Right answers | **82.7%** (124 / 150) | 71.3% (107) | 62.7% (94) |
| Confidently wrong | **18** | 31 | 35 |
| Tokens per question | **9.6k** | 51.8k | 33.5k |
| Cost per question | **$0.0033** | $0.0100 | $0.0046 |
| Median answer time | **11.2 s** | 15.6 s | 16.2 s |

VOOL's lead is more than noise: p = 0.005 against OpenClaw and p < 0.001 against Hermes (exact McNemar
on matched questions). The memory shipped in VOOL 0.7 scored 123 of 150 on the same set. Full method,
every round and the weak spots: [v0.7-memory-locomo.md](v0.7-memory-locomo.md).

## All benchmarks

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
