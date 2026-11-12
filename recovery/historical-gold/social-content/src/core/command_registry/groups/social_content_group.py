"""Social content group — local campaign/draft/metric/hypothesis management.

Binds to ``storage.social_content_store`` (the one local content authority).
Reads are open; every mutation is a separate command with its own
``side_effect_class`` and an OperatorAuthority gate — a model turn cannot
self-approve content decisions. Nothing here publishes: there is no outbound
command in this group by design (X/LinkedIn/Bluesky/Mastodon/Threads have no
canonical connector; Telegram/Discord handoff goes through the channel
authority, never through this group).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    Availability,
    AuthorityDecision,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OperatorAuthority,
)


# ---------------------------------------------------------------- probes + gates


def _probe_store(context: dict) -> tuple[bool, str]:
    try:
        from storage.social_content_store import _ensure_schema

        _ensure_schema()
        return True, ""
    except Exception as exc:
        return False, f"social content store unreachable: {exc}"


def _gate_mutation(inp, ctx) -> ApprovalDecision:
    """Canonical approval gate for local content mutations: only the operator
    principal (the loopback owner-local surface) may mutate. A model request
    mints a typed approval requirement instead of executing. A bare
    authority_token string deliberately does NOT pass: this lane binds no
    out-of-band token grant, so nothing unauthenticated can pose as one."""
    if str(getattr(ctx, "principal", "") or "") == "operator":
        return ApprovalDecision(required=False)
    return ApprovalDecision(
        required=True,
        reason="social content mutation — operator approval required",
    )


def _gate_operator(inp, ctx) -> AuthorityDecision:
    """Content DECISIONS (approve/refuse) are operator-only: a model turn can
    never approve content, even with an approval token."""
    if str(getattr(ctx, "principal", "") or "") != "operator":
        return AuthorityDecision(
            granted=False, reason="only the operator may decide content"
        )
    return AuthorityDecision(granted=True)


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class CampaignCreateInput:
    project_id: str
    objective: str
    audience: str
    platforms: tuple[str, ...] = ()
    guardrails: tuple[str, ...] = ()


@dataclass(frozen=True)
class CampaignScopeInput:
    project_id: str
    limit: int = 50


@dataclass(frozen=True)
class CampaignIdInput:
    campaign_id: str


@dataclass(frozen=True)
class CampaignArchiveInput:
    campaign_id: str
    expected_version: int


@dataclass(frozen=True)
class CampaignUpdateInput:
    campaign_id: str
    expected_version: int
    objective: str | None = None
    audience: str | None = None
    platforms: tuple[str, ...] | None = None
    status: str | None = None


@dataclass(frozen=True)
class DraftInspectInput:
    draft_id: str
    version: int = 0


@dataclass(frozen=True)
class DraftSaveInput:
    campaign_id: str
    platform: str
    body: str
    format: str = "post"
    draft_id: str = ""
    claim_ledger: tuple[dict[str, str], ...] = ()
    source_ledger: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftDecisionInput:
    draft_id: str
    draft_version: int
    decision: str
    reason: str = ""
    destination: str = ""


@dataclass(frozen=True)
class MetricsImportInput:
    project_id: str
    metric_name: str
    value: float
    unit: str = "count"
    content_draft_id: str = ""
    source: str = "operator_entered"
    provenance: str = ""
    denominator_known: bool = False


@dataclass(frozen=True)
class HypothesisCloseInput:
    hypothesis_id: str
    outcome: str
    disposition: str = ""
    confidence: str = "low"


# ---------------------------------------------------------------- handlers


def _handle_campaign_create(inp, ctx):
    from storage import social_content_store as store

    try:
        doc = store.create_campaign(
            project_id=inp.project_id, objective=inp.objective, audience=inp.audience,
            platforms=tuple(inp.platforms or ()), guardrails=tuple(inp.guardrails or ()),
        )
    except store.SecretRefusal as exc:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Campaign refused: {exc}", detail={"field": exc.field},
        )
    except store.ValidationError as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc,
        summary=f"Campaign {doc['campaign_id']} created (v{doc['version']})",
        receipts=({"kind": "social_campaign_created", "campaign_id": doc["campaign_id"]},),
    )


def _handle_campaign_list(inp, ctx):
    from storage import social_content_store as store

    rows = store.list_campaigns(inp.project_id, limit=inp.limit, include_archived=True)
    return HandlerOk(
        data={"count": len(rows), "campaigns": rows},
        summary=f"{len(rows)} campaign(s) in {inp.project_id}",
    )


def _handle_campaign_inspect(inp, ctx):
    from storage import social_content_store as store

    doc = store.get_campaign(inp.campaign_id)
    if doc is None:
        return HandlerFault(
            fault_code="fault_validation", summary=f"No campaign {inp.campaign_id!r}"
        )
    return HandlerOk(data=doc, summary=f"Campaign {inp.campaign_id}")


def _handle_campaign_update(inp, ctx):
    from storage import social_content_store as store

    try:
        doc = store.update_campaign(
            inp.campaign_id, expected_version=int(inp.expected_version),
            objective=inp.objective, audience=inp.audience,
            platforms=tuple(inp.platforms) if inp.platforms is not None else None,
            status=inp.status,
        )
    except store.RevisionConflict as exc:
        return HandlerFault(
            fault_code="conflict", summary=str(exc),
            detail={"campaign_id": inp.campaign_id},
            remediation_hint="re-inspect the campaign and retry with its current version",
        )
    except (store.ValidationError, store.UnknownRecord) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc, summary=f"Campaign {inp.campaign_id} updated to v{doc['version']}",
        receipts=({"kind": "social_campaign_updated", "campaign_id": inp.campaign_id},),
    )


def _handle_draft_inspect(inp, ctx):
    from storage import social_content_store as store

    doc = store.get_draft(inp.draft_id, version=int(inp.version) or None)
    if doc is None:
        return HandlerFault(
            fault_code="fault_validation", summary=f"No draft {inp.draft_id!r}"
        )
    approval = store.approval_for(
        draft_id=doc["draft_id"], version=doc["version"],
        content_hash=doc["content_hash"], platform=doc["platform"],
    )
    data = dict(doc)
    data["approved"] = approval is not None
    data["approval"] = approval
    return HandlerOk(
        data=data, summary=f"Draft {inp.draft_id} v{doc['version']} "
        f"({'approved' if approval else 'not approved'})",
    )


def _handle_draft_list(inp, ctx):
    from storage import social_content_store as store

    rows = store.list_drafts(inp.project_id, limit=inp.limit)
    return HandlerOk(
        data={"count": len(rows), "drafts": rows},
        summary=f"{len(rows)} draft(s) in {inp.project_id}",
    )


def _handle_draft_save(inp, ctx):
    from storage import social_content_store as store

    try:
        doc = store.save_draft(
            campaign_id=inp.campaign_id, platform=inp.platform, body=inp.body,
            format=inp.format,
            draft_id=str(inp.draft_id) or None,
            claim_ledger=tuple(inp.claim_ledger or ()),
            source_ledger=tuple(inp.source_ledger or ()),
        )
    except store.SecretRefusal as exc:
        return HandlerFault(
            fault_code="fault_validation", summary=f"Draft refused: {exc}",
            detail={"field": exc.field},
        )
    except (store.ValidationError, store.UnknownRecord) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc,
        summary=f"Draft {doc['draft_id']} v{doc['version']} saved "
        f"({doc['platform']}, hash {doc['content_hash'][:12]})",
        receipts=({"kind": "draft_saved", "draft_id": doc["draft_id"],
                   "version": doc["version"],
                   "content_hash": doc["content_hash"]},),
    )


def _handle_draft_decide(inp, ctx):
    from core.social_content import review as review_mod
    from storage import social_content_store as store

    draft = store.get_draft(inp.draft_id, version=int(inp.draft_version))
    if draft is None:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"No draft {inp.draft_id!r} at version {inp.draft_version}",
        )
    if str(inp.decision).strip().lower() == "approved":
        # the SAME fence as the library lane: review + privacy withhold +
        # replay refusal. An operator approval can never skip the fence.
        result = review_mod.approve_exact(
            store, draft_id=inp.draft_id, draft_version=int(inp.draft_version),
            destination=inp.destination,
        )
        if not result.get("ok"):
            return HandlerFault(
                fault_code="conflict" if result.get("code") == "approval_replay_refused"
                else "fault_validation",
                summary=f"{result.get('code')}: {result.get('summary', '')}",
            )
        return HandlerOk(
            data=result["decision"],
            summary=f"Decision approved for {inp.draft_id}@{inp.draft_version}",
            receipts=({"kind": "draft_approved", "draft_id": inp.draft_id,
                       "draft_version": int(inp.draft_version),
                       "content_hash": result["decision"]["content_hash"],
                       "platform": result["decision"]["platform"]},),
        )
    try:
        dec = store.record_decision(
            draft_id=inp.draft_id, draft_version=int(inp.draft_version),
            content_hash=draft["content_hash"], platform=draft["platform"],
            decision=inp.decision, reason=inp.reason, destination=inp.destination,
            operator="operator",
        )
    except (store.ValidationError, store.UnknownRecord, store.SecretRefusal) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=dec,
        summary=f"Decision {dec['decision']} recorded for {inp.draft_id}@{inp.draft_version}",
        receipts=({
            "kind": f"draft_{dec['decision']}".lower(),
            "draft_id": inp.draft_id, "draft_version": int(inp.draft_version),
            "content_hash": dec["content_hash"], "platform": dec["platform"],
        },),
    )


def _handle_metrics_import(inp, ctx):
    from storage import social_content_store as store

    content_ref = (
        {"draft_id": inp.content_draft_id} if inp.content_draft_id else {}
    )
    try:
        doc = store.add_metric_observation(
            project_id=inp.project_id, metric_name=inp.metric_name,
            value=inp.value, unit=inp.unit, content_ref=content_ref,
            source=inp.source, provenance=inp.provenance,
            denominator_known=bool(inp.denominator_known),
        )
    except (store.ValidationError, store.SecretRefusal) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc,
        summary=f"Metric {doc['metric_name']}={doc['value']} recorded ({doc['source']})",
        receipts=({"kind": "metric_observation", "observation_id": doc["observation_id"]},),
    )


def _handle_metrics_inspect(inp, ctx):
    from storage import social_content_store as store

    rows = store.list_metric_observations(inp.project_id, limit=inp.limit)
    return HandlerOk(
        data={"count": len(rows), "observations": rows},
        summary=f"{len(rows)} metric observation(s) in {inp.project_id}",
    )


def _handle_hypothesis_inspect(inp, ctx):
    from storage import social_content_store as store

    rows = store.list_hypotheses(inp.project_id, limit=inp.limit)
    return HandlerOk(
        data={"count": len(rows), "hypotheses": rows},
        summary=f"{len(rows)} hypothesis record(s) in {inp.project_id}",
    )


def _handle_campaign_archive(inp, ctx):
    from storage import social_content_store as store

    try:
        doc = store.archive_campaign(inp.campaign_id, expected_version=int(inp.expected_version))
    except store.RevisionConflict as exc:
        return HandlerFault(fault_code="conflict", summary=str(exc))
    except (store.ValidationError, store.UnknownRecord) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc, summary=f"Campaign {inp.campaign_id} archived (v{doc['version']})",
        receipts=({"kind": "social_campaign_archived", "campaign_id": inp.campaign_id},),
    )


def _handle_hypothesis_close(inp, ctx):
    from storage import social_content_store as store

    try:
        doc = store.close_hypothesis(
            inp.hypothesis_id, outcome=inp.outcome,
            disposition=inp.disposition, confidence=inp.confidence,
        )
    except (store.ValidationError, store.UnknownRecord) as exc:
        return HandlerFault(fault_code="usage", summary=str(exc))
    return HandlerOk(
        data=doc, summary=f"Hypothesis {inp.hypothesis_id} closed: {doc['outcome']}",
        receipts=({"kind": "hypothesis_closed", "hypothesis_id": inp.hypothesis_id},),
    )


_MUTATING_FAULTS = (
    FaultBinding(when="store_missing", fault_code="fault_validation",
                 remediation=("vool campaign list",)),
)


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="social_content",
            description="local social campaign/draft/metric/hypothesis management — "
                        "planning truth, never publication",
            aliases=("campaign",),
        )
    )
    reg.add(
        CommandSpec(
            command_id="campaign.create",
            group="social_content",
            description="Create a local social campaign (objective/audience/platforms/guardrails)",
            input_schema=CampaignCreateInput,
            effects="mutating",
            capabilities=frozenset({"social_content.campaign.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_campaign_create"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 20, 21, 30),
            next_actions=(NextAction(command_id="campaign.list", label="List campaigns"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="campaign.list",
            group="social_content",
            description="List local social campaigns for a project",
            aliases=("campaigns",),
            input_schema=CampaignScopeInput,
            effects="read_only",
            capabilities=frozenset({"social_content.campaign.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_campaign_list"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="campaign.inspect",
            group="social_content",
            description="Inspect one campaign by id",
            input_schema=CampaignIdInput,
            effects="read_only",
            capabilities=frozenset({"social_content.campaign.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_campaign_inspect"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="campaign.update",
            group="social_content",
            description="Update a campaign (CAS on expected_version)",
            input_schema=CampaignUpdateInput,
            effects="mutating",
            capabilities=frozenset({"social_content.campaign.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_campaign_update"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="campaign.archive",
            group="social_content",
            description="Archive a campaign (reversible; nothing is deleted)",
            input_schema=CampaignUpdateInput,
            effects="mutating",
            capabilities=frozenset({"social_content.campaign.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_campaign_update"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="draft.save",
            group="social_content",
            description="Save a draft version (hash-bound, privacy-scanned; never publishes)",
            input_schema=DraftSaveInput,
            effects="mutating",
            capabilities=frozenset({"social_content.draft.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_draft_save"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 20, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="draft.inspect",
            group="social_content",
            description="Inspect one draft version with its approval state",
            input_schema=DraftInspectInput,
            effects="read_only",
            capabilities=frozenset({"social_content.draft.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_draft_inspect"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="draft.list",
            group="social_content",
            description="List drafts for a project",
            input_schema=CampaignScopeInput,
            effects="read_only",
            capabilities=frozenset({"social_content.draft.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_draft_list"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    for verb, decision in (("approve", "approved"), ("reject", "refused")):
        reg.add(
            CommandSpec(
                command_id=f"draft.{verb}",
                group="social_content",
                description=f"Record an operator {verb} decision binding exact draft bytes/platform/version",
                input_schema=DraftDecisionInput,
                effects="mutating",
                capabilities=frozenset({"social_content.decision.write"}),
                permission=OperatorAuthority(
                    kind="social_content.mutate",
                    verifier="core.command_registry.groups.social_content_group:_gate_operator",
                ),
                handler=Handler("core.command_registry.groups.social_content_group:_handle_draft_decide"),
                availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
                fault_bindings=_MUTATING_FAULTS,
                exit_codes=(0, 2, 21, 30),
            )
        )
    reg.add(
        CommandSpec(
            command_id="metrics.import",
            group="social_content",
            description="Import one metric observation (operator-entered by default)",
            input_schema=MetricsImportInput,
            effects="mutating",
            capabilities=frozenset({"social_content.metrics.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_metrics_import"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 21, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="metrics.inspect",
            group="social_content",
            description="List metric observations for a project",
            input_schema=CampaignScopeInput,
            effects="read_only",
            capabilities=frozenset({"social_content.metrics.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_metrics_inspect"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="hypothesis.inspect",
            group="social_content",
            description="List content hypotheses for a project",
            input_schema=CampaignScopeInput,
            effects="read_only",
            capabilities=frozenset({"social_content.hypothesis.read"}),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_hypothesis_inspect"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            exit_codes=(0, 2),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="hypothesis.close",
            group="social_content",
            description="Close a hypothesis with a typed outcome (never 'proven')",
            input_schema=HypothesisCloseInput,
            effects="mutating",
            capabilities=frozenset({"social_content.hypothesis.write"}),
            permission=ApprovalGate(
                kind="social_content.mutate",
                verifier="core.command_registry.groups.social_content_group:_gate_mutation",
            ),
            handler=Handler("core.command_registry.groups.social_content_group:_handle_hypothesis_close"),
            availability=Availability("core.command_registry.groups.social_content_group:_probe_store"),
            fault_bindings=_MUTATING_FAULTS,
            exit_codes=(0, 2, 21, 30),
        )
    )
