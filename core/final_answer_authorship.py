"""Who is allowed to WRITE the final answer, decided once, by one authority.

WHY THIS EXISTS
---------------
`core.grounding_publication` is the one gate between a generated answer and the wire, and it
asks exactly one question: *is this answer supported by what the turn retrieved?* It asks it
only of turns `core.execution_requirements` marked ``current_information_required``. Every
DIRECT turn -- an explanation, a definition, "how does X work" -- has no lifecycle row, and the
gate returns before reading a byte.

That is right for the EVIDENCE question and silent on a different one:

    was this model ever shown to be able to write this answer at all?

Nothing asked. A model registered by Ollama discovery, never probed, never measured, could
write the served bytes of an open-domain turn on the strength of having been installed.
`core.local_model_tool_certification` already measures exactly that -- per model identity, per
fingerprint, with a typed state -- and its own store stamped ``routing_effect: "none"`` on
every row it returned. The measurement was taken and then read by nobody.

Turning local models off hides this. It does not fix it, and it contradicts the local-first
promise. The missing piece is an eligibility AUTHORITY, not a prompt and not a model-name rule.

WHAT IT DECIDES, AND FROM WHAT
------------------------------
One question, asked of one identity: **may THIS model take the final-answer author role on THIS
turn?** The inputs are all already in the runtime; nothing new is registered:

* the answer role -- `core.turn_model_call_ledger`'s own ``call_role`` vocabulary, where
  ``answer_generation`` is the authoring role and everything else is a supporting one;
* the task class -- `core.agent_runtime.grounded_mode.AnswerMode`;
* the certification state -- `core.local_model_tool_certification.certification_status`, whose
  states (``verified`` / ``degraded`` / ``incompatible`` / ``stale`` / ``probing`` /
  ``unknown``) are the certification vocabulary this module reuses verbatim;
* the escalation permission -- `core.cloud_escalation_policy`, the operator's configured
  answer to "may this runtime burst to my own cloud model?";
* the local-only lane -- `core.auto_local_only_mode` / `core.local_model_policy`.

JURISDICTION -- DELIBERATELY NARROW, AND NOT A LANE HEURISTIC
------------------------------------------------------------
Two conditions must both hold before this module has anything to say:

1. **The certification authority can actually measure this model.** That is the SAME predicate
   `run_local_model_tool_certification` enforces before it will run at all
   (`certification_applies`): an OpenAI-compatible loopback endpoint. A model the probe cannot
   reach by construction cannot be asked to hold a probe result, so for those lanes the state
   is ``not_applicable`` and the operator's own configuration is the attestation. This is a
   statement about what the measurement can reach -- not about a model's size, its name, or how
   many billions of parameters it claims.

2. **The answer rests on the model alone.** A turn whose bytes are backed by something the
   runtime minted -- a deterministic tool result, a typed observation, bound evidence -- is a
   model RENDERING evidence, and rendering is not authoring out of weights. Those turns pass
   untouched, which is what keeps a local-first runtime a working product: an uncertified local
   model may still read files, run tools, summarise what a lane observed, classify, plan and
   extract. What it may not do is originate an open-domain answer nothing backs.

WHAT HAPPENS WHEN THE ANSWER IS NO
----------------------------------
* **Escalate**, when a certified configured model is available and permitted. Both identities
  are recorded -- the model the turn asked for and the model that will write -- because a
  silent substitution is the failure this runtime has already paid for once.
* **Refuse**, typed and honest, when no eligible author exists. The model's bytes do not ship.
  What ships names the model, the role it could not take, and why.

WHERE IT BINDS
--------------
`core.finalization.finalize_answer`, beside the grounding gate and for the same reason: it is
the single authority every semantic answer traverses exactly once, through all six transport
doors. A check anywhere else certifies one door and leaves five open. The decision is RECORDED
at `core.turn_model_call_ledger.record_served_usage` -- the one seam that names the response
that was actually served rather than a call that was merely attempted.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.enum_compat import StrEnum

#: The first words of the refusal. Load-bearing twice: it is what tells a reader the answer was
#: withheld, and it is how this transform recognises its own output and stays a fixed point --
#: finalization admits identical duplicates, so gating already-gated bytes must return them
#: byte-identically.
UNCERTIFIED_AUTHOR_NOTICE_LEAD = "I can't publish this answer:"

#: How many turns' decisions are kept. Same order of magnitude as the model-call ledger's own
#: bound; a decision is only read once, by the finalization of the turn that made it.
_MAX_TRACKED_TURNS = 512

#: The certification state reported for a model the probe cannot reach by construction. Not a
#: member of ``CERTIFICATION_STATES``: those describe a run that happened, and this describes
#: the absence of a question rather than the absence of an answer.
STATE_NOT_APPLICABLE = "not_applicable"

CERTIFICATION_SOURCE_MEASURED = "measured_tool_certification"
CERTIFICATION_SOURCE_OPERATOR = "operator_configured_lane"
CERTIFICATION_SOURCE_NONE = "none"

REASON_CERTIFIED = "certified_author"
REASON_SUPPORTING_ROLE = "supporting_role_not_authorship"
REASON_UNCERTIFIED_AUTHOR = "uncertified_author"
REASON_ESCALATED = "escalated_to_certified_author"
REASON_NO_ELIGIBLE_AUTHOR = "no_eligible_author"
REASON_CLOUD_ESCALATION_NOT_PERMITTED = "cloud_escalation_not_permitted"
REASON_LOCAL_ONLY_NO_ELIGIBLE_AUTHOR = "local_only_no_eligible_author"
REASON_RUNTIME_SUPPORTED = "runtime_supported_answer"
REASON_AUTHOR_UNRESOLVED = "author_identity_unresolved"
REASON_DETERMINISTIC_AUTHOR = "runtime_composed_output"
REASON_BLOCKED_BEFORE_GENERATION = "blocked_before_generation"


class AuthorRole(StrEnum):
    """The role a model call takes in a turn.

    The values are `core.turn_model_call_ledger`'s own ``call_role`` tokens, not a new
    taxonomy: ``answer_generation`` is what the router stamps on the call that writes the
    served answer, and ``conductor_generation`` is what the planner hook stamps on its own
    bounded generations.
    """

    FINAL_ANSWER = "answer_generation"
    #: Bytes the RUNTIME composed -- a tool result rendered, an arithmetic answer, a typed
    #: refusal. An explicit assertion about what the output IS, and the only way non-model output
    #: may pass this authority. It is deliberately NOT the same thing as "no identity could be
    #: read": one is a positive statement, the other is the absence of one, and only the first is
    #: safe to publish.
    DETERMINISTIC = "runtime_composed"
    PLANNING = "conductor_generation"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    TOOL_INTENT = "tool_intent"
    #: The independent verification lane. Its bytes are never served: the lane consumes them
    #: internally as a PASS/FAIL verdict (``_verifier_verdict``) and the answer it judged is
    #: delivered by the primary lane. A verifier is therefore a supporting role exactly like a
    #: planner or classifier -- refusing to CALL it for lacking a final-answer author
    #: certification misreads a verdict as authorship and reports the lane "failed" when it was
    #: never allowed to run (measured: every ``task_role: verifier`` request was refused with
    #: ``author_not_certified_for_final_answer`` and the turn shipped "unverified").
    VERIFIER = "verification"


#: The one role this module governs. Everything else passes.
FINAL_ANSWER_ROLE = str(AuthorRole.FINAL_ANSWER.value)

#: `record_provider_call` documents ``call_role`` as empty for ordinary answer calls, so an
#: empty role on the SERVED response is the authoring role, not an unknown one.
_AUTHORING_ROLES = frozenset({"", FINAL_ANSWER_ROLE})


@dataclass(frozen=True)
class AuthorCertification:
    """What the runtime holds about one exact model identity's fitness to author."""

    certified: bool
    state: str
    source: str
    provider_id: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "certified": bool(self.certified),
            "state": str(self.state),
            "source": str(self.source),
            "provider_id": str(self.provider_id),
            "detail": str(self.detail),
        }


