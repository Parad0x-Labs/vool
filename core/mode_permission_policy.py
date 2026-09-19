"""Controller-owned VOOL operating modes and tool permission decisions.

This module is deliberately independent of prompt text.  The active mode is trusted
application state, while every model-selected tool call is reduced to a stable set of
permission actions and checked here immediately before dispatch.

The lower filesystem, sandbox, operating-system, spend, and provider gates remain in
force.  A mode can only remove permission or require an approval; it can never widen
one of those harder boundaries.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import hmac
import json
import os
import secrets
import shlex
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.tool_argument_aliases import (
    bind_known_argument_aliases,
    input_schema_for_intent,
    side_effect_class_for_intent,
)


class OperatingMode(str, Enum):
    MANUAL = "manual"
    REVIEW_EDITS = "review_edits"
    PLAN = "plan"
    AUTO = "auto"
    BYPASS_PERMISSIONS = "bypass_permissions"


MODE_LABELS: dict[OperatingMode, str] = {
    OperatingMode.MANUAL: "Manual",
    OperatingMode.REVIEW_EDITS: "Review edits",
    OperatingMode.PLAN: "Plan",
    OperatingMode.AUTO: "Auto",
    OperatingMode.BYPASS_PERMISSIONS: "Bypass permissions",
}

# Compatibility only. These names are no longer rendered in the product UI.
_LEGACY_MODE_ALIASES = {
    "ask": OperatingMode.MANUAL,
    "build": OperatingMode.AUTO,
    "bypass": OperatingMode.BYPASS_PERMISSIONS,
}


class PermissionAction(str, Enum):
    READ_FILES = "read_files"
    CREATE_FILES = "create_files"
    MODIFY_FILES = "modify_files"
    OVERWRITE_EXISTING_FILES = "overwrite_existing_files"
    DELETE_FILES = "delete_files"
    LIST_DIRECTORIES = "list_directories"
    RUN_SAFE_COMMANDS = "run_safe_commands"
    RUN_SIDE_EFFECTING_COMMANDS = "run_side_effecting_commands"
    INSTALL_DEPENDENCIES = "install_dependencies"
    USE_NETWORK = "use_network_access"
    USE_BROWSER = "use_browser_or_web_retrieval"
    ACCESS_EXTERNAL_PROVIDERS = "access_external_providers"
    ACCESS_SECRETS = "access_secrets"
    CHANGE_PROVIDER_CONFIGURATION = "change_provider_configuration"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    GIT_MERGE_REBASE = "git_merge_or_rebase"
    GIT_RESET_CLEAN = "git_reset_or_clean"
    DEPLOY = "deployment"
    SEND_EXTERNAL_MESSAGES = "external_messages"
    FINANCIAL_ACTION = "financial_or_paid_actions"
    CHANGE_SETTINGS = "change_settings"
    CHANGE_SECURITY_SETTINGS = "change_security_settings"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"
    # Public, unauthenticated, read-only network retrieval -- e.g. a weather or market-price
    # lookup. Deliberately its own action, distinct from USE_NETWORK/ACCESS_EXTERNAL_PROVIDERS:
    # those also cover sending, publishing, purchasing, and any authenticated call, all of which
    # stay gated. A tool earns this action only via its own declared contract
    # (`_web_tool_is_public_read_only`, mode_permission_policy.py) -- never by matching a word like
    # "weather" or "price" in the user's prompt, which would be a keyword exception, not a policy.
    PUBLIC_READ_ONLY_RETRIEVAL = "public_read_only_retrieval"


class PermissionEffect(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    effect: PermissionEffect
    mode: OperatingMode
    actions: tuple[PermissionAction, ...]
    reason: str
    approval_request: dict[str, Any] | None = None

    @property
    def allowed(self) -> bool:
        return self.effect is PermissionEffect.ALLOW


_ALL_ACTIONS = tuple(PermissionAction)


def _matrix_row(
    *,
    allow: set[PermissionAction],
    prompt: set[PermissionAction] | None = None,
) -> dict[PermissionAction, PermissionEffect]:
    prompted = set(prompt or ())
    return {
        action: (
            PermissionEffect.ALLOW
            if action in allow
            else PermissionEffect.REQUIRE_APPROVAL
            if action in prompted
            else PermissionEffect.DENY
        )
        for action in _ALL_ACTIONS
    }


_BASIC_READS = {
    PermissionAction.READ_FILES,
    PermissionAction.LIST_DIRECTORIES,
    PermissionAction.RUN_SAFE_COMMANDS,
}

# Manual/Review-edits specifically: local basic reads plus public read-only network retrieval.
# Scoped to these two modes only (not merged into `_BASIC_READS` itself) so Plan mode's existing
# flat network denial is untouched -- a shared constant would have silently widened Plan too.
_MANUAL_MODE_READS = _BASIC_READS | {PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL}

MODE_PERMISSION_MATRIX: dict[OperatingMode, dict[PermissionAction, PermissionEffect]] = {
    OperatingMode.MANUAL: _matrix_row(
        allow=_MANUAL_MODE_READS,
        prompt=set(_ALL_ACTIONS) - _MANUAL_MODE_READS - {PermissionAction.ACCESS_SECRETS},
    ),
    OperatingMode.REVIEW_EDITS: _matrix_row(
        allow=_MANUAL_MODE_READS,
        prompt=set(_ALL_ACTIONS) - _MANUAL_MODE_READS - {PermissionAction.ACCESS_SECRETS},
    ),
    OperatingMode.PLAN: _matrix_row(allow=_BASIC_READS),
    OperatingMode.AUTO: _matrix_row(
        allow=_BASIC_READS
        | {
            PermissionAction.CREATE_FILES,
            PermissionAction.MODIFY_FILES,
            PermissionAction.RUN_SIDE_EFFECTING_COMMANDS,
            PermissionAction.USE_NETWORK,
            PermissionAction.USE_BROWSER,
            PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
            # web.search/web.research/web.fetch/web.browser_render now resolve to this action
            # instead of {USE_NETWORK, USE_BROWSER} (see actions_for_tool). Auto already allowed
            # both of those, so omitting this action here would have silently made Auto -- meant
            # to be the MORE permissive mode -- deny these tools while Manual now allows them.
            # Caught by tests/gauntlet/test_tool_surface_invariants.py's golden diff, which showed
            # exactly that inversion, before it was ever regenerated to hide it.
            PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL,
        },
        prompt={
            PermissionAction.DELETE_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
            PermissionAction.INSTALL_DEPENDENCIES,
            PermissionAction.CHANGE_PROVIDER_CONFIGURATION,
            PermissionAction.GIT_COMMIT,
            PermissionAction.GIT_PUSH,
            PermissionAction.GIT_MERGE_REBASE,
            PermissionAction.GIT_RESET_CLEAN,
            PermissionAction.DEPLOY,
            PermissionAction.SEND_EXTERNAL_MESSAGES,
            PermissionAction.FINANCIAL_ACTION,
            PermissionAction.CHANGE_SETTINGS,
        },
    ),
    OperatingMode.BYPASS_PERMISSIONS: _matrix_row(
        allow=set(_ALL_ACTIONS)
        - {
            PermissionAction.ACCESS_SECRETS,
            PermissionAction.FINANCIAL_ACTION,
            PermissionAction.CHANGE_SECURITY_SETTINGS,
            PermissionAction.UNKNOWN_SIDE_EFFECT,
        },
        # Money retains its own explicit consent boundary even in bypass mode.
        prompt={PermissionAction.FINANCIAL_ACTION},
    ),
}


_LOCK = threading.RLock()
_ACTIVE_MODES: dict[str, dict[str, Any]] = {}
_BYPASS_GRANTS: dict[str, dict[str, Any]] = {}
_APPROVALS: dict[str, dict[str, Any]] = {}
_TASK_APPROVALS: list[dict[str, Any]] = []
# Standing per-project grants, keyed by project id, mirrored to the project entry in projects.json so
# they survive a restart and a new chat. This is a SEPARATE mechanism from
# ``context["project_permissions"]``, which is deny-only and must stay that way: a project entry can
# still only NARROW the mode matrix, while this grant can only turn a PROMPT into an allow, and only
# for the bounded low-risk class below. A value of {} is a cached "no grant".
_PROJECT_APPROVALS: dict[str, dict[str, Any]] = {}

# --- Typed internal authority ------------------------------------------------------------------
# The ONLY legitimate way for a non-chat, in-process caller to act without a user-selected mode.
#
# It replaces the old compatibility seam, which was simply "this context carries no operating_mode,
# so skip the permission decision entirely". That seam could not tell a background maintenance task
# apart from a chat turn whose mode failed to arrive, and it granted BOTH of them everything.
#
# A scope is: unguessable (a Python caller must hold the token — nothing carried in a model payload
# or an HTTP body can name one), narrow (an explicit allowlist of PermissionActions; anything
# outside it falls through to the ordinary mode matrix), in-process (memory only, so it dies with
# the daemon and cannot be replayed), and ceilinged (the classes below are never grantable, no
# matter what the caller declares).
_INTERNAL_AUTHORITY: dict[str, dict[str, Any]] = {}

#: Never delegable to an internal scope, at any breadth. Secrets, money, security settings, and
#: anything whose effect the classifier could not identify stay with the human every time.
_INTERNAL_AUTHORITY_CEILING: frozenset[PermissionAction] = frozenset(
    {
        PermissionAction.ACCESS_SECRETS,
        PermissionAction.FINANCIAL_ACTION,
        PermissionAction.CHANGE_SECURITY_SETTINGS,
        PermissionAction.UNKNOWN_SIDE_EFFECT,
    }
)

# --- A11 durable approval truth ---------------------------------------------------------------
# Pending approvals used to live ONLY in _APPROVALS: a daemon restart silently dropped every
# prompt, so a page still showing one could never resolve it (server truth said "no such
# approval"). Pending state is now mirrored to disk and restored on first use; entries past
# their expires_at restore as status "expired" — honest terminal truth, not silent absence.
# Resolved/consumed approvals are deliberately NOT persisted: their lifecycle stays exactly
# as before. Nothing here touches grant semantics.
_PERSISTED_APPROVALS_RESTORED = False


def _pending_approvals_path() -> Path | None:
    try:
        from core.runtime_paths import active_data_dir

        return (active_data_dir() / "pending_approvals.json").resolve()
    except Exception:
        return None


def _persist_pending_approvals_locked() -> None:
    # A11 lifecycle coherence — restore BEFORE snapshotting. Every mirror write is a FULL
    # snapshot of the current pending set, so persisting before a pending-only restore has
    # run would replace the file with the post-restart memory view and SILENTLY ERASE other
    # still-pending approvals that never made it back into memory (the restart-mint
    # overwrite failure). Restoring first under this same re-entrant lock makes every
    # writer (mint, resolve, consume, expiry sweep) end with one invariant:
    #   mirror == exactly the current pending set.
    _restore_persisted_approvals()
    path = _pending_approvals_path()
    if path is None:
        return
    pending = {
        token: {key: (list(value) if isinstance(value, tuple) else value) for key, value in entry.items()}
        for token, entry in _APPROVALS.items()
        if entry.get("status") == "pending"
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pending), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass  # durability is best-effort; in-memory truth remains authoritative


def _restore_persisted_approvals() -> None:
    global _PERSISTED_APPROVALS_RESTORED
    with _LOCK:
        if _PERSISTED_APPROVALS_RESTORED:
            return
        _PERSISTED_APPROVALS_RESTORED = True
    path = _pending_approvals_path()
    if path is None or not path.exists():
        return
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(stored, dict):
        return
    now = time.time()
    with _LOCK:
        for token, entry in stored.items():
            clean = str(token or "").strip()
            if not clean or not isinstance(entry, dict) or clean in _APPROVALS:
                continue
            restored = dict(entry)
            # An expired prompt must SAY expired — never resurrect as resolvable-pending.
            restored["status"] = (
                "expired" if float(restored.get("expires_at") or 0) <= now else "pending"
            )
            _APPROVALS[clean] = restored


def _ensure_approvals_restored() -> None:
    with _LOCK:
        if _PERSISTED_APPROVALS_RESTORED:
            return
    _restore_persisted_approvals()

# The only actions a standing project grant can ever cover: reads, searches, and file writes that land
# inside the bound project root. Deletes, moves, commands with side effects, network sends, git,
# deployment, settings, secrets, and FINANCIAL_ACTION are absent on purpose and keep prompting every
# single time, in every mode.
_PROJECT_SCOPE_ACTIONS = frozenset(
    {
        PermissionAction.READ_FILES,
        PermissionAction.LIST_DIRECTORIES,
        PermissionAction.RUN_SAFE_COMMANDS,
        PermissionAction.CREATE_FILES,
        PermissionAction.MODIFY_FILES,
        PermissionAction.OVERWRITE_EXISTING_FILES,
    }
)

_WRITE_ACTIONS = frozenset(
    {
        PermissionAction.CREATE_FILES,
        PermissionAction.MODIFY_FILES,
        PermissionAction.OVERWRITE_EXISTING_FILES,
    }
)

# ``source_context`` key carrying the CONCRETE further calls the model asked for in the same reply
# as the call now being gated. Server-owned: the tool loop writes it from the provider's own batch,
# and ``RESERVED_TRUST_KEYS`` strips any inbound copy, so a caller cannot post a plan and have the
# controller pre-authorize it.
PENDING_BATCH_CALLS_KEY = "pending_batch_calls"

# Upper bound on how many calls one request-scope grant may cover. A reply asking for more still
# gets a grant -- for the first N, in the model's own order. The rest keep their own prompt, so the
# cap can only ever ask MORE often, never authorize more.
_MAX_REQUEST_SCOPE_MEMBERS = 32

# The ONLY intents a bounded request-level batch may cover, head or member. Enumerated by name
# rather than derived from an action class, because the action class is too coarse to be an
# authority boundary here: `machine.write_file` and `machine.ensure_directory` write to
# ~/Desktop, ~/Downloads and ~/Documents (see `runtime_execution_tools._resolve_machine_directory`),
# and `web0.open_builder_draft`/`web0.create_project`/every other `web0.*` mutation classify into
# CREATE_FILES/MODIFY_FILES too. All of them therefore passed a `set(actions) <= _WRITE_ACTIONS`
# member test and rode one "allow all planned changes" click -- measured on this branch before the
# fix: a batch headed by an in-workspace `workspace.write_file` listed and covered a
# `machine.write_file` to `Desktop/b.txt`, and a second batch headed by `machine.write_file` was
# offered a request scope of its own. An operator approving a workspace scaffold authorized a
# write outside the workspace in the same click.
#
# Directory setup joins the set: `workspace.ensure_directory` creates a directory inside the
# workspace and destroys nothing. Deletes, moves, commands, network, git, settings, secrets and
# payments are absent on purpose and keep prompting one at a time, whatever else the same reply
# planned.
_REQUEST_SCOPE_INTENTS = frozenset(
    {
        "workspace.ensure_directory",
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
    }
)

# Members of `_REQUEST_SCOPE_INTENTS` that create or edit a FILE. The complement (directory setup)
# is what makes a batch "mixed", and is the only thing the operator-facing label keys on.
_REQUEST_SCOPE_FILE_WRITE_INTENTS = frozenset(
    {
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
    }
)

# Argument keys that name a filesystem target. Anything a tool calls a path is treated as one.
_PATH_ARG_KEYS = (
    "path",
    "paths",
    "file_path",
    "target_path",
    "destination",
    "destination_path",
    "source_path",
    "directory",
    "cwd",
)


def normalize_mode(value: Any, *, allow_legacy: bool = True) -> OperatingMode | None:
    if isinstance(value, OperatingMode):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if allow_legacy and normalized in _LEGACY_MODE_ALIASES:
        return _LEGACY_MODE_ALIASES[normalized]
    try:
        return OperatingMode(normalized)
    except ValueError:
        return None


def supported_mode_values() -> frozenset[str]:
    return frozenset(mode.value for mode in OperatingMode)


def set_active_mode(
    session_id: str,
    mode: str | OperatingMode,
    *,
    project_id: str = "",
    client_turn_id: str = "",
    bypass_token: str = "",
    workspace_root: str = "",
) -> dict[str, Any]:
    clean_session = str(session_id or "").strip()
    requested = normalize_mode(mode)
    if not clean_session or requested is None:
        raise ValueError("A valid session and operating mode are required.")

    if requested is OperatingMode.BYPASS_PERMISSIONS:
        grant = validate_bypass_grant(
            bypass_token,
            session_id=clean_session,
            project_id=str(project_id or "").strip(),
            task_id=str(client_turn_id or "").strip(),
            workspace_root=str(workspace_root or "").strip(),
        )
        if grant is None:
            raise PermissionError("Bypass permissions needs a current, explicitly confirmed grant.")

    now = time.time()
    non_durable_revocation = ""
    with _LOCK:
        previous = dict(_ACTIVE_MODES.get(clean_session) or {})
        revision = int(previous.get("revision") or 0) + (0 if previous.get("mode") == requested.value else 1)
        if (
            requested is not OperatingMode.BYPASS_PERMISSIONS
            and str(previous.get("bypass_token") or "").strip()
        ):
            # Returning to Manual (or any non-bypass mode) INVALIDATES the session's bypass
            # grant, it does not merely stop displaying it: a stale client page still holding
            # the token could otherwise re-arm bypass through `active_mode_state`'s
            # context fallback. Revoked in the store, so every future consult fails closed.
            stale_token = str(previous.get("bypass_token") or "").strip()
            if stale_token in _BYPASS_GRANTS:
                _BYPASS_GRANTS[stale_token]["revoked"] = True
                if _BYPASS_GRANTS[stale_token].get("until_off"):
                    try:
                        _record_bypass_revocation_durably_locked([stale_token])
                    except BypassStoreError as exc:
                        # Revoked in memory for this process; neither the mirror rewrite nor the
                        # journal append could record it, so the grant CAN return after a restart.
                        # Named in the state (the UI surfaces it) rather than swallowed into a
                        # false durable-success.
                        non_durable_revocation = str(exc)
        state = {
            "session_id": clean_session,
            "project_id": str(project_id or previous.get("project_id") or "").strip(),
            "client_turn_id": str(client_turn_id or previous.get("client_turn_id") or "").strip(),
            "mode": requested.value,
            "label": MODE_LABELS[requested],
            "revision": revision,
            "updated_at": now,
            "bypass_token": str(bypass_token or "").strip() if requested is OperatingMode.BYPASS_PERMISSIONS else "",
        }
        if non_durable_revocation:
            state["bypass_revocation_not_durable"] = non_durable_revocation
        _ACTIVE_MODES[clean_session] = state
    return dict(state)


def active_mode_state(source_context: dict[str, Any] | None) -> dict[str, Any]:
    context = dict(source_context or {})
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    with _LOCK:
        state = dict(_ACTIVE_MODES.get(session_id) or {}) if session_id else {}
    if state:
        if state.get("mode") == OperatingMode.BYPASS_PERMISSIONS.value:
            grant = validate_bypass_grant(
                str(state.get("bypass_token") or ""),
                session_id=session_id,
                project_id=str(context.get("project_id") or state.get("project_id") or ""),
                task_id=str(context.get("cancel_turn_id") or state.get("client_turn_id") or ""),
                workspace_root=_trusted_write_root(context),
            )
            if grant is None:
                # Expiration/revocation fails closed immediately, including mid-task.
                return {**state, "mode": OperatingMode.MANUAL.value, "label": MODE_LABELS[OperatingMode.MANUAL]}
        return state
    fallback = normalize_mode(context.get("operating_mode")) or OperatingMode.MANUAL
    # GOBLIN inv 2 (NO SELF-MINT): a caller-supplied `operating_mode` string is
    # client/model-controlled data. It may name any mode EXCEPT bypass without proof — it
    # can never MINT BYPASS_PERMISSIONS authority. The two authoritative paths
    # (`set_active_mode`, and the state-exists branch above) both require a canonical,
    # currently-valid bypass grant; this fallback did not, so a context carrying
    # `operating_mode="bypass_permissions"` with no server-side session record was honored
    # unvalidated. Fail closed to MANUAL exactly like the expired-grant branch (L363-365).
    if fallback is OperatingMode.BYPASS_PERMISSIONS:
        grant = validate_bypass_grant(
            str(context.get("bypass_token") or ""),
            session_id=session_id,
            project_id=str(context.get("project_id") or ""),
            task_id=str(context.get("cancel_turn_id") or ""),
            workspace_root=_trusted_write_root(context),
        )
        if grant is None:
            fallback = OperatingMode.MANUAL
    return {
        "session_id": session_id,
        "project_id": str(context.get("project_id") or "").strip(),
        "client_turn_id": str(context.get("cancel_turn_id") or "").strip(),
        "mode": fallback.value,
        "label": MODE_LABELS[fallback],
        "revision": 0,
        "updated_at": 0.0,
        "bypass_token": (str(context.get("bypass_token") or "").strip()
                         if fallback is OperatingMode.BYPASS_PERMISSIONS else ""),
    }


def mode_policy_is_active(source_context: dict[str, Any] | None) -> bool:
    """Whether this call carries an EXPLICIT mode — a record, or a named mode on the context.

    This is now a reporting predicate only. It once decided whether `execute_tool_intent` consulted
    `decide_tool_call` at all, which made the absence of a mode a silent, total permission bypass:
    a caller with no `operating_mode` and no session record reached workspace writes and
    side-effecting shell commands with no decision taken. Every foreground dispatch is decided
    unconditionally now, and a call with no mode resolves to MANUAL (see `effective_mode_state`).

    It is deliberately NOT the answer to "may this run" — nothing may branch on it to skip a
    permission decision.
    """
    context = dict(source_context or {})
    if normalize_mode(context.get("operating_mode")) is not None:
        return True
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    with _LOCK:
        return bool(session_id and session_id in _ACTIVE_MODES)


def grant_internal_authority(
    *,
    label: str,
    actions: Iterable[PermissionAction],
    duration_seconds: int = 0,
    session_id: str = "",
    task_id: str = "",
    intents: Iterable[str] = (),
    workspace_root: str = "",
) -> str:
    """Mint a narrow, in-process authority for a background/internal caller. Returns its token.

    `label` names WHY the scope exists and is carried into the permission event record, so an
    allow taken on this path is attributable rather than anonymous. `actions` is the exact
    allowlist: a call whose classified actions are not all inside it falls through to the ordinary
    mode matrix.

    A scope is BOUNDED or it is not minted. It must carry BOTH:

    * an expiry — `duration_seconds` is required (1..86400). There is no process-lifetime
      token: an action-only bearer that lives until the process ends is exactly the
      "internal authority" shape this repair refuses.
    * a binding beyond its actions — at least one of `session_id`, `task_id`, `intents`,
      `workspace_root`. The consult seam enforces each declared binding against the call
      (session and task from the decision's own ids, intents from the intent, workspace
      from the context's workspace root), and a call that does not match every declared
      binding decides as if the scope did not exist.

    The ceiling in `_INTERNAL_AUTHORITY_CEILING` is applied HERE as well as at consult time, so a
    caller cannot even hold a token that names secrets, money, or security settings.
    """
    clean_label = str(label or "").strip()
    if not clean_label:
        raise ValueError("An internal authority scope must state what it is for.")
    requested = {action for action in actions if isinstance(action, PermissionAction)}
    granted = frozenset(requested - _INTERNAL_AUTHORITY_CEILING)
    if not granted:
        raise ValueError("An internal authority scope must name at least one grantable action.")
    try:
        bounded_duration = int(duration_seconds)
    except (TypeError, ValueError):
        bounded_duration = 0
    if not 1 <= bounded_duration <= 86400:
        raise ValueError(
            "An internal authority scope must declare a bounded duration_seconds (1..86400); "
            "a process-lifetime bearer is not mintable."
        )
    clean_session = str(session_id or "").strip()
    clean_task = str(task_id or "").strip()
    clean_intents = frozenset(
        str(item or "").strip() for item in (intents or ()) if str(item or "").strip()
    )
    clean_workspace = str(workspace_root or "").strip()
    if not (clean_session or clean_task or clean_intents or clean_workspace):
        raise ValueError(
            "An internal authority scope must bind to something beyond its actions: "
            "session_id, task_id, intents, or workspace_root."
        )
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        _INTERNAL_AUTHORITY[token] = {
            "token": token,
            "label": clean_label,
            "actions": granted,
            "session_id": clean_session,
            "task_id": clean_task,
            "intents": clean_intents,
            "workspace_root": clean_workspace,
            "issued_at": now,
            "expires_at": now + bounded_duration,
        }
    return token


def revoke_internal_authority(token: str) -> bool:
    with _LOCK:
        return _INTERNAL_AUTHORITY.pop(str(token or "").strip(), None) is not None


# --- The chat-scoped workspace approval (Manual mode) -----------------------------------------
#
# "Allow workspace reads and edits in this chat" is a STANDING grant with a stated scope, minted
# only by the owner's explicit approval decision. It rides the internal-authority consult above:
# the exact allowlist is reads plus ORDINARY writes (create/modify) inside the trusted bound
# workspace. Deletion, overwrites, arbitrary commands, external transmission, secrets, wallet
# signing, protected-contact changes and money NEVER ride this grant -- they still prompt through
# the mode matrix, because the scope simply does not name their actions.
_CHAT_WORKSPACE_ACTIONS: frozenset[PermissionAction] = frozenset(
    {
        PermissionAction.READ_FILES,
        PermissionAction.LIST_DIRECTORIES,
        PermissionAction.CREATE_FILES,
        PermissionAction.MODIFY_FILES,
    }
)
#: session_id -> internal-authority token. One standing workspace scope per chat; a second
#: grant replaces the first (and revokes its token) rather than stacking.
_CHAT_WORKSPACE_AUTHORITY: dict[str, str] = {}


def grant_chat_workspace_authority(
    *,
    session_id: str,
    workspace_root: str,
    duration_seconds: int = 28800,
) -> dict[str, Any]:
    """Mint the chat-scoped workspace read/edit scope. Bounded by construction.

    Duration is required (the internal-authority law: bounded or not minted), defaults to eight
    hours and is capped at one day -- a chat grant that outlives its day is a standing project
    grant wearing a chat label, and the project grant has its own explicit flow.
    """
    clean_session = str(session_id or "").strip()
    clean_root = str(workspace_root or "").strip()
    if not clean_session:
        raise ValueError("A chat workspace scope requires the chat's session id.")
    if not clean_root:
        raise ValueError("A chat workspace scope requires the chat's trusted workspace root.")
    confinement = _workspace_root_confined(clean_root)
    if confinement:
        raise ValueError(f"a chat workspace scope cannot bind this root: {confinement}")
    bounded = max(60, min(int(duration_seconds or 28800), 86400))
    token = grant_internal_authority(
        label="chat workspace read/edit approval",
        actions=_CHAT_WORKSPACE_ACTIONS,
        duration_seconds=bounded,
        session_id=clean_session,
        workspace_root=clean_root,
    )
    with _LOCK:
        previous = _CHAT_WORKSPACE_AUTHORITY.get(clean_session)
        if previous and previous != token:
            _INTERNAL_AUTHORITY.pop(previous, None)
        _CHAT_WORKSPACE_AUTHORITY[clean_session] = token
    return {
        "session_id": clean_session,
        "workspace_root": clean_root,
        "duration_seconds": bounded,
        "token": token,
        "actions": sorted(action.value for action in _CHAT_WORKSPACE_ACTIONS),
    }


def revoke_chat_workspace_authority(session_id: str) -> bool:
    clean_session = str(session_id or "").strip()
    with _LOCK:
        token = _CHAT_WORKSPACE_AUTHORITY.pop(clean_session, None)
    if not token:
        return False
    _INTERNAL_AUTHORITY.pop(token, None)
    return True


def session_has_pending_workspace_approval(session_id: str) -> bool:
    """Whether THIS chat currently shows a pending permission ask inside the chat-workspace class.

    The guard that keeps `grant_chat_workspace` an OPERATOR answer rather than a mintable API
    call: the standing scope may only be created while the server itself has asked this chat to
    approve an ordinary workspace read/edit. A bare loopback POST naming any session and root --
    from a plugin, a model tool, or a scripted client -- finds no pending ask and mints nothing.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return False
    workspace_actions = {action.value for action in _CHAT_WORKSPACE_ACTIONS}
    now = time.time()
    with _LOCK:
        for approval in _APPROVALS.values():
            if str(approval.get("session_id") or "") != clean_session:
                continue
            if approval.get("status") != "pending":
                continue
            if float(approval.get("expires_at") or 0) <= now:
                continue
            actions = {str(item) for item in (approval.get("actions") or ())}
            if actions and actions <= workspace_actions:
                return True
    return False


def chat_workspace_authority_state(session_id: str) -> dict[str, Any]:
    """The chat's standing workspace scope if live (token valid and unexpired), else {}."""
    clean_session = str(session_id or "").strip()
    with _LOCK:
        token = _CHAT_WORKSPACE_AUTHORITY.get(clean_session) or ""
    if not token:
        return {}
    scope = _internal_authority_scope({"internal_authority_token": token})
    if scope is None:
        with _LOCK:
            _CHAT_WORKSPACE_AUTHORITY.pop(clean_session, None)
        return {}
    return dict(scope)


def _internal_authority_scope(source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """The live scope for this context, or None. Expiry and the ceiling both fail closed.

    Two carriers, one authority: an explicit ``internal_authority_token`` on the context, or the
    CHAT-STANDING workspace scope minted by the owner's "allow workspace reads and edits in this
    chat" approval. The standing scope is looked up by the context's own session id — never from
    a client field — and every consult re-runs the full binding check (session, workspace,
    actions), so a call outside the granted workspace or beyond the granted actions decides as if
    the scope did not exist.
    """
    token = str((source_context or {}).get("internal_authority_token") or "").strip()
    if not token:
        context_session = str(
            (source_context or {}).get("runtime_session_id")
            or (source_context or {}).get("session_id")
            or ""
        ).strip()
        with _LOCK:
            token = _CHAT_WORKSPACE_AUTHORITY.get(context_session) or ""
    if not token:
        return None
    with _LOCK:
        scope = _INTERNAL_AUTHORITY.get(token)
        if not scope:
            return None
        expires_at = float(scope.get("expires_at") or 0)
        if expires_at and expires_at <= time.time():
            _INTERNAL_AUTHORITY.pop(token, None)
            return None
        return dict(scope)


def _path_inside(child: str, root: str) -> bool:
    """Whether `child` is `root` or somewhere under it (both resolved, same drive)."""
    try:
        return os.path.commonpath([os.path.realpath(child), os.path.realpath(root)]) == os.path.realpath(
            root
        )
    except (ValueError, OSError):
        return False


def _trusted_write_root(source_context: dict[str, Any] | None) -> str:
    """The root a relative write lands under, in the WRITER's precedence
    (`core.runtime_execution_tools._workspace_root`): the trusted context's `workspace`, then
    `workspace_root`. A context may legitimately carry both -- the envelope executor, its restore
    step and the blackbox operator re-root `workspace` over an inherited `workspace_root` -- so a
    permission decision that reads its own precedence describes a different file than the one the
    writer touches. Every decision here that locates a write reads this."""
    context = source_context if isinstance(source_context, dict) else {}
    return str(context.get("workspace") or context.get("workspace_root") or "").strip()


def _step_execution_context(
    source_context: dict[str, Any] | None, step_arguments: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The context the inner call of a `code.task.step` ACTUALLY runs under: the named task's own
    workspace pins both roots (`core.code_assistant.task_runtime.task_execution_context`), whatever
    roots the caller named. The caller's context when the step names no task this session owns --
    the step refuses such a call before any inner tool runs."""
    task_id = str((step_arguments or {}).get("task_id") or "").strip()
    if not task_id:
        return source_context
    try:
        from core.code_assistant.task_runtime import task_execution_context

        return task_execution_context(source_context, task_id) or source_context
    except Exception:
        return source_context


def _internal_scope_matches(
    scope: dict[str, Any],
    *,
    context: dict[str, Any],
    intent: str,
    task_id: str,
) -> bool:
    """Every binding the scope declared must hold for THIS call, or it decides as if absent.

    A scope bound to a session, task, intent set, or workspace root is a claim about what it
    is for; a call that is not that thing cannot use it. Each binding family is checked only
    when the scope declared it, so the narrowest honest scope (one binding) stays usable while
    no scope can float free of everything it names.
    """
    bound_session = str(scope.get("session_id") or "").strip()
    if bound_session:
        call_session = str(
            context.get("runtime_session_id") or context.get("session_id") or ""
        ).strip()
        if call_session != bound_session:
            return False
    bound_task = str(scope.get("task_id") or "").strip()
    if bound_task and str(task_id or "").strip() != bound_task:
        return False
    bound_intents = frozenset(scope.get("intents") or frozenset())
    if bound_intents and str(intent or "").strip() not in bound_intents:
        return False
    bound_root = str(scope.get("workspace_root") or "").strip()
    if bound_root:
        call_root = _trusted_write_root(context)
        if not call_root or not _path_inside(call_root, bound_root):
            return False
    return True


def effective_mode_state(
    source_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """THE effective-mode authority for a context. Missing or invalid always means MANUAL.

    `active_mode_state` already fails closed this way; this name states the contract at the door so
    no caller has to re-derive it — and so nobody re-introduces "no mode, so no decision".
    """
    return active_mode_state(source_context)


def resolve_effective_mode(
    *,
    session_id: str,
    requested_mode: Any = None,
    project_id: str = "",
    client_turn_id: str = "",
    bypass_token: str = "",
    workspace_root: str = "",
) -> dict[str, Any]:
    """Resolve and REGISTER the controller-owned mode for a turn. Never raises; never AUTO.

    One authority, three inputs, in priority order:

    1. An explicit, valid `requested_mode` — recorded as the session's mode (this is the composer
       selection, and a mode change mid-session takes effect from here).
    2. Otherwise the session's existing controller-owned record, so a turn that simply omits the
       field keeps the mode the operator already chose instead of being downgraded every other
       turn.
    3. Otherwise MANUAL — reads work, mutations ask. Defaulting to AUTO would leave exactly the
       hole this replaced: AUTO permits writes and side-effecting commands, so "no mode" would
       still mean "do whatever" with a friendlier name.

    An EXPLICITLY invalid requested_mode is a fourth fact, not case 2: it never inherits the
    session's selection (an AUTO session stays AUTO for omitted turns, never for a turn that
    named a mode that does not parse). The turn resolves to MANUAL and the state carries
    `invalid_mode_refused` naming the refused value, so an ingress can answer it out loud
    instead of silently serving Manual.

    Bypass is never minted here: an explicit bypass request without a currently-valid, explicitly
    confirmed server-side grant fails closed to MANUAL, and the returned state records
    `downgraded_from` so an ingress can tell the operator their request was refused rather than
    silently honoured.
    """
    clean_session = str(session_id or "").strip()
    requested = normalize_mode(requested_mode)
    # An EXPLICITLY invalid mode is not an omitted one. `normalize_mode` collapses both to
    # None; without this split, a turn that names a mode the authority cannot parse fell
    # through to the session's existing selection — so one typo'd mode field on an AUTO
    # session kept every AUTO permission for that turn. An invalid value fails THIS turn
    # closed to MANUAL (the controller-owned selection is left untouched for honest turns)
    # and the refusal is named in the state so an ingress can answer it out loud.
    raw_requested = "" if requested_mode is None else str(requested_mode).strip()
    explicitly_invalid = requested is None and bool(raw_requested)

    if explicitly_invalid:
        with _LOCK:
            existing = dict(_ACTIVE_MODES.get(clean_session) or {}) if clean_session else {}
        state = dict(existing) if existing else {
            "session_id": clean_session,
            "project_id": str(project_id or "").strip(),
            "client_turn_id": str(client_turn_id or "").strip(),
            "revision": 0,
            "updated_at": 0.0,
            "bypass_token": "",
        }
        state.update(
            {
                "session_id": clean_session,
                "mode": OperatingMode.MANUAL.value,
                "label": MODE_LABELS[OperatingMode.MANUAL],
                "bypass_token": "",
            }
        )
        return {**state, "invalid_mode_refused": raw_requested[:80]}

    if requested is OperatingMode.BYPASS_PERMISSIONS:
        grant = validate_bypass_grant(
            str(bypass_token or ""),
            session_id=clean_session,
            project_id=str(project_id or "").strip(),
            task_id=str(client_turn_id or "").strip(),
            workspace_root=str(workspace_root or "").strip(),
        )
        if grant is None:
            downgraded = resolve_effective_mode(
                session_id=clean_session,
                requested_mode=OperatingMode.MANUAL,
                project_id=project_id,
                client_turn_id=client_turn_id,
            )
            return {**downgraded, "downgraded_from": OperatingMode.BYPASS_PERMISSIONS.value}

    if requested is not None and clean_session:
        return set_active_mode(
            clean_session,
            requested,
            project_id=str(project_id or "").strip(),
            client_turn_id=str(client_turn_id or "").strip(),
            bypass_token=str(bypass_token or ""),
            workspace_root=str(workspace_root or "").strip(),
        )

    context = {
        "runtime_session_id": clean_session,
        "project_id": str(project_id or "").strip(),
        "cancel_turn_id": str(client_turn_id or "").strip(),
    }
    with _LOCK:
        existing = dict(_ACTIVE_MODES.get(clean_session) or {}) if clean_session else {}
    if existing:
        # Re-read through the state reader so an expired/revoked bypass fails closed here too.
        return dict(active_mode_state(context))
    if not clean_session:
        return dict(active_mode_state(context))
    return set_active_mode(
        clean_session,
        OperatingMode.MANUAL,
        project_id=str(project_id or "").strip(),
        client_turn_id=str(client_turn_id or "").strip(),
    )


#: Server-minted, single-use, exact-bindings confirmations for bypass activation.
#: A confirmation is minted ONLY by the local UI route (op=request_bypass_confirmation),
#: lives 60 seconds, and is consumed exactly once by activate_bypass_grant with MATCHING
#: bindings. A caller-asserted boolean can never again stand in for the user's click:
#: replayed confirmations die with the nonce, a confirmation minted for one action cannot
#: approve another (bindings), and there is no phrase to say.
_PENDING_BYPASS_CONFIRMATIONS: dict[str, dict[str, Any]] = {}
_BYPASS_CONFIRMATION_TTL_SECONDS = 60.0


def request_bypass_confirmation(
    *,
    session_id: str,
    project_id: str = "",
    task_id: str = "",
    scope: str = "task",
    duration_seconds: int = 900,
    until_off: bool = False,
    workspace_root: str = "",
) -> str:
    """Mint a single-use confirmation for THIS exact bypass activation.

    Minting grants nothing; only the matching activate consumes it.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        raise ValueError("session_id is required")
    confirmation_id = secrets.token_urlsafe(24)
    now = time.time()
    with _LOCK:
        stale = [
            cid for cid, record in _PENDING_BYPASS_CONFIRMATIONS.items()
            if now - float(record.get("minted_at") or 0.0) > _BYPASS_CONFIRMATION_TTL_SECONDS
        ]
        for cid in stale:
            _PENDING_BYPASS_CONFIRMATIONS.pop(cid, None)
        _PENDING_BYPASS_CONFIRMATIONS[confirmation_id] = {
            "session_id": clean_session,
            "project_id": str(project_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "scope": str(scope or "task").strip().lower(),
            "duration_seconds": int(duration_seconds or 900),
            "until_off": bool(until_off),
            "workspace_root": str(workspace_root or "").strip(),
            "minted_at": now,
        }
    return confirmation_id


def _consume_bypass_confirmation(
    confirmation_id: str,
    *,
    session_id: str,
    project_id: str,
    task_id: str,
    scope: str,
    duration_seconds: int,
    until_off: bool,
    workspace_root: str,
) -> tuple[bool, str]:
    record = None
    with _LOCK:
        record = _PENDING_BYPASS_CONFIRMATIONS.pop(str(confirmation_id or ""), None)
    if record is None:
        return False, "confirmation not found, already used, or expired"
    if time.time() - float(record.get("minted_at") or 0.0) > _BYPASS_CONFIRMATION_TTL_SECONDS:
        return False, "confirmation expired"
    expected = {
        "session_id": str(session_id or "").strip(),
        "project_id": str(project_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "scope": str(scope or "task").strip().lower(),
        "duration_seconds": int(duration_seconds or 900),
        "until_off": bool(until_off),
        "workspace_root": str(workspace_root or "").strip(),
    }
    for key, value in expected.items():
        if record.get(key) != value:
            return False, f"confirmation was minted for different {key}"
    return True, ""


def activate_bypass_grant(
    *,
    session_id: str,
    project_id: str = "",
    task_id: str = "",
    scope: str = "task",
    duration_seconds: int = 900,
    confirmation_id: str = "",
    until_off: bool = False,
    workspace_root: str = "",
) -> dict[str, Any]:
    ok, reason = _consume_bypass_confirmation(
        confirmation_id,
        session_id=session_id,
        project_id=project_id,
        task_id=task_id,
        scope=scope,
        duration_seconds=duration_seconds,
        until_off=until_off,
        workspace_root=workspace_root,
    )
    if not ok:
        raise PermissionError(
            "Bypass activation requires a current, single-use confirmation minted for "
            f"this exact action ({reason}). Request one from the local VOOL UI."
        )
    clean_session = str(session_id or "").strip()
    clean_scope = str(scope or "task").strip().lower()
    if not clean_session:
        raise ValueError("session_id is required")
    if clean_scope not in {"task", "session", "project"}:
        raise ValueError("scope must be task, session, or project")
    clean_task = str(task_id or "").strip()
    if clean_scope == "task" and not clean_task:
        raise ValueError("task scope requires an active task id")
    if clean_scope == "project" and not str(project_id or "").strip():
        raise ValueError("project scope requires an active project")
    until_off = bool(until_off)
    clean_workspace = str(workspace_root or "").strip()
    if until_off:
        # "Until I turn it off — this chat only." A no-timer grant exists ONLY bound to one chat
        # and that chat's trusted workspace; there is deliberately no until-off task or project
        # variant, because either would be an unrestricted standing grant wearing a timerless
        # face. The workspace binding is re-checked on every consult, so moving the chat to a
        # different workspace does not carry the old grant with it.
        if clean_scope != "session":
            raise ValueError("an until-off bypass is chat-scoped only")
        if not clean_workspace:
            raise ValueError("an until-off bypass requires the chat's trusted workspace root")
        confinement = _workspace_root_confined(clean_workspace)
        if confinement:
            raise ValueError(f"an until-off bypass cannot bind this workspace: {confinement}")
    # Timed grants run from minutes up to a full day. Minutes and hours, including a custom
    # duration, are real product needs; longer than a day is a permanent grant pretending to be
    # a timed one, so it is not mintable — the until-off chat scope is the honest shape for that.
    bounded_duration = max(60, min(int(duration_seconds or 900), 86400))
    token = secrets.token_urlsafe(32)
    now = time.time()
    grant = {
        "token": token,
        "session_id": clean_session,
        "project_id": str(project_id or "").strip(),
        "task_id": clean_task,
        "scope": clean_scope,
        "issued_at": now,
        # until-off carries expires_at None — never an enormous timestamp standing in for
        # infinity, so every reader can tell the two lifetimes apart by structure.
        "expires_at": None if until_off else now + bounded_duration,
        "until_off": until_off,
        "workspace_root": clean_workspace if until_off else "",
        "revoked": False,
    }
    with _LOCK:
        _BYPASS_GRANTS[token] = grant
        if until_off:
            # A no-timer grant that cannot be made DURABLE is not activated: reporting success
            # while the mirror write failed would be exactly the false durable-success this
            # store refuses. The mint is rolled back and the storage failure is the answer.
            try:
                _persist_bypass_grants_locked()
            except BypassStoreError:
                _BYPASS_GRANTS.pop(token, None)
                raise ValueError(
                    "the until-off bypass could not be recorded durably, so it was not activated; "
                    "fix the profile storage (disk space, permissions) and approve it again"
                ) from None
    return dict(grant)


def _bypass_grants_path() -> Path | None:
    try:
        from core.runtime_paths import active_data_dir

        return (active_data_dir() / "bypass_grants.json").resolve()
    except Exception:
        return None


class BypassStoreError(RuntimeError):
    """The durable mirror of until-off bypass authority could not be written.

    Raised INSTEAD of a silent best-effort swallow: an activation that cannot be made durable is
    refused (and rolled back), and a revocation that cannot be recorded -- by the mirror rewrite
    NOR by the append-only revocation journal beside it -- says so, because the alternative --
    returning success -- is exactly how a revoked grant silently reappears after a restart.
    In-memory truth stays authoritative for the running process either way.
    """


#: Durable-store envelope schema. Bumping it invalidates old files (they restore as nothing).
_BYPASS_STORE_SCHEMA = 1


def _workspace_root_confined(workspace_root: str) -> str:
    """Why this root may NOT host a standing grant, or "" when it may.

    The authority stores (data dir for the mirror, config dir for the integrity key) must never
    live INSIDE a workspace a grant authorizes: a root at or above the profile directory would put
    `bypass_grants.json` and its key inside the very edit scope the grant mints, so the ordinary
    workspace-edit capability the grant itself provides could rewrite the authority. The same law
    refuses the home directory and the filesystem root for the same reason.
    """
    root = str(workspace_root or "").strip()
    if not root:
        return "a standing grant requires a workspace root"
    try:
        from core.runtime_paths import active_config_home_dir, active_data_dir

        protected = [active_data_dir(), active_config_home_dir()]
    except Exception:
        protected = []
    try:
        resolved = Path(root).expanduser().resolve()
    except (OSError, RuntimeError):
        return "the workspace root could not be resolved"
    if str(resolved) in {"/", str(Path.home().resolve())}:
        return "the filesystem root and the home directory cannot be standing workspaces"
    for guarded in protected:
        try:
            if _path_inside(str(guarded), str(resolved)):
                return (
                    "the workspace root contains VOOL's own authority storage, so it cannot be "
                    "a standing grant scope"
                )
        except (OSError, RuntimeError):
            continue
    return ""


def _bypass_store_key() -> bytes | None:
    """The machine-local HMAC key for the grant mirror, created once under the config dir.

    Stored beside (never inside) any grantable workspace with owner-only permissions. This is
    tamper-evidence against edits from ordinary workspace scopes and plugins, NOT a security
    boundary against code running with the owner's own OS identity -- such code can read this key
    like the owner can, and that limit is stated rather than hidden.
    """
    try:
        from core.runtime_paths import active_config_home_dir

        path = active_config_home_dir() / "bypass_grants.key"
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            data = b""
        if len(data) == 64:
            return data
        if data:
            # A key file of the wrong size is corruption, not a key: replace it (which also
            # invalidates every existing grant -- they restore as nothing, fail closed).
            data = b""
        fresh = secrets.token_bytes(64)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".key.tmp")
        tmp.write_bytes(fresh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return fresh
    except Exception:
        return None


def _grant_canonical_bytes(grants: dict[str, dict[str, Any]]) -> bytes:
    """The exact bytes the MAC covers: the grants map, canonical JSON, nothing else."""
    return json.dumps(grants, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _durable_bypass_grants_locked() -> dict[str, dict[str, Any]]:
    return {
        token: grant
        for token, grant in _BYPASS_GRANTS.items()
        if grant.get("until_off") and not grant.get("revoked")
    }


def _envelope_canonical_bytes(grants: dict[str, dict[str, Any]], *, journal_required: bool) -> bytes:
    """The bytes the CURRENT envelope MAC covers: the grants AND the security-relevant metadata.

    The stamp that makes the revocation journal required history is itself security-relevant --
    an envelope whose stamp is stripped must not become readable as a legacy store -- so it rides
    inside the MAC. Legacy envelopes (written before the journal existed) keep their own
    grants-only MAC shape; a stripped CURRENT envelope matches neither form and fails closed.
    """
    payload = {
        "schema": _BYPASS_STORE_SCHEMA,
        "journal_required": bool(journal_required),
        "grants": grants,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _persist_bypass_grants_locked() -> None:
    """Mirror ONLY the until-off chat grants to disk, atomically and with an integrity MAC.

    Durability is REQUIRED, not best-effort: a failed write raises `BypassStoreError` so callers
    can refuse activations and name non-durable revocations instead of reporting success. Timed
    grants stay in memory exactly as before: they expire on their own, and persisting them would
    resurrect expired authority after a clock skew.

    The file is `{"schema", "revocation_journal", "grants", "mac"}` written to a temp file,
    fsynced, then `os.replace`d into place -- a reader ever sees the previous complete file or
    the new one, never a torn write, so process interruption during persistence cannot
    half-record authority. The MAC covers the envelope's security-relevant metadata (schema and
    the journal requirement) beside the grants, via `_envelope_canonical_bytes`.

    The journal and its completeness anchor are written BEFORE the mirror is replaced: a stamped
    mirror may not come into existence without its history files, and a persist that finds the
    journal already unverifiable refuses before changing anything durable.
    """
    path = _bypass_grants_path()
    if path is None:
        raise BypassStoreError("the bypass grant store location is unavailable")
    key = _bypass_store_key()
    if key is None:
        raise BypassStoreError("the bypass grant integrity key is unavailable")
    durable = _durable_bypass_grants_locked()
    envelope = {
        "schema": _BYPASS_STORE_SCHEMA,
        # The stamp that makes the revocation journal REQUIRED history for this store: grants in
        # this envelope were minted by code that maintains the journal, so a restart finding the
        # journal missing knows the store's revocation past is unverifiable, not empty. An
        # UNstamped envelope with the legacy grants-only MAC is a pre-journal store -- no
        # journalled revocation can exist for it, and it restores exactly as it always did.
        "revocation_journal": True,
        "grants": durable,
        "mac": hmac.new(key, _envelope_canonical_bytes(durable, journal_required=True), hashlib.sha256).hexdigest(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        journal = _bypass_revocations_path()
        if journal is not None:
            with open(journal, "a", encoding="utf-8"):
                pass
            # The ESTABLISHED history must be complete before the anchor is written: re-anchoring
            # truncated history would sign a replacement head over lost acknowledged revocations.
            # Genuine initialization (empty or absent journal, no acknowledged head) passes and
            # the anchor write below then records the zero head explicitly.
            established, records, reason = _bypass_revocation_history_state()
            if not established:
                raise BypassStoreError(
                    f"the existing revocation history is unverifiable ({reason}); "
                    "the store cannot be persisted"
                )
            _write_bypass_revocation_head_locked(len(records), (_record_digest(records[-1]) if records else ""))
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BypassStoreError:
        raise
    except Exception as exc:
        raise BypassStoreError(f"the bypass grant store could not be written: {exc}") from exc


def _bypass_revocations_path() -> Path | None:
    """The append-only revocation journal, a sibling of the mirror (same directory, same law).

    Derived FROM the mirror path so every path redirection (tests isolate the store) covers both
    files together -- a journal for a different store would be a second authority.
    """
    mirror = _bypass_grants_path()
    if mirror is None:
        return None
    return mirror.with_name("bypass_grants.revocations.log")


def _bypass_revocations_head_path() -> Path | None:
    """The journal's completeness anchor, a sibling of the journal.

    Holds the MAC'd chain head (last acknowledged record's sequence and digest). Per-record MACs
    prove each record authentic; only the anchor proves the SET complete, because an attacker who
    deletes whole valid records leaves every surviving MAC intact. Derived from
    `_bypass_revocations_path()` so path redirections cover all three store files together.
    """
    journal = _bypass_revocations_path()
    if journal is None:
        return None
    return journal.with_name("bypass_grants.revocations.head")


def _record_digest(record: dict[str, Any]) -> str:
    """The chain digest of one journal record: its canonical bytes without its own MAC."""
    body = {key: value for key, value in dict(record).items() if key != "mac"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _write_bypass_revocation_head_locked(head_seq: int, head_hash: str) -> None:
    """Record the journal chain head, MAC'd with the store key.

    Written in place (truncate + write + fsync): the anchor must stay updatable in exactly the
    envelope where the journal append is needed -- no directory write, no rename. A torn anchor
    overwrite (crash mid-write) leaves bytes that do not verify, and restore fails CLOSED on
    that: the owner re-approves, stale authority does not return.
    """
    anchor = _bypass_revocations_head_path()
    if anchor is None:
        raise BypassStoreError("the bypass revocation anchor location is unavailable")
    key = _bypass_store_key()
    if key is None:
        raise BypassStoreError("the bypass grant integrity key is unavailable")
    payload = {
        "schema": _BYPASS_STORE_SCHEMA,
        "head_seq": int(head_seq),
        "head_hash": str(head_hash or ""),
    }
    mac = hmac.new(key, f"{payload['schema']}|{payload['head_seq']}|{payload['head_hash']}".encode(), hashlib.sha256).hexdigest()
    try:
        with open(anchor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({**payload, "mac": mac}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        raise BypassStoreError(f"the bypass revocation anchor could not be written: {exc}") from exc


def _bypass_journal_chain() -> tuple[list[dict[str, Any]], bool]:
    """The journal's records in chain order, and whether the chain verifies.

    Every non-empty line-separated segment must parse, carry this schema, MAC-verify under the
    store key, carry the next contiguous sequence number, and name the previous record's digest.
    NOTHING is inferred from a missing trailing newline: a complete record without its newline is
    a complete record, and an unparsable segment -- torn or tampered, the two are
    indistinguishable -- makes the whole history untrusted rather than silently dropped.
    """
    path = _bypass_revocations_path()
    if path is None or not path.exists():
        return [], True
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return [], False
    key = _bypass_store_key()
    if key is None:
        return [], False
    records: list[dict[str, Any]] = []
    previous_digest = ""
    for segment in raw.split("\n"):
        if not segment.strip():
            continue
        try:
            record = json.loads(segment)
        except Exception:
            return [], False
        if not isinstance(record, dict) or record.get("schema") != _BYPASS_STORE_SCHEMA:
            return [], False
        token = str(record.get("token") or "")
        if not token or record.get("seq") != len(records) + 1 or str(record.get("prev") or "") != previous_digest:
            return [], False
        body = {k: v for k, v in record.items() if k != "mac"}
        expected = hmac.new(
            key,
            json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if str(record.get("mac") or "") != expected:
            return [], False
        records.append(record)
        previous_digest = _record_digest(record)
    return records, True


def _verified_bypass_head() -> tuple[int, str] | None:
    """The anchor's MAC-verified (head_seq, head_hash), or None when it is missing, unreadable,
    malformed or fails its MAC. None means the acknowledged head is NOT provable -- callers
    decide what that forbids; nothing may treat it as zero history."""
    anchor = _bypass_revocations_head_path()
    if anchor is None or not anchor.exists():
        return None
    try:
        head = json.loads(anchor.read_text(encoding="utf-8"))
    except Exception:
        return None
    key = _bypass_store_key()
    if key is None or not isinstance(head, dict) or head.get("schema") != _BYPASS_STORE_SCHEMA:
        return None
    try:
        head_seq = int(head.get("head_seq"))
    except (TypeError, ValueError):
        return None
    head_hash = str(head.get("head_hash") or "")
    expected = hmac.new(key, f"{head.get('schema')}|{head_seq}|{head_hash}".encode(), hashlib.sha256).hexdigest()
    if str(head.get("mac") or "") != expected:
        return None
    return head_seq, head_hash


def _bypass_store_lifecycle() -> str:
    """The AUTHENTICATED lifecycle of the permission mirror.

    'established' -- a stamped envelope whose current-form MAC verifies: this store was written
    by journal-aware code, so its journal and anchor are REQUIRED history. 'legacy' -- an
    unstamped envelope with the legacy grants-only MAC: the store predates the journal, and no
    journalled revocation can exist for it. 'fresh' -- no mirror at all: nothing is established,
    so a chain may start. 'unauthenticated' -- a mirror whose bytes do not verify: what history
    this store requires is unknowable, and no path may guess; restore quarantines such a mirror
    on its own MAC check, and the mutation paths refuse.

    This is the authority for initialization eligibility: an empty or missing journal proves
    nothing on its own, because the mirror beside it may already REQUIRE the history that is
    gone."""
    path = _bypass_grants_path()
    if path is None or not path.exists():
        return "fresh"
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "unauthenticated"
    if not isinstance(envelope, dict) or envelope.get("schema") != _BYPASS_STORE_SCHEMA:
        return "unauthenticated"
    key = _bypass_store_key()
    if key is None:
        return "unauthenticated"
    grants = envelope.get("grants")
    grants = grants if isinstance(grants, dict) else {}
    stamped = envelope.get("revocation_journal") is True
    expected = (
        hmac.new(key, _envelope_canonical_bytes(grants, journal_required=True), hashlib.sha256).hexdigest()
        if stamped
        else hmac.new(key, _grant_canonical_bytes(grants), hashlib.sha256).hexdigest()
    )
    if str(envelope.get("mac") or "") != expected:
        return "unauthenticated"
    return "established" if stamped else "legacy"


def _bypass_revocation_history_state() -> tuple[bool, list[dict[str, Any]], str]:
    """Whether the ESTABLISHED revocation history is complete, its records, and why not.

    Established means: the journal's chain verifies AND reaches the authenticated anchor head.
    Every path that MUTATES the history -- appending a record, re-writing the anchor, persisting
    the store -- must pass this first: appending to or re-anchoring truncated history signs a
    replacement head over lost acknowledged revocations and legitimizes them.

    Initialization eligibility is bound to the AUTHENTICATED store lifecycle
    (`_bypass_store_lifecycle`), never to the journal's own emptiness: a store whose stamped
    mirror authenticates is ESTABLISHED and must always find its history and anchor -- an empty
    journal or a missing anchor there is LOST REQUIRED HISTORY, not a first chain. Only a FRESH
    store (no mirror at all) may start a chain from nothing, and only a LEGACY store (unstamped
    mirror, legacy MAC) may treat an absent journal as "no journalled revocations exist". An
    unauthenticated mirror makes the store's requirements unknowable, and every path refuses.
    """
    journal = _bypass_revocations_path()
    if journal is None:
        return False, [], "the revocation history location is unavailable"
    lifecycle = _bypass_store_lifecycle()
    if lifecycle == "unauthenticated":
        return False, [], "the permission mirror does not authenticate, so required history is unknowable"
    if not journal.exists():
        if lifecycle == "established":
            return False, [], "the journal is missing while the authenticated mirror requires journal history"
        anchor = _bypass_revocations_head_path()
        anchored = _verified_bypass_head()
        if anchored is None:
            if anchor is not None and anchor.exists():
                return False, [], "the anchor exists but does not verify"
            return True, [], ""  # fresh or legacy store: no journal ever existed for it
        head_seq, head_hash = anchored
        if head_seq > 0 or head_hash:
            return False, [], "the journal is missing while its anchor still acknowledges revocations"
        return True, [], ""  # a verified zero-history anchor acknowledges nothing
    records, chain_ok = _bypass_journal_chain()
    if not chain_ok:
        return False, [], "the journal chain does not verify"
    anchor = _bypass_revocations_head_path()
    anchored = _verified_bypass_head()
    if anchored is None:
        if anchor is not None and not anchor.exists() and not records:
            if lifecycle == "established":
                # An empty journal and no anchor beside a stamped, authenticated mirror: the
                # store's required history is missing, not being initialized.
                return False, [], "the anchor is missing while the authenticated mirror requires journal history"
            # Fresh or legacy store: the chain proves NOTHING was ever recorded, so this is
            # initialization or migration, not truncation.
            return True, records, ""
        return False, [], "the anchor is missing or does not verify"
    head_seq, head_hash = anchored
    if head_seq > len(records):
        return False, [], "the journal no longer reaches its acknowledged head"
    if head_seq == 0 and head_hash:
        return False, [], "the acknowledged head digest does not match an empty history"
    if head_seq > 0 and _record_digest(records[head_seq - 1]) != head_hash:
        return False, [], "the record at the acknowledged head is not the acknowledged one"
    return True, records, ""


def _bypass_revocation_history() -> tuple[set[str], bool]:
    """The revoked tokens the journal proves, and whether that proof is COMPLETE.

    Per-record MACs prove each surviving record authentic; the chain (sequence numbers linked by
    digests) proves order and continuity; the MAC'd anchor proves the acknowledged prefix is all
    still here. All three must hold: a journal shorter than the anchored head, or whose record at
    the anchored position has a different digest, has LOST acknowledged revocations, and
    "which grants were revoked?" has no answer -- least of all the empty set, which is exactly
    what truncation wants to hear.

    Records beyond the anchored head are authentic-but-unacknowledged (a crash between the
    record's fsync and the anchor's): they are counted as revocations anyway -- the fail-safe
    direction, since a record that never landed cannot resurrect anything and one that did must
    not. The supported boundary, stated plainly: this detects deletion, truncation, reordering
    and forgery of journal records by any party that cannot produce valid MACs under the store
    key. It does NOT detect a filesystem-level rollback that restores an OLD journal AND an OLD
    anchor together (there is no monotonic reference outside these files), and it is no defense
    against code running with the owner's identity, which can read the key and rewrite history
    exactly as it can rewrite the mirror. No rollback resistance beyond that is claimed.
    """
    established, records, _reason = _bypass_revocation_history_state()
    if not established:
        return set(), False
    return {str(record.get("token") or "") for record in records}, True


def _append_bypass_revocation_journal_locked(token: str) -> None:
    """Durably record ONE revocation: a chained, MAC'd journal line, then the anchor.

    The fallback when the mirror's atomic rewrite fails: a rewrite must replace the whole file
    through the directory, while the record append and the anchor rewrite need only file-write
    permission. The record is fsync'd first, the anchor second; a crash between the two leaves
    one authentic unacknowledged record, which restore counts as revoked (fail-safe).

    The ESTABLISHED history must already be complete before anything is written: appending to
    truncated history would sign a replacement head over lost acknowledged revocations and
    legitimize them, so an unverifiable history is refused -- named -- rather than extended.
    """
    path = _bypass_revocations_path()
    if path is None:
        raise BypassStoreError("the bypass revocation journal location is unavailable")
    key = _bypass_store_key()
    if key is None:
        raise BypassStoreError("the bypass grant integrity key is unavailable")
    established, records, reason = _bypass_revocation_history_state()
    if not established:
        raise BypassStoreError(
            f"the existing revocation history is unverifiable ({reason}); refusing to extend it"
        )
    previous_digest = _record_digest(records[-1]) if records else ""
    record = {
        "schema": _BYPASS_STORE_SCHEMA,
        "seq": len(records) + 1,
        "prev": previous_digest,
        "token": str(token or ""),
        "revoked_at": time.time(),
    }
    mac = hmac.new(
        key,
        json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({**record, "mac": mac}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        raise BypassStoreError(f"the bypass revocation journal could not be written: {exc}") from exc
    # Only after the record is durable does the anchor advance to cover it.
    _write_bypass_revocation_head_locked(record["seq"], _record_digest({**record, "mac": mac}))


def _record_bypass_revocation_durably_locked(tokens: Any) -> None:
    """Record revocations durably: mirror rewrite first, journal append as the fallback.

    When the mirror rewrite succeeds the revoked grants leave the file entirely (the historical
    fast path, unchanged). When it fails, the journal lines alone still hold the revocations, and
    `restore_persisted_bypass_grants` honors them -- a restart cannot revive a grant. Only when
    BOTH writes fail does this raise, leaving the caller the named non-durable state; the failure
    raised is the MIRROR's, because that is the store the operator was told about. A partial
    journal fallback (some lines written, then the disk fails) is still correct: every line is
    independently valid, and what did not land was never durable.
    """
    token_list = [str(token or "") for token in ([tokens] if isinstance(tokens, str) else tokens) if str(token or "")]
    try:
        _persist_bypass_grants_locked()
        return
    except BypassStoreError as mirror_error:
        for token in token_list:
            try:
                _append_bypass_revocation_journal_locked(token)
            except BypassStoreError:
                raise mirror_error from None


#: One-shot loader flag for the until-off grant mirror, same pattern as `_PERSISTED_APPROVALS_RESTORED`.
_BYPASS_GRANTS_RESTORED = False


def _ensure_bypass_grants_restored() -> None:
    global _BYPASS_GRANTS_RESTORED
    if _BYPASS_GRANTS_RESTORED:
        return
    _BYPASS_GRANTS_RESTORED = True
    try:
        restore_persisted_bypass_grants()
    except Exception:
        return


def _valid_until_off_grant_shape(token: str, grant: Any) -> bool:
    """Structural law for a RESTORED grant. `until_off=true` alone proves nothing.

    A JSON object saying until_off is not evidence of authority: the entry must carry the exact
    shape an activation mints -- chat-scoped, no timer, a real session id, and an absolute
    workspace root -- and its key must equal its own token. Anything else restores as nothing.
    """
    if not isinstance(grant, dict):
        return False
    if str(grant.get("token") or "") != str(token or ""):
        return False
    if grant.get("until_off") is not True or grant.get("expires_at") is not None:
        return False
    if str(grant.get("scope") or "") != "session":
        return False
    if not str(grant.get("session_id") or "").strip():
        return False
    root = str(grant.get("workspace_root") or "").strip()
    if not root or not os.path.isabs(root):
        return False
    if not isinstance(grant.get("issued_at"), (int, float)):
        return False
    return isinstance(grant.get("revoked"), bool)


def restore_persisted_bypass_grants() -> int:
    """Load until-off chat grants from disk into memory, after full validation.

    Fail-closed at every doubt: a file that does not parse, does not carry a matching integrity
    MAC, or carries a structurally invalid entry restores NOTHING from that file (it is moved
    aside as `.corrupt-<ts>` so it cannot keep poisoning restarts). An entry whose chat is gone,
    deleted, no longer project-bound, or whose project root no longer equals the grant's bound
    workspace is dropped: authority that outlived its binding is not restored. Revoked entries on
    disk stay revoked -- including grants the mirror still holds live but the revocation journal
    names: those restore as explicitly revoked, never executable.

    The revocation journal is part of the store's truth, not an optional hint. An envelope
    written by journal-aware code carries the `revocation_journal` stamp -- INSIDE the envelope
    MAC, so a current store with its stamp stripped matches neither the current MAC form nor the
    legacy one and fails closed; it is never accepted as a legacy store. For a stamped store the
    journal and its completeness anchor are REQUIRED history: an unreadable journal, a chain that
    does not verify, an anchor that does not match the records, a journal shorter than its
    anchored head, or either file missing altogether means this store's revocation past is
    UNVERIFIABLE, and the answer to "which grants were revoked?" is NOT the empty set -- it is
    "unknown", so NOTHING from that store restores (the files are quarantined, same as a corrupt
    mirror). An UNstamped envelope carrying the legacy grants-only MAC is a pre-journal store:
    no journalled revocation can exist for it, and it restores exactly as it always did.
    """
    import json as _json

    with _LOCK:
        path = _bypass_grants_path()
        if path is None or not path.exists():
            return 0
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            return 0
        try:
            envelope = _json.loads(raw or "{}")
        except Exception:
            envelope = None
        grants: dict[str, Any] = {}
        mac_hex = ""
        if isinstance(envelope, dict):
            maybe = envelope.get("grants")
            if isinstance(maybe, dict):
                grants = maybe
            mac_hex = str(envelope.get("mac") or "")
        key = _bypass_store_key()
        journal_required = bool(isinstance(envelope, dict) and envelope.get("revocation_journal") is True)
        # The MAC form follows the claim the envelope makes. A stamped envelope must carry the
        # CURRENT MAC, which covers the security-relevant metadata (schema + journal requirement)
        # beside the grants; an unstamped one must carry the LEGACY grants-only MAC. A current
        # store with its stamp stripped presents the current MAC to the legacy check and fails
        # it -- a modified store is never accepted as legacy.
        expected_mac = (
            hmac.new(key, _envelope_canonical_bytes(grants, journal_required=True), hashlib.sha256).hexdigest()
            if journal_required
            else hmac.new(key, _grant_canonical_bytes(grants), hashlib.sha256).hexdigest()
        )
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema") != _BYPASS_STORE_SCHEMA
            or key is None
            or mac_hex != expected_mac
        ):
            # Corrupt or tampered: quarantine so the NEXT restart does not read the same bytes,
            # and restore nothing. Dropping is safe -- the owner re-approves; forging is not.
            with contextlib.suppress(OSError):
                path.rename(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
            return 0

        def _quarantine_store() -> None:
            for doomed in (path, _bypass_revocations_path(), _bypass_revocations_head_path()):
                if doomed is None:
                    continue
                with contextlib.suppress(OSError):
                    doomed.rename(doomed.with_name(f"{doomed.name}.corrupt-{int(time.time())}"))

        journal_path = _bypass_revocations_path()
        journal_exists = journal_path is not None and journal_path.exists()
        journal_required = bool(isinstance(envelope, dict) and envelope.get("revocation_journal") is True)
        if journal_exists:
            journal_revoked, trusted = _bypass_revocation_history()
            if not trusted:
                # Unverifiable revocation history: treating it as "no revocations" is exactly the
                # resurrection this store refuses, so nothing from this store becomes executable
                # and the bytes are quarantined instead of re-poisoning the next restart.
                _quarantine_store()
                return 0
        elif journal_required:
            # A journal-aware store whose journal is gone has lost required history -- deletion
            # and tampering are indistinguishable here, and both must fail closed.
            _quarantine_store()
            return 0
        else:
            # A legacy pre-journal envelope: no journalled revocation can exist for it.
            journal_revoked = set()
        loaded = 0
        for token, grant in dict(grants).items():
            if not _valid_until_off_grant_shape(str(token), grant):
                continue
            session_id = str(grant.get("session_id") or "").strip()
            root, reason = _authoritative_chat_workspace(session_id)
            if reason != "project" or _realpath_str(root) != _realpath_str(str(grant.get("workspace_root"))):
                # The chat was deleted or moved to another workspace while the process was down:
                # the binding the grant was minted under no longer holds.
                continue
            entry = dict(grant)
            entry["expires_at"] = None
            entry.setdefault("project_id", "")
            entry.setdefault("task_id", "")
            if not entry.get("revoked") and str(token) in journal_revoked:
                # The grant was revoked while the mirror could not be rewritten; the revocation
                # was recorded in the journal instead. The mirror still holds the live grant, so
                # WITHOUT this line a restart would silently make the revoked authority
                # executable again. It restores as explicitly revoked, never live.
                entry["revoked"] = True
            if entry.get("revoked"):
                _BYPASS_GRANTS.setdefault(str(token), entry)
                continue
            _BYPASS_GRANTS.setdefault(str(token), entry)
            loaded += 1
        return loaded


def _realpath_str(value: str) -> str:
    try:
        return str(Path(str(value or "")).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(value or "")


def _authoritative_chat_workspace(session_id: str) -> tuple[str, str]:
    try:
        from core.context_namespace import authoritative_chat_workspace

        return authoritative_chat_workspace(session_id)
    except Exception:
        return "", "unreadable"


def validate_bypass_grant(
    token: str,
    *,
    session_id: str,
    project_id: str = "",
    task_id: str = "",
    workspace_root: str = "",
) -> dict[str, Any] | None:
    clean_token = str(token or "").strip()
    if not clean_token:
        return None
    _ensure_bypass_grants_restored()
    with _LOCK:
        grant = dict(_BYPASS_GRANTS.get(clean_token) or {})
    if not grant or grant.get("revoked"):
        return None
    if grant.get("until_off"):
        # No timer to expire; the binding IS the lifetime. Chat identity, the trusted
        # workspace, AND the chat's CURRENT server-owned project binding are re-checked on
        # every consult, so navigating the chat to a different workspace (or reconstructing
        # the task under another project) never carries an old grant with it.
        if str(grant.get("session_id") or "") != str(session_id or ""):
            return None
        bound_root = str(grant.get("workspace_root") or "").strip()
        call_root = str(workspace_root or "").strip()
        if not bound_root or not call_root or not _path_inside(call_root, bound_root):
            return None
        authoritative, reason = _authoritative_chat_workspace(str(session_id or ""))
        if reason != "project" or _realpath_str(authoritative) != _realpath_str(bound_root):
            # The chat is deleted, unbound, or bound elsewhere NOW: the grant's binding is
            # gone, and a stale client-supplied root is not authority that can replace it.
            return None
        return grant
    if float(grant.get("expires_at") or 0) <= time.time():
        return None
    scope = str(grant.get("scope") or "task")
    if scope in {"task", "session"} and str(grant.get("session_id") or "") != str(session_id or ""):
        return None
    if scope == "task" and str(grant.get("task_id") or "") != str(task_id or ""):
        return None
    if scope == "project" and (
        not str(project_id or "").strip()
        or str(grant.get("project_id") or "") != str(project_id or "").strip()
    ):
        return None
    return grant


def revoke_bypass_grant(token: str) -> bool:
    clean_token = str(token or "").strip()
    store_error: BypassStoreError | None = None
    with _LOCK:
        if clean_token not in _BYPASS_GRANTS:
            return False
        _BYPASS_GRANTS[clean_token]["revoked"] = True
        if _BYPASS_GRANTS[clean_token].get("until_off"):
            try:
                _record_bypass_revocation_durably_locked(clean_token)
            except BypassStoreError as exc:
                # The in-memory revocation stands for this process; neither the mirror rewrite
                # nor the journal append could record it, so the failure is NAMED rather than
                # swallowed -- after a restart the grant can come back, and the caller must be
                # able to say so.
                store_error = exc
        for session_id, state in list(_ACTIVE_MODES.items()):
            if state.get("bypass_token") == clean_token:
                _ACTIVE_MODES[session_id] = {
                    **state,
                    "mode": OperatingMode.MANUAL.value,
                    "label": MODE_LABELS[OperatingMode.MANUAL],
                    "revision": int(state.get("revision") or 0) + 1,
                    "bypass_token": "",
                    "updated_at": time.time(),
                }
    if store_error is not None:
        raise store_error
    return True


def revoke_session_bypass_grants(session_id: str) -> int:
    """Revoke every bypass grant bound to one chat. The chat-delete owner calls this so no
    authority outlives its own session; returning to Manual via `set_active_mode` revokes the
    session's ACTIVE token through the same store."""
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return 0
    revoked = 0
    store_error: BypassStoreError | None = None
    with _LOCK:
        for _token, grant in list(_BYPASS_GRANTS.items()):
            bound = str(grant.get("session_id") or "")
            if bound and bound == clean_session and not grant.get("revoked"):
                grant["revoked"] = True
                revoked += 1
        if revoked:
            try:
                _record_bypass_revocation_durably_locked(
                    [
                        token
                        for token, grant in _BYPASS_GRANTS.items()
                        if grant.get("session_id") == clean_session
                        and grant.get("until_off")
                        and grant.get("revoked")
                    ]
                )
            except BypassStoreError as exc:
                store_error = exc
            for sid, state in list(_ACTIVE_MODES.items()):
                if sid == clean_session and state.get("bypass_token"):
                    _ACTIVE_MODES[sid] = {
                        **state,
                        "mode": OperatingMode.MANUAL.value,
                        "label": MODE_LABELS[OperatingMode.MANUAL],
                        "revision": int(state.get("revision") or 0) + 1,
                        "bypass_token": "",
                        "updated_at": time.time(),
                    }
    if store_error is not None:
        raise store_error
    return revoked


# `find` primaries that turn a listing into a mutation (delete a match, exec/ok a command on it,
# write matches to a file). This is the single source of truth for that flag set -- other gates
# (core.execution_gate.ExecutionGate) must call `command_is_read_only`/`command_actions` below
# instead of re-deriving their own copy of this list. A duplicated copy is how `find ... -delete`
# was once classified as read-only by execution_gate's own logic while this module already knew
# better (confirmed red-team finding, 2026-08-04).
FIND_MUTATING_FLAGS = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint"})


def _command_actions(command: str) -> set[PermissionAction]:
    command_text = str(command or "")
    # Commands execute through a shell-capable sandbox. A read command followed by redirection,
    # substitution, a pipeline, or another command is no longer a bounded read.
    if any(marker in command_text for marker in ("\n", ";", "&&", "||", "|", ">", "<", "`", "$(")):
        return {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}
    try:
        argv = shlex.split(command_text, posix=True)
    except ValueError:
        return {PermissionAction.UNKNOWN_SIDE_EFFECT}
    if not argv:
        return {PermissionAction.UNKNOWN_SIDE_EFFECT}
    base = Path(str(argv[0])).name.lower()
    if base == "git":
        sub = str(argv[1] if len(argv) > 1 else "").lower()
        if sub in {"status", "diff", "show", "log", "rev-parse", "grep", "ls-files", "ls-tree", "branch"}:
            # git branch with any positional target or mutation flag is not a read.
            tail = [str(item) for item in argv[2:]]
            if sub != "branch" or all(item.startswith("-") and item not in {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--force"} for item in tail):
                return {PermissionAction.RUN_SAFE_COMMANDS}
        if sub == "commit":
            return {PermissionAction.GIT_COMMIT}
        if sub == "push":
            return {PermissionAction.GIT_PUSH}
        if sub in {"merge", "rebase", "cherry-pick"}:
            return {PermissionAction.GIT_MERGE_REBASE}
        if sub in {"reset", "clean", "checkout", "restore", "stash", "update-ref", "reflog", "worktree"}:
            return {PermissionAction.GIT_RESET_CLEAN}
        return {PermissionAction.UNKNOWN_SIDE_EFFECT}
    if base in {"pip", "pip3", "uv", "npm", "pnpm", "yarn", "cargo", "gem", "brew", "apt", "apt-get"}:
        lowered = " ".join(str(item).lower() for item in argv[1:])
        if any(marker in lowered.split() for marker in ("install", "add", "update", "upgrade")):
            return {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}
    if base == "find" and any(str(item).lower() in FIND_MUTATING_FLAGS for item in argv[1:]):
        return {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}
    if base in {"ls", "pwd", "cat", "head", "tail", "wc", "rg", "grep", "find"}:
        return {PermissionAction.RUN_SAFE_COMMANDS}
    if base == "sed" and not any(str(item).startswith("-i") or str(item) in {"w", "W"} for item in argv[1:]):
        return {PermissionAction.RUN_SAFE_COMMANDS}
    return {PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}


def command_is_read_only(command: str) -> bool:
    """Public entry point: is this shell command string a bounded, side-effect-free read?

    Other permission/execution gates that need a read-only verdict on a raw command string
    (e.g. `core.execution_gate.ExecutionGate._is_read_only_command`) must call this rather than
    maintaining a second, independent classifier that can silently drift out of sync with the one
    actually enforced by `decide_tool_call`.
    """
    return _command_actions(command) == {PermissionAction.RUN_SAFE_COMMANDS}


_WORKSPACE_WRITE_SIDE_EFFECTS = frozenset(
    {"workspace_write", "workspace_delete", "filesystem_write", "destructive"}
)


def _declares_workspace_write(intent: str) -> bool:
    """Whether this tool's own contract says it changes the workspace."""

    return side_effect_class_for_intent(intent) in _WORKSPACE_WRITE_SIDE_EFFECTS


def _web_tool_is_public_read_only(name: str) -> bool:
    """Whether this `web.*` intent's own contract declares it public, unauthenticated, read-only.

    Driven entirely by the tool's declared `side_effect_class` -- never by matching a word like
    "weather" or "price" in the user's prompt, which is a keyword exception, not a capability
    policy. Deliberately scoped to the `web.*` family (search/research/fetch/browser-render), not
    a blanket `side_effect_class == "read_only"` rule: `email.read` and `x.trending` ALSO declare
    `side_effect_class="read_only"`, but they read a locally-configured, authenticated
    account/bearer token, not public data -- a blanket rule would have exempted those too,
    directly against the "authenticated or private data still requires approval" contract.
    `browser.*` (arbitrary browser automation, not pure fetch/search) is excluded the same way, by
    never reaching this function -- see its caller.
    """
    return side_effect_class_for_intent(name) == "read_only"


def _declared_write_target_exists(
    arguments: dict[str, Any] | None, source_context: dict[str, Any] | None
) -> bool:
    """Whether a declared write's target already exists on disk.

    Resolved ONLY against the trusted `source_context` root, never against a model-supplied
    argument -- the same rule the builtin derivation follows, and for the same reason: letting a
    caller steer this check lets it steer create-vs-overwrite, and therefore whether Auto allows
    the write unprompted.
    """

    args = dict(arguments or {})
    path_value = str(args.get("path") or args.get("target") or args.get("destination") or "").strip()
    if not path_value:
        return False
    root_value = _trusted_write_root(source_context)
    try:
        candidate = Path(path_value) if os.path.isabs(path_value) else (
            Path(root_value, path_value) if root_value else None
        )
        return bool(candidate and candidate.exists())
    except (OSError, ValueError):
        return False


def actions_for_tool(
    intent: str,
    arguments: dict[str, Any] | None = None,
    source_context: dict[str, Any] | None = None,
    *,
    code_task_id: str = "",
) -> tuple[PermissionAction, ...]:
    name = str(intent or "").strip().lower()
    args = dict(arguments or {})
    actions: set[PermissionAction] = set()

    if name == "code.task.step":
        # The wrapper journals and stages one inner action; it does not itself
        # create files or execute a command. Keep the inner action's actual gate --
        # and the task the step names, which is the only task whose approved
        # proposal may bind that inner action -- evaluated where the task writer
        # actually runs it: the task's own workspace, never the caller's roots.
        inner = str(args.get("intent") or "").strip().lower()
        inner_args = args.get("arguments", {})
        if not inner or inner.startswith("code.task.") or not isinstance(inner_args, dict):
            return (PermissionAction.UNKNOWN_SIDE_EFFECT,)
        return actions_for_tool(
            inner,
            inner_args,
            _step_execution_context(source_context, args),
            code_task_id=str(args.get("task_id") or "").strip(),
        )

    # A contract may DECLARE what it does. Everything below derives permissions by matching on the
    # intent STRING — `workspace.`/`machine.` prefixes, then substrings like "read" and "write" —
    # which no new family and no third-party tool can participate in. The pdf.* and skill.* families
    # fell straight through to UNKNOWN_SIDE_EFFECT, which denies in Auto: a read-only PDF reader
    # would have been unusable without an approval prompt on every call. `permission_actions` was
    # already on RuntimeToolContract for this and had no reader.
    try:
        # The ONE registry (builtins plus registered plugin/MCP contracts): a plugin manifest's or
        # an MCP trust pin's declared actions are validated against their class at load, so reading
        # them here is reading a checked declaration, not trusting a string.
        from core.tool_registry import registry_map

        declared = tuple(getattr(registry_map().get(name), "permission_actions", ()) or ())
    except Exception:
        declared = ()
    if declared:
        # A DECLARATION IS NOT A GRANT. The early return below skips every derivation, including
        # the argument-sensitive ones (create-vs-overwrite, for instance), so a third-party
        # manifest could pick the cheaper matrix row for what it actually does. For any contract
        # whose source is not `builtin`, the declaration is unioned with the floor its own
        # declared side-effect class implies; the matrix takes the strictest effect over the set,
        # so a manifest can be MORE specific and can never be cheaper than the class it declared.
        try:
            from core.plugin_lifecycle import effective_permission_actions

            contract_for_actions = registry_map().get(name)
            declared = effective_permission_actions(
                side_effect_class=str(getattr(contract_for_actions, "side_effect_class", "") or ""),
                declared=tuple(declared),
                source=str(getattr(contract_for_actions, "source", "builtin") or "builtin"),
                target_exists=_declared_write_target_exists(args, source_context),
            )
        except Exception:
            # A floor that cannot be computed must not silently widen anything; keep what was
            # declared and let the rest of the pipeline decide.
            pass
        resolved = tuple(
            PermissionAction(value) for value in declared
            if value in {item.value for item in PermissionAction}
        )
        if resolved:
            return resolved

    if name == "sandbox.run_command":
        actions.update(_command_actions(str(args.get("command") or "")))
    elif name.startswith("workspace.") or name.startswith("machine."):
        # A tool that DECLARES it writes can never be classified into a read-only action, whatever
        # its name happens to contain. The read-verb list below includes "diff", and
        # `workspace.apply_unified_diff` contains it — so the patch tool matched the read branch and
        # was granted `read_files`. Measured: in `plan` mode, advertised as strictly read-only,
        # `workspace.write_file` was DENIED while `workspace.apply_unified_diff` was ALLOWED, and a
        # model could rewrite any file in the workspace by emitting a patch instead of a write.
        #
        # The tools already carry the answer: every one of write_file, replace_in_file and
        # apply_unified_diff declares `side_effect_class="workspace_write"`. Reading the declaration
        # is what the side-effect contract exists for; pattern-matching the name second-guesses it.
        declared_write = _declares_workspace_write(name)
        if not declared_write and any(part in name for part in ("list", "tree", "search", "read", "inspect", "find", "disk_usage", "status", "diff", "summary")):
            actions.add(PermissionAction.LIST_DIRECTORIES if any(part in name for part in ("list", "tree", "find")) else PermissionAction.READ_FILES)
        elif any(part in name for part in ("write", "replace", "apply_unified_diff", "ensure_directory")):
            path_value = str(args.get("path") or "").strip()
            # Resolve the target with the WRITER's own authority
            # (`core.runtime_execution_tools.resolve_write_target`): the machine's safe home
            # directories for `machine.*`; for `workspace.*`, the confined, symlink-resolved path
            # under the root the writer itself prefers (`workspace`, then `workspace_root`, then the
            # configured workspace). Existence decided anywhere else classifies a different file than
            # the one written, and an overwrite of a real file reads as a create that Auto mode
            # allows unprompted: the machine root was checked against `workspace_root` (red-team
            # finding, 2026-08-04), and a context carrying both roots was checked against the one the
            # writer ignores (revision-5 review). Only the trusted `source_context` names a root --
            # never a model-supplied argument, which this check once read first (red-team finding,
            # 2026-08-04).
            target: Path | None = None
            if path_value:
                try:
                    from core.runtime_execution_tools import resolve_write_target

                    target = resolve_write_target(name, path_value, source_context)
                except Exception:
                    target = None
                if target is None and not name.startswith("machine."):
                    # The writer refuses this name (it resolves outside the workspace), so nothing can
                    # be written through it -- but it is not a create either: an existing file behind
                    # the name, resolved under the writer's root, keeps the overwrite prompt it always
                    # had, and the writer still refuses the write if it is approved.
                    target = _resolve_against(_trusted_write_root(source_context), path_value)
            exists = bool(target and target.exists())
            if name in {"workspace.write_file", "machine.write_file"} and exists:
                # A full-file write over an existing file is a VERIFIED REPLACE only when an
                # OPERATOR-approved code proposal binds its complete action identity: this
                # exact tool, this destination (resolved as the writer resolves it, in the
                # owning task's workspace), the same prior bytes and replacement content, a
                # live owning task of this session, unconsumed (read from the journal by
                # core.code_assistant, never from the conversation). Possession of the current
                # hash alone is concurrency evidence, NOT approval of the new content; the same
                # relative path in another workspace, or on the machine's home directories, is
                # a different file and keeps prompting, exactly as on the pinned base.
                expected_hash = str(args.get("expected_hash") or "").strip().lower()
                content_value = str(args.get("content") or "")
                path_value = str(args.get("path") or "").strip()
                proposal_bound = False
                if expected_hash:
                    try:
                        from core.code_assistant.task_runtime import approved_replacement_matches

                        proposal_bound = approved_replacement_matches(
                            source_context,
                            intent=name,
                            path=path_value,
                            content=content_value,
                            expected_hash=expected_hash,
                            task_id=code_task_id,
                            # A task step is admitted by its task and its writer re-checks the reviewed
                            # bytes inside the flight recorder, refusing a stale base there (journaled);
                            # a direct write has no such admission, so its live bytes are checked here.
                            reviewed_bytes=not code_task_id,
                        )
                    except Exception:
                        proposal_bound = False
                if proposal_bound:
                    actions.add(PermissionAction.MODIFY_FILES)
                else:
                    actions.add(PermissionAction.OVERWRITE_EXISTING_FILES)
            else:
                actions.add(PermissionAction.MODIFY_FILES if exists or "replace" in name or "apply_unified_diff" in name else PermissionAction.CREATE_FILES)
        elif "move_path" in name:
            actions.update({PermissionAction.MODIFY_FILES, PermissionAction.DELETE_FILES})
        elif any(part in name for part in ("delete", "remove", "rollback")):
            actions.add(PermissionAction.DELETE_FILES)
        elif "run_test" in name or "run_lint" in name or "run_formatter" in name:
            actions.add(PermissionAction.RUN_SIDE_EFFECTING_COMMANDS)
        else:
            actions.add(PermissionAction.UNKNOWN_SIDE_EFFECT)
    elif (name.startswith("web.") or name == "browser.render") and _web_tool_is_public_read_only(name):
        # `browser.render` is the renamed `web.browser_render` (one contracted name
        # owns the lane): same public-page render surface, same read-only contract,
        # so it keeps the public-read class instead of falling into the generic
        # browser-automation branch below.
        actions.add(PermissionAction.PUBLIC_READ_ONLY_RETRIEVAL)
    elif name.startswith("web.") or name.startswith("browser."):
        actions.update({PermissionAction.USE_NETWORK, PermissionAction.USE_BROWSER})
    elif name.startswith("web0."):
        if name == "web0.publish":
            actions.update({PermissionAction.USE_NETWORK, PermissionAction.DEPLOY})
        elif name in {"web0.open_builder_draft", "web0.create_project"}:
            actions.add(PermissionAction.CREATE_FILES)
        else:
            actions.add(PermissionAction.MODIFY_FILES)
    elif name in {"email.read", "email.open", "email.draft.reconcile", "x.trending"}:
        actions.update({PermissionAction.USE_NETWORK, PermissionAction.ACCESS_EXTERNAL_PROVIDERS})
    elif name in {"email.send", "email.reply", "email.draft.send"} or name.startswith(("discord.", "telegram.")):
        actions.update({PermissionAction.USE_NETWORK, PermissionAction.SEND_EXTERNAL_MESSAGES})
    elif name in {"email.draft.save", "email.draft.get", "email.draft.approve"}:
        # Reviewable draft state under the runtime home: no file, no network, no send. It is the
        # RECORD of a review, not an effect — the send itself is email.draft.send above, and an
        # empty action set adds nothing to gate in any mode (the matrix sees no action).
        pass
    elif name == "marketplace.search_listings":
        actions.update({PermissionAction.USE_NETWORK, PermissionAction.ACCESS_EXTERNAL_PROVIDERS})
    elif name.startswith(("wallet.", "pay.", "marketplace.purchase")):
        actions.add(PermissionAction.FINANCIAL_ACTION)
    elif "publish" in name or name.startswith("deploy."):
        actions.update({PermissionAction.USE_NETWORK, PermissionAction.DEPLOY})
    elif name.startswith("provider."):
        actions.add(PermissionAction.CHANGE_PROVIDER_CONFIGURATION)
    elif name.startswith("settings.") or name.startswith("set."):
        actions.add(PermissionAction.CHANGE_SETTINGS)
    elif name.startswith("secret.") or name.startswith("credential."):
        actions.add(PermissionAction.ACCESS_SECRETS)
    elif name.startswith("operator."):
        if name in {"operator.inspect_disk_usage", "operator.inspect_processes", "operator.inspect_services", "operator.list_tools"}:
            actions.add(PermissionAction.READ_FILES)
        elif name in {"operator.check_availability", "operator.list_calendars", "operator.inspect_calendar_event"}:
            # Reading the configured provider's calendars: a governed network read, the
            # same class the web read lane carries -- not a side-effecting command.
            actions.add(PermissionAction.USE_NETWORK)
        elif name in {"operator.find_notes", "operator.show_note"}:
            actions.add(PermissionAction.READ_FILES)
        elif name == "operator.save_note":
            actions.add(PermissionAction.CREATE_FILES)
        elif name in {"operator.propose_calendar_event", "operator.update_calendar_event"}:
            # Creating/updating an event on an external provider's calendar. The mode
            # matrix governs the tool call; the provider lane adds its own explicit
            # approval turn on top, so no mode silently books.
            actions.add(PermissionAction.ACCESS_EXTERNAL_PROVIDERS)
        elif "cleanup" in name:
            actions.add(PermissionAction.DELETE_FILES)
        elif "schedule" in name:
            actions.add(PermissionAction.SEND_EXTERNAL_MESSAGES)
        else:
            actions.add(PermissionAction.RUN_SIDE_EFFECTING_COMMANDS)
    elif name.startswith("hive."):
        actions.add(PermissionAction.USE_NETWORK)
        if any(part in name for part in ("create", "publish", "update", "delete", "claim", "submit")):
            actions.add(PermissionAction.ACCESS_EXTERNAL_PROVIDERS)
    elif name.startswith("orchestration."):
        actions.add(PermissionAction.RUN_SIDE_EFFECTING_COMMANDS)
    elif name.startswith(("image.", "video.")):
        actions.update({PermissionAction.ACCESS_EXTERNAL_PROVIDERS, PermissionAction.USE_NETWORK})
    else:
        # Unknown tools do not inherit an accidental allow from a permissive mode.
        actions.add(PermissionAction.UNKNOWN_SIDE_EFFECT)
    return tuple(sorted(actions, key=lambda item: item.value))


def _path_strings(arguments: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in _PATH_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, (list, tuple)):
            values.extend(str(item) for item in list(value)[:24] if str(item).strip())
        elif value not in (None, "") and not isinstance(value, (dict, bool)):
            values.append(str(value))
    return [item for item in values if item.strip()]


def _resolve_against(root: str, raw_path: str) -> Path | None:
    """Absolute, symlink-resolved target for one path argument, or None when it cannot be resolved."""
    text = str(raw_path or "").strip()
    if not text:
        return None
    try:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            base = str(root or "").strip()
            if not base:
                return None
            candidate = Path(base).expanduser() / candidate
        # ``strict=False`` so a file that does not exist yet still resolves to where it WOULD land,
        # with every symlink on the existing prefix followed (a symlinked parent cannot smuggle a
        # write out of the project root).
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _resolved_targets(arguments: dict[str, Any], source_context: dict[str, Any]) -> list[str]:
    root = _trusted_write_root(source_context)
    out: list[str] = []
    for raw in _path_strings(arguments):
        resolved = _resolve_against(root, raw)
        out.append(str(resolved) if resolved is not None else f"?{raw}")
    return sorted(set(out))


# The argument keys that actually determine what lands on disk for each write-type intent. Used
# to bind a pure-write approval to the CONTENT being written (see `_resolved_action_identity`),
# not only its target path -- a bare path+intent match let a replayed approval token be reused
# with entirely different `content`/`old_text`/`new_text`/`patch` and write it silently
# (confirmed red-team finding, 2026-08-04: operator approves a diff showing one thing, the file
# on disk ends up containing something else).
_WRITE_CONTENT_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "workspace.write_file": ("content",),
    "machine.write_file": ("content",),
    "workspace.replace_in_file": ("old_text", "new_text", "replace_all"),
    "workspace.apply_unified_diff": ("patch",),
}


def _write_content_signature(intent: str, arguments: dict[str, Any]) -> str:
    """Hash of the fields that determine what a write-type call actually puts on disk."""
    keys = _WRITE_CONTENT_ARG_KEYS.get(str(intent or "").strip().lower())
    # An unmapped write intent hashes the whole argument dict -- fail closed (byte-exact), not
    # open, for any write-shaped tool this table does not yet know about.
    payload = {key: arguments.get(key) for key in keys} if keys is not None else dict(arguments)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolved_action_identity(
    *,
    intent: str,
    arguments: dict[str, Any],
    actions: tuple[PermissionAction, ...],
    source_context: dict[str, Any],
) -> dict[str, Any]:
    """What the controller resolved this call to: the intent, where it lands, and its side-effect class.

    An approval is granted against THIS, not against the exact argument dict the model happened to
    emit. A paused turn is resumed by re-planning it, and a re-plan of the same edit routinely differs
    by a few bytes of incidental argument bookkeeping — under a whole-arguments-hash fingerprint that
    missed the operator's token and raised a second prompt for an action they had just approved.

    For a PURE file write with a resolved target — the same file, the same task, the same mode
    revision, the same side-effect class, still one-time — the raw argument dict is excluded, but a
    hash of the actual CONTENT being written (`content`/`old_text`+`new_text`/`patch`, per intent) is
    always included: the approval must bind to what gets written, not only to where. For every other
    class the arguments ARE the action — a command string, a URL, a recipient, an amount — so they
    stay in the identity and those approvals remain byte-exact.
    """
    targets = _resolved_targets(arguments, source_context)
    identity: dict[str, Any] = {
        "intent": str(intent or ""),
        "effects": sorted(action.value for action in actions),
        "targets": targets,
    }
    write_only = bool(actions) and set(actions) <= _WRITE_ACTIONS
    if write_only and targets:
        identity["content_hash"] = _write_content_signature(intent, arguments)
    else:
        identity["arguments"] = arguments
    return identity


def _fingerprint(*, session_id: str, task_id: str, identity: dict[str, Any], mode_revision: int) -> str:
    payload = {
        "session_id": str(session_id or ""),
        "task_id": str(task_id or ""),
        "identity": identity,
        "mode_revision": int(mode_revision),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _call_permission_identity(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    session_id: str,
    task_id: str,
    mode_revision: int,
    source_context: dict[str, Any],
) -> tuple[str, tuple[PermissionAction, ...], dict[str, Any], dict[str, Any]]:
    """The exact-call fingerprint, action set, alias-bound arguments and resolved identity.

    ``decide_tool_call`` and the batch-grant builder below both need this. Deriving it twice would
    put a second classifier next to the gate that can disagree with it: the grant would then cover a
    call the gate is about to fingerprint differently (so the operator's click does nothing), or
    cover one the gate classifies as riskier than the batch admits.
    """
    args = bind_known_argument_aliases(
        arguments, input_schema=input_schema_for_intent(str(intent or ""))
    )
    actions = actions_for_tool(intent, args, source_context)
    identity = _resolved_action_identity(
        intent=str(intent or ""),
        arguments=args,
        actions=actions,
        source_context=source_context,
    )
    fingerprint = _fingerprint(
        session_id=session_id,
        task_id=task_id,
        identity=identity,
        mode_revision=mode_revision,
    )
    return fingerprint, actions, args, identity


def _primary_target(arguments: dict[str, Any] | None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    for key in ("path", "file_path", "target_path", "destination", "destination_path"):
        value = str(args.get(key) or "").strip()
        if value:
            return value[:240]
    return ""


def _request_scope_eligible(
    *,
    intent: str,
    arguments: dict[str, Any],
    actions: tuple[PermissionAction, ...],
    source_context: dict[str, Any],
) -> bool:
    """Whether this exact call may ride a bounded request-level batch grant, head or member.

    Four conditions, all required, and the same four for the head as for every member -- one
    predicate rather than two, so the set the operator is shown can never be wider than the set the
    grant admits:

    * the intent is one of ``_REQUEST_SCOPE_INTENTS`` -- named, not inferred from an action class;
    * its resolved action set is still entirely inside ``_WRITE_ACTIONS``, so a tool that gets
      reclassified into something riskier later drops out of the batch without this list changing;
    * it names at least one path -- an unlocated write is never covered;
    * every path it names resolves, with symlinks followed, INSIDE the workspace root. A `..`
      escape, an absolute path elsewhere, or a symlinked parent pointing out of the workspace is
      not a workspace change and does not join a workspace batch.
    """
    if str(intent or "").strip().lower() not in _REQUEST_SCOPE_INTENTS:
        return False
    if not actions or not set(actions).issubset(_WRITE_ACTIONS):
        return False
    configured_root = _trusted_write_root(source_context)
    if not configured_root:
        return False
    workspace_root = _resolve_against("", configured_root)
    if workspace_root is None:
        return False
    raw_paths = _path_strings(arguments)
    if not raw_paths:
        return False
    for raw in raw_paths:
        resolved = _resolve_against(str(workspace_root), raw)
        if resolved is None or not _inside(str(workspace_root), str(resolved)):
            return False
    return True


def _planned_edit_batch(
    *,
    head_intent: str,
    head_fingerprint: str,
    head_actions: tuple[PermissionAction, ...],
    head_arguments: dict[str, Any],
    session_id: str,
    task_id: str,
    mode_revision: int,
    source_context: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...], tuple[dict[str, str], ...]]:
    """The concrete set of calls one "allow all planned changes for this request" grant may cover.

    Bounded three ways, all by construction rather than by wording:

    * The HEAD must itself pass ``_request_scope_eligible``. A batch headed by a command, a delete,
      a push, a payment or a write landing outside the workspace offers no request scope at all.
    * A member joins only on the SAME predicate. Deletes, moves, commands, network sends, git,
      settings, secrets, financial actions and writes to `~/Desktop`, `~/Downloads`, `~/Documents`
      or anywhere else outside the workspace are therefore excluded whatever else the same reply
      asked for, and keep prompting one at a time.
    * Every member is stored as its exact-call fingerprint -- session, task, target, byte-exact
      arguments and mode revision. The grant authorizes those calls and nothing else: a replan that
      changes a byte, a path, the mode or the turn produces a fingerprint that is simply not in the
      set, and prompts again.

    Returns ``(fingerprints, covered, excluded)``. ``excluded`` names the calls in the SAME plan
    that the batch refused, so "Review details" can say what one click does not buy instead of
    leaving the operator to infer it from a count. Returns three empty tuples when there is no
    second call to cover, so a single pending write keeps the plain once/deny prompt it has today.
    """
    planned = source_context.get(PENDING_BATCH_CALLS_KEY)
    if not isinstance(planned, (list, tuple)) or not planned:
        return (), (), ()
    if not _request_scope_eligible(
        intent=head_intent,
        arguments=head_arguments,
        actions=head_actions,
        source_context=source_context,
    ):
        return (), (), ()
    fingerprints: list[str] = [head_fingerprint]
    covered: list[dict[str, str]] = [
        {"intent": str(head_intent or ""), "target": _primary_target(head_arguments)}
    ]
    excluded: list[dict[str, str]] = []
    for entry in list(planned)[:_MAX_REQUEST_SCOPE_MEMBERS]:
        if not isinstance(entry, dict):
            continue
        member_intent = str(entry.get("intent") or "").strip()
        if not member_intent:
            continue
        member_arguments = entry.get("arguments")
        member_arguments = dict(member_arguments) if isinstance(member_arguments, dict) else {}
        member_fingerprint, member_actions, member_args, _member_identity = _call_permission_identity(
            intent=member_intent,
            arguments=member_arguments,
            session_id=session_id,
            task_id=task_id,
            mode_revision=mode_revision,
            source_context=source_context,
        )
        if not _request_scope_eligible(
            intent=member_intent,
            arguments=member_args,
            actions=member_actions,
            source_context=source_context,
        ):
            excluded.append(
                {"intent": member_intent, "target": _primary_target(member_arguments)}
            )
            continue
        if member_fingerprint in fingerprints:
            continue
        fingerprints.append(member_fingerprint)
        covered.append({"intent": member_intent, "target": _primary_target(member_arguments)})
    if len(fingerprints) < 2:
        return (), (), ()
    return tuple(fingerprints), tuple(covered), tuple(excluded)


def _bound_project(source_context: dict[str, Any]) -> tuple[str, str]:
    """(project_id, real project root) for the chat's bound project, or ("", "")."""
    project_id = str(source_context.get("_trusted_project_id") or source_context.get("project_id") or "").strip()
    if not project_id:
        return "", ""
    try:
        from core import project_store

        root = str(project_store.project_root(project_id) or "")
    except Exception:
        return "", ""
    if not root:
        return "", ""
    resolved = _resolve_against("", root)
    return (project_id, str(resolved)) if resolved is not None else ("", "")


def _inside(root: str, target: str) -> bool:
    try:
        Path(target).relative_to(Path(root))
        return True
    except ValueError:
        return False


def project_scope_eligibility(
    *,
    intent: str,
    arguments: dict[str, Any],
    actions: tuple[PermissionAction, ...],
    source_context: dict[str, Any],
) -> tuple[bool, str]:
    """Whether a standing project grant may cover this exact call. Returns (eligible, project_id).

    Three conditions, all required: a bound project that still exists on disk, a side-effect class
    entirely inside the low-risk set, and every named path resolving inside that project's root. A
    write additionally has to name a target — an unlocated write is never covered.
    """
    project_id, root = _bound_project(source_context)
    if not project_id or not root:
        return False, ""
    if not actions or not set(actions) <= _PROJECT_SCOPE_ACTIONS:
        return False, ""
    targets = [
        _resolve_against(_trusted_write_root(source_context), raw)
        for raw in _path_strings(arguments)
    ]
    if any(target is None for target in targets):
        return False, ""
    if any(not _inside(root, str(target)) for target in targets):
        return False, ""
    if set(actions) & _WRITE_ACTIONS and not targets:
        return False, ""
    return True, project_id


def project_approval_state(project_id: str) -> dict[str, Any]:
    """The standing low-risk grant for a project, or {}. Reads through to the project store once."""
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    with _LOCK:
        cached = _PROJECT_APPROVALS.get(pid)
    if cached is not None:
        return dict(cached)
    try:
        from core import project_store

        stored = dict(project_store.get_project_low_risk_approval(pid) or {})
    except Exception:
        stored = {}
    with _LOCK:
        _PROJECT_APPROVALS[pid] = stored
    return dict(stored)


def grant_project_approval(project_id: str, *, session_id: str = "") -> dict[str, Any]:
    """Record the standing low-risk grant for a project (memory + project store)."""
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    try:
        from core import project_store

        root = str(project_store.project_root(pid) or "")
    except Exception:
        root = ""
    if root:
        confinement = _workspace_root_confined(root)
        if confinement:
            raise ValueError(f"a project approval cannot bind this root: {confinement}")
    actions = sorted(action.value for action in _PROJECT_SCOPE_ACTIONS)
    grant = {
        "granted_at": "",
        "granted_by_session": str(session_id or ""),
        "actions": actions,
        "granted_epoch": time.time(),
    }
    try:
        from core import project_store

        stored = project_store.set_project_low_risk_approval(
            pid, granted_by_session=str(session_id or ""), actions=actions
        )
        if stored:
            grant = {**grant, **stored}
    except Exception:
        pass
    if not grant.get("granted_at"):
        grant["granted_at"] = str(grant.get("granted_epoch") or time.time())
    with _LOCK:
        _PROJECT_APPROVALS[pid] = dict(grant)
    return dict(grant)


def revoke_project_approval(project_id: str) -> bool:
    """Withdraw a project's standing low-risk grant. Every covered action prompts again."""
    pid = str(project_id or "").strip()
    if not pid:
        return False
    with _LOCK:
        had_cached = bool(_PROJECT_APPROVALS.get(pid))
        _PROJECT_APPROVALS[pid] = {}
    removed = False
    try:
        from core import project_store

        removed = bool(project_store.clear_project_low_risk_approval(pid))
    except Exception:
        removed = False
    return bool(removed or had_cached)


def _record_approval_event(
    *,
    session_id: str,
    family: str,
    intent: str,
    detail: str = "",
    handled: bool,
) -> None:
    """Append one approval row to the routing decision ledger. Fail-soft; never raises into a turn.

    893 routing rows in a day and exactly one mentioned an approval, so the loop an operator was
    actually stuck in did not exist in the ledger at all. Every prompt, allow, deny, grant, and resume
    now lands there in the same row shape as a routing decision.
    """
    try:
        from core.routing_decision_log import record_decision

        record_decision(
            session_id=str(session_id or ""),
            user_input=" ".join(part for part in (str(intent or ""), str(detail or "")) if part.strip()),
            family=str(family or "approval"),
            handled=bool(handled),
        )
    except Exception:
        pass


def _approval_effect_summary(actions: tuple[PermissionAction, ...]) -> str:
    words = [action.value.replace("_", " ") for action in actions]
    return ", ".join(words)


def _affected_resources(arguments: dict[str, Any]) -> list[str]:
    resources: list[str] = []
    for key in ("path", "paths", "cwd", "resource", "url", "recipient", "channel", "target"):
        value = arguments.get(key)
        if isinstance(value, list):
            resources.extend(str(item) for item in value[:12] if str(item).strip())
        elif value not in (None, ""):
            resources.append(str(value))
    return resources[:12]


def _affected_resources_display(
    arguments: dict[str, Any], source_context: dict[str, Any]
) -> list[str]:
    """`_affected_resources` with workspace-relative FILE paths resolved to absolute locations.

    The approval ask is the one place the operator sees where a write will land before
    allowing it. A relative "Affects: finalbot/README.md" hid the destination entirely --
    the folder was created inside VOOL's internal workspace and the operator only found out
    by asking (measured live 2026-09-18). Only the filesystem fields (path/paths/cwd) are
    resolved; url/recipient/channel are not paths and pass through untouched.
    """
    root = _trusted_write_root(source_context).rstrip("/")
    raw = _affected_resources(arguments)
    if not root:
        return raw
    path_like: set[str] = set()
    for key in ("path", "paths", "cwd"):
        value = arguments.get(key)
        if isinstance(value, list):
            path_like.update(str(item).strip() for item in value)
        elif value not in (None, ""):
            path_like.add(str(value).strip())
    displayed: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value in path_like and value and not value.startswith(("/", "~")):
            displayed.append(f"{root}/{value}")
        else:
            displayed.append(value)
    return displayed


def _edit_preview(intent: str, arguments: dict[str, Any], source_context: dict[str, Any]) -> str:
    if intent not in {"workspace.write_file", "workspace.replace_in_file", "machine.write_file"}:
        return ""
    raw_path = str(arguments.get("path") or "").strip()
    if not raw_path:
        return ""
    if intent == "machine.write_file":
        # Same root the tool actually writes to (Path.home()/{Desktop,Downloads,Documents}), not
        # workspace_root -- otherwise this resolves against the wrong directory entirely, exactly
        # the bug-4 mismatch. Without this branch, a machine.write_file overwrite correctly REQUIRES
        # approval (bug 4's fix) but ships with diff_preview="" -- the operator approves a real
        # Desktop/Downloads/Documents overwrite with no visibility into what content will land
        # (confirmed red-team finding, 2026-08-04).
        try:
            from core.runtime_execution_tools import _resolve_machine_directory

            target = _resolve_machine_directory(raw_path)
        except Exception:
            return ""
        if target is None:
            return ""
    else:
        raw_root = _trusted_write_root(source_context)
        if not raw_root:
            return ""
        root = Path(raw_root).expanduser()
        try:
            target = (root / raw_path).resolve()
            target.relative_to(root.resolve())
        except (OSError, ValueError):
            return ""
    before = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else ""
    if intent in {"workspace.write_file", "machine.write_file"}:
        after = str(arguments.get("content") or "")
    else:
        old_text = str(arguments.get("old_text") or "")
        new_text = str(arguments.get("new_text") or "")
        after = before.replace(old_text, new_text, -1 if bool(arguments.get("replace_all")) else 1)
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{raw_path}",
            tofile=f"b/{raw_path}",
            lineterm="",
            n=3,
        )
    )
    preview = "\n".join(lines[:120])
    return preview if len(preview) <= 6000 else preview[:5999] + "…"


def _prior_allow_covers_locked(
    fingerprint: str,
    *,
    session_id: str,
    task_id: str,
) -> bool:
    """Whether THIS exact call was already granted by the operator in this task -- even
    after its token was consumed. CALLER MUST HOLD ``_LOCK``.

    The resume loop re-plans the whole turn after every approval pause, so a write the
    operator already reviewed and approved can be re-emitted byte-identically. Asking
    again for an action already covered is the repeated-prompt defect this closes: the
    operator answered this exact question; the answer stands for its exact call.

    Narrow by construction:
    * same ``session_id`` AND same ``task_id`` (the logical client turn id, stable across
      the pause and resume) -- never another chat's or turn's grant;
    * an ALLOW lineage only: ``approved`` or ``consumed`` (consumed implies it was
      approved, then spent). A ``denied`` or ``expired`` entry never covers anything;
    * the EXACT fingerprint -- and, only when the operator resolved the ask with the
      REQUEST scope ("allow all planned changes"), membership in that granted batch. A
      ``once`` resolution of a batch-offered ask deliberately refused the wider set, so
      its recorded ``batch_fingerprints`` cover nothing beyond the head's own exact call;
    * unexpired. Nothing is spent here: a consumed grant's re-emission is idempotent
      coverage of the one call it already authorized.
    """

    now = time.time()
    for approval in _APPROVALS.values():
        if str(approval.get("session_id") or "") != session_id:
            continue
        if str(approval.get("task_id") or "") != task_id:
            continue
        if str(approval.get("status") or "") not in {"approved", "consumed"}:
            continue
        if float(approval.get("expires_at") or 0) <= now:
            continue
        if str(approval.get("fingerprint") or "") == fingerprint:
            return True
        if str(approval.get("scope") or "") == "request":
            batch = tuple(approval.get("batch_fingerprints") or ()) + tuple(
                approval.get("remaining_batch") or ()
            )
            if fingerprint in batch:
                return True
    return False


def _consume_matching_approval(
    source_context: dict[str, Any],
    *,
    fingerprint: str,
    session_id: str,
    task_id: str,
) -> bool:
    token = str(source_context.get("mode_approval_token") or "").strip()
    if not token:
        # No live token in hand, but the operator may already have granted this EXACT call
        # in this task (its token was consumed when the approved action ran). A resumed
        # turn re-emitting the identical call rides that prior grant instead of asking a
        # second time for the same reviewed bytes.
        _ensure_approvals_restored()
        with _LOCK:
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
    _ensure_approvals_restored()
    with _LOCK:
        approval = _APPROVALS.get(token)
        if not approval:
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
        bound = bool(
            approval.get("status") == "approved"
            and approval.get("session_id") == session_id
            and approval.get("task_id") == task_id
            and float(approval.get("expires_at") or 0) > time.time()
        )
        if not bound:
            # The token in hand does not cover THIS call (it was spent, or was for a
            # different action) -- but an earlier grant in this task may. The re-emitted
            # identical call must not re-prompt.
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
        # A request-scope grant covers the CONCRETE set of writes the operator saw listed, each one
        # exactly once. It is not a wider permission than a once grant, only a longer one: the same
        # exact-call fingerprints, spent one at a time, and the token is consumed when the last of
        # them is used. Anything outside the set -- a replanned path, swapped bytes, a delete the
        # model added afterwards -- is simply not a member and prompts on its own.
        if approval.get("scope") == "request":
            remaining = [str(item) for item in list(approval.get("remaining_batch") or ())]
            if fingerprint not in remaining:
                return False
            remaining.remove(fingerprint)
            approval["remaining_batch"] = tuple(remaining)
            if not remaining:
                approval["status"] = "consumed"
                _persist_pending_approvals_locked()
            return True
        if approval.get("fingerprint") != fingerprint:
            return False
        approval["status"] = "consumed"
        _persist_pending_approvals_locked()
        return True


def _matching_approval_covers(
    source_context: dict[str, Any],
    *,
    fingerprint: str,
    session_id: str,
    task_id: str,
) -> bool:
    """Whether ``_consume_matching_approval`` WOULD spend a token for this call. Never spends it.

    The same lookup and the same binding checks, minus the mutation and the persist: this is what a
    non-consuming preview reads, so its answer cannot drift from what the consuming decision would
    do a moment later on the same state.
    """
    token = str(source_context.get("mode_approval_token") or "").strip()
    if not token:
        _ensure_approvals_restored()
        with _LOCK:
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
    _ensure_approvals_restored()
    with _LOCK:
        approval = _APPROVALS.get(token)
        if not approval:
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
        bound = bool(
            approval.get("status") == "approved"
            and approval.get("session_id") == session_id
            and approval.get("task_id") == task_id
            and float(approval.get("expires_at") or 0) > time.time()
        )
        if not bound:
            return _prior_allow_covers_locked(fingerprint, session_id=session_id, task_id=task_id)
        if approval.get("scope") == "request":
            return fingerprint in [str(item) for item in list(approval.get("remaining_batch") or [])]
        return approval.get("fingerprint") == fingerprint


def request_batch_grant_covers(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> bool:
    """Whether a live request-scope grant still holds this exact call. Never consumes it.

    The tool loop asks this before it runs a mutating batch member inline. It is a peek at the same
    set ``_consume_matching_approval`` spends, so the answer cannot drift from what the gate will
    decide a moment later -- and a False here just makes the walk defer, which is what it does today
    for every write.
    """
    context = dict(source_context or {})
    token = str(context.get("mode_approval_token") or "").strip()
    if not token:
        return False
    state = active_mode_state(context)
    revision = int(state.get("revision") or 0)
    session_id = str(
        context.get("runtime_session_id") or context.get("session_id") or state.get("session_id") or ""
    ).strip()
    logical_task_id = str(
        context.get("cancel_turn_id") or state.get("client_turn_id") or task_id or ""
    ).strip()
    fingerprint, _actions, _args, _identity = _call_permission_identity(
        intent=str(intent or ""),
        arguments=arguments,
        session_id=session_id,
        task_id=logical_task_id,
        mode_revision=revision,
        source_context=context,
    )
    with _LOCK:
        approval = _APPROVALS.get(token)
        if not approval or approval.get("scope") != "request":
            return False
        return bool(
            approval.get("status") == "approved"
            and approval.get("session_id") == session_id
            and approval.get("task_id") == logical_task_id
            and float(approval.get("expires_at") or 0) > time.time()
            and fingerprint in tuple(approval.get("remaining_batch") or ())
        )


def register_external_approval(request: dict[str, Any]) -> str:
    """Register one PENDING approval in the SAME trusted store the tool gate uses, resolvable
    only through the SAME operator door (``resolve_approval`` -- the chat/native approval UI,
    or ``vool approvals resolve``). The caller supplies the full fact sheet the operator will
    see; this adds the token, pending status and expiry discipline. Used by surfaces whose
    consent is an approval (for example a UsePod spend grant), so spend consent inherits the
    same resolution authority, expiry and persistence as every other approval -- never a
    second approval mechanism."""
    import secrets as _secrets

    token = _secrets.token_urlsafe(32)
    entry = {
        **dict(request or {}),
        "approval_id": token,
        "status": "pending",
        "one_time": True,
        "expires_at": float(request.get("expires_at") or (time.time() + 86400)),
    }
    with _LOCK:
        _ensure_approvals_restored()
        _APPROVALS[token] = entry
        _persist_pending_approvals_locked()
    return token


def resolve_approval(token: str, *, decision: str, scope: str = "once") -> dict[str, Any] | None:
    _ensure_approvals_restored()
    clean_token = str(token or "").strip()
    normalized = str(decision or "").strip().lower()
    requested_scope = str(scope or "once").strip().lower()
    with _LOCK:
        approval = _APPROVALS.get(clean_token)
        if not approval or approval.get("status") != "pending":
            return None
        if normalized not in {"allow", "deny"}:
            return None
        # A prompt past its expiry is NOT resolvable — the same terminal truth the restore path
        # tells after a restart ("expired"), enforced on the live process too. Without this, an
        # entry that expired while sitting in memory (no restore involved) still converts into a
        # grant, so lifetime discipline depended on which process asked.
        if float(approval.get("expires_at") or 0) <= time.time():
            approval["status"] = "expired"
            _persist_pending_approvals_locked()
            return None
        approval["status"] = "approved" if normalized == "allow" else "denied"
        if normalized == "deny":
            approval["scope"] = "once"
        elif requested_scope == "request" and approval.get("batch_fingerprints"):
            # "Allow all planned changes for this request": bounded to the exact calls listed on
            # this request when it was raised, each spendable once. A request whose controller did
            # not build a batch falls through to the narrower branches below rather than inventing
            # one here -- an unbounded request scope would be the blanket grant this must not be.
            approval["scope"] = "request"
            approval["remaining_batch"] = tuple(approval.get("batch_fingerprints") or ())
        elif requested_scope == "project" and approval.get("project_scope_eligible") and approval.get("project_id"):
            # A project grant covers only the bounded low-risk class the controller already checked
            # when it built this request; the check runs again on every later call.
            approval["scope"] = "project"
        elif approval.get("edit_batch"):
            # An edit batch never broadens to the whole TASK: a task grant matches on intent + action
            # class alone, so it would authorize edits to files nobody has seen yet.
            approval["scope"] = "once"
        else:
            approval["scope"] = "task" if requested_scope == "task" else "once"
        approval["resolved_at"] = time.time()
        if approval["status"] == "approved" and approval["scope"] == "task":
            _TASK_APPROVALS.append(
                {
                    "session_id": approval.get("session_id"),
                    "task_id": approval.get("task_id"),
                    "intent": approval.get("intent"),
                    "actions": tuple(approval.get("actions") or ()),
                    "mode_revision": approval.get("mode_revision"),
                    "expires_at": approval.get("expires_at"),
                    # The grant must bind to the ACTUALLY-approved call, not merely its
                    # action-class tuple. Without this, approving one benign
                    # `sandbox.run_command` (e.g. `echo hi > note.txt`) with "task" scope
                    # silently authorized ANY LATER command that happened to classify into the
                    # same PermissionAction set -- confirmed: `find important_data -delete` ran
                    # with zero new approval after such a grant, in Manual mode. `fingerprint`
                    # already hashes intent + resolved targets/arguments + mode_revision (see
                    # `_fingerprint`/`_resolved_action_identity`), so matching on it here is an
                    # exact-call match, not a new parallel classifier.
                    "fingerprint": approval.get("fingerprint"),
                }
            )
        resolved = dict(approval)
        # Approval state changed: the durable mirror must agree with in-memory truth.
        _persist_pending_approvals_locked()
    if resolved.get("status") == "approved" and resolved.get("scope") == "project":
        grant_project_approval(str(resolved.get("project_id") or ""), session_id=str(resolved.get("session_id") or ""))
        _record_approval_event(
            session_id=str(resolved.get("session_id") or ""),
            family="approval_project_granted",
            intent=str(resolved.get("intent") or ""),
            detail=f"project={resolved.get('project_id') or ''}",
            handled=True,
        )
    if resolved.get("status") == "approved" and resolved.get("scope") == "request":
        _record_approval_event(
            session_id=str(resolved.get("session_id") or ""),
            family="approval_request_batch_granted",
            intent=str(resolved.get("intent") or ""),
            detail=f"actions={len(tuple(resolved.get('batch_fingerprints') or ()))}",
            handled=True,
        )
    _record_approval_event(
        session_id=str(resolved.get("session_id") or ""),
        family="approval_allowed" if resolved.get("status") == "approved" else "approval_denied",
        intent=str(resolved.get("intent") or ""),
        detail=f"scope={resolved.get('scope') or 'once'}",
        handled=resolved.get("status") == "approved",
    )
    return resolved


def _planned_batch_label(planned_actions: tuple[dict[str, str], ...]) -> str:
    """The middle phrase of "Allow all N ___ for this request".

    A batch of nothing but file writes is "planned changes". One that also sets up a directory is
    "planned workspace changes" -- the wider word is earned by the directory being in the set, and
    is derived from the concrete member intents rather than written into a branch of the UI.
    """
    intents = {str(item.get("intent") or "").strip().lower() for item in planned_actions}
    setup = intents - _REQUEST_SCOPE_FILE_WRITE_INTENTS
    return "planned workspace changes" if setup else "planned changes"


def _new_approval_request(
    *,
    session_id: str,
    task_id: str,
    intent: str,
    arguments: dict[str, Any],
    actions: tuple[PermissionAction, ...],
    mode: OperatingMode,
    mode_revision: int,
    source_context: dict[str, Any],
    fingerprint: str,
    project_scope_eligible: bool = False,
    project_id: str = "",
    batch_fingerprints: tuple[str, ...] = (),
    planned_actions: tuple[dict[str, str], ...] = (),
    excluded_actions: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    edit_batch = any(action in _WRITE_ACTIONS for action in actions)
    scope_options = ["once"] if edit_batch else ["once", "task"]
    # One bounded batch approval for the writes this request has ALREADY planned. Offered only when
    # the controller could enumerate them, so the operator is choosing a listed set of files, never
    # a standing filesystem permission.
    if len(batch_fingerprints) > 1:
        scope_options.append("request")
    if project_scope_eligible and project_id:
        scope_options.append("project")
    # Present the actual inner operation while retaining the outer call's exact
    # fingerprint, permission requirements and one-time scope.
    display_intent, display_arguments, display_context = intent, arguments, source_context
    if intent == "code.task.step" and isinstance(arguments.get("arguments"), dict):
        inner_intent = str(arguments.get("intent") or "").strip()
        if inner_intent and not inner_intent.startswith("code.task."):
            display_intent, display_arguments = inner_intent, arguments["arguments"]
            # The diff the operator reviews is of the file the TASK writer replaces.
            display_context = _step_execution_context(source_context, arguments) or source_context
    request = {
        "approval_id": token,
        "task_id": task_id,
        "intent": intent,
        "action": f"Run {display_intent}" + (" through code.task.step" if display_intent != intent else ""),
        "affected_resources": _affected_resources_display(display_arguments, display_context),
        "expected_side_effects": _approval_effect_summary(actions),
        "reversible": bool(edit_batch or PermissionAction.GIT_COMMIT in actions),
        "scope_options": scope_options,
        "one_time": True,
        "edit_batch": edit_batch,
        "diff_preview": _edit_preview(display_intent, display_arguments, display_context) if edit_batch else "",
        "expires_at": time.time() + 86400,
        # What the "allow all planned changes" button would actually cover, named file by file, so
        # the operator approves a visible list rather than a count.
        "planned_actions": [dict(item) for item in planned_actions],
        "planned_action_count": len(batch_fingerprints),
        # And what it would NOT cover, from the same plan, named the same way. A batch that quietly
        # dropped the `sandbox.run_command` sitting beside the writes still showed a truthful count,
        # so the operator could only learn the command was excluded by being asked about it later.
        "excluded_actions": [dict(item) for item in excluded_actions],
        # Server-derived button copy. The composition of the covered set is what decides it, so the
        # browser never has to classify anything to phrase the grant it is offering.
        "planned_action_label": _planned_batch_label(planned_actions),
    }
    with _LOCK:
        _APPROVALS[token] = {
            **request,
            "fingerprint": fingerprint,
            "session_id": session_id,
            "mode": mode.value,
            "mode_revision": mode_revision,
            "actions": tuple(action.value for action in actions),
            "project_scope_eligible": bool(project_scope_eligible and project_id),
            "project_id": str(project_id or ""),
            "batch_fingerprints": tuple(batch_fingerprints),
            "remaining_batch": (),
            "status": "pending",
        }
        # Mirror the pending prompt durably so a restart cannot silently erase it.
        _persist_pending_approvals_locked()
    _record_approval_event(
        session_id=session_id,
        family="approval_required",
        intent=intent,
        detail=f"scopes={'/'.join(scope_options)}",
        handled=False,
    )
    return request


@dataclass(frozen=True)
class PermissionPreview:
    """What ``decide_tool_call`` WOULD decide, computed without consuming anything.

    Same effect, actions and reason the consuming decision would carry, plus what it would have
    spent or created: ``would_consume_token`` (an approval token that matches and would be spent),
    ``would_request_approval`` (a pending approval request would be created and persisted).
    Nothing here is authority: no grant, no token, no approval row, no event. There is exactly one
    consuming point, ``decide_tool_call``, at actual dispatch.
    """

    effect: PermissionEffect
    mode: OperatingMode
    actions: tuple[PermissionAction, ...]
    reason: str
    would_consume_token: bool = False
    would_request_approval: bool = False

    @property
    def allowed(self) -> bool:
        return self.effect is PermissionEffect.ALLOW


def preview_tool_call(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> PermissionPreview:
    """The PURE reading of the permission decision. Consumes no token, creates no approval
    request, records no approval event, persists nothing. Same code path as ``decide_tool_call``
    with every consuming step replaced by its read-only twin."""
    decision, meta = _decide_tool_call(
        intent=intent, arguments=arguments, task_id=task_id, source_context=source_context, consume=False
    )
    return PermissionPreview(
        effect=decision.effect,
        mode=decision.mode,
        actions=tuple(decision.actions),
        reason=str(decision.reason or ""),
        would_consume_token=bool(meta.get("token_matched")),
        would_request_approval=bool(meta.get("approval_requested")),
    )


def decide_tool_call(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> PermissionDecision:
    """THE consuming decision: spends a matching approval token, creates and persists an approval
    request when one is required, records approval events. Exactly one such point exists."""
    decision, _meta = _decide_tool_call(
        intent=intent, arguments=arguments, task_id=task_id, source_context=source_context, consume=True
    )
    return decision


def _decide_tool_call(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
    consume: bool,
) -> tuple[PermissionDecision, dict[str, Any]]:
    meta: dict[str, Any] = {"token_matched": False, "approval_requested": False}
    context = dict(source_context or {})
    # Bind aliases BEFORE anything classifies this call. `actions_for_tool` and the action identity
    # below both read `args["path"]`; the handler binds `file_path`/`file`/`filepath` onto `path`
    # further down, so without this the gate decides over a call that is not the one that runs. In
    # `auto` mode that gap read an overwrite of an existing file as a create and allowed it.
    args = bind_known_argument_aliases(
        arguments, input_schema=input_schema_for_intent(str(intent or ""))
    )
    state = active_mode_state(context)
    mode = normalize_mode(state.get("mode"), allow_legacy=False) or OperatingMode.MANUAL
    revision = int(state.get("revision") or 0)
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or state.get("session_id") or "").strip()
    # The browser/API turn id is the stable logical task identity across an approval pause and
    # controller resume. Internal TaskRecord ids may be regenerated when the HTTP stream restarts.
    # Falling back to the internal id preserves the contract for non-chat callers.
    logical_task_id = str(context.get("cancel_turn_id") or state.get("client_turn_id") or task_id or "").strip()
    fingerprint, actions, args, identity = _call_permission_identity(
        intent=str(intent or ""),
        arguments=args,
        session_id=session_id,
        task_id=logical_task_id,
        mode_revision=revision,
        source_context=context,
    )

    token_step = _consume_matching_approval if consume else _matching_approval_covers
    if token_step(
        context,
        fingerprint=fingerprint,
        session_id=session_id,
        task_id=logical_task_id,
    ):
        meta["token_matched"] = True
        if consume:
            _record_approval_event(
                session_id=session_id,
                family="approval_resumed",
                intent=str(intent or ""),
                detail=" ".join(identity.get("targets") or ())[:120],
                handled=True,
            )
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            mode=mode,
            actions=actions,
            reason="Exact action approval matched this task, target, and side-effect class.",
        ), meta

    with _LOCK:
        task_approved = any(
            item.get("session_id") == session_id
            and item.get("task_id") == logical_task_id
            and item.get("intent") == str(intent or "")
            and tuple(item.get("actions") or ()) == tuple(action.value for action in actions)
            and int(item.get("mode_revision") or 0) == revision
            and float(item.get("expires_at") or 0) > time.time()
            # The action-class tuple alone is not enough to prove THIS call was the one
            # approved -- it only proves this call happens to classify the same way as some
            # other, unrelated call that was. Require the exact-call fingerprint too.
            and item.get("fingerprint") == fingerprint
            for item in _TASK_APPROVALS
        )
    if task_approved:
        if consume:
            _record_approval_event(
                session_id=session_id,
                family="approval_task_grant",
                intent=str(intent or ""),
                detail="scope=task",
                handled=True,
            )
        return PermissionDecision(
            effect=PermissionEffect.ALLOW,
            mode=mode,
            actions=actions,
            reason="This non-edit action type was approved for the current task and mode revision.",
        ), meta

    # Project permissions can only narrow mode permissions, never widen them.
    project_permissions = dict(context.get("project_permissions") or {})
    denied_by_project = [action for action in actions if project_permissions.get(action.value) is False]
    if denied_by_project:
        return PermissionDecision(
            effect=PermissionEffect.DENY,
            mode=mode,
            actions=actions,
            reason=f"The current project denies: {_approval_effect_summary(tuple(denied_by_project))}.",
        ), meta

    # A typed internal scope is consulted INSIDE the decision, never around it. It is evaluated
    # after the project-level deny above (a project can still narrow it) and before the mode
    # matrix, because a background caller has no composer mode to read. It can only ever allow the
    # exact actions it declared: anything broader falls through to the matrix below and is decided
    # like any other call, so a scope minted for one narrow job cannot carry a second one.
    internal_scope = _internal_authority_scope(context)
    if internal_scope is not None:
        scoped: frozenset[PermissionAction] = frozenset(internal_scope.get("actions") or frozenset())
        covered = bool(actions) and all(action in scoped for action in actions)
        ceilinged = any(action in _INTERNAL_AUTHORITY_CEILING for action in actions)
        bound = _internal_scope_matches(
            internal_scope, context=context, intent=str(intent or ""), task_id=str(task_id or "")
        )
        if covered and not ceilinged and bound:
            if consume:
                _record_approval_event(
                    session_id=session_id,
                    family="internal_authority_grant",
                    intent=str(intent or ""),
                    detail=f"scope={internal_scope.get('label') or ''}",
                    handled=True,
                )
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                mode=mode,
                actions=actions,
                reason=(
                    f"Internal authority '{internal_scope.get('label') or ''}' explicitly covers "
                    f"{_approval_effect_summary(actions)}."
                ),
            ), meta

    effects = [MODE_PERMISSION_MATRIX[mode][action] for action in actions]
    if PermissionEffect.DENY in effects:
        return PermissionDecision(
            effect=PermissionEffect.DENY,
            mode=mode,
            actions=actions,
            reason=(
                f"{MODE_LABELS[mode]} mode does not permit {_approval_effect_summary(actions)}. "
                "The controller blocked the tool before dispatch."
            ),
        ), meta
    if PermissionEffect.REQUIRE_APPROVAL in effects:
        # A standing project grant is checked AFTER the mode DENY above, so it can only ever turn a
        # PROMPT into an allow. It cannot resurrect an action Plan mode forbids, and it cannot survive
        # a project-level deny, which is evaluated earlier.
        project_eligible, grant_project_id = project_scope_eligibility(
            intent=str(intent or ""),
            arguments=args,
            actions=actions,
            source_context=context,
        )
        if project_eligible and project_approval_state(grant_project_id).get("granted_at"):
            if consume:
                _record_approval_event(
                    session_id=session_id,
                    family="approval_project_grant",
                    intent=str(intent or ""),
                    detail=" ".join(identity.get("targets") or ())[:120],
                    handled=True,
                )
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                mode=mode,
                actions=actions,
                reason="This project allows low-risk reads and in-project writes without a new prompt.",
            ), meta
        meta["approval_requested"] = True
        if not consume:
            # A preview reports that an approval WOULD be requested; it creates and persists none.
            return PermissionDecision(
                effect=PermissionEffect.REQUIRE_APPROVAL,
                mode=mode,
                actions=actions,
                reason=f"{MODE_LABELS[mode]} mode requires approval for this exact action.",
            ), meta
        batch_fingerprints, planned_actions, excluded_actions = _planned_edit_batch(
            head_intent=str(intent or ""),
            head_fingerprint=fingerprint,
            head_actions=actions,
            head_arguments=args,
            session_id=session_id,
            task_id=logical_task_id,
            mode_revision=revision,
            source_context=context,
        )
        approval = _new_approval_request(
            session_id=session_id,
            task_id=logical_task_id,
            intent=str(intent or ""),
            arguments=args,
            actions=actions,
            mode=mode,
            mode_revision=revision,
            source_context=context,
            fingerprint=fingerprint,
            project_scope_eligible=project_eligible,
            project_id=grant_project_id,
            batch_fingerprints=batch_fingerprints,
            planned_actions=planned_actions,
            excluded_actions=excluded_actions,
        )
        return PermissionDecision(
            effect=PermissionEffect.REQUIRE_APPROVAL,
            mode=mode,
            actions=actions,
            reason=f"{MODE_LABELS[mode]} mode requires approval for this exact action.",
            approval_request=approval,
        ), meta
    return PermissionDecision(
        effect=PermissionEffect.ALLOW,
        mode=mode,
        actions=actions,
        reason=f"{MODE_LABELS[mode]} mode allows this bounded action.",
    ), meta


def reset_mode_permission_state() -> None:
    """Clear every mode/permission session state, INCLUDING the durable pending-approval mirror.

    Historically this cleared only memory, which made the disk mirror a second authority: an
    approval supposedly cleared by a reset came back on the next restore (and the next restart)
    — invalidated permission was revived from disk. One coherent lifecycle means reset
    invalidates in BOTH places at once. The restore flag is set True so a later lazy restore can
    never re-read anything above this point.
    """
    with _LOCK:
        _ACTIVE_MODES.clear()
        _BYPASS_GRANTS.clear()
        _APPROVALS.clear()
        _TASK_APPROVALS.clear()
        _PROJECT_APPROVALS.clear()
        _INTERNAL_AUTHORITY.clear()
        global _PERSISTED_APPROVALS_RESTORED
        _PERSISTED_APPROVALS_RESTORED = True
    try:
        path = _pending_approvals_path()
        if path is not None and path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        pass  # best-effort; an unremovable mirror leaves no revivable authority to read
