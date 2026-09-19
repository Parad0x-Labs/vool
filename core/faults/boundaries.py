"""The user-facing failure-boundary coverage registry: every boundary, its owning seam, its code
space, and where the user reads the outcome -- as typed, checkable data.

``core.faults.catalog`` is the vocabulary for the faults the CORE turn maps (``vool.fault.v1``).
But a user's recovery path crosses boundaries that predate or sit beside that vocabulary and
carry their OWN stable, closed code sets: the updater's ``UpdateFault``, attachment refusals'
``AttachmentRefused.code``, dictation's ``DictationUnavailable`` codes, contacts'
``ContactsError.reason``, plugin storage states, workspace root reasons. Those code spaces are
well-designed and stable -- migrating them into ``vool.fault.v1`` would be a compatibility break
with no user gain. What was missing is ONE place that answers "is any user-facing failure
boundary silently unclassified?" -- which is what this module is.

Every entry names: the boundary, its owning module(s), the code space (registry codes or the
boundary's own), where the failure is BORN, what surface the user reads, and the recovery action
the boundary itself offers. A boundary that had no typed classification would have to declare
``codespace=None`` here -- visibly, in review, instead of by omission. None currently does.

The generated error reference (``tools/generate_error_book.py``) renders this registry as the
coverage matrix; ``tests/test_fault_boundary_coverage.py`` validates its structure and pins the
boundary list against the inventory the delivery goal named (startup/update, workspace,
filesystem/tool execution, permissions, provider credentials/network/limits, model availability,
price/budget approvals, wallet/payment, contacts, plugin/skill loading, attachments, storage,
voice, reporting).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundaryCodeSpace:
    """One boundary's classification space: registry codes or its own closed set."""

    #: "vool.fault.v1" when the codes come from the fault catalog; otherwise the boundary's own
    #: closed vocabulary, named by its owning type (e.g. "core.updater.status.UpdateFault").
    kind: str
    #: The codes themselves (catalog codes, or the boundary's own stable values).
    codes: tuple[str, ...] = ()
    #: Where the codes are DECLARED (module path), for the debugger's first step.
    declared_in: str = ""


@dataclass(frozen=True)
class FailureBoundary:
    """One user-facing failure boundary and its complete error-to-recovery path."""

    name: str
    owners: tuple[str, ...]
    codespace: BoundaryCodeSpace
    #: Where the failure is classified (the boundary that owns the mapping).
    mapped_at: str
    #: The surface the user reads: file:line of the message/hint that renders it.
    user_surface: str
    #: The recovery action the boundary itself offers (exact capability, not prose hope).
    recovery: str
    #: Whether the runtime records a core.faults record for this boundary (honest flag, not a
    #: quality judgement: a boundary with its own closed code space may record nothing there).
    files_fault_records: bool = False
    notes: str = ""


