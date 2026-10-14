"""Use the model's own tools: one switch that hands a pinned cloud model its provider's tools.

Off by default. While it is on, a turn that runs on a cloud model the owner picked:

* is offered none of VOOL's tools (no tool loop, no VOOL web research or adaptive research), and
* gets its provider's own tools only where the provider really offers them on the API VOOL calls.
  On such a turn VOOL's live-data, price and conductor lanes also step aside, since the model
  searches for itself. For a model whose provider offers no tool, those quick fact lanes keep
  answering: nothing else would fetch a live value, and they never hand the model a tool.

What "really offers" means is a fixed table below, not a guess. Today the only entry is
OpenRouter's ``openrouter:web_search`` server tool on its chat-completions endpoint: OpenRouter runs
the search at its side and returns the answer with ``url_citation`` annotations. The other
providers VOOL reaches through their OpenAI-compatible endpoints document no server-side tool
there (Anthropic's compatibility layer carries function tools only; Gemini's compatibility endpoint
refuses ``google_search``; Groq's built-in tools come with its compound models, whose tool fees
the provider does not report back as a cost; Moonshot's ``$web_search`` needs a client round trip
and is flagged by its vendor as not for production), so for them the switch only turns VOOL's
tools off.

Money and privacy rules this module keeps:

* The native set is an allowlist of search tools. Nothing that pays, signs or moves money is ever
  in it, so wallet and payment actions keep going through VOOL's wallet, caps, approval and
  receipts (the wallet's own chat commands are VOOL features, not model tools, and are untouched).
* The search runs only where VOOL itself would search: the turn needs current information, the
  owner did not close it to tools or the network, and VOOL's retrieval door opened a receipt for
  it before the answer was written. One answering call per turn may carry it.
* A provider tool that costs money is attached only to a call that already holds VOOL's paid-call
  reservation (``authorized_paid_call``). A verified-free model never gets one: a web search on a
  free model would bill the owner's provider credits outside the caps.
* The search engine is pinned to one with a published per-request price, the number of searches
  per call is capped, the reservation holds that whole fee, and settlement and the cost shown
  always include it (``searched_call_cost_estimate``), whatever the provider's total says.
* Models that run unpriced tools of their own on every call are never sent to
  (``unpriced_builtin_tools``), with the switch on or off.
* Only the owner, from their own machine, may switch it; a stored value other than JSON true reads
  as OFF, which keeps VOOL's tools on: the safe default.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from core.model_pricing import UNPRICED_BUILTIN_TOOLS_REFUSAL, unpriced_builtin_tools

PREF_KEY = "model_own_tools"
# Server-stamped onto the turn's source_context by the turn door. Readers treat it as a NARROWING
# signal only (skip VOOL's tool loop); nothing that spends money trusts it.
TURN_CONTEXT_KEY = "model_own_tools_turn"
# Server-stamped beside TURN_CONTEXT_KEY when the pinned model's provider runs its own web search on
# a paid call. VOOL's live-data lanes (prices, rates, weather) then step aside for the model.
TURN_SEARCH_CONTEXT_KEY = "model_own_tools_search"
#: The provider search's retrieval receipt, opened through the canonical door before synthesis.
PROVIDER_SEARCH_RECEIPT_KEY = "model_own_tools_search_receipt"
#: Admits the provider search to ONE answering call, once its receipt is open. A shared token, so
#: every copy of the turn's context sees the call that used it.
PROVIDER_SEARCH_ADMISSION_KEY = "model_own_tools_search_admission"
#: Every key this module stamps; only the server writes them (see `stamp_turn`).
STAMP_KEYS = (
    "model_own_tools_turn",
    "model_own_tools_search",
    PROVIDER_SEARCH_RECEIPT_KEY,
    PROVIDER_SEARCH_ADMISSION_KEY,
)
# Stamped by the router onto ``ModelRequest.metadata`` for a call that may carry provider tools.
REQUEST_METADATA_KEY = "model_own_tools"

OPENROUTER_WEB_SEARCH = "openrouter:web_search"
SEARCH_MAX_RESULTS = 5
# The search engine is PINNED so the fee has a published price. OpenRouter's default ("auto")
# hands some models to their own provider's native search, billed at that provider's rate, which
# VOOL cannot know in advance; Exa is billed per request to the OpenRouter account (OpenRouter's
# web search docs, read 2026-10-13: $0.005-$0.007 per request, up to 10 results included).
SEARCH_ENGINE = "exa"
# How many searches one call may run, so the fee has a ceiling.
SEARCH_MAX_USES = 2
# Each search is counted at this bound, above both published Exa figures.
SEARCH_USD_PER_REQUEST_BOUND = 0.01
# What one call's provider search may add, held in the reservation on top of the token estimate.
SEARCH_ALLOWANCE_USD = SEARCH_MAX_USES * SEARCH_USD_PER_REQUEST_BOUND

# Every provider tool type VOOL may ever attach. Search only: the adapter refuses anything else.
ALLOWED_PROVIDER_TOOL_TYPES = frozenset({OPENROUTER_WEB_SEARCH})


def mode_on() -> bool:
    """The owner's saved switch. Any failure to read it answers OFF (VOOL's tools stay on)."""
    try:
        from core.user_preferences import load_preferences

        return bool(getattr(load_preferences(), PREF_KEY, False))
    except Exception:
        return False


def set_mode(on: bool) -> bool:
    """Save the switch and read it back. Returns the value that is actually stored."""
    from core.user_preferences import load_preferences, save_preferences

    prefs = load_preferences()
    before = getattr(prefs, PREF_KEY, False) is True
    setattr(prefs, PREF_KEY, bool(on))
    save_preferences(prefs)
    stored = mode_on()
    if stored != before:
        # A privacy- and spend-relevant switch leaves a record of every change.
        try:
            from core import audit_logger

            audit_logger.log(
                "model_own_tools_changed",
                target_id=PREF_KEY,
                target_type="preference",
                details={"on": stored},
                actor="owner",
            )
        except Exception:
            pass
    return stored


def _is_local_manifest(manifest: Any) -> bool:
    """Whether a manifest is a model on this machine. The cost class is the authority (the same one
    the paid-call gates read): a paid cloud lane is cloud wherever its base URL points."""
    from core.model_selection_policy import provider_cost_class

    try:
        cost_class = provider_cost_class(manifest)
    except Exception:
        return True
    if cost_class == "paid_cloud":
        return False
    if cost_class == "free_local":
        return True
    metadata = dict(getattr(manifest, "metadata", None) or {})
    if str(metadata.get("deployment_class") or "").strip().lower() == "local":
        return True
    base_url = str(dict(getattr(manifest, "runtime_config", None) or {}).get("base_url") or "").strip().lower()
    host = str(urlparse(base_url).hostname or "").lower()
    return not base_url or host in {"127.0.0.1", "localhost", "::1"}


def is_openrouter_manifest(manifest: Any) -> bool:
    provider_name = str(getattr(manifest, "provider_name", "") or "").strip().lower()
    base_url = str(dict(getattr(manifest, "runtime_config", None) or {}).get("base_url") or "").strip()
    host = str(urlparse(base_url).hostname or "").strip().lower()
    return provider_name == "openrouter-byok" or host == "openrouter.ai"


def provider_tools_for(manifest: Any) -> tuple[dict[str, Any], ...]:
    """The provider-side tools this model's provider really offers on VOOL's endpoint, or ()."""
    if manifest is None or _is_local_manifest(manifest):
        return ()
    if is_openrouter_manifest(manifest):
        return (
            {
                "type": OPENROUTER_WEB_SEARCH,
                "parameters": {
                    "engine": SEARCH_ENGINE,
                    "max_results": SEARCH_MAX_RESULTS,
                    "max_uses": SEARCH_MAX_USES,
                },
            },
        )
    return ()


def _explicit_pin(source_context: dict[str, Any] | None) -> str:
    from core.auto_local_only_mode import is_auto_selection

    context = dict(source_context or {})
    requested = str(context.get("requested_model") or "").strip()
    if not requested or is_auto_selection(requested):
        return ""
    # Auto's per-chat stickiness is not an owner pick (MF-22).
    if str(context.get("model_selection") or "").strip().lower() == "sticky":
        return ""
    return requested


def pinned_cloud_manifest(
    source_context: dict[str, Any] | None,
    *,
    resolve_manifest: Callable[[dict[str, Any] | None], Any],
) -> Any | None:
    """The cloud manifest the owner pinned for this turn, or None (Auto, local, unresolved)."""
    if not _explicit_pin(source_context):
        return None
    try:
        manifest = resolve_manifest(source_context)
    except Exception:
        return None
    if manifest is None or _is_local_manifest(manifest):
        return None
    return manifest


def turn_uses_model_own_tools(
    source_context: dict[str, Any] | None,
    *,
    resolve_manifest: Callable[[dict[str, Any] | None], Any],
) -> bool:
    """True when the switch is on and this turn runs on a cloud model the owner pinned."""
    if not mode_on():
        return False
    return pinned_cloud_manifest(source_context, resolve_manifest=resolve_manifest) is not None


def stamp_turn(
    source_context: dict[str, Any],
    *,
    resolve_manifest: Callable[[dict[str, Any] | None], Any],
) -> bool:
    """Server-side: write this turn's own-tools stamps onto ``source_context`` (dropping any copy
    a caller sent). Returns whether the turn uses the model's own tools."""
    for key in STAMP_KEYS:
        source_context.pop(key, None)
    if not mode_on():
        return False
    manifest = pinned_cloud_manifest(source_context, resolve_manifest=resolve_manifest)
    if manifest is None:
        return False
    source_context[TURN_CONTEXT_KEY] = True
    if provider_tools_for(manifest) and not _is_verified_free(manifest):
        source_context[TURN_SEARCH_CONTEXT_KEY] = True
    return True


