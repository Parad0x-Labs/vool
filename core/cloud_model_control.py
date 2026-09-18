"""Shared cloud-model controls: one switch path for chat commands, NL intents, and the UI.

``set_cloud_model`` is the single way the OpenRouter burst model changes — the ``cloud model``
chat command, the natural-language switch intent, and the dropdown's POST /api/cloud/model all
delegate here, so every surface persists the same policy, re-registers the lane the same way,
and reports the same wording. ``force_catalog_refresh`` is the single live-refresh path.
"""
from __future__ import annotations

import re
from typing import Any

CLOUD_CREDENTIAL_NAME = "llm.cloud.openrouter"
# A model id is a bare id ("gpt-4.1-mini"), a vendor/name slug ("deepseek/deepseek-chat-v3"), or
# either with a ":tag". A leading "<provider>:" prefix is parsed off separately (not by this regex)
# so a direct provider's bare id is accepted where the old vendor/name-only pattern rejected it.
CLOUD_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?(?::[A-Za-z0-9._-]+)?$")

# --- A11 cost-classification truth -----------------------------------------------------------
# Stable machine-readable reasons carried by every surface that refuses an unconfirmed pin.
# `MODEL_COST_UNKNOWN`: the catalog could not classify the id at all (no cached row, cold/empty
#   cache, unreadable cache). `PAID_STATUS_UNKNOWN`: the catalog HAS the row but its published
#   pricing is indeterminate, so free-vs-paid cannot be mechanically read. Both fail CLOSED: an
#   unknown-cost pin is never persisted without the request-scoped spend confirmation the server
#   itself requires — and neither state ever invents a price.
MODEL_COST_UNKNOWN = "MODEL_COST_UNKNOWN"
PAID_STATUS_UNKNOWN = "PAID_STATUS_UNKNOWN"

COST_STATE_FREE = "free"
COST_STATE_PAID = "paid"
COST_STATE_UNKNOWN = "unknown"


def canonical_model_identity(model_id: str) -> str:
    """The canonical lookup identity used for EVERY cost decision.

    Case-insensitive and whitespace-trimmed, so `ZZZ/Alpha-Big`, ` zzz/alpha-big ` and the
    catalog's own `zzz/alpha-big` are one model to the classifier — a case or spacing variant can
    never walk past a paid classification by not matching the row. The SUFFIX is deliberately part
    of identity (`vendor/name:free` vs `vendor/name` differ): on OpenRouter the ``:free`` tag names
    a distinct zero-cost route, so conflating them would misclassify genuinely different lanes.
    """
    return re.sub(r"\s+", "", str(model_id or "").strip().lower())


def split_provider_prefix(arg: str) -> tuple[str | None, str]:
    """Parse an optional '<provider>:model' prefix. Returns (provider_id|None, remaining_model).
    Only splits when the prefix is a KNOWN provider id, so a ':tag' (e.g. '.../model:free') and a
    slash slug are never mistaken for a provider prefix."""
    from core.cloud_providers import PROVIDERS

    head, sep, rest = str(arg or "").partition(":")
    if sep and rest and head.strip().lower() in PROVIDERS:
        return head.strip().lower(), rest
    return None, arg


#: ``catalog_state`` of a classification: the row was read; the catalog answered but lists no
#: such id; or the catalog could not be read at all (no cache and the refresh failed).
CATALOG_STATE_ROW = "row"
CATALOG_STATE_UNLISTED = "unlisted"
CATALOG_STATE_UNAVAILABLE = "unavailable"


def _openrouter_catalog_row(probe: str, *, allow_network: bool) -> tuple[Any, str]:
    """``(row, catalog_state)`` for a canonical OpenRouter id.

    The cache is consulted first. When it holds no row and ``allow_network`` is set, ONE bounded
    refresh (``refresh_openrouter_catalog``'s 15 s timeout, through the sealed remote-fetch door)
    is the explicit verification a cold cache calls for -- the alternative, deciding a lane from a
    catalog that was never read, is what silently turned the free lane off on 2026-09-06.
    """
    from core.openrouter_catalog import safe_all_models

    def _lookup(models: tuple[Any, ...]) -> Any:
        return next((item for item in models if canonical_model_identity(item.model_id) == probe), None)

    try:
        models, _age = safe_all_models(allow_network=False)
    except Exception:
        models = ()
    row = _lookup(models)
    if row is not None:
        return row, CATALOG_STATE_ROW
    if allow_network:
        try:
            models, _age = safe_all_models(allow_network=True)
        except Exception:
            models = ()
        row = _lookup(models)
        if row is not None:
            return row, CATALOG_STATE_ROW
    return None, (CATALOG_STATE_UNLISTED if models else CATALOG_STATE_UNAVAILABLE)


