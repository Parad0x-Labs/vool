# Conversation-truth regression kit

Audited HEAD: `091ed83b708b4e9198561a7c5f36004a60217e28` (branch `audit/conversation-truth-20260906`,
created from the source lane's committed HEAD `fix/alpha-chat-startup-20260905`). Read-only audit:
this kit contains NO production-code changes. Fable owns the active repair; these tests are the
executable statement of the defects and the controls the repair must keep green.

## Run

```bash
cd ~/vool/worktrees/conversation-truth-audit-20260906
RUNNER=~/vool/vool-engine/.venv/bin/python

# Pure-function half (fast, deterministic):
$RUNNER -m pytest test-kit/test_cp1_incident_repro.py test-kit/test_cp2_controls.py \
                  test-kit/test_cp3_census_attribution.py test-kit/test_cp4_isolation.py -q

# Reproduction half (RED at HEAD by design — each is a confirmed defect):
$RUNNER -m pytest test-kit/test_repro_ct2_misbinds.py test-kit/test_repro_ct3_census.py -q

# Everything at once (repros must be named or the directory run includes them via test_ prefix —
# they are, after the rename; expect 13 failed / 22 passed in the pure half):
$RUNNER -m pytest test-kit/ -q --ignore=test-kit/test_cp4_served_identity.py

# Served half (real /api/chat, isolated daemon + certified scripted loopback provider;
# no paid calls, no local models; boots its own daemon per test, ~1-2 min under load):
$RUNNER -m pytest test-kit/test_cp4_served_identity.py -q

# Instrument (prints observed behaviour; not a regression test):
$RUNNER -m pytest test-kit/probe_cp2_adversaries.py -q -s
```

## Expected outcome at HEAD 091ed83b

| file | cases | expected | meaning |
|---|---|---|---|
| test_cp1_incident_repro.py | 3 | 3 passed | the recorded incident is declined at HEAD; severed-guard repro attributes the boundary |
| test_cp2_controls.py | 12 | 12 passed | genuine follow-ups still work; safe declines hold |
| test_cp3_census_attribution.py | 2 | 2 passed | the incident's census arithmetic reproduced; legitimate rebind obeys the receipt law |
| test_cp4_isolation.py | 5 | 5 passed | session scoping, provenance-first identity, walk-back rules |
| test_cp4_served_identity.py | 2 | 2 passed | served two-session isolation + accept-once replay semantics (real /api/chat) |
| test_repro_ct2_misbinds.py | 8 | **8 failed** | CT-201…CT-207: seven live mis-bind mechanisms (CT-202 pinned at both the continuation and the authorization seam) |
| test_repro_ct3_census.py | 5 | **5 failed** | CT-301 (×2), CT-302 (×3): obligation-completeness defects |
| probe_cp2_adversaries.py | 1 | 1 passed (instrument) | prints observed behaviour per input; not a regression test |

After the owning repairs land, every file must report all-pass with the SAME assertions
(the repro files assert correct behaviour; they are not xfail — a pass means the defect is gone,
and no assertion was weakened to get there).

## Design rules obeyed

- Distinct input/output markers: every case uses fresh session ids (`kit_lib.sid`); the incident
  texts are quoted verbatim from the recorded request.
- Independent oracles: continuation return value, plan `(operation, entity)` pairs,
  `requirements_for().answer_mode/reason_codes`, demand-unit mint shape, plan unit binding vs
  canonical set, SQLite event rows and provider payload capture on the served side. No oracle
  re-implements the production predicate.
- Deterministic interleavings: served turns are sequential with fixed `turn_id`s and fixed
  `X-Request-ID`s; no sleeps govern correctness (the one `sleep(10)` only quarantines the first
  turn's post-response background work out of the replay measurement window).
- Positive controls: every family carries must-still-work cases (B-column of the inventory).
- Guard-severance: CP1 (×2) and CP3 (×1) sever `_content_the_obligation_does_not_hold` — the
  repo's sabotage idiom — to prove the tests bind the repaired invariant, not the strings.
- No paraphrase padding: the six CP2 repros are six distinct mechanisms on one seam, each with
  its own gate in the decision tree; the census repros are two further seams (mint, binding).
- Real boundaries over regex: mis-binds are additionally provable at the plan/classification
  level, and identity/isolation at the real `/api/chat` door; the kit keeps one served layer for
  exactly the families where the wire adds truth (identity, replay, cross-session).
- Fixtures are scripted protocol evidence (`FIXTURE-REPLY: …`), never real-model usefulness
  claims; no paid calls, no local models, no Keychain, no operator data.

## Scenario inventory

`SCENARIO_INVENTORY.md` — the full stratified inventory (A–F families) including scenarios NOT
covered by the kit (documented as such), for future lanes.