def _is_verified_free(manifest: Any) -> bool:
    try:
        from core.model_selection_policy import is_verified_free_cloud_manifest

        return bool(is_verified_free_cloud_manifest(manifest))
    except Exception:
        # Unknown price: treat as free so no paid search is promised for it.
        return True


def turn_stamped(source_context: dict[str, Any] | None) -> bool:
    return bool((source_context or {}).get(TURN_CONTEXT_KEY))


def turn_has_provider_search(source_context: dict[str, Any] | None) -> bool:
    return turn_stamped(source_context) and bool((source_context or {}).get(TURN_SEARCH_CONTEXT_KEY))


def withdraw_provider_search(*contexts: dict[str, Any] | None) -> None:
    """The turn may not search (the door refused, or the owner closed the turn to tools or the
    network): the call goes without the provider's search."""
    for context in contexts:
        if isinstance(context, dict):
            context.pop(TURN_SEARCH_CONTEXT_KEY, None)
            context.pop(PROVIDER_SEARCH_RECEIPT_KEY, None)
            context.pop(PROVIDER_SEARCH_ADMISSION_KEY, None)


def admit_provider_search(*contexts: dict[str, Any] | None) -> None:
    """The receipt is open: admit the provider search to the turn's answering call (one call)."""
    admission = {"used": False}
    for context in contexts:
        if isinstance(context, dict):
            context[PROVIDER_SEARCH_ADMISSION_KEY] = admission