def _row_is_free(row: Any) -> bool:
    try:
        from core.openrouter_catalog import model_is_free

        return bool(model_is_free(row))
    except Exception:
        return False


def _free_lane_unverified_note(catalog_state: str, *, lane_enabled: bool, model_id: str) -> str:
    """What the pin reply says when a ``:free`` pin could not be row-verified.

    The lane is left exactly as it was, and the sentence names which of the two things happened
    and what turns the lane on -- never a silent flag flip in either direction.
    """
    lane_state = "on" if lane_enabled else "off"
    if catalog_state == CATALOG_STATE_UNAVAILABLE:
        return (
            " The OpenRouter catalog could not be reached to verify this `:free` variant, so the "
            f"free cloud lane was left {lane_state} as it was. Pin it again once the catalog is "
            "reachable (or refresh the catalog from Settings) to verify and enable the lane."
        )
    return (
        f" The OpenRouter catalog lists no `{model_id}` row, so the `:free` suffix alone does not "
        f"verify it for the free cloud lane, which was left {lane_state} as it was. Check the id "
        "against the catalog; a listed `:free` variant enables the lane when pinned."
    )


def classify_cloud_model_cost(
    *, provider_id: str, model_id: str, allow_network: bool = False
) -> dict[str, Any]:
    """The ONE mechanical cost classification for a concrete cloud-model pin.

    Server-owned, deterministic, and never price-inventing:

    * ``openrouter`` — a id ending in ``:free`` is free by OpenRouter's own naming contract; any
      other id is classified from its CACHED catalog row via :func:`core.openrouter_catalog.model_is_free`.
      A known row with indeterminate pricing reports ``PAID_STATUS_UNKNOWN``; no readable row
      (cold/empty cache or unlisted id) reports ``MODEL_COST_UNKNOWN``. Matching is done on the
      canonical identity, so case/spacing variants cannot dodge the row they really are.
    * direct providers — every listed model of a BYOK vendor (openai, anthropic, groq, google,
      deepseek, moonshot, custom) is metered by that vendor: the same authority law as OpenRouter,
      applied where applicable instead of treating OpenRouter as the only paid universe.

    Returns ``{"cost_state": "free"|"paid"|"unknown", "reason_code": ""|MODEL_COST_UNKNOWN|PAID_STATUS_UNKNOWN,
    "row_verified_free": bool}``. ``row_verified_free`` is True only when a cached catalog row was
    read and its all-zero published pricing made the FREE verdict — never from the ``:free``
    suffix alone, which stays untrusted as free-lane-enabling evidence by itself.
    """
    probe = canonical_model_identity(model_id)
    if not probe:
        return {
            "cost_state": COST_STATE_UNKNOWN,
            "reason_code": MODEL_COST_UNKNOWN,
            "row_verified_free": False,
        }
    if str(provider_id or "").strip().lower() == "openrouter":
        row, catalog_state = _openrouter_catalog_row(probe, allow_network=allow_network)
        if probe.endswith(":free"):
            # The suffix is the provider's own guaranteed-free tag; trusted as zero-cost for the
            # PIN even when no row confirms it (mirrors model_is_free). Free-LANE evidence is the
            # row: a ``:free`` variant the catalog actually lists is verified; a pasted suffix the
            # catalog has never heard of is not -- and "never heard of" is told apart from "could
            # not ask" in ``catalog_state`` so the pin door can say which one happened.
            verified = row is not None and _row_is_free(row)
            return {
                "cost_state": COST_STATE_FREE,
                "reason_code": "",
                "row_verified_free": bool(verified),
                "catalog_state": CATALOG_STATE_ROW if verified else catalog_state,
            }
        if row is None:
            return {
                "cost_state": COST_STATE_UNKNOWN,
                "reason_code": MODEL_COST_UNKNOWN,
                "row_verified_free": False,
                "catalog_state": catalog_state,
            }
        try:
            from core.openrouter_catalog import model_is_free, model_pricing_is_known

            if model_is_free(row):
                return {
                    "cost_state": COST_STATE_FREE,
                    "reason_code": "",
                    "row_verified_free": True,
                    "catalog_state": CATALOG_STATE_ROW,
                }
            if not model_pricing_is_known(row):
                # The row exists but its published pricing is indeterminate: refuse-with-reason,
                # never read that as free and never invent a number.
                return {
                    "cost_state": COST_STATE_UNKNOWN,
                    "reason_code": PAID_STATUS_UNKNOWN,
                    "row_verified_free": False,
                    "catalog_state": CATALOG_STATE_ROW,
                }
            return {
                "cost_state": COST_STATE_PAID,
                "reason_code": "",
                "row_verified_free": False,
                "catalog_state": CATALOG_STATE_ROW,
            }
        except Exception:
            return {
                "cost_state": COST_STATE_UNKNOWN,
                "reason_code": MODEL_COST_UNKNOWN,
                "row_verified_free": False,
                "catalog_state": catalog_state,
            }
    # Direct providers have no free tier in their own catalog vocabulary: a concrete pin there is
    # a metered-spend choice and asks for exactly the same confirmation.
    return {"cost_state": COST_STATE_PAID, "reason_code": "", "row_verified_free": False}