@dataclass(frozen=True)
class AuthorshipDecision:
    """One turn's answer to "who may write this?", and every input that produced it."""

    eligible: bool
    author_role: str
    task_class: str
    requested_model: str = ""
    selected_model: str = ""
    certification_state: str = STATE_NOT_APPLICABLE
    certification_source: str = CERTIFICATION_SOURCE_NONE
    reason: str = ""
    escalated: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.final_answer_authorship.v1",
            "eligible": bool(self.eligible),
            "author_role": str(self.author_role),
            "task_class": str(self.task_class),
            "requested_model": str(self.requested_model),
            "selected_model": str(self.selected_model),
            "certification_state": str(self.certification_state),
            "certification_source": str(self.certification_source),
            "reason": str(self.reason),
            "escalated": bool(self.escalated),
            "detail": str(self.detail),
        }

    def refusal_text(self, *, attempted_failed: tuple[str, ...] | list[str] | None = None) -> str:
        """What ships instead of the model's bytes. Names the model, the role and the cause.

        ``attempted_failed`` carries the certified authors this turn DID call and that failed
        before producing usable output (the router's ranked loop records them). Without them the
        refusal can only describe the runtime's options ("no certified model was available and
        permitted"); with them it reports what actually happened -- measured live 2026-09-08,
        the refusal claimed no certified model was tried while two free-cloud authors had been
        called and had failed, which sent the operator to debug routing that worked.
        """

        attempted = tuple(str(name).strip() for name in (attempted_failed or ()) if str(name).strip())
        author = self.selected_model or self.requested_model or "the model that wrote it"
        if self.reason == REASON_AUTHOR_UNRESOLVED:
            cause = (
                "this runtime could not resolve which configured model wrote it, so there is no "
                "identity to hold the answer to."
            )
        elif self.reason == REASON_BLOCKED_BEFORE_GENERATION:
            if attempted:
                tried = ", ".join(f"`{name}`" for name in attempted)
                cause = (
                    f"`{author}` is not certified to author on this runtime, so the call was "
                    f"never made; the certified model{'s' if len(attempted) > 1 else ''} this "
                    f"turn did call ({tried}) each failed before producing a usable answer."
                )
            else:
                cause = (
                    f"`{author}` is not certified to author on this runtime, so the call was never "
                    "made, and no certified model was available and permitted to write it instead."
                )
        elif self.reason == REASON_UNCERTIFIED_AUTHOR:
            cause = (
                f"`{author}` has no certification run on this runtime, so it is not certified to "
                "take the final-answer role."
            )
        elif self.reason == REASON_LOCAL_ONLY_NO_ELIGIBLE_AUTHOR:
            cause = (
                f"`{author}` has no certification run on this runtime, and this turn is in Local "
                "Only, so no configured cloud model may be reached to write it instead."
            )
        elif self.reason == REASON_CLOUD_ESCALATION_NOT_PERMITTED:
            cause = (
                f"`{author}` has no certification run on this runtime. A configured cloud model "
                "could write it, but cloud escalation is not enabled for this runtime."
            )
        elif attempted:
            tried = ", ".join(f"`{name}`" for name in attempted)
            cause = (
                f"`{author}` has no certification run on this runtime; the certified model"
                f"{'s' if len(attempted) > 1 else ''} this turn did call ({tried}) each failed "
                "before producing a usable answer."
            )
        else:
            cause = (
                f"`{author}` has no certification run on this runtime, and no certified model was "
                "available to write it instead."
            )
        return (
            f"{UNCERTIFIED_AUTHOR_NOTICE_LEAD} nothing this turn retrieved, computed or observed "
            f"backs it, so the answer would rest on the writing model alone. {cause}\n\n"
            "What would make this answerable: run the local model tool certification for that "
            "model, pick a certified model, or ask something this runtime can look up, compute "
            "or read for you."
        )


# --------------------------------------------------------------------------- certification


_CERTIFICATION_CACHE_TTL_SECONDS = 30.0
_CERT_CACHE: dict[str, tuple[float, AuthorCertification]] = {}
_CERT_LOCK = threading.Lock()


def _now() -> float:
    import time

    return time.monotonic()


def _certification_cache_key(manifest: Any) -> str:
    """The identity a certification verdict is cached under: the model AT its destination. The
    measured fingerprint binds the endpoint (``base_identity``), so a lane re-registered at another
    destination under the same provider id must never be answered from the previous destination's
    verdict (revision-5 review R2: within the TTL a replaced custom endpoint inherited the old
    endpoint's authorship, and a newly measured one inherited the old refusal)."""
    provider_id = str(getattr(manifest, "provider_id", "") or "")
    runtime_config = getattr(manifest, "runtime_config", None) or {}
    try:
        base_url = str(runtime_config.get("base_url") or "").strip()
    except AttributeError:
        base_url = ""
    from core.local_model_tool_certification import normalized_base_identity

    return f"{provider_id}\n{normalized_base_identity(base_url) if base_url else ''}"


