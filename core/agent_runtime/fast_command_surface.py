from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from core.hive_activity_tracker import session_hive_state
from core.human_input_adapter import runtime_session_id
from core.runtime_lane_truth import FACET_KEY, maybe_runtime_lane_answer, runtime_lane_question
from network import signer as signer_mod


def _record_routing_decision(*, session_id: str, user_input: str, family: str) -> None:
    """Fail-soft routing telemetry: which fast-path family answered this turn (owner-local log)."""
    try:
        from core.routing_decision_log import record_decision

        record_decision(session_id=session_id, user_input=user_input, family=family, handled=True)
    except Exception:
        pass

logger = logging.getLogger("vool.fast_command_surface")


def _fast_path_route_metadata(
    reason: str,
    *,
    route_prefix: str = "deterministic",
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from core.context_retrieval import get_last_retrieval_telemetry

        capsule = dict(get_last_retrieval_telemetry() or {})
    except Exception:
        capsule = {}
    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_scope_active
    from core.turn_model_call_ledger import turn_model_calls

    # NOT the literal 0 this used to write. "A deterministic lane produced the answer" and "no
    # provider was called on this turn" are different facts: a routing gate above this lane can
    # spend a call and then hand the turn to a deterministic reader, and a live turn did exactly
    # that -- two provider calls, then a 0.0008s file read, reported as `model_calls: 0`. The
    # answer still owes nothing to a model, which is what `fast_path_hit` and `route_skips` say;
    # this field says what the turn COST.
    model_calls = turn_model_calls(source_context)
    web_calls = remote_fetch_attempt_count() if remote_fetch_scope_active() else 0
    route_skips = ["model", "tool_loop"]
    if web_calls == 0:
        route_skips.append("web")
    return {
        "route": f"{route_prefix}:{reason}",
        "route_reason": str(reason or "fast_path"),
        "route_skips": route_skips,
        "model_selected": "",
        "model_residency": {
            "known": False,
            "reason": "not_probed_on_deterministic_path",
        },
        "prompt_eval_count": 0,
        "model_calls": model_calls,
        "web_calls": web_calls,
        "capsule_mode": str(capsule.get("capsule_mode") or "none"),
        "exact_response_control": str(capsule.get("response_control") or "") == "capsule_exact",
        "fast_path_hit": True,
    }


_CREDIT_SEND_RE = re.compile(
    r"(?:send|transfer|give)\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(\S+)",
    re.IGNORECASE,
)
_CREDIT_SPEND_RE = re.compile(
    r"spend\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(?:prioriti[sz]e|boost|fund)",
    re.IGNORECASE,
)

# Whole-message `cloud ...` command that configures BYOK cloud escalation. Anchored so a
# stray "cloud" inside normal conversation does not trigger it.
_CLOUD_CMD_RE = re.compile(
    r"^\s*/?cloud(?:\s+(off|ask|auto|status|cap\s+\d+))?\s*$",
    re.IGNORECASE,
)

# `cloud key ...` — BYOK key onboarding from chat. Kept separate from _CLOUD_CMD_RE because the
# argument is an opaque secret rather than one of a fixed set of words.
_CLOUD_KEY_CMD_RE = re.compile(r"^\s*/?cloud\s+key(?:\s+(.+?))?\s*$", re.IGNORECASE | re.DOTALL)
_CLOUD_KEY_FORGET_WORDS = {"forget", "remove", "delete", "clear", "off"}
_CLOUD_CREDENTIAL_NAME = "llm.cloud.openrouter"

# `image key ...` — BYOK image-generation key onboarding from chat (fal.ai and any OpenAI-style
# endpoint). Same secret-sealing + owner-gating as the cloud key; the value is consumed here and never
# reaches a model. `image key <key> [model]` optionally overrides the fal model id.
_IMAGE_KEY_CMD_RE = re.compile(r"^\s*/?image\s+key(?:\s+(.+?))?\s*$", re.IGNORECASE | re.DOTALL)

# `cloud model ...` — pick which OpenRouter model the burst lane uses.
_CLOUD_MODEL_CMD_RE = re.compile(r"^\s*/?cloud\s+model(?:\s+(.+?))?\s*$", re.IGNORECASE)
_CLOUD_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?::[A-Za-z0-9._-]+)?$")

# `cloud models [free|all|coding|<search>]` — the LIVE catalog. free/all/coding list; anything else
# is a search query so the user can type a name instead of scrolling the whole list.
_CLOUD_MODELS_CMD_RE = re.compile(r"^\s*/?cloud\s+models(?:\s+(.+?))?\s*$", re.IGNORECASE | re.DOTALL)

# `cloud usage [today|week|month|all]` — read-only token-usage report per model over a window.
_CLOUD_USAGE_CMD_RE = re.compile(r"^\s*/?cloud\s+usage(?:\s+(today|week|month|all))?\s*$", re.IGNORECASE)

# Natural-language "what free AIs are on offer?" — must be answered from the live catalog, not by
# the local model (which invents ids). Requires a free-models noun phrase plus a listing verb.
_FREE_MODELS_INTENT_RE = re.compile(r"\bfree\b[^.?!]{0,40}\b(model|models|ai|ais|llm|llms)\b|\b(model|models|ai|ais|llm|llms)\b[^.?!]{0,40}\bfree\b", re.IGNORECASE)
_FREE_MODELS_VERB_RE = re.compile(r"\b(what|which|list|show|see|offer|available|check|browse|options?)\b", re.IGNORECASE)
# "the game the free ai gave us in this chat" is about the CONVERSATION, not a request to list the
# catalog — a reference to the past chat suppresses the free-models intent so it doesn't dump the list.
_FREE_MODELS_CHAT_REF_RE = re.compile(
    r"\b(this chat|our chat|the chat|chat history|conversation|history|earlier|previous(?:ly)?|"
    r"you (?:gave|made|wrote|said|created|showed|built|generated)|we (?:got|had|made|were)|gave us|"
    r"last (?:message|reply|turn|one)|that (?:game|code|thing|one|html|script))\b",
    re.IGNORECASE,
)

# Natural-language OpenRouter onboarding intent. Users do not speak in command syntax — "connect
# to openrouter for me", "i have my api key, set it up" — and without this the request falls
# through to the local model, which hallucinates generic CLI advice instead of using VOOL's own
# tooling. Anchored on an explicit "openrouter" mention plus an action word, with a negative guard
# so coding requests ABOUT the OpenRouter API ("write a script that calls openrouter") still reach
# the model.
_OPENROUTER_MENTION_RE = re.compile(r"\bopen\s*[-_]?\s*router\b", re.IGNORECASE)
_OPENROUTER_ACTION_RE = re.compile(
    r"\b(connect|set\s*up|setup|configure|use|using|stay|keep|add|link|integrate|enable|hook|api|key|switch)\b",
    re.IGNORECASE,
)
_OPENROUTER_CODING_GUARD_RE = re.compile(
    r"\b(write|script|code|coding|example|snippet|curl|python|javascript|typescript|implement|build\s+me|sdk|docs?|documentation)\b",
    re.IGNORECASE,
)
_OPENROUTER_INTENT_MAX_LEN = 400  # longer messages are real tasks, not onboarding requests

# Whole-message `brakes ...` command that toggles the per-spend OS-approval brake.
_BRAKES_CMD_RE = re.compile(r"^\s*/?brakes(?:\s+(on|off|status))?\s*$", re.IGNORECASE)


def maybe_handle_brakes_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``brakes on|off|status`` — the per-spend OS-approval brake (ON by default).

    Reading status is allowed from anywhere; turning the brake OFF or ON is honored only from
    the owner's own local session, and turning it OFF additionally requires a live OS approval
    (handled in :func:`core.spend_authorization.set_spend_consent_required`) so an injected chat
    message can never disable it.
    """
    match = _BRAKES_CMD_RE.match(user_input or "")
    if not match:
        return None
    from core.spend_authorization import set_spend_consent_required, spend_consent_required

    arg = (match.group(1) or "status").strip().lower()
    if arg == "status":
        if spend_consent_required():
            return "Spend brakes: ON — every wallet spend needs a live OS approval. (`brakes off` to change.)"
        return (
            "Spend brakes: OFF — VOOL can spend your wallet without asking each time. Only your "
            "own machine can still trigger a spend and the emergency freeze still works. "
            "`brakes on` to re-enable per-spend approval."
        )
    if not owner_local:
        return "Spend brakes can only be changed from your own local session."
    _changed, message = set_spend_consent_required(arg == "on")
    return message


def _cloud_key_configured() -> bool:
    """Whether a BYOK cloud key is actually available, so an ask/auto policy can really burst.

    Checks the OpenRouter BYOK lane the feature documents. Slot name and the
    FULL accepted env-alias set are read from the ONE canonical vocabulary
    table (``core.cloud_providers`` — ``slot_for`` / ``env_key_present``) —
    every alias, not just the primary one, so a host keyed through a
    compatibility alias reads PRESENT here exactly as it does on cloud status,
    memory-first routing, and the escalation lane.
    """
    from core.cloud_providers import env_key_present, slot_for

    if env_key_present("openrouter"):
        return True
    try:
        from core import credential_store

        return bool(credential_store.has_credential(slot_for("openrouter")))
    except Exception:
        return False


def _cloud_status_text(cep: Any, policy: Any, *, prefix: str = "") -> str:
    lines: list[str] = []
    if prefix:
        lines.append(prefix)
    if policy.mode == cep.MODE_OFF:
        lines.append("Cloud escalation is OFF — every task runs on the free local model.")
    elif policy.mode == cep.MODE_ASK:
        lines.append(
            "Cloud escalation is ASK — a hard task waits for your approval before any paid "
            "cloud call. Until the approval prompt is wired end to end this stays LOCAL, so it "
            "never spends without consent."
        )
    else:
        lines.append(
            "Cloud escalation is AUTO — eligible tasks can use your cloud key within your "
            "monetary budgets. There is no daily call-count limit."
        )
    # Readiness: an ask/auto policy does nothing without a cloud key to spend.
    if policy.mode != cep.MODE_OFF and not _cloud_key_configured():
        lines.append(
            "Note: no cloud key is configured yet, so nothing will burst to cloud. Give me your "
            "OpenRouter key with `cloud key <your-key>` — I seal it in the encrypted credential "
            "store on this machine and mask it out of the chat log (Settings > Cloud and "
            "OPENROUTER_API_KEY also work)."
        )
    try:
        from core import policy_engine

        if policy_engine.local_only_mode():
            lines.append(
                "Note: local-only mode is on, so this cloud policy is currently overridden — "
                "nothing will burst to cloud until local-only mode is off."
            )
    except Exception:
        pass
    try:
        from core import usage_meter

        lines.extend(usage_meter.usage_notices())
    except Exception:
        pass
    lines.append(
        "Controls: cloud off | cloud ask | cloud auto. "
        "Monetary budgets still apply. Only your own local session can change these."
    )
    return "\n".join(lines)


def maybe_handle_cloud_key_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``cloud key [<key>|forget]`` — BYOK cloud-key onboarding straight from chat.

    ``cloud key`` explains where the key goes, ``cloud key <value>`` seals it in the encrypted
    credential store, and ``cloud key forget`` deletes it. Returns None when the message is not a
    cloud-key command.

    Handling it here is what makes an inline key safe: the value is consumed at this fast-command
    layer, so it is never sent to a model or a provider, and the persisted turn is masked by
    :func:`core.secret_redaction.redact_secrets` inside
    :func:`core.persistent_memory.append_conversation_event` — the single choke point that redacts
    before the conversation log and every downstream memory writer. Setting or clearing the key is
    owner-local only, so a remote channel cannot install or wipe the owner's cloud key. The key is
    never echoed back; only its last four characters are shown so the owner can identify it.
    """
    match = _CLOUD_KEY_CMD_RE.match(user_input or "")
    if not match:
        return None
    arg = (match.group(1) or "").strip()

    if not owner_local:
        return "The cloud key can only be set from your own local session, so I left it unchanged."

    from core import credential_store
    from core.cloud_providers import PROVIDERS, active_provider, all_slots, config_for, detect_provider

    def _any_key() -> bool:
        try:
            return any(credential_store.has_credential(s) for s in all_slots())
        except Exception:
            return False

    if not arg:
        state = "A cloud key is already set." if _any_key() else "No cloud key is set yet."
        return (
            f"{state} You can hand it to me right here — paste `cloud key <your-key>` and I seal it "
            "in the encrypted credential store on this machine, then mask it out of the chat log. "
            "I detect the provider from the key; if it is ambiguous, add the provider after it "
            "(e.g. `cloud key sk-... openai`). You can also paste it into the Cloud field in "
            "Settings if you prefer. It stays on this machine and is only ever sent to the provider "
            "on a cloud burst you allow. `cloud key forget` removes it."
        )

    if arg.lower() in _CLOUD_KEY_FORGET_WORDS:
        prov = active_provider() or "openrouter"
        cfg = config_for(prov)
        try:
            removed = bool(credential_store.delete_credential(cfg.credential_slot))
        except Exception:
            removed = False
        if removed:
            # Retire the burst lane too — an enabled lane with no key would just fail to auth.
            try:
                from core.cloud_runtime import bump_cloud_broker_epoch
                from core.runtime_provider_defaults import deactivate_provider_byok

                deactivate_provider_byok(prov)
                bump_cloud_broker_epoch()
            except Exception:
                pass
            return f"{cfg.label} key removed and the cloud lane disabled."
        return "There was no stored cloud key to remove."

    # `cloud key <key> [provider]` — an explicit trailing provider disambiguates a bare `sk-`.
    parts = arg.split()
    secret = parts[0]
    explicit_provider = parts[1].strip().lower() if len(parts) > 1 else ""
    if explicit_provider:
        if explicit_provider not in PROVIDERS:
            return f"`{explicit_provider}` is not a known provider. Known: {', '.join(sorted(PROVIDERS))}."
        return _seal_cloud_key(secret, explicit_provider)
    guess = detect_provider(secret)
    if guess.confidence == "high":
        return _seal_cloud_key(secret, guess.provider_id)
    if guess.confidence == "low":
        cands = ", ".join(guess.candidates)
        return (
            f"That key's provider is ambiguous — it could be {cands}. Tell me which by adding it: "
            f"`cloud key <key> {guess.candidates[0]}` (or open Settings and pick the provider)."
        )
    # Too short to be any provider's key: say that (the historical message) and store nothing.
    if len(secret) < 16:
        return (
            "That does not look like a complete API key, so I did not store anything. Paste the "
            "whole key as `cloud key <your-key>` (an OpenRouter key starts with `sk-or-`, others vary)."
        )
    # Unrecognized prefix: NEVER store under a guessed default slot (credential intelligence
    # P0, 2026-09-02). A key sealed into the wrong provider's slot authenticates forever
    # against the wrong endpoint with a key the user knows is good — the worst failure this
    # flow can have. Ask instead; the operator's explicit trailing provider is authoritative.
    return (
        "I could not tell which provider that key belongs to from its shape, so I stored "
        "nothing. If you know it, add the provider after the key — `cloud key <your-key> "
        "openai` (or openrouter, groq, deepseek, moonshot, anthropic) — or paste it into the "
        "Cloud field in Settings and pick the provider there."
    )


