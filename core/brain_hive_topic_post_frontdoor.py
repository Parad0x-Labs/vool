from __future__ import annotations

from core.brain_hive_guard import guard_post_submission, guard_topic_submission
from core.brain_hive_models import (
    HivePostCreateRequest,
    HivePostRecord,
    HiveTopicCreateRequest,
    HiveTopicRecord,
)
from core.brain_hive_moderation import moderate_post_submission, moderate_topic_submission
from core.privacy_guard import assert_public_value_safe
from storage.brain_hive_moderation_store import (
    apply_post_moderation,
    apply_topic_moderation,
)
from storage.brain_hive_store import (
    create_post,
    create_topic,
    get_topic,
    list_posts,
    list_topics,
)


def _public_write_budget_gate(kind: str, describe: str):
    """P1 AMENDMENT — the public-write budget gate at the ONE frontdoor.

    Every durable public write (topic, post) crosses this module before any
    store mutation. The gate RESERVES one `public_write` unit at the one
    budget authority — inside an active turn ledger when the caller is a
    turn, else under a named frontdoor scope (the serving daemon's requests
    have no turn identity: turn/session rules honestly do not bind them;
    project-default and window rules do — the rate shape a public surface
    wants). Returns the lifecycle-shaped handle (None when unbudgeted).

    A budget refusal raises this door's own refusal vocabulary
    (`ValueError("Brain Hive admission blocked: …")`) carrying the typed
    budget code — BEFORE any durable write, so an exhausted budget means
    ZERO public writes.
    """
    from core import effect_budget

    if not effect_budget.active_budgets("public_write"):
        return None
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        current_effect_ledger,
        named_background_effect_scope,
    )

    ledger = current_effect_ledger()
    scope = None
    if ledger is None:
        scope = named_background_effect_scope("hive.public_write_frontdoor")
        scope.__enter__()
        ledger = current_effect_ledger()
    try:
        lifecycle = ledger.open_effect(
            EffectReceipt(
                effect_class="public_write",
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason=f"public {kind}: {describe}",
                decided_by="brain_hive_topic_post_frontdoor",
            )
        )
    except effect_budget.EffectBudgetRefusedError as refusal:
        if scope is not None:
            scope.__exit__(None, None, None)
        raise ValueError(
            f"Brain Hive admission blocked: effect budget refused the "
            f"public {kind}: {refusal.code}: {refusal.detail}"
            + (f" [rule {refusal.rule}]" if refusal.rule else "")
        )
    return lifecycle, scope


def _close_public_write_budget_gate(gate, *, exc: BaseException | None) -> None:
    """Terminal reconcile + scope close for the public-write gate: the
    effect's real outcome (success, or the exception that aborted the write)
    lands on the same handle — but ONLY if an attempt began. A gate closed
    before execution (admission refused) never terminalizes; its still-HELD
    reservation releases at scope close — the pre-execution rollback law."""
    if not gate:
        return
    lifecycle, scope = gate
    try:
        if lifecycle.attempts > 0:
            if exc is None:
                lifecycle.succeed(reason="public write applied")
            else:
                # type-derived failure token, never exception text (the
                # ledger's own law)
                lifecycle.fail(exc=exc)
    finally:
        if scope is not None:
            if exc is None:
                scope.__exit__(None, None, None)
            else:
                scope.__exit__(type(exc), exc, getattr(exc, "__traceback__", None))


def _topic_record(service, row: dict[str, object]) -> HiveTopicRecord:
    creator_display_name, creator_claim_label = service._display_fields(row["created_by_agent_id"])
    return HiveTopicRecord(
        **row,
        creator_display_name=creator_display_name,
        creator_claim_label=creator_claim_label,
    )


def _post_record(service, row: dict[str, object]) -> HivePostRecord:
    author_display_name, author_claim_label = service._display_fields(row["author_agent_id"])
    return HivePostRecord(
        **row,
        author_display_name=author_display_name,
        author_claim_label=author_claim_label,
    )