def invalidate_author_certification(manifest: Any = None) -> None:
    """Forget cached certification verdicts for one model identity — at every destination it was
    cached under — or all of them when no manifest is given. The certification authority calls this
    whenever it records a new measured run, so the next eligibility decision reads that run instead
    of serving the superseded verdict for the rest of the cache lifetime (revision-5 review R2
    follow-up, found on the served path: a destination certified seconds after a refused turn stayed
    refused for up to the TTL)."""
    provider_id = str(getattr(manifest, "provider_id", "") or "") if manifest is not None else ""
    with _CERT_LOCK:
        if not provider_id:
            _CERT_CACHE.clear()
            return
        for key in [key for key in _CERT_CACHE if key.split("\n", 1)[0] == provider_id]:
            _CERT_CACHE.pop(key, None)


def author_certification(
    manifest: Any,
    *,
    db_path: str | Path | None = None,
    use_cache: bool = True,
) -> AuthorCertification:
    """What this exact model identity is certified for, read never guessed.

    A short TTL cache sits in front of `certification_status` because that call performs a cheap
    backend identity read against the local endpoint (it has to: the backend version is part of
    the fingerprint a run was taken under), and this authority is consulted on the serving path.
    """

    provider_id = str(getattr(manifest, "provider_id", "") or "")
    if manifest is None:
        return AuthorCertification(
            certified=False,
            state=STATE_NOT_APPLICABLE,
            source=CERTIFICATION_SOURCE_NONE,
            detail="no manifest resolved for the answering model",
        )
    cache_key = _certification_cache_key(manifest)
    if use_cache and provider_id:
        with _CERT_LOCK:
            cached = _CERT_CACHE.get(cache_key)
        if cached is not None and (_now() - cached[0]) < _CERTIFICATION_CACHE_TTL_SECONDS:
            return cached[1]

    from core.local_model_tool_certification import certification_applies

    if not certification_applies(manifest):
        # The probe is restricted to an OpenAI-compatible loopback endpoint by construction, so
        # this lane can never hold a probe result. The operator configured it -- registered it,
        # enabled it, and for a remote lane supplied their own credentials -- and that
        # configuration is the attestation available for it.
        certification = AuthorCertification(
            certified=bool(getattr(manifest, "enabled", True)),
            state=STATE_NOT_APPLICABLE,
            source=CERTIFICATION_SOURCE_OPERATOR,
            provider_id=provider_id,
            detail="lane is outside the local tool-certification probe's reach",
        )
    else:
        from core.local_model_tool_certification import certification_status

        try:
            status = certification_status(manifest, db_path=db_path)
        except Exception:
            status = {"state": "unknown"}
        state = str(status.get("state") or "unknown")
        certification = AuthorCertification(
            certified=state == "verified",
            state=state,
            source=(
                CERTIFICATION_SOURCE_MEASURED if state == "verified" else CERTIFICATION_SOURCE_NONE
            ),
            provider_id=provider_id,
            detail=str(status.get("run_id") or ""),
        )

    if use_cache and provider_id:
        with _CERT_LOCK:
            _CERT_CACHE[cache_key] = (_now(), certification)
    return certification


def _task_class_for(request_text: str, task_class: Any) -> str:
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for

    if isinstance(task_class, AnswerMode):
        return str(task_class.value)
    named = str(task_class or "").strip()
    if named:
        return named
    try:
        return str(answer_mode_for(str(request_text or "")).value)
    except Exception:
        return str(AnswerMode.DIRECT.value)


def _cloud_escalation_permitted() -> bool:
    """The operator's configured answer to "may this runtime reach my cloud model?".

    Read from `core.cloud_escalation_policy`, which already owns the question. ``off`` is the
    default and means no. Fail-closed: a policy that cannot be read is not a permission.
    """

    try:
        from core.cloud_escalation_policy import MODE_OFF, load_policy

        return str(load_policy().normalized().mode or MODE_OFF).strip().lower() != str(MODE_OFF)
    except Exception:
        return False


def decide_final_answer_author(
    *,
    request_text: str = "",
    task_class: Any = None,
    author_role: Any = FINAL_ANSWER_ROLE,
    requested_manifest: Any = None,
    requested_model: str = "",
    candidates: Any = (),
    local_only: bool | None = None,
    cloud_escalation_permitted: bool | None = None,
    allow_escalation: bool = True,
    db_path: str | Path | None = None,
) -> AuthorshipDecision:
    """The one decision. Returns eligibility plus every input that produced it.

    ``allow_escalation`` is the difference between asking BEFORE and asking AFTER. Before a call,
    "a certified model is available" is a route: take it. After the bytes exist, it is not --
    the model that already wrote them either held the role or did not, and an escalation that
    was merely POSSIBLE may not retroactively bless the text an uncertified model produced. The
    served-usage recorder therefore asks with escalation off, and gets a verdict about the
    writer rather than about the runtime's options.
    """

    role = str(getattr(author_role, "value", author_role) or "").strip()
    resolved_class = _task_class_for(request_text, task_class)
    asked_for = str(
        requested_model or getattr(requested_manifest, "provider_id", "") or ""
    ).strip()

    if role == str(AuthorRole.DETERMINISTIC.value):
        # The positive assertion. Runtime-composed bytes name themselves, and never borrow the
        # unresolved-identity exit below.
        return AuthorshipDecision(
            eligible=True,
            author_role=role,
            task_class=resolved_class,
            requested_model=asked_for,
            selected_model=asked_for,
            reason=REASON_DETERMINISTIC_AUTHOR,
        )
    if role not in _AUTHORING_ROLES:
        # Classifying, planning, extracting and calling tools are not authorship. A model that
        # may not write the answer must still be usable for every role it is fit for -- that is
        # the difference between an eligibility boundary and switching local models off.
        return AuthorshipDecision(
            eligible=True,
            author_role=role,
            task_class=resolved_class,
            requested_model=asked_for,
            selected_model=asked_for,
            reason=REASON_SUPPORTING_ROLE,
        )

    if requested_manifest is None:
        # FAIL CLOSED. This used to pass, on an availability argument: a registry read that fails
        # must not refuse every answer in the process. That trade was wrong. "We cannot tell who
        # wrote this" is the single state in which model-written bytes must never ship, and it is
        # exactly the state a storage failure produces -- the moment the runtime knows least is
        # not the moment to relax. Deterministic output does not need this exit: it has
        # `AuthorRole.DETERMINISTIC`, which is a positive assertion about what the bytes are
        # rather than an inference from an identity nobody could read.
        return AuthorshipDecision(
            eligible=False,
            author_role=FINAL_ANSWER_ROLE,
            task_class=resolved_class,
            requested_model=asked_for,
            selected_model="",
            certification_state=STATE_NOT_APPLICABLE,
            certification_source=CERTIFICATION_SOURCE_NONE,
            reason=REASON_AUTHOR_UNRESOLVED,
            detail="no configured manifest resolved for the answering model",
        )
    requested_certification = author_certification(requested_manifest, db_path=db_path)
    if requested_certification.certified:
        return AuthorshipDecision(
            eligible=True,
            author_role=FINAL_ANSWER_ROLE,
            task_class=resolved_class,
            requested_model=asked_for,
            selected_model=asked_for,
            certification_state=requested_certification.state,
            certification_source=requested_certification.source,
            reason=REASON_CERTIFIED,
        )

    resolved_local_only = bool(local_only) if local_only is not None else False
    cloud_permitted = (
        bool(cloud_escalation_permitted)
        if cloud_escalation_permitted is not None
        else _cloud_escalation_permitted()
    )
    from core.local_model_policy import manifest_is_local

    withheld_cloud_candidate = False
    for candidate in (list(candidates or []) if allow_escalation else []):
        candidate_id = str(getattr(candidate, "provider_id", "") or "")
        if not candidate_id or candidate_id == asked_for:
            continue
        certification = author_certification(candidate, db_path=db_path)
        if not certification.certified:
            continue
        is_local = bool(manifest_is_local(candidate))
        if not is_local and (resolved_local_only or not cloud_permitted):
            # A configured cloud lane is not, by existing, permission to use it. The escalation
            # is withheld and NAMED, so the refusal below can say which door was closed.
            withheld_cloud_candidate = True
            continue
        return AuthorshipDecision(
            eligible=True,
            author_role=FINAL_ANSWER_ROLE,
            task_class=resolved_class,
            requested_model=asked_for,
            selected_model=candidate_id,
            certification_state=certification.state,
            certification_source=certification.source,
            reason=REASON_ESCALATED,
            escalated=True,
            detail=(
                f"{asked_for or 'the requested model'} is not certified to author; "
                f"{candidate_id} is"
            ),
        )

    if not allow_escalation:
        # Asked after the fact: name the writer's own state, not the runtime's options.
        reason = REASON_UNCERTIFIED_AUTHOR
    elif resolved_local_only:
        reason = REASON_LOCAL_ONLY_NO_ELIGIBLE_AUTHOR
    elif withheld_cloud_candidate:
        reason = REASON_CLOUD_ESCALATION_NOT_PERMITTED
    else:
        reason = REASON_NO_ELIGIBLE_AUTHOR
    return AuthorshipDecision(
        eligible=False,
        author_role=FINAL_ANSWER_ROLE,
        task_class=resolved_class,
        requested_model=asked_for,
        selected_model="",
        certification_state=requested_certification.state,
        certification_source=requested_certification.source,
        reason=reason,
        detail=requested_certification.detail,
    )