def maybe_handle_image_key_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``image key [<key>|forget]`` — BYOK image-generation onboarding from chat.

    ``image key`` explains where the key goes, ``image key <fal-key> [model]`` seals a fal.ai service in
    the encrypted credential store and turns image generation on, and ``image key forget`` removes it.
    Owner-local only; the key is consumed here so it never reaches a model, and the persisted turn is
    masked by redact_secrets. Returns None when the message is not an image-key command.
    """
    match = _IMAGE_KEY_CMD_RE.match(user_input or "")
    if not match:
        return None
    arg = (match.group(1) or "").strip()

    if not owner_local:
        return "The image key can only be set from your own local session, so I left it unchanged."

    from core import media_tools

    if not arg:
        state = "An image service is already configured." if media_tools.has_image_service() else "No image service is set yet."
        return (
            f"{state} Paste your fal.ai key with `image key <your-key>` — I seal it in the encrypted "
            "credential store on this machine, turn image generation on, and mask it out of the chat log. "
            "It defaults to FLUX schnell; add a model to override (e.g. `image key <key> fal-ai/flux/dev`). "
            "The key stays on this machine and is only ever sent to fal when you ask for an image. "
            "`image key forget` removes it."
        )

    if arg.lower() in _CLOUD_KEY_FORGET_WORDS:
        removed = media_tools.forget_image_service()
        return (
            "Image service key removed and image generation disabled."
            if removed else "There was no stored image key to remove."
        )

    parts = arg.split()
    secret = parts[0]
    model = parts[1].strip() if len(parts) > 1 else ""
    if len(secret) < 16:
        return (
            "That does not look like a complete image key, so I did not store anything. Paste your full "
            "fal.ai key after `image key`."
        )
    shape_note = "" if media_tools.detect_fal_key(secret) else " (that didn't look like the usual fal `id:secret` shape, but I stored it — if generation fails, double-check the key)"
    ok, last4 = media_tools.configure_image_service(api_key=secret, provider="fal", model=model)
    if not ok:
        return "I couldn't seal that image key. Paste your full fal.ai key after `image key`."
    model_note = f"`{model}`" if model else "FLUX schnell"
    return (
        f"Sealed your fal image key (…{last4}) in the encrypted store and turned image generation on, "
        f"using {model_note}. Ask me to \"generate an image of …\" and I'll create it.{shape_note} "
        "`image key forget` removes it."
    )


def _seal_cloud_key(secret: str, provider_id: str = "openrouter") -> str:
    """Seal a cloud key for a provider in the encrypted store, confirm it landed, make that
    provider active, and register the burst lane. The provider is detected/chosen by the caller;
    an ambiguous bare `sk-` key is asked about there and never reaches here without a decision."""
    from core import credential_store
    from core.cloud_providers import config_for

    cfg = config_for(provider_id) or config_for("openrouter")
    provider_id = cfg.provider_id
    slot, label = cfg.credential_slot, cfg.label

    if len(secret) < 16:
        return (
            "That does not look like a complete API key, so I did not store anything. Paste the "
            "whole key as `cloud key <your-key>` (an OpenRouter key starts with `sk-or-`, others vary)."
        )
    try:
        credential_store.store_credential(slot, secret, label=label)
    except Exception:
        return (
            "I could not save the cloud key just now, so nothing was stored. Check that VOOL can "
            "write its data directory, then try again."
        )
    # store_credential can fail soft; confirm from the store rather than claim a save that never landed.
    try:
        stored = bool(credential_store.has_credential(slot))
    except Exception:
        stored = False
    if not stored:
        return (
            "I could not confirm the cloud key was saved, so treat it as not stored. Check that "
            "VOOL can write its data directory, then try again."
        )
    # Make this provider the active cloud lane. Reset the model to that provider's default ONLY
    # when switching providers, so re-pasting the same provider's key keeps the chosen model but a
    # NEW provider does not inherit a model id it does not have.
    try:
        from dataclasses import replace

        from core import cloud_escalation_policy as cep
        from core.cloud_providers import active_provider

        prev = active_provider(str(cep.load_policy().provider or "")) or "openrouter"
        keep_model = cep.load_policy().model if provider_id == prev else ""
        cep.save_policy(replace(cep.load_policy(), provider=provider_id, model=keep_model))
    except Exception:
        pass
    # Register the burst lane NOW — provider registration otherwise happens at boot, and a key
    # stored mid-session would silently do nothing until the next restart.
    lane_note = "The cloud lane is registered and will take effect after a restart."
    try:
        from core.cloud_runtime import bump_cloud_broker_epoch
        from core.runtime_provider_defaults import activate_provider_byok, retire_nonactive_provider_lanes

        bump_cloud_broker_epoch()  # the free/paid escalation broker must see the new key
        if activate_provider_byok(provider_id):
            lane_note = "The cloud lane is live now."
        # One active provider (v1): retire any other provider's still-enabled BYOK lane so a burst
        # can never route to the provider the user just switched away from.
        retire_nonactive_provider_lanes(provider_id)
    except Exception:
        pass
    return (
        f"{label} key saved (ends {secret[-4:]}) and sealed in your encrypted credential store. It "
        f"stays on this machine, and the key text is masked out of the chat log. {lane_note} "
        "Pick a model with `cloud model <id>` (or keep the default), then turn bursting on with "
        "`cloud ask` (approve each one) or `cloud auto`."
    )


# Bare cloud-key shapes (the user pasting just the key after being asked for it). Specific
# provider prefixes are listed BEFORE the generic bare `sk-` so an sk-or-/sk-ant-/sk-proj- key is
# recognized by its provider rather than swept up as an ambiguous bare `sk-`.
_BARE_KEY_RE = re.compile(
    r"\b(?:sk-or-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|sk-proj-[A-Za-z0-9_-]{16,}"
    r"|gsk_[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)
# Kept as an alias for any external reference; the multi-provider regex above supersedes it.
_OPENROUTER_BARE_KEY_RE = re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}\b")


# =====================================================================================
# R1g amendment — typed secret INTAKE spans. The three secret handlers above own
# detection, the credential store and the safe reply; these public helpers expose
# the SAME regexes' match geometry so the intake seam
# (`core.agent_runtime.secret_intake`) can carve the consumed span out of the
# original message without a second, drifting copy of the patterns. The handlers
# themselves are unchanged for messages that are nothing but the command.
# =====================================================================================


def cloud_key_command_match(text: str) -> re.Match[str] | None:
    """The cloud-key command match on `text`, or None (same regex the handler uses)."""
    return _CLOUD_KEY_CMD_RE.match(str(text or ""))


def image_key_command_match(text: str) -> re.Match[str] | None:
    """The image-key command match on `text`, or None (same regex the handler uses)."""
    return _IMAGE_KEY_CMD_RE.match(str(text or ""))


def bare_key_span(text: str) -> tuple[int, int] | None:
    """The (start, end) span of a bare provider key in `text`, or None.

    Applies the handler's OWN admission bound — a key recognized in a message of
    more than eight words is a paste, not a key-drop, and `maybe_handle_bare_secret`
    deliberately leaves it to the persistence redaction layer — so the intake and
    the handler can never disagree about what counts.
    """
    value = str(text or "")
    if not value.strip() or len(value.split()) > 8:
        return None
    match = _BARE_KEY_RE.search(value)
    if match is None:
        return None
    return (match.start(), match.end())


#: The cloud-key forget vocabulary, exposed for the intake's qualifier logic so
#: `cloud key forget and explain X` consumes the forget word, never the demand.
CLOUD_KEY_FORGET_WORDS = frozenset(_CLOUD_KEY_FORGET_WORDS)


_TELEGRAM_SETUP_RE = re.compile(
    r"(?:"
    r"connect\s+(?:me\s+|my\s+|vool\s+)?(?:to\s+)?telegram"
    # A bare "telegram bot" is usually a build, research, or documentation request. Keep
    # the setup fast path for explicit connection/setup language so it cannot hijack queries
    # such as "latest telegram bot api updates" before the live-info router sees them.
    r"|telegram\s+(?:bridge|setup|integration|account)"
    r"|(?:use|run|access|control|reach|talk\s+to|chat\s+with|work\s+(?:with|on|from))\s+"
    r"(?:vool|you|this|it)?\s*(?:from|on|via|through)\s+(?:my\s+)?(?:phone|mobile|telegram)"
    r"|work\s+from\s+my\s+phone"
    r"|vool\s+(?:on|to|via|through)\s+telegram"
    r"|(?:connect|link)\s+(?:my\s+)?(?:tg|telegram)"
    r")",
    re.IGNORECASE,
)


def maybe_handle_telegram_setup_intent(user_input: str, *, owner_local: bool = False) -> str | None:
    """Guide the user to connect VOOL to Telegram (chat from your phone) instead of fumbling it as a
    file write. VOOL ships a chat bridge (relay.bridge_workers.telegram_chat_bridge); point the user
    at @BotFather + how to start it, owner-locked."""
    text = " ".join(str(user_input or "").split())
    if not text or not _TELEGRAM_SETUP_RE.search(text):
        return None
    return (
        "Yes — I can bridge to Telegram so you can chat with me from your phone. No cloud, no open ports: "
        "the bridge runs here and reaches out to Telegram.\n\n"
        "**Setup (~2 min):**\n"
        "1. In Telegram, message **@BotFather** → `/newbot` → pick a name → it hands you a **bot token**.\n"
        "2. On this Mac, start the bridge with that token:\n"
        "   ```\n"
        "   TELEGRAM_BOT_TOKEN=<your-token> ~/Applications/vool/.venv/bin/python -m relay.bridge_workers.telegram_chat_bridge\n"
        "   ```\n"
        "3. Open your new bot and send it any message. **The first chat to message becomes the owner** — "
        "after that only your phone can drive this VOOL; everyone else is refused.\n\n"
        "Your phone → your bot → the bridge → me, and the reply comes back the same way. Messages from the "
        "bridge run as a remote *channel*, so I can chat and read but never act with your local owner "
        "privileges. Paste the token into your own terminal — I never handle it."
    )


def maybe_handle_bare_secret(user_input: str, *, owner_local: bool = False) -> str | None:
    """Catch a secret pasted bare in chat, before it can reach any model.

    VOOL asks for the key; users answer with the key — not with command syntax. A short message
    carrying a recognized provider key is sealed exactly as `cloud key <key>` would be; a bare
    `sk-` whose provider is ambiguous is ASKED about, never auto-stored under a guess. Any other
    short bare secret is stopped with guidance. Long messages are left alone (pasting a config file
    for help must still work); the persistence layer redacts those.
    """
    text = str(user_input or "").strip()
    if not text or len(text.split()) > 8:
        return None
    key_match = _BARE_KEY_RE.search(text)
    if key_match:
        if not owner_local:
            return "The cloud key can only be set from your own local session, so I left it unchanged."
        from core.cloud_providers import detect_provider

        secret = key_match.group(0)
        guess = detect_provider(secret)
        if guess.confidence == "high":
            return _seal_cloud_key(secret, guess.provider_id)
        # Ambiguous bare `sk-` (OpenAI / DeepSeek / Moonshot share it): ask, never guess-and-store.
        cands = ", ".join(guess.candidates) if guess.candidates else "openai, deepseek, moonshot"
        return (
            f"That looks like an API key but its provider is ambiguous ({cands}), so I did not "
            f"store it. Tell me which by pasting `cloud key {secret[:8]}… <provider>`, or open "
            "Settings and pick the provider."
        )
    try:
        from core.secret_redaction import contains_secret

        if not contains_secret(text):
            return None
    except Exception:
        return None
    return (
        "That looks like a secret, so I did not pass it to any model and will not repeat it — the "
        "chat log keeps only a masked copy. If it is your cloud provider key, paste it as "
        "`cloud key <key>` (or on its own — I detect the provider). For any other credential, use "
        "Settings > credentials so it is sealed in the encrypted store."
    )


# "Is the key set?" asked in plain words — must be answered from the store, not by the model.
_KEY_STATUS_SUBJECT_RE = re.compile(r"\b(api\s*key|key|openrouter|cloud)\b", re.IGNORECASE)
_KEY_STATUS_VERB_RE = re.compile(r"\b(set|stored|saved|configured|added|already|alr|have|check)\b", re.IGNORECASE)
_KEY_STATUS_CHECK_RE = re.compile(r"^\s*check\b.*\b(already|alr)\b.*\b(set|stored|saved)\b", re.IGNORECASE)

# One constant answer for every non-owner caller. Whether a key exists is itself owner-only:
# branching on the store here would turn this handler into an existence oracle even without the
# suffix. Refusing rather than returning None also keeps the local model from inventing an answer.
_CLOUD_KEY_STATUS_REMOTE_REFUSAL = (
    "Cloud-key status is owner-local only, so I will not say whether a key is stored. "
    "Ask from your own local session."
)


def maybe_handle_cloud_key_status_intent(user_input: str, *, owner_local: bool = False) -> str | None:
    """Answer "do we have the api key set?" / "check if this already set" from the store.

    Owner-local only: the reply reveals that a cloud key exists and its last four characters, so a
    channel message or any non-loopback caller gets a fixed refusal instead — never a store read.
    """
    text = str(user_input or "").strip()
    if not text or len(text) > 70:
        return None
    subject = bool(_KEY_STATUS_SUBJECT_RE.search(text) and _KEY_STATUS_VERB_RE.search(text))
    bare_check = bool(_KEY_STATUS_CHECK_RE.match(text)) and len(text) <= 40
    if not subject and not bare_check:
        return None
    # Whether a key is STORED and whether the CONNECTION works are different questions, and this
    # handler only knows the first. Driving the built runtime, "quick sanity check: is the
    # openrouter connection actually alive?" landed here — subject "openrouter", verb "check" — and
    # was answered "yes — a cloud key is sealed in the encrypted store" while the real probe verdict
    # was `untested`. Read against the question asked, that "yes" asserts a live connection nothing
    # had verified. A question about the connection, the lane or the serving model belongs to
    # core.runtime_lane_truth, which reads the probe verdict instead of inferring one from storage.
    lane_facet = runtime_lane_question(text)
    if lane_facet and lane_facet != FACET_KEY:
        return None
    if not owner_local:
        return _CLOUD_KEY_STATUS_REMOTE_REFUSAL

    from core import credential_store

    try:
        value = credential_store.get_credential(_CLOUD_CREDENTIAL_NAME) or ""
    except Exception:
        value = ""
    prefix = "If you mean your OpenRouter cloud key: " if bare_check and not subject else ""
    if value:
        return (
            f"{prefix}yes — a cloud key is sealed in the encrypted store (ends {value[-4:]}). "
            "`cloud status` shows the full setup; `cloud key forget` removes it."
        )
    return (
        f"{prefix}no — no cloud key is stored yet. Paste it as `cloud key <your-key>` (or on its "
        "own — I catch `sk-or-...` automatically) and I seal it in the encrypted store."
    )


_CLOUD_DIAG_RE = re.compile(
    r"\bcloud\s+diagnostics?\b"
    r"|\bcredential\s+diagnostics?\b"
    r"|\b(?:export|show|dump|list)\s+(?:my\s+)?(?:stored\s+)?(?:cloud\s+|credential\s+|api\s+)?"
    r"key(?:s|chain)?\s+diagnostics?\b",
    re.IGNORECASE,
)


def maybe_handle_cloud_diagnostics_intent(user_input: str, *, owner_local: bool = False) -> str | None:
    """`cloud diagnostics` — a REDACTED snapshot of stored credentials for support/export.

    Owner-local only. Shows each credential's name, label, backend, and MASKED last-4 — never a full
    secret (``credential_store.export_diagnostics`` is redacted by construction). Satisfies
    VOOL_MIGRATION.md §4 "export-diagnostics-with-secrets-redacted".
    """
    text = str(user_input or "").strip()
    if not text or len(text) > 64 or not _CLOUD_DIAG_RE.search(text):
        return None
    if not owner_local:
        return "Credential diagnostics are owner-only, so I won't report this host's stored keys from here."

    from core import credential_store

    try:
        diag = credential_store.export_diagnostics()
    except Exception:
        return "I couldn't read the credential store just now — try again in a moment."
    creds = list(diag.get("credentials") or [])
    where = "the macOS Keychain" if diag.get("backend") == "keychain" else "the encrypted local vault"
    if not creds:
        return f"No credentials are stored yet (backend: {where}). Add one with `cloud key <key>` or Settings."
    lines = [f"Stored credentials ({len(creds)}) — backend: {where}. No full secret is ever shown:"]
    for cred in creds:
        label = f" — {cred['label']}" if cred.get("label") else ""
        lines.append(f"- {cred.get('name')}{label}  ({cred.get('masked_suffix') or 'empty'})")
    lines.append("`cloud key forget` erases one. Keys live in the OS secret store — never in logs, the DB, or exports.")
    return "\n".join(lines)


# A token only counts as a MODEL name when it is model-shaped: a known family, or it carries a
# digit or hyphen (gpt-4.1, qwen3-coder, hy3). This stops an ordinary English word that happens
# to be a catalog family — "command" (cohere/command-*), "sonar" (perplexity/sonar) — from being
# read as a model, so "pick a command from the menu" / "use sonar to find it" reach the assistant.
# The digit/hyphen rule already covers versioned names; this list covers current families named
# as a single bare word (proper nouns unlikely to be used as common nouns in a switch sentence).
# Deliberately EXCLUDES common-noun families (command, sonar) — those are named via their
# hyphenated variant ("command-r") when the user really means the model.
_MODEL_SHAPED_FAMILY_RE = re.compile(
    r"^(deepseek|qwen|llama|gemma|nemotron|glm|kimi|mistral|mixtral|ministral|codestral|pixtral"
    r"|gpt|claude|gemini|grok|phi|olmo|nova|jamba|hermes|hunyuan|dolphin|reka|zephyr|openchat"
    r"|wizardlm|mythomax|sarvam|aya|granite|falcon|molmo|solar|starling|airoboros)$",
    re.IGNORECASE,
)


def _is_model_shaped(tok: str) -> bool:
    return bool(_MODEL_SHAPED_FAMILY_RE.match(tok)) or any(c.isdigit() for c in tok) or "-" in tok


def _model_token_match(tok: str, model_id: str) -> tuple[bool, bool]:
    """Whether ``tok`` names the model, and whether it is the exact full variant.

    A token matches only when it names the model FAMILY (first slug segment, e.g. ``gemma``,
    ``hy3``, ``deepseek``), a hyphen-delimited prefix of the variant (``qwen3-coder``), or the
    full variant — never an interior or suffix fragment. This is what stops an English word that
    happens to sit inside some id (``allenai/olmo-3-32b-think`` → "think", ``...-customtools`` →
    "tools") from hijacking ordinary chat, while still catching real model-name questions.
    """
    variant = model_id.split("/", 1)[-1].split(":", 1)[0].lower()
    family = variant.split("-", 1)[0]
    matches = tok in (variant, family) or variant.startswith(tok + "-")
    return matches, tok == variant


# "what about HY3?" — a model question answered from the live catalog, never guessed. Triggers
# ONLY when a word in a short message actually matches a catalog model, so ordinary chat that
# never names a model cannot be hijacked.
_MODEL_LOOKUP_TRIGGER_RE = re.compile(r"\?|\b(free|price|cost|available|what about|is there|why)\b", re.IGNORECASE)
_MODEL_LOOKUP_STOPWORDS = frozenset(
    ["what", "about", "the", "and", "for", "you", "not", "there", "this", "that", "with", "is", "it", "in", "on", "of", "are", "do", "we", "she", "her", "can", "was", "why", "how", "see", "dont", "don", "did", "does", "have", "has", "had", "they", "them", "from", "but", "all", "any", "our", "your", "mine", "out", "now", "free", "model", "models", "llm", "llms", "list", "chat", "code", "coder", "instruct", "mini", "nano", "ultra", "super", "pro", "max", "base", "turbo", "vision", "preview", "beta", "alpha", "reasoning", "content", "thinking", "safety", "cloud", "openrouter", "vool"]
)


# GRADUATED NAME EVIDENCE. A token with a digit or a hyphen (gpt-4.1, hy3, qwen3-coder,
# command-r) is a model name by shape and a bare "?" is enough to ask the catalog about it. A bare
# family word is weaker evidence: "solar", "llama", "falcon", "hermes", "nova", "granite", "phi",
# "aya", "gemini" are ordinary English words too, and this recognizer was hijacking plain
# knowledge questions on them -- measured on the s50 rig (3e1bf797): "What is the smallest planet
# in our solar system?" and "How does solar power work?" both routed to `model_lookup_intent` and
# answered from the OpenRouter catalog about `upstage/solar-pro-*`. Two structural facts separate
# the two uses, for every family alike and without enumerating any topic: a name is asked about
# inside a CATALOG FRAME (free / price / available / what about / is there / why / model), not
# under a bare question mark; and a name is never an attributive modifier of the noun that
# follows it ("solar system", "solar power") nor a common noun under a determiner ("a falcon",
# "our solar", "the llama"). "a hermes model" keeps its head noun and stays a name.
_MODEL_LOOKUP_CATALOG_FRAME_RE = re.compile(
    r"\b(free|price|prices|pricing|cost|costs|available|availability|what about|is there|why"
    r"|model|models|llm|llms|context|tokens?)\b",
    re.IGNORECASE,
)
_MODEL_LOOKUP_FUNCTION_WORDS = _MODEL_LOOKUP_STOPWORDS | frozenset(
    ["a", "an", "am", "be", "been", "being", "at", "to", "by", "as", "or", "if", "so", "up", "vs",
     "per", "via", "than", "then", "when", "which", "while", "into", "onto", "over", "under",
     "after", "before", "again", "still", "also", "just", "only", "very", "too", "yet", "its",
     "their", "his", "my", "here", "today", "yesterday", "tomorrow", "right", "already", "even"]
)
_COMMON_NOUN_DETERMINERS = frozenset(
    ["a", "an", "the", "our", "my", "your", "his", "her", "their", "its", "this", "these",
     "those", "some", "any", "every", "each", "no"]
)
_MODEL_HEAD_NOUNS = frozenset(["model", "models", "llm", "llms"])


def _content_word(word: str) -> bool:
    """A word that carries meaning of its own: not a function word, frame word, head noun, or
    another model-shaped token ("deepseek vs qwen" asks about both, not about a 'vs')."""
    return (
        word.isalpha()
        and word not in _MODEL_LOOKUP_FUNCTION_WORDS
        and word not in _MODEL_HEAD_NOUNS
        and not _MODEL_LOOKUP_CATALOG_FRAME_RE.fullmatch(word)
        and not _is_model_shaped(word)
    )


def _bare_family_used_as_name(words: list[str], index: int) -> bool:
    """Whether the bare family word at ``words[index]`` is used as a NAME, not an English word.

    A name asked about in a catalog frame is what the sentence is ABOUT, so nothing with meaning
    of its own follows it: "what about solar?", "is solar free?", "is there a hermes model?",
    "what about hermes on openrouter?". The English uses all put content after the word -- an
    attributive modifier's head ("solar system", "solar power"), a predicate ("granite so
    heavy"), a complement ("the llama a good pet") -- and a common noun sits under a determiner
    ("a falcon", "our solar") unless a head noun makes it a name again ("a hermes model").
    """
    nxt = words[index + 1] if index + 1 < len(words) else ""
    prev = words[index - 1] if index > 0 else ""
    if any(_content_word(word) for word in words[index + 1 :]):
        return False
    if prev in _COMMON_NOUN_DETERMINERS and nxt not in _MODEL_HEAD_NOUNS:
        return False
    return True


def _model_name_tokens(text: str) -> list[str]:
    """The words of ``text`` that name a model, under the graduated evidence rule above."""
    words = re.findall(r"[a-z0-9][a-z0-9._-]*", text.lower())
    framed = bool(_MODEL_LOOKUP_CATALOG_FRAME_RE.search(text))
    tokens: list[str] = []
    for index, word in enumerate(words):
        if len(word) < 3 or word in _MODEL_LOOKUP_STOPWORDS or not _is_model_shaped(word):
            continue
        strong = any(c.isdigit() for c in word) or "-" in word
        if not strong and not (framed and _bare_family_used_as_name(words, index)):
            continue
        tokens.append(word)
    return tokens


# The three catalog readers below — this one, `cloud models`, and the free-models intent — accept
# `owner_local` for a uniform dispatch signature but deliberately do not gate on it. They serve
# only the public openrouter.ai model list, which is fetched unauthenticated (the owner's key is
# never attached) and reveals nothing about this machine's configuration or stored credentials.
# Gating them would break catalog questions from a channel for no confidentiality gain. Any handler
# here that reads the credential store, the cloud policy, or the usage ledger must gate instead.
def maybe_handle_model_lookup_intent(user_input: str, *, owner_local: bool = False) -> str | None:
    text = str(user_input or "").strip()
    if not text or len(text) > 80 or not _MODEL_LOOKUP_TRIGGER_RE.search(text):
        return None
    tokens = _model_name_tokens(text)
    if not tokens:
        return None

    from core.openrouter_catalog import model_is_free, model_pricing_is_known, safe_all_models

    models, _age = safe_all_models(allow_network=True)
    if not models:
        return None
    matches = []
    for model in models:
        # Match the model FAMILY/variant, never a fragment of the id or the marketing display
        # name — so "can you create your own tools?" and "do you think ..." fall through to the
        # model instead of matching "...-customtools" / "...-think".
        for tok in tokens:
            if _model_token_match(tok, model.model_id)[0]:
                matches.append(model)
                break
    if not matches:
        return None  # no real catalog match -> let the model answer normally
    matches.sort(key=lambda m: (model_is_free(m), m.context_length), reverse=True)
    lines = []
    for model in matches[:3]:
        ctx = f"{model.context_length // 1000}k ctx" if model.context_length else "ctx n/a"
        if model_is_free(model):
            lines.append(f"- `{model.model_id}` is FREE right now ({ctx}) — select it with `cloud model {model.model_id}`")
        elif not model_pricing_is_known(model):
            # Printing a price here would mean inventing one: the provider published none.
            lines.append(f"- `{model.model_id}` has no published price ({ctx}) — treated as paid")
        else:
            per_m_in = float(model.prompt_usd_per_token or 0.0) * 1_000_000
            per_m_out = float(model.completion_usd_per_token or 0.0) * 1_000_000
            lines.append(f"- `{model.model_id}` is paid (~${per_m_in:.2f} in / ${per_m_out:.2f} out per 1M tokens, {ctx})")
    return "From the live OpenRouter catalog:\n" + "\n".join(lines)


def _free_models_text(*, focus: str = "free") -> str:
    """The LIVE free-model list, labelled with its cache age — never an invented one.

    "No catalog at all", "catalog read but nothing is free", and "nothing free matches this
    filter" are three different facts and each gets its own wording. ``safe_free_models`` returns
    ``age is None`` only when there is no usable catalog, so that flag — not an empty list — is
    what makes a claim about reaching openrouter.ai true.
    """
    from core.openrouter_catalog import model_is_coding, safe_free_models

    free, age = safe_free_models(allow_network=True)
    if age is None:
        return (
            "I could not reach openrouter.ai just now and have no cached catalog, so I will not "
            "guess at model names. Try `cloud models` again in a moment."
        )
    freshness = "just now" if age < 90 else f"{int(age // 60)} min ago"
    if not free:
        return (
            f"I read the OpenRouter catalog fine (refreshed {freshness}), and right now none of "
            "its models are free — every one carries a price. Nothing is wrong with the "
            "connection; the free tier is just empty at the moment. Try `cloud models` again "
            "later, or pick a paid model with `cloud model <id>`."
        )
    rows = [m for m in free if model_is_coding(m)] if focus == "coding" else list(free)
    if not rows:
        return (
            f"{len(free)} free models on OpenRouter right now — live catalog, refreshed "
            f"{freshness} — but none of them are coding models. `cloud models` lists all "
            f"{len(free)}."
        )
    shown = rows if focus == "all" else rows[:12]
    lines = []
    for m in shown:
        tag = " — coding" if model_is_coding(m) else ""
        ctx = f"{m.context_length // 1000}k ctx" if m.context_length else "ctx n/a"
        lines.append(f"- `{m.model_id}` ({ctx}){tag}")
    # Count what is actually listed: under `coding` the unfiltered free total would overstate it.
    noun = "free coding model" if focus == "coding" else "free model"
    label = noun if len(rows) == 1 else f"{noun}s"
    more = f" (+{len(rows) - len(shown)} more — `cloud models all` shows every one)" if len(rows) > len(shown) else ""
    return (
        f"{len(rows)} {label} on OpenRouter right now — live catalog, refreshed {freshness}, "
        "auto-refreshes hourly on use:\n" + "\n".join(lines) + more + "\n"
        "Pick one with `cloud model <id>`, or `cloud model auto` and I keep you on the best free "
        "chat + coding models as the list changes."
    )


def maybe_handle_cloud_models_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``cloud models [free|coding|all|<search>]`` — list what's free/coding, or search the
    live catalog by name so the user can type an AI's name instead of scrolling the whole list."""
    match = _CLOUD_MODELS_CMD_RE.match(user_input or "")
    if not match:
        return None
    arg = (match.group(1) or "free").strip()
    low = arg.lower()
    if low in ("free", "all", "coding"):
        return _free_models_text(focus=low if low in ("coding", "all") else "free")
    return _search_models_text(arg)


