# Demo Studio P1 — Evidence Index & Findings (2026-09-02)

Branch `build/demo-studio-p1-20260902`, base `a7b78e2a`. All artifacts here
were produced by real runs; nothing is hand-authored.

## Proof chain

| file | what it proves |
|---|---|
| `RED-pass1.txt` | RED: 9 collection errors — the whole contract did not exist (`No module named 'ops.demo_studio'`) |
| `GREEN-pass1.txt` | GREEN: 69 passed / 2 opt-in e2e skips after implementation |
| `GREEN-final.txt` | final unit state: 70 passed (adds the JSONL/honesty success-path test) |
| `SABOTAGE-sha-check-RED.txt` | file-level sabotage of the full-SHA check fails the named guard test (layer marker `full-SHA mismatch`) |
| `SABOTAGE-sha-check-RESTORED-GREEN.txt` | restore → green |
| `SABOTAGE-redaction-RED.txt` | redaction engine disabled → 3 named redaction tests fail |
| `SABOTAGE-redaction-RESTORED-GREEN.txt` | restore → green |
| `regression-head.txt` | 37 passed on the adjacent-lane selection at HEAD; identical selection at base `a7b78e2a` also 37 passed — zero regressions |
| `captures/*/` | genuine captures (manifest + public event mirror + SRT + poster + MP4) |
| `aborts/*/` | genuine capture-time aborts (typed reports) |

## Genuine captures (real staged app, real chromium, real recording)

- `restart_resume_continuity-PASS` — turn answered → daemon tree terminated →
  relaunched at the same SHA → transcript restored through the real sidebar;
  codeword asserted on-screen. (382 KB MP4.)
- `model_switching-PASS` — model menu interaction, lane pinned on-screen,
  turn completed on the pinned lane.
- `file_photo_attachment-PASS` — real upload door, `attachment_staged` events,
  turn completed referencing the attachments.
- `local_chat-LIVE-honest-refusal` — **live model lane** (`--live-models`,
  real Ollama on 11434, real qwen2.5:7b/qwen3:4b): the product's own
  tool-certification flow ran for real (`run_id tool-cert-21b9…`, state
  `degraded`, `model emitted no tool calls`), the authorship fence then
  refused both real lanes, and the turn completed with the product's honest
  lane-failure answer. Captured as-is: this is the product's real behavior,
  and the manifest says `model_certified: false`.

The hermetic (`scripted_stub`) local_chat journeys film the same real UI with
the deterministic provider lane; their turns complete with honest
authorship-fence refusals of the uncertified stub lane — recorded truthfully
in the manifests' event mirrors.

## Product defects found by the studio (not repaired in this lane)

1. **Public events read 500s under concurrent turn load** —
   `GET /api/runtime/events` intermittently answered HTTP 500 (no traceback
   surfaced in daemon logs) while a turn was in flight, on a fresh home.
   Reproduced indirectly in-process: `POST /api/chat` 500s with
   `sqlite3.OperationalError: no such table: invocation_requests`
   (`core/invocation/ledger.py:133` via `core/web/api/service.py:2229`) when
   store initialization races first use. Evidence: `aborts/events-500-*`.
   The studio aborts visibly on persistent failure and retries transient
   reads with backoff (bounded, and recorded in this report).
2. **Authorship fence vs available local models** — the tool-certification
   probe requires native tool-call emission; the content-only stub can never
   pass (by design), and the real local models on this machine (qwen2.5:7b,
   qwen3:4b via Ollama) also emitted no tool calls (`model_emission: failed`,
   "model emitted no tool calls"). Every final answer on this machine is
   therefore honestly refused by the fence. This is correct product behavior;
   it is documented here because it defines what a demo capture can show
   without a certified model.

## Studio-side defects found and fixed during live use

- bundle manifest seeds lazily and the dead-lane row is pruned after a failed
  turn → warm-up turn, then repoint; single model identity (boot-seeded row).
- ollama-local manifests hardcode `base_url: 127.0.0.1:11434`; the answering
  lane reads only the persisted manifest → post-boot repoint at the stub.
- `NULLA_INSTALL_PROFILE=local-max` crashes boot without a real backend →
  not used.
- idempotent menu openers (double-click toggled the popover closed).
- page session resolution before polling; context-destroyed races tolerated.
- restart rebinding: new port → wire/driver/page rebase.
- events.jsonl written as compact JSONL; fixture PNG made valid.

## Port to v0.8 (2026-11-12)

The studio moved onto the v0.8 line. In this folder only machine-local paths
changed: `RED-pass1.txt` now reads `<repo>/` and `<python>/`, and the four
capture manifests' `render` block names each artifact by its file name (what
`render.py` records now). Every artifact sha256 binding still holds. The
captures' `demo.webm` files were never kept here; their bindings stay in the
manifests.

Measured on the v0.8 head `247124ed` (0.8.0-beta), scripted stub lane,
`--lane v08`, real staged app and real chromium:

- `restart_resume_continuity`: pass. Both daemons the film shows (first boot
  and relaunch) passed `ops/build_identity.py check --lane v08`; the page
  shows the product's own "isn't running right now" bar while the daemon is
  really down, then the transcript reopens with the codeword.
- `local_chat`, `file_photo_attachment`, `model_switching`: every step passes,
  then the run aborts (exit 3) because `task_completed` never arrives. v0.8's
  final-answer authorship fence refuses the uncertified stub model
  (`author_not_certified_for_final_answer`) and records the turn as
  `task_failed`. At `5bf351ba` the same refusal ended in `task_completed`,
  which is what the `-PASS` captures above show ("All ranked provider lanes
  failed", then "The turn completed"). Filming these journeys with an answer
  needs a certified model (`--live-models` on a machine that has one).

Studio-side fixes made for the port:

- the stub port now goes into the FIRST boot (it was left out, so the first
  daemon ran with no stub lanes and no network containment); v0.8 seeds its
  ollama-local rows from that environment, so the warm-up turn and the store
  repoint now run only for commits that still hardcode 127.0.0.1:11434;
- a restart relaunches with the first boot's exact arguments (it used to drop
  the bundle selection, the stub model name and the cloud stub lane);
- the stub lane carries the stub's own model name everywhere;
- the v0.8 model menu's run modes (`.run-choice[data-run-model]`) are
  selectable, and a pick counts only once the page shows it checked;
- `--lane` films only a lane head that the line's build guard passes.