def escalation_target(
    *,
    served_provider_id: str = "",
    served_manifest: Any = None,
    request_text: str = "",
    author_role: Any = FINAL_ANSWER_ROLE,
    candidates: Any = (),
    local_only: bool | None = None,
    cloud_escalation_permitted: bool | None = None,
    db_path: str | Path | None = None,
) -> AuthorshipDecision | None:
    """The certified model that should have written this, or None.

    Returned only when the model that DID write is not certified to author and a different,
    certified, permitted model is configured. The caller re-runs the turn against the named
    model and records both identities -- which is what makes this an escalation rather than a
    substitution: the model the turn asked for is never erased from the record.
    """

    decision = decide_final_answer_author(
        request_text=request_text,
        author_role=author_role,
        requested_manifest=served_manifest,
        requested_model=served_provider_id,
        candidates=candidates,
        local_only=local_only,
        cloud_escalation_permitted=cloud_escalation_permitted,
        allow_escalation=True,
        db_path=db_path,
    )
    if decision.escalated and decision.selected_model and decision.selected_model != served_provider_id:
        return decision
    return None


#: The role this call takes, resolved from what the CALLER declared -- never guessed from the
#: model, the prompt or the lane. An explicit server-authored role wins; the planner and conductor
#: identify their own generations in the request metadata; a tool_intent turn is asking for a call,
#: not for prose. Everything else is the answer.
def resolve_author_role(
    *,
    source_context: Any = None,
    request_metadata: Any = None,
    output_mode: str = "",
) -> str:
    context = source_context if isinstance(source_context, dict) else {}
    declared = str(context.get("model_call_role") or "").strip()
    if declared and declared != FINAL_ANSWER_ROLE:
        return declared
    metadata = request_metadata if isinstance(request_metadata, dict) else {}
    if str(metadata.get("planner_call_kind") or "").strip():
        return str(AuthorRole.PLANNING.value)
    if metadata.get("conductor_node"):
        return str(AuthorRole.PLANNING.value)
    # The verification lane stamps its own role on the request (memory_first_router's
    # ``_verify_primary_response`` sets ``task_role: verifier`` with
    # ``defer_stream_until_verified``); its output is a verdict, never served bytes.
    if str(metadata.get("task_role") or "").strip().lower() == "verifier":
        return str(AuthorRole.VERIFIER.value)
    if str(output_mode or "").strip() == "tool_intent":
        return str(AuthorRole.TOOL_INTENT.value)
    return FINAL_ANSWER_ROLE


def precall_author_verdict(
    *,
    manifest: Any,
    source_context: dict[str, Any] | None = None,
    request_metadata: Any = None,
    output_mode: str = "",
    request_text: str = "",
    db_path: str | Path | None = None,
) -> AuthorshipDecision | None:
    """May THIS manifest be called to write the final answer? Asked BEFORE the call.

    Returns None when the question does not arise -- a planner, conductor, classifier or
    tool_intent call is not authorship and this authority has nothing to say about it. Returns a
    decision otherwise, and an ineligible one means the call must not be made at all.

    The publication gate downstream is unchanged and stays the independent backstop. The
    difference this makes is not safety, it is COST and truth: refusing after the fact spends the
    input, spends the wall clock, and produces a full answer that then has to be thrown away --
    and a runtime that generates text it always intended to discard is lying to its own operator
    about what it did.

    Escalation is deliberately NOT performed here. This seam sees ONE candidate at a time, and
    the caller is already iterating ranked candidates: refusing this one lets the next be tried,
    which is the escalation, performed by the code that owns candidate order.
    """

    role = resolve_author_role(
        source_context=source_context,
        request_metadata=request_metadata,
        output_mode=output_mode,
    )
    if role != FINAL_ANSWER_ROLE:
        return None
    decision = decide_final_answer_author(
        request_text=request_text,
        author_role=role,
        requested_manifest=manifest,
        requested_model=str(getattr(manifest, "provider_id", "") or ""),
        allow_escalation=False,
        db_path=db_path,
    )
    if decision.eligible:
        return decision
    return AuthorshipDecision(
        eligible=False,
        author_role=FINAL_ANSWER_ROLE,
        task_class=decision.task_class,
        requested_model=decision.requested_model,
        selected_model="",
        certification_state=decision.certification_state,
        certification_source=decision.certification_source,
        reason=REASON_BLOCKED_BEFORE_GENERATION,
        detail=decision.detail,
    )


