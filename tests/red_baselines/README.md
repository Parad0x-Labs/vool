# RED-1 baseline captures — AUD-20260829-003 round 2

Captured 2026-08-29 by RED-1 against the FROZEN served checkout
`/Users/example-user/Desktop/OX-VOOL/code` @ 0b4c2eb4 (clean tree),
serving daemon PID 25223 (booted 2026-08-29 21:39:06 local).

These are the BEFORE picture. They were produced in an ephemeral session
scratchpad and copied here so they survive; `red_a_baseline.json` is gzipped
(3.3 MB raw). Hashes in SHA256SUMS.txt.

- `red_a_baseline.json.gz` — reroute census over the harvested population
  (997 prompt literals from 21 seam-touching test modules; 184 flows where
  the closed contract holds; 70/184 would reroute under a bare
  unclaimed-slice guard).
- `red_b_baseline_api_chat.json|.log` — A13/A14 gauntlet over /api/chat.
- `red_b_baseline_frontdoor.json` — same gauntlet at the second entrance
  (served commit carries no closure_verdict and no provenance block).
- `red_c_baseline.json|.log` — adversarial corpus: 39/39 turns certified
  covered:true / open_count:0 over 16 silently dropped slots.
- `probe0.py` — the probe used to capture them.

Harnesses that produce these live in `tests/red_a_reroute_census.py`,
`tests/red_b_served_gauntlet.py`, `tests/red_c_adversarial_corpus.py`.
They are named `red_*` deliberately: pyproject's `python_files = ["test_*.py"]`
means pytest does not collect them, so they cannot perturb blue's runs.