def search_admitted(source_context: dict[str, Any] | None) -> bool:
    admission = (source_context or {}).get(PROVIDER_SEARCH_ADMISSION_KEY)
    return turn_has_provider_search(source_context) and isinstance(admission, dict) and not admission.get("used")


@dataclass(frozen=True)
class ProviderToolGrant:
    tools: tuple[dict[str, Any], ...]

    def as_metadata(self) -> dict[str, Any]:
        return {"tools": [dict(tool) for tool in self.tools]}


def provider_tool_grant(
    *,
    manifest: Any,
    source_context: dict[str, Any] | None,
    authorized_paid_call: Any,
) -> ProviderToolGrant | None:
    """Provider tools for ONE call, or None.

    Needs a turn the server stamped when the switch was on (the turn keeps the value it started
    with; the switch is not re-read here), a search the retrieval door admitted and nothing has
    used yet, a pinned paid cloud model and a reservation. The first grant uses the admission, so
    any later call in the same turn (a check, a retry) goes without the search.
    """
    if authorized_paid_call is None or not search_admitted(source_context):
        return None
    if not _explicit_pin(source_context) or _is_verified_free(manifest):
        return None
    tools = provider_tools_for(manifest)
    if not tools:
        return None
    source_context[PROVIDER_SEARCH_ADMISSION_KEY]["used"] = True
    return ProviderToolGrant(tools=tools)