def _search_models_text(query: str) -> str:
    """Search the LIVE OpenRouter catalog by name. Ranks exact > name-part > prefix > substring, with
    free models first, so "hunyuan" or "deepseek" surfaces the right ids without the full list."""
    from core.openrouter_catalog import load_openrouter_catalog, model_is_free

    q = " ".join(str(query or "").strip().lower().split())
    if len(q) < 2:
        return "Give me at least 2 characters to search, e.g. `cloud models hunyuan`."
    try:
        catalog = load_openrouter_catalog()
    except Exception:
        catalog = ()
    if not catalog:
        return f"I couldn't read the OpenRouter catalog just now. Try `cloud models {query.strip()}` again in a moment."

    def _score(model):
        mid = str(model.model_id or "").lower()
        name_part = mid.split("/")[-1].split(":")[0]
        hay = f"{mid} {str(model.name or '').lower()}"
        if mid == q or name_part == q:
            return 0
        if name_part.startswith(q) or mid.startswith(q):
            return 1
        if q in name_part:
            return 2
        if q in hay:
            return 3
        return 99

    scored = sorted(
        ((_score(m), m) for m in catalog),
        key=lambda t: (t[0], 0 if model_is_free(t[1]) else 1, -int(getattr(t[1], "context_length", 0) or 0)),
    )
    hits = [m for score, m in scored if score < 99][:12]
    if not hits:
        return f"No OpenRouter model matches “{query.strip()}”. Try a shorter/different name, or `cloud models` for the free list."
    lines = [f"Models matching “{query.strip()}” (live OpenRouter catalog):"]
    lines += [f"- `{m.model_id}` — {'free' if model_is_free(m) else 'paid'}" for m in hits]
    lines.append("Switch with `cloud model <id>` (copy the full id).")
    return "\n".join(lines)