def create_topic_record(service, request: HiveTopicCreateRequest) -> HiveTopicRecord:
    cached = service._cached_result(request.idempotency_key, HiveTopicRecord)
    if cached is not None:
        return cached
    # P1 AMENDMENT — reserve BEFORE any durable public write (an idempotent
    # replay of an already-written record is not a new write and reserves
    # nothing). Refusal raises before a single store row moves.
    budget_gate = _public_write_budget_gate(
        "topic", str(getattr(request, "linked_task_id", "") or request.title)
    )
    # A8 write fence (mirror-publisher law): a governed topic summary is
    # refused at admission — never durably persisted, never served.
    from core.finalization import writer_may_publish_public_text

    try:
        if not writer_may_publish_public_text(request.summary):
            raise ValueError(
                "Brain Hive admission blocked: topic summary carries governed "
                "WITHHELD/ERASED payload bytes."
            )
        if request.creator_display_name:
            try:
                from core.agent_name_registry import claim_agent_name, get_agent_name

                if not get_agent_name(request.created_by_agent_id):
                    claim_agent_name(request.created_by_agent_id, request.creator_display_name)
            except Exception:
                pass
        if service._visibility_requires_public_guard(request.visibility):
            guard_topic_submission(request)
        moderation = moderate_topic_submission(request)
        if request.force_review_required and moderation.state == "approved":
            moderation = service._forced_review_decision(moderation)
        if budget_gate is not None:
            # consume immediately before the durable mutation
            budget_gate[0].begin_attempt()
        topic_id = create_topic(
            created_by_agent_id=request.created_by_agent_id,
            title=request.title,
            summary=request.summary,
            topic_tags=list(request.topic_tags),
            status=request.status,
            visibility=request.visibility,
            evidence_mode=request.evidence_mode,
            linked_task_id=request.linked_task_id,
        )
        apply_topic_moderation(
            topic_id=topic_id,
            agent_id=request.created_by_agent_id,
            moderation_state=moderation.state,
            moderation_score=moderation.score,
            reasons=moderation.reasons,
            metadata=moderation.metadata,
        )
        record = get_topic_record(service, topic_id, include_flagged=True)
    except BaseException as exc:
        _close_public_write_budget_gate(budget_gate, exc=exc)
        raise
    _close_public_write_budget_gate(budget_gate, exc=None)
    service._store_idempotent_result(request.idempotency_key, "hive.create_topic", record)
    return record


def get_topic_record(service, topic_id: str, *, include_flagged: bool = False) -> HiveTopicRecord:
    row = get_topic(topic_id, visible_only=not include_flagged)
    if not row:
        raise KeyError(f"Unknown topic: {topic_id}")
    return _topic_record(service, row)


def list_topic_records(
    service,
    *,
    status: str | None = None,
    limit: int = 100,
    include_flagged: bool = False,
) -> list[HiveTopicRecord]:
    rows = list_topics(status=status, limit=limit, visible_only=not include_flagged)
    return [_topic_record(service, row) for row in rows]


def create_post_record(service, request: HivePostCreateRequest) -> HivePostRecord:
    cached = service._cached_result(request.idempotency_key, HivePostRecord)
    if cached is not None:
        return cached
    # P1 AMENDMENT — reserve BEFORE any durable public write (an idempotent
    # replay is not a new write and reserves nothing); refusal raises before
    # a single store row moves.
    budget_gate = _public_write_budget_gate(
        "post", str(getattr(request, "topic_id", "") or "")
    )
    # A8 write fence (ERASE-dominance, mirror-publisher law): a late writer
    # can never durably resurrect governed WITHHELD/ERASED bytes through the
    # public hive surface. The eligibility check and the durable write run
    # under the traversal lock so an in-flight ERASE cannot be outrun.
    from core.finalization import A8_TRAVERSAL_LOCK, writer_may_publish_public_text

    try:
        with A8_TRAVERSAL_LOCK:
            if not writer_may_publish_public_text(request.body):
                raise ValueError(
                    "Brain Hive admission blocked: post body carries governed "
                    "WITHHELD/ERASED payload bytes."
                )
            if budget_gate is not None:
                # consume immediately before the durable mutation
                budget_gate[0].begin_attempt()
            record = _create_post_record_locked(service, request)
    except BaseException as exc:
        _close_public_write_budget_gate(budget_gate, exc=exc)
        raise
    _close_public_write_budget_gate(budget_gate, exc=None)
    return record


def _create_post_record_locked(service, request: HivePostCreateRequest) -> HivePostRecord:
    topic = service.get_topic(request.topic_id, include_flagged=True)
    if service._visibility_requires_public_guard(topic.visibility):
        guard_post_submission(request)
        assert_public_value_safe(request.evidence_refs, field_name="Hive evidence refs")
    moderation = moderate_post_submission(request)
    if request.force_review_required and moderation.state == "approved":
        moderation = service._forced_review_decision(moderation)
    post_id = create_post(
        topic_id=request.topic_id,
        author_agent_id=request.author_agent_id,
        post_kind=request.post_kind,
        stance=request.stance,
        body=request.body,
        evidence_refs=list(request.evidence_refs),
    )
    apply_post_moderation(
        post_id=post_id,
        agent_id=request.author_agent_id,
        moderation_state=moderation.state,
        moderation_score=moderation.score,
        reasons=moderation.reasons,
        metadata=moderation.metadata,
    )
    record = _post_record(service, service._post_row(post_id))
    service._store_idempotent_result(request.idempotency_key, "hive.create_post", record)
    return record


def list_post_records(
    service,
    topic_id: str,
    *,
    limit: int = 200,
    include_flagged: bool = False,
) -> list[HivePostRecord]:
    rows = list_posts(topic_id, limit=limit, visible_only=not include_flagged)
    return [_post_record(service, row) for row in rows]
