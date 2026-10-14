# auto_router_audit_20261002 — adversarial audit harness for the VOOL Auto routing path

Mission: audit/auto-router-benchmark-20261002 at frozen public base
`dca4679fccd777554c4ad9f9653f0a3e8406b0ef`. Adds files only; production code is untouched
(enforced by `tests/test_auto_router_audit_20261002.py::test_harness_adds_files_only_no_production_edits`).

## What it does

Runs the REAL Auto selection + execution seams (`MemoryFirstRouter._execute_provider_task`
→ plan mint → `provider_routing.rank_provider_candidates` → ranked loop →
`_invoke_manifest` gates → provider execution boundary → output guards → usage accounting)
against a versioned scenario corpus (`corpus.json`, dev/held-out/control splits declared
before any run), with every model endpoint synthetic (`.invalid` hosts / loopback) and a
process-wide transport guard that refuses every non-allowlisted host — a live call cannot
leave, and the guard validates itself on every run.

## Run it (from the repo root)

    python3 tools/auto_router_audit_20261002/run_audit.py \
        --profile-root <job profiles dir> \
        --artifacts  <job artifacts dir> \
        [--splits dev,heldout,control] [--ids id1,id2] [--keep-profiles]

Outputs: `BASELINE.json` (machine-readable, per-scenario checks + verdicts),
`SCORECARD.md`, `traces/<scenario>.json` (raw per-case evidence), `corpus_copy.json`.
Exit code 0 only when every scored scenario passes and the guard control is observed.

## Deterministic pytest subset (rerunnable by anyone at the pinned base)

    python -m pytest tests/test_auto_router_audit_20261002.py -q --basetemp <ssd tmp>

Covers corpus integrity, guard self-validation (external dispatch refused, allowlisted
served, install/uninstall restore), profile-root containment, and a 4-scenario fixture run.

## Layout

- `corpus.json` — scenarios, expectations, splits (schema: see `audit_lib/report.check_trace`)
- `audit_lib/synthetic.py` — manifest archetypes, catalog rows, scripted response bodies
- `audit_lib/guard_transport.py` — the only wire the audit may speak
- `audit_lib/runner.py` — disposable VOOL_HOME per scenario, real seams, trace capture
- `audit_lib/report.py` — expectation checks, BASELINE/SCORECARD builders
- `run_audit.py` — CLI

Determinism patches (the same two the canonical routing tests use):
`_cached_free_vram_gb → None`, `should_probe_health → False`. Nothing in selection,
eligibility, or the provider gates is patched.