def _resolve_switch_provider(provider_arg: str | None, prefix_provider: str | None) -> tuple[str, str]:
    """(provider_id, message_or_empty) resolved EXACTLY like the switch itself resolves it:
    explicit argument -> already-parsed ``provider:`` prefix -> active/default provider."""
    from core import cloud_escalation_policy as cep
    from core.cloud_providers import PROVIDERS, active_provider

    prov = str(provider_arg or "").strip().lower() or str(prefix_provider or "").strip().lower()
    if prov and prov not in PROVIDERS:
        return "", f"`{prov}` is not a known cloud provider."
    if not prov:
        try:
            prov = active_provider(str(cep.load_policy().provider or "")) or "openrouter"
        except Exception:
            prov = "openrouter"
    return prov, ""


def _cloud_key_usable(provider: str | None = None) -> bool:
    """Whether a NON-EMPTY cloud key actually resolves for the provider (default: active) — the
    same value-resolve the connection pill uses, so the switch note never claims a live lane the
    pill reports as no_key."""
    try:
        from core.cloud_connection_state import _resolve_key

        return bool(_resolve_key(provider))
    except Exception:
        return False


def _provider_default_model(provider_id: str) -> str:
    """The model a provider uses when the user has not picked one."""
    if provider_id == "openrouter":
        from core.runtime_provider_defaults import default_openrouter_model_name

        return default_openrouter_model_name()
    from core.cloud_providers import config_for

    cfg = config_for(provider_id)
    return cfg.default_model if cfg else ""


