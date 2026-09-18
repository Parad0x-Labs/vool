from __future__ import annotations

from dataclasses import dataclass, field

from core import policy_engine
from core.local_model_bundles import model_parameter_billions
from core.model_capabilities import capability_score, required_capabilities
from core.model_health import circuit_is_open
from core.model_trust import provider_base_trust
from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class ModelSelectionRequest:
    task_kind: str
    output_mode: str = "plain_text"
    preferred_provider: str | None = None
    preferred_model: str | None = None
    preferred_source_types: list[str] = field(default_factory=list)
    require_license_metadata: bool = True
    forbid_bundled_weights: bool = True
    allow_paid_fallback: bool = False
    exclude_provider_ids: list[str] = field(default_factory=list)
    min_trust: float = 0.0
    # The operator selected the VOOL Auto Local Only lane for this turn. Distinct from
    # `policy_engine.local_only_mode()`, which is a machine-wide setting: this one is per-turn and
    # travels with the request, so one chat can be local-only while another is not. Both bind the
    # same way — as a HARD exclusion below, never as a score penalty a strong cloud model could
    # out-rank.
    local_only: bool = False


def provider_cost_class(manifest: ModelProviderManifest) -> str:
    # An explicit cost_class on the manifest wins. A bring-your-own-key remote lane (e.g. the
    # OpenRouter burst lane) reaches a paid provider through the generic openai_compatible
    # adapter, so the adapter_type heuristic below would misread it as "remote_unknown" and it
    # would never be metered or gated as paid. Marking it explicitly keeps the cap and the
    # paid-fallback exclusion binding on that lane.
    metadata = getattr(manifest, "metadata", None) or {}
    explicit = str(metadata.get("cost_class") or "").strip()
    if explicit in {"free_local", "remote_unknown", "paid_cloud"}:
        return explicit
    if manifest.adapter_type == "cloud_fallback_provider":
        return "paid_cloud"
    base_url = str(manifest.runtime_config.get("base_url") or "")
    if manifest.source_type in {"local_path", "subprocess"}:
        return "free_local"
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        return "free_local"
    return "remote_unknown"


def is_verified_free_cloud_manifest(manifest: ModelProviderManifest) -> bool:
    """Whether this manifest names a model the catalog prices at zero.

    This is what lets a model run without a paid reservation, so the verdict has to come from
    published pricing rather than from the model's name. A ``:free`` suffix is a string OpenRouter
    controls: measured, ``openai/gpt-4.1-mini:free`` passed a suffix check and posted a real
    request against the user's key. The catalog is consulted instead, and a model that is absent
    from it is treated as paid; a listed ``:free`` variant is free by the catalog's own reading
    (``model_is_free``), which admits OpenRouter's guaranteed-free tag with or without published
    per-token prices.

    Kept narrow to the BYOK OpenRouter lane so no other remote manifest can reach the exception.
    """
    if str(getattr(manifest, "provider_name", "") or "").strip().lower() != "openrouter-byok":
        return False
    model_name = str(getattr(manifest, "model_name", "") or "").strip()
    if not model_name:
        return False
    # A suffix check first, only ever to avoid a catalog lookup for the vast majority of models
    # that do not claim to be free. It can never admit one on its own.
    if not model_name.lower().endswith(":free"):
        return False
    try:
        from core.openrouter_catalog import model_is_free, safe_all_models

        models, _age = safe_all_models(allow_network=False)
    except Exception:
        return False
    wanted = model_name.lower()
    for model in models:
        if str(getattr(model, "model_id", "") or "").strip().lower() == wanted:
            return bool(model_is_free(model))
    return False


def charges_the_user(manifest: ModelProviderManifest, *, cost_class: str | None = None) -> bool:
    """Whether a call to this manifest spends the user's money.

    A paid-class lane whose model the catalog does not price at zero. This is the ONE reading of the
    paid fence, consumed by the ranker below and by the turn-routing plan (``core.turn_routing``).
    Measured 2026-09-08: the two fences carried separate copies of the rule, the ranker admitted the
    operator's pinned ``:free`` model and the plan refused it as paid, so the pin fell unresolved and
    an auto-pick answered in the operator's place.
    """
    resolved = str(cost_class if cost_class is not None else provider_cost_class(manifest))
    return resolved == "paid_cloud" and not is_verified_free_cloud_manifest(manifest)


