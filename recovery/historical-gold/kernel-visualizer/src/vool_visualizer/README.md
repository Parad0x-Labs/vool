# VOOL kernel trace visualizer

See, live and step-by-step, what the kernel does with each VOOL question — by
consuming the kernel's own durable journal, presented as a layered interactive
DAG. Built so you can *look at* what's happening and find the wrong write
yourself, and so you (or a coding agent) can later *change* how it behaves.

It is **read-only** with respect to kernel semantics: all imported code is a
passive observer. The only kernel touch is an optional, **off-by-default**
display side-channel (described below) that cannot change behavior.

## Why this exists
You asked to "see visually the stuff it is doing so [you] can fix it yourself."
The kernel already records everything per turn (`question`, `answer`,
`tape[effect.seq]`, `terminal_state`, `stamps`, `transcript`) in
`repl_sessions.jsonl`. This visualizer turns those bytes into an interactive
diagram instead of a wall of JSON.

## Quick start (no live kernel needed)
```bash
cd <worktree>/vool_visualizer
../.venv/bin/python -m vool_visualizer.server --demo
# open http://127.0.0.1:8123
```
`--demo` points at the durable Round-0 sample journal so the diagram works
immediately with the 34-turn hardmix run.

Point at your own journal:
```bash
../.venv/bin/python -m vool_visualizer.server \
    --journal "$NULLA_HOME/repl_sessions.jsonl" \
    --port 8123
```

## Two views
- **http://127.0.0.1:8123/** — layered **2D DAG** (context → extract → derive →
  synthesize → web → compute → verify → repair → recall → render). Good for
  reading the exact pipeline and per-node detail.
- **http://127.0.0.1:8123/lab** — the **3D kernel lab bench**: stages are
  colored lanes, each effect / obligation / stamp / answer is a floating
  "microchip" you can **drag** on the bench, orbit with the mouse, and ▶ run a
  lit signal sphere along the effect tape. Fully offline (three.js vendored
  under `static/vendor/`; no network at runtime).
  - Left rail: turns + commit disposition. Top-right: per-turn stamps, stage
    legend, selected-part detail, and read-only knobs.
  - Drag a chip to move it; drag empty space / two-finger to orbit.

Click ▶ on a turn in either view to step the kernel signal effect-by-effect in
tape order.

## What you see
- **Left** — turn list: each turn's commit disposition (committed / partial /
  crash / settled), effect count, and torn-stamp badges (⛔web / ⛔machine).
- **Center** — the layered DAG for the selected turn: columns are the pipeline
  stages (context → extract → derive → synthesize → web → compute → verify →
  repair → recall → render). Nodes are effects (green=ok, red=errored/invented,
  judging beam reads the `error` on the tape) and the per-obligation terminal
  states plus the final answer. Click a node to see its result/error and the
  code region that drives it.
- **Right** — turn details (stamps, commit, crash), behavior-knob intent panel
  (*read-only* in this round), and a live prompt/code pane.
- **Step ▶** — animate the effects of a turn in tape order so you can follow
  exactly the order the kernel acted.

## Live view (step-by-step while the kernel runs)
The visualizer can animate a turn as it fires, instead of only replaying a
finished turn. To enable, set `VOOL_VIS_EVENTS` to a writable file before
starting the kernel REPL; the kernel then appends one JSON display-line per
effect attempt to that file (success/error), and the visualizer tails it over
`/api/events`. This file is a **display-only** side-channel: the canonical
journal (`repl_sessions.jsonl`) is still written atomically at turn end and is
the only law-bound store; nothing in the kernel reads the events file.

```bash
VOOL_VIS_EVENTS=/tmp/vool_vis_events.jsonl \
  ../.venv/bin/python -m core.kernel.repl          # in another terminal
# then:
../.venv/bin/python -m vool_visualizer.server --journal "$NULLA_HOME/repl_sessions.jsonl" \
  --events /tmp/vool_vis_events.jsonl --port 8123
```

Behaviour is **identical** when `VOOL_VIS_EVENTS` is unset (0 imports, 0
exceptions, 0 calls — verified by the kernel test suite staying `57 passed`).

## Behavior knobs: this round is read-only
The right-hand "Behavior knobs" panel currently shows intent *only*: guard
toggles (word-count contract, entity binding, harvest binding, render echo
ban, multipart UNKNOWN) and threshold sliders (ledger TTL, stale-aging,
token-window). Round 1 does **not** write those back into `repl.py` — the
delta review review is open and no behavior may change. The intended
write-back seam is described in `docs/` (the knob panel → a `repl.py` guard
reader → the concrete gate) and is gated behind an env flag set to `0`.

When the review closes and you actually want to flip the guards, the natural
+small enhancement is to bind the knobs to `repl.py`'s own consts (e.g.
active ledger, `_apply_ledger_text`, the generic-token ban) and re-arch test. 
That is a follow-up change to the kernel, not part of this artifact.

## Files
```
vool_visualizer/
  server.py      # stdlib http server + API (SSE-style /api/events tail; no npm)
  observer.py     # journal -> Turn models; live events reader
  graph.py        # layered-DAG layout (stage per effect_id)
  models.py       # Turn / Obligation / Effect / stamps
  static/index.html · app.js · app.css   # 2D layered DAG UI
  static/lab.html · app3d.js · lab.css   # 3D kernel lab bench (three.js)
  static/vendor/                          # offline three.js + OrbitControls
  README.md
core/kernel/vis_events.py   # env-gated side-channel (OFF default; no-op off)
core/kernel/effects.py      # 2 best-effort opt-in emit calls (behavior-neutral)
```

## Verification
- Kernel suite with the hook present (events disabled): `57 passed`.
- Journal + hash when events on vs off: identical; events file only written when
  the env var is set.
- Demo server: `/api/state` 34 turns; `/api/turn/4` full layered graph;
  `/api/events` tails the display stream.

## Limits / next steps (you asked to be able to fix things)
1. **Live step animation** is display-only now. To make a knob *change a turn*,
   the write-back env flag (`VOOL_VIS_WRITEBACK`) must flip to 1 after the
   review closes and the kernel guards must read the knob file.
2. **3D view**: shipped a light "3D tilt" aesthetic; a real three.js graph is a
   later option — for debugging the layered 2D DAG is usually the clearer fit.
3. **Per-effect prompt panes**: the `model.*` effects inputs can be shown; the
   server could expose a `show_prompts` link to the same inputs the kernel
   builds (prompt text is in the journal's tape context for derive/synthesize;
   extract/verify prompts come from the constant builders, not the journal).

This artifact is a reader over durable bytes; it does not itself create,
alter, or delete any kernel record.