def maybe_handle_free_models_intent(user_input: str, *, owner_local: bool = False) -> str | None:
    """Answer "what free AIs are on offer?" from the live catalog instead of letting the local
    model invent ids (it produced nonexistent ones like `llama3:free`)."""
    text = str(user_input or "")
    if len(text) > _OPENROUTER_INTENT_MAX_LEN:
        return None
    if not _FREE_MODELS_INTENT_RE.search(text) or not _FREE_MODELS_VERB_RE.search(text):
        return None
    if _OPENROUTER_CODING_GUARD_RE.search(text):
        return None
    if _FREE_MODELS_CHAT_REF_RE.search(text):
        return None  # a reference to the past chat ("the free ai gave us…") isn't a catalog request
    return _free_models_text()


def maybe_handle_cloud_model_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``cloud model [<id>]`` — choose which cloud model the burst lane uses.

    ``cloud model`` shows the current choice; ``cloud model <vendor/name[:tag]>`` persists it in
    the cloud policy (so it survives restarts and is honored by provider registration) and
    re-registers the lane immediately when a key is present. ``cloud model default`` clears the
    override. Owner-local only for changes, like every other cloud mutation.

    Paid authority is the ONE shared server path: a pin classified paid/cost-unknown by
    :func:`core.cloud_model_control.classify_cloud_model_cost` is refused here exactly like the
    HTTP endpoint refuses it, unless the same message explicitly carries the spend confirmation
    for THIS switch (`--paid`, e.g. ``cloud model vendor/name --paid``). The consent travels only
    through this one invocation — nothing sticky is stored anywhere.
    """
    match = _CLOUD_MODEL_CMD_RE.match(user_input or "")
    if not match:
        return None
    arg = (match.group(1) or "").strip()

    from core import cloud_escalation_policy as cep
    from core.runtime_provider_defaults import default_openrouter_model_name

    if not arg:
        current = cep.load_policy().model or default_openrouter_model_name()
        shown = "auto (best free chat + coding, follows the live catalog)" if current == "auto" else f"`{current}`"
        return (
            f"The cloud lane uses {shown}. `cloud models` lists what is free right now; change with "
            "`cloud model <id>` (free ids end in `:free`), or `cloud model auto` to have me keep "
            "you on the best free chat + coding models automatically. "
            "`cloud model default` goes back to the default."
        )
    # Request-scoped spend consent, parsed off before anything else looks at the id. Free ids,
    # `auto` and `default` ignore it; refused pins explain how to give it properly.
    confirm_paid = False
    lowered_tail = arg.lower()
    for flag in ("--confirm-paid", "--paid"):
        if lowered_tail.endswith(flag):
            confirm_paid = True
            arg = arg[: -len(flag)].strip()
            break

    # A bare partial name ("hunyuan", "deepseek") is a search, not a full id: surface the matching
    # ids to pick from rather than failing to switch. Full ids (vendor/name[:tag]), 'auto' and
    # 'default' switch directly.
    if arg.lower() not in ("auto", "default") and "/" not in arg:
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", arg):
            return (
                f"`{arg}` does not look like a model id (a bare id like `gpt-4.1-mini` or a "
                "`vendor/name[:tag]` slug), so I left the model unchanged."
            )
        return _search_models_text(arg)
    # All mutations flow through the ONE shared switch path (also used by the NL switch intent
    # and the UI's POST /api/cloud/model), where the central paid classification now lives — so
    # no surface can persist a paid pin past the gate another surface would have hit.
    from core.cloud_model_control import set_cloud_model

    _ok, message, _chosen = set_cloud_model(
        arg, owner_local=owner_local, confirm_paid=confirm_paid
    )
    return message


def maybe_handle_cloud_usage_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle ``cloud usage [today|week|month|all]`` — a read-only token-usage report.

    Reads the real ledger (served responses) for the window and shows free-local vs paid-cloud
    totals plus the top per-model rows. Read-only in the sense that it never spends or mutates,
    but the ledger is owner-private — it names the models the owner runs and what they cost — so
    it is owner-local only. Read-only does not mean public.
    """
    match = _CLOUD_USAGE_CMD_RE.match(user_input or "")
    if not match:
        return None
    if not owner_local:
        return (
            "Cloud usage is owner-local only, so I will not report this machine's token or spend "
            "history from here. Ask from your own local session."
        )
    from core import usage_meter

    span = (match.group(1) or "all").strip().lower()
    if span in ("today", "week", "month"):
        window = usage_meter.resolve_window(range_=span)
        since_ts, until_ts = window["since_ts"], window["until_ts"]
        header = {"today": "today", "week": "this week (from Monday)", "month": "this month"}[span]
    else:
        since_ts = until_ts = None
        header = "all time"
    summary = usage_meter.usage_summary(since_ts=since_ts, until_ts=until_ts)
    by_model = usage_meter.usage_by_model(since_ts=since_ts, until_ts=until_ts)

    lines = [f"Cloud usage — {header}", usage_meter.format_report(summary)]
    if by_model:
        lines.append("By model:")
        for row in by_model[:8]:
            label = row["model_id"] or row["provider_id"] or "unknown"
            money = ""
            if row.get("cost_class") == usage_meter.COST_PAID_CLOUD:
                usd = float(row.get("usd") or 0.0)
                money = f"  {'$' if row.get('all_actual') else '~$'}{usd:.4f}"
            lines.append(f"  {label:<34} {row['total_tokens']:>10,} tokens  ({row['responses']} responses){money}")
    else:
        lines.append("No usage recorded in this window yet.")
    return "\n".join(lines)


# "How does the spend cap work?" / "can you guarantee my $5 limit?" — answered here, from the
# real policy + ledger, because the local model improvises on this question and improvising about
# a money guarantee is the worst possible failure mode. The honest answer is a BRAKE, not a fence:
# VOOL declines to START a call it estimates would breach the cap, and cannot promise zero
# overshoot. The long form lives in docs/SPEND_CAPS.md; this handler says the same thing and fills
# in this machine's real numbers.
_SPEND_CAP_DOC = "docs/SPEND_CAPS.md"