def reported_cost_class(manifest: ModelProviderManifest) -> str:
    """Return the billing truth shown in usage/events without weakening routing gates.

    OpenRouter manifests intentionally remain ``paid_cloud`` for provider selection so an
    unverified remote model can never bypass spend authorization.  Once the catalog has verified
    an exact zero-price model, however, calling that response ``paid_cloud`` in the operator ledger
    is false.  Keep policy classification and reported billing classification separate.
    """

    if is_verified_free_cloud_manifest(manifest):
        return "free_cloud"
    return provider_cost_class(manifest)


def _hard_exclusion_reason(
    manifest: ModelProviderManifest,
    request: ModelSelectionRequest,
    *,
    required: object,
) -> str:
    """Return "" if the manifest passes every HARD (non-negotiable) constraint, else the reason it
    is excluded. Hard = safety / cost / compliance: enabled, not excluded, license, bundled-weight
    policy, local-only mode, paid gating, required capability, trust. The model/provider TAG is NOT
    here — it is a SOFT preference the ranker degrades gracefully, so a tag mismatch never bricks the
    brain while a policy/compliance violation still does."""
    if not manifest.enabled:
        return "disabled"
    if manifest.metadata.get("chat_selection_only") and not (
        request.preferred_model == manifest.model_name
        and request.preferred_provider == manifest.provider_name
    ):
        return "chat_selection_not_requested"
    if manifest.provider_id in set(request.exclude_provider_ids):
        return "excluded_by_request"
    if request.forbid_bundled_weights and manifest.weights_are_bundled:
        return "bundled_weights_forbidden"
    if request.require_license_metadata and (
        not str(manifest.license_name or "").strip() or not str(manifest.resolved_license_reference or "").strip()
    ):
        return "missing_license_metadata"
    cost_class = provider_cost_class(manifest)
    if policy_engine.local_only_mode() and cost_class != "free_local":
        return "not_free_local_in_local_only_mode"
    # The per-turn Local Only lane, checked separately from the machine-wide setting so a ranking
    # trace names which rule bound. Note this sits ABOVE the verified-free bypass below: "free" is a
    # price, not a location, and a zero-cost OpenRouter call still leaves the machine.
    if request.local_only and cost_class != "free_local":
        from core.auto_local_only_mode import SELECTION_EXCLUSION_REASON

        return SELECTION_EXCLUSION_REASON
    if not request.allow_paid_fallback and charges_the_user(manifest, cost_class=cost_class):
        return "paid_not_permitted"
    if required and capability_score(manifest, task_kind=request.task_kind, output_mode=request.output_mode) <= 0.0:
        return "no_required_capability"
    if provider_base_trust(manifest) < request.min_trust:
        return "below_min_trust"
    return ""


def _learned_sufficiency_adjustment(manifest: ModelProviderManifest, request: ModelSelectionRequest) -> float:
    """Bounded learned-quality adjustment; deliberately NOT health (see core.learning.model_sufficiency)."""
    from core.learning.model_sufficiency import sufficiency_adjustment

    return sufficiency_adjustment(
        task_kind=request.task_kind,
        provider_id=manifest.provider_id,
        model_id=str(manifest.model_name or ""),
    )


