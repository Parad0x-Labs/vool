# VOOL — Windows Lane Capability Matrix

**Lane:** Windows (Windows 10 19045, 8-core CPU, 8 GB RAM, GTX 1080 8 GB VRAM, driver 582.66)
**Original audit:** 2026-07-19 against `main` @ `1e4fd82` (runtime reported `build_id 0.4.1-closed-test+1e4fd82d09d7`, `dirty=false`)
**Re-verified:** 2026-07-20 against `main` @ `b88e8a9` — see [What moved since `1e4fd82`](#what-moved-since-1e4fd82)
**Sources:** five independent Windows audit passes (tooling, chat/model runtime, install/app shell, cloud+spend, UI/API), each driving real entry points on this box, plus a 2026-07-20 re-verification pass that re-ran the still-broken rows rather than assuming them unchanged.
**Status of the product itself:** closed test, unaudited by any third party. Nothing described here is security-audited, and no component is on mainnet in this lane.

**Apple / Linux lane:** start at [Parity checklist](#parity-checklist-for-the-applelinux-lane). It states, per capability, what to run on macOS/Linux, and which items are shared Python (already yours, no porting) versus Windows-only by nature.

## KAS Windows validation handoff

The frozen rows below describe the Windows 10 / GTX 1080 audit at `1e4fd82`; they remain untouched.
This KAS-owned section records Windows 11 / RTX 4060 evidence through the Windows 0.4.4 closed-test
release built from exact clean `main` `e35b634`.

| Profile field | Verified value |
|---|---|
| Operating system | Microsoft Windows 11 Home, version 10.0.26200, build 26200 |
| CPU / memory | 10 physical cores, 16 logical processors, 31.8 GiB RAM |
| GPU | NVIDIA GeForce RTX 4060, 8188 MiB VRAM, driver 610.62 |
| Source revision | Windows `0.4.4` closed-test release built from exact clean `main` `e35b634` |
| Base revision | `e35b634` |
| Hardware evidence | `validation-logs/handover-44-20260719/kas-windows-profile.txt` |

### KAS context-integrity firewall implementation candidate

This KAS-only subsection records source evidence from
`agent/context-integrity-firewall-20260730`, rebased onto exact current
`main` `507e4fe`. It does not alter the frozen Windows 10 / GTX 1080 rows, make an Apple claim,
or claim that the shipped Windows 0.4.4 installer contains this implementation. Installed/live
evidence from earlier candidate commits remains historical and is not transferred to this head.

| Capability | KAS result | Evidence |
|---|---|---|
| Chat and project context is default-deny before ranking | VERIFIED on the exact installed candidate; source and live local-model paths exercised | Server-created `ContextAccessPolicy` permits current-chat content automatically and requires explicit grants for project/profile/other-chat material; records without provable scope or provenance are quarantined before ranking. Unique-marker tests cover new chats, cross-chat and cross-project isolation, archives, simultaneous chats, caches, provider switches, and tool-result boundaries. The installed candidate was launched with isolated state and served a real local `qwen2.5:7b` turn with a linked context manifest |
| Chat namespace lifecycle and memory freshness | VERIFIED in source regression; installed model path proven, lifecycle UI not claimed | Additive namespace/import/capsule storage covers create, archive, restore, duplicate, branch, and delete. Structured facts carry authority/status metadata; user corrections supersede lower-authority values, equal-authority conflicts are disputed, and assistant guesses are not promoted as authoritative memory. The installed proof demonstrates the runtime reaches the guarded provider path; it does not claim every lifecycle operation was manually driven |
| Context Capsule and prompt provenance | VERIFIED on direct, buffered, streaming, and installed live local-model paths | Immutable capsule versions retain objective, decisions, constraints, unresolved work, verified actions, and receipt references. Tested provider paths seal a signed, redacted manifest for the final payload; the installed live turn produced linked context/provider manifests and a final payload hash without raw secrets or duplicated prompt text |
| Canonical VOOL ecosystem grounding and structural response constraints | VERIFIED in source regression; exact rebuilt installed blind evaluation pending | Ecosystem questions retrieve allowlisted repository documentation with source path/content hash instead of fixed answer strings. Exact-word requirements are enforced structurally with one bounded retry; expected answers are not embedded in production modules. Installed seeds exposed missing spelled counts, a repair that replaced a usable draft, and prose-only six-word counting that returned generic fallback instead of identity. Candidate `8de6b10` then proved a string regex in the first structured repair could not compile on the shipped Ollama/llama.cpp grammar transport. Longer exact counts now use a transport-portable exact-length array, strict local non-empty one-word validation, and edge-punctuation normalization; all answer words remain model-generated. Rebuilt installed proof remains required |
| Cumulative regression against current main | VERIFIED locally | Exact full local pytest after the installed-seed response-control, transport-portable schema repair, and worker-probe fixes: `9,319 passed, 108 skipped, 16 xfailed, 14 xpassed, 179 warnings, 62 subtests`. The focused response/runtime pack passed `162` tests with one expected xfail, the current four-worker run exited cleanly, and full repository Ruff is clean. The worker probe uses bounded `nvidia-smi` output instead of importing Torch/CUDA into the API process merely to size helper lanes |
| Installed Windows bundle, mandatory live local-model acceptance, and blind three-seed evaluation | HISTORICAL candidate proof only; exact rebased head unverified | The earlier `ed8346a` candidate passed the bundle/live/blind gates. This rebase changes the source identity, so that receipt is not proof for the current head. No shipped-installer claim is made; the final stacked Windows handoff will rebuild and re-drive from its exact clean commit |

**Context-integrity boundary:** this is a locally verified review candidate, not a shipped or fully
certified capability. Earlier candidate installed/live paths passed, but the exact rebased head has
not inherited that proof, and this does not prove that every possible context leak is eliminated.
The branch and current main are Ruff-clean; GitHub Actions is live again.
No credential, raw provider prompt, or absolute local path is included here.

### KAS baseline-failure cleanup follow-up

This KAS-only subsection records the clean-main baseline split and the Windows/shared fixes on
`agent/kas-baseline-fixes` from `origin/main` `3c2ebd1`. It does not alter the frozen Windows 10 /
GTX 1080 rows, make an Apple claim, or claim that the shipped `v0.4.4-closed-test` installer
contains this follow-up.

| Capability | KAS result | Evidence |
|---|---|---|
| Windows-like home resolution honors explicit `HOME` and `USERPROFILE` before `expanduser()` | VERIFIED in source regression | `resolve_named_folder_under_home()` now honors explicit profile variables with a safe `expanduser()` fallback; Desktop precedence and separator-insensitive matching pass in `tests/test_folder_audit.py` |
| Explicit workspace search reaches the real workspace runtime before folder overview | VERIFIED through the real `VoolAgent.run_once` entry point | File-content search prompts return grounded matches; the generic “find where ... is wired in this workspace” path reaches tool intent instead of a folder summary; OpenClaw regression tests pass |
| Broad folder overview remains grounded and deterministic | VERIFIED through the real `VoolAgent.run_once` entry point | `what is this project about` still returns the bound workspace listing and README content; the overview-precedence regression passes |
| Clean-main baseline failure split | PARTIAL — four KAS/shared failures fixed; six Apple failures remain | Clean `main` `3c2ebd1` reproduced all 10 original failures. The remaining Loop-owned tests are four close-to-Dock contract tests in `tests/test_bundle_tooling.py` and two branded-ICNS contract tests in `tests/test_desktop_shortcut_icon.py`; no Apple files were changed here |
| Affected-suite validation for this follow-up | VERIFIED locally; six Apple baseline failures remain outside KAS scope | `tests/test_folder_audit.py` plus `tests/test_openclaw_tooling_context.py`: 149 passed; changed-file Ruff and compileall passed. Full pytest: 5,749 passed, 76 skipped, 15 xfailed, 13 xpassed, with only the same four close-to-Dock and two ICNS Apple failures. Four-worker shards completed with the same six failures and no KAS/shared regression. Full Ruff has six unrelated baseline violations in tests |
### KAS-verified N12 routing and runtime truth (source checkout)

This KAS-only subsection records the Windows source implementation on the current checkout
`3c2ebd1`. It does not alter the frozen Windows 10 / GTX 1080 section or the Apple matrix, and it
does not claim that the shipped `v0.4.4-closed-test` installer contains these changes.

| Capability | KAS result | Evidence |
|---|---|---|
| `cloud model auto` activates the verified-free cloud policy for a key-present owner-local chat | VERIFIED in Windows source regression; live catalog/chat remains UNVERIFIED | `set_cloud_model` is the production caller for `set_free_cloud_enabled`; `memory_first_router.resolve()` builds `CloudTaskRequirements` only after the key, policy, and owner-local gates pass; `tests/test_n12_free_cloud_routing.py::test_cloud_model_auto_enables_the_verified_free_lane` and `::test_resolve_builds_real_requirements_for_key_present_owner_chat`; 72 focused tests passed |
| Natural free-OpenRouter switching uses the real provider path and cannot be satisfied by local-model prose | VERIFIED in deterministic source regression; live UI/provider E2E remains UNVERIFIED | Live-catalog-selected models call the cloud model control path with verified-free evidence and report the activated OpenRouter provider; `::test_natural_free_model_switch_reports_openrouter_provider` |
| Paid fallback remains authorization-gated | VERIFIED by existing routing gates plus focused regression | The N12 path does not grant paid authorization; paid reservations still require server authorization and no paid request was made |
| Provider deadline begins immediately before provider invocation | VERIFIED by source regression/code inspection; live timing remains UNVERIFIED | Fallback deadline is started immediately before mux, race, or sequential provider invocation in `core/memory_first_router.py`; focused router regression passed |
| Native structured-output/schema requests | VERIFIED in adapter source regression; live provider support remains UNVERIFIED | OpenAI-compatible requests use provider-advertised JSON schema support and only set strict mode when explicitly advertised; Ollama receives the native schema format; `::test_openai_compatible_structured_request_uses_native_schema` |
| Local residency versus active inference is visible | VERIFIED in source/UI contract regression; installed UI remains UNVERIFIED | Routing decisions and chat/turn evidence expose `residency`, `active_inference`, provider, and model; `::test_execution_visibility_distinguishes_local_residency_from_active_inference` |
| Shared tool-loop runtime events retain safe redacted arguments | VERIFIED in Windows source regression | Selection, planner, and model-selected events use the existing secret-safe redactor; `tests/test_runtime_task_events.py::RuntimeTaskEventsTests::test_tool_selection_persists_redacted_arguments`; executor-specific events owned by the other lane were not changed |

**N12 boundary:** no OpenRouter key was used, no live cloud request was made, no paid model was
called, and no installed-bundle or shipped-installer claim is made. A clean 0.4.4 candidate was
rebuilt from this branch and Inno compiled it successfully (SHA-256
`5A4FBB1A590CA72AC1A3AD5EB2471771E10A84A6CA5906CEE98E63E4FB9AEF6A`), but installed runtime/live
E2E remains unverified because the existing user runtime owns port 11435. The implementation is
local to this KAS branch until review and merge.

### KAS-verified VOOL Checkpoint A: OpenRouter attribution

This KAS-only addendum records the shared-runtime verification from current `main` `fc162de`
and the feature branch that closes the uncovered request paths. The frozen Windows 10 / GTX 1080
section above and the Apple matrix are unchanged.

| Capability | KAS result | Evidence |
|---|---|---|
| VOOL OpenRouter attribution values and fixed-last enforcement | VERIFIED in local request-path tests; live packet capture remains UNVERIFIED | Canonical factory returns `HTTP-Referer=https://vool.dev`, `X-OpenRouter-Title=VOOL`, and `X-OpenRouter-Categories=personal-agent,programming-app,general-chat,writing-assistant`; adapter caller headers are overwritten; `tests/test_openrouter_attribution.py` covers adapter, provider, transport, catalog, and auth-probe paths |
| OpenRouter routing boundary | VERIFIED by code and mocked transport paths | Requests remain direct to `openrouter.ai`; no proxy or gateway path was added. Policy-bound transport reapplies the canonical headers immediately before dispatch; no paid/live request was made for this verification |

### KAS-verified Windows VOOL deep-link handoff

This KAS-only addendum records the Windows portion of VOOL OAuth Checkpoint B that can proceed
before the OpenRouter OAuth app registration. The frozen Windows 10 / GTX 1080 section above and
the Apple matrix are unchanged. The handler is source-verified but not yet present in an installed
bundle until this branch is reviewed, merged, and rebuilt.

| Capability | KAS result | Evidence |
|---|---|---|
| Per-user `vool://auth/openrouter/callback` registration | PARTIAL — source and installer contracts verified; installed-bundle hand test pending | `installer/bundle/vool.iss` registers `HKCU\Software\Classes\vool` with `PrivilegesRequired=lowest`; `vool_protocol_handler.py` validates and URL-decodes `code` + `state`, starts/reuses the bundle supervisor, and posts only to the shared loopback callback endpoint at `127.0.0.1:11435`. Main's shared `core/oauth_callback.py` keeps the callback in memory and consumes it once; the duplicate ephemeral listener was removed during rebase. `tests/test_vool_protocol_handler.py` plus `tests/test_vool_url_scheme.py`: 14 passed; Inno Setup scratch compile and installed-bundle handoff remain pending. No PKCE exchange, website request, or secret persistence was added. |

### KAS-verified Windows stability changes from rebased PR #37

| Committed change | KAS result | Evidence |
|---|---|---|
| Zero-second expiry for pending registration and self-update offers | VERIFIED | 42-test committed regression scope passed; `kas-windows-regression-scope.txt` |
| Withdrawn offline presence is excluded at the expiry boundary | VERIFIED | 42-test committed regression scope passed; `kas-windows-regression-scope.txt` |
| Peer endpoint selection is deterministic when Windows timestamps collide | VERIFIED | 42-test committed regression scope passed; `kas-windows-regression-scope.txt` |
| Simulated POSIX stop-path test runs on Windows | VERIFIED as test portability only | `tests/test_vool_stop.py` passed in the committed regression scope; no production macOS claim |
| Repository lint for the committed checkout | VERIFIED | `python -m ruff check .` → `All checks passed!`; `kas-windows-regression-scope.txt` |
| Cumulative full local pytest regression | VERIFIED | `5,385 passed, 76 skipped, 15 xfailed, 13 xpassed`; `pytest-full-rebased-fe3e45e.txt` |
| Four-worker CI-equivalent shard regression | VERIFIED | All four shards exited 0; `pytest-shards-rebased-fe3e45e.txt` |
| Wheel/package build and import smoke test | VERIFIED | Wheel built and `apps`, `core`, and `installer` imported successfully |

`0326477` records the earlier Windows stability and test-portability hardening. The #42 KAS
section below adds only capability-ledger truth evidence and makes no Apple/Loop claim.

### KAS-verified Windows baseline health sweep

This subsection records the KAS Windows source checkout based on current `main` `fc162de`.
It is locally verified evidence for the health branch, not a claim that the shipped
`v0.4.4-closed-test` installer already contains these changes. The frozen Windows 10 / GTX 1080
section above and the Apple matrix are unchanged.

| Change | KAS result | Evidence |
|---|---|---|
| Windows live RAM sensing for the resource governor | VERIFIED in source regression | `core/system_resources.py` uses the Windows `GlobalMemoryStatusEx` API without a new dependency; image/resource tests pass on the Windows runner |
| Filesystem-root project rejection | VERIFIED | `Path(root).parent == Path(root)` rejects the drive root on Windows as well as `/`; `tests/test_project_store.py::test_create_rejects_missing_and_non_dir` passes |
| Resource and optional-media paths fail soft when a local daemon/skill is unavailable | VERIFIED in source regression | Ollama resource probes return zero on probe exceptions; injected local-render runners still exercise parsing/error contracts when the optional skill is absent; image autostart and local-media tests pass |
| Telegram setup no longer hijacks live-info, research, or builder requests | VERIFIED in real entrypoint regression | `latest telegram bot api updates` reaches planned live lookup; explicit setup remains deterministic and model-free; milestone, acceptance, OpenClaw, and chat-truth suites pass |
| Cloud-model command rejects malformed ids before catalog search | VERIFIED | `cloud model bad!id` returns the structured model-id rejection and does not change persisted policy |
| VOOL OpenRouter attribution expectations remain aligned | VERIFIED in source regression | Outbound attribution tests require `HTTP-Referer=https://vool.dev`, `X-OpenRouter-Title=VOOL`, and the four-category value; header names and internal `VOOL_*` identifiers remain unchanged |
| Baseline health after cumulative fixes | VERIFIED locally on Windows | Ruff green; full `python -m pytest -q`: `5,575 passed, 76 skipped, 15 xfailed, 13 xpassed, 50 subtests`; four-worker isolated shard run: all four shards green (`1,613 + 1,332 + 1,496 + 1,133 passed`); the Ollama prewarm assertion now pins an 8 GB RAM probe, with the focused API/adaptive/local-Ollama/docs suite at `146 passed, 2 skipped`; no baseline failures remain on this branch |

The branch remains pending review/merge and a post-merge Windows installer rebuild. The current
shipped installer is still sourced from `e35b634`; no installed-bundle capability is claimed for
the health-branch changes until that rebuild is performed.

### KAS-verified Tier 1 correctness fixes

These rows were exercised on the KAS Windows 11 / RTX 4060 profile from current `main`.

| Committed change | KAS result | Evidence |
|---|---|---|
| Workspace search excludes `.pyc`, `__pycache__`, and build/cache noise and finds eligible matches beyond the old 500-file budget | VERIFIED | Real `execute_tool_intent` search finds the target after 700 eligible files; repository search for `def load_builtin_tools` includes `tools/registry.py`; focused gate passed 45 tests with 2 skips and exact-head full pytest passed 5,406 tests |
| Workspace search distinguishes a bounded/truncated negative result from a complete no-match | VERIFIED | Default search scans all eligible files with `truncated=False`; explicit `scan_limit=500` returns `truncated_no_results` with the limit in structured details |
| Workspace symbol search finds definitions beyond the old 500-file budget | VERIFIED | Real repository symbol search for `load_builtin_tools` completes with `truncated=False` and includes `tools/registry.py`; targeted regression passed |
| Deterministic arithmetic accepts `multiplied by`, `divided by`, `times`, `plus`, and `minus` without sending a model request | VERIFIED | `4821 multiplied by 37` and additional operator cases pass in `tier1-regressions.txt` |
| Runtime tool arguments are fail-closed; unknown `directory=Downloads` is rejected instead of defaulting to Desktop | VERIFIED | `machine.list_directory` returns `ok=False`, `status=invalid_arguments`, with unknown/allowed keys |
| Orchestration payload cleanup preserves adjacent user-facing content across routing, envelope, and capacity fragments | VERIFIED | Real `run_once` preserves `4821 * 37 = 178377.` while removing mixed `task_envelope`/`capacity_state` output; balanced inline JSON removal preserves safe text on both sides; 27 marker variants plus existing routing-only fallback tests pass. On the exact stacked review head, the combined focused gate passed 129 tests with 2 skips and full pytest passed 5,443 tests |
| Current PR cloud-auto integration test is provider-explicit and passes | VERIFIED | `tests/test_cloud_free_lane_reachability.py` passed in the final focused regression run; full result is in `pytest-full-rebased-fe3e45e.txt` |

### KAS-verified Tier 2 truth, stability, and installer fixes

These rows describe the Tier 2 implementation tested from the rebased KAS Windows working tree
and the final clean-cache packaged artifact. The packaging changes and this KAS evidence section
are committed together. Paid BYOK remains disabled.

| Committed change | KAS result | Evidence |
|---|---|---|
| Capability ledger reports browser support only when policy, Playwright, and the runtime dependency path are available | VERIFIED | One `web.browser` row remains; disabled policy/dependency state is `unsupported`/`unavailable`; #42 ledger regression passed in `capability-ledger-42-dc4532e.txt` |
| Hive read capability fails closed until live reachability is verified, while retaining only configured dispatch paths | VERIFIED | Configured watcher-only state is `supported=false`, `support_level=partial`, `availability_state=configured_unverified`; an unreachable real `hive.list_available` call remains `unreachable`; explicit reachability probes cover `reachable` and `unreachable` in `capability-ledger-42-dc4532e.txt` |
| `sell.quote` is represented as an unsigned simulated quote, not settlement or wallet custody | VERIFIED | Ledger and tool description state `simulated`/`partial`; #42 capability-claim regression passed in `capability-ledger-42-dc4532e.txt` |
| Research evidence labels are normalized from raw `strong_evidence` and downgraded when source notes/domains are insufficient | VERIFIED | The helper is exercised with weak and sufficient note/domain sets; adaptive-research and web-tool callers already use it; evidence is in `capability-ledger-42-dc4532e.txt` |
| Cumulative #42 regression and four-worker shard validation | VERIFIED | Ruff passed; rebased `5,396 passed, 76 skipped, 15 xfailed, 13 xpassed`; focused capability suite `28 passed`; current-base retry shard run all four exited 0. First pre-rebase shard run had one transient transport-port failure that passed in isolation; `capability-ledger-42-dc4532e.txt` |
| Bundle manifest is the selected-model and model-store source for launcher, runtime defaults, model store, and doctor | VERIFIED in clean staged and installed bundle | Manifest/model consistency, doubled-separator compatibility, and doctor tests select `qwen2.5:7b` over a conflicting caller model and resolve the declared store; `installer-doctor-43-e416142.txt` |
| Failed bundle model pull cannot coexist with a ready local provider claim | VERIFIED | API startup and doctor regression tests report `blocked` with the pull error |
| Embedded-Python dependency bootstrap installs `setuptools`/`wheel` first, then the unchanged runtime list with `--no-build-isolation` | VERIFIED | Fresh clean-cache `build_bundle.ps1` completed; embedded `pip check`, imports, and compile passed; `installer-doctor-43-e416142.txt` |
| Bundled official Ollama runtime includes `llama-server.exe` and its runner/DLL support tree | VERIFIED | Clean stage is 3,188 MB; `ollama\lib\ollama\llama-server.exe` is present; final installed runner hash matches the stage; `ollama-runner-probe-fe3e45e.txt`, `installed-ollama-runner-fe3e45e.txt` |
| Clean-cache bundle build and Inno Setup packaging produce the expected artifact | VERIFIED | Fresh stage completed with the official runtime; Inno Setup compiled a 1,468,578,439-byte `VOOL-Setup.exe`; SHA-256 `CD72A745281F979B448823EC3720E0CE6C8201FA60AA3448D72E69F12191DDA6`; `installer-doctor-43-e416142.txt` |
| `VOOL-Setup.exe` installs the bundle into a fresh directory without overwriting the existing installation | VERIFIED | Logged silent install exit 0; scratch install contains root launcher, manifest, embedded Python, Ollama, supervisor, doctor, and app source; `installer-doctor-43-e416142.txt` |
| Installed runtime imports, dependency consistency, Python compilation, and staged-file integrity | VERIFIED | Installed `pip check`, imports, `compileall`, and runner hash comparison passed; `installer-doctor-43-e416142.txt` |
| Launcher chain is explicit and distinct from the installer artifact | VERIFIED | `VOOL-Setup.exe` is the Inno installer; installed shortcut chain is `wscript.exe` → `vool.vbs` → `VOOL.cmd` → bundled `pythonw.exe bundle_supervisor.py` → Ollama/API/window; there is no standalone launcher EXE |
| Bundle launcher parses the Windows root path and BOM-written manifest correctly | VERIFIED | Scratch launch state recorded the clean install root and `model=qwen2.5:7b`; BOM fixture and packaged manifest passed supervisor/core reader checks |
| Bundle API startup is delegated without a synchronous multi-GB pull; doctor understands bundle layout and writes its report outside the inspected tree | VERIFIED for bundle-specific truth; overall doctor report PARTIAL | Installed doctor selected the manifest model and wrote outside the install tree; installed API served `/healthz` and `/v1/models` on isolated port `11436` using an already-installed model; generic overall doctor status remains `degraded` because non-bundle launchers/receipt are absent from a self-contained stage; `installer-doctor-43-e416142.txt` |
| Scratch launcher health surface | PARTIAL with ownership boundary | Final installed supervisor created state/logs and stopped cleanly; `/healthz` and `/v1/models` were already served by a pre-existing source API on port 11435, and the machine's official Ollama daemon was already healthy on 11434. Packaged Ollama ownership is proven separately by the live runner probes; packaged API-child ownership remains unclaimed because existing listeners were preserved |
| Windows bundle supervisor prevents duplicate instances, restarts failed Ollama/API children with bounded backoff, records health transitions and restart diagnostics, and remains stoppable | VERIFIED on the KAS Windows branch in controlled supervisor tests | Simulated child exit/restart, startup-timeout backoff, HTTP health truth, lease contention/release, persistent state/logs, and full `--once` cleanup passed; the packaged stop path remains covered by prior installed-bundle evidence; no claim that the original silent-death root cause is known; `runtime-supervisor-41-dc4532e.txt` |
| Source and bundle launchers recognize both module-form and script-form API processes for stale recovery | VERIFIED as launcher contract | `Start_VOOL.bat` contract test covers both `apps.vool_api_server` and `vool_api_server.py` forms |
| Bundle supervisor clears stale source/bundle API Python processes before starting its own unhealthy API child | VERIFIED in controlled Windows contract path | PowerShell process filter is restricted to `python.exe`/`pythonw.exe` API command lines; restart, backoff, lease, stop, and diagnostics tests pass; `installer-doctor-43-e416142.txt` |
| Four-worker shard runner uses the active Python interpreter instead of requiring a standalone `pytest.exe` | VERIFIED | Final four-worker run launched all shards as `python -m pytest`; all four exited 0; `installer-doctor-43-e416142.txt` |
| Usage metering under parallel test/runtime load | VERIFIED in focused, full-suite, and repeat-shard regression | Spend-cap tests use a per-test SQLite database and require `record_usage()` success. Production metering retries only confirmed SQLite `busy`/`locked` errors with a bounded delay and remains fail-soft for every other error. Focused usage/spend regression: 67 passed; exact full pytest and three consecutive clean four-worker runs passed with the cumulative stability fixes |
| Contribution-receipt ordering when timestamps collide | VERIFIED in focused regression | Chain-tail lookup and receipt listing use SQLite insertion order (`rowid`) after timestamp, so equal timestamps preserve append order instead of sorting random receipt UUIDs. Equal-timestamp chain verification and reward-engine receipt selection pass; focused scope: 9 passed |
| Windows local-subprocess cancellation timing | VERIFIED in repeated real-process regression | The test now matches the implementation's five-second `taskkill /T /F` timeout plus bounded wait, still requires the spawned process tree to be dead before `invoke()` returns, and retains an eight-second bound below the request timeout. Ten consecutive focused runs passed |
| Ephemeral UDP plus required stream startup under port contention | VERIFIED in focused, stress, full-suite, and repeat-shard regression | Ephemeral startup reserves an OS-approved TCP port first, binds UDP to the immediately preceding port, and hands the already-bound TCP socket to the stream server. This removes the adjacent-port race and avoids repeatedly selecting Windows-denied TCP ranges. It never reports `running=True` with a missing required stream. Explicit non-ephemeral configurations retain fail-soft startup and audit `explicit_port_fail_soft`. Focused transport scope: 19 passed; 30 consecutive start/restart stress runs passed without a skip; exact full pytest and three consecutive clean four-worker runs passed |
| Windows path and atomic-replace test portability | VERIFIED in focused rerun | Workspace assertions normalize relative paths with `as_posix()`. Concurrent cloud-state readers treat Windows' transient sharing denial during another writer's atomic replacement as unavailable, while still requiring every successful read and the final file to parse. These are test-contract changes only; no cross-platform runtime claim |
| OpenRouter connection-state shard isolation | VERIFIED in contaminating-order replay | The OpenRouter-specific fixture pins the active provider to OpenRouter, so a persisted provider selection left by an earlier shard file cannot make its fake OpenRouter credential appear absent. The exact 12 preceding shard files plus the connection-state module passed 159 tests in order. This is test isolation only; production provider selection remains unchanged |
| Cumulative Windows four-worker stability gate | VERIFIED after host-state audit | Exact full pytest: `5,413 passed, 76 skipped, 15 xfailed, 13 xpassed, 50 subtests`. Three consecutive clean four-worker runs then passed all four shards. A discarded earlier third run hit `WinError 1450` while four detached `llama-server.exe` processes from a July 20 scratch packaging experiment held roughly 26 GB of committed memory; the affected tests passed immediately after stopping only those stale experiment processes. No production service was stopped and no assertion was weakened |
| Free/no-spend OpenRouter UI/API E2E with a saved temporary key | UNVERIFIED | No OpenRouter key was present in the current local credential store or environment during this pass; no paid calls were made and no key was logged |
| Free/no-spend OpenRouter UI/API E2E with a saved temporary key | VERIFIED on installed 0.4.4 candidate | Real Settings save produced a green pill; live auth passed; the catalog returned 342 models / 17 free in free-first order; natural-language HY3 selection persisted `tencent/hy3` without executing it; natural-language refresh reported the same counts; a selected zero-price model served 177 tokens through `nvidia/nemotron-3-ultra-550b-a55b:free` at $0.0000 actual provider cost; Settings showed the model/tokens/cost; key removal left zero stored credentials, a gray pill, and local Auto. The temporary key and clipboard were cleared; no key entered logs or history |
| Exact-zero provider cost remains zero in natural-language usage reports | VERIFIED in source regression and corrected installed candidate | Installed E2E exposed `format_report()` treating numeric `0.0` as missing and displaying the fallback estimate as actual. The formatter now selects `usd` whenever `all_actual` is true, including zero; focused cloud/usage scope passed 48 tests, full pytest passed 5,406 tests, and the installed `a0044f3` candidate rendered a seeded zero actual cost as `$0.000000 actual` |
| Embedded dependency bootstrap fails closed | VERIFIED in clean rebuilt artifact | The first `0541439` rebuild was rejected after installed `pip check` and runtime imports found no embedded `site-packages`. The `a0044f3` builder throws after get-pip, setuptools/wheel, runtime dependency installation, or `pip check` failure; a clean-cache rebuild then emitted `No broken requirements found`, and staged plus installed dependency/import checks passed |
| Merged #29 adaptive local context sizing / #10 live verification | VERIFIED on this Windows 11 / RTX 4060 bundle path | Policy diagnostics for bucket `B` keep `heavy_reasoning` at or above `general` (qwen3:8b `8192` vs `6144`; deepseek-r1:14b safely capped at `4096`); final installed qwen2.5:7b at `6144` and qwen3:8b at `8192` both returned `OK` through the packaged runner; `ollama-live-context-fe3e45e.txt`, `installed-ollama-runner-fe3e45e.txt`, `installed-ollama-heavy-fe3e45e.txt` |

### KAS Windows 0.4.4 release

This section records only the KAS Windows 11 / RTX 4060 release lane. Candidate rows retain their
historical boundaries; the final table below is the exact-main release proof.

| Candidate capability | KAS result | Evidence |
|---|---|---|
| Runtime/package/release-channel version is single-sourced at `0.4.4` | VERIFIED by contract tests | App-version, bundle, provenance, and documentation contract gate: 33 passed; cumulative focused gate: 124 passed, 2 skipped |
| Bundle stamps exact source provenance and carries version/commit/dirty-state metadata | VERIFIED in staged and installed bundle | Manifest and staged `build-source.json` report `0.4.4`, source `a0044f3b9590`, and `dirty=false`; installed `/api/runtime/version` reported `0.4.4-closed-test+windows+a0044f3b9590` |
| Current-main Windows path portability | VERIFIED | Workspace-search path comparison uses POSIX-normalized relative paths; concurrent cloud-state test tolerates Windows' transient replace/read sharing denial while still requiring every successful read and the final file to parse |
| Full local regression | VERIFIED | Ruff green; exact fail-closed candidate `a0044f3`: `5,406 passed, 76 skipped, 15 xfailed, 13 xpassed, 50 subtests`; exact stacked review head including the focused stability precursor: `5,415 passed, 76 skipped, 15 xfailed, 13 xpassed, 50 subtests` |
| Four-worker CI-equivalent regression | VERIFIED on exact stacked review head | Three consecutive independent four-worker runs passed all four shards using the active project interpreter. The fixes cover isolated spend ledgers, deterministic equal-timestamp receipt ordering, real Windows cancellation timing, race-free required stream startup, and OpenRouter fixture isolation. No assertion was relaxed |
| Wheel/package build and import smoke | VERIFIED | 0.4.4 sdist/wheel built; clean temporary venv installed the wheel and imported the runtime/package surfaces |
| `VOOL-Setup.exe` 0.4.4 clean build | VERIFIED as a candidate — not shipped | Fail-closed clean-cache stage was 3,188 MB with no embedded model; Inno Setup 6.7.3 produced 1,467,715,847 bytes, SHA-256 `6948477E7ECF35815E895C11BC9BC72FD87E382DD26BC6BD66321EEFC7021F20`; unsigned because this machine has no code-signing certificate |
| Isolated silent install and payload integrity | VERIFIED | Silent install completed without restart; Add/Remove Programs registered `VOOL version 0.4.4`; required launcher/supervisor/Python/Ollama/doctor files were present; staged-to-installed hashes matched for the manifest, provenance stamp, embedded Python, Ollama, and `llama-server.exe`; `pip check`, imports, and compileall passed |
| Installed doctor and model-store truth | VERIFIED for bundle truth; generic overall report remains degraded by design | Doctor detected the installed manifest, selected `qwen2.5:7b`, resolved the isolated persistent model store, and wrote its report outside the install tree |
| Installed API/model/live-chat proof | VERIFIED with candidate boundary | Corrected `a0044f3` installed API served 11 models and reported its exact Windows build identity. The preceding `13ae647` installed candidate ran `qwen2.5:7b` and returned exactly `OK` with 56 prompt and 2 completion tokens; no model download or cloud call occurred. No claim is made that the model call was repeated on `a0044f3` |
| Installed supervisor recovery and stop | VERIFIED | Forced termination of installed API PID produced a new installed-Python PID, restart count 2, and the same exact build ID; supervisor stop removed state and left zero installed processes; the pre-existing source API was restored afterwards |
| Windows release gauntlet | VERIFIED for CI-equivalent optional-OpenClaw scope | Focused Windows tests, provider probe, and Windows package build passed in the release-preparation run. A post-build isolated rerun against `a0044f3` again passed the focused Windows regression and provider probe; installer, benchmark, and package rebuild were deliberately skipped because their exact candidate checks were already complete. OpenClaw was excluded and recorded as optional/skipped rather than invoking the configured external agent |
| Local optional OpenClaw extension | PARTIAL — operator authorization boundary | With a temporary non-service gateway, config validation, doctor, gateway health, agents list, and memory status passed; the final agent call was refused because a scope upgrade awaits operator approval. No approval was granted or bypassed |
| Free/no-spend OpenRouter installed-app E2E | VERIFIED live on candidate `13ae647`; correction verified installed on `a0044f3` | Settings/API authentication, 342-model/17-free live catalog, free-first UI, HY3 configuration-only switch, catalog refresh counts, exact selected-model route, per-model usage, and removal/local-Auto fallback passed. One zero-price response used 177 tokens on `nvidia/nemotron-3-ultra-550b-a55b:free`; provider actual cost was $0.0000. The corrected `a0044f3` installed runtime separately proved exact-zero text reporting; no second key or paid call was used |

#### Exact-main 0.4.4 release evidence

| Release capability | KAS result | Evidence |
|---|---|---|
| Exact source provenance | VERIFIED | Clean release worktree and staged/installed manifests identify `e35b634a8a5b`; `source_dirty_state=false`; runtime reported `0.4.4-closed-test+windows+e35b634a8a5b` |
| Exact-main regression | VERIFIED | Ruff passed; full pytest: `5,447 passed, 76 skipped, 15 xfailed, 13 xpassed, 50 subtests`; a fresh four-worker CI-equivalent run passed all four shards |
| Wheel/package smoke | VERIFIED | `vool_hive_mind-0.4.4-py3-none-any.whl` built, installed into a clean temporary venv, and imported `core.app_version` as `0.4.4` |
| Final installer artifact | VERIFIED and released as `v0.4.4-closed-test` | `VOOL-Setup.exe`, 1,466,068,694 bytes, SHA-256 `3195B3B116FADAFBA762312C982C9FB9C71E071831BAFDF2C56EA1B1E1FFEBA1`; Authenticode status `NotSigned` |
| Isolated install and integrity | VERIFIED | Silent install exited 0; Add/Remove Programs reported `VOOL version 0.4.4`; required launcher, supervisor, embedded Python, Ollama, `llama-server.exe`, doctor, manifest, and provenance files were present; staged/installed hashes matched; embedded `pip check`, imports, and `compileall` passed |
| Installed API and chat | VERIFIED | `/healthz` and `/v1/models` served from the installed bundle; `/api/runtime/version` matched exact source; real `/api/chat` returned `12 * 9 = 108.` with zero model tokens through the deterministic arithmetic path |
| Installed supervisor recovery | VERIFIED | Terminating only the installed API child changed PID `6200` to `14744`, incremented the API restart count to 2, restored health, and preserved the exact build identity; `--stop` removed state and left no candidate processes; the pre-existing 0.4.3 test app was restored afterwards |
| Doctor output boundary | VERIFIED for bundle truth; overall degraded is expected | Doctor selected the manifest model/store and wrote its report outside the inspected install tree. Generic script-install artifacts absent from a self-contained bundle remain reported as degraded rather than fabricated as ready |
| OpenRouter release boundary | INCLUDED from merged candidate proof; not re-spent on final rebuild | The merged free/no-spend E2E code is present in `e35b634`. The temporary key had already been removed and was not reused for the exact release rebuild; no paid call or new cloud spend was made |

#### KAS context memory round-trip follow-up (PR #80, stacked on PR #71)

This subsection records the durable semantic-memory write/read proof added on the KAS branch
stacked on corrected PR #71 at `6de7a60`, whose base is current `main` `507e4fe`. It does not change Loop's
Windows 10 / GTX 1080 rows.
The final post-fix live run used the isolated WSL runtime and the visible browser: 52/52 cases
completed with 0 browser timeouts. Deterministic memory/control turns and model-backed turns are
reported separately; this is not a claim that every case invoked a model.

| Capability | KAS result | Evidence |
|---|---|---|
| Finalized-turn semantic write | VERIFIED in source regression | `append_conversation_event` is now the single finalized-turn seam for normal and fast-path turns; it carries the server-resolved chat policy and runtime home into semantic storage. |
| Runtime-home and agent-lane consistency | VERIFIED in source regression | Semantic reads and writes use the selected runtime home; scheduled fact extraction uses the shared `vool_chat` semantic lane, removing the prior `vool`/`vool_chat` split. |
| Current-chat scoped memory retrieval | VERIFIED in source regression | A marker written through the finalized-turn path is recalled in its originating chat and is absent from a new chat; scope filtering remains before ranking/distillation. |
| Exact identifier and code recall | VERIFIED in source regression | `HX-77412` and `BAY 13 EAST` survive storage and capsule distillation; source-bound validation rejects the lossy `BAY` response when the full code is expected. No canned answer was added. |
| Same-chat typed recall, stale-value response control, and natural forget handling | VERIFIED in source and exact installed bundle | Final A-C plus D replay completed all relevant cases. The exact `a857962` installed bundle then stored two facts independently, changed identifier `INSTALL-MEM-A857-7319` to `INSTALL-MEM-A857-9842`, returned only the corrected value, forgot that identifier through a descriptive request, preserved the sibling `0.091 SOL` budget note, denied the forgotten value, and denied the identifier again in a fresh chat. No stale value or cross-chat recall appeared. Cumulative source proof is 103 focused memory/scoping cases, full pytest at `9,356 passed, 109 skipped, 17 xfailed, 13 xpassed`, all four shards, and full Ruff green. |
| Concurrent provider-manifest signer initialization | VERIFIED in source stress regression | PR #71 makes signed provider manifests part of concurrent local inference. The unlocked first-use regression created 16 process identities and the four-worker run recorded malformed-seed thread exceptions. A process-local reentrant lock now serializes signer load/create/rotation; the first-use and real concurrent Ollama/provider paths passed 20 consecutive repetitions. |
| Canonical VOOL grounding in the installed layout | VERIFIED in source and bundle-contract regression; exact installed re-drive pending | The bundle builder now stages the five allowlisted canonical documents. Canonical project questions bypass the context-free `plain_task_minimal` route and keep provenance-bearing canonical passages in the normal context path. README identifies VOOL as Parad0x Labs' local-first personal agent; the model still composes the answer rather than receiving a canned response. |
| Constrained conversation versus tool routing | VERIFIED in source regression; exact installed re-drive pending | Explicit spelled exact-word constraints through twenty are parsed structurally. A constraint-only workspace-topic prompt remains conversational even with the optional catalog enabled, while a constrained explicit file action still reaches tools. Provider unavailability with no structured tool output returns to normal routing instead of coercing `None` to an empty tool call. |
| Installed bundle, live local-model, and blind-seed proof | VERIFIED on the exact stacked Windows candidate | The `a857962` installed bundle completed three fresh visible-UI blind prompts through local `qwen2.5:7b`. Each turn recorded active inference, one model call, one context/provider-manifest link, a final payload hash, provider token usage, and `fallback=false`; the exact-six response contained six model-generated words. The separate installed memory sequence also completed without stale or cross-chat recall. This verifies the candidate, not the currently shipped `e35b634` installer. |

The exact restacked branch passes 104 focused memory cases across the round-trip and scoped-command surface,
full pytest (`9,355 passed, 109 skipped, 17 xfailed, 13 xpassed`), all four isolated shards, and
full Ruff.
The earlier 52-turn WSL drive remains behavioral evidence for the same memory lifecycle. The final
stacked Windows handoff regenerated the installed proof from exact commit `a857962`; it does not
claim elimination of every possible leak or that the current shipped installer contains the branch.

#### KAS final stacked Windows installer handoff (PR #72, stacked on PR #80)

This Windows-only handoff is deliberately last in the stack so one exact candidate can validate
PR #71, PR #80, and the installer path together. It does not change Loop's Windows 10 / GTX 1080
rows or any Apple-owned file.

| Capability | KAS result | Evidence |
|---|---|---|
| Checkout-derived bundle provenance | VERIFIED in source and exact clean build | `build_bundle.ps1` rejected caller-supplied provenance and stamped clean Git commit `a8579625501734576629b73a1adad82511b29e1b` into both `build-source.json` and `bundle_manifest.json`; dirty state is false and the runtime build ID reports `0.4.4-closed-test+windows+a85796255017`. |
| Installer tooling compatibility | VERIFIED in focused and full source regression | The final installer/build-source/doctor/docs pack passes 89 tests. Exact full pytest is `9,359 passed, 109 skipped, 17 xfailed, 13 xpassed`; all four isolated shards, a fresh wheel build/install/import smoke, and full Ruff are green. The host-specific direct-termination assertion was replaced by the platform-neutral `_stop_child` contract while a separate Windows test pins `taskkill /T /F`. |
| Installed supervisor clean stop owns descendant processes | VERIFIED in exact installed bundle | Killing only the installed API child changed its PID, incremented the API restart counter from 1 to 2, and restored `/healthz`. The final `--stop` left zero listeners on 11434/11435 and zero processes owned by the installed tree, including the Ollama runner. |
| Installed doctor grades the shipped bundle layout | VERIFIED for bundle truth; profile warning retained | The installed doctor graded the bundle layout, launcher set, Ollama binary, and model-pull state healthy; wrote its report outside the inspected tree; and left the install-tree SHA-256 digest unchanged. Overall status remains honestly `degraded` only because the install-profile registry labels the required `qwen2.5:7b` lane unregistered, despite separate live inventory and inference proof. |
| Exact stacked installer and installed runtime | VERIFIED review candidate; not shipped | Exact clean commit `a857962` produced unsigned `VOOL-Setup.exe`, 1,468,373,650 bytes, SHA-256 `BAB6AFB9E4D4342CA4B2625A894EC968F5F9FDCB8C0EA7894A791C2F1455373E`. Silent install exited 0; 5,542 payload files had 0 missing, 0 hash mismatches, and 0 unexpected files; embedded `pip check`, imports, and compilation passed. API/Ollama health, three blind local-model UI turns with sealed context/provider proof, the full correction/forget/fresh-chat memory sequence, supervised API recovery, and clean stop passed. A post-merge rebuild from the resulting exact `main` SHA is still required before release. |

#### Deferred Tier 3 owner decisions

Tier 3 remains intentionally deferred: #32 (Windows sandbox backend), #33 (drive alias accounting),
#34 (Windows console encoding), #22 (remaining per-model installer/model-store envelope beyond this
RTX 4060 proof), and #8 (credential/key-at-rest owner decision). #10's original inversion is not
reproducible on this live Windows path; remote issue closure remains an owner action after review of
these receipts. Paid BYOK remains disabled; no cloud spend or Apple/Loop capability claim is added
by this section.

---

## How to read this

Three statuses, and they mean exactly what they say:

- **VERIFIED** — someone ran it on Windows and watched it work. The observed output is quoted inline where it is short. If there is no evidence in the row, it is not VERIFIED.
- **BROKEN** — it was run on Windows and it failed, returned a wrong answer, or returned a confident non-answer. Failures are stated in full, not softened.
- **UNVERIFIED** — it was **not exercised**. This is not a claim in either direction. It does not mean "probably works". Several UNVERIFIED rows exist because exercising them would have spent money, mutated the user's machine, or downloaded multi-GB models, and the audit was read-mostly by policy.

Two additional statuses appear where a single capability genuinely splits:

- **PARTIAL** — it ran, part of it worked, and the limit is documented in the row. Used sparingly and always with the boundary stated.
- **FIXED** — it was BROKEN at `1e4fd82` and the fix was **re-driven on 2026-07-20** at `b88e8a9`. The original failure text is kept in the row so a reader can see what moved. A row marked FIXED without a measurement is a bug in this document.

**Nothing has been silently dropped.** Every row that was BROKEN at `1e4fd82` is still present below, either still BROKEN (re-driven, still failing) or marked FIXED with the new measurement beside the old one.

Two further rules this document follows:

1. **Reachable ≠ working.** Several intents were confirmed to reach the executor and return a real gate/validation response rather than "unknown intent". That is recorded as reachability, not as a working capability.
2. **Where the five audit passes disagree, the disagreement is printed** (see [Conflicts between audit passes](#conflicts-between-audit-passes)). Nothing was reconciled by picking the more flattering reading.

---

## What moved since `1e4fd82`

Counted exactly, against the 34 numbered defects in [Known broken / open](#known-broken--open):

- **9 fully closed** — items 5, 6, 8, 9, 10, 12, 13, 14, 15.
- **1 partly closed** — item 16 (thumbs removed; the Projects sidebar is still browser-only, but now discloses it).
- **2 more closed that were never in the 34** — Windows credential-directory protection (N1) and two extra non-owner disclosures the AST sweep found (N2).
- **1 whose cause is fixed but whose symptom is not** — item 23 (version single-sourcing landed; the stale installed bundle needs a rebuild).
- Plus three dead UI controls that had rows but no number: `web0_enabled`, the disabled composer "+" attach button, and the paid-cloud overclaim strings.

Each line below names what it closes and how it was measured on 2026-07-20 at `b88e8a9`. The rest
are still open and were **re-driven**, not assumed.

| # | Was | Now | How it was measured on 2026-07-20 |
|---|---|---|---|
| 6 | Non-owner got the cloud key's last 4 chars | **FIXED** | Drove `maybe_handle_cloud_key_status_intent` with a dummy key ending `WXYZ` and `owner_local=False` → `"Cloud-key status is owner-local only…"`; no suffix, no store read. Two further disclosures found by an AST sweep and closed the same way: the OpenRouter onboarding intent and `cloud usage` |
| — | (sweep result) | — | Re-ran the sweep: 20 functions take `owner_local`; 3 never reference it, and all 3 are the public-catalog readers documented at `fast_command_surface.py:496` (unauthenticated openrouter.ai list, discloses nothing about this machine) |
| 8 | "Bypass permissions" bypassed nothing | **FIXED** | `grep -c bypass core/vool_chat_page.py` → `0`. The control is gone, not relabelled |
| 15 | Effort Faster/Balanced and tiers Fast/Daily accepted and dropped | **FIXED** | The selectors are gone from the page (`local-fast`/`local-daily`/`local-heavy`/`smarter` → 0 hits). `service.py:1588` now accepts `effort` **only** for `smarter`, which sets the one hook that reaches real machinery, and says so in a comment |
| 16 | Thumbs and Projects were `localStorage` with no disclosure | **PARTIAL → honest** | Thumbs removed (`rateMsg`, `vool_ratings` → 0 hits). Projects are **still browser-only** but now say so in three places, e.g. the `+ Project` tooltip: `"projects are saved in this browser only, never on the server"` |
| 9 | Negative price clamped to free | **FIXED** | `{'prompt':'-1','completion':'0'}` → `known=False free=False`. `_price` returns `None` below zero rather than clamping |
| 10 | Free lane 100% dead against the live catalog | **FIXED** | Synthetic payloads through `parse_openrouter_catalog`: a zero-priced model with **no** `:free` in its name → `free=True`; a model **named** `…:free` but priced → `free=False`. No `pricing.request` key is required. The verdict is the price, not the name |
| 5 | `cloud models` claimed a network failure that did not happen | **FIXED** | `_free_models_text` now branches on `age is None` (no usable catalog) instead of `if not free`, so "reached it, nothing is free" and "could not reach it" are different sentences |
| 12 | `recommend_openrouter_model()` returned `None` for every input | **FIXED** | Against a synthetic 3-model catalog it returns `c/free`. It was dead only because every live model read as unpriced (#10); with pricing known, the cost filter has candidates again |
| 13 | The paid cloud lane could not execute at all | **FIXED** | `core/paid_call_reservation.py` builds the reservation server-side. 99 tests green across `test_owner_pick_paid_reservation`, `test_paid_call_settlement_ledger`, `test_spend_cap_explainer`, `test_paid_gate_owner_local_hardening`, `test_spend_authorization` |
| — | Caps did not bind the paid lane | **FIXED** | Independent 25-way concurrent attack written for this pass (no key, no network, ledger only): 25 simultaneous owner-local picks of `claude-sonnet-4-5` at 12k in / 900 out → **20 granted, 5 refused**, settled cost **$0.99**, and a true worst case of **$5.00** — the cap exactly, since 20 reservations of $0.25 is what the ceiling admits. Reservations are sized per call from published rates, so concurrent turns cannot all hide under one flat ceiling |
| — | Windows `move_path` could move `~/.ssh` | **FIXED** | On `win32`: `_protected_roots()` → 16 roots; driving `machine.move_path` at `~/.ssh`, `~/.aws` and `kernel32.dll` each returned `blocked_protected_path` and **all three still exist**. The guard had been gated to non-Windows — the one platform the installer ships for |
| — | Build mode broken (#36) | **FIXED** | The CSRF hardening had stripped the builder's own `_trusted_local_only` flag. `tests/test_runtime_continuity.py` green |
| — | Installer version hardcoded `0.4.0` in four disagreeing places | **FIXED** | `core/app_version.py` is the single source (`VOOL_VERSION = "0.4.3"`); `vool.iss` takes `/DAppVersion` and falls back to `0.0.0-unstamped` rather than to a stale number. `tests/test_app_version.py` pins it against `pyproject.toml` |
| — | `asyncio.get_event_loop()` passed locally, failed on a fresh CI shard | **FIXED** | Zero occurrences remain under `core/` or `apps/` |
| — | Multi-provider BYOK | **NEW** | 8 credential slots enumerated from `core.cloud_providers.PROVIDERS`: anthropic, custom, deepseek, google, groq, moonshot, openai, openrouter |
| — | Spend-cap honesty | **NEW** | `docs/SPEND_CAPS.md` plus an owner-gated in-chat explainer (`maybe_handle_spend_cap_explainer_intent`). The doc states plainly that when a provider returns no usage figures the dollar cap **does not bound spend at all** and only the call count does |

**Still open after re-driving on 2026-07-20:** items 1–4, 7, 11, 14, 17–34. The ones re-run today
are marked in their rows below with the observed output. Two caveats on scope:

- The **model-dependent** rows (word-phrased arithmetic, the two fast-path hijacks, the crash) were
  **not** re-driven — that needs a live model turn, which this pass did not run. Their mechanisms
  were re-read and are unchanged; that is weaker evidence than a re-run, and is labelled as such.
- The **installed-bundle** rows (22–30) were not re-driven either: this pass ran in a worktree with
  no installed bundle. They are carried forward from `1e4fd82` unchanged, not re-confirmed.

---

## Before you run anything

Every command below assumes the repo root on the import path. Without it each row fails with
`ModuleNotFoundError: core`, which looks like a missing capability and is not one.

```bash
cd <repo root>
export PYTHONPATH="$PWD"          # PowerShell: $env:PYTHONPATH = $PWD
python -m pytest -q tests/test_spend_ledger_concurrency.py
```

The flagship row below -- "the 25-way concurrent attack" -- is not prose to reconstruct. It ships
as `tests/test_spend_ledger_concurrency.py`, which drives the real `reserve_owner_pick_paid_call`
against a registered BYOK manifest built from a dummy key, with no transport and no network. Run
that file rather than rebuilding the attack from this table.

Module paths for the functions named in the rows, since the table gives bare names:

| Named in a row | Lives in |
|---|---|
| `maybe_handle_cloud_key_status_intent`, `maybe_handle_openrouter_intent`, `maybe_handle_cloud_usage_command` | `core/agent_runtime/fast_command_surface.py` |
| `parse_openrouter_catalog`, `model_is_free` | `core/openrouter_catalog.py` |
| `_protected_roots` | `core/machine_file_ops.py` |
| `reserve_owner_pick_paid_call`, `_reservation_ceiling_usd` | `core/paid_call_reservation.py` |
| `reserve_spend`, `settle_spend` | `core/model_spend_ledger.py` |

Driving the three owner-gated handlers needs the credential store stubbed, or they answer from
whatever this machine actually holds:

```python
import core.credential_store as cs
cs.has_credential = lambda name: True
cs.get_credential = lambda name: "sk-or-v1-DUMMY-NOT-A-REAL-KEY"
```

Two rows carry no signal on the wrong platform. `tests/test_installer_doctor.py` fails 8 of 10 on
Windows for launch-agent reasons and is expected to pass on macOS -- a Windows failure there says
nothing about the Apple lane. Where a row's pass condition is a test count, read it as "green",
not as the exact number: the suite grows.

## Parity checklist for the Apple/Linux lane

This document speaks **only** for Windows. It makes **no claim** about macOS or Linux behaviour, in
either direction. This section exists so the Apple lane can check its own side without re-deriving
what to test.

**Read the split first.** Three categories, and they need different work:

### A. Shared Python — already yours, no porting needed

These changed in `core/` or `apps/` and contain no platform branch. The macOS `.app` and the Linux
lane get them from a pull. What is **not** free is verification: the code is shared, the *evidence*
is not, and the Windows measurement does not transfer.

| Capability | What to run on macOS/Linux | Pass condition |
|---|---|---|
| Non-owner disclosure gating (3 handlers) | Call `maybe_handle_cloud_key_status_intent`, `maybe_handle_openrouter_intent`, `maybe_handle_cloud_usage_command` with a **dummy** stored key and `owner_local=False` | All three return the owner-local refusal. No key suffix, no store read |
| The AST sweep itself | Re-run a sweep for functions taking `owner_local` that never reference it | Only the 3 public-catalog readers (`fast_command_surface.py:496`). Any other hit is a new disclosure |
| Free/paid verdict from price, not name | `parse_openrouter_catalog` on synthetic payloads: zero-priced **without** `:free`, and priced **with** `:free` | First → `free=True`; second → `free=False`. No `pricing.request` key required. Unpriced/null/malformed/negative → `known=False free=False` |
| Multi-provider BYOK (8 slots) | Enumerate `core.cloud_providers.PROVIDERS` | anthropic, custom, deepseek, google, groq, moonshot, openai, openrouter — each with its own base URL and credential slot |
| Paid reservation + binding caps | The 25-way concurrent attack: 25 simultaneous owner-local picks of a paid model against a `$5.00` daily cap, **dummy key, stubbed transport** | Worst-case granted × per-call cost stays inside the cap. On Windows: 20 granted / 5 refused, $0.99 settled, $5.00 worst case. Expect the same shape — the ledger is shared |
| Settlement priced from tokens | `pytest tests/test_paid_call_settlement_ledger.py` | Green. A non-OpenRouter provider (which returns no `usage.cost`) must still move the USD ledger |
| Reservation/gate suites | `pytest tests/test_owner_pick_paid_reservation.py tests/test_paid_call_settlement_ledger.py tests/test_spend_cap_explainer.py tests/test_paid_gate_owner_local_hardening.py tests/test_spend_authorization.py` | 99 passed on Windows |
| Build mode (#36) | `pytest tests/test_runtime_continuity.py` | Green. The CSRF hardening had stripped the builder's own `_trusted_local_only` flag |
| Version single-sourcing | `pytest tests/test_app_version.py` | Green — `core/app_version.py` (`0.4.3`) is the only source, pinned against `pyproject.toml`. **But the macOS side is NOT fixed — measured, not guessed:** `installer/bundle/build_macos_app.sh:166` hardcodes `<key>CFBundleShortVersionString</key><string>1.0</string>`. The Windows lane fixed `vool.iss` (now `/DAppVersion`, falling back to `0.0.0-unstamped`); the macOS `.app` still reports `1.0` in Finder and About regardless of the code it ships. **This is an Apple-lane action item, not a Windows one — the Windows lane did not touch it.** |
| `asyncio.get_event_loop()` | Grep `core/` and `apps/` | Zero occurrences. This one failed only on a fresh CI shard, so a local green run does not clear it |
| UI controls (removed / made honest) | Serve `/chat`, grep the HTML for `bypass`, `local-fast`, `local-daily`, `local-heavy`, `smarter`, `rateMsg`, `vool_ratings`, `WEB0_ENABLED` | All zero. `core/vool_chat_page.py` is shared Python emitting shared HTML/JS — no separate macOS UI to fix |
| Still-broken shared defects | `workspace.search_text` for a string in a deep file; `machine.list_directory {directory:…}`; `sell.quote`; `hive.list_available`; `web.browser_render` | **Expect the same failures.** These are shared Python with no platform branch. If macOS *passes* any of them, that difference is itself the finding |

### B. Platform-split — results do NOT transfer, re-verify independently

Same feature, genuinely different mechanism per OS. A Windows PASS here is not evidence for macOS.

| Capability | Why it splits | What the Apple lane must do |
|---|---|---|
| `sandbox.run_command` | Hard-blocked on Windows for lack of a kernel network-isolation backend. The refusal text names `sandbox-exec` as the macOS backend | **Expect it to WORK on macOS**, and Linux via `bwrap`/`unshare`/`firejail`. Drive it. A Windows-shaped "blocked" result on macOS would be a regression |
| Protected-path denylist | Windows matching relies on `os.path.normcase` for NTFS case-insensitivity — which is exactly why Linux needed a separate case-sensitivity fix | Re-verify your own denylist by **observing refusals**, not by reading the guard |
| Credential-directory guard in `machine_file_ops` | The Windows gap is fixed (16 roots, refusals observed). The **root set differs per platform** — `%APPDATA%\Microsoft\Credentials` has no macOS analogue; `~/Library/Keychains` has no Windows one | Enumerate `_protected_roots()` on macOS and confirm the Keychain, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/solana` are covered. Then drive `move_path` at each and confirm nothing moved |
| `credential_store` file permissions | `_save_raw` only `chmod`s on **POSIX**. On Windows the file inherits the user-profile ACL | macOS/Linux is the platform where the `chmod` actually runs — verify the mode is what you intend. This is the one at-rest control Windows does not have |
| Node signing key unencrypted at rest | Shared defect, but the mitigation differs: Windows has no keyring backend by default | Check whether the macOS Keychain backend is picked up automatically. If it is, macOS is **better** here and the row should say so |
| `machine.event_log_errors` | Reads the Windows Event Log | No meaningful macOS equivalent. Expect this intent to be unavailable or to need a `log show` equivalent — do not treat its absence as a regression |

### C. Windows-only by nature — not your problem, do not port

No macOS/Linux counterpart. Listed so the Apple lane can skip them deliberately rather than wonder.

- **The Inno Setup installer** (`installer/bundle/vool.iss`) and everything it packages. The macOS lane has `build_macos_app.sh`. Only the *structural* property is worth comparing: user data lives outside the install root, so uninstall does not remove it.
- **The `.cmd` / `.vbs` / `.ps1` launch chain** (`vool-launch.cmd`, `vool.vbs`, `vool-open.ps1`) — including defect 22, the synchronous model pull that blocks startup. That is a launcher bug in a file macOS does not have.
- **Console-window suppression** (`core/windows_quiet_subprocess.py`, which ORs `CREATE_NO_WINDOW` into `Popen`). No macOS analogue — nothing to suppress.
- **WebView2 / pywebview / pythonnet window hosting** and the multi-hive registry probe.
- **Drive-letter tooling** — the `subst` alias double-count in `machine.disk_usage` (defect 33) is a drive-letter concept. macOS/Linux mount points can still double-count via bind mounts; worth a separate look, but it is not this bug.
- **`installer/doctor.py`** grades the Windows script-install layout (defect 29).
- **`Start_VOOL.bat`'s stale-process kill pattern** (defect 30).

### D. The honest gaps in this document

State these plainly rather than letting the Apple lane assume coverage:

- **Model-dependent rows were not re-driven** on 2026-07-20 (word-phrased arithmetic, the two fast-path hijacks, the crash). Mechanisms re-read only.
- **Installed-bundle rows 22–30 were not re-driven** — this pass had no installed bundle.
- **No paid call has ever been made** in this lane, on any commit. Every cap measurement uses a dummy key and a stubbed transport. "The caps bind" means the *reservation ledger* refuses; it does not mean anyone has watched a real provider bill arrive under a cap.
- **The model-to-tool-call link is still untested.** All tool verification used hand-built payloads. Whether the local model reliably emits these intents with correct argument names from natural language is unmeasured — and given the silent-ignore-unknown-args defect, that is the largest untested link in the tool surface, on either platform.

---

## Out of scope: macOS / Linux — original notes

Retained from the `1e4fd82` audit; the checklist above supersedes them where they overlap.

Specific notes for the Apple lane comparing surfaces:

- **Expect asymmetry both ways, not a Windows deficit.** `sandbox.run_command` is hard-blocked on Windows for lack of a kernel network-isolation backend; its own refusal text names `sandbox-exec` as the macOS backend, so the Apple lane should expect that intent to **work** there. Conversely `machine.event_log_errors` reads the Windows Event Log and has no meaningful macOS equivalent.
- **Path-guard results do not transfer.** The Windows protected-path check relies on `os.path.normcase` for case-insensitive matching on NTFS. That is correct here and is precisely why the Linux lane needed a separate case-sensitivity fix. The Apple lane must re-verify its own denylist rather than inherit this row.
- **`credential_store` file permissions are POSIX-only.** `_save_raw` only `chmod`s on POSIX; on Windows the encrypted store file inherits the user-profile ACL. The Apple lane's at-rest posture is therefore different and must be checked separately.
- **The install/app-shell section is Inno Setup / WebView2 / `.cmd` / `.vbs` specific.** None of it maps to a macOS bundle. Only the *structural* claim — user data lives outside the install root, so uninstall does not remove it — is worth comparing as a design property.
- **The headline model defect (qwen3 reasoning leak) could not be tested here at all** because `qwen3:4b` is not installed on this box. Do not treat that fix as Windows-verified.

---

## Tooling

Inventory measured by running, not reading: `runtime_tool_specs()` → **60 intents**; `runtime_capability_ledger()` → **35 capability entries**. `tools/registry.py` registers only 5 builtins; the remaining ~55 intents come from `core/runtime_execution_tools.py` and `core/local_operator_actions.py`.

### Machine / host inspection

| Capability | Status | Observed |
|---|---|---|
| `machine.inspect_specs` | VERIFIED | `OS: Windows 10.0.19045 / CPU cores: 8 / RAM: 7.9 GiB / Accelerator: cuda / GPU: NVIDIA GeForce GTX 1080 / VRAM: 8.0 GiB / Recommended local model: qwen2.5:7b` — matches the real box |
| `machine.disk_usage` | VERIFIED | Real per-drive table across 4 drives, `Total free: 801.7 GB`. See the double-count wart below |
| `machine.display_inspect` | VERIFIED | `1920 x 1080 @ 74 Hz. Physical size: about 21.7" diagonal (from the monitor's EDID)` — genuine EDID read |
| `machine.list_processes` | VERIFIED | Live process table with pids and RSS (`MsMpEng.exe (pid 3316) 762.4 MB`, …) |
| `machine.list_directory` | VERIFIED | Real `~/Downloads` contents with `limit` honoured. **See silent-wrong-answer defect below** |
| `machine.find_folder` | VERIFIED | 25 real hits across C:/D:/F:/G: to depth 6, with an honest footer naming the searched roots and the depth |
| `machine.find_largest` | VERIFIED | Real measured sizes (`WSL — 26.34 GB [folder]`) plus `I won't delete anything without your explicit go-ahead on a specific path.` |
| `machine.event_log_errors` | VERIFIED | Real Windows Event Log Critical/Error entries with source, event ID, timestamp (`VBoxNetLwf` ID 12 at `2026-07-19T18:25:08Z`; DCOM 10010). Windows-only capability |
| `machine.read_file` | VERIFIED | Numbered lines 1–5 of a real `~/Desktop` file, `max_lines` honoured |
| `machine.write_file`, `machine.ensure_directory`, `machine.move_path` (valid non-protected path) | UNVERIFIED | Reachable — reached the executor and returned real gate/validation responses, not "unknown intent". Mutating, so not driven |

**Drive double-count (Windows-specific wart):** this box has an aliased drive pair (`subst`). Both members are reported with identical size and free space, which is correct per-drive, but `Total free: 801.7 GB across 4 drive(s)` counts the alias twice. Mapped/`subst` drives are common on Windows, so this is worth fixing in this lane specifically.

### Workspace

| Capability | Status | Observed |
|---|---|---|
| `workspace.list_tree` / `workspace.list_files` | VERIFIED | Real file listings from the repo root |
| `workspace.read_file` | VERIFIED | Correct numbered output, `max_lines` honoured |
| `workspace.git_status` | VERIFIED | `## main...origin/main` (clean) |
| `workspace.git_summary` | VERIFIED | `branch main @ 1e4fd82d09d7; 100 visible branches (50 local, 50 remote tracking); 14 commits on 2026-07-19; dirty no` |
| `workspace.run_lint` | VERIFIED — really executes a subprocess | `[SANDBOX RUN] Executing: python -m ruff check . -> Exit code: 0 -> All checks passed!` |
| `workspace.run_tests` | VERIFIED — really executes a subprocess | `python -m pytest tests/test_docs_entrypoints.py -q` → `Exit code: 0 / 10 passed in 0.85s` |
| **`workspace.search_text`** | **BROKEN — confident false negative. Re-driven 2026-07-20, unchanged** | `{query:'def load_builtin_tools'}` → `ok=True status=no_results "No text matches for \"def load_builtin_tools\" were found in the workspace."` The string is at `tools/registry.py:41`. Scoped to `path:'tools'` → `status=executed`, returns the correct hit. See below |
| **`workspace.symbol_search`** | **BROKEN — same root cause. Re-driven 2026-07-20, unchanged** | Same walk, same budget exhaustion. See below |
| `workspace.write_file`, `ensure_directory`, `replace_in_file`, `apply_unified_diff`, `rollback_last_change`, `run_formatter` (apply mode) | UNVERIFIED | Reachable; mutating, not driven |

**`workspace.search_text` / `symbol_search` — measured failure.**
`{query:'def load_builtin_tools'}` → `ok=True status=no_results "No text matches ... were found in the workspace."` The string is at `tools/registry.py:41` (confirmed by grep). Cause, measured not inferred, in `core/runtime_execution_tools.py:2804` and `:2122-2143`:

```
_iter_workspace_files(root, glob_pattern='**/*', limit=500)  -> 500 of 7426 repo files
  .pyc inside that budget: 390     actually searchable text files: 105  (1.4% of the repo)
  last file reached: core/agent_runtime/__pycache__/hive_topic_create_preflight.cpython-312.pyc
  tools/registry.py present in scanned set: False
```

The walk only skips dot-prefixed parts, so `__pycache__` is not excluded and `.pyc` files consume the budget ahead of the source they shadow. `glob:'*.py'` does **not** rescue it — there are 1378 non-dot `.py` files and `tools/registry.py` sorts at index 1369/1378, past the 500 cap. Only explicit `path` scoping works: `{query:'def load_builtin_tools', path:'tools'}` → `status=executed, "tools/registry.py:41 def load_builtin_tools() -> None:"`. So workspace code search is usable only when the caller already knows the subdirectory. The failure mode is the damaging kind: `ok=True`, so an agent reports "not found" as a successful search.
*Not enumerated:* whether other callers of `_iter_workspace_files` are degraded the same way. Confirmed for these two only.

### Web

| Capability | Status | Observed |
|---|---|---|
| `web.search` | VERIFIED — live network | Real result links/snippets (barrons.com, coinmarketcap.com, coindesk.com) with URLs |
| `web.fetch` | VERIFIED | `Status: ok / Preview: Example Domain This domain is for use in documentation examples...` |
| `web.research` | PARTIAL | Runs and returns live hits (found `github.com/x402-foundation/x402`), but 2 of 3 hits were unrelated MDN pages while it declared `Stop reason: strong_evidence`. The confidence label is not earned by the hit set |
| `web.browser_render` | BROKEN / disabled | `ok=False status=disabled_by_policy`. `browser.render` is registered in `tools/registry.py` but unreachable in this configuration |
| `browser.render` engine path (Playwright or equivalent) | UNVERIFIED | The runtime intent is policy-disabled, so no browser engine was ever invoked. Whether an engine is even installed on this box is unknown |

**Duplicate:** `web.fetch` is registered **twice** in `runtime_tool_specs()` — once via the web-tool lane, once via `runtime_execution_tools`. Duplicate intent name in the surface presented to the model.

### Execution

| Capability | Status | Observed |
|---|---|---|
| Fixed validation lane (`run_tests`, `run_lint`) | VERIFIED | Spawns real subprocesses on Windows (ruff exit 0, pytest 10 passed) |
| **`sandbox.run_command` (arbitrary bounded shell)** | **BROKEN on Windows — categorical** | `ok=False status=blocked_by_policy: "Sandbox execution blocked: OS-level network isolation is required but unavailable (expected one of: bwrap, unshare, firejail on Linux; sandbox-exec on macOS). Windows has no kernel network-isolation backend."` Offers WSL2, or `network_isolation_mode=heuristic_only` as an explicit informed override (not set) |

State this precisely: **"VOOL can execute code on Windows" is true for the fixed test/lint lane and false for the arbitrary-command lane.**

### Operator, marketplace, hive, other

| Capability | Status | Observed |
|---|---|---|
| `operator.list_tools` | VERIFIED, and its self-report is accurate | Honest 3-tier inventory: 5 supported; `operator.schedule_calendar_event` marked partially supported; `operator.discord_post` / `operator.telegram_send` marked configured-but-unavailable |
| `operator.inspect_services` | VERIFIED | Real Windows service table |
| `operator.inspect_disk_usage` | VERIFIED | Real C:\ scan plus a cleanup **preview** (`128.1 MB across 1 bounded temp root(s)`) and a pending-action id. Preview only, nothing deleted |
| `operator.schedule_calendar_event` | PARTIAL / arg-shape mismatch | Writes a local `.ics` to an outbox — not a live calendar integration (the tool says so itself). Probe with documented-looking args → `status=invalid_request`; the documented arg shape and accepted arg shape do not match |
| `operator.discord_post`, `operator.telegram_send` | BROKEN here (not configured) | Self-reports "bridge sending is not configured on this runtime" |
| `marketplace.search_listings` | VERIFIED reachable | `No marketplace listings matched.` — functional, empty catalog |
| `marketplace.purchase_knowledge` | UNVERIFIED | Reachable (returned a `needs a shard_id` validation); burns credits, not driven |
| `demo.plan` | VERIFIED on a public repo | Produced a real 30 s shot-by-shot plan with voiceover derived from the actual README of `github.com/psf/requests` |
| `sell.quote` | BROKEN as a product surface — stub | Returns a quote payable to a hardcoded placeholder: `0.000500 USDC to stub-wallet`. `stub-wallet` is a literal default in `core/null_protocol.py:110`, `core/web0_work_receipt.py:63`, `core/web/api/service.py:160,1295`. The quote works; the sell-your-compute capability is not settleable as shipped |
| `hive.list_available` | BROKEN | `ok=False status=unreachable: "I couldn't reach the Hive watcher right now."` The ledger advertises `hive.read` as `supported:true`. **Ledger claim and runtime behaviour disagree** |
| `hive.list_research_queue`, `export_research_packet`, `search_artifacts` | UNVERIFIED | Ledger claims read support, but the one read intent driven was unreachable, so the rest cannot be vouched for |
| `hive.*` write family | UNVERIFIED | Ledger reports `supported:false`; `/api/runtime/capabilities` shows `public_hive_enabled:false`. Not driven (mutating, shared surface) |
| `web0.*` builder chain | UNVERIFIED | `web0.create_project` reached the executor and returned `status=rejected template_id_required`, proving it is wired. Chain is mutating; downstream steps unknown |
| `mcp.*` bridge | UNVERIFIED | `is_mcp_intent('mcp.foo.bar')` → `True`, so the routing branch is live, but no MCP server is configured on this box; nothing executed end-to-end |
| `set.use` / `set.save` | VERIFIED (read) / UNVERIFIED (write) | `set.use` honest: `No set named VOOL_HOST. Saved sets: (none yet).` `set.save` writes state, not driven |
| Unknown intent handling | VERIFIED | `bogus.intent` → `ok=False status=unsupported: "No. \`bogus.intent\` is not wired on this runtime."` No hallucinated success |

### Argument-shape friction (raises the cost of every row above)

Several tools failed on first call purely because emitted arg names differ from what a caller would guess: `machine.list_directory` wants `path` (not `directory`); `machine.read_file` / `workspace.read_file` want `max_lines`/`start_line` (not `limit`); `machine.find_largest` wants `top` (not `limit`).

This combines badly with **silent-ignore-unknown-args**: `machine.list_directory {directory:'Downloads'}` returned `ok=True` with the contents of `~/Desktop`, correctly labelled `Visible entries under ~/Desktop`. The label is honest, but a model that emits the wrong key gets a plausible, successful-looking answer about the wrong directory. Same behaviour on `machine.find_largest` (`{limit:5}` ignored, returned 8 rows). Model arg drift becomes wrong answers rather than errors.

---

## Chat & model runtime

| Capability | Status | Observed |
|---|---|---|
| Model resolution (Ollama up) | VERIFIED | `default_runtime_model_tag()` → `qwen2.5:7b`; `installed_ollama_model_names()` → `('qwen2.5:7b',)`; `/healthz` `model_pull` `ready`/`already installed` |
| Hardware probe | VERIFIED | `cpu_cores=8, ram_gb=7.95, gpu=NVIDIA GeForce GTX 1080, vram_gb=8.0, accelerator=cuda, accelerator_status=usable, driver=582.66, vram_free_gb=3.10` |
| Factual Q&A | VERIFIED | Canberra (23.4 s), Tokyo (51.1 s), Rayleigh scattering (3.2 s), three primes over 100 → 101/103/107 (2.6 s) |
| Symbolic arithmetic fast path (`ast` eval, not model text) | VERIFIED | `4821 * 37 = 178377.` (0.1 s); `100*50 = 5000.` (0.2 s); `12 + 30 = 42.` (0.1 s); `12345/7 = 1763.57142857` |
| Disk/machine question fast path | VERIFIED | Real per-drive table in 0.2 s, values match the disks |
| Pronoun follow-up (anaphora) | VERIFIED | After the Canberra turn, "How big is it?" → `Canberra covers an area of approximately 814 square kilometres` (5.4 s) |
| Ordinal follow-up | VERIFIED | After "three largest planets", "Tell me about the second one." → correct Saturn answer (38.2 s) |
| No reasoning leak on the shipped model | VERIFIED | All 13 collected turns scanned for `Okay, the user is asking`, `Hmm,`, `Wait,`, `Let me think`, `<think>`, `First, I need to` → **LEAK_MARKERS: NONE** |
| The `think`-flag handling is load-bearing | VERIFIED at raw Ollama | `qwen2.5:7b` + `think=True` → `HTTP 400 {"error":"\"qwen2.5:7b\" does not support thinking"}`; key omitted → OK, `thinking_len=0`. `_thinking_runtime_config('qwen3:4b')` → `{'think': True}`; `('qwen2.5:7b')` → `{}` |
| Context compaction / retention | VERIFIED by benchmark and fresh artifact probe | `memory_compression_bench --turns 30 --repeats 3`: `summarizer_ran=true`, model `qwen2.5:7b`, `compression_fired=true`, retained facts **5/5** across all 15 samples (no spread), peak tokens min 270 / median 278 / max 283. A fresh extracted `closed-test-babd8e8` install, isolated to five temporary `VOOL_HOME` roots, also compressed and retained **5/5** facts in every sample using `qwen2.5:7b`. Reference: raw transcript 521 tok / 5 facts; naive last-10 window 120 tok / **1** fact |
| **Word-phrased arithmetic** | **BROKEN — 3/3 reproducible** | "What is 4821 multiplied by 37?" never returns 178377. Returns `I finished the work, but I'm stripping internal orchestration details from the reply.` at 66.1 s, 58.4 s, 37.3 s. Same sum as `4821 * 37` answers in 0.1 s |
| **Fast-path hijack: creative/video director** | **BROKEN — intermittent, ~1 in 3** | "What is the capital of Australia?" returned a full video shot plan (`*Shot Size:* Wide Establishing Shot, *Angle:* Overhead ... *Lens:* 24mm`). The word "Canberra" never appears. Same prompt answered correctly on 2 of 3 attempts |
| **Fast-path hijack: live-info/web-research** | **BROKEN — seen once** | A train word problem returned the user's own prompt echoed back plus scraped MDN/Google-cookie boilerplate. The correct answer (150 miles) never given |
| **Default model tag when Ollama is down** | **BROKEN** | Ollama down → `default_runtime_model_tag()` = `qwen3:4b`; Ollama up → `qwen2.5:7b`. Isolated by stubbing the inventory probe to empty. `core/runtime_provider_defaults.py:125` → `_installed_alternative_to()` returns `''` on empty inventory (line 96), so the bundle pick `qwen3:4b` survives. On a cold box the runtime points at a multi-GB model that is not installed — and one the surrounding code documents as unusable for chat |
| **Inventory probe ignores `OLLAMA_HOST`** | **PARTIAL** (was BROKEN) | Was: the probe hardcoded `http://127.0.0.1:11434` and no caller overrode it. Re-checked 2026-07-20: `installed_ollama_model_names` now takes a `base_url` parameter, and **one** caller passes the env-derived value — `core/web/api/runtime.py:313` uses `ollama_base_url(env)`, which reads `VOOL_RAW_OLLAMA_API_URL` or `OLLAMA_HOST`. The function still does **not** read `OLLAMA_HOST` itself, and the other six callers — including `runtime_provider_defaults`, which picks the model tag — still take the 127.0.0.1 default. So `/healthz` honours a remote Ollama and the model-tag pick does not |
| **Stability under sequential single-user chat** | **BROKEN — observed once** | Mid-run the API server dropped the connection (`WinError 10054`), then `WinError 10061`. `netstat` showed nothing on 11435 and no ollama process — **both** exited. No traceback; `api_err.log` ends cleanly at `VOOL API server ready.` Free RAM at that moment 1493 MB of 8141 MB. No watchdog respawn; both restarted by hand |
| **qwen3 reasoning-leak path itself** | **UNVERIFIED — cannot be tested here** | `qwen3:4b` is not installed (live 404: `model 'qwen3:4b' not found`). Both checkable halves were verified (config emits `think:True` for qwen3; qwen2.5 genuinely 400s with `think`), but the claim "think:true keeps the qwen3 monologue out of content" is **unproven on Windows**. Pulling the model would mutate the box |
| Streaming path | UNVERIFIED in the runtime pass | Every call there used `stream:false`. (The UI pass did drive streaming — see UI & API) |
| Crash root cause | UNVERIFIED | Memory pressure is the obvious suspect (1493 MB free RAM, 3.10 GB free VRAM against a 4.7 GB model) but nothing was logged, no OOM event captured, no re-run. Calling it OOM would be a guess |
| Whether the two hijacks are deterministic | UNVERIFIED | 1-of-3 and 1-of-1 respectively; not enough repeats to establish a trigger rate |

**Latency, measured wall-clock, `/api/chat`:** fast paths 0.1–0.2 s. Model turns: 2.6, 3.2, 5.2, 5.4, 19.1, 23.4, 36.6, 37.3, 38.2, 51.1, 58.4, 66.1, 75.8 s. Harness summary over one 5-prompt battery: `n=5 min=2.6s median=19.1s max=58.4s`. Contributing factor: `/healthz` reports `context_window 4096` and `vram_budget_gb 6.75` while only 3.10 GB VRAM was free of 8.0, so the 4.7 GB model partially offloads to CPU on this box.

**Installed-model reality check:** the audit brief listed six models. `ollama list` shows exactly one: `qwen2.5:7b 845dbda0ea48 4.7 GB`. `nomic-embed-text` is absent, so the memory benchmark above ran on the fallback embedding backend `hash-bow:384d`, not real embeddings.

**Word-phrased arithmetic — mechanism.** Two compounding defects: (a) the calculator fast path matches symbolic operators (`*`, `x`, `+`) but not the words "multiplied by", so the query falls through to the model; (b) the model's reply is then destroyed by the internal-orchestration sanitizer. The replacement string is the catch-all `return` at the end of the sanitizer's if-chain in `core/agent_runtime/response.py:318` — any model text that trips an internal-orchestration pattern loses the **whole** answer rather than the offending fragment.

---

## Install & app shell

Two different things are called "the install" in the source reports, and they behave differently. This section separates them:

- **Repo HEAD run from source** (`python -m apps.vool_api_server`) — this is what most of this document measured.
- **The bundle actually installed on this box** (`%LOCALAPPDATA%\Programs\VOOL`) — a **stale 0.4.0 build** that does not contain the startup fix.

| Capability | Status | Observed |
|---|---|---|
| Installer shape | VERIFIED by inspection against the real install on disk | Inno Setup, `AppVersion 0.4.0`, `PrivilegesRequired=lowest`, `DefaultDirName={autopf}\VOOL` → `%LOCALAPPDATA%\Programs\VOOL`. Present: embedded CPython 3.12, `app\` (4691+ files), `ollama\ollama.exe`, `models\` (empty), `VOOL.cmd`, `vool.vbs`, `vool-open.ps1`, `vool_window.py`, `unins000.exe`. Bundled: pywebview 6.2.1, pythonnet 3.1.0, clr_loader 0.3.1, uvicorn 0.51.0, starlette 1.3.1 |
| Data preserved across upgrade/uninstall | VERIFIED **structurally only** | Program files in `%LOCALAPPDATA%\Programs\VOOL`; user data in `%LOCALAPPDATA%\VOOL` — disjoint. `vool.iss` has **no** `[UninstallDelete]` section, so uninstall removes only `{app}`. Data dir confirmed to hold `vool_web0_v2.db` (2,138,112 B), `keys\`, `memory\`, `honesty_receipts\`, `owner_identity.json`. **The uninstaller was not run** |
| Port binds before the model pull (repo HEAD) | VERIFIED by running it | Cold start on 11436 with Ollama live: `BOUND=True ELAPSED_SECONDS=8.17`. Log ordering: `Provider prewarm deferred; binding the API port first and warming in the background.` then `VOOL API server ready.` |
| Port binds even with Ollama down (repo HEAD) | VERIFIED | Ollama killed → `NO_OLLAMA_BOUND=True ELAPSED=17.83s`, and `/healthz` honestly reported `model_pull {status:"failed", detail:"<urlopen error [WinError 10061] ...>"}`. The port bound anyway |
| `start_ollama_model_pull()` is non-blocking | VERIFIED | `RETURNED_AFTER_SECONDS=0.000`, `{"status":"starting","percent":0}` → later `{"status":"failed","detail":"pull model manifest: file does not exist"}` |
| Native WebView2 window opens | VERIFIED under the bundled interpreter | `pywebview import: OK`, `pythonnet/clr import: OK`, `_has_webview2() -> True`. A real window was created against `/chat`, ran 9.5 s, destroyed cleanly. WebView2 Evergreen `pv=150.0.4078.83` found in the `WOW6432Node` hive only — the other two probed hives are absent, so the multi-hive probe is load-bearing |
| Single-instance window guard | VERIFIED | Named mutex `Local\VOOL_WINDOW_SINGLETON`: process A `_single_instance() -> True` while B `-> False` |
| Launch chain | VERIFIED by inspection | Shortcut → `wscript.exe vool.vbs` (avoids CreateProcess 193 on `.vbs` and the console flash) → `VOOL.cmd` → bundled `ollama serve`, then `pythonw.exe vool_api_server.py --port 11435`, then `pythonw.exe vool_window.py`. Window waits ≤90 s for `/healthz`, gates on the WebView2 registry key, falls back to Edge `--app` via `vool-open.ps1`, logs to `%LOCALAPPDATA%\VOOL\open.log` |
| Console suppression | VERIFIED (code path + observation) | `core/windows_quiet_subprocess.py` patches `Popen.__init__` to OR in `CREATE_NO_WINDOW`; called at module level in `apps/vool_api_server.py:37-39`, before `main()`. No console windows appeared during any launch. Child `creationflags` were not read back |
| User-data redirection (`VOOL_HOME`) | VERIFIED | Redirected the whole runtime into a scratch dir with its own DB/keys/pid; the user's real `vool_api.pid` was untouched |
| Installer doctor runs | PARTIAL | `build_report()` → `OVERALL: degraded, DEGRADED: ['venv','install_receipt']`, ollama ok, `install_profile` ready, local-only, qwen2.5:7b, capacity bucket A. **But see below — it cannot grade a bundle install** |
| **Bundle launcher blocks startup on the model pull** | **BROKEN** | `VOOL.cmd` runs `ollama list \| find /I "qwen2.5:7b"` and, on failure, `ollama pull qwen2.5:7b` **synchronously at step 2**, before starting the server at step 3. On a genuine first run nothing binds 11435 until a ~4.7 GB download finishes — exactly the `ERR_CONNECTION_REFUSED` case the runtime fix removes. The runtime-side fix is real and works; it is **bypassed on the shipped Windows launch path** |
| **Persistent model store is empty** | **BROKEN on this box** | `VOOL.cmd` sets `OLLAMA_MODELS=%LOCALAPPDATA%\VOOL\models`; that directory has **0 entries**. The real 4.7 GB model lives in `%USERPROFILE%\.ollama\models`. The launcher's "already installed" guard passes only because a pre-existing default-store ollama server answers `ollama list` — verified: with `OLLAMA_MODELS` pointed at the empty dir, `ollama list` still printed the model. Start VOOL with no ollama already running and it serves from an empty store |
| **The installed build is stale** | **BROKEN** | `%LOCALAPPDATA%\Programs\VOOL\app\core\web\api\runtime.py` has **zero** matches for `start_ollama_model_pull` or `model_pull` (repo HEAD has them). Installed `update_channel.json` says `0.4.0-closed-test`; HEAD reports `0.4.1`. Install-root mtimes 13–14/07/2026. A user on the currently-shipped Windows installer still gets the blocking-startup behaviour |
| **Last real launch of the installed app timed out** | **BROKEN — observed** | `%LOCALAPPDATA%\VOOL\open.log` contains exactly one line: `server not ready after wait; opening anyway (may show a connect error)` |
| **No watchdog / respawn / auto-start for the bundle install** | **BROKEN (absent)** | `schtasks /query /tn "VOOL_Daemon"` → `ERROR: The system cannot find the file specified.` The onlogon watchdog exists only in the script-installer lane. `VOOL.cmd` has no health check, no retry, no respawn — three `start /B` calls, then exit |
| **Doctor cannot grade a bundle install** | **BROKEN (wrong target)** | `installer/doctor.py` grades the script-install layout (`.venv`, `install_receipt.json`, `Start_VOOL.bat`, …). The bundle root contains none of those, so pointed at a bundle it reports every launcher missing. Pointed at the repo it reports degraded for `venv`+`install_receipt`, which are source-checkout artifacts, not faults. Also `doctor.main()` **writes** `install_doctor.json` into the tree it inspects |
| **Model selection flips when Ollama is unreachable** | **BROKEN** | Ollama down → `/healthz` reports `model_tag "qwen3:4b"`. `VOOL.cmd` pulls `qwen2.5:7b` and `build_bundle.ps1` defaults to it. **The bundle and the runtime disagree about which model first-run gets** |
| **Capability truth reports `ready` for an absent model** | **BROKEN** | In the Ollama-down run, `model_pull.status="failed"` while the same payload carried `provider_capability_truth[0] availability_state:"ready", circuit_open:false, last_error:null` for `ollama-local:qwen3:4b` |
| **Signing key written unencrypted at startup** | **BROKEN (known, unfixed)** | Startup stderr: `Node signing key is being stored UNENCRYPTED at ...\data\keys\node_signing_key.b64 -- a stolen disk can read it. Set VOOL_KEY_PASSPHRASE or install a keyring backend...` Fires on a default Windows start with no opt-in |
| Cross-lane stale-process kill | BROKEN (narrow) | `Start_VOOL.bat:85` matches `*apps.vool_api_server*`; the bundle's command line is `...\app\apps\vool_api_server.py`, which does not match the dotted pattern. Only bites if both lanes are on one box — but they share port 11435 |
| Real first-run install from `VOOL-Setup.exe` | UNVERIFIED | No setup exe on this box, and it would overwrite the existing install. The ~4.7 GB re-download consequence of the empty model store is **inferred** from launcher logic plus the measured empty directory; it was not allowed to download |
| `build_bundle.ps1` produces a bundle | UNVERIFIED | Not executed (needs network downloads and a local `ollama.exe` path). Only the already-staged output on disk was inspected |
| Edge `--app` fallback (no WebView2) | UNVERIFIED | Never triggered — WebView2 is installed here |
| The installed bundle's own `/healthz` | UNVERIFIED | `VOOL.cmd` was never launched. All measurements were repo HEAD under a system Python |
| OpenClaw gateway (port 18789) | UNVERIFIED | Never started or probed. That lane is untouched by this audit |

**Correction to circulating notes:** there is no `core/vool_window.py`. The window module is `installer/bundle/vool_window.py`, installed to the install root.

---

## Cloud / OpenRouter

Baseline state on this box, read-only and unchanged before and after: no credential stored (`has_credential('llm.cloud.openrouter')` = `False`, `list_credentials()` = `[]`, `OPENROUTER_API_KEY` unset), policy `mode='off', daily_cap=25, on_cap_reached='ask', free_cloud_enabled=False, model=''`, `used_today()=0`. Live `/api/cloud/status` → `{"state":"no_key","detail":"no cloud key configured","model":"","mode":"off"}`.

| Capability | Status | Observed |
|---|---|---|
| Cloud chat commands exist and dispatch | VERIFIED | `cloud` / `cloud status` / `cloud off|ask|auto` / `cloud cap N` / `cloud key [<key>\|forget]` / `cloud model [...]` / `cloud models [free\|all\|coding]` / `cloud usage [...]`, optional leading slash. Regexes at `core/agent_runtime/fast_command_surface.py:46-68`, dispatched from `core/agent_runtime/turn_frontdoor.py:99-264` with the server-stamped owner flag |
| `cloud usage` drives the real ledger | VERIFIED | `Local (free): 3,435 tokens (6 responses) / Cloud (paid): 0 tokens (0 responses) ~$0.000000 est.` — matches `/api/runtime/usage` |
| `/api/cloud/models` returns a real live catalog | VERIFIED | 338 models with real prices, e.g. `qwen/qwen3-coder`, `context_length 1048576`, `prompt_usd_per_m 0.3`, `completion_usd_per_m 1.0` |
| Free-model verdict on **synthetic** payloads (the `1e4fd82` fix) | VERIFIED | no pricing → `known=False free=False`; empty `{}` → False/False; nulls → False/False; malformed `'free'` string → False/False; empty-string price → False/False; explicit zero (str and int) → `known=True free=True`; real paid → `known=True free=False, estimate_cost=0.002`. `estimate_cost()` returns `None` for every unpriced case, so an unknown-cost model can never rank as cheapest |
| **Free lane against the LIVE catalog** | **FIXED** (was BROKEN — 100% dead) | Was: 0 of 338 models classed free, including all 14 ids ending `:free`, because the verdict required a `pricing.request` fee OpenRouter publishes for no model. Re-measured 2026-07-20 on synthetic payloads: a zero-priced model with no `:free` suffix → `known=True free=True`; a model named `…:free` but priced → `known=True free=False`. The verdict is the published price, not the name. Unpriced / null / malformed / negative all still refuse (`known=False free=False`) |
| **`cloud models` message is factually false** | **FIXED** (was BROKEN) | Was: `I could not reach openrouter.ai just now and have no cached catalog…` while openrouter.ai was reachable throughout. `_free_models_text` (`fast_command_surface.py:545`) now branches on `age is None` — "no usable catalog" — instead of `if not free`, so "read the catalog, nothing is free" and "could not reach it" are three distinct wordings, one per fact |
| **`cloud model auto` silently selects a PAID model** | **BROKEN — root cause removed, fallback not re-driven** | `pick_auto_free_models` returned `{}` only because the free verdict was dead (row above); with 17 of 338 now classed free it should no longer reach the fallback. But `core/runtime_provider_defaults.py` still falls through to the paid `_DEFAULT_OPENROUTER_MODEL = 'openai/gpt-4.1-mini'` when the pick yields nothing, and **that fallback was not re-driven in this pass**. Treat as open until someone measures `cloud model auto` against an empty free set |
| **`recommend_openrouter_model()` is dead in production** | **FIXED** (was BROKEN) | Was: `None` for every input, because line 124 filters on `estimate_cost(...) is not None` and that was `None` for all 338 unpriced models. Re-measured 2026-07-20 against a synthetic 3-model catalog (`required_context=8000`, 1000/500 tokens) → returns `c/free`. The recommender was collateral damage from the free-verdict defect, not a separate bug |
| **Negative price clamps to free** | **FIXED** (was BROKEN) | Was: `_price` used `max(0.0, float(value))`, so `{'prompt':'-1',…}` → `pricing_known=True, is_free=True`. Re-measured 2026-07-20: `{'prompt':'-1','completion':'0'}` → `known=False free=False`. `_price` now returns `None` below zero, with the reasoning in the code — a negative price is a value the parser does not understand, not a discount |
| **BYOK paid burst end-to-end** | **FIXED** (was BROKEN — structurally unreachable) | `core/paid_call_reservation.py` builds the authorization server-side, so an owner-local explicit pick of a paid model now executes. See "Security & spend" for the cap measurement. Still gated on: owner-local only, explicit pick only, paid cost class only, never `tool_intent` |
| ask-mode approval path (consent returns True) | UNVERIFIED | `require_os_user_consent` was deliberately stubbed to raise, to avoid a real Windows Hello prompt. The **fail-closed** branch was verified (`(False, False)`); the success branch is unexercised |
| Any real paid call, key handling, or spend metering under load | UNVERIFIED | No credential exists on this box; no real key was used. `paid_cloud.responses = 0` all time |
| Settings-UI BYOK key-entry flow (`POST /api/settings/credentials`) | UNVERIFIED | Read-only audit; the seal/confirm/activate sequence was not driven |
| Whether the `request`-price requirement ever matched a real payload | UNVERIFIED | Only the 2026-07-19 catalog was sampled. No historical payloads checked, so it is unknown whether this ever worked or was broken from the moment `1e4fd82` landed |

**Free lane — the original measurement, and the fix.** The `1e4fd82` fix required `pricing.request` to be a published number before a model's pricing counted as known. OpenRouter publishes **no `request` key for any model**:

```
pricing keys across 338 models:
  {'prompt': 338, 'completion': 338, 'input_cache_read': 178,
   'web_search': 101, 'input_cache_write': 53, ...}      # 'request' appears 0 times
```

So `model_pricing_is_known()` was `False` for 338/338 and `model_is_free()` `False` for all 338 **including the 14 ids literally ending in `:free`**. The UI showed `$0.00/M` and "not free" simultaneously. The direction was fail-safe — nothing was wrongly charged — but the free lane was unusable, and that shipped.

**Re-measured 2026-07-20 at `b88e8a9`** (synthetic payloads through `parse_openrouter_catalog`, no network):

```
a/zero:free                known=True  free=True    # zero-priced, :free suffix
b/zero-noname              known=True  free=True    # zero-priced, NO suffix  -> price wins
c/paid                     known=True  free=False
d/named-free-but-paid:free known=True  free=False   # :free suffix, real price -> name loses
e/nopricing                known=False free=False
f/nulls                    known=False free=False
g/malformed                known=False free=False
h/negative                 known=False free=False
```

Two properties matter for the Apple lane to re-check: the verdict needs only the per-token pair (no `request` key), and it is **derived from price, not from the model id**. Rows `b` and `d` are the pair that proves it — a free model without the suffix is still free, and a paid model wearing the suffix is still paid. All five unpublished-pricing shapes still refuse.

---

## Security & spend

| Control | Status | Observed |
|---|---|---|
| Path confinement — system file read | VERIFIED by observed refusal | `machine.read_file 'C:/Windows/System32/drivers/etc/hosts'` → `ok=False status=not_allowed: "I cannot read that path in this lane. I can only read files inside: ~/Desktop, ~/Downloads, ~/Documents."` |
| Path confinement — secret-file traversal | VERIFIED | `machine.read_file '../../.env'` → same refusal |
| Path confinement — workspace escape | VERIFIED | `workspace.read_file '../../../Windows/win.ini'` → `status=error: "Path escapes the active workspace."` |
| Path confinement — directory listing | VERIFIED | `machine.list_directory 'C:/Windows/System32'` → `status=not_allowed` |
| Protected-path move guard | VERIFIED — refusal observed, no file moved | `machine.move_path 'C:/Windows/System32/kernel32.dll'` → `status=blocked_protected_path: "That path is a protected system or wallet location and cannot be moved."` |
| Credential-dir denylist on Windows | VERIFIED | `_protected_roots()` returned **16** roots: `~/.ssh` [exists], `~/.aws` [exists], `~/.gnupg`, `~/.config/solana` [exists], `~/.config/gcloud`, `~/.config/gh`, `%APPDATA%\gnupg`, `%APPDATA%\Microsoft\Credentials` [exists], `%LOCALAPPDATA%\gnupg`, `%LOCALAPPDATA%\Microsoft\Credentials` [exists], `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`, `C:\ProgramData`, plus the VOOL home. `_is_protected`: all True; `~/Desktop`, `~/Downloads`, `~/Documents/a.docx` False. Bare drive anchor `C:\` and any unresolvable path are treated as protected (fails closed) |
| `move_path` split behaviour | VERIFIED | `~/.ssh` → `blocked_protected_path`; `~/.aws` → `blocked_protected_path`; `~/Desktop` → `consent_declined`. Credential dirs are refused **before** the consent gate; Desktop remains movable behind consent |
| Spend gate — auto-fallback matrix | VERIFIED (policy swapped in memory only; disk policy re-read unchanged) | `mode=off` remote/owner → `(False,False)`; `mode=ask` remote/owner → `(False,False)`; `mode=auto` remote → `(False,False)`; `mode=auto` owner-local → `(True,True)`. **A remote caller never gets an auto burst in any mode** |
| Spend gate — explicit paid request | VERIFIED | `off`/`auto` remote → `(False,False)`; owner-local → `(True,False)` (honoured as a deliberate choice, not charged to the daily cap). `resolved_allow_paid` already False → untouched |
| Forged trust flags rejected | VERIFIED | `{'cloud_escalation_approved':True,'_owner_local':False}` → `(False,False)`; even with forged `_owner_local:True` → `(False,False)`. The flag is not read by the gate at all, **and** `strip_reserved_trust_keys` removes it: input → `{'surface':'cli'}`. Wired on the real HTTP path at `core/web/api/service.py:1434` (strip) then `:1457` (server stamps owner-local from `is_loopback_host(client_host)`) |
| `authorized_paid_call` backstop | VERIFIED (unchanged) | `core/memory_first_router.py` forces `resolved_allow_paid=False` whenever the key is `None`, and filters paid manifests lacking a valid authorization. `_paid_call_authorization` rejected a forged dict, `True`, and `'approved'`. **What changed:** the authorization is now built server-side by `core/paid_call_reservation.py`, so the key is no longer always `None`. It is still never read from an inbound body — `reserve_owner_pick_paid_call` reads only the server-stamped owner-local flag plus inert ids, and a caller-supplied `authorized_paid_call` is still stripped and never consulted |
| **Paid spend caps bind under concurrency** | **VERIFIED — measured this pass** | 25 simultaneous owner-local picks of `claude-sonnet-4-5` (12k prompt / 900 completion) against `per_call $0.25 / per_task $1.00 / daily $5.00`: **20 granted, 5 refused, 0 errors**; settled cost **$0.99**; the worst case is **$5.00**, the cap itself, because 20 reservations at the $0.25 floor is exactly what fits. No key, no network — the reservation and settlement ledger were driven directly. The mechanism is `_reservation_ceiling_usd`: each call reserves what *that* call is estimated to cost at published rates, so a flat ceiling can no longer let 20 reservations stand under a $5.00 day while each settles near $1.08 (the measured $21.60 overshoot this replaced) |
| **Paid settlement is priced from tokens** | VERIFIED by suite | Settlement previously read `usage.cost`, which **only OpenRouter returns**, so calls to the other seven slots settled $0.00 and the USD cap never advanced. Now priced from token counts at published rates. 99 tests green across the five reservation/settlement/gate suites; `test_paid_call_settlement_ledger.py` drives the real spend ledger with replayed token counts (no adapter, transport or key) |
| Secret redaction | VERIFIED on real inputs | `sk-or-v1-...` → `[redacted-api-key]`; `OPENROUTER_API_KEY=...` → `[redacted-api-key]`; `api_key: hunter2xyz` → `[redacted]`; `Bearer eyJ...` → `[redacted-jwt]`; AWS `AKIA...` and `ghp_...` → `[redacted-api-key]`; OPENSSH block → `[redacted-private-key]`; WIF `5KJvsng...` → `[redacted-key]`. A 44-char Solana **public** address and ordinary prose pass through unmodified. Applied on the real persistence path at `core/persistent_memory.py:170-171`, to **both** user and assistant text |
| Credential store never leaks through the listing surface | VERIFIED | With a raw store containing plaintext and ciphertext, `list_credentials()` → `[{'name':'llm.cloud.openrouter','label':'OpenRouter BYOK'}]`; probes for the plaintext, ciphertext, nonce, and the keys `ct_b64`/`nonce_b64`/`value` all returned False. Live `GET /api/settings/credentials` → `{"credentials":[]}` |
| Non-owner cannot mutate cloud state | VERIFIED | With `owner_local=False`: `cloud auto`, `cloud cap 500` → `"Cloud escalation can only be changed from your own local session."`; `cloud key forget` → refused; `cloud model ...` → refused; a bare pasted `sk-or-` key → refused and **not echoed back**. `store_credential`/`delete_credential` were stubbed to raise — neither fired, so no mutation was even attempted |
| HTTP write endpoints loopback-gated from the real TCP peer | VERIFIED | `POST /api/cloud/model` → `set_cloud_model(model, owner_local=is_loopback_host(client_host))`, 403 otherwise (`service.py:1210-1214`). `POST /api/settings/credentials` allowlists exactly `{'llm.cloud.openrouter'}` and enforces JSON content-type, Origin/Host allowlist, 64 KB body cap, unknown-field rejection (`service.py:1093-1120, 1174-1205`) |
| `pay.x402` fail-closed | VERIFIED — refusal observed, no spend | `status=user_action_required, mode=tool_preview: "No spend opt-in was given (allow_spend is off). Re-call pay.x402 with allow_spend=true, approve=true, and a max_spend_usdc cap (<= 1.00)"` |
| `web0.publish` opt-in gate | VERIFIED | `status=requires_opt_in: "...needs an explicit opt-in plus a caller-supplied wallet — it never runs autonomously."` |
| `operator.cleanup_temp_files` approval gate | VERIFIED — nothing deleted | `status=approval_required: "Temp cleanup is ready but still needs explicit approval. Reply with: approve cleanup <id>"` |
| **Secret disclosure to a non-owner** | **FIXED** (was BROKEN) | Was: `maybe_handle_cloud_key_status_intent` accepted `owner_local` and never used it, so a non-owner asking "do we have the api key set?" got `"yes — a cloud key is sealed in the encrypted store (ends WXYZ)…"` — key suffix plus confirmation a key exists. Re-driven 2026-07-20 with the same stubbed key and `owner_local=False` → `"Cloud-key status is owner-local only, so I will not say whether a key is stored. Ask from your own local session."` No suffix, and the store is not read at all |
| **Two further non-owner disclosures (found by AST sweep)** | **FIXED** | The original finding was reported as one handler. An AST sweep of every function taking `owner_local` found **two more** that never referenced it: the OpenRouter onboarding intent (both branches keyed on whether a key is stored, so answering at all disclosed key existence) and `cloud usage` (the ledger names the models the owner runs and what they cost). Both re-driven with `owner_local=False` → refusals. **Sweep re-run this pass:** 20 functions take `owner_local`; the 3 that do not reference it are the public-catalog readers documented at `fast_command_surface.py:496`, which serve only the unauthenticated openrouter.ai list and disclose nothing about this machine |
| **Signing key unencrypted at rest by default** | **BROKEN (known, unfixed)** | Re-confirmed 2026-07-20: the warning still lives at `network/signer.py:321` and still fires on a default Windows start with no opt-in. Warned, not prevented |
| End-to-end HTTP forgery against the live install | UNVERIFIED | `POST /api/chat` writes a session log, which the audit rules forbade. The strip+stamp logic was verified in-process and read at its real call site, but not driven through the socket |
| `move_path` against `~/.gnupg` end-to-end | UNVERIFIED | The directory does not exist here, so the existence check fires first (`status=source_missing`). `_is_protected(~/.gnupg)` → `True`, so the denylist entry is correct, but the full refusal path for that dir was not observed |

**Windows-specific note on the credential store:** `credential_store._save_raw` only `chmod`s on POSIX. On Windows the encrypted store file relies on the user-profile ACL. This is stated in the code and is the accepted local-first boundary, but it means the file inherits whatever the profile grants.

---

## UI & API

Served surface: `/chat` → 200, 102,106 bytes, self-contained inline CSS/JS (`core/vool_chat_page.py`, 1718 lines). `/trace` → 200 (75,927 B). `/web0` → 200 (14,509 B).

| Capability | Status | Observed |
|---|---|---|
| Sessions sidebar (list + history) | VERIFIED end-to-end | `GET /api/chat/sessions` → real threads e.g. `{"session_id":"openclaw:c2-gate","title":"what is my screen resolution?","turn_count":4,"archived":false}`. `GET /api/chat/history?session=...` → real transcript |
| Sidebar rename is a real backend mutation | VERIFIED | `POST /api/chat/session {title:"AUDIT RENAME PROBE"}` → `{"meta":{"title":"AUDIT RENAME PROBE"}}`, appeared in the list, restored cleanly. Unknown-field guard live: `{"colour":"red"}` → `{"error":"unknown fields: ['colour']"}` |
| Streaming chat + typed `vool_event` task-event channel | VERIFIED | Produced `task.started`, `task.stage_changed`, `model.changed` ×3, `model.call_started`, `model.call_completed`, `cloud.cost_updated`, `task.completed`, carrying `{"lane":"daily","paid":false,"provider_id":"ollama-local:qwen2.5:7b","model_id":"qwen2.5:7b","model_call_id":"model-call-32f7ae6f..."}`. This is what drives the task card, exec panel and snake |
| Mode selector Ask / Plan / Build is **really enforced** | VERIFIED | `mode=ask` → `"You're in Ask mode, which is read-only, so I didn't create or change any files. Switch the mode selector to Build or Auto..."` and **no file created**. `mode=plan` → same. `mode=build` → `"I wrote \`audit_probe_build.txt\`"` and the file genuinely appeared (confirmed via `git status`, then removed). Enforcement at `core/agent_runtime/turn_dispatch.py:211` and `core/agent_runtime/builder_facade.py:336` |
| Usage panel reads a real ledger | VERIFIED | Started at all-zeros; after 12 driven turns `free_local {responses:12, prompt_tokens:10343, output_tokens:2087, total_tokens:12430}` with a correct `by_model` row |
| Cloud pill in the no-key state | VERIFIED honest | `/api/cloud/status` `state:"no_key"` → gray "Local only" pill (`vool_chat_page.py:1418`) |
| Cloud model id requested with no key | VERIFIED — degrades honestly | `model="anthropic/claude-3.5-sonnet"` answered from local with the footer `local | qwen2.5:7b | 472 tok` |
| Message queue behind the composer chips | VERIFIED | `enqueue` → `{"queue_item_id":"msg-2737de05...","status":"pending"}`; `claim` → `in_flight` (atomic); `complete` → `[]` |
| Canonical session id from the UI | VERIFIED | The page mints `openclaw:<20 hex>` and the server persisted it verbatim into sessions and history |
| **Model picker "Heavy" / effort "Smarter"** | **FIXED by removal** (was BROKEN — 3/3) | Was: both returned `"I couldn't get a usable model response in this run…"` with no `model_id` in any event, because with one enabled manifest the heavy request collapsed the ranking to empty. The selectors are gone (`local-heavy`, `smarter` → 0 hits in `core/vool_chat_page.py`). `service.py:1588` still accepts `effort=smarter` from an API caller and sets the autopilot heavy hook — **that path was not re-driven**, so on an 8 GB box an explicit API `effort=smarter` may still collapse. The dead *control* is gone; the underlying single-manifest limit is not |
| **Effort "Faster" / "Balanced"** | **FIXED by removal** (was BROKEN — dead controls) | Was: `service.py` wrote `source_context["effort"]` with no consumer anywhere; `faster` and `balanced` routed byte-identically. Both selectors removed. The server now accepts `effort` **only** for `smarter`, with the reason in a comment at `service.py:1585`: a stored key nobody reads is not a setting |
| **Model tiers "Fast" / "Daily" / "VOOL Auto"** | **FIXED by removal** (was BROKEN — cannot differ) | Was: `local-fast`/`local-daily`/`local-heavy` appeared nowhere outside `vool_chat_page.py`, fell through to a catch-all, matched no manifest, and collapsed onto `qwen2.5:7b`. All three strings → 0 hits. The picker no longer offers a distinction the runtime cannot make |
| **Mode "Bypass permissions"** | **FIXED by removal** (was BROKEN — mislabelled) | Was: a duplicate Build button wearing a security-sounding label — `mode=bypass` wrote a file exactly like Build, and no handler for `bypass` existed. `grep -c bypass core/vool_chat_page.py` → **0**. Removed rather than relabelled |
| **Paid-cloud UI strings** | **FIXED** (was BROKEN — overclaim) | Was: "Paid cloud models are available", "unlock the paid cloud models" etc. went green on a saved key while no paid call could execute. The overclaiming strings are gone (0 hits), and the claim they made is now true anyway — the paid lane executes behind a reservation (see Security & spend) |
| **Thumbs up/down** | **FIXED by removal** (was BROKEN — cosmetic) | Was: `rateMsg` wrote only `localStorage['vool_ratings']` with no feedback route in `service.py`; the signal never left the browser. Both `rateMsg` and `vool_ratings` → 0 hits. Removed rather than left looking functional |
| **Projects sidebar** | **PARTIAL — still browser-only, now disclosed** (was BROKEN — undisclosed) | `createProject`/`assignSession`/`deleteProject` are **still pure `localStorage`** with no server route and no sync. What changed is the honesty: three disclosures now say so, including the `+ Project` tooltip (`"projects are saved in this browser only, never on the server"`) and an in-panel line (`"clearing site data removes them, and other browsers will not see them"`). The limitation is unchanged; the silence about it is not |
| **Stop button** | **BROKEN — client-only. Re-checked 2026-07-20, unchanged** | `refs.stop` aborts the client fetch. There is still **no server-side cancel route** for an in-flight `/api/chat` turn — the only `cancel` in `service.py` remains queue-item cancel (line 1129). An aborted turn still billed +1 response / +148 output tokens to the meter |
| **`web0_enabled` parameter** | **FIXED by removal** (was BROKEN — dead) | Was: the served page always carried `WEB0_ENABLED = false` because `render_vool_chat_html()` was called bare, so the Web0 header link was permanently hidden. `WEB0_ENABLED` → 0 hits; the dead parameter and the permanently-hidden link are gone. `/web0` still resolves from Settings → Advanced |
| Composer "+" attach button | **FIXED by removal** (was: disabled by design) | Was a visible control that could never be clicked, tooltip "Add context (coming in Settings)". Both the tooltip string and the control are gone (0 hits) |
| Cloud pill `ok`/`failed`/`untested`, `POST /api/cloud/test`, `POST /api/cloud/model` | UNVERIFIED | Require a real key, which the audit rules forbade. Only the `no_key` path was exercised. Note the code path predicts these unlock the picker without unlocking execution |
| Whether the backend truly aborts generation on Stop | UNVERIFIED | Ambiguous: the meter rose by exactly 1 response / 148 tokens then stopped climbing over ~60 s, and no transcript was written. That rules out "it finished the whole essay" but does not prove prompt cancellation |
| Permission/approval-pause UX (paused snake, "Waiting for your approval", `.task-card.perm`) | UNVERIFIED | No driven turn triggered a permission gate |
| Execution-panel tabs Changes / Files / Tests / Terminal / Preview with real data | UNVERIFIED | Only Activity/Plan-shaped step events were observed; `run.files`, `run.tests`, `run.preview` were never populated |
| Receipts tab with real signed receipts | UNVERIFIED | `GET /api/runtime/receipts` returned the honest empty shape `{"receipts":[],"chain_verified":null,"chain_detail":""}`; no session produced signed receipts, so true/false rendering is untested |
| Mode "Auto" | UNVERIFIED | `builder_facade` groups it with `build`, so it is presumably live, but only ask/plan/build/bypass were driven |
| Actual DOM click-through | UNVERIFIED | All UI behaviour was verified by driving the exact HTTP calls the page's JS makes, plus reading the served HTML. No headless browser was run against the page |

**Read-only runtime endpoints, all VERIFIED 200:** `/healthz` (`build_id 0.4.1-closed-test+1e4fd82d09d7`, `model_tag qwen2.5:7b`, `model_pull ready`), `/api/runtime/usage`, `/api/runtime/receipts` (empty chain), `/api/runtime/capabilities` (`local_only_mode:true, public_hive_enabled:false, allow_workspace_writes:true, allow_sandbox_execution:true`), `/api/chat/sessions`, `/api/settings/credentials`, `/api/cloud/status`, `/api/cloud/models`.

---

## Known broken / open

Ordered by consequence. Nothing here is buried elsewhere in the document. **Numbering is stable
from the `1e4fd82` audit** — a fixed item keeps its number and is struck through with its
measurement, so a reader can see what moved rather than finding a shorter list and guessing.

Legend: **~~struck~~ = FIXED**, re-driven 2026-07-20 at `b88e8a9`. Plain = still open.

### Wrong answers presented as successes

1. **`workspace.search_text` / `workspace.symbol_search` return `ok=True status=no_results` for strings that exist.** 500-file scan cap consumed by 390 `.pyc` files; 105 of 7426 files are actually searched. Only explicit `path` scoping works. `core/runtime_execution_tools.py:2804`, `:2122-2143`. **Re-driven 2026-07-20 — unchanged:** bare query → `no_results`; `path:'tools'` → the correct `tools/registry.py:41` hit. Still the worst class of defect here, because `ok=True` means an agent reports "not found" as a successful search.
2. **Word-phrased arithmetic is destroyed by the sanitizer, 3/3.** "4821 multiplied by 37" → `I finished the work, but I'm stripping internal orchestration details from the reply.` (37–66 s), while `4821 * 37` answers in 0.1 s. Catch-all return at `core/agent_runtime/response.py:318` drops the whole answer, not the offending fragment. **Not re-driven 2026-07-20** (needs a live model turn). The catch-all is still at `response.py:318`, so the mechanism is unchanged — but that is a code read, which is weaker than the original 3/3 measurement.
3. **Two fast paths intermittently hijack ordinary questions.** A plain factual question returned a video shot plan (1 of 3 attempts); a word problem returned the user's own prompt plus scraped MDN/Google-cookie text. Trigger rate not established. **Not re-driven 2026-07-20** (needs live model turns).
4. **Silent-ignore-unknown-args turns model arg drift into wrong answers.** `machine.list_directory {directory:'Downloads'}` → `ok=True` with `~/Desktop` contents. **Re-driven 2026-07-20 — unchanged:** same call, `ok=True status=executed`, `Visible entries under ~/Desktop`. The label is honest; the answer is about the wrong directory.
5. ~~**`cloud models` reports a network failure that did not happen.**~~ **FIXED.** `_free_models_text` (`fast_command_surface.py:545`) now branches on `age is None` — no usable catalog — instead of `if not free`, so "could not reach openrouter.ai", "read it, nothing is free", and "nothing free matches this filter" are three separate sentences.

### Security / disclosure

6. ~~**Non-owner key-status disclosure.**~~ **FIXED,** and it was three handlers, not one. An AST sweep of every function taking `owner_local` found the reported key-status handler plus the OpenRouter onboarding intent and `cloud usage`. All three re-driven with `owner_local=False` → refusals, no store read. Sweep re-run this pass: the only three functions that still take `owner_local` without referencing it are the public-catalog readers, which serve the unauthenticated openrouter.ai list and disclose nothing about this machine.
7. **Node signing key written unencrypted at rest by default.** The runtime warns at startup and proceeds. Fixing requires the user to set `VOOL_KEY_PASSPHRASE` or install a keyring backend. **Re-confirmed 2026-07-20 — unchanged** (`network/signer.py:321`).
8. ~~**Mode "Bypass permissions" bypasses nothing and behaves as Build.**~~ **FIXED by removal.** `grep -c bypass core/vool_chat_page.py` → 0. The mode selector is now Ask / Plan / Build / Auto.
9. ~~**Negative price clamps to free**~~ **FIXED.** `_price` returns `None` below zero instead of `max(0.0, …)`, so `{'prompt':'-1'}` → `known=False free=False`.
**N1 — NEW, not one of the original 34: Windows credential directories were movable.**
`machine_file_ops`' credential-directory guard had been gated to non-Windows, so `move_path` could
move `~/.ssh/id_rsa` and `~/.aws/credentials` on the one platform that ships an installer.
**FIXED and re-driven 2026-07-20:** 16 protected roots on `win32`; `~/.ssh`, `~/.aws` and
`kernel32.dll` each → `blocked_protected_path`, all three still present afterwards.

**N2 — NEW: two further non-owner disclosures.** Found by the AST sweep that item 6 prompted; see
item 6 for the measurement.

### Capabilities that do not work as advertised

10. ~~**The free OpenRouter lane was 100% dead against the live catalog.**~~ **FIXED** (`d9381a6`, then `a2a7824` for routing). The verdict required a `pricing.request` fee OpenRouter publishes for no model, so 0 of 338 read free including all 14 ids ending `:free`. It now needs only the per-token pair. Re-measured 2026-07-20 on synthetic payloads: zero-priced **without** the `:free` suffix → free; **named** `:free` but priced → not free. The verdict follows the price, not the id. All five unpublished-pricing shapes still refuse.
11. **`cloud model auto` can still fall through to a paid model** (`openai/gpt-4.1-mini`) when the free pick yields nothing. The trigger that made this fire constantly — the dead free verdict — is fixed, so it should now be unreachable in practice, but **the fallback itself is unchanged and was not re-driven against an empty free set.** Open until measured.
12. ~~**`recommend_openrouter_model()` returns `None` for every input.**~~ **FIXED.** It was collateral damage from #10: line 124 filters on `estimate_cost(...) is not None`, which was `None` for all 338 unpriced models. Re-measured 2026-07-20 against a synthetic catalog → returns a model.
13. ~~**The paid cloud lane cannot execute at all.**~~ **FIXED.** `core/paid_call_reservation.py` builds the authorization server-side for an owner-local explicit pick. It is still never read from an inbound body. **And the caps now bind:** an independent 25-way concurrent attack written for this pass granted 20, refused 5, settled cost **$0.99**, worst case **$5.00** (the cap itself) — because each call reserves what that call is estimated to cost rather than a flat ceiling. Settlement is priced from token counts at published rates, not from `usage.cost`, which only OpenRouter returns. No key and no network were involved; the ledger was driven directly.
14. ~~**Model picker "Heavy" and effort "Smarter" both kill the turn.**~~ **FIXED by removal of the controls.** Note the boundary: the *selectors* are gone, but `service.py:1588` still honours an API-supplied `effort=smarter`, and the single-enabled-manifest condition that made the heavy lane collapse on an 8 GB box is unchanged. A direct API caller can still reach it; that path was not re-driven.
15. ~~**Effort "Faster"/"Balanced" and tiers "Fast"/"Daily" are transmitted and dropped.**~~ **FIXED by removal.** All four selectors gone; the server now accepts `effort` only for the one value that reaches real machinery.
16. **Thumbs removed; the Projects sidebar is still `localStorage`-only — but now says so.** Thumbs and their `vool_ratings` store are gone (0 hits). Projects remain browser-only with no server route; the change is that three places now disclose it, including the `+ Project` tooltip. The limitation stands; the silence about it does not.
17. **Stop is client-side only.** No server cancel route; an aborted turn still billed +1 response / +148 tokens to the meter. **Re-checked 2026-07-20 — unchanged:** the only `cancel` in `service.py` is queue-item cancel (line 1129).
18. **`sell.quote` pays a hardcoded `stub-wallet`.** The quote surface works; the capability is not settleable as shipped. **Re-driven 2026-07-20 — unchanged:** `Quote for task compute: 0.000500 USDC to stub-wallet`. Literal default still at `core/null_protocol.py:110`, `core/web0_work_receipt.py:63`, `core/web/api/service.py:165,1403`.
19. **`hive.list_available` is unreachable while the capability ledger advertises `hive.read` as supported** — ledger and runtime disagree. **Re-driven 2026-07-20 — unchanged:** `ok=False`.
20. **`web.browser_render` is `disabled_by_policy`** despite `browser.render` being registered. **Re-driven 2026-07-20 — unchanged:** `ok=False status=disabled_by_policy`.
21. **`web.research` labels weak evidence `strong_evidence`.** Not re-driven this pass (needs a live network research run).

### Install / shell

**Scope note for 22–30:** these describe the **installed bundle** on the audit box. This
re-verification pass ran from a worktree with no installed bundle, so **none of 22–30 were
re-driven**. They are carried forward from `1e4fd82` unchanged and must not be read as
re-confirmed. Two of them have known code-level movement, noted inline.

22. **The shipped bundle launcher blocks startup on a synchronous ~4.7 GB model pull**, defeating the runtime's own bind-first fix. The runtime fix is real and verified; the launch path bypasses it. **Code re-read 2026-07-20 — unchanged:** `installer/bundle/vool-launch.cmd:23-26` still runs `ollama list | find /I "qwen2.5:7b"` then a synchronous `ollama pull` at step 2, before the server starts.
23. **The installed bundle on this box is stale (0.4.0) and does not contain the fix** — no `start_ollama_model_pull` in its `runtime.py`. Users on the current installer still get blocking startup. **Partially addressed:** the *cause of the version confusion* is fixed — the installer version was hardcoded at `0.4.0` across four disagreeing sources, and now derives from `core/app_version.py` (`VOOL_VERSION = "0.4.3"`), with `vool.iss` taking `/DAppVersion` and falling back to `0.0.0-unstamped` rather than to a stale number. **The stale bundle itself is not addressed** — that needs a rebuild, which this pass did not do.
24. **The persistent model store `%LOCALAPPDATA%\VOOL\models` is empty**; the model actually lives in the default ollama store. The "already installed" guard only passes because a pre-existing default-store server answers. The claim that the model survives uninstall does not hold here.
25. **`model_tag` flips to `qwen3:4b` whenever Ollama is not reachable** — an uninstalled thinking model that the surrounding code documents as unusable for chat. The bundle pulls `qwen2.5:7b`. Bundle and runtime disagree about the first-run model. **Re-driven 2026-07-20 — unchanged:** with the inventory probe stubbed empty, `default_runtime_model_tag()` → `qwen3:4b`; with `qwen2.5:7b` installed → `qwen2.5:7b`.
26. **`provider_capability_truth` reported `availability_state:"ready"` in the same payload where `model_pull.status` was `"failed"`.**
27. **No watchdog, respawn, or auto-start for the bundle install**; `VOOL_Daemon` scheduled task does not exist. `VOOL.cmd` fires three `start /B` calls and exits.
28. **The last real launch of the installed app timed out**: `open.log` = `server not ready after wait; opening anyway (may show a connect error)`.
29. **`installer/doctor.py` cannot grade a bundle install** (it grades the script-install layout) and `doctor.main()` writes `install_doctor.json` into the tree it inspects. **Code re-read 2026-07-20 — unchanged** (`doctor.py:185,238,293`).
30. **`Start_VOOL.bat`'s stale-process kill pattern does not match bundle command lines**, though both lanes share port 11435.

### Stability

31. **The API server and Ollama both exited during ordinary sequential single-user chat**, silently, with no traceback and no respawn. Free RAM at the time: 1493 MB of 8141 MB. Root cause not established — memory pressure is the obvious suspect but no OOM event was captured and the failure was not re-run. Do not record this as OOM.

### Windows-lane categorical gap

32. **`sandbox.run_command` is unavailable on Windows** — no kernel network-isolation backend. The fixed test/lint lane still executes real subprocesses. Escape hatch (`network_isolation_mode=heuristic_only`) exists and was not used. **Re-driven 2026-07-20 — unchanged:** `ok=False status=blocked_by_policy`. This is the row the Apple lane should expect to differ: the refusal text names `sandbox-exec` as the macOS backend, so the same intent is expected to **work** on macOS.

### Cosmetic

33. **Drive double-count**: an aliased (`subst`) drive pair is counted twice in the "Total free" line. **Code re-read 2026-07-20 — unchanged** (`core/runtime_execution_tools.py:610` sums every row).
34. **Console encoding**: em-dash/middot in tool output renders as mojibake under the default Windows code page (`WSL ? 26.34 GB`); correct with a UTF-8 stdout wrapper. This was a harness artifact, not a tool defect, but it is a real risk for any Windows CLI surface printing those glyphs without forcing UTF-8.

---

## Conflicts between audit passes

Printed rather than reconciled.

1. **"The live install" means two different things.** Two passes probed a server on `127.0.0.1:11435` reporting `0.4.1-closed-test+1e4fd82d09d7` and described it as "the live install". A third pass inspected the **actual installed bundle** on disk and found `0.4.0-closed-test` with the startup fix absent. Both are correct: the 11435 server was a **source-run** process at HEAD, not the installed bundle. Read every "live install" claim in this document as *repo HEAD run from source*, and every install/app-shell claim about the bundle as applying to the stale 0.4.0 build on disk.

2. **Running-service state at audit start.** Three passes independently reported that neither Ollama nor VOOL was running when they began, and started services themselves. One pass observed a foreign VOOL server appear on 11435 mid-audit (PID 8616, parent already gone) that it could not attribute to any respawn code — no runtime code was found that spawns the API server. Most likely a concurrent audit lane. **Consequence:** usage-meter figures differ across passes (3,435 tokens / 6 responses in one; 0 → 12,430 tokens / 12 responses in another) because they measured different server processes. Neither number is wrong; neither is a repo-wide constant.

3. **Model inventory.** The audit brief listed six installed models (`qwen2.5:7b`, `qwen3:4b`, `qwen3:8b`, `qwen3:0.6b`, `deepseek-r1:14b`, `nomic-embed-text`). `ollama list` shows exactly one: `qwen2.5:7b`. Confirmed by a live 404 for `qwen3:4b`. The brief is wrong for this box. Downstream: `nomic-embed-text` being absent means the memory benchmark ran on the fallback `hash-bow:384d` backend, not real embeddings.

4. **Capability ledger vs runtime, on hive.** The ledger reports `hive.read` as `supported:true`; `hive.list_available` returns `status=unreachable`. Unresolved — the ledger is not a reliable statement of runtime reachability for this family.

5. **Thin evidence, stated as thin.** Nobody drove the model-to-tool-call link end-to-end. All tool verification used hand-built `{intent, arguments}` payloads through `execute_tool_intent`. **Whether `qwen2.5:7b` reliably emits these intents with correct argument names from natural language is untested**, and given the silent-ignore-unknown-args behaviour, that is the single largest untested link in the tool surface.

---

## Method and state

- Tools were driven by calling `core.tool_intent_executor.execute_tool_intent()` directly with hand-built payloads. Gates were verified by **observing actual refusals** (7 distinct refusals recorded), never by reading guard code.
- Chat and UI were driven through the real HTTP entry point (`apps/vool_api_server.py`) with the exact calls the chat page's JS makes.
- Install behaviour was measured by cold-starting repo HEAD on scratch ports (11436/11437) with `VOOL_HOME` redirected to a scratch dir, so the user's install data was not mutated.
- Cloud/spend was exercised in-process with the policy swapped **in memory only**; the on-disk policy was re-read afterwards and was unchanged.
- **No source was edited. No commit, no push. No API key was used. No paid call was made** — `paid_cloud.responses = 0` all time. Two `audit_probe_*.txt` files created by Build-mode tests were removed; `git status --porcelain` returned 0 files afterwards.
- One pass left `ollama.exe` running on 11434 deliberately and did not kill a foreign process belonging to another lane.
- Actions explicitly **not** taken, and why: the uninstaller was not run (destroys the user's install); `VOOL-Setup.exe` was not installed (would overwrite); `build_bundle.ps1` was not executed (network downloads); `qwen3:4b` was not pulled (multi-GB mutation); `require_os_user_consent` was stubbed rather than triggering a real Windows Hello prompt; no forged HTTP body was sent to `/api/chat` (would write a session log).

**Files most implicated across the audits** (repo-relative):

### KAS current-main rebase and context-integrity firewall candidate (#71)

This addendum records the KAS source checkout rebased onto exact `main` `507e4fe`. It updates only
KAS-owned Windows evidence and makes no Apple/Loop claim. The candidate is not a shipped installer.

| Capability | KAS result | Evidence |
|---|---|---|
| Scoped turn context and default-deny retrieval | VERIFIED in source regression; exact rebased installed runtime remains UNVERIFIED | PR #71's server-created turn policy, scoped candidate filtering, correction authority, archive/lifecycle handling, and receipt-only tool context passed the changed-test sweep (`935 passed`) and focused context/provider pack (`211 passed`) after rebasing onto `507e4fe` |
| Final provider-payload provenance | SOURCE-FIXED after installed and loaded-shard defects; corrected exact-head proof remains UNVERIFIED | Candidate `677e4c5` produced the signed context and provider manifests plus a real `model.call_completed` receipt, but `turn.trace_completed` lost their join across a copied request context. The terminal trace now joins only same-request/same-client-turn completed-call receipts from the newest bounded event window, carrying redacted context/provider IDs, final payload hash, model identity, and output-control verdict. Constraint retries link the provider payload that actually produced the visible response rather than the discarded draft. Regressions place more than 200 older events ahead of the turn and prove no raw prompt or credential is persisted. A later four-worker run exposed one non-canonical manifest persistence error. Concurrent repetition proved a generated session-summary source-ID hash was occasionally reclassified as base58 and rehashed on the second sanitizer pass; canonical hash tokens are now idempotent. Finite-score normalization separately prevents NaN/infinity from entering ranking, report records, signatures, or JSON persistence. The installed candidate predates these repairs and is not accepted as final proof |
| Ordinary-chat routing and structural response controls | VERIFIED in source regression after installed live-model blockers were reproduced | The installed stack first misrouted a canonical VOOL identity question to research, then failed to parse spelled six-word constraints, then exposed repair defects: an underlength retry replaced a usable initial draft and a later prose-only retry returned generic fallback instead of identity. Candidate `8de6b10` then proved the first exact-count schema's string pattern failed on the shipped Ollama/llama.cpp grammar compiler. Canonical project questions now use allowlisted repository grounding; explicit spelled counts through twenty become structural constraints; and longer exact-count repair uses a transport-portable exact-length array with strict local word validation before joining. No expected identity answer is embedded in production. Exact installed proof remains pending |
| Compatibility with current shared tool-result truth | VERIFIED by consumer audit | KAS consumers preserve `status=truncated`, `total`, `dropped`, `total_lines`, and `match_limit_reached`; no Windows-lane consumer was found to turn a partial result into a complete absence claim |
| Cumulative local regression | VERIFIED locally | Exact full pytest after the canonical-routing, response-constraint, transport-portable exact-word repair, worker-probe, terminal-trace, final-retry linkage, generated-hash idempotence, and finite-score repairs: `9,319 passed, 108 skipped, 16 xfailed, 14 xpassed, 179 warnings, 62 subtests`; the focused response/runtime pack passed `162` tests with one expected xfail and full Ruff passes. The exact concurrent reproducer moved from `3/120` failures before the generated-hash fix to `0/120` after it. The current default four-worker run exited cleanly after the runner was made host-independent by skipping the real-hardware Torch GPU fallback inside parallel workers; mocked GPU-path coverage remains active and no assertion was relaxed |
| Installed Windows bundle and mandatory live local-model/blind evaluation | BLOCKED on rebuild of the corrected stacked head | The `5030e0b` installed stack proved the local model and provider-manifest path but exposed the canonical-question routing/streaming defect above. That artifact does not contain this repair and is not accepted. The shipped Windows artifact remains `e35b634`; no release claim is made until #72 is restacked, rebuilt, and the blind seed passes on the exact resulting source |