# A "strong" subject is a compound that can only be about a spending ceiling, so it fires on its
# own. A bare "budget", "limit" or "cap" is NOT here — those are ordinary English and must reach
# the assistant ("what is a budget", "there is no limit to what you can do").
_SPEND_CAP_SUBJECT_RE = re.compile(
    r"\b(spend|spends|spending|cost|costs|dollar|dollars|usd|money|budget|price|billing)"
    r"[\s-]*(cap|caps|limit|limits|ceiling|ceilings|guard|guardrail|guardrails|brake|brakes)\b"
    r"|\b(cap|caps|limit|limits|ceiling|brake)\s+on\s+(spend\w*|cost\w*|money|billing|usage|usd)\b"
    r"|\b(daily|monthly|per[\s-]?call|per[\s-]?task)\s+(cap|limit|budget|max|maximum|ceiling)\b"
    r"|\bspend\w*\s+(guard\w*|brake|control|protection|authoriz\w+)\b"
    r"|\$\s?\d[\d,.]*\s*(cap|limit|budget|ceiling|max|maximum|a\s+day|per\s+day|a\s+month)\b"
    r"|\b(cap|caps|limit|limits)\b[^.?!]{0,25}\$\s?\d"
    r"|\boverspend\w*\b",
    re.IGNORECASE,
)
# The "what happens if I go over?" family. Kept separate and deliberately narrow so that "let's go
# over the plan" (review, not overspend) can never match: an overrun needs either an explicit
# "what happens" frame or a spend-shaped object after "over".
_SPEND_CAP_OVERRUN_RE = re.compile(
    r"\b(what\s+happens|what\s+if|happens?)\b[^.?!]{0,45}"
    r"\b(go(?:ing)?\s+over|goes\s+over|went\s+over|exceed\w*|overshoot\w*|overrun|blow\s+past)\b"
    r"|\b(go(?:ing)?|goes|went|run|runs)\s+over\s+(the\s+|my\s+|that\s+|your\s+)?"
    r"(cap|caps|limit|limits|budget|ceiling|\$)"
    r"|\b(exceed\w*|overshoot\w*|breach\w*|blow\s+past)\s+(the\s+|my\s+|your\s+)?"
    r"(cap|caps|limit|limits|budget|ceiling)\b",
    re.IGNORECASE,
)
# A question/explain frame. Without one, "set the spend cap" and "the spend cap is fine" are not
# requests for an explanation. Deliberately excludes the bare copula ("is"/"are") and "work(s)":
# those match ordinary statements ABOUT the cap, which are not requests to have it explained.
_SPEND_CAP_QUESTION_RE = re.compile(
    r"\?|\b(how|what|why|when|can|could|does|do|will|would|should|explain|tell|guarantee\w*"
    r"|promise\w*|sure|certain|happens?|trust)\b",
    re.IGNORECASE,
)
# This handler EXPLAINS; it must never swallow a request to CHANGE a cap (that is `cloud cap N`,
# and it runs before this one only for the exact command form). A mutation-shaped message falls
# through untouched.
_SPEND_CAP_MUTATION_RE = re.compile(
    r"\b(set|change|raise|lower|increase|decrease|bump|reduce|update|make|configure|put|drop|lift)\b"
    r"[^.?!]{0,30}\b(cap|caps|limit|limits|budget|ceiling)\b"
    r"|\b(cap|caps|limit|limits|budget|ceiling)\b[^.?!]{0,20}\bto\s+\$?\d",
    re.IGNORECASE,
)
# Someone else's household finances are not VOOL's spend cap. A message anchored on an outside
# money subject reaches the assistant instead of getting this canned answer.
_SPEND_CAP_OFFTOPIC_RE = re.compile(
    r"\b(credit\s*card|debit\s*card|bank|mortgage|rent|grocer\w+|shopping|salary|payroll|tax|taxes"
    r"|insurance|loan|overdraft|savings\s+account|household|wedding|holiday|vacation|steam|netflix)\b",
    re.IGNORECASE,
)
_SPEND_CAP_MAX_LEN = 160

# The general truth is not machine state — it is how the product works, and it is published in
# docs/SPEND_CAPS.md. Everyone gets it. Only the NUMBERS below are owner-private.
_SPEND_CAP_EXPLAINER = (
    "Straight answer: the spend cap is a brake, not a fence. I will not START a paid call I "
    "estimate would breach your cap, and I stop starting them once the cap is reached — but I "
    "cannot promise you never go a cent over. Each call is reserved at what it is estimated "
    "to cost, so overshoot stays near one call's cost while I know the model's price. If a "
    "provider reports no usage figures, I record less than was spent and the dollar ceiling "
    "stops moving -- then only the daily call count limits you. "
    "not by zero.\n"
    "Why an exact cap is impossible:\n"
    "  - A call's cost is not knowable before it runs. Output length is only known once the "
    "model has finished generating, so the last call before the ceiling can cross it.\n"
    "  - I settle from token counts times a published price. That is an estimate of the "
    "provider's bill, not the bill — providers meter on their own side and may count cached "
    "reads, system tokens or tool tokens differently.\n"
    "  - Prices change, and my price table can be out of date.\n"
    "  - A call already in flight cannot be recalled when the ceiling is reached.\n"
    "If you need a hard ceiling, set it at the provider — that is the only real one, because the "
    "provider is the party that meters and bills you. Prepaid credit with auto-reload off is the "
    "strictest form; a budget alert is not a cap, it only notifies.\n"
    "I never hold your money and I never proxy the call: your key goes from this machine to the "
    "provider, so the bill is between you and them, and I can only decline to start the next "
    "call — never refund or cancel one.\n"
    "About this build specifically: the paid-cloud lane is unaudited and its caps have been "
    "measured NOT to bind as described above — the dollar ceilings currently settle at $0.00 for "
    "every provider except OpenRouter, and concurrent turns can each pass a cap that had room "
    "for one. Set a limit at your provider and treat that as the only one until this line is "
    "gone."
)


def _spend_cap_state_lines() -> list[str] | None:
    """This machine's REAL configured caps and today's usage, or None if it cannot be read.

    Returns None rather than a partial or invented figure: a spend answer that guesses its own
    numbers is worse than one that admits it could not look.
    """
    try:
        from core import cloud_escalation_policy as cep
        from core import usage_meter
        from core.paid_call_reservation import spend_limits

        policy = cep.load_policy().normalized()
        used_calls = int(cep.used_today())
        limits = spend_limits()
        window = usage_meter.resolve_window(range_="today")
        summary = usage_meter.usage_summary(
            since_ts=window["since_ts"], until_ts=window["until_ts"]
        )
        paid = summary[usage_meter.COST_PAID_CLOUD]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("spend-cap state read failed (%s)", exc)
        return None

    spent = float(paid.get("usd") or 0.0)
    responses = int(paid.get("responses") or 0)
    exact = bool(paid.get("all_actual", True))
    if responses == 0:
        spend_line = "$0.0000 — no paid cloud responses today"
    elif exact:
        spend_line = (
            f"${spent:.4f} over {responses} response(s) — every one carried a "
            "provider-reported cost"
        )
    else:
        spend_line = (
            f"~${spent:.4f} over {responses} response(s) — some or all of that is my own "
            "estimate from token counts, not a provider figure"
        )
    return [
        "On this machine right now:",
        f"  cloud escalation mode: {policy.mode}",
        f"  cloud reservations today: {used_calls} (usage statistic; no call-count limit)",
        f"  USD ceilings:          ${limits.per_call_usd:.2f}/call, ${limits.per_task_usd:.2f}/task, "
        f"${limits.daily_usd:.2f}/day, ${limits.monthly_usd:.2f}/month",
        f"  paid spend today:      {spend_line}",
    ]


def maybe_handle_spend_cap_explainer_intent(
    user_input: str, *, owner_local: bool = False
) -> str | None:
    """Answer "how does the spend cap work" / "can you guarantee my $5 limit" honestly.

    The local model improvises on this question, and an improvised money guarantee is the worst
    thing this product can say, so the answer is deterministic. The general explanation is public
    (it is just how the product works, published in ``docs/SPEND_CAPS.md``); the configured caps
    and today's usage are owner-private machine state, so a non-owner caller gets the explanation
    with the numbers withheld. If the state cannot be read, it says so instead of guessing.

    Falls through (``None``) for ordinary chat that merely uses the words budget/limit/cap, and
    for a request to CHANGE a cap.
    """
    text = str(user_input or "").strip()
    if not text or len(text) > _SPEND_CAP_MAX_LEN:
        return None
    if _SPEND_CAP_MUTATION_RE.search(text) or _SPEND_CAP_OFFTOPIC_RE.search(text):
        return None
    subject = bool(_SPEND_CAP_SUBJECT_RE.search(text))
    overrun = bool(_SPEND_CAP_OVERRUN_RE.search(text))
    if not subject and not overrun:
        return None
    if subject and not _SPEND_CAP_QUESTION_RE.search(text):
        return None

    lines = [_SPEND_CAP_EXPLAINER]
    if not owner_local:
        lines.append(
            "Your configured caps and today's spend are owner-local only, so I will not report "
            "this machine's numbers from here. Ask from your own local session."
        )
    else:
        state = _spend_cap_state_lines()
        if state is None:
            lines.append(
                "I could not read this machine's cap settings or spend ledger just now, so I am "
                "not going to quote you numbers I have not looked at. Check that VOOL can read "
                "its data directory, then ask again."
            )
        else:
            lines.extend(state)
    lines.append(f"The full write-up is in `{_SPEND_CAP_DOC}`; `cloud usage today` shows the ledger.")
    return "\n".join(lines)


# Natural-language ACTIONS. Unlike the informational intents above, these PERFORM the real
# operation (switch/refresh/probe) and report the actual outcome — closing the gap where the
# local model narrated "Switching to HY3" while nothing happened. Each returns a structured
# dict for the action fast-path (or None to fall through), never a bare string.
_CLOUD_SWITCH_VERB_RE = re.compile(
    r"\b(switch(?:\s+(?:me\s+)?to)?|go\s+with|let'?s\s+go\s+with|lets\s+go\s+with|con\w{1,6}t(?:\s+me)?\s+to|use|pick|select)\b",
    re.IGNORECASE,
)
_CLOUD_SWITCH_MAX_LEN = 100
# Words that name local tiers or generic concepts — never treat them as a cloud-model candidate,
# so "switch to heavy" (a LOCAL tier) can never touch the cloud policy.
_CLOUD_SWITCH_EXTRA_STOPWORDS = frozenset(
    ["fast", "daily", "heavy", "local", "auto", "default", "reset", "lane", "switch", "with",
     "pick", "select", "use", "using", "lets", "connect", "conenct", "please", "pls", "now"]
)

# Only a refresh-shaped verb aimed explicitly at the model catalog fires this — NOT the generic
# "update ... list" ("update the reading/shopping/guest list" is ordinary chat), and not a bare
# "list".
_CATALOG_REFRESH_RE = re.compile(
    r"\b(refresh|re-?fetch|re-?check|reload)\b[^.?!]{0,30}\b(models?|catalog|catalogue)\b"
    r"|\b(models?|catalog|catalogue)\b[^.?!]{0,30}\b(refresh|re-?fetch|re-?check|reload)\b"
    r"|\brefresh\b[^.?!]{0,15}\b(now|again)\b",
    re.IGNORECASE | re.DOTALL,
)
_CONNECTION_TEST_RE = re.compile(
    r"\b(confirm|check|test|verify)\b.{0,40}\bconn\w{0,7}\b"
    r"|\bconn\w{0,7}\b.{0,40}\b(confirm|check|test|verify|working|status)\b"
    r"|\bare\s+we\s+connected\b",
    re.IGNORECASE | re.DOTALL,
)
# A connection test uses the owner's cloud KEY against the provider, so it must be about the
# cloud/OpenRouter — never a bare "are we connected?" (peer/DB connectivity in a P2P product).
_CLOUD_SUBJECT_RE = re.compile(
    r"\b(open\s*[-_]?\s*router|cloud|api[-\s]?key|\bapi\b|byok|provider|openai)\b",
    re.IGNORECASE,
)


def maybe_handle_cloud_switch_intent(user_input: str, *, owner_local: bool = False) -> dict[str, Any] | None:
    """"lets go with HY3" / "switch to gemma" — REALLY switch the cloud model, or say why not.

    Fires only on: a short message, a switch verb, and a candidate word that matches the live
    catalog. Zero catalog matches fall through to the model (ordinary chat is never hijacked);
    a candidate matching several distinct models gets a disambiguation reply and NO action; an
    exact variant-name match ("hy3" -> tencent/hy3) narrows families like hy3 vs hy3-preview,
    and a free variant is preferred. The switch itself is the ONE shared path (`set_cloud_model`).
    """
    text = str(user_input or "").strip()
    if not text or len(text) > _CLOUD_SWITCH_MAX_LEN:
        return None
    if not _CLOUD_SWITCH_VERB_RE.search(text):
        return None
    if _OPENROUTER_CODING_GUARD_RE.search(text):
        return None
    tokens = [
        w
        for w in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", text.lower())
        if w not in _MODEL_LOOKUP_STOPWORDS and w not in _CLOUD_SWITCH_EXTRA_STOPWORDS
    ]
    if not tokens:
        return None

    from core.openrouter_catalog import model_is_free, safe_all_models

    models, _age = safe_all_models(allow_network=True)
    if not models:
        return None
    tokens = [t for t in tokens if _is_model_shaped(t)]  # only model-shaped words can name a model
    if not tokens:
        return None
    matched = []
    exact = []
    for model in models:
        # Family/variant match only (see _model_token_match), so a switch verb plus a common
        # word that merely appears inside some model's id or name cannot trigger a real switch.
        for tok in tokens:
            ok, is_exact = _model_token_match(tok, model.model_id)
            if ok:
                matched.append(model)
                if is_exact:
                    exact.append(model)
                break
    if exact:
        matched = exact
    if not matched:
        return None  # no real catalog candidate -> ordinary chat, let the model answer
    base_ids = {m.model_id.split(":", 1)[0] for m in matched}
    if len(base_ids) > 1:
        matched.sort(key=lambda m: (model_is_free(m), m.context_length), reverse=True)
        lines = "\n".join(
            f"- `{m.model_id}`" + (" — free" if model_is_free(m) else "") for m in matched[:3]
        )
        return {
            "response": (
                "A few models match that — tell me which one:\n" + lines +
                "\nPick with `cloud model <id>` or say it more precisely."
            ),
            "success": False,
            "advice_only": True,
            "reason": "cloud_switch_ambiguous",
            "details": {"candidates": sorted(base_ids)},
        }
    # Unique family: prefer the free variant when it exists.
    frees = [m for m in matched if model_is_free(m)]
    chosen = (frees or sorted(matched, key=lambda m: len(m.model_id)))[0].model_id
    if not owner_local:
        return {
            "response": "The cloud model can only be changed from your own local session, so I left it unchanged.",
            "success": False,
            "advice_only": True,
            "reason": "cloud_switch_refused_remote",
            "details": {"requested": chosen},
        }

    # A11 server-owned paid authority: the loose text intent can never self-certify pricing. The
    # SAME mechanical classifier that gates the HTTP endpoint and the exact command gates here;
    # a paid or cost-unknown match refuses BEFORE anything is persisted, naming how to consent,
    # and no caller-supplied `_verified_free` exists anymore to wave it through.
    from core.cloud_model_control import (
        MODEL_COST_UNKNOWN,
        PAID_STATUS_UNKNOWN,
        classify_cloud_model_cost,
    )

    classification = classify_cloud_model_cost(provider_id="openrouter", model_id=chosen)
    if classification["cost_state"] != "free":
        if classification["reason_code"] == MODEL_COST_UNKNOWN:
            reason_wording = "I could not verify what this model costs"
        elif classification["reason_code"] == PAID_STATUS_UNKNOWN:
            reason_wording = "This model's published pricing is indeterminate"
        else:
            reason_wording = f"`{chosen}` is PAID per the catalog"
        return {
            "response": (
                f"{reason_wording}, so I left your cloud model unchanged. Pinning it spends "
                "provider credits per turn. To switch anyway, make it an explicit paid pin: "
                f"`cloud model {chosen} --paid` — the confirmation covers that switch only."
            ),
            "success": False,
            "advice_only": True,
            "reason": "cloud_switch_paid_confirm_required",
            "details": {
                "requested": chosen,
                "cost_state": classification["cost_state"],
                "reason_code": classification["reason_code"] or "PAID_MODEL_CONFIRM_REQUIRED",
            },
        }

    from core.cloud_model_control import set_cloud_model

    ok, message, chosen_id = set_cloud_model(chosen, owner_local=True)
    activated_provider = ""
    if ok:
        try:
            from core.runtime_provider_defaults import activate_provider_byok

            activated_provider = activate_provider_byok("openrouter")
        except Exception:
            activated_provider = ""
    return {
        "response": message,
        "success": bool(ok),
        "advice_only": False,
        "reason": "cloud_switch_intent",
        "details": {
            "requested": chosen,
            "model": chosen_id,
            "provider_id": activated_provider or "openrouter-byok",
            "provider_activated": bool(activated_provider),
            "route": "cloud_model_control",
            "tool_backed": True,
        },
    }