def provider_tool_reservation_allowance_usd(manifest: Any, *, source_context: dict[str, Any] | None) -> float:
    """What a paid reservation must hold on top of the tokens for this call's provider tools."""
    if not search_admitted(source_context) or not provider_tools_for(manifest):
        return 0.0
    return SEARCH_ALLOWANCE_USD


def granted_provider_tools(metadata: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Read the router's grant back out of request metadata, refusing anything off the allowlist."""
    grant = dict((metadata or {}).get(REQUEST_METADATA_KEY) or {})
    tools = []
    for tool in list(grant.get("tools") or []):
        if not isinstance(tool, dict):
            raise ValueError("provider tool grant is malformed")
        if str(tool.get("type") or "") not in ALLOWED_PROVIDER_TOOL_TYPES:
            raise ValueError(f"provider tool {tool.get('type')!r} is not on VOOL's allowlist")
        tools.append(dict(tool))
    return tuple(tools)


def citation_sources(message: dict[str, Any] | None) -> list[dict[str, str]]:
    """``{title, url, content}`` from a chat-completions message's ``url_citation`` annotations.

    ``content`` is the text the provider's search returned for that source, when it sent one.
    """
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in list((message or {}).get("annotations") or []):
        if not isinstance(item, dict) or item.get("type") != "url_citation":
            continue
        citation = item.get("url_citation") if isinstance(item.get("url_citation"), dict) else item
        url = str(citation.get("url") or "").strip()
        scheme = urlparse(url).scheme.lower()
        if scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": " ".join(str(citation.get("title") or "").split())[:200],
                "url": url,
                "content": " ".join(str(citation.get("content") or "").split())[:2000],
            }
        )
    return sources


PROVIDER_SEARCH_METADATA_KEY = "provider_search"


def provider_search_metadata(granted: tuple[dict[str, Any], ...], sources: list[dict[str, str]]) -> dict[str, Any]:
    """What an adapter reports about a call that carried the provider's own search: which tools
    rode it and the sources the provider returned (empty when it cited nothing)."""
    if not granted:
        return {}
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(dict(source))
    return {
        PROVIDER_SEARCH_METADATA_KEY: {
            "tools": [str(tool.get("type") or "") for tool in granted],
            "sources": unique[:20],
        }
    }


def _link_text(title: str) -> str:
    """A search result's title as plain link text: it can never open a link of its own."""
    text = re.sub(r"[\[\]()<>`*_]", " ", str(title or ""))
    return " ".join(text.split())[:160] or "source"


def with_sources(text: str, sources: list[dict[str, str]]) -> str:
    """Append the provider's sources the answer does not already link, as a plain list."""
    missing: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "")
        if not url or url in seen or url in str(text or ""):
            continue
        seen.add(url)
        missing.append(source)
    if not missing:
        return text
    lines = [f"- [{_link_text(source.get('title') or source['url'])}]({source['url']})" for source in missing[:10]]
    return f"{str(text or '').rstrip()}\n\nSources used (found by the model's own search):\n" + "\n".join(lines)


def provider_search_fee_usd(usage: Any) -> float:
    """The provider search fee to count for one call: the searches the provider reported, or every
    search the call was allowed when it reported no count (an unreported count is never zero)."""
    count: int | None = None
    try:
        server_tool_use = dict((usage or {}).get("server_tool_use") or {})
        raw = server_tool_use.get("web_search_requests")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
            count = int(raw)
    except Exception:
        count = None
    if count is None:
        count = SEARCH_MAX_USES
    return round(count * SEARCH_USD_PER_REQUEST_BOUND, 8)