def set_cloud_model(
    model_or_keyword: str,
    *,
    provider: str | None = None,
    owner_local: bool = False,
    confirm_paid: bool = False,
    council_capability: str = "",
) -> tuple[bool, str, str]:
    """Persist + activate a cloud model choice for a provider. Returns (ok, message, chosen_model).

    Accepts a bare id (``gpt-4.1-mini``), a ``vendor/name[:tag]`` slug, an explicit
    ``provider:model`` prefix, ``auto`` (OpenRouter free-catalog), or ``default``/``reset``. The
    provider is: the explicit arg → a ``provider:`` prefix → else the active provider (OpenRouter
    legacy). Owner-local only. Save is verified by re-read; the lane re-registers immediately when
    a key exists.

    A11 SERVER-OWNED PAID AUTHORITY: this function — the single mutation authority every surface
    (HTTP endpoint, chat command, NL intent, direct/internal callers) converges on — derives the
    model identity and cost classification itself and REFUSES any pin whose cost it classifies as
    paid/unknown-cost unless ``confirm_paid`` carries the request-scoped spend confirmation for
    THIS call. The refusal message embeds the stable machine-readable reason
    (`PAID_MODEL_CONFIRM_REQUIRED` / `MODEL_COST_UNKNOWN` / `PAID_STATUS_UNKNOWN`) at its start;
    there is no sticky confirmation state to corrupt — each switch re-derives everything.

    (The former ``_verified_free`` caller-asserted bypass is gone: no surface certifies pricing;
    only the mechanical classifier does.)
    """
    arg = str(model_or_keyword or "").strip()
    if not arg:
        return False, "No model given — try `cloud model <id>`, `cloud model auto`, or `cloud model default`.", ""
    if not owner_local:
        return False, "The cloud model can only be changed from your own local session, so I left it unchanged.", ""
    # COUNCIL MODEL-PIN FENCE at the mutation authority itself. Every surface converges
    # here — this endpoint, the `cloud model` chat command, the natural-language switch
    # intent, direct internal callers — so a writer added later is fenced by construction
    # rather than by remembering to ask the endpoint's guard. A council's own seat pin and
    # final restoration carry the run's capability (loopback-validated); nothing else may
    # move the pin out from under a run.
    from core.council import pin_lock as _council_pin_lock

    _council_refusal = _council_pin_lock.refuse_pin_write_reason(
        council_capability, owner_local=owner_local
    )
    if _council_refusal:
        return False, _council_refusal, ""

    from dataclasses import replace

    from core import cloud_escalation_policy as cep
    from core.cloud_providers import PROVIDERS

    # Resolve the provider: explicit arg -> "provider:model" prefix -> active provider.
    prefix_provider, arg = split_provider_prefix(arg)
    prov, resolution_error = _resolve_switch_provider(provider, prefix_provider)
    if resolution_error:
        return False, resolution_error, ""

    def _save(model_value: str) -> bool:
        cep.save_policy(replace(cep.load_policy(), model=model_value, provider=prov))
        p = cep.load_policy()
        return p.model == model_value and p.provider == (prov if prov in PROVIDERS else "")

    if arg.lower() == "auto":
        # `auto` means "follow the live FREE catalog". Only OpenRouter publishes one, so for a
        # direct provider there is no free candidate to switch to. REFUSE — standing in that
        # provider's (paid) default would answer "pick a free model" by spending the user's key,
        # and falling through would persist the literal string "auto" as a model id.
        if prov != "openrouter":
            label = PROVIDERS[prov].label if prov in PROVIDERS else prov
            return False, (
                f"`auto` only ever picks a free model from the live OpenRouter catalog, and {label} "
                f"does not publish one — I will not fall back to a paid model on your key, so I left "
                f"the cloud model unchanged. Name a model with `cloud model <id>`, or switch to "
                f"OpenRouter for free-first routing."
            ), ""
        else:
            # Resolve the free pick BEFORE persisting. `auto` is a request for a FREE model, so with
            # no verified-free candidate there is nothing to switch to and the command refuses with
            # the policy untouched. Standing in the paid default instead would answer "pick a free
            # model" by silently spending the user's credits on one they never named.
            picks: dict[str, str] = {}
            try:
                from core.openrouter_catalog import pick_auto_free_models

                picks = pick_auto_free_models(allow_network=True)
            except Exception:
                picks = {}
            if not picks:
                return False, (
                    "I could not read a verified-free model from the live OpenRouter catalog just now, "
                    "so I left the cloud model unchanged. `auto` only ever picks a free model — I will "
                    "not fall back to a paid one on your key. Check the connection and try again, or "
                    "name a model with `cloud model <id>`."
                ), ""
            previous_policy = cep.load_policy()
            if not _save("auto"):
                return False, (
                    "I could not save the model choice (auto) just now, so it is unchanged. Check "
                    "that VOOL can write its data directory, then try again."
                ), ""
            # This is the production caller for System B's free lane. Persisting the model choice
            # alone used to leave free_cloud_enabled=False, so the broker never fired from chat.
            cep.set_free_cloud_enabled(True)
            if not cep.load_policy().free_cloud_enabled:
                cep.save_policy(previous_policy)
                return False, "I could not enable the verified-free cloud lane just now, so I left the choice unchanged.", ""
            lane_note = ""
            try:
                from core.runtime_provider_defaults import activate_provider_byok, retire_nonactive_provider_lanes

                if _cloud_key_usable("openrouter") and activate_provider_byok("openrouter"):
                    lane_note = " Lanes are live now."
                retire_nonactive_provider_lanes("openrouter")
            except Exception:
                pass
            picked = ", ".join(f"{lane}: `{mid}`" for lane, mid in sorted(picks.items()))
            return True, (
                f"Cloud model set to auto — I route each burst to the current best FREE model and "
                f"re-pick as the live catalog changes (refreshed hourly on use). Today that is "
                f"{picked}.{lane_note}"
            ), "auto"

    if arg.lower() in ("default", "reset"):
        if not _save(""):
            return False, "I could not clear the model override just now, so it is unchanged.", ""
        chosen = _provider_default_model(prov)
        cep.set_free_cloud_enabled(False)
    else:
        if not CLOUD_MODEL_ID_RE.match(arg):
            return False, (
                f"`{arg}` does not look like a model id (a bare id like `gpt-4.1-mini` or a "
                "`vendor/name[:tag]` slug), so I left the model unchanged."
            ), ""

        # A11 central authority: classify cost BEFORE anything is persisted, on canonical
        # identity, for OpenRouter AND direct providers alike. Unknown-cost states fail closed;
        # nothing here invents a price and no caller-side assertion can stand in for the
        # classifier (`_verified_free` is gone).
        classification = classify_cloud_model_cost(provider_id=prov, model_id=arg, allow_network=True)
        if classification["cost_state"] != COST_STATE_FREE and not confirm_paid:
            if classification["reason_code"] == MODEL_COST_UNKNOWN:
                refusal = (
                    "The catalog could not classify what this model costs, so I will not pin it "
                    f"without your explicit spend confirmation: `{arg}` may be paid per token. "
                    "Confirm the pin to spend provider credits — the confirmation applies to this "
                    "switch only, never permanently."
                )
            elif classification["reason_code"] == PAID_STATUS_UNKNOWN:
                refusal = (
                    "This model's published pricing is indeterminate in the catalog, so I will not "
                    f"pin `{arg}` without your explicit spend confirmation: it may be paid per "
                    "token. Confirm the pin to spend provider credits — the confirmation applies "
                    "to this switch only, never permanently."
                )
            else:
                refusal = (
                    f"That model is PAID per the catalog. Confirm the pin to spend provider "
                    f"credits on `{arg}` — the confirmation applies to this switch only, never "
                    "permanently. Spend caps still apply."
                )
            code = classification["reason_code"] or "PAID_MODEL_CONFIRM_REQUIRED"
            return False, f"{code}: {refusal}", ""
        previous_policy = cep.load_policy()
        if not _save(arg):
            return False, (
                f"I could not save the model choice ({arg}) just now, so it is unchanged. Check "
                "that VOOL can write its data directory, then try again."
            ), ""
        chosen = arg
        # A free lane is enabled only by a ROW-VERIFIED free classification of THIS exact id from
        # the same server-owned classifier — never by a pasted ``:free`` suffix alone, which reads
        # as zero-cost for the pin gate but is not free-lane-enabling pricing evidence. A
        # confirmed paid/unknown pin never touches the auto-follow lane beyond turning it off.
        verify_note = ""
        if prov == "openrouter" and classification.get("row_verified_free") is True:
            cep.set_free_cloud_enabled(True)
            if not cep.load_policy().free_cloud_enabled:
                cep.save_policy(previous_policy)
                return False, "I could not enable the verified-free cloud lane just now, so I left the choice unchanged.", ""
        elif (
            prov == "openrouter"
            and classification["cost_state"] == COST_STATE_FREE
            and not confirm_paid
        ):
            # The pin landed on the provider's own ``:free`` naming contract, but the explicit
            # catalog verification could not confirm THIS variant (the catalog was unreachable, or
            # lists no such row). The free lane is left EXACTLY as it was: a lane that cannot be
            # verified is never switched off in silence, and a suffix alone never switches it on.
            # The reply says which happened and what turns the lane on.
            verify_note = _free_lane_unverified_note(
                str(classification.get("catalog_state") or ""),
                lane_enabled=bool(previous_policy.free_cloud_enabled),
                model_id=arg,
            )
        elif prov == "openrouter":
            # A confirmed paid/unknown-cost pin turns the auto-follow free lane off on purpose.
            cep.set_free_cloud_enabled(False)

    # Re-register the ACTIVE provider's lane on the new model right away when a key exists.
    lane_note = ""
    verify_note = locals().get("verify_note", "")
    label = PROVIDERS[prov].label if prov in PROVIDERS else prov
    try:
        from core.runtime_provider_defaults import activate_provider_byok, retire_nonactive_provider_lanes

        if _cloud_key_usable(prov) and activate_provider_byok(prov):
            lane_note = f" The {label} lane now points at it."
        else:
            lane_note = f" It will take effect once you add a working {label} key in Settings."
        # One active provider (v1): retire any other provider's still-enabled BYOK lane.
        retire_nonactive_provider_lanes(prov)
    except Exception:
        pass
    free_note = "" if chosen.endswith(":free") else " Heads up: this id is not a `:free` one, so bursts spend your credits."
    return True, f"Cloud model set to `{chosen}`.{lane_note}{free_note}{verify_note}", chosen