def maybe_handle_catalog_refresh_intent(user_input: str, *, owner_local: bool = False) -> dict[str, Any] | None:
    """"refresh the list now" — REALLY refresh the catalog and report the real counts."""
    text = str(user_input or "").strip()
    if not text or len(text) > 80:
        return None
    if not _CATALOG_REFRESH_RE.search(text):
        return None
    if _OPENROUTER_CODING_GUARD_RE.search(text) or re.search(r"\b(page|browser|tab|memory)\b", text, re.IGNORECASE):
        return None
    if not owner_local:
        return None  # a refresh hits the network + rewrites the on-disk cache — owner's session only

    from core.cloud_model_control import force_catalog_refresh

    result = force_catalog_refresh()
    if result.get("ok"):
        response = (
            f"Catalog refreshed from openrouter.ai just now — {result['free']} free of "
            f"{result['total']} models. `cloud models` shows the list."
        )
    else:
        response = (
            "I tried to refresh the catalog just now but could not reach openrouter.ai "
            f"({result.get('error', 'network error')}); the previous cached list still stands."
        )
    return {
        "response": response,
        "success": bool(result.get("ok")),
        "advice_only": False,
        "reason": "catalog_refresh_intent",
        "details": {**result, "tool_backed": True},
    }


def maybe_handle_connection_test_intent(user_input: str, *, owner_local: bool = False) -> dict[str, Any] | None:
    """"confirm the connection" — REALLY probe the provider's auth-gated endpoint and report."""
    text = str(user_input or "").strip()
    if not text or len(text) > 80:
        return None
    if not _CONNECTION_TEST_RE.search(text):
        return None
    if not _CLOUD_SUBJECT_RE.search(text):
        return None  # must name the cloud/OpenRouter — "are we connected?" (peers) is not this
    if _OPENROUTER_CODING_GUARD_RE.search(text) or re.search(r"\b(wifi|internet|ethernet|vpn|bluetooth)\b", text, re.IGNORECASE):
        return None
    if not owner_local:
        return None  # the probe sends the owner's stored key to the provider — owner's session only

    from core import cloud_escalation_policy as cep
    from core.cloud_connection_state import STATE_NO_KEY, STATE_OK, run_auth_probe
    from core.runtime_provider_defaults import default_openrouter_model_name

    result = run_auth_probe()
    state = result.get("state")
    if state == STATE_NO_KEY:
        response = (
            "There is no cloud key stored yet, so there is nothing to test — paste it as "
            "`cloud key <your-key>` first."
        )
    elif state == STATE_OK:
        try:
            policy = cep.load_policy()
            model = policy.model or default_openrouter_model_name()
            mode = policy.mode
        except Exception:
            model, mode = "", ""
        response = (
            f"Connection verified — OpenRouter accepted your key just now (HTTP {result.get('http_status')}). "
            f"Cloud model: `{model}`, bursting `{mode}`."
        )
    elif result.get("detail") == "unauthorized":
        response = (
            "Connection test FAILED — OpenRouter rejected the key (HTTP "
            f"{result.get('http_status')}). Re-check it, or paste a new one with `cloud key <key>`."
        )
    else:
        response = (
            "Connection test FAILED — I could not reach OpenRouter just now "
            f"({result.get('detail', 'network error')}). The key itself was not judged."
        )
    return {
        "response": response,
        "success": state == STATE_OK,
        "advice_only": False,
        "reason": "connection_test_intent",
        "details": {**{k: result.get(k) for k in ("state", "detail", "http_status")}, "tool_backed": True},
    }


def maybe_handle_openrouter_intent(
    user_input: str,
    *,
    owner_local: bool = False,
    requested_model: str = "",
) -> str | None:
    """Answer natural-language OpenRouter onboarding requests deterministically.

    "Connect to openrouter for me", "I have my API key, set it up" — without this interceptor
    those reach the local model, which does not know VOOL's own tooling and improvises generic
    (often wrong) CLI instructions. This routes them to the real flow instead. Guarded so coding
    questions about the OpenRouter API and long real tasks still go to the model.

    Owner-local only: both branches below are keyed on whether a cloud key is stored, so answering
    a non-owner at all — even with the generic onboarding text — would disclose key existence.
    Every step this describes is owner-gated anyway, so there is nothing a non-owner could act on.
    """
    text = str(user_input or "")
    if len(text) > _OPENROUTER_INTENT_MAX_LEN:
        return None
    if not _OPENROUTER_MENTION_RE.search(text):
        return None
    if not _OPENROUTER_ACTION_RE.search(text):
        return None
    if _OPENROUTER_CODING_GUARD_RE.search(text):
        return None
    if not owner_local:
        return (
            "Cloud setup is owner-local only, so I will not report or change the OpenRouter "
            "connection from here. Ask from your own local session."
        )

    from core import cloud_escalation_policy as cep
    from core.runtime_provider_defaults import default_openrouter_model_name

    try:
        from core import credential_store

        has_key = bool(credential_store.has_credential(_CLOUD_CREDENTIAL_NAME))
    except Exception:
        has_key = False

    if not has_key:
        return (
            "I can set that up myself — no CLI, no environment variables. Three steps, right here:\n"
            "1. Paste your key as: `cloud key <your-OpenRouter-key>` — I seal it in the encrypted "
            "credential store on this machine and mask it out of the chat log.\n"
            "2. Pick a model: `cloud models` shows what is free right now; `cloud model <id>` picks "
            "one, or `cloud model auto` keeps you on the best free chat + coding models as the "
            "live list changes.\n"
            "3. Turn bursting on: `cloud ask` (I check with you each time) or `cloud auto`.\n"
            "Your key stays on this machine and is only sent to OpenRouter on a burst you allow."
        )
    pinned_model = str(requested_model or "").strip()
    if pinned_model and pinned_model.lower() not in {"vool", "vool:latest", "auto"}:
        return (
            f"Yes — this chat is pinned to `{pinned_model}`. Substantive turns stay on that model; "
            "VOOL's local control and safety commands may still execute locally, and tool results "
            "are returned to the pinned model for the answer. The `cloud off/ask/auto` setting only "
            "controls automatic cloud bursting from VOOL Auto; it does not disable this explicit "
            "per-chat model pin."
        )
    policy = cep.load_policy()
    model = policy.model or default_openrouter_model_name()
    shown = "auto (best free chat + coding)" if model == "auto" else f"`{model}`"
    mode_note = (
        f"bursting is `{policy.mode}`" if policy.mode != cep.MODE_OFF else "bursting is OFF — turn it on with `cloud ask` or `cloud auto`"
    )
    return (
        f"Already connected: your OpenRouter key is sealed in the credential store, the lane uses "
        f"{shown}, and {mode_note}. Useful controls: `cloud status`, `cloud models` (live free "
        "list), `cloud model <id>` or `cloud model auto`, `cloud key forget`."
    )


def maybe_handle_cloud_command(user_input: str, *, owner_local: bool = False) -> str | None:
    """Handle a ``cloud ...`` chat command that configures BYOK cloud escalation.

    Returns a human-readable confirmation/status string, or None when the message is not a
    cloud command. The whole message must be the command: ``cloud``/``cloud status``,
    ``cloud off``, ``cloud ask``, ``cloud auto``, ``cloud cap N``. Reading status is allowed
    from anywhere, but CHANGING mode/cap is honored only from the owner's own local session
    (``owner_local``, the server-stamped trust signal from :mod:`core.request_trust`), so a
    remote channel message or a forged ``surface`` cannot flip the owner's cloud spend policy.
    An API key is never accepted inline (it would land in the chat transcript) — the key is
    stored separately through the encrypted credential store.
    """
    match = _CLOUD_CMD_RE.match(user_input or "")
    if not match:
        return None

    from core import cloud_escalation_policy as cep

    arg = (match.group(1) or "status").strip().lower()
    is_mutation = arg != "status"
    if is_mutation and not owner_local:
        return _cloud_status_text(
            cep, cep.load_policy(),
            prefix="Cloud escalation can only be changed from your own local session.",
        )

    prefix = ""
    if arg in (cep.MODE_OFF, cep.MODE_ASK, cep.MODE_AUTO):
        cep.set_mode(arg)
        # Verify the change actually persisted before claiming success: set_mode/save_policy
        # fail soft (a store write can fail without raising), so re-read and confirm rather
        # than report a mode change that never hit disk.
        if cep.load_policy().mode != arg:
            return (
                f"I could not save the cloud setting ({arg}) just now, so it is unchanged. "
                "Check that VOOL can write its data directory, then try again."
            )
        prefix = f"Cloud escalation set to {arg}."
    elif arg.startswith("cap"):
        return "Call-count limits have been removed. Use Settings → Usage & Budgets to manage monetary spending limits. No setting was changed."
    return _cloud_status_text(cep, cep.load_policy(), prefix=prefix)


def maybe_handle_credit_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None = None,
    signer_module: Any = signer_mod,
    transfer_credits_fn: Any,
    get_credit_balance_fn: Any,
    escrow_credits_for_task_fn: Any,
    session_hive_state_fn: Any = session_hive_state,
    runtime_session_id_fn: Any = runtime_session_id,
) -> dict[str, Any] | None:
    send_match = _CREDIT_SEND_RE.search(user_input)
    if send_match:
        amount = float(send_match.group(1))
        target_peer = send_match.group(2).strip()
        peer_id = signer_module.get_local_peer_id()
        ok = transfer_credits_fn(peer_id, target_peer, amount, reason="chat_transfer")
        if ok:
            response = f"Sent {amount:.2f} credits to {target_peer}. Your new balance: {get_credit_balance_fn(peer_id):.2f}."
        else:
            balance = get_credit_balance_fn(peer_id)
            response = f"Transfer failed. Your balance is {balance:.2f} credits (need {amount:.2f})."
        session_id = runtime_session_id_fn(device=agent.device, persona_id=agent.persona_id)
        return {
            "task_id": str(uuid.uuid4()),
            "response": response,
            "response_class": "task_status",
            "confidence": 0.95,
            "mode": "fast_path",
            "model_execution": {"used_model": False, "source": "credit_ledger"},
            "session_id": session_id,
            "source_context": source_context or {},
        }

    spend_match = _CREDIT_SPEND_RE.search(user_input)
    if spend_match:
        amount = float(spend_match.group(1))
        peer_id = signer_module.get_local_peer_id()
        hive_state = session_hive_state_fn(session_id)
        interaction_payload = dict(hive_state.get("interaction_payload") or {})
        active_topic_id = str(interaction_payload.get("active_topic_id") or "").strip()
        wants_current_hive_task = any(
            marker in " ".join(str(user_input or "").strip().lower().split())
            for marker in ("current hive task", "this hive task", "active hive task")
        )
        task_id = active_topic_id or str(uuid.uuid4())
        if wants_current_hive_task and not active_topic_id:
            response = "I don't have an active Hive task selected in this session, so I can't attach credits to a real task yet."
            return {
                "task_id": str(uuid.uuid4()),
                "response": response,
                "response_class": "task_status",
                "confidence": 0.9,
                "mode": "fast_path",
                "model_execution": {"used_model": False, "source": "credit_ledger"},
                "session_id": session_id,
                "source_context": source_context or {},
            }
        ok = escrow_credits_for_task_fn(peer_id, task_id, amount)
        if ok:
            if active_topic_id:
                response = (
                    f"Reserved {amount:.2f} credits to prioritize Hive task `{active_topic_id[:8]}`. "
                    f"Remaining balance: {get_credit_balance_fn(peer_id):.2f}."
                )
            else:
                response = (
                    f"Reserved {amount:.2f} credits to prioritize your Hive task. "
                    f"Remaining balance: {get_credit_balance_fn(peer_id):.2f}."
                )
        else:
            balance = get_credit_balance_fn(peer_id)
            response = f"Could not reserve credits. Your balance is {balance:.2f} (need {amount:.2f})."
        return {
            "task_id": task_id,
            "response": response,
            "response_class": "task_status",
            "confidence": 0.95,
            "mode": "fast_path",
            "model_execution": {"used_model": False, "source": "credit_ledger"},
            "session_id": session_id,
            "source_context": source_context or {},
        }

    return None


