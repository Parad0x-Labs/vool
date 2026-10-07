# Evidence registry

Each public claim VOOL makes, and the code, test or command that backs it. The claims come from
the [README](../../README.md) and the [concepts pages](../concepts/). How to set up a checkout
and run these is in [REVIEW.md](../../REVIEW.md).

**Platform** says where the evidence actually runs. "Any" means the test has no platform skip;
a named platform means the test skips everywhere else, and a skip is not a pass.

Last checked against `main` at `9adff83` (2026-10-06): the test command in REVIEW.md gave 162 passed and 20 skipped on Linux,
the skips being the macOS-only confinement tests marked below.

## Permissions and operating modes

| Claim | Backed by | Platform |
| --- | --- | --- |
| Every tool call gets a permission decision before it runs; there is no path that skips it. | `core/tool_intent_executor.py` (`execute_tool_intent` calls `decide_tool_call` unconditionally) · `tests/test_permission_authority_is_unconditional.py` | Any |
| A request with no mode, an invalid mode, or a forged "bypass" claim is treated as Manual: changes wait for approval and nothing is written. | `core/mode_permission_policy.py` (`resolve_effective_mode`) · `tests/test_permission_authority_is_unconditional.py::test_spoofed_bypass_in_the_context_cannot_mint_authority`, `::test_invalid_mode_string_falls_to_manual_not_to_auto` · demo B, attempt 5 | Any |
| Manual mode asks before every change; reads still work. | `MODE_PERMISSION_MATRIX` in `core/mode_permission_policy.py` · `tests/test_permission_authority_is_unconditional.py::test_manual_and_review_require_approval_for_mutations`, `::test_modeless_reads_still_work_so_the_default_is_manual_not_a_blanket_block` | Any |
| Auto mode runs everyday changes (create and edit files, run commands) and still asks before deleting, overwriting, installing, spending, deploying, sending messages, and changing settings or git history. | `MODE_PERMISSION_MATRIX` · `tests/test_mode_permission_policy.py::test_manual_review_and_auto_have_distinct_real_effects` · `tests/test_permission_authority_is_unconditional.py::test_auto_mode_contract_is_preserved` · demo A (edit runs), demo B attempt 3 (overwrite held) | Any |
| Plan mode is read-only: it blocks writes and side-effecting commands before they are dispatched. | `tests/test_mode_permission_policy.py::test_matrix_is_total_and_plan_is_strictly_read_only` · `tests/test_permission_authority_is_unconditional.py::test_plan_mode_denies_mutations_through_the_executor` · demo B attempts 1 and 2 | Any |
| An approval covers one exact call: it cannot be reused for a different file, task or mode. | `tests/test_permission_authority_is_unconditional.py::test_an_approval_does_not_carry_to_a_different_call` · `tests/test_mode_permission_policy.py::test_exact_edit_approval_is_one_time_and_cannot_cross_tasks_or_mode_revisions` | Any |
| Bypass mode needs a confirmed, scoped, expiring grant from the server; it cannot be set by the request itself. | `tests/test_mode_permission_policy.py::test_bypass_requires_confirmation_is_scoped_expires_revokes_and_keeps_hard_boundaries`, `::test_mode_endpoint_is_loopback_only_and_bypass_cannot_be_set_without_grant` | Any |

## Workspace boundary

| Claim | Backed by | Platform |
| --- | --- | --- |
| File tools operate only inside the workspace: `../` paths, absolute paths and symlinks that lead out are refused. | `tests/test_scope_authority.py::test_workspace_confinement_blocks_traversal_and_absolute_escape`, `::test_workspace_confinement_blocks_symlink_escape` · demo B attempt 4 | Any |
| A refused operation leaves no trace on disk, inside or outside the workspace. | demo B (SHA-256 fingerprint before and after every attempt) · `tests/test_permission_authority_is_unconditional.py` (each test asserts the file does not exist) | Any |

## Command sandbox

| Claim | Backed by | Platform |
| --- | --- | --- |
| Shell commands and test runs only run under OS-level confinement; with no usable backend they refuse instead of running unconfined. | `sandbox/` and `core/runtime_execution_tools.py` (`sandbox_unavailable`) · `tests/test_sandbox_command_failure_is_not_success.py::test_an_unusable_backend_is_not_reported_as_a_failed_command` · demo A step 4 without `bwrap` | Any |
| A failing command is reported as a failure, never as success. | `tests/test_sandbox_command_failure_is_not_success.py` | Any |
| Confined jobs cannot write outside the workspace, read files in the home folder, or reach the network without a grant. | `tests/test_job_runner_real_confinement.py` | macOS only |

## Records and receipts

| Claim | Backed by | Platform |
| --- | --- | --- |
| Tool activity records what was requested, what ran and what came back. | `tests/test_a_receipt_names_a_tool_that_ran.py` · the `/trace` page of a running service · each demo prints the decision and result per call | Any |
| Each turn keeps its own receipts; turns running at the same time never see each other's, and a turn ending does not drain another. | `core/effect_gateway.py` · `tests/test_one_turn_owns_its_effect_receipts.py` · demo C | Any |
| Signed honesty receipts can be verified offline, and tampering is detected. | `core/honesty_receipt.py` · `python -m core.honesty_receipt demo` · `tests/test_honesty_receipt.py`, `tests/test_honesty_receipt_cli.py` | Any |
| Limit, stated by the tool itself: deleting receipts from the end of a chain is not detected, so completeness is not proven. A valid signature does not mean an answer is correct. | output of `python -m core.honesty_receipt demo` · README "Check the receipts" | Any |

## Local-first and external tools

| Claim | Backed by | Platform |
| --- | --- | --- |
| The local service starts and answers without any account or cloud key. | `python -m apps.vool_api_server`, then `/healthz` and `/api/runtime/capabilities` (checked on Linux with no model and no key) | Linux checked |
| Local Only mode means no public network access through any door; loopback and your own LAN endpoints stay reachable. | `core/remote_fetch_policy.py` · `tests/test_local_only_zero_public_egress.py` | Any |
| Install profiles choose the local model setup and can be changed later. | `python -m apps.vool_cli install-profile` · `tests/test_install_profile_cli.py` | Any |
| An MCP server gets only the environment variables it is configured with or explicitly allowed, never your ambient secrets. | `core/mcp_client.py`, `core/plugin_executor.py` (`build_child_env`) · `tests/test_mcp_env_is_an_allowlist.py` | Any |

## Updates

| Claim | Backed by | Platform |
| --- | --- | --- |
| With no trusted publisher key configured, the updater reports updates as unavailable instead of fetching anything. | `tests/updater/test_boot_api_ui.py::TestHonestDisable::test_no_trusted_key_means_unavailable_not_silent` · [release status](../trust/release-status.md) | Any |
| An update artifact with an invalid signature is refused. | `tests/updater/test_download_resume.py::TestVerificationFailures::test_invalid_artifact_signature_refused`; end-to-end on macOS in `tests/updater/test_e2e_sandbox.py::TestEndToEnd::test_invalid_artifact_signature_changes_nothing` | Any / macOS |

## Not claimed here

These are outside what a reviewer can check from tests, so the registry makes no claim on them:
answer quality, memory recall quality, speed, and cost. They depend on the model and the task.
Release artifacts, signing and notarization are tracked on the
[release status](../trust/release-status.md) page.

## Keeping this current

When a claim changes in the README or the concepts pages, update its row here in the same
change, and re-run the listed tests. A row whose test was removed or now skips everywhere is
a claim without evidence and must be removed or fixed.
