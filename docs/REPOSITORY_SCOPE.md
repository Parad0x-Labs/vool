# Repository scope and subsystem status

VOOL's product center is the local agent: conversation, models, memory, tools,
permissions and inspectable results. This repository also preserves optional
integrations, networking research and historical source. Presence in the tree
does not mean a feature starts by default or is ready for a public deployment.

This map classifies source ownership and activation boundaries. It is not a
certification of every feature, a release manifest or a claim that research has
been abandoned. See [release status](trust/release-status.md) for distribution
status and [engineering status](STATUS.md) for more detailed implementation notes.

## Classification

| Area | Status | Evidence and preservation boundary |
| --- | --- | --- |
| Local API, chat, memory, tools and permission enforcement | Current runtime | `apps/vool_api_server.py`, `core/web/api/`, `core/agent_runtime/`, `core/execution/` and `storage/`. Preserve supported workflows. |
| Queen/coder/verifier roles and local orchestration | Current bounded runtime, configuration-dependent | `core/runtime_execution_tools.py` exposes `orchestration.execute_envelope`; `core/orchestration/` owns execution and result merging. These roles are also used by `core/runtime_install_profiles.py` and provider routing. They are not evidence of an enabled peer network. |
| OpenClaw-specific bridge, registration and launchers | Retired integration; residual wiring pending removal | OpenClaw skills belong in a separate repository, not VOOL's product scope. `installer/bootstrap_vool.sh` still passes setup options and `installer/install_vool.sh` still calls `installer/register_openclaw_agent.py`; this label does not remove those calls. Preserve generic API clients while decoupling product setup from the retired integration. |
| `.null` names, `null://`, Web0 browser and payment extensions | Retained extension surface; mixed activation boundaries | `core/web/api/service.py` serves `/web0`, `/null-browser`, `/api/web0/resolve` and `/api/null`; `core/null_protocol.py` parses the protocol. Remote dial has its own opt-in policy. These paths are not all governed by the research-networking flag. |
| UDP/TCP mesh, DHT, peer discovery and swarm retrieval | Experimental research; outside normal production networking | `core/runtime_mode.py` gates production entrypoints through explicit research opt-in. Owners include `core/daemon/`, `network/dht.py`, `network/transport.py` and `retrieval/swarm_query.py`. See [research systems](../research/README.md). |
| Public Hive presence, autonomous peer tasks and idle commons | Experimental research | `core/agent_runtime/presence.py`, `core/public_hive/` and the daemon task lane belong to research networking. Research service invocation is distinct from ordinary web research requested by a user. |
| Brain Hive, meet-and-greet, watch and coordination dashboards | Retained research/service surfaces | `apps/brain_hive_watch_server.py`, `apps/meet_and_greet_server.py`, `apps/meet_and_greet_node.py`, `core/brain_hive_*` and `core/dashboard/`. Service entrypoints are not required launch instructions for the desktop app. Shared rendering/query code stays in place. |
| NULLA-era environment names, homes, database names and bundle IDs | Old-install migration compatibility only | VOOL is the only current product name. `core/env_compat.py`, `core/runtime_paths.py` and the [compatibility map](VOOL_IDENTITY_COMPATIBILITY_MAP.md) preserve old installations until a tested migration can retire aliases. These are not an alternate brand or a reason to expose the old name in current product copy. |
| `recovery/historical-gold/` | Historical source preservation, not runtime | [Vault policy](../recovery/README.md), provenance and `tests/test_recovery_vault_isolation.py` describe its isolation. Retained code is not automatically an approved runtime dependency. |
| `nulladocs/remaining-direct-clock-callers.tsv` | Engineering inventory, not a second documentation site | The public documentation root is `docs/`, configured by `.gitbook.yaml`. Keep the clock-caller inventory until its owner reconciles it; a legacy directory name alone is not a deletion reason. |
| `vendor/`, `third_party/`, website and platform trees | Separate dependency or delivery ownership | Preserve licensing, packaging and platform contracts. A directory's absence from the default desktop boot path does not make it disposable. |

## Important mixed dependencies

The local queen/coder/verifier execution path is separate from remote Hive task
execution. Moving `core/orchestration/` into research would misclassify working
local tool contracts. Likewise, ordinary web search and model-provider requests
are not peer-network research.

Web0 is not a single inactive component: `core/runtime_backbone.py` starts a local
task-poll worker while building provider snapshots, and that loop also hosts update
checking. Its related registry, receipt and accounting modules must be traced at
their call sites before any future extraction. Labeling the peer stack research
does not prove all Web0 background work is disabled.

The `.null` protocol/domain surface is also distinct from legacy `.nulla_runtime`
data paths and from [Dark Null Protocol](SYSTEM_SPINE.md#related-ecosystem-projects).
Do not apply a repository-wide text rename across those meanings.

### Retired OpenClaw integration

OpenClaw-specific skills are maintained in the separate
[openclaw-skills repository](https://github.com/Parad0x-Labs/openclaw-skills).
VOOL must not present OpenClaw installation, registration or skill distribution as
its product purpose. Remaining bootstrap options, installer registration, generated
launchers and OpenClaw-specific documentation are removal candidates. Removing
them requires install/upgrade and direct VOOL launch verification, plus retention of
generic API compatibility where it serves other clients. This documentation pass
classifies the residual integration; it does not claim the installer is already
decoupled. This classification does not move or publish any skills.

### Implementation status is not activation state

`core/feature_flags.py` is a descriptive implementation-status catalogue, despite
its name. `core/runtime_capabilities.py` consumes that catalogue together with
`RuntimeContext.feature_flags`; production research activation has an additional
authority in `core/runtime_mode.py`. A catalogue value such as `implemented` or
`partial` does not establish that a service is running or allowed in this process.
Capability reporting must be checked against those actual gates before making
product claims. These existing readers cannot safely be removed as unused text.

## Research activation and packaging

The production peer-network boundary is owned by `core/runtime_mode.py`, including
`mesh_daemon_boot_allowed` and `background_presence_threads_allowed`. Dedicated
research runners can explicitly enable `VOOL_RESEARCH_NETWORKING`; starting one
is not the same operation as starting the normal local API. See the research guide
before running peer services.

Research source remains in the public tree and some entrypoints remain in package
metadata. **Disabled by default is not the same as excluded from an artifact.**
Do not infer package contents, internet-deployment readiness or universal network
isolation from a documentation label. Tests cover specific boundaries; direct
service invocations and shared helpers need their own review.

## Cleanup policy

Keep source, imports, persisted identifiers, tests and security checks intact when
improving labels. Link to this map from subsystem entrypoints instead of presenting
research architecture as the default product. Historical implementation notes
describe their period; they are not current release promises.

A future move or extraction needs an import/caller and packaging inventory,
persisted-data review, explicit destination/ownership and cumulative validation of
both the retained runtime and extracted subsystem. No such move is part of this
classification pass. Inactive or uncertain code remains marked for investigation;
it is not silently removed or exempted from security review.
