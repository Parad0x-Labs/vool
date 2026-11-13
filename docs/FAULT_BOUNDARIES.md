# Fault owners and boundary coverage

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

The developer view of the fault catalog (`core/faults/catalog.py`): which module owns
each code, and every user-facing failure boundary with the code space that classifies
it. Readers want [the Error Book](ERROR_BOOK.md); this file stays off the docs site.

- **Schema:** `vool.fault.v1`
- **Catalog version:** `1`
- **Catalog digest:** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Fault count:** 59

## Code owners

| Code | Category | Retry | Security | Owning module |
|---|---|---|---|---|
| `confinement_refusal` | security | never | yes | `core.agent_runtime.builder.app_builder` |
| `credential_failure` | security | never | yes | `core.credential_store` |
| `wallet_backup_unavailable` | security | never | yes | `core.wallet.pilot_custody` |
| `wallet_caller_refused` | security | retry_after_change | yes | `core.wallet.caller_binding` |
| `wallet_card_data_refused` | security | never | yes | `core.wallet.cards` |
| `wallet_chain_identity_mismatch` | security | never | yes | `core.wallet.chains` |
| `wallet_environment_inactive` | security | retry_after_change | yes | `core.wallet.environment` |
| `wallet_export_pin_not_accepted` | security | retry_after_change | yes | `core.wallet.custody` |
| `wallet_export_refused` | security | never | yes | `core.wallet.authority` |
| `wallet_export_unavailable` | security | never | yes | `core.wallet.custody` |
| `wallet_legacy_surface_retired` | security | never | yes | `core.wallet.authority` |
| `wallet_network_disabled` | security | never | yes | `core.wallet.custody` |
| `wallet_outbound_refused` | security | never | yes | `core.wallet.outbound` |
| `wallet_quote_mismatch` | security | never | yes | `core.wallet.quotes` |
| `wallet_recovery_refused` | security | retry_after_change | yes | `core.wallet.pilot_custody` |
| `wallet_signature_invalid` | security | never | yes | `core.wallet.signers` |
| `wallet_unlock_throttled` | security | retry_later | yes | `core.wallet.pilot_custody` |
| `evidence_corruption` | integrity | never | yes | `core.execution_truth` |
| `integrity_verification_failure` | integrity | never | yes | `core.contribution_proof` |
| `unsupported_claim` | integrity | never | no | `core.grounding_publication` |
| `wallet_duplicate_payment` | integrity | never | yes | `core.wallet.proposals` |
| `evm_pocket_custody_unavailable` | policy | never | no | `core.wallet.custody` |
| `permission_denied` | policy | never | yes | `core.runtime_execution_tools` |
| `wallet_acknowledgement_required` | policy | retry_after_change | no | `core.wallet.meaning` |
| `wallet_amount_invalid` | policy | retry_after_change | no | `core.wallet.amounts` |
| `wallet_approval_rejected` | policy | never | yes | `core.wallet.lifecycle` |
| `wallet_confirmation_required` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_credential_mismatch` | policy | retry_after_change | no | `core.wallet.pilot_custody` |
| `wallet_dependency_unavailable` | policy | retry_after_change | no | `core.wallet.evm` |
| `wallet_device_auth_denied` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_disabled` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_insufficient_funds` | policy | retry_after_change | no | `core.wallet.quotes` |
| `wallet_limit_exceeded` | policy | never | yes | `core.wallet.limits` |
| `wallet_not_found` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_password_invalid` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_paykit_unavailable` | policy | retry_after_change | no | `core.wallet.paykit_x402` |
| `wallet_payment_channel_off` | policy | retry_after_change | no | `core.wallet.channels` |
| `wallet_pin_invalid` | policy | retry_after_change | no | `core.wallet.custody` |
| `wallet_quote_expired` | policy | retry_after_change | no | `core.wallet.quotes` |
| `wallet_recipient_refused` | policy | retry_after_change | no | `core.wallet.quotes` |
| `wallet_request_ambiguous` | policy | retry_after_change | no | `core.wallet.transfers` |
| `wallet_send_needs_vool_wallet` | policy | retry_after_change | no | `core.wallet.lifecycle` |
| `wallet_setup_state_invalid` | policy | retry_after_change | no | `core.wallet.pilot_custody` |
| `wallet_signing_unavailable` | policy | retry_after_change | no | `core.wallet.signers` |
| `wallet_storage_class_refused` | policy | retry_after_change | no | `core.wallet.pilot_custody` |
| `wallet_x402_cap_exceeded` | policy | never | yes | `core.wallet.x402` |
| `wallet_x402_sdk_unavailable` | policy | retry_after_change | no | `core.wallet.x402_sdk` |
| `x402_scheme_unavailable` | policy | retry_after_change | no | `core.wallet.x402` |
| `provider_exhausted` | availability | retry_later | no | `core.turn_model_call_ledger` |
| `provider_unavailable` | availability | retry_later | no | `core.turn_model_call_ledger` |
| `timeout` | availability | retry_now | no | `core.remote_fetch_policy` |
| `tool_unavailable` | availability | retry_after_change | no | `core.runtime_execution_tools` |
| `wallet_broadcast_failed` | availability | retry_later | no | `core.wallet.lifecycle` |
| `wallet_device_auth_unavailable` | availability | retry_after_change | no | `core.wallet.custody` |
| `wallet_quote_unavailable` | availability | retry_later | no | `core.wallet.quotes` |
| `wallet_security_key_needed` | availability | retry_after_change | no | `core.wallet.pilot_custody` |
| `wallet_simulation_failed` | availability | retry_later | no | `core.wallet.lifecycle` |
| `cancelled` | cancellation | never | no | `core.remote_fetch_policy` |
| `unknown` | internal | never | no | `core.faults.mapping` |

## Boundary coverage

Every user-facing failure boundary and the code space that classifies it. A boundary
keeps its OWN closed vocabulary when it predates or sits beside `vool.fault.v1`; what
this matrix promises is that none is silently unclassified -- a boundary with no code
space would have to show that here.

| Boundary | Classified by | Codes | Where it is mapped | What the user reads | Recovery | Fault records |
|---|---|---|---|---|---|---|
| startup | process exit + boot log | `no supported backend found`, `Database healthcheck failed` | build_runtime_backbone / bootstrap_storage_environment | apps/vool_cli.py:85 ('Vool could not start: {exc}') | Install a supported runtime (mlx/torch/onnxruntime) or repair the data store; the CLI names which. | own code space |
| update | core.updater.status.UpdateFault | `download_failed`, `download_blocked`, `insufficient_disk_space`, `verification_failed`, `update_not_applicable`, `install_failed`, `migration_failed`, `health_check_failed`, `destructive_work_active`, `stale_update_cleaned_up`, `unexpected_failure` | UpdatePhase/UpdateFault status store (update_v2/status.json under the runtime data dir) | core/updater/status.py:42-186 plain_message() + per-fault recovery() | Per-fault recovery sentences; every failure leaves the app as it was or rolls back and restarts. | own code space |
| project_workspace | workspace root_reason | `project`, `missing_chat`, `deleted_chat`, `unbound`, `project_missing`, `unreadable` | authoritative_chat_workspace (HTTP 409 + reason) | core/web/api/service.py:4399-4407; chat toast core/vool_chat_page.py:11001; core/folder_overview.py:81-92 | Bind the chat to a project folder (Projects -> New project / pick existing) or name an explicit path; pending requests wait, nothing is cancelled. | own code space |
| filesystem_tool_execution | vool.fault.v1 | `permission_denied`, `confinement_refusal`, `tool_unavailable`, `timeout` | core.faults.mapping.map_exception at the executing seam; records via record_fault | fault user_message on the execution receipt; refusal text on the turn | Permission: request the action explicitly so it is inside an authorized scope. Confinement: same. Tool unavailable: the message names the missing prerequisite. | yes |
| permissions | vool.fault.v1 | `permission_denied` | the permission controller's decision (scope-named, never prose) | the approval prompt, or the typed refusal on the turn | Approve the exact action, or change the governing policy; the refusal names the governing scope. | yes |
| provider_credentials | vool.fault.v1 + attempt evidence | `credential_failure`, `provider_credential_unavailable` | per-attempt evidence; local refusal vs wire attempt distinguished before wording | core/agent_runtime/memory_runtime.py:730 _provider_auth_failure_hint ('stopped before sending... nothing was charged' vs the both-facts wording) | Restore the key under Settings -> API Keys, then send again; the message never words a local refusal as a provider rejection. | yes |
| provider_network_limits | vool.fault.v1 + ProviderErrorClass | `provider_unavailable`, `provider_exhausted`, `timeout`, `PROVIDER_RATE_LIMIT` | fault_code_for_provider_error_class (typed error class, never prose) | degraded-response hints; 429 wording quotes the provider's own Retry-After when present | Retry later for limits/exhaustion; the wording never pretends a short wait fixes a quota. | yes |
| model_availability | routing block reasons | `no_ranked_provider`, `emergency_lane_insufficient`, `selected_provider_excluded_before_invocation`, `selected_model_unavailable`, `explicit_heavy_lane_unavailable` | explicit-pin terminal _selected_model_blocked_decision (no silent substitution) | 'Selected model X could not run (reason); no other model was substituted.' | Pick an available model or repair the named gate; a pinned model is never silently answered by another. | own code space |
| price_budget_approvals | reservation denial codes | `payment_lane_removed`, `per_call_spend_cap_exceeded`, `per_task_spend_cap_exceeded`, `daily_spend_cap_exceeded`, `monthly_spend_cap_exceeded`, `device_monthly_spend_cap_exceeded`, `daily_call_cap_exceeded`, `cloud_policy_unreadable`, `not_owner_local`, `reservation_unavailable`, `spend_cap_exceeded` | denial['reason'] set by the refusing authority, carried verbatim to the blocked-pin terminal | _selected_model_block_cause: 'running it would exceed your spend cap' -- pre-send wording, never claims the provider was contacted | Raise the cap deliberately in Settings or pick a model inside the cap. A pick saved for UsePod (removed in 0.8) is refused before anything is sent; pick another model. | own code space |
| wallet_payment | vool.fault.v1 wallet family | `wallet_disabled`, `wallet_quote_unavailable`, `wallet_quote_expired`, `wallet_quote_mismatch`, `wallet_insufficient_funds`, `wallet_limit_exceeded`, `wallet_duplicate_payment`, `wallet_broadcast_failed`, `wallet_approval_rejected`, `wallet_x402_cap_exceeded`, `wallet_signature_invalid`, `wallet_chain_identity_mismatch`, `wallet_password_invalid`, `wallet_device_auth_unavailable`, `wallet_device_auth_denied`, `wallet_security_key_needed`, `wallet_export_unavailable`, `wallet_export_pin_not_accepted`, `wallet_acknowledgement_required` | each wallet boundary maps its own code before any signature or send | fault user_message on the proposal/turn; every refusal states exactly what did not happen | Per-code operator action; unknown outcomes (broadcast_failed, paid-result-unknown) say how to CHECK state and never invite a blind duplicate retry. | yes |
| contacts | ContactsError.reason | `name_required`, `contact_not_found`, `contact_deleted`, `change_stale`, `revision_mismatch`, `too_many_pending`, `operation_expired`, `operation_cancelled`, `authorization_required`, `batch_too_large`, `authentication_required`, `authorization_expired`, `authorization_not_for_this_change`, `authorization_used`, `change_invalid`, `change_unknown`, `contact_kind_unknown`, `credential_changed`, `endpoint_not_found`, `one_change_per_contact`, `operation_changed`, `operation_committed`, `operation_needs_no_confirmation`, `operation_not_found`, `operation_not_pending`, `operation_stale`, `owner_confirmation_required`, `protected_review_required`, `revision_invalid`, `source_unknown`, `suggestion_decided`, `suggestion_not_found`, `too_many_aliases`, `too_many_endpoints`, `verification_unknown` | ContactsError(reason, message, status) at the store/authority seam | exc.as_dict() with HTTP status; every sentence states 'Nothing was saved/changed' where true | Re-review the changed entry, confirm with PIN in review, or re-save before the confirmation expires. | own code space |
| plugin_skill_loading | plugin storage states + typed contract violations | `accessible`, `missing`, `denied`, `stalled`, `failed`, `contract invalid`, `unmet prerequisites`, `unavailable capabilities`, `disabled by operator`, `duplicate plugin identity` | bounded storage probe + typed contract validation law (fail-soft, never a boot hang) | catalog reason strings (core/plugin_catalog.py _reason_for) + the Plugins/Skills panel with Rescan | Grant folder access / answer the consent dialog / press Rescan; every state says 'nothing was disabled or changed'. | own code space |
| attachments | AttachmentRefused.code | `too_large`, `too_many`, `unsupported_type`, `not_text`, `image_unparseable`, `content_mismatch`, `symlink_refused`, `invalid_id`, `missing_session`, `name_rejected`, `empty_file`, `already_bound`, `duplicate_id`, `executable_masquerade`, `invalid_turn`, `not_owned`, `turn_bytes_exceeded`, `declared_type_mismatch`, `secret_detected` | AttachmentRefused(code, message, http_status) at upload/queue/send seams | the attachment chip keeps the server message; limits are shown up front | Remove or shrink/retry per the chip's reason; failed chips block send so a message never silently drops a file. | own code space |
| storage | StoreVersionError.code | `VOOL_E_STORE_VERSION`, `VOOL_E_STORE_TOO_NEW` | version-stamp check at open; boot healthcheck | startup: 'Vool could not start: {exc}'; in-turn: core/error_surface.py generic redacted surface | Downgrade refusal is deliberate (open only with the binary that upgraded the store); corruption needs the backup. | own code space |
| voice | DictationUnavailable.code | `empty_recording`, `recording_too_large`, `speech_toolchain_unavailable`, `speech_recognizer_unauthorized`, `speech_recognizer_unavailable`, `speech_recognizer_on_device_unavailable`, `decoder_failed`, `speech_probe_failed`, `decoder_bad_output`, `speech_build_failed` | DictationUnavailable(code, message, remediation); local-only by design | pre-record availability probe shows message + remediation; browser fallbacks say 'Type your message instead.' | Every typed unavailability carries a remediation (install CLT, grant Speech Recognition, add a dictation language). | own code space |
| reporting | submission failure codes | `draft_not_found`, `approval_required`, `consent_mismatch`, `outbound_scan_refused` | approval-bound submission state machine (payload sha256 binding) | exact-bytes preview dialog; redaction summary chip; 'Nothing is sent yet' | Re-approve the new bytes after any edit; local export needs no consent; duplicates return the prior issue URL. | own code space |