# ------------------------------------------------------------------ the turn's own record


@dataclass
class AuthorshipRecord:
    """What one turn decided about its author, indexed by that turn's own identity.

    Indexed the way `core.grounding_lifecycle` indexes a lifecycle, and for the same reason:
    finalization presents a turn id, the writing seam saw a request id, and a record findable
    under only one of the two is a record the publication seam cannot find.
    """

    record_id: str
    identity: Any
    decision: AuthorshipDecision
    model_authored: bool = False
    supported_by_runtime: bool = False
    served_models: tuple[str, ...] = ()
    #: Identities this turn refused to CALL, in refusal order. Distinct from a decision about
    #: bytes: these models wrote nothing, because they were never asked to.
    blocked_before_generation: tuple[str, ...] = ()
    #: Certified authors this turn DID call that failed before producing usable output, in
    #: attempt order. Distinct from `blocked_before_generation` (never called) and from
    #: `served_models` (wrote bytes). The publication refusal reads this so it reports the
    #: attempts that actually happened instead of claiming none were permitted.
    attempted_failed: tuple[str, ...] = ()
    #: The rows the runtime minted this turn, as `core.claim_support` reads them. Carried so the
    #: publication authority can adjudicate a tool-backed answer per CLAIM instead of the gate
    #: blessing the whole answer because the turn observed something.
    support_rows: tuple[dict[str, Any], ...] = ()
    request_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.decision.to_dict())
        payload.update(
            {
                "record_id": self.record_id,
                "model_authored": bool(self.model_authored),
                "supported_by_runtime": bool(self.supported_by_runtime),
                "served_models": list(self.served_models),
                "blocked_before_generation": list(self.blocked_before_generation),
                "attempted_failed": list(self.attempted_failed),
            }
        )
        return payload

    @property
    def must_refuse(self) -> bool:
        """The bytes may not ship.

        Two ways to get here, and they are different facts. Either a model WROTE them and is not
        certified to take the author role, or every candidate that could have written them was
        refused before the call and none of the escalation targets was available and permitted --
        in which case the bytes on the table are whatever the runtime composed after the lane came
        back empty, and presenting that as an answer to the question would be its own untruth.

        A turn whose bytes ARE backed by what the runtime minted is decided per claim by
        `authored_publication_verdict`, not here; this property is the whole-answer verdict for
        the case where nothing backs them.
        """

        if self.decision.eligible:
            return False
        if bool(self.model_authored) and not self.supported_by_runtime:
            return True
        # P0 MIXED-DEMAND TERMINAL CLOSURE — the third way now carries its own
        # docstring's exception. "The lane came back empty" is the premise of
        # this refusal, and a turn the runtime MINTED rows for did not come
        # back empty: its composed bytes are backed output, and backed output
        # is decided per claim by `authored_publication_verdict`, "not here" —
        # the property's own words, which the code did not implement for
        # runtime-composed answers (only for model-authored ones). Measured on
        # the served surface: a three-demand composite turn executed its clock
        # and conversion units, recorded them satisfied, and still shipped the
        # blocked-lane refusal because this branch refused bytes nothing about
        # them backed the refusal of.
        if bool(self.supported_by_runtime) and self.support_rows:
            return False
        return bool(self.blocked_before_generation) and not self.model_authored


_LOCK = threading.Lock()
_RECORDS: OrderedDict[str, AuthorshipRecord] = OrderedDict()
_INDEX: dict[str, str] = {}


def _identity(source_context: Any):
    from core.grounding_lifecycle import turn_identity

    return turn_identity(source_context)


def _turn_conflicts(identity: Any, presented_turn: str) -> bool:
    """Does the presented turn id contradict every turn identity this record carries?

    Contradiction, not absence -- the same distinction `core.grounding_lifecycle` draws. A record
    that never learned a turn id agrees with any; only one that carries turn identities and
    matches none of them is refused.
    """

    known = tuple(
        token
        for token in (getattr(identity, "turn_id", ""), getattr(identity, "task_id", ""))
        if token
    )
    if not known:
        return False
    return presented_turn not in known


def _index_unlocked(record: AuthorshipRecord) -> None:
    for token in record.identity.tokens:
        _INDEX[token] = record.record_id


def _evict_unlocked() -> None:
    while len(_RECORDS) > _MAX_TRACKED_TURNS:
        record_id, _evicted = _RECORDS.popitem(last=False)
        for token, mapped in list(_INDEX.items()):
            if mapped == record_id:
                _INDEX.pop(token, None)


def _record_for_unlocked(identity) -> AuthorshipRecord | None:
    for token in identity.tokens:
        record_id = _INDEX.get(token)
        if not record_id:
            continue
        record = _RECORDS.get(record_id)
        if record is not None:
            _RECORDS.move_to_end(record_id)
            return record
    return None


def _is_authoring(decision: AuthorshipDecision) -> bool:
    return str(decision.author_role) == FINAL_ANSWER_ROLE