def _rank_once(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
    *,
    relax_model_preference: bool,
) -> list[ModelProviderManifest]:
    ranked: list[tuple[float, ModelProviderManifest]] = []
    required = required_capabilities(request.task_kind, request.output_mode)
    for manifest in manifests:
        if _hard_exclusion_reason(manifest, request, required=required):
            continue
        provider_match = not request.preferred_provider or manifest.provider_name == request.preferred_provider
        model_match = not request.preferred_model or manifest.model_name == request.preferred_model
        # Strict pass: the requested provider/model gate. Relaxed pass: they only reward, so a
        # present-but-differently-tagged local model still serves.
        if not relax_model_preference and (not provider_match or not model_match):
            continue

        cost_class = provider_cost_class(manifest)
        score = capability_score(manifest, task_kind=request.task_kind, output_mode=request.output_mode)
        score += 0.55 * provider_base_trust(manifest)
        if request.preferred_source_types and manifest.source_type in set(request.preferred_source_types):
            score += 0.22
        if cost_class == "free_local":
            score += 0.24
        elif cost_class == "remote_unknown":
            score -= 0.05
        elif cost_class == "paid_cloud":
            # Finding F, 2026-08-04: `is_verified_free_cloud_manifest` already knows this specific
            # manifest is priced at zero by the OpenRouter catalog, but it was only ever consulted
            # for the paid-not-permitted exclusion bypass and the billing label -- never as a
            # POSITIVE ranking input. So ordinary tool_intent/chat routing (folder listing, project
            # overview) kept defaulting to a weaker local model (qwen3:8b) even when a genuinely
            # free, more reliable cloud model was available and eligible. Scored slightly above the
            # free_local bonus: a verified-free cloud model costs the operator nothing either, and
            # is measurably more reliable at tool-argument generation (Finding B). This does not
            # touch `local_only_mode`, which excludes every non-free_local manifest before scoring
            # ever runs (`_hard_exclusion_reason` above) and stays the hard, unconditional floor.
            score += 0.30 if is_verified_free_cloud_manifest(manifest) else -0.12
        score += _lane_fit_score(manifest, request)
        # Learned task-class sufficiency (PB01): a bounded, additive-only adjustment from the
        # learning store's fresh per-(task_kind, provider) observations. Applied AFTER every hard
        # exclusion above, so it can reorder eligible manifests and nothing else -- it must never
        # rescue a paid/local-only/license/capability gate, and it is 0.0 (status-quo selection)
        # when there is not enough fresh evidence or the store is unreadable.
        score += _learned_sufficiency_adjustment(manifest, request)
        if relax_model_preference:
            # Keep an exact tag match on top when present, without excluding the rest.
            if request.preferred_model and model_match:
                score += 0.60
            if request.preferred_provider and provider_match:
                score += 0.40
        if circuit_is_open(manifest.provider_id):
            score -= 10.0

        ranked.append((score, manifest))

    ranked.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    return [item[1] for item in ranked]


