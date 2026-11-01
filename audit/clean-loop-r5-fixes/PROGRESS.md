# Progress

Start: 2026-11-01 09:47 EET (task start; local time Europe/Vilnius)
Base sha: 8c403f0b841b7c757884dadacc837b22607df294
Initial git status: clean on delivery/clean-loop-08-r5-fixes-20261101 before adding task artifacts.
Python: Python 3.12.13
OS: Darwin 25.5.0, arm64

Doing: inspect round-5 diagnosis and clean-loop implementation.
Done: verified mounted volume identity and task SHA; fresh clone at the required base and branch.
Still broken: implementation and validation not started.
ETA: within 2 h 30 min of task start.

## Round-5 evidence availability

The permitted round-5 `RESULT.md` has aggregate route counts and the 16 miss IDs, but not per-row route reasons, tool traces, PDF statuses, or VOOL replies. `h2h/full-voold.out` is inconsistent with that result (22/45 and labelled as a round-4 run), so it is not valid row evidence. The raw miss table cannot be completed from these files without inventing facts; implementation order follows the task's C1-C7 ownership descriptions.

## 10:02 EET update

Doing: implement bounded local document indexing and the requested regression fixes.
Done: the pre-fix hold-out failed because document matching was absent; after the route/index change it passed 40/40 general asks kept out, 30/30 personal asks admitted, and the memory-only question remained out. Added PDF, follow-up, mixed-read, archive, and switch regressions.
Still broken: cumulative clean-loop run exposed old default-off assertions and an old PDF binary-fallback expectation; explicit-off tests are being updated per C7, PDF fallback is being constrained to plain printable text, and one legacy no-result phrase is being preserved alongside the required wording.
ETA: 12:17 EET.