def record_authorship_decision(
    source_context: dict[str, Any] | None,
    decision: AuthorshipDecision,
    *,
    model_authored: bool = False,
    supported_by_runtime: bool = False,
    served_model: str = "",
    blocked_model: str = "",
    support_rows: Any = None,
    request_text: str = "",
    supersedes_author: bool = False,
) -> bool:
    """Put one turn's authorship decision into execution truth. Never raises.

    ``model_authored`` and ``supported_by_runtime`` are raise-only, exactly like
    `core.grounding_lifecycle.record_model_authorship`: a later supporting call may not lower a
    turn that already served model-written bytes, and a later unsupported generation may not
    erase the fact that the turn also observed something.
    """

    try:
        identity = _identity(source_context)
        if not identity.tokens:
            return False
        with _LOCK:
            record = _record_for_unlocked(identity)
            if record is None:
                import uuid

                record = AuthorshipRecord(
                    record_id=f"authorship-{uuid.uuid4().hex}",
                    identity=identity,
                    decision=decision,
                )
                _RECORDS[record.record_id] = record
            else:
                record.identity = _merge_identity(record.identity, identity)
                # RAISE-ONLY, with exactly one way down. A recorded refusal stands against
                # every later decision -- a classifier that ran after the answer, a second
                # candidate that merely got called, an escalation that was ATTEMPTED and
                # returned nothing -- because none of those wrote the bytes that are about to
                # ship. The single exception is `supersedes_author`, which only the escalation
                # caller sets, and only after it has confirmed the certified model actually
                # produced the served output. Without that asymmetry a failed escalation lifts
                # its own refusal and the uncertified author's text ships behind it.
                # A model that ACTUALLY SERVED and is eligible supersedes a PRE-CALL BLOCK --
                # that block was about a different model which, by construction, wrote nothing.
                # It does not supersede a refusal over bytes that already exist: measured, an
                # escalation target that was called and returned NOTHING would otherwise bless
                # the uncertified model's answer standing behind it.
                served_over_precall_block = bool(
                    decision.eligible
                    and _is_authoring(decision)
                    and model_authored
                    and not record.model_authored
                )
                if supersedes_author or served_over_precall_block or (
                    not record.must_refuse
                    and (_is_authoring(decision) or not _is_authoring(record.decision))
                ):
                    record.decision = decision
            record.model_authored = bool(
                record.model_authored or (model_authored and _is_authoring(decision))
            )
            record.supported_by_runtime = bool(
                record.supported_by_runtime or supported_by_runtime
            )
            served = str(served_model or "").strip()
            if served and served not in record.served_models:
                record.served_models = (*record.served_models, served)
            blocked = str(blocked_model or "").strip()
            if blocked and blocked not in record.blocked_before_generation:
                record.blocked_before_generation = (*record.blocked_before_generation, blocked)
            rows = tuple(
                dict(row) for row in list(support_rows or []) if isinstance(row, dict)
            )
            if rows:
                record.support_rows = rows
            text = str(request_text or "").strip()
            if text and not record.request_text:
                record.request_text = text
            _index_unlocked(record)
            _evict_unlocked()
        return True
    except Exception:
        return False