def set_auto_free_model(model_or_auto: str, *, owner_local: bool = False) -> tuple[bool, str, str]:
    """Set the one verified-free OpenRouter model VOOL Auto may escalate to.

    ``auto`` delegates selection to the live free catalog. A concrete id must exist in the cached
    verified-free catalog and carry the provider's ``:free`` tag. The preference never changes the
    explicit composer pin and can never authorize a paid route.
    """
    requested = str(model_or_auto or "").strip()
    if not owner_local:
        return False, "The Auto fallback can only be changed from your own local session.", ""
    if not requested:
        return False, "Choose `auto` or a verified-free OpenRouter model.", ""

    from dataclasses import replace

    from core import cloud_escalation_policy as cep

    if requested.lower() == "auto":
        chosen = "auto"
    else:
        if not CLOUD_MODEL_ID_RE.match(requested) or not requested.lower().endswith(":free"):
            return False, "VOOL Auto accepts only a verified `:free` OpenRouter model.", ""
        try:
            from core.openrouter_catalog import safe_free_models

            free_models, _age = safe_free_models(allow_network=False)
            verified_ids = {str(item.model_id) for item in free_models}
        except Exception:
            verified_ids = set()
        if requested not in verified_ids:
            return False, "That model is not in the cached verified-free OpenRouter catalog.", ""
        chosen = requested

    stored = cep.save_policy(
        replace(
            cep.load_policy(),
            auto_free_model=chosen,
            free_cloud_enabled=True,
        )
    )
    if stored.auto_free_model != chosen or not stored.free_cloud_enabled:
        return False, "I could not save the Auto fallback, so it is unchanged.", ""
    label = "the strongest eligible verified-free model" if chosen == "auto" else f"`{chosen}` only"
    return True, f"VOOL Auto will stay local first, then use {label} when cloud help is needed.", chosen