def searched_call_cost_estimate(*, provider_id: str, model_id: str, usage: Any) -> dict[str, Any] | None:
    """What a call that carried the provider search cost, for the caps and the cost shown.

    The provider's own reported total counts when it is the larger figure. Otherwise the tokens
    at the model's price plus the search fee count, so a provider total that left the search out
    can never settle below the search. None when the provider reported nothing at all: the
    reservation (which holds the search allowance) then stays in place.
    """
    from core.model_pricing import (
        BASIS_NO_USAGE,
        BASIS_UNKNOWN_CEILING,
        estimate_call_usd,
        settlement_usd,
        usage_tokens,
    )

    base = settlement_usd(provider_id=provider_id, model_id=model_id, usage=usage)
    if base.basis == BASIS_NO_USAGE:
        return None
    prompt_tokens, completion_tokens = usage_tokens(usage)
    # The floor is the tokens at the published input rate plus the search; a cache write is already
    # inside the reported cost, so the reservation's worst-case write surcharge is not added here.
    tokens = estimate_call_usd(
        provider_id=provider_id,
        model_id=model_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        include_cache_write=False,
    )
    fee = provider_search_fee_usd(usage)
    floor = float(tokens.usd) + fee
    estimate = base.as_dict()
    if floor > float(base.usd):
        estimate.update(
            usd=round(floor, 8),
            known=False,
            basis=BASIS_UNKNOWN_CEILING,
            source=f"tokens at the model's price plus the provider search counted at up to ${fee:.4f}",
        )
    else:
        estimate["source"] = f"{base.source} (includes the provider search)"
    return estimate


# The unpriced built-in tools rule lives in core.model_pricing (shipped in 0.7.1); re-exported here.


def close_provider_search_on_failure(source_context: dict[str, Any] | None, failure: BaseException) -> None:
    """The answering call raised: close the open provider-search receipt as failed, never leave it started."""
    if not isinstance(source_context, dict):
        return
    receipt = source_context.pop(PROVIDER_SEARCH_RECEIPT_KEY, None)
    if not isinstance(receipt, dict):
        return
    try:
        from core.retrieval_observability import finish_web_retrieval

        finish_web_retrieval(source_context, receipt, failure=failure)
    except Exception:
        pass


def record_provider_search(source_context: dict[str, Any] | None, model_execution: Any) -> None:
    """Close the provider search's receipt with what the provider's search returned.

    The receipt was opened through the canonical retrieval door BEFORE synthesis (see
    ``ResearchToolLoopFacadeMixin._open_provider_search_receipt``); without one, nothing is
    recorded and nothing the provider cited counts as evidence. What closes it is only what the
    adapter read off the provider's response (``url_citation`` annotations), never the answer's
    wording:

    * the receipt, with one note per returned source (a search that cited nothing, or a call
      that failed, closes it as returning nothing), and
    * one observation per source carrying the text the provider's search returned, which the
      publication gate matches the answer against CLAIM BY CLAIM. A sentence the provider's
      sources do not support is still withheld, exactly as for VOOL's own search.
    """
    if not isinstance(source_context, dict):
        return
    receipt = source_context.pop(PROVIDER_SEARCH_RECEIPT_KEY, None)
    if not isinstance(receipt, dict) or not turn_has_provider_search(source_context):
        return
    from core.retrieval_observability import finish_web_retrieval
    from core.retrieval_provenance import MODEL_PROVIDER_SEARCH

    details = getattr(model_execution, "details", None)
    search = dict((details or {}).get(PROVIDER_SEARCH_METADATA_KEY) or {}) if isinstance(details, dict) else {}
    notes: list[dict[str, Any]] = []
    for source in list(search.get("sources") or []):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if urlparse(url).scheme.lower() not in {"http", "https"}:
            continue
        title = str(source.get("title") or "").strip()
        content = str(source.get("content") or "").strip()
        notes.append(
            {
                "origin_domain": str(urlparse(url).hostname or ""),
                "search_provider": MODEL_PROVIDER_SEARCH,
                "source_type": "provider_search",
                "result_title": title or url,
                "url": url,
                **({"summary": content} if content else {}),
            }
        )
    finish_web_retrieval(source_context, receipt, notes=notes)
    observed = [dict(note, ok=True) for note in notes if note.get("summary")]
    if observed:
        from core.grounding_lifecycle import record_typed_observations

        record_typed_observations(source_context, entries=observed)