BOUNDARIES: tuple[FailureBoundary, ...] = (
    FailureBoundary(
        name="startup",
        owners=("core/runtime_bootstrap.py", "core/runtime_backbone.py", "apps/vool_api_server.py", "apps/vool_cli.py"),
        codespace=BoundaryCodeSpace(kind="process exit + boot log", codes=("no supported backend found", "Database healthcheck failed"), declared_in="apps/vool_cli.py"),
        mapped_at="build_runtime_backbone / bootstrap_storage_environment",
        user_surface="apps/vool_cli.py:85 ('Vool could not start: {exc}')",
        recovery="Install a supported runtime (mlx/torch/onnxruntime) or repair the data store; the CLI names which.",
        notes="Boot is fail-loud by design: a broken runtime must not serve half-alive.",
    ),
    FailureBoundary(
        name="update",
        owners=("core/updater/status.py", "core/updater/service.py", "core/updater/transaction.py"),
        codespace=BoundaryCodeSpace(
            kind="core.updater.status.UpdateFault",
            codes=("download_failed", "download_blocked", "insufficient_disk_space", "verification_failed",
                   "update_not_applicable", "install_failed", "migration_failed", "health_check_failed",
                   "destructive_work_active", "stale_update_cleaned_up", "unexpected_failure"),
            declared_in="core/updater/status.py",
        ),
        mapped_at="UpdatePhase/UpdateFault status store (update_v2/status.json under the runtime data dir)",
        user_surface="core/updater/status.py:42-186 plain_message() + per-fault recovery()",
        recovery="Per-fault recovery sentences; every failure leaves the app as it was or rolls back and restarts.",
        notes="Updates never take the app down; installation is gesture-gated with auto-rollback.",
    ),
    FailureBoundary(
        name="project_workspace",
        owners=("core/context_namespace.py", "core/web/api/service.py", "core/folder_overview.py"),
        codespace=BoundaryCodeSpace(
            kind="workspace root_reason",
            codes=("project", "missing_chat", "deleted_chat", "unbound", "project_missing", "unreadable"),
            declared_in="core/context_namespace.py",
        ),
        mapped_at="authoritative_chat_workspace (HTTP 409 + reason)",
        user_surface="core/web/api/service.py:4399-4407; chat toast core/vool_chat_page.py:11001; core/folder_overview.py:81-92",
        recovery="Bind the chat to a project folder (Projects -> New project / pick existing) or name an explicit path; pending requests wait, nothing is cancelled.",
    ),
    FailureBoundary(
        name="filesystem_tool_execution",
        owners=("core/runtime_execution_tools.py", "core/confinement paths"),
        codespace=BoundaryCodeSpace(kind="vool.fault.v1", codes=("permission_denied", "confinement_refusal", "tool_unavailable", "timeout"), declared_in="core/faults/catalog.py"),
        mapped_at="core.faults.mapping.map_exception at the executing seam; records via record_fault",
        user_surface="fault user_message on the execution receipt; refusal text on the turn",
        recovery="Permission: request the action explicitly so it is inside an authorized scope. Confinement: same. Tool unavailable: the message names the missing prerequisite.",
        files_fault_records=True,
    ),
    FailureBoundary(
        name="permissions",
        owners=("core/mode_permission_policy.py", "core/attempt_approval.py", "core/appeal_queue.py"),
        codespace=BoundaryCodeSpace(kind="vool.fault.v1", codes=("permission_denied",), declared_in="core/faults/catalog.py"),
        mapped_at="the permission controller's decision (scope-named, never prose)",
        user_surface="the approval prompt, or the typed refusal on the turn",
        recovery="Approve the exact action, or change the governing policy; the refusal names the governing scope.",
        files_fault_records=True,
    ),
    FailureBoundary(
        name="provider_credentials",
        owners=("core/credential_store.py", "core/agent_runtime/memory_runtime.py"),
        codespace=BoundaryCodeSpace(kind="vool.fault.v1 + attempt evidence", codes=("credential_failure", "provider_credential_unavailable (attempt error class)"), declared_in="core/faults/catalog.py + provider attempt records"),
        mapped_at="per-attempt evidence; local refusal vs wire attempt distinguished before wording",
        user_surface="core/agent_runtime/memory_runtime.py:730 _provider_auth_failure_hint ('stopped before sending... nothing was charged' vs the both-facts wording)",
        recovery="Restore the key under Settings -> API Keys, then send again; the message never words a local refusal as a provider rejection.",
        files_fault_records=True,
    ),
    FailureBoundary(
        name="provider_network_limits",
        owners=("core/normalized_provider_result.py", "core/turn_model_call_ledger.py"),
        codespace=BoundaryCodeSpace(kind="vool.fault.v1 + ProviderErrorClass", codes=("provider_unavailable", "provider_exhausted", "timeout", "PROVIDER_RATE_LIMIT"), declared_in="core/faults/catalog.py + core/normalized_provider_result.py"),
        mapped_at="fault_code_for_provider_error_class (typed error class, never prose)",
        user_surface="degraded-response hints; 429 wording quotes the provider's own Retry-After when present",
        recovery="Retry later for limits/exhaustion; the wording never pretends a short wait fixes a quota.",
        files_fault_records=True,
    ),
    FailureBoundary(
        name="model_availability",
        owners=("core/memory_first_router.py"),
        codespace=BoundaryCodeSpace(kind="routing block reasons", codes=("no_ranked_provider", "emergency_lane_insufficient", "selected_provider_excluded_before_invocation", "selected_model_unavailable"), declared_in="core/memory_first_router.py"),
        mapped_at="explicit-pin terminal _selected_model_blocked_decision (no silent substitution)",
        user_surface="'Selected model X could not run (reason); no other model was substituted.'",
        recovery="Pick an available model or repair the named gate; a pinned model is never silently answered by another.",
    ),
    FailureBoundary(
        name="price_budget_approvals",
        owners=("core/paid_call_reservation.py", "core/model_pricing.py", "core/model_spend_ledger.py", "core/model_price_acceptance.py"),
        codespace=BoundaryCodeSpace(
            kind="reservation denial codes",
            codes=("usepod_route_not_approved", "usepod_route_state_unavailable", "per_call_spend_cap_exceeded",
                   "per_task_spend_cap_exceeded", "daily_spend_cap_exceeded", "monthly_spend_cap_exceeded",
                   "daily_call_cap_exceeded", "cloud_policy_unreadable", "not_owner_local", "reservation_unavailable"),
            declared_in="core/paid_call_reservation.py + core/model_spend_ledger.py",
        ),
        mapped_at="denial['reason'] set by the refusing authority, carried verbatim to the blocked-pin terminal",
        user_surface="_selected_model_block_cause: 'running it would exceed your spend cap' -- pre-send wording, never claims the provider was contacted",
        recovery="Raise the cap/ceiling deliberately in Settings, approve the model's price, or pick a model inside the cap.",
    ),
    FailureBoundary(
        name="wallet_payment",
        owners=("core/wallet/*",),
        codespace=BoundaryCodeSpace(kind="vool.fault.v1 wallet family", codes=("wallet_disabled", "wallet_quote_unavailable", "wallet_quote_expired", "wallet_quote_mismatch", "wallet_insufficient_funds", "wallet_limit_exceeded", "wallet_duplicate_payment", "wallet_broadcast_failed", "wallet_approval_rejected", "wallet_x402_cap_exceeded", "wallet_signature_invalid", "wallet_chain_identity_mismatch"), declared_in="core/faults/catalog.py"),
        mapped_at="each wallet boundary maps its own code before any signature or send",
        user_surface="fault user_message on the proposal/turn; every refusal states exactly what did not happen",
        recovery="Per-code operator action; unknown outcomes (broadcast_failed, paid-result-unknown) say how to CHECK state and never invite a blind duplicate retry.",
        files_fault_records=True,
    ),
    FailureBoundary(
        name="contacts",
        owners=("core/contacts/store.py", "core/contacts/authority.py", "core/web/api/contacts_api.py"),
        codespace=BoundaryCodeSpace(
            kind="ContactsError.reason",
            codes=("name_required", "contact_not_found", "contact_deleted", "change_stale", "revision_mismatch",
                   "too_many_pending", "operation_expired", "operation_cancelled", "authorization_required", "batch_too_large"),
            declared_in="core/contacts/store.py + core/contacts/authority.py",
        ),
        mapped_at="ContactsError(reason, message, status) at the store/authority seam",
        user_surface="exc.as_dict() with HTTP status; every sentence states 'Nothing was saved/changed' where true",
        recovery="Re-review the changed entry, confirm with PIN in review, or re-save before the confirmation expires.",
    ),
    FailureBoundary(
        name="plugin_skill_loading",
        owners=("core/plugin_catalog.py", "core/plugin_tools.py", "core/native_skill_library.py"),
        codespace=BoundaryCodeSpace(
            kind="plugin storage states + typed contract violations",
            codes=("accessible", "missing", "denied", "stalled", "failed", "contract invalid", "unmet prerequisites", "unavailable capabilities", "disabled by operator", "duplicate plugin identity"),
            declared_in="core/plugin_catalog.py + core/native_skill_library.py",
        ),
        mapped_at="bounded storage probe + typed contract validation law (fail-soft, never a boot hang)",
        user_surface="catalog reason strings (core/plugin_catalog.py _reason_for) + the Plugins/Skills panel with Rescan",
        recovery="Grant folder access / answer the consent dialog / press Rescan; every state says 'nothing was disabled or changed'.",
    ),
    FailureBoundary(
        name="attachments",
        owners=("core/chat_attachments.py",),
        codespace=BoundaryCodeSpace(kind="AttachmentRefused.code", codes=("too_large", "too_many", "unsupported_type", "not_text", "image_unparseable", "content_mismatch"), declared_in="core/chat_attachments.py"),
        mapped_at="AttachmentRefused(code, message, http_status) at upload/queue/send seams",
        user_surface="the attachment chip keeps the server message; limits are shown up front",
        recovery="Remove or shrink/retry per the chip's reason; failed chips block send so a message never silently drops a file.",
    ),
    FailureBoundary(
        name="storage",
        owners=("storage/db.py", "storage/migrations.py", "core/error_surface.py"),
        codespace=BoundaryCodeSpace(kind="StoreVersionError.code", codes=("VOOL_E_STORE_VERSION", "VOOL_E_STORE_TOO_NEW"), declared_in="storage/db.py"),
        mapped_at="version-stamp check at open; boot healthcheck",
        user_surface="startup: 'Vool could not start: {exc}'; in-turn: core/error_surface.py generic redacted surface",
        recovery="Downgrade refusal is deliberate (open only with the binary that upgraded the store); corruption needs the backup.",
    ),
    FailureBoundary(
        name="voice",
        owners=("core/dictation.py", "core/artifact_readers/speech_tool.py", "core/vool_chat_page.py"),
        codespace=BoundaryCodeSpace(
            kind="DictationUnavailable.code",
            codes=("empty_recording", "recording_too_large", "speech_toolchain_unavailable", "speech_recognizer_unauthorized", "speech_recognizer_unavailable", "speech_recognizer_on_device_unavailable", "decoder_failed"),
            declared_in="core/dictation.py + core/artifact_readers/speech_tool.py",
        ),
        mapped_at="DictationUnavailable(code, message, remediation); local-only by design",
        user_surface="pre-record availability probe shows message + remediation; browser fallbacks say 'Type your message instead.'",
        recovery="Every typed unavailability carries a remediation (install CLT, grant Speech Recognition, add a dictation language).",
    ),
    FailureBoundary(
        name="reporting",
        owners=("core/bug_report/*", "core/faults/bug_export.py"),
        codespace=BoundaryCodeSpace(kind="submission failure codes", codes=("draft_not_found", "approval_required", "consent_mismatch", "outbound_scan_refused"), declared_in="core/bug_report/service.py"),
        mapped_at="approval-bound submission state machine (payload sha256 binding)",
        user_surface="exact-bytes preview dialog; redaction summary chip; 'Nothing is sent yet'",
        recovery="Re-approve the new bytes after any edit; local export needs no consent; duplicates return the prior issue URL.",
        notes="Redaction is deterministic and auditable; fault evidence exports typed codes only (no context, no cause chains).",
    ),
)


def boundary_names() -> tuple[str, ...]:
    return tuple(b.name for b in BOUNDARIES)


__all__ = ["BOUNDARIES", "BoundaryCodeSpace", "FailureBoundary", "boundary_names"]