def force_catalog_refresh() -> dict[str, Any]:
    """Force a live OpenRouter catalog fetch NOW. Returns {ok, total, free, fetched_at|error}.

    On failure the previous cache is untouched, so a failed refresh degrades to old-but-real
    data rather than an empty list.
    """
    try:
        from core.openrouter_catalog import model_is_free, refresh_openrouter_catalog

        models = refresh_openrouter_catalog()
        free = sum(1 for m in models if model_is_free(m))
        fetched_at = models[0].fetched_at if models else ""
        # Market feed: diff this refresh against the last snapshot into typed events + price
        # history (watched models only). Best-effort by that module's own contract — a feed
        # failure may never break the refresh whose data is already in hand.
        try:
            from core.model_market_feed import record_catalog_refresh

            record_catalog_refresh(models)
        except Exception:
            pass
        # Model Radar: observe the same REAL fetch through the normalized authority, so
        # qualified free/price-drop news rides every genuine refresh. Same fail-quiet law:
        # the radar may never break the refresh whose data is already in hand.
        try:
            from core.model_radar_service import observe_openrouter_catalog

            observe_openrouter_catalog(models)
        except Exception:
            pass
        return {"ok": True, "total": len(models), "free": free, "fetched_at": fetched_at}
    except Exception as exc:
        return {"ok": False, "total": 0, "free": 0, "error": type(exc).__name__}


__all__ = [
    "CLOUD_CREDENTIAL_NAME",
    "CLOUD_MODEL_ID_RE",
    "COST_STATE_FREE",
    "COST_STATE_PAID",
    "COST_STATE_UNKNOWN",
    "MODEL_COST_UNKNOWN",
    "PAID_STATUS_UNKNOWN",
    "canonical_model_identity",
    "classify_cloud_model_cost",
    "force_catalog_refresh",
    "set_auto_free_model",
    "set_cloud_model",
    "split_provider_prefix",
]