def record_authorship_attempts(
    source_context: dict[str, Any] | None,
    attempted_models: Any,
) -> bool:
    """Certified authors this turn CALLED and that failed before producing usable output.

    Written by the router's ranked loop at its exhaustion point, where the attempt list is
    ground truth. The publication gate reads it so a refusal reports the attempts that
    actually happened instead of claiming none were permitted (measured live 2026-09-08: two
    free-cloud authors were called and failed; the served refusal said no certified model was
    available and permitted). Never raises; a record that does not exist is created EMPTY --
    the attempts are turn truth whether or not any decision was recorded yet.
    """

    try:
        names: list[str] = []
        for name in list(attempted_models or []):
            cleaned = str(name or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
        if not names:
            return False
        identity = _identity(source_context)
        if not identity.tokens:
            return False
        with _LOCK:
            record = _record_for_unlocked(identity)
            if record is None:
                import uuid

                record = AuthorshipRecord(
                    record_id=f"authorship-{uuid.uuid4().hex}",
                    identity=identity,
                    decision=AuthorshipDecision(
                        eligible=False,
                        author_role=FINAL_ANSWER_ROLE,
                        task_class="",
                        reason=REASON_NO_ELIGIBLE_AUTHOR,
                    ),
                )
                _RECORDS[record.record_id] = record
            # A model the fence blocked before generation was NEVER called -- the ranked
            # loop's `attempted` list conflates typed refusals with real calls, and printing
            # a blocked model as "called and failed" is the same lie in the other direction
            # (measured on the rebuilt app 2026-09-08: the refusal named the blocked local
            # model as the author that had been called).
            callable_names = tuple(
                name for name in names if name not in record.blocked_before_generation
            )
            if not callable_names:
                return False
            merged = tuple(record.attempted_failed) + tuple(
                name for name in callable_names if name not in record.attempted_failed
            )
            record.attempted_failed = merged
            _index_unlocked(record)
            _evict_unlocked()
        return True
    except Exception:
        return False


def record_runtime_support(
    source_context: dict[str, Any] | None,
    *,
    support_rows: Any = None,
    request_text: str = "",
) -> bool:
    """The runtime MINTED rows that back this turn's composed bytes. Never raises.

    Makes no authorship claim at all — that is the point. A composite lane (the
    demand-owned mixed turn) executes units through deterministic lanes and
    composes their answers; the authorship QUESTION for those bytes is "backed
    runtime output", which the publication gate adjudicates per claim against
    exactly these rows. Without this call the turn's record keeps only whatever
    the router decided before any unit ran, and a pre-generation block refuses
    bytes the runtime had every right to compose.

    Raise-only like the fields it writes: a later call may add rows and support,
    never remove them, and the decision is untouched.
    """

    try:
        rows = tuple(
            dict(row) for row in list(support_rows or []) if isinstance(row, dict)
        )
        if not rows:
            return False
        identity = _identity(source_context)
        if not identity.tokens:
            return False
        with _LOCK:
            record = _record_for_unlocked(identity)
            if record is None:
                return False
            record.supported_by_runtime = True
            record.support_rows = rows
            text = str(request_text or "").strip()
            if text and not record.request_text:
                record.request_text = text
            _index_unlocked(record)
        return True
    except Exception:
        return False


def _merge_identity(stored, seen):
    from core.grounding_lifecycle import merge_turn_identity

    return merge_turn_identity(stored, seen)


def authorship_record_for_publication(*, turn_id: str = "") -> AuthorshipRecord | None:
    """The record of the turn now finalizing, or None when no model authored anything.

    None is the ordinary case and means "publish unchanged": a deterministic answer, a fast
    path, a turn whose model call never served. Resolution is by turn-unique identity only.
    """

    try:
        current = _identity(None)
        presented = str(turn_id or "").strip()
        tokens = [
            token
            for token in (current.bound_request_id, current.request_id, presented)
            if token
        ]
        with _LOCK:
            for token in tokens:
                record_id = _INDEX.get(token)
                if not record_id:
                    continue
                record = _RECORDS.get(record_id)
                if record is None:
                    continue
                # The same two refusals `lifecycle_for_publication` makes, and for the same
                # reason: ids ARE reused here (`fast:<session>:<hash>` in the fast lane, fixed
                # ids in tests), so one matching token is not enough. A record from a replaced
                # generation, or one that carries turn identities and matches NONE of the
                # presented one, is a different turn's verdict -- and consuming it would let an
                # earlier refusal land on a later answer.
                if not record.identity.same_generation_as(current):
                    continue
                if presented and _turn_conflicts(record.identity, presented):
                    continue
                _RECORDS.move_to_end(record_id)
                return record
    except Exception:
        return None
    return None


def authored_publication_verdict(
    content: str,
    *,
    request_text: str = "",
    observations: Any = (),
    author_role: Any = FINAL_ANSWER_ROLE,
):
    """Adjudicate a tool-backed, MODEL-AUTHORED answer per claim, with the existing authority.

    `supported_by_runtime` answered a question about the TURN -- did this turn observe anything --
    and then used it as a verdict about the ANSWER. Those are not the same question, and the gap
    between them is a whole sentence: one supported line let an unsupported one ride out beside
    it, which is precisely the laundering `core.grounding_publication` was built to stop for
    current-information turns.

    So this does not decide anything itself. It hands the bytes and the rows to
    `core.grounding_publication.publication_verdict` -- the same claim-to-source authority, the
    same PARTIAL semantics, the same withheld-work notice -- by presenting the turn's typed
    observations as what they are. No second support predicate exists, and none is wanted: a
    second one would be a second answer to the same question.

    Returns None when the question does not arise: a non-authoring role (runtime-composed bytes
    are not a generation, which that authority already knows), or a turn with no rows to match
    against, which the eligibility verdict decides instead.
    """

    role = str(getattr(author_role, "value", author_role) or "")
    if role != FINAL_ANSWER_ROLE:
        return None
    rows = [dict(row) for row in list(observations or []) if isinstance(row, dict)]
    if not rows:
        return None
    from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
    from core.grounding_publication import publication_verdict

    lifecycle = GroundingLifecycle(
        lifecycle_id="authorship-support",
        identity=TurnIdentity(),
        request_text=str(request_text or ""),
        typed_observations=tuple(rows),
        model_authored=True,
    )
    try:
        return publication_verdict(lifecycle, str(content or ""))
    except Exception:  # pragma: no cover - the authority must not end a working turn
        return None


def gate_authored_content(content: str, *, turn_id: str = "") -> tuple[str, dict[str, Any]]:
    """The publication gate. Returns the bytes that may ship, and what decided them.

    A fixed point: content already carrying the refusal is returned byte-identically, because
    finalization admits identical duplicates and refuses different-content re-finalization.
    """

    text = str(content or "")
    record = authorship_record_for_publication(turn_id=turn_id)
    if record is None:
        return text, {}
    payload = record.to_dict()
    if record.support_rows and record.decision.eligible is False and (
        record.model_authored or record.supported_by_runtime
    ):
        # Backed by something the runtime minted — whether a model then wrote
        # over it or the runtime composed it whole from executed units (P0
        # mixed-demand: a composite turn's clock/conversion blocks are minted
        # output even when no model authored a byte). The rows do not bless
        # the answer wholesale -- the claim authority decides, line by line,
        # exactly as it does for a current-information turn.
        verdict = authored_publication_verdict(
            text,
            request_text=record.request_text,
            observations=record.support_rows,
            author_role=record.decision.author_role,
        )
        if verdict is not None:
            payload["publication"] = f"claim_adjudicated:{verdict.state}"
            payload["withheld_claims"] = list(verdict.withheld_claims)
            payload["coverage"] = verdict.coverage
            return verdict.content, payload
    if not record.must_refuse:
        payload["publication"] = "published"
        return text, payload
    if UNCERTIFIED_AUTHOR_NOTICE_LEAD in text:
        payload["publication"] = "refused_idempotent"
        return text, payload
    payload["publication"] = "refused"
    payload["withheld_characters"] = len(text)
    return record.decision.refusal_text(attempted_failed=record.attempted_failed), payload


# ---- execution claims ------------------------------------------------------------------------
#
# A model-authored reply that PRESENTS a tool run -- a header naming one of this runtime's own
# tools, followed by the "query"/"results"/"source" lines a real run would leave -- is a claim
# about what the runtime did. Measured 2026-09-06 (private profile, real provider): after a
# refused turn, the next reply committed "**Tool: Web Search** / Search query: ... / Source:
# (simulated search results...)" as the answer. Nothing ran; the author was certified; no gate
# read the claim. The check is evidence-based, not lexical: the header must name a tool the
# runtime actually exposes (the capability graph's own intents), and it stands only when the
# turn's ledgers hold NO execution at all -- a tool run this turn is never second-guessed here.
_EXECUTION_HEADER_RE = re.compile(
    r"^[\s>*_#`\-]*(?:tool(?:\s+(?:call|result|output|action|use|invocation))?|action|function(?:\s+call)?"
    r"|command|running|ran|executing|executed|calling|called|invoking|invoked|using|used)"
    r"\s*[:\-\u2013\u2014(]\s*(?P<name>[^\n]{1,80})$",
    re.IGNORECASE,
)
_NAME_FOLD_RE = re.compile(r"[^a-z0-9]+")
EXECUTION_CLAIM_NOTICE_LEAD = "Removed from this reply:"


def _runtime_tool_names() -> dict[str, str]:
    """{folded variant: intent} for every intent the runtime exposes to the model.

    `web.search` is reachable as "web search", "web_search", "websearch" and "web.search";
    the map is rebuilt per call because the visible set is policy-dependent (owner-local,
    read-only mode) and cheap to enumerate.
    """
    variants: dict[str, str] = {}
    names: list[tuple[str, str]] = []
    try:
        from core import capability_graph as graph
        with contextlib.suppress(Exception):
            graph.ensure_registry_bootstrap()
        # Every REGISTERED implementation, not only the ones offered to the model this turn: a
        # reply that presents a run of a tool the runtime owns is a claim about the runtime
        # whether or not that tool was on this turn's menu (the served rig hides web.search
        # from the model and the fabricated transcript named it anyway).
        for impl in graph.all_implementations():
            intent = str(getattr(impl, "tool_intent", "") or "").strip().lower()
            if intent:
                names.append((intent, intent))
                label = str(getattr(impl, "label", "") or "").strip().lower()
                if label:
                    names.append((label, intent))
        for spec in list(graph.model_visible_specs()):
            intent = str((spec or {}).get("intent") or "").strip().lower() if isinstance(spec, dict) else ""
            if intent:
                names.append((intent, intent))
    except Exception:
        names = []
    for name, intent in names:
        if not intent or intent.startswith(("respond.", "operator.", "capability.")):
            continue
        parts = [part for part in re.split(r"[._\s]+", name) if part]
        if not parts:
            continue
        for form in (name, " ".join(parts), "".join(parts), "_".join(parts)):
            folded = _NAME_FOLD_RE.sub(" ", form.lower()).strip()
            if len(folded) >= 4:
                variants[folded] = intent
    return variants


def _claimed_intent(name: str, variants: dict[str, str]) -> str:
    folded = _NAME_FOLD_RE.sub(" ", str(name or "").lower()).strip()
    if not folded:
        return ""
    for variant, intent in sorted(variants.items(), key=lambda kv: len(kv[0]), reverse=True):
        if variant and (folded == variant or folded.startswith(variant + " ") or (" " + variant + " ") in (" " + folded + " ")):
            return intent
    return ""


def execution_claims(content: str) -> list[tuple[int, int, str]]:
    """`(start_line, end_line, intent)` for every block of `content` that presents a tool run.

    A block is the header line and every following non-blank line. Only headers naming one of
    the runtime's own intents count; prose that mentions a tool is not a claim to have run it.
    """
    lines = str(content or "").split("\n")
    variants = _runtime_tool_names()
    if not variants:
        return []
    claims: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        match = _EXECUTION_HEADER_RE.match(lines[index].strip())
        intent = _claimed_intent(match.group("name"), variants) if match else ""
        if not intent:
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip():
            end += 1
        claims.append((index, end, intent))
        index = end
    return claims


def _turn_executed_anything(turn_id: str, source_context: Any) -> bool:
    """Whether this turn's ledgers hold any execution: a tool intent, a retrieval, a typed
    observation or an effect receipt. Any one of them stands the check down."""
    try:
        from core.turn_model_call_ledger import turn_call_accounting
        if turn_call_accounting(source_context).get("tools"):
            return True
    except Exception:
        pass
    try:
        from core.grounding_lifecycle import lifecycle_for_publication
        lifecycle = lifecycle_for_publication(turn_id=turn_id)
        if lifecycle is not None and (
            lifecycle.retrieved_source_count or lifecycle.typed_observations or lifecycle.bound_notes
        ):
            return True
    except Exception:
        pass
    try:
        from core.effect_gateway import effect_receipts
        if effect_receipts():
            return True
    except Exception:
        pass
    return False


def gate_execution_claims(
    content: str, *, turn_id: str = "", source_context: Any = None
) -> tuple[str, dict[str, Any]]:
    """Withhold the blocks of a MODEL-AUTHORED reply that present a tool run this turn never made.

    Stands down for: runtime-composed bytes (nothing to fabricate), a turn whose ledgers hold any
    execution, and a request that set a stipulated or illustrative frame ("pretend you ran ...",
    "show me what a search transcript would look like") -- there the transcript is the thing the
    user asked for, distinguishable from a claim by the request itself. Returns the surviving
    bytes and the record; when nothing survives the caller sees "" and takes its no-answer path.
    """
    text = str(content or "")
    record = authorship_record_for_publication(turn_id=turn_id)
    model_authored = bool(getattr(record, "model_authored", False)) if record is not None else False
    lifecycle = None
    try:
        from core.grounding_lifecycle import lifecycle_for_publication
        lifecycle = lifecycle_for_publication(turn_id=turn_id)
    except Exception:
        lifecycle = None
    if not model_authored:
        model_authored = bool(lifecycle is not None and lifecycle.generated)
    if not model_authored and source_context is not None:
        # The third recorder: a DIRECT served turn has no lifecycle, and its authorship record
        # is keyed by a turn id the transport may not present; the turn's own model-call ledger
        # still knows a provider call completed (the seal receives the turn context).
        try:
            from core.turn_model_call_ledger import turn_call_accounting
            accounting = turn_call_accounting(source_context)
            model_authored = int(accounting.get("completed_calls") or 0) > 0 or bool(accounting.get("served_usage"))
        except Exception:
            model_authored = False
    if not model_authored:
        return text, {"publication": "stood_down:not_model_authored"}
    claims = execution_claims(text)
    if not claims:
        return text, {"publication": "stood_down:no_execution_claims"}
    request_text = str(getattr(record, "request_text", "") or "") or str(
        getattr(lifecycle, "request_text", "") or ""
    )
    try:
        from core.stipulated_frame import has_stipulated_frame
        if request_text and has_stipulated_frame(request_text):
            return text, {"execution_claims": [c[2] for c in claims], "publication": "illustrative_frame"}
    except Exception:
        pass
    if _turn_executed_anything(turn_id, source_context):
        return text, {"execution_claims": [c[2] for c in claims], "publication": "execution_evidence_present"}
    lines = text.split("\n")
    removed = set()
    for start, end, _intent in claims:
        removed.update(range(start, end))
    kept = [line for i, line in enumerate(lines) if i not in removed]
    body = "\n".join(kept).strip()
    intents = list(dict.fromkeys(c[2] for c in claims))
    named = ", ".join(f"`{intent}`" for intent in intents)
    notice = (
        f"{EXECUTION_CLAIM_NOTICE_LEAD} a described run of {named} that this turn did not perform. "
        "No tool ran, so nothing presented as its result was real."
    )
    payload = {
        "execution_claims": intents,
        "publication": "execution_claims_withheld",
        "blocks_removed": len(claims),
    }
    if not body:
        return "", payload
    return f"{body}\n\n{notice}", payload


def reset_for_tests() -> None:
    """Drop every tracked turn and cached certification. Test-support only."""

    with _LOCK:
        _RECORDS.clear()
        _INDEX.clear()
    with _CERT_LOCK:
        _CERT_CACHE.clear()


__all__ = [
    "CERTIFICATION_SOURCE_MEASURED",
    "CERTIFICATION_SOURCE_NONE",
    "CERTIFICATION_SOURCE_OPERATOR",
    "EXECUTION_CLAIM_NOTICE_LEAD",
    "FINAL_ANSWER_ROLE",
    "REASON_AUTHOR_UNRESOLVED",
    "REASON_BLOCKED_BEFORE_GENERATION",
    "REASON_CERTIFIED",
    "REASON_CLOUD_ESCALATION_NOT_PERMITTED",
    "REASON_DETERMINISTIC_AUTHOR",
    "REASON_ESCALATED",
    "REASON_LOCAL_ONLY_NO_ELIGIBLE_AUTHOR",
    "REASON_NO_ELIGIBLE_AUTHOR",
    "REASON_RUNTIME_SUPPORTED",
    "REASON_SUPPORTING_ROLE",
    "REASON_UNCERTIFIED_AUTHOR",
    "STATE_NOT_APPLICABLE",
    "UNCERTIFIED_AUTHOR_NOTICE_LEAD",
    "AuthorCertification",
    "AuthorRole",
    "AuthorshipDecision",
    "AuthorshipRecord",
    "author_certification",
    "authored_publication_verdict",
    "authorship_record_for_publication",
    "decide_final_answer_author",
    "escalation_target",
    "execution_claims",
    "gate_authored_content",
    "gate_execution_claims",
    "invalidate_author_certification",
    "record_authorship_decision",
    "record_runtime_support",
    "reset_for_tests",
]