# ---- chat command -------------------------------------------------------------------------------

_LEAD = r"^\s*(?:(?:hey|ok|okay)\s+vool[,\s]+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
_TAIL = r"(?:\s+(?:for|in)\s+(?:this|every|all)\s+chats?)?[\s,]*(?:please|now|thanks|thank\s+you)?[\s.!?]*$"
_VOOL = r"vool(?:'s|s)?"
_ON = re.compile(
    _LEAD
    + r"(?:"
    + r"(?:use|switch\s+to|turn\s+on|enable)\s+(?:the\s+)?(?:model'?s|ai'?s|provider'?s|your)\s+(?:own|native|built[-\s]?in)\s+tool(?:s|ing)"
    + r"|let\s+the\s+model\s+use\s+its\s+own\s+tool(?:s|ing)"
    + rf"|(?:turn\s+off|disable|switch\s+off|stop\s+using)\s+{_VOOL}\s+tool(?:s|ing)"
    + rf"|{_VOOL}\s+tool(?:s|ing)\s+off"
    + r"|(?:the\s+)?(?:model'?s|ai'?s)\s+own\s+tool(?:s|ing)\s+on"
    + r"|/tools\s+(?:model|native|own)"
    + r")"
    + _TAIL,
    re.IGNORECASE,
)
_OFF = re.compile(
    _LEAD
    + r"(?:"
    + rf"(?:use|switch\s+(?:back\s+)?to|go\s+back\s+to)\s+{_VOOL}\s+(?:own\s+)?tool(?:s|ing)(?:\s+again)?"
    + rf"|(?:turn|switch)\s+{_VOOL}\s+tool(?:s|ing)\s+(?:back\s+)?on"
    + rf"|(?:turn\s+on|enable|switch\s+on)\s+{_VOOL}\s+tool(?:s|ing)(?:\s+again)?"
    + rf"|{_VOOL}\s+tool(?:s|ing)\s+on"
    + r"|(?:turn\s+off|disable|stop\s+using)\s+(?:the\s+)?(?:model'?s|ai'?s|provider'?s|your)\s+(?:own|native|built[-\s]?in)\s+tool(?:s|ing)"
    + r"|(?:the\s+)?(?:model'?s|ai'?s)\s+own\s+tool(?:s|ing)\s+off"
    + rf"|(?:switch|go)\s+back\s+to\s+{_VOOL}"
    + r"|/tools\s+vool"
    + r")"
    + _TAIL,
    re.IGNORECASE,
)
_STATUS = re.compile(
    _LEAD
    + r"(?:/tools(?:\s+status)?|(?:which|whose|what)\s+tools\s+(?:are\s+you\s+using|are\s+on|do\s+you\s+use))"
    + r"[\s?.!]*$",
    re.IGNORECASE,
)

REPLY_ON = (
    "Done. Cloud models you choose now use their own tools instead of VOOL's, so most will answer "
    "without searching. A few paid ones search the web themselves: that search runs at the "
    "provider, so your question goes to that company, and it counts against your spending limit. "
    "Payments still need your approval. Auto and local models keep VOOL's tools. Say "
    "\"use VOOL's tools\" to switch back."
)
REPLY_OFF = "Done. I'm using VOOL's tools again for every model."
REPLY_ALREADY_ON = "The model's own tools are already on. Say \"use VOOL's tools\" to switch back."
REPLY_ALREADY_OFF = "I'm already using VOOL's tools."
REPLY_STATUS_ON = (
    "The model's own tools are on: cloud models you choose don't get VOOL's tools. Auto and local "
    "models keep them. Say \"use VOOL's tools\" to switch back."
)
REPLY_STATUS_OFF = "VOOL's tools are on for every model. Say \"use the model's own tools\" to change that."
#: The switch is one setting for every chat; a message that names one chat is told so.
REPLY_ALL_CHATS = " This applies to all your chats."
REPLY_NOT_OWNER = (
    "Only you, writing from your own computer, can change which tools the models use, so nothing changed."
)
REPLY_FAILED = "I couldn't save that setting, so nothing changed."