# Fast-path reasons whose response is NOT a task.
#
# Every other fast path IS one: a date, a sum, a smalltalk reply and a UI command are each a unit of
# work the runtime was asked for, performed and closed, so minting a task id and emitting
# `task_completed` for them records something that happened. A turn with no request in it is not.
# There is no goal, no envelope, no checkpoint, no tool call and no model call -- nothing ran -- so
# a `fast-...` id and a `task_completed` event would be the runtime writing a task record for work
# that never existed, and `core/task_event_model.py` would project it to a `task.completed` card.
#
# Membership is the whole mechanism: adding a reason here is how a lane opts out of task
# bookkeeping, and no caller has to change to do it.
_NON_TASK_FAST_PATH_REASONS = frozenset({"empty_turn_fast_path"})


def fast_path_reason_is_task(reason: str) -> bool:
    """Whether a fast-path reason describes work the runtime actually performed."""
    return str(reason or "").strip().lower() not in _NON_TASK_FAST_PATH_REASONS


def fast_path_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    response: str,
    confidence: float,
    source_context: dict[str, object] | None,
    reason: str,
    classification_details: dict[str, Any] | None = None,
    runtime_event_details: dict[str, Any] | None = None,
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
    checkpoint_status: str = "completed",
    failure_text: str = "",
    route_prefix: str = "deterministic",
) -> dict[str, Any]:
    # A task-bound fast path gets a task id; a non-task one gets a TURN id and is labelled as a
    # turn everywhere it lands. It still needs an identity -- the honesty passes read their receipts
    # by target id, and a lane with no receipt at all gets its report rewritten -- but the identity
    # must say what it actually identifies.
    # M3 requirement 7: every fast lane's OWN successful observations mint support on the turn's
    # grounding lifecycle. Here rather than in each lane because this is the one builder every
    # fast lane returns through -- the alternative is a list of lanes to keep in step, and a lane
    # missing from that list is a lane asked to prove work it really did. The sweep reads the same
    # same-turn evidence channels `turn_ran_observations` reads, and `core.observation_evidence`
    # decides which entries are observations, so a failed lookup mints nothing.
    try:
        from core.grounding_lifecycle import harvest_turn_observations

        harvest_turn_observations(source_context if isinstance(source_context, dict) else None)
    except Exception:
        pass
    is_task = fast_path_reason_is_task(reason)
    pseudo_task_id = f"fast-{uuid.uuid4().hex[:12]}" if is_task else ""
    response_id = pseudo_task_id or f"turn-{uuid.uuid4().hex[:12]}"
    target_type = "task" if is_task else "turn"
    turn_result = agent._turn_result(
        response,
        agent._fast_path_response_class(reason=reason, response=response, details=classification_details),
        debug_origin=reason,
    )
    agent._apply_interaction_transition(session_id, turn_result)
    decorated_response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    append_conversation_event_fn(
        session_id=session_id,
        user_input=user_input,
        assistant_output=decorated_response,
        source_context=source_context,
        response_class=turn_result.response_class.value,
    )
    audit_logger_module.log(
        "agent_fast_path_response",
        target_id=response_id,
        target_type=target_type,
        details={"reason": reason, "source_surface": (source_context or {}).get("surface")},
    )
    _record_routing_decision(session_id=session_id, user_input=user_input, family=reason)
    is_explicit_heavy_block = reason == "explicit_heavy_model_blocked"
    lane = _fast_path_lane(reason)
    phase = "blocked" if is_explicit_heavy_block else "completed"
    planned_model_id = "qwen3.5:35b-a3b" if is_explicit_heavy_block else ""
    # `model_not_used` is a claim about the whole turn, and on a turn that spent provider calls
    # upstream of this lane it is false -- the exact line a live 136s turn printed under two
    # `model.call_completed` events. The answer genuinely owes nothing to a model either way, so
    # the distinction is named rather than dropped: nothing ran, versus nothing ran FOR THE ANSWER.
    from core.turn_model_call_ledger import turn_call_accounting

    _call_accounting = turn_call_accounting(source_context)
    _turn_provider_calls = int(_call_accounting.get("calls") or 0)
    _attempted_providers = list(_call_accounting.get("providers") or [])
    _timed_out = "PROVIDER_TIMEOUT" in set(
        _call_accounting.get("failed_error_classes") or []
    )
    if is_explicit_heavy_block:
        fallback_reason = "explicit_heavy_lane_unavailable"
    elif _timed_out:
        fallback_reason = "model_attempt_timed_out_before_fast_path_answer"
    elif _turn_provider_calls > 0:
        fallback_reason = "model_attempt_failed_before_fast_path_answer"
    else:
        fallback_reason = "model_not_used"
    # Provider attempts describe what the turn invoked, not who authored this answer. A failed
    # model immediately before a deterministic composition is never the actual answer adapter.
    proof_provider_id = "runtime-fast-path"
    proof_model_id = ""
    verifier_status = "blocked_no_primary" if is_explicit_heavy_block else "not_required"
    agent._emit_chat_truth_metrics(
        task_id=response_id,
        target_type=target_type,
        reason=reason,
        response_text=decorated_response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via="fast_path",
        fast_path_hit=True,
        model_inference_used=False,
        model_final_answer_hit=False,
        model_execution_source="fast_path",
        tool_backing_sources=agent._chat_truth_fast_path_backing_sources(reason),
    )
    # True of a non-task turn too, and the field it fills is `turn_id` -- no model ran, and this is
    # the receipt that says so.
    agent._emit_runtime_event(
        source_context,
        event_type="model_lane_proof",
        message="Fast-path response completed without model inference.",
        schema="vool.model_lane_proof.v1",
        turn_id=response_id,
        session_id=session_id,
        task_class=reason,
        task_kind="fast_path",
        output_mode="plain_text",
        complexity=_fast_path_complexity(reason),
        lane=lane,
        lane_type=lane,
        phase=phase,
        provider_role="drone",
        role="drone",
        planned_provider_id="runtime-fast-path",
        planned_model_id=planned_model_id,
        provider_id=proof_provider_id,
        model_id=proof_model_id,
        actual_adapter_provider_id="",
        actual_adapter_model_id="",
        backend="provider_attempt_then_fast_path" if _turn_provider_calls else "fast_path",
        tokens_per_second=0.0,
        measurement_source=(
            "provider_call_ledger_plus_deterministic_fast_path"
            if _turn_provider_calls
            else "deterministic_fast_path"
        ),
        queue_depth=0,
        fallback_reason=fallback_reason,
        verifier_status=verifier_status,
        kv_cache_status="fast_path=not_applicable",
        speculative_status="inactive",
        attempted=_attempted_providers,
        failover_used=False,
        mismatch=False,
    )
    # Nothing is left spinning by the omission: the two callers that pass a non-task reason both
    # return from `_run_once_inner` ahead of its `task_received`/`task_resumed` emit, so no
    # `task.started` card was ever opened for this turn that a `task.completed` would have to close.
    if is_task:
        agent._emit_runtime_event(
            source_context,
            event_type="task_completed",
            message=f"Fast-path response ready: {agent._runtime_preview(decorated_response)}",
            task_id=pseudo_task_id,
            status=reason,
            **_builder_runtime_event_details(runtime_event_details),
        )
    # `checkpoint_status`/`failure_text` let a caller whose response is only PARTIALLY successful
    # (e.g. a typed multipart plan where some subtasks failed) say so -- prior to this parameter,
    # every fast-path caller finalized as "completed" with an empty failure_text regardless of
    # what the rendered response actually showed, which is why "why did that fail?" had nothing
    # structured to answer from. `action_fast_path_result` already had this distinction; this
    # brings `fast_path_result` to parity with it.
    #
    # This runs for a non-task response too, and must: it CLOSES a checkpoint that already exists
    # and never creates one (`core/agent_runtime/checkpoints.py:finalize_runtime_checkpoint`
    # returns early when the source context carries no `runtime_checkpoint_id`). At the front door
    # there is none and this is a no-op; on a resume there is a real, poisoned one, and leaving it
    # open would keep offering it back.
    agent._finalize_runtime_checkpoint(
        source_context,
        status=str(checkpoint_status or "completed"),
        final_response=decorated_response,
        failure_text=str(failure_text or ""),
    )
    return {
        # Empty when this response is not a task. The key stays -- `run_once`'s contract has it and
        # `core/channel_gateway.py:process_channel_request` subscripts it -- but an id here would
        # claim a task existed. `turn_id` carries the identity that actually exists.
        "task_id": pseudo_task_id,
        "turn_id": response_id,
        "response": str(decorated_response or ""),
        "mode": "advice_only",
        "confidence": float(confidence),
        "understanding_confidence": 1.0,
        "interpreted_input": user_input,
        "topic_hints": [],
        "prompt_assembly_report": {},
        "model_execution": {"source": "fast_path", "used_model": False},
        "media_analysis": {"used_provider": False, "reason": "fast_path"},
        "curiosity": {"mode": "skipped", "reason": "fast_path"},
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": "",
        "response_class": turn_result.response_class.value,
        **_fast_path_route_metadata(reason, route_prefix=route_prefix, source_context=source_context),
    }


def action_fast_path_result(
    agent: Any,
    *,
    task_id: str,
    session_id: str,
    user_input: str,
    response: str,
    confidence: float,
    source_context: dict[str, object] | None,
    reason: str,
    success: bool,
    details: dict[str, object] | None = None,
    mode_override: str | None = None,
    task_outcome: str | None = None,
    learned_plan: Any | None = None,
    workflow_summary: str = "",
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
    explicit_planner_style_requested_fn: Any,
) -> dict[str, Any]:
    # M3 requirement 7: every fast lane's OWN successful observations mint support on the turn's
    # grounding lifecycle. Here rather than in each lane because this is the one builder every
    # fast lane returns through -- the alternative is a list of lanes to keep in step, and a lane
    # missing from that list is a lane asked to prove work it really did. The sweep reads the same
    # same-turn evidence channels `turn_ran_observations` reads, and `core.observation_evidence`
    # decides which entries are observations, so a failed lookup mints nothing.
    try:
        from core.grounding_lifecycle import harvest_turn_observations

        harvest_turn_observations(source_context if isinstance(source_context, dict) else None)
    except Exception:
        pass
    turn_result = agent._turn_result(
        response,
        agent._action_response_class(
            reason=reason,
            success=success,
            task_outcome=task_outcome,
            response=response,
        ),
        workflow_summary=workflow_summary,
        debug_origin=reason,
        allow_planner_style=explicit_planner_style_requested_fn(user_input),
    )
    agent._apply_interaction_transition(session_id, turn_result)
    decorated_response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    append_conversation_event_fn(
        session_id=session_id,
        user_input=user_input,
        assistant_output=decorated_response,
        source_context=source_context,
        response_class=turn_result.response_class.value,
    )
    agent._update_task_result(
        task_id,
        outcome=task_outcome or ("success" if success else "failed"),
        confidence=confidence,
    )
    if success and learned_plan is not None:
        agent._promote_verified_action_shard(task_id, learned_plan)
    audit_logger_module.log(
        "agent_channel_action",
        target_id=task_id,
        target_type="task",
        details={
            "reason": reason,
            "success": bool(success),
            "source_surface": (source_context or {}).get("surface"),
            "source_platform": (source_context or {}).get("platform"),
            **dict(details or {}),
        },
    )
    _record_routing_decision(session_id=session_id, user_input=user_input, family=reason)
    agent._emit_chat_truth_metrics(
        task_id=task_id,
        reason=reason,
        response_text=decorated_response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via="action_fast_path",
        fast_path_hit=True,
        model_inference_used=False,
        model_final_answer_hit=False,
        model_execution_source="channel_action",
        tool_backing_sources=agent._chat_truth_action_backing_sources(
            reason=reason,
            success=success,
            task_outcome=task_outcome,
        ),
    )
    checkpoint_status = "completed" if success and (task_outcome or "success") == "success" else (
        "pending_approval" if (task_outcome or "") == "pending_approval" else "failed"
    )
    event_type = (
        "task_completed"
        if checkpoint_status == "completed"
        else "task_pending_approval"
        if checkpoint_status == "pending_approval"
        else "task_failed"
    )
    if _should_emit_action_lane_proof(reason=reason, details=details):
        agent._emit_runtime_event(
            source_context,
            event_type="model_lane_proof",
            message="Runtime action path completed without model inference.",
            schema="vool.model_lane_proof.v1",
            turn_id=task_id,
            session_id=session_id,
            task_class=reason,
            task_kind="tool_workflow",
            output_mode="action_result",
            complexity="medium",
            lane="daily",
            lane_type="daily",
            phase=checkpoint_status,
            provider_role="drone",
            role="drone",
            planned_provider_id="runtime-action",
            planned_model_id="",
            provider_id="runtime-action",
            model_id="",
            actual_adapter_provider_id="",
            actual_adapter_model_id="",
            backend="tool_workflow",
            tokens_per_second=0.0,
            measurement_source="runtime_action_path",
            queue_depth=0,
            fallback_reason="model_not_used",
            verifier_status="not_required",
            kv_cache_status="tool_workflow=not_applicable",
            speculative_status="inactive",
            attempted=[],
            failover_used=False,
            mismatch=False,
        )
    agent._emit_runtime_event(
        source_context,
        event_type=event_type,
        message=(
            f"{'Completed' if checkpoint_status == 'completed' else 'Awaiting approval for' if checkpoint_status == 'pending_approval' else 'Failed'} action response: "
            f"{agent._runtime_preview(decorated_response)}"
        ),
        task_id=task_id,
        status=reason,
        **_builder_runtime_event_details(dict(details or {})),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status=checkpoint_status,
        final_response=decorated_response if checkpoint_status == "completed" else "",
        failure_text="" if checkpoint_status != "failed" else decorated_response,
    )
    return {
        "task_id": task_id,
        "response": str(decorated_response or ""),
        "mode": mode_override or ("tool_queued" if success else "tool_failed"),
        # run_once closes the turn-root attempt from `result.get("success", True)`; a payload
        # without the key turns every fast-path FAILURE into a SUCCEEDED attempt while the
        # checkpoint this same function just marked says failed. The verdict this result was
        # built from must ride the result.
        "success": bool(success),
        "confidence": float(confidence),
        "understanding_confidence": 1.0,
        "interpreted_input": user_input,
        "topic_hints": [
            "discord"
            if "discord" in user_input.lower()
            else "telegram"
            if "telegram" in user_input.lower()
            else "channel"
        ],
        "prompt_assembly_report": {},
        "model_execution": {"source": "channel_action", "used_model": False},
        "media_analysis": {"used_provider": False, "reason": "channel_action"},
        "curiosity": {"mode": "skipped", "reason": "channel_action"},
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": workflow_summary,
        "response_class": turn_result.response_class.value,
        "details": dict(details or {}),
        **_fast_path_route_metadata(
            reason, route_prefix="action", source_context=source_context
        ),
    }


