# Authority map — who decides what, and how that is enforced

**Lane:** `build/kas-law-repoops-plugin-20260902`, from `58849eb3`.
**Status of every claim below:** the "enforced by" column names a test that fails if the claim
stops being true. A row with no test is marked so.

---

## 0. The law

> **KAS owns external adapters and channels. VOOL owns permissions, semantic truth, effects,
> privacy and finality.**

An adapter translates a typed VOOL request into a provider's wire shape and a provider's reply
back into typed VOOL data. It holds no authority: it cannot decide whether an effect may happen,
cannot open a socket, cannot read a secret, cannot redact, and cannot declare anything final.

The law was previously a claim in prose. It is now a scan.

---

## 1. The authorities, and the one module that owns each

| Question | The one authority | Where a caller reaches it |
|---|---|---|
| MAY this effect execute? | `core.mode_permission_policy.decide_tool_call` | `core.authorized_tool_execution.execute_authorized_runtime_tool`, or a contract's declared `permission_actions` at the runtime door |
| Did it actually happen? | `core.effect_gateway` (`EffectLedger`, `EffectLifecycle`, `EffectReceipt`) | opened by the turn scope; every door emits into it |
| Did it happen when the reply never came? | `core.effect_reconciliation` + `core.runtime_continuity` unresolved-effect store | `reserve_logical_effect` → `classify_effect_outcome` → `resolve_unresolved_effect` |
| May this leave the machine? | `core.remote_fetch_policy.open_remote` (transport), `core.finalization` A8 availability (payload) | `core.kas.transport.build_transport` for adapters |
| What did the turn actually claim? | `core.finalization` (A7) | the serving lanes |
| Which byte changed, and can it be undone? | `core.blackbox` | `_dispatch_with_mutation_activity` at the runtime door |
| What went wrong, typed? | `core.faults` (12 codes, closed set) | `FaultRecord.for_code` + `record_fault` |
| May this task spend a model at all? | `core.council.gate0.Gate0` over `core.council.task_contract.TaskContract` | `core.repoops.task_law.evaluate` |
| Is this plugin available? | `core.plugin_lifecycle.is_available` | `core.execution.capabilities._plugin_tool_specs` |

---

## 2. KAS — the adapter boundary

```
core/kas/
  contract.py     the vocabulary: KasRequest / KasResponse / TransportDenied / TransportUnknown,
                  ExternalAdapter, and ONE ForgeAdapter contract
  transport.py    the ONE egress VOOL builds and injects into an adapter
  registry.py     the only way an adapter is constructed (with its transport already bound)
  conformance.py  the law as an AST scan
  adapters/
    github.py     GitHub, translating only
    gitlab.py     GitLab, translating only, behind the same contract
```

**What the transport decides so no adapter has to:** the request crosses
`core.remote_fetch_policy.open_remote`, which refuses outright with no turn or background effect
ledger open and runs the full `authorized → started → succeeded|failed|cancelled` lifecycle. The
credential is resolved from an opaque **binding id** inside VOOL and attached there; the adapter
never holds, sees or logs a secret. An adapter that sets its own `Authorization`, aims at a host
VOOL did not pin, or uses a non-HTTP scheme is refused before any socket. A mutating request whose
reply never arrived raises `TransportUnknown` — never a failure.

**Enforced by** `tests/kas/test_kas_law.py`:
`test_every_shipped_adapter_holds_no_authority` (the scan over the shipped adapters),
`test_the_scan_names_the_authority_an_adapter_would_duplicate`,
`test_an_adapter_that_sets_its_own_credential_header_is_refused`,
`test_a_request_to_an_unpinned_host_never_reaches_a_socket`,
`test_the_adapter_names_a_binding_and_never_holds_a_secret`,
`test_github_and_gitlab_produce_the_same_typed_pull_request`.

**One contract, two wires.** GitLab calls a pull request a merge request, numbers it per project,
says `opened` where the shared vocabulary says `open`, keys pipelines by ref, and returns a diff as
structured JSON. All of that difference stops inside the adapter. RepoOps never learns which forge
it is talking to.

---

## 3. RepoOps — the production repository vertical

`core/repoops/{contracts,plane,gitops,forge,task_law}.py`, offered from the one tool registry as
`repo.*` and dispatched through the one runtime door
(`core.runtime_execution_tools.execute_runtime_tool`), so the registry contracts, the mode matrix,
workspace confinement, the effect gateway, the Blackbox recorder and the fault catalog all apply
unchanged.

