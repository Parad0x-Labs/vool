"""Ops-surfaces group — the skills library, the RepoOps plane, and the plugin lifecycle.

CP2 convergence: the native-skills lane (GET /api/skills, POST /api/skills/enable) and the
KAS/RepoOps lane (/api/repoops/*, /api/plugins/lifecycle) shipped HTTP surfaces after the
registry's census pinned zero unmigrated actions; the census law (every release-critical
operator action is owned or adapted) demanded they enter the registry rather than stay
LEGACY_UNMIGRATED. The commands bind to the SAME authorities the routes call — the registry
never rewrites them.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)


@dataclass(frozen=True)
class SkillEnableInput:
    skill_id: str
    enabled: bool


@dataclass(frozen=True)
class RepoSessionInput:
    session_id: str


@dataclass(frozen=True)
class AuthorizePushInput:
    repo_session_id: str
    plan_hash: str
    force: bool = False
    branch_delete: bool = False


@dataclass(frozen=True)
class AuthorizeForgeActionInput:
    repo_session_id: str
    action_hash: str = ""
    resolve: str = ""


@dataclass(frozen=True)
class LifecycleActionInput:
    action: str
    plugin_id: str


def _gate_operator(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="this control is an operator decision")
    return AuthorityDecision(granted=True)


def _probe_skill_library(context: dict) -> tuple[bool, str]:
    try:
        from core.native_skill_library import skill_inventory

        skills = skill_inventory()
        return True, f"{len(skills)} skill(s) in the library"
    except Exception as exc:
        return False, f"native skill library unreachable: {exc}"


def _probe_repo_store(context: dict) -> tuple[bool, str]:
    try:
        from core.web.api.repoops_api import repo_sessions_payload

        payload = repo_sessions_payload()
        sessions = (payload or {}).get("sessions") or (payload or {}).get("items") or []
        if not sessions:
            return False, "no RepoOps sessions recorded — nothing to authorize"
        return True, f"{len(sessions)} session(s) recorded"
    except Exception as exc:
        return False, f"RepoOps session store unreachable: {exc}"


def _probe_plugin_lifecycle(context: dict) -> tuple[bool, str]:
    try:
        from core.plugin_lifecycle import lifecycle_snapshot

        snapshot = lifecycle_snapshot()
        packs = (snapshot or {}).get("packs") or (snapshot or {}).get("plugins") or []
        if not packs:
            return False, "no plugin packs discovered on this machine"
        return True, f"{len(packs)} pack(s) in the lifecycle store"
    except Exception as exc:
        return False, f"plugin lifecycle store unreachable: {exc}"


def _handle_skills_list(inp, ctx):
    from core.native_skill_library import skill_inventory

    return HandlerOk(data={"skills": skill_inventory()}, summary="Native skill inventory")


def _handle_skills_enable(inp, ctx):
    from core.native_skill_library import set_skill_enabled

    outcome = set_skill_enabled(str(inp.skill_id or ""), bool(inp.enabled))
    if not outcome.get("ok"):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Skill {inp.skill_id!r} enable state not changed: {outcome.get('reason') or outcome.get('error') or 'refused'}",
            detail={"skill_id": inp.skill_id, "outcome": outcome},
        )
    return HandlerOk(
        data=outcome,
        summary=f"Skill {inp.skill_id} {'enabled' if inp.enabled else 'disabled'}",
        receipts=({"kind": "skill_state", "skill_id": inp.skill_id, "enabled": bool(inp.enabled)},),
    )


def _handle_repo_sessions(inp, ctx):
    from core.web.api.repoops_api import repo_sessions_payload

    return HandlerOk(data=repo_sessions_payload(), summary="RepoOps sessions")


def _handle_repo_session(inp, ctx):
    from core.web.api.repoops_api import repo_session_payload

    payload = repo_session_payload(str(inp.session_id or ""))
    if not payload.get("found"):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"RepoOps session {inp.session_id!r} not found",
            detail={"session_id": inp.session_id},
        )
    return HandlerOk(data=payload, summary=f"RepoOps session {inp.session_id}")


def _handle_authorize_push(inp, ctx):
    from core.web.api.repoops_api import authorize_push

    payload = authorize_push(
        repo_session_id=str(inp.repo_session_id or ""),
        plan_hash=str(inp.plan_hash or ""),
        force=bool(inp.force),
        branch_delete=bool(inp.branch_delete),
        workspace_root="",
    )
    if not payload.get("ok"):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Push authorization refused: {payload.get('error') or payload.get('reason') or 'refused'}",
            detail={"repo_session_id": inp.repo_session_id, "payload": payload},
        )
    return HandlerOk(
        data=payload,
        summary=f"Push authorized for session {inp.repo_session_id}",
        receipts=({"kind": "repoops_authorization", "repo_session_id": inp.repo_session_id, "plan_hash": inp.plan_hash},),
    )


def _handle_authorize_forge_action(inp, ctx):
    from core.web.api.repoops_api import authorize_forge_action

    payload = authorize_forge_action(
        repo_session_id=str(inp.repo_session_id or ""),
        action_hash=str(inp.action_hash or ""),
        resolve=str(inp.resolve or ""),
        workspace_root="",
    )
    if not payload.get("ok"):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Forge-action authorization refused: {payload.get('error') or payload.get('status') or 'refused'}",
            detail={"repo_session_id": inp.repo_session_id, "payload": payload},
        )
    return HandlerOk(
        data=payload,
        summary=f"Forge action {'resolved' if payload.get('resolved') else 'authorized'} for session {inp.repo_session_id}",
        receipts=(
            {
                "kind": "repoops_forge_action_authorization",
                "repo_session_id": inp.repo_session_id,
                "action_hash": payload.get("action_hash") or inp.action_hash,
            },
        ),
    )


def _handle_lifecycle_state(inp, ctx):
    from core.plugin_lifecycle import lifecycle_snapshot

    return HandlerOk(data=lifecycle_snapshot(), summary="Plugin lifecycle state")


def _handle_lifecycle_action(inp, ctx):
    from core.web.api.repoops_api import plugin_lifecycle_action

    payload = plugin_lifecycle_action(action=str(inp.action or ""), plugin_id=str(inp.plugin_id or ""))
    if not payload.get("ok"):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Lifecycle action {inp.action!r} on {inp.plugin_id!r} refused: {payload.get('error') or payload.get('reason') or 'refused'}",
            detail={"action": inp.action, "plugin_id": inp.plugin_id, "payload": payload},
        )
    return HandlerOk(
        data=payload,
        summary=f"Plugin {inp.plugin_id}: {inp.action}",
        receipts=({"kind": "plugin_lifecycle", "action": inp.action, "plugin_id": inp.plugin_id},),
    )


@dataclass(frozen=True)
class InvalidateInput:
    reason: str = "operator_correction"


def _handle_learning_list(inp, ctx):
    from core.learning import list_procedure_records

    return HandlerOk(data={"procedures": list_procedure_records()}, summary="Learned procedures")


def _handle_learning_invalidate(inp, ctx):
    from core.learning import invalidate_procedure
    from core.learning_integration import valid_procedure_id

    # the registry's own id gate: never a raw path
    pid = str(getattr(inp, "procedure_id", "") or "")
    if not valid_procedure_id(pid):
        return HandlerFault(fault_code="fault_validation", summary="invalid procedure id", detail={"procedure_id": pid})
    shard = invalidate_procedure(pid, reason=str(inp.reason or "operator_correction"))
    if shard is None:
        return HandlerFault(fault_code="fault_validation", summary=f"procedure {pid!r} not found", detail={"procedure_id": pid})
    return HandlerOk(
        data={"procedure_id": pid, "status": shard.status},
        summary=f"Procedure {pid} invalidated",
        receipts=({"kind": "learning_correction", "procedure_id": pid, "action": "invalidate"},),
    )


def _handle_learning_delete(inp, ctx):
    from core.learning import delete_procedure
    from core.learning_integration import valid_procedure_id

    pid = str(getattr(inp, "procedure_id", "") or "")
    if not valid_procedure_id(pid):
        return HandlerFault(fault_code="fault_validation", summary="invalid procedure id", detail={"procedure_id": pid})
    if not delete_procedure(pid):
        return HandlerFault(fault_code="fault_validation", summary=f"procedure {pid!r} not found", detail={"procedure_id": pid})
    return HandlerOk(
        data={"procedure_id": pid, "deleted": True},
        summary=f"Procedure {pid} deleted",
        receipts=({"kind": "learning_correction", "procedure_id": pid, "action": "delete"},),
    )


def _probe_learning_store(context: dict) -> tuple[bool, str]:
    try:
        from core.learning import list_procedure_records

        records = list_procedure_records()
        return True, f"{len(records)} learned procedure(s)"
    except Exception as exc:
        return False, f"learning store unreachable: {exc}"


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="ops", description="skill library, RepoOps plane, and plugin lifecycle surfaces"))
    reg.add(
        CommandSpec(
            command_id="skills.list",
            group="ops",
            description="List the native skill library: versions, enabled state, typed availability",
            effects="read_only",
            capabilities=frozenset({"skill.read"}),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_skills_list"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="skills.set_enabled",
            group="ops",
            description="Enable or disable one native skill (operator decision; persists across restarts)",
            input_schema=SkillEnableInput,
            effects="mutating",
            capabilities=frozenset({"skill.control"}),
            permission=OperatorAuthority(
                kind="skill.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_skills_enable"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_skill_library"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="repoops.sessions",
            group="ops",
            description="List recorded RepoOps sessions",
            effects="read_only",
            capabilities=frozenset({"repo.read"}),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_repo_sessions"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="repoops.session",
            group="ops",
            description="Show one RepoOps session",
            input_schema=RepoSessionInput,
            effects="read_only",
            capabilities=frozenset({"repo.read"}),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_repo_session"),
            exit_codes=(0, 2, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="repoops.authorize_push",
            group="ops",
            description="Mint the server-side authorization that moves a remote ref (operator-only, owner-local)",
            input_schema=AuthorizePushInput,
            effects="mutating",
            capabilities=frozenset({"repo.control"}),
            permission=OperatorAuthority(
                kind="repo.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_authorize_push"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_repo_store"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="repoops.authorize_forge_action",
            group="ops",
            description=(
                "Mint the server-side authorization for one forge action (draft PR create, PR text "
                "update, comment), or resolve an unproven one as not-applied (operator-only, owner-local)"
            ),
            input_schema=AuthorizeForgeActionInput,
            effects="mutating",
            capabilities=frozenset({"repo.control"}),
            permission=OperatorAuthority(
                kind="repo.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_authorize_forge_action"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_repo_store"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="plugins.lifecycle.state",
            group="ops",
            description="Show every pack's lifecycle state — installed, verified, enabled, revoked, available",
            effects="read_only",
            capabilities=frozenset({"plugin.read"}),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_lifecycle_state"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="learning.list",
            group="ops",
            description="List learned procedures: status, counters, provenance, bounded guidance",
            effects="read_only",
            capabilities=frozenset({"learning.read"}),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_learning_list"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="learning.invalidate",
            group="ops",
            description="Invalidate one learned procedure (operator correction; stops reuse immediately)",
            input_schema=SkillEnableInput,
            effects="mutating",
            capabilities=frozenset({"learning.control"}),
            permission=OperatorAuthority(
                kind="learning.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_learning_invalidate"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_learning_store"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="learning.delete",
            group="ops",
            description="Delete one learned procedure entirely (operator forget path)",
            input_schema=SkillEnableInput,
            effects="mutating",
            capabilities=frozenset({"learning.control"}),
            permission=OperatorAuthority(
                kind="learning.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_learning_delete"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_learning_store"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="plugins.lifecycle.transition",
            group="ops",
            description="Install / verify / enable / disable / update / revoke / uninstall one plugin pack (operator-only)",
            input_schema=LifecycleActionInput,
            effects="mutating",
            capabilities=frozenset({"plugin.control"}),
            permission=OperatorAuthority(
                kind="plugin.control",
                verifier="core.command_registry.groups.ops_surfaces:_gate_operator",
            ),
            handler=Handler("core.command_registry.groups.ops_surfaces:_handle_lifecycle_action"),
            availability=Availability("core.command_registry.groups.ops_surfaces:_probe_plugin_lifecycle"),
            exit_codes=(0, 2, 10, 20, 40),
        )
    )
