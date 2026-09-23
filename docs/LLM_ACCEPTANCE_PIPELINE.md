# LLM Acceptance pipeline

The weekly LLM Acceptance workflow (`.github/workflows/llm_acceptance.yml`) has two lanes:

- **`llm-acceptance-fast`** — scheduled weekly (Mondays 06:00 UTC) on `ubuntu-latest`, runs
  `python ops/llm_eval.py --skip-live-runtime`. This is the lane that gates.
- **`llm-acceptance-live`** — opt-in only: `workflow_dispatch` with `run_live_runtime=true`,
  after the fast lane passes, on the self-hosted model-provisioned runner. Nothing in the
  weekly schedule ever starts it, and it never touches paid providers.

## What the fast lane selects

`ops/llm_eval.py` runs, via `core.llm_eval.pack.run_pytest_pack`:

1. a fixed set of scenario targets (context discipline, routing reliability, research
   quality, hive integrity, VoolBook provenance), and
2. the **48h regression pack**: `RECENT_48H_BASELINE_TARGETS` plus every `tests/**.py` file
   changed in the last 48 hours whose path matches the LLM keyword filter
   (`core.llm_eval.pack.collect_recent_llm_inventory`, backed by `git log --since`).

Because (2) is selected from git history, the workflow checks out with `fetch-depth: 0`;
a shallow clone truncates the window to the tip commit whenever more than one commit lands
inside it and silently under-selects the pack.

## Provisioning contract

The fast lane's selection is a **subset of the CI suite**: every file it can run is a file
the CI Linux shards already run. Its provisioning therefore mirrors the CI shards
(`.github/workflows/ci.yml`) exactly:

| Requirement | Owner | Why |
| --- | --- | --- |
| Python packages | `pip install -e ".[dev,browser,companion,evm]"` | The global liquefy conftest fixture imports `zstandard` (declared under `[companion]`); browser and wallet lanes import `playwright` / `eth-*`. Installing only `.[dev]` is what failed every scenario at setup in run 35600263193 (`ModuleNotFoundError: No module named 'zstandard'`). |
| Kernel sandbox backend | `bubblewrap` + AppArmor `userns` profile | Runtime-lane tests in the 48h inventory execute journaled steps through the real sandbox runner; the runner fails closed without a working bwrap (measured in CI run 35520785157). |
| Served-browser lane | `playwright install --with-deps chromium` + AppArmor profile | Inventory-eligible web tests launch the bundled Chromium, which cannot start under Ubuntu 24.04's restricted-userns policy without the grant (measured in CI run 35752689216). |

If a future target needs something this contract does not provide, the failure surfaces as
a red scenario with the exact `ModuleNotFoundError`/probe text in its captured stdout — it
must be fixed by extending the declared extras or the provisioning steps, never by
deselecting the scenario.

## Reporting truth

- Every pack's status comes from its **pytest exit code**; setup errors, collection
  errors, failed tests and usage errors are all nonzero and therefore red.
- A pack that executed nothing (`passed+failed+errors+xfailed+xpassed == 0`) is treated as
  a functional failure of the 48h regression lane, not a pass.
- `parse_pytest_summary` counts pytest's singular rows (`1 error`) as well as plural ones;
  the archived 2026-09-21 run recorded `errors: 0` next to a stdout reading `1 error`,
  which under-reported the failure the exit code still caught.
- A pack that exceeds its execution bound reports red (`exit_code` 124, `timed_out: true`,
  partial stdout/stderr retained) instead of hanging or crashing the lane.
- The summary distinguishes `ci_fast_green` (what the weekly lane proves) from
  `overall_full_green` (which requires the opt-in live lane); a skipped live lane is
  reported as `blocked`/`not_run` with its reason, never as a pass.

## Execution bounds

- `llm-acceptance-fast` carries an explicit `timeout-minutes` sized from a measured full
  local run plus headroom for provisioning and inventory growth.
- Each pytest pack is bounded by `--pack-timeout` (1800 s in the fast lane, 3600 s in the
  live lane): a single hung pack cannot consume the job budget or leave no report.
- `llm-acceptance-live` is bounded provisionally (240 min) because no successful live
  lane has been measured yet; tighten it after the first green live run.

## Regression tests

`tests/test_llm_acceptance_pipeline.py` pins the contracts above: singular-row counting,
setup/collection/timeout packs staying red, and the workflow's provisioning parity,
checkout depth, opt-in live gating and explicit bounds.