def _fast_path_lane(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized in {
        "smalltalk_fast_path",
        "heartbeat_poll_fast_path",
        "date_time_fast_path",
        "direct_math_fast_path",
        "same_chat_math_recall",
        "same_chat_transcript_recall",
        "ui_command_fast_path",
        # No model, no tools, no request to weigh -- the cheapest turn the runtime can have.
        "empty_turn_fast_path",
    }:
        return "tiny"
    if normalized == "explicit_heavy_model_blocked":
        return "deep"
    return "daily"


def _fast_path_complexity(reason: str) -> str:
    lane = _fast_path_lane(reason)
    if lane == "tiny":
        return "trivial"
    if lane == "deep":
        return "hard"
    return "medium"


def _should_emit_action_lane_proof(*, reason: str, details: dict[str, object] | None) -> bool:
    if not str(reason or "").startswith("model_tool_intent_"):
        return True
    tool_steps = [
        str(item).strip()
        for item in list((details or {}).get("tool_steps") or [])
        if str(item).strip()
    ]
    return bool(tool_steps)


def _builder_runtime_event_details(payload: dict[str, Any] | None) -> dict[str, Any]:
    builder = dict((payload or {}).get("builder_controller") or {})
    if not builder:
        return {}
    tool_steps = [
        str(item).strip()
        for item in list(builder.get("tool_steps") or [])
        if str(item).strip()
    ]
    artifacts = dict(builder.get("artifacts") or {})
    changed_paths: list[str] = []
    for item in list(artifacts.get("file_diffs") or []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    details: dict[str, Any] = {}
    if tool_steps:
        details["tool_name"] = tool_steps[-1]
        details["tool_steps"] = tool_steps
    if changed_paths:
        details["changed_paths"] = changed_paths
        details["touched_paths"] = list(changed_paths)
    return details


def maybe_handle_capability_truth_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    capability_truth_for_request_fn: Any,
    render_capability_truth_response_fn: Any,
) -> dict[str, Any] | None:
    # Which provider is connected, whether its key passed a probe, and which lane served the last
    # turn are facts this runtime stores. Left to the model they were answered from priors, and on
    # commit 12128c2 the answer was a flat denial of the cloud provider that had just served the
    # very message making it — footer `cloud | nemotron-3-ultra-550b-a55b:free | 1,295 tok`, with
    # /api/cloud/status reporting openrouter/ok/authorized at the same moment.
    #
    # First in this handler on purpose. The gap machinery below can answer "I can't confirm that"
    # to a question phrased as "can you confirm the cloud connection is working?" — and here there
    # IS evidence, so a hedge would be its own wrong answer. See core/runtime_lane_truth.py.
    lane_answer = maybe_runtime_lane_answer(user_input)
    if lane_answer:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=lane_answer,
            confidence=0.97,
            source_context=source_context,
            reason="runtime_lane_truth_query",
        )

    report = capability_truth_for_request_fn(
        user_input,
        extra_entries=agent._capability_ledger_entries(),
    )
    normalized = _normalize_capability_prompt(user_input)
    if not report:
        if _looks_like_capability_inventory_prompt(normalized):
            response_text = (
                compact_capabilities_text(agent)
                if _wants_compact_capability_inventory(normalized)
                else agent._help_capabilities_text()
            )
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=response_text,
                confidence=0.96,
                source_context=source_context,
                reason="capability_truth_query",
            )
        if normalized in {
            "what can you do",
            "what can you do right now",
            "what can you do right now on this machine",
            "what can you do on this machine",
            "what are you able to do right now on this machine",
            "what actions can you take right now on this machine",
        }:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=agent._help_capabilities_text(),
                confidence=0.96,
                source_context=source_context,
                reason="capability_truth_query",
            )
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=render_capability_truth_response_fn(report),
        confidence=0.96,
        source_context=source_context,
        reason="capability_truth_query",
    )


# A recall/summary of the mission itself (recall verb bound directly to the "mission" noun, so it
# does NOT fire on "Active mission: cap 0.05. What files did you change?"). A pure value-setter
# ("Update mission: cap 0.05 SOL") has no recall verb and does not match.
_MISSION_RECALL_RE = re.compile(
    r"\b(?:summari[sz]e|recap|state|describe|give me|what(?:'s| is| are)?|which|remind me(?: of| about)?)\s+"
    r"(?:the\s+|my\s+|our\s+|current\s+|active\s+)*mission\b"
    r"|\bcurrent mission\b|\bthe mission so far\b",
    re.IGNORECASE,
)


def maybe_handle_mission_render_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    current_active_mission_slots_fn: Any,
    render_mission_answer_fn: Any,
) -> dict[str, Any] | None:
    """Answer a mission summary/recall turn deterministically from the current session's typed
    active-mission slots, BEFORE any context/model call — so cross-session "Prior session
    continuity" can never contaminate an exact money/domain value. Returns None (falls through to
    the normal path) unless the current session has slots AND the message is a mission recall."""
    slots = current_active_mission_slots_fn(session_id)
    if not slots:
        return None
    norm = " ".join(str(user_input or "").lower().split())
    if not _MISSION_RECALL_RE.search(norm):
        return None
    rendered = render_mission_answer_fn(slots, honor_forbidden=True)
    if not rendered:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=rendered,
        confidence=0.97,
        source_context=source_context,
        reason="active_mission_render",
    )


def help_capabilities_text(agent: Any) -> str:
    lines = [
        "Wired on this runtime:",
        "- plain-language reasoning, persistent memory, and rolling chat continuity",
        "- local-first model with opt-in BYOK cloud burst: `cloud off|ask|auto` "
        "and `cloud status` — monetary budgets apply; off by default, changeable only from your own local session",
    ]
    supported_entries = [entry for entry in agent._capability_ledger_entries() if entry.get("supported")]
    partial_entries = [
        entry
        for entry in supported_entries
        if str(entry.get("support_level") or "").strip().lower() == "partial"
    ]
    full_entries = [
        entry
        for entry in supported_entries
        if str(entry.get("support_level") or "").strip().lower() != "partial"
    ]
    unsupported_entries = [entry for entry in agent._capability_ledger_entries() if not entry.get("supported")]
    for entry in full_entries:
        lines.append(f"- {str(entry.get('claim') or '').strip()}")
    if partial_entries:
        lines.append("")
        lines.append("Partially supported on this runtime:")
        for entry in partial_entries:
            claim = str(entry.get("claim") or "").strip()
            note = str(entry.get("partial_reason") or "").strip()
            lines.append(f"- {claim}" + (f" ({note})" if note else ""))
    lines.append("- I report real tool executions, approval previews, and failures directly instead of bluffing")
    if unsupported_entries:
        lines.append("")
        lines.append("Not wired or not enabled here:")
        for entry in unsupported_entries:
            reason = str(entry.get("unsupported_reason") or entry.get("claim") or "").strip()
            if reason:
                lines.append(f"- {reason}")
    return "\n".join(lines)


def compact_capabilities_text(agent: Any) -> str:
    supported_entries = [entry for entry in agent._capability_ledger_entries() if entry.get("supported")]
    claims = " ".join(str(entry.get("claim") or "").lower() for entry in supported_entries)
    includes_download = "download" in claims
    includes_sandbox = "sandbox" in claims or "command" in claims
    parts = [
        "Local powers here:",
        "workspace file/folder read-write",
    ]
    if includes_download:
        parts.append("downloads")
    if includes_sandbox:
        parts.append("bounded sandbox commands")
    parts.append("runtime and machine inspection")
    return ", ".join(parts[:-1]) + ", and " + parts[-1] + "."


def _normalize_capability_prompt(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split()).strip(" \t\r\n?!.,")


def _looks_like_capability_inventory_prompt(normalized: str) -> bool:
    if not normalized:
        return False
    if "what are your actual local powers" in normalized:
        return True
    if "what can you actually do" in normalized and any(marker in normalized for marker in ("machine", "local", "locally", "right now")):
        return True
    return "what can you do" in normalized and any(
        marker in normalized
        for marker in ("machine", "local", "locally", "right now")
    )


def _wants_compact_capability_inventory(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "one short line",
            "one clean line",
            "one line only",
            "real quick",
            "short answer only",
        )
    )


def render_credit_status(normalized_input: str) -> str:
    from core.credit_ledger import list_credit_ledger_entries, reconcile_ledger
    from core.dna_wallet_manager import DNAWalletManager
    from core.scoreboard_engine import get_peer_scoreboard

    peer_id = signer_mod.get_local_peer_id()
    ledger = reconcile_ledger(peer_id)
    scoreboard = get_peer_scoreboard(peer_id)
    wallet_status = DNAWalletManager().get_status()
    mention_wallet = any(token in normalized_input for token in ("wallet", "usdc", "dna"))
    mention_rewards = any(token in normalized_input for token in ("earn", "earned", "reward", "share", "hive", "task"))
    mention_receipts = any(token in normalized_input for token in ("receipt", "receipts", "ledger", "payout", "payouts"))
    provider_score = float(getattr(scoreboard, "provider", 0.0) or 0.0)
    validator_score = float(getattr(scoreboard, "validator", 0.0) or 0.0)
    trust_score = float(getattr(scoreboard, "trust", 0.0) or 0.0)
    glory_score = float(getattr(scoreboard, "glory_score", 0.0) or 0.0)
    tier_label = str(getattr(scoreboard, "tier", "Newcomer") or "Newcomer")

    parts = [
        f"You currently have {ledger.balance:.2f} compute credits.",
        (
            f"Provider score {provider_score:.1f}, validator score {validator_score:.1f}, "
            f"trust {trust_score:.1f}, glory score {glory_score:.1f}, tier {tier_label}."
        ),
    ]
    if wallet_status is None:
        if mention_wallet:
            parts.append("DNA wallet is not configured on this runtime yet.")
    else:
        parts.append(
            f"DNA wallet: hot {wallet_status.hot_balance_usdc:.2f} USDC, cold {wallet_status.cold_balance_usdc:.2f} USDC."
        )
    if mention_rewards or "credit" in normalized_input:
        parts.append(
            "Plain public Hive posts do not mint credits by themselves. Credits and provider score come from rewarded assist tasks and accepted results."
        )
    if mention_receipts:
        entries = list_credit_ledger_entries(peer_id, limit=4)
        if not entries:
            parts.append("No credit receipts are recorded for this peer yet.")
        else:
            receipt_lines = ["Recent credit receipts:"]
            for entry in entries:
                amount = float(entry.get("amount") or 0.0)
                sign = "+" if amount >= 0 else ""
                receipt_id = str(entry.get("receipt_id") or "").strip() or "no-receipt"
                reason = str(entry.get("reason") or "").strip() or "unknown"
                timestamp = str(entry.get("timestamp") or "").strip() or "unknown time"
                receipt_lines.append(
                    f"- {sign}{amount:.2f} for `{reason}` ({receipt_id[:24]}) at {timestamp}."
                )
            parts.append("\n".join(receipt_lines))
    if ledger.mode:
        parts.append(f"Ledger mode is {ledger.mode}.")
    return " ".join(part.strip() for part in parts if part.strip())