def rank_providers(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> list[ModelProviderManifest]:
    """Rank eligible providers best-first, local-first with a self-heal.

    First rank with the requested provider/model as a HARD filter, so an available specialist wins
    exactly as before. If that yields nothing — e.g. the request asked for ``qwen3:8b`` but only
    ``qwen2.5:7b`` is installed — re-rank with provider/model as a SOFT preference so any capable,
    enabled LOCAL provider still serves instead of the agent going brain-dead
    (``source="no_provider_available"``). The relaxed pass still enforces every hard constraint
    (enabled, license, capability, trust, local-only, paid gating), so it never fails open to a paid
    cloud lane — it only degrades the model-tag preference.
    """
    strict = _rank_once(manifests, request, relax_model_preference=False)
    ranked = strict or _rank_once(manifests, request, relax_model_preference=True)
    return _drop_models_too_small_to_select_a_tool(ranked, request)


def _drop_models_too_small_to_select_a_tool(
    ranked: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> list[ModelProviderManifest]:
    """Remove sub-floor models from the tool-selection lane, but never empty the list.

    A score penalty is not enough on this path. ``provider_routing`` re-scores by ORDINAL POSITION
    (``total - index``) and then adds a role bonus, so the magnitude of the lane-fit penalty is
    discarded and a drone-tagged local tiny model climbs straight back to the top -- measured: the
    lane-fit change alone left qwen3:0.6b serving tool_intent on the live daemon. Capability has to
    remove the candidate, not argue about it.

    If nothing clears the floor the original ranking is returned untouched: on a machine whose only
    model is small, a small model attempting the call beats no brain at all.
    """
    if not ranked:
        return ranked
    task_kind = str(request.task_kind or "").strip().lower()
    output_mode = str(request.output_mode or "").strip().lower()
    if output_mode != "tool_intent" and task_kind != "tool_intent":
        return ranked
    capable = [
        manifest
        for manifest in ranked
        if model_parameter_billions(manifest.model_name) >= _TOOL_SELECTION_MIN_PARAMETER_B
    ]
    return capable or ranked


def explain_provider_exclusions(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> list[dict[str, str]]:
    """Per-manifest eligibility, for diagnosing an empty ranking (a brain-offline event).

    ``status == "eligible"`` means the manifest clears every hard constraint (it would serve under
    the self-heal even if its tag doesn't match the request). Any other value is the first hard
    constraint that excluded it — so an empty inventory reads "seeding never ran", all-``disabled``
    reads "manifests were turned off", and ``missing_license_metadata`` etc. name themselves.
    """
    required = required_capabilities(request.task_kind, request.output_mode)
    rows: list[dict[str, str]] = []
    for manifest in manifests:
        rows.append(
            {
                "provider_id": str(manifest.provider_id),
                "model": str(manifest.model_name),
                "enabled": "1" if manifest.enabled else "0",
                "status": _hard_exclusion_reason(manifest, request, required=required) or "eligible",
            }
        )
    return rows


def select_provider(
    manifests: list[ModelProviderManifest],
    request: ModelSelectionRequest,
) -> ModelProviderManifest | None:
    ranked = rank_providers(manifests, request)
    return ranked[0] if ranked else None


# A model must be at least this big to be trusted to SELECT a tool. Set from the measurement in
# _lane_fit_score: 0.6B fabricates instead of dispatching, 8B dispatches correctly. Nothing between
# the two is installed on the reference machine, so this sits just under the smallest known-good
# size rather than pretending to a precision the evidence does not support.
_TOOL_SELECTION_MIN_PARAMETER_B = 4.0


def _lane_fit_score(manifest: ModelProviderManifest, request: ModelSelectionRequest) -> float:
    metadata = dict(manifest.metadata or {})
    bundle_role = str(metadata.get("bundle_role") or "").strip().lower()
    orchestration_role = str(metadata.get("orchestration_role") or "").strip().lower()
    task_kind = str(request.task_kind or "").strip().lower()
    output_mode = str(request.output_mode or "").strip().lower()
    parameter_b = model_parameter_billions(manifest.model_name)

    if task_kind in {"classification", "tool_intent", "format", "extract", "tag"} or output_mode == "tool_intent":
        # Picking one of ~57 tools from a ~17.5k-char catalogue is not the same job as tagging a
        # string, and the sub-1B lane cannot do it. Measured by replaying one captured tool_intent
        # prompt ("run the command: ls -la") at two sizes: qwen3:8b returned
        # {"intent":"sandbox.run_command","arguments":{"command":"ls -la"}}, while qwen3:0.6b returned
        # respond.direct carrying invented prose about a staging database -- so the user was told
        # "I'm ready to help" and nothing ran. Cheap classification/format/extract/tag keep the tiny
        # lane; real tool selection gets a floor. Unknown and cloud models report 8.0B and are unaffected.
        if output_mode == "tool_intent" or task_kind == "tool_intent":
            if parameter_b and parameter_b < _TOOL_SELECTION_MIN_PARAMETER_B:
                return -1.1
            if bundle_role in {"reasoning", "heavy_reasoning"} or orchestration_role == "queen":
                return -0.45
            if parameter_b >= 13.0:
                return -0.25
            return 0.5
        if bundle_role == "lightweight_utility":
            return 0.75
        if bundle_role in {"reasoning", "heavy_reasoning"} or orchestration_role == "queen":
            return -0.45
        if parameter_b >= 13.0:
            return -0.25
        return 0.12

    if task_kind in {"reasoning", "agent_planning"}:
        if bundle_role in {"reasoning", "heavy_reasoning"}:
            return 0.6
        if bundle_role == "coding":
            return 0.3
        if bundle_role == "lightweight_utility":
            return -1.2
        return 0.0

    if task_kind == "normalization_assist" and output_mode == "plain_text":
        if bundle_role == "general":
            return 0.35
        if bundle_role == "lightweight_utility":
            return -0.4
        if bundle_role == "heavy_reasoning":
            return -0.55
        if bundle_role == "reasoning":
            return -0.2
        return 0.0

    if task_kind in {"action_plan", "coding_help_complex"} or output_mode == "action_plan":
        if bundle_role == "coding":
            return 0.5
        if bundle_role == "reasoning":
            return 0.45
        if bundle_role == "heavy_reasoning":
            return 0.25
        if bundle_role == "lightweight_utility":
            return -1.25
        if parameter_b >= 13.0:
            return 0.15
        return 0.0

    if task_kind in {"summarization", "candidate_shard_generation"}:
        if bundle_role in {"reasoning", "coding"}:
            return 0.25
        if bundle_role == "heavy_reasoning":
            return 0.1
        if bundle_role == "lightweight_utility":
            return -0.65
    return 0.0