**The enforced stage machine:**

```
inspect → bind → retrieve → diagnose → repair → test → review → authorize → push → verify → seal
```

| Invariant | Refusal | Enforced by |
|---|---|---|
| A dirty tree describes no SHA | `dirty_worktree` | `test_a_dirty_tree_refuses_to_bind_because_no_sha_describes_the_disk` |
| The repository moved underneath the session | `binding_diverged` | `test_a_repository_that_moved_underneath_the_session_refuses_every_later_step` |
| CI truth is bound to a SHA, never a branch name | — | `test_the_vertical_from_inspect_to_a_sealed_receipt` |
| A repair needs a diagnosis with executed evidence | `stage_violation` / `unsupported_claim` | `test_a_diagnosis_cannot_cite_a_step_this_session_never_ran` |
| A repair is a WORKSPACE_WRITE task needing an independent reviewer | `mutation_not_authorized` | `test_gate0_refuses_a_repair_with_no_independent_reviewer_before_any_model_call` |
| An approval needs a green run this session executed | `unsupported_claim` | `test_a_review_cannot_approve_without_a_green_run_this_session_executed` |
| The turn cannot authorize its own push | `operator_gesture_required` | `test_a_turn_cannot_author_its_own_authorize_push` |
| A plan that changed after consent is a different push | `plan_diverged` | `test_a_plan_that_changed_after_consent_invalidates_the_authorization` |
| Force-push and branch deletion are default-denied | `default_denied` | `test_force_push_and_branch_deletion_are_denied_by_default` |
| An identical push is blocked while unresolved | `duplicate_effect_blocked` | `test_a_severed_push_is_unknown_not_failed_and_blocks_an_identical_retry` |
| UNKNOWN is not FAILED | `unknown` | the same test |
| A simulated push verifies nothing and claims nothing | `not_applicable` | `test_a_simulated_push_never_moves_the_remote_and_claims_nothing` |
| A cancelled turn stops the session permanently | `cancelled` | `test_a_cancelled_turn_stops_the_session_before_any_further_effect` |
| A re-issued step_id replays and never re-executes | `replayed` | `test_a_re_issued_step_id_replays_and_never_executes_twice` |
| Concurrent writers to one path are serialized | — | `test_concurrent_writers_to_one_path_are_serialized` |
| A session survives a restart | — | `test_a_session_survives_a_restart_and_the_receipt_answers_from_the_journal` |

**Typed git operations:** `branch`, `commit`, `cherry_pick`, `revert`, `merge`, `tag`, `restore`.
A merge that conflicts reports the conflicted paths and leaves the tree conflicted rather than
guessing a resolution. `core/repoops/gitops.py` contains **no argv that expresses a force push or a
branch delete** — the default-deny is an absence, not an `if`.

**Authorize Push** is minted server-side at `POST /api/repoops/authorize-push`, owner-local only.
`repo_push_authorization` is in `core.request_trust.RESERVED_TRUST_KEYS`, so an inbound body
carrying one is stripped before any runtime sees it.

---

## 4. TASK_CONTRACT and Gate 0

`core.council.task_contract` and `core.council.gate0` were built correct, sabotage-proven, and
**constructed only by their own test file** — nothing in the product could be refused by them.
`core/repoops/task_law.py` is the wiring: it builds the typed contract from the session the
operator asked for, observes the facts from git and the live model-health circuit-breaker registry,
and hands both to the real `Gate0`. The refusal happens at `repo.session.open`, before a model has
been asked anything, so a wrong SHA, an unavailable provider, an exhausted budget or an
unauthorized mutation costs **zero model calls**.

A repair re-evaluates under a `WORKSPACE_WRITE` contract, which the task law refuses to build with
two seats holding one model identity — a mutation reviewed by the model that wrote it is not
reviewed.

**Enforced by** `test_gate0_refuses_an_exhausted_budget_and_names_the_axis`,
`test_gate0_refuses_an_unavailable_provider`, `test_gate0_refuses_a_wrong_base_sha`,
`test_gate0_refuses_a_repair_with_no_independent_reviewer_before_any_model_call`.

---

## 5. Plugin lifecycle

```
discover → inspect → install → verify → enable → invoke → update → revoke → uninstall → evidence
```