#: The chat replies above by their catalog name: ``own_tools.reply.<name>`` holds each one in
#: every app language. The constants stay the English source (and the fallback when a catalog
#: cannot answer); the case-to-reply mapping in handle_tools_command is unchanged.
_REPLIES: dict[str, str] = {
    "on": REPLY_ON,
    "off": REPLY_OFF,
    "already_on": REPLY_ALREADY_ON,
    "already_off": REPLY_ALREADY_OFF,
    "status_on": REPLY_STATUS_ON,
    "status_off": REPLY_STATUS_OFF,
    "all_chats": REPLY_ALL_CHATS,
    "not_owner": REPLY_NOT_OWNER,
    "failed": REPLY_FAILED,
}


def reply_text(name: str, locale: str = "") -> str:
    """One own-tools chat reply in the app's language (``core.i18n.turn_locale``).

    ``name`` is a key of the reply table (``"on"``, ``"already_on"``, ``"not_owner"``, ...). An
    explicit ``locale`` wins; empty means the language the chat request bound for this turn.
    Unknown, missing or English locales serve the English constant.
    """
    from core.i18n.turn_locale import turn_text

    return turn_text("own_tools.reply." + name, _REPLIES[name], locale)


def parse_tools_command(text: str) -> str:
    """``"on"`` / ``"off"`` / ``"status"`` for a message that is only this command, else ``""``."""
    raw = unicodedata.normalize("NFKC", str(text or ""))
    for apostrophe in ("’", "ʼ", "`", "´", "‘"):
        raw = raw.replace(apostrophe, "'")
    if len(raw) > 120:
        return ""
    if _ON.match(raw):
        return "on"
    if _OFF.match(raw):
        return "off"
    if _STATUS.match(raw):
        return "status"
    return ""


def handle_tools_command(text: str) -> tuple[bool, str]:
    action = parse_tools_command(text)
    if not action:
        return False, ""
    current = mode_on()
    if action == "status":
        return True, reply_text("status_on" if current else "status_off")
    want = action == "on"
    if want == current:
        return True, reply_text("already_on" if want else "already_off")
    try:
        stored = set_mode(want)
    except Exception:
        return True, reply_text("failed")
    if stored != want:
        return True, reply_text("failed")
    reply = reply_text("on" if want else "off")
    if re.search(r"\bthis\s+chat\b", str(text or ""), re.IGNORECASE):
        reply += reply_text("all_chats")
    return True, reply


__all__ = [
    "ALLOWED_PROVIDER_TOOL_TYPES",
    "OPENROUTER_WEB_SEARCH",
    "PREF_KEY",
    "PROVIDER_SEARCH_ADMISSION_KEY",
    "PROVIDER_SEARCH_RECEIPT_KEY",
    "REPLY_NOT_OWNER",
    "REQUEST_METADATA_KEY",
    "SEARCH_ALLOWANCE_USD",
    "SEARCH_ENGINE",
    "SEARCH_MAX_USES",
    "SEARCH_USD_PER_REQUEST_BOUND",
    "STAMP_KEYS",
    "TURN_CONTEXT_KEY",
    "TURN_SEARCH_CONTEXT_KEY",
    "UNPRICED_BUILTIN_TOOLS_REFUSAL",
    "ProviderToolGrant",
    "admit_provider_search",
    "citation_sources",
    "close_provider_search_on_failure",
    "granted_provider_tools",
    "handle_tools_command",
    "mode_on",
    "parse_tools_command",
    "pinned_cloud_manifest",
    "provider_search_fee_usd",
    "provider_search_metadata",
    "provider_tool_grant",
    "provider_tool_reservation_allowance_usd",
    "provider_tools_for",
    "record_provider_search",
    "reply_text",
    "search_admitted",
    "searched_call_cost_estimate",
    "set_mode",
    "stamp_turn",
    "turn_has_provider_search",
    "turn_stamped",
    "turn_uses_model_own_tools",
    "unpriced_builtin_tools",
    "with_sources",
    "withdraw_provider_search",
]
