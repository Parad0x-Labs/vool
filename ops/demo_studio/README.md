# VOOL Demo Studio (P1)

Records the **actual served VOOL product** — never a mock, never a scripted
fake UI, never a hand-authored success screen.

## Flow (capture mode)

1. select a typed demo journey (`ops/demo_studio/journeys.py` — data, not scripts);
2. stage the product at an exact SHA (`git archive`, byte-census, read-only —
   reuses `ops/served_reality/appstage.py`);
3. boot the isolated daemon and gate on `/healthz`: exact commit, `dirty:false`
   (studio re-verifies independently — three separately-written SHA checks);
   with `--lane`, every daemon filmed (the first boot and each restart) must
   also pass the line's own guard, `ops/build_identity.py check --lane`, and
   the manifest keeps each verdict under `boot.build_identity`;
4. verify the capability manifest (`/api/runtime/capabilities`) against the
   journey's declared rows;
5. drive the real chat page in real chromium (screen recording on,
   `reduced_motion='reduce'` pinned);
6. drain the public typed event trace (`/api/runtime/events`) while the
   journey runs; a zoom is planned only when a focus-rule event fires AND its
   on-screen element was really there (live DOM box captured at event time);
7. captions are derived ONLY from the public typed event allowlist — the
   streaming content channel (`model_output_chunk`) is excluded by
   construction;
8. everything the studio emits passes the redaction engine, then an
   independent leak scan;
9. render: `demo.mp4` (H.264 + embedded mov_text subtitles), `demo.webm`,
   `captions.srt`, `poster.png` (a real frame), `events.jsonl` (redacted
   public mirror), and `evidence.manifest.json` binding commit, app version,
   journey spec hash, timeline, step results, zoom justifications, and every
   artifact's sha256;
10. any failed step, missing expected event, failed workspace assertion, or
    manifest-verification violation aborts the run with an `abort-report.json`
    and exit code 3.

## Modes

- **dry-run** — deterministic, byte-identical plan manifests for CI. Boots
  nothing, records nothing, claims nothing (`honesty.captured: false`, fixed
  epoch `2026-09-02T00:00:00Z`).
- **capture** — the real thing. Model lane is either the deterministic
  provider stub (`honesty.model_lane: "scripted_stub"` — the repo's
  established bench instrument; the UI and runtime stay fully real) or the
  operator's real model lanes (`--live-models`).

The stub lane is wired the way the served-reality bench wires it, from the
first boot: every model-lane seam points at the stub, the daemon's network is
contained (an external fetch gets the stub's recorded 502), and a restart
relaunches with the same arguments. The stub is not certified for VOOL's
answer check, so a scripted turn completes with the product's own refusal to
publish an unchecked answer; the film shows that as it is.

## Usage

```
python -m ops.demo_studio --list
python -m ops.demo_studio --journey local_chat --mode dry-run --out <out-dir>
python -m ops.demo_studio --journey local_chat --mode capture \
    --sha HEAD --python <python with the runtime extras> --out <out-dir>
python -m ops.demo_studio --journey local_chat --mode capture \
    --lane v08 --heads-manifest <heads manifest> --repo <clean checkout> \
    --python <python with the runtime extras> --out <out-dir>
```

`--python` boots the staged app, so it needs the product's runtime
dependencies including the `companion` extra (the served command surface
refuses to start without `zstandard`). The studio itself needs Playwright's
Chromium and `ffmpeg`/`ffprobe` on PATH.

Exit codes: 0 pass/planned · 2 refusal (config, journey, boot gate, lane guard) · 3 aborted.

## Honesty rules

- Journeys that need live dependencies (`live_network`, `operator_models`)
  declare them and refuse visibly where they are absent — a contained machine
  never fabricates sources, tool calls, or council seats.
- Zooms without a citing event are refused by the validator; every manifest
  publishes its zoom justifications for audit.
- An aborted run ships an abort report and never a success manifest.