**Installed never means available, permitted or invoked.** `core.plugin_lifecycle.is_available` is
a conjunction re-checked on every offer: installed AND verified AND enabled AND not revoked AND the
pack still hashing to the digest it was verified at. Editing a manifest or a skill body after
verification drops the pack from the model's catalog until it is re-verified.

**Plugins and skills cannot grant authority.** A declared `permission_actions` tuple used to
short-circuit every derivation in `actions_for_tool`, including the argument-sensitive one. A
third-party declaration is now unioned with the floor its declared side-effect class implies and,
for a write class whose target exists, with `overwrite_existing_files`. The union is safe because
the mode matrix takes the strictest effect over the set, so it can only tighten. A skill contributes
text and an `allowed-tools` narrowing lever; it cannot seat a tool it was not offered.

**Credentials stay opaque.** A plugin record names binding ids and has no field that can hold a
secret. The one API returning a plaintext value is `core.credential_store.get_credential`, and no
plugin path reaches it.

**The store fails closed.** Atomic (mkstemp + `os.replace`), 0600, and an unreadable store offers
nothing. The previous store failed OPEN: a corrupt file meant nothing was disabled.

**Enforced by** `tests/test_plugin_lifecycle.py` (19 tests).

---

## 6. Duplicate authorities: removed, and counted

**Removed in this lane:**

| Module | What it duplicated |
|---|---|
| `core/remote_forge/gate.py` | permissions — its own role→permission matrix and token namespace |
| `core/remote_forge/execute.py` | effects, receipts and finality — its own ledger and render-boundary claim guard |
| `core/remote_forge/adapters.py::GitHubAdapter` | egress + credentials — raw `urlopen` to api.github.com on `GITHUB_TOKEN` from the environment |
| `channels/gateway.py` | the channel contract — a second, unwired one whose default handler authored an answer with no turn, no admission and no finalization |

**Counted, not yet removed.** `core/kas/egress_census.py` walks the product tree for modules that
reach the network without the door. Today: **27 modules**. Six are verified third-party bypasses
(the cloud-model lanes are the highest-volume egress in the product), seven are verified
local-service probes, and fourteen the census found that this lane has not read — labelled
`unreviewed` rather than given a reason nobody checked.
`tests/kas/test_egress_census.py` pins the set and the per-module counts: a new site fails the
gate, and so does removing one without deleting its ledger entry. The ledger is meant to shrink.

**Still present, and NOT addressed by this lane** — stated because it is the largest remaining
duplicate authority in the tree:

- `core/platform/broker.py` (261 lines) calls itself "the ONE admission/idempotency/UNKNOWN/receipt
  seam" and admits through `core.kernel.capabilities.check_tool_call` rather than
  `decide_tool_call`, with its own status vocabulary parallel to the effect gateway's.
- The pre-existing `core/repoops/{identity,wsfs,localgit,ci,evidence,archaeology,bootstrap,ci_analysis,local_lifecycle,pr_lifecycle}.py`
  (≈2,400 lines) route their mutations through that broker. They are unreachable from the product —
  no tool contract, no registry entry, no capability-graph row — and are exercised only by
  `tests/repoops/test_{bootstrap,ci_analysis,local_lifecycle,pr_lifecycle,repoops}.py` and four demo
  scripts. `core/repoops/localgit.py:89` runs `git fetch --all` (real network egress) with no
  admission at all, and `core/repoops/bootstrap.py:106` shells out to `git clone` on a
  caller-supplied URL.
- `core/egress_gate.py` declares itself the ONE exposure authority and has **zero production
  callers**; every lane teaches itself its own privacy policy.

The production vertical this lane delivers (`core/repoops/{contracts,plane,gitops,forge,task_law}.py`)
uses none of them.

---

## 7. Served and CLI surfaces

| Surface | Path | Guard |
|---|---|---|
| List sessions | `GET /api/repoops/sessions` | read-only |
| One session's journal | `GET /api/repoops/session?id=` | read-only |
| **Authorize Push** | `POST /api/repoops/authorize-push` | owner-local; the stamp is minted server-side |
| Lifecycle state | `GET /api/plugins/lifecycle` | read-only |
| Lifecycle acts | `POST /api/plugins/lifecycle` | owner-local |
| Terminal view | `python -m core.repoops {sessions,receipt,authority,plugins}` | read-only by construction — there is deliberately no push and no authorize here |

**Enforced by** `tests/repoops/test_repoops_served.py` (7 tests), which drives the same
`dispatch_get` / `dispatch_post` the HTTP server calls.
