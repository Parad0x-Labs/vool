"""
core/request_trust.py
=====================
Server-side "owner-local" trust signal for privileged runtime actions.

The runtime's ``/api/chat`` HTTP endpoint is unauthenticated and binds loopback by
default. The only place a caller can forge a trust label is the HTTP request body:
channels deliver a remote user's plain text (the adapter builds ``source_context`` in
trusted server code), and the CLI runs in-process, so neither crosses a forgery boundary.
An HTTP body, by contrast, can claim any ``surface`` ("openclaw", "cli", ...) or set any
field it likes.

So privileged, spend- or shutdown-touching gates must NOT trust the caller-supplied
``surface``. Instead the API dispatcher stamps a server-controlled ``_owner_local`` flag on
``source_context`` from the real TCP peer address (loopback == the machine owner's own
session) after stripping any caller-supplied copy, and every privileged gate consults THIS
flag via :func:`request_is_owner_local`.

Honest limit: on the default loopback bind, *any* local process is treated as the owner —
that is the accepted trust boundary for a local-first, single-user runtime, the same
boundary any local daemon draws. What this closes is the *remote* forgery path: a caller on
a non-loopback peer (an exposed ``--bind 0.0.0.0`` API, or a channel message) can no longer
present itself as owner-local by setting a body field.
"""
from __future__ import annotations

import ipaddress
from typing import Any

# Server-stamped key: True only when the request provably came from the owner's local
# session. Callers must never set it over the wire — the dispatcher strips and re-stamps it.
OWNER_LOCAL_KEY = "_owner_local"

# Server-stamped key: True on every turn that arrived through the HTTP service (core.web.api.runtime
# stamps it on each served turn). On such a turn an absent or unknown owner-local state is a remote
# turn: only an explicit ``_owner_local: True`` (a loopback peer) is the owner.
HTTP_INGRESS_KEY = "_http_ingress"

# source_context keys a caller must never supply; the server owns them. Stripped from every
# inbound body before the trusted values are stamped. "authorized_paid_call" is a server-built paid
# reservation (see ModelOrchestrator.authorize_paid) and participates in the spend decision, so a
# caller-supplied one is stripped here rather than relying on downstream structural checks to reject it.
RESERVED_TRUST_KEYS = (
    OWNER_LOCAL_KEY,
    HTTP_INGRESS_KEY,
    # M2: the typed turn contract is SERVER-authored at the run_once intake. An inbound
    # body carrying this key is stripped here; only core.turn_contract writes it.
    "turn_request",
    "turn_state",
    "turn_proposals",
    # The turn's RequestGraph projection is written by the turn door (core.semantic.turn_graph);
    # a caller-supplied one would let a client label its own slot set onto the receipt.
    "request_graph",
    "_semantic_shadow",
    # The root-cause diagnosis contract is core-authored (planner/finalizer
    # seams in core.root_cause_contract); a caller-supplied one would let a
    # client forge "Root cause repaired" status onto any turn.
    "root_cause_contract",
    "cloud_escalation_approved",
    "autopilot_allow_heavy_model",
    "authorized_paid_call",
    # "Use the model's own tools" turn stamps (core.model_own_tools.STAMP_KEYS): server-written
    # at run_once; a caller copy would claim a provider search the owner's switch never granted.
    "model_own_tools_turn",
    "model_own_tools_search",
    "model_own_tools_search_receipt",
    "model_own_tools_search_admission",
    # The operator's Authorize Push is SERVER-stamped by the owner-local operator surface.
    # A request body carrying it would let a model author its own consent to move a remote ref.
    "repo_push_authorization",
    # The operator's forge-action authorization (draft PR create/update, comment) is minted the
    # same way, by the same owner-local surface. Reserved for the identical reason: a turn must
    # not be able to write its own consent to put content on a forge.
    "repo_forge_action_authorization",
    # Server-authored, redacted receipts for the bounded paid planner/conductor scope.  They do
    # not grant authority, but accepting caller-written rows would make the returned trace lie.
    "pinned_paid_helper_receipts",
    # Joined into model.call_* evidence by the provider boundary.  A caller must not be able to
    # label an ordinary answer as a bounded conductor generation.
    "model_call_role",
    # core.user_rules: the rules lane marks the turn it answered (the reply guard leaves that
    # answer unmasked), and the secret intake marks a remainder it carved a credential out of.
    # A caller-written marker would let a client switch the reply guard off.
    "_user_rules_command",
    "_secret_intake_consumed",
    "access_policy",
    "context_access_policy",
    "_context_access_policy",
    "context_grants",
    "grant_confirmed_profile",
    "trusted_private_local",
    "allow_project_context",
    "allow_user_profile_context",
    "allow_action_receipts",
    "allow_shared_context",
    "allow_cross_chat",
    "allow_archived_context",
    "allow_cold_context",
    "chat_archived",
    "chat_id",
    "project_id",
    "_trusted_project_id",
    # The concrete further calls one model reply asked for. The tool loop writes it from the
    # provider's own batch and the permission controller turns it into a bounded batch approval, so
    # a caller-supplied one would be a caller choosing what a single click authorizes.
    "pending_batch_calls",
    # A9 P0: the turn's routing plan is minted server-side BEFORE model spend (core.turn_routing)
    # and enforced at the broker seam. A caller-supplied one would be a client choosing its own
    # provider fences — exactly what the paid/locality ceilings exist to prevent.
    "turn_routing_plan",
    "turn_routing_retry",
    # VOOL School: the server-stamped school principal + resolved policy for a
    # school session. Stamped ONLY by the /api/chat ingress gate after the
    # signed session token verifies; a caller-supplied copy is stripped here.
    "school_policy",
)

# In-process surfaces that identify the machine owner's own session. Only consulted as a
# fallback for callers that never crossed the HTTP boundary (the dispatcher always stamps
# ``_owner_local`` for HTTP requests, so this fallback is reached only in-process, where the
# surface is set by trusted local code and cannot be forged).
_OWNER_SURFACES = frozenset({"cli", "desktop", "local", ""})

def is_loopback_host(host: str) -> bool:
    """True when ``host`` is a loopback address (the machine owner's own session).

    ``host`` is a socket peer address. It is parsed as an IP address (IPv4, IPv6, an IPv6 zone or
    brackets, and an IPv4-mapped IPv6 address unwrapped to its IPv4 form), never matched by string
    prefix: ``127.evil.com`` or ``127.0.0.1.example`` is a name, not a loopback address, and fails.
    The one name accepted is ``localhost`` itself, which some in-process callers pass; a real
    socket peer is always an address, so no remote caller can present it.

    Empty/unknown -> False (fail closed): a request whose peer we cannot confirm as loopback
    is not treated as owner-local.
    """
    h = str(host or "").strip().lower()
    if not h:
        return False
    if h == "localhost":
        return True
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(h)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_loopback)


#: The peer a trusted in-process caller names when it forwards a request on the owner's behalf
#: AFTER doing its own authentication (for example the mobile companion, once a paired phone's
#: grant and device signature have both verified). Nothing on the wire can produce it: the
#: servers always pass the real socket peer.
IN_PROCESS_OWNER_PEER = "127.0.0.1"



def in_process_owner_extra() -> dict[str, str]:
    """``ExecutionContext.extra`` for a command the owner runs in their own process (the ``vool``
    terminal commands, the blackbox CLI). The call site names the owner explicitly; nothing infers
    it from a projection or from a missing peer."""

    return {"client_host": IN_PROCESS_OWNER_PEER}


def in_process_owner_source_context() -> dict[str, bool]:
    """The owner-local stamp an in-process turn (the terminal chat, the standing coordinator) carries
    explicitly. Its source context never comes from a request body, so no caller can forge it."""

    return {OWNER_LOCAL_KEY: True}


def owner_peer_for_turn(source_context: Any) -> str:
    """The peer a command run on behalf of a turn names: the owner's only when the turn carries the
    explicit owner-local stamp, otherwise ``""`` (unknown, which owner-local checks refuse)."""

    sc = source_context if isinstance(source_context, dict) else {}
    return IN_PROCESS_OWNER_PEER if sc.get(OWNER_LOCAL_KEY) is True else ""


def execution_peer_host(ctx: Any) -> str:
    """The transport peer of one command-registry execution, or ``""`` when it is unknown.

    Every projection (api, cli, model, chat, core) names its peer in ``extra["client_host"]``: the
    servers pass the real socket peer, in-process owner callers pass :func:`in_process_owner_extra`.
    A missing peer is unknown, and owner-local checks refuse it.
    """

    extra = getattr(ctx, "extra", None) or {}
    return str(extra.get("client_host") or "")


# Registry capabilities that change the owner's own settings, keys or providers. A command that
# declares one runs only for the owner's own session (a loopback peer), whatever its route or
# its own permission gate says, so a command added later with the same capability is covered
# without a route of its own being gated.
OWNER_LOCAL_CAPABILITIES = frozenset({"change_settings", "change_provider_configuration"})
_OWNER_LOCAL_CAPABILITY_PREFIXES = ("settings.",)


def capability_requires_owner_local(capability: str) -> bool:
    cap = str(capability or "").strip()
    return cap in OWNER_LOCAL_CAPABILITIES or cap.startswith(_OWNER_LOCAL_CAPABILITY_PREFIXES)


# What a turn that is NOT the owner's own session may still run: a paired phone, a channel, or any
# other device on the network "can chat and read; they can never inherit owner-gated privileges"
# (core.channel_gateway). Everything else -- files and folders on this computer, notes, calendar,
# mail, contacts, the profile, media projects, the browser profile, repositories, skills, the
# wallet, the owner's X account, and every lane that spends the owner's provider keys or money --
# is the owner's own. Fail closed: an intent is the owner's unless it is listed here.
REMOTE_TURN_TOOL_INTENTS = frozenset({
    "respond.direct",
    "capability.expand_family",
    # One public page, unkeyed: core.remote_turn_fetch refuses this computer and its local network
    # and checks every redirect hop. It uses no key and no paid provider.
    "web.fetch",
    # NOT here, each because it would spend the owner's keys, money or network position:
    # web.search / web.research (keyed search providers), web.chain_lookup and token.* (chain-data
    # API keys), marketplace.search_listings and sell.quote (paid marketplace and quote services),
    # pay.* (payments), and browser.render (a browser follows redirects and loads sub-resources that
    # cannot be pinned to checked public addresses, so it could reach the owner's network).
})


#: What the person on the other device reads when a tool is refused for that reason.
REMOTE_TURN_REFUSAL_TEXT = (
    "I can't do that from another device. Opening files, using your accounts or changing anything "
    "on this computer only works in VOOL on the computer itself."
)


def turn_is_remote(source_context: Any) -> bool:
    """Whether this turn is NOT the owner's own session, failing closed.

    A stamped owner-local state is authoritative, and only an explicit ``True`` is the owner. A turn
    that came through the HTTP service with no stamp (or any value other than ``True``) is remote.
    A context with neither key was built in-process and never crossed the HTTP boundary.
    """

    sc = source_context if isinstance(source_context, dict) else {}
    if OWNER_LOCAL_KEY in sc:
        return sc.get(OWNER_LOCAL_KEY) is not True
    return bool(sc.get(HTTP_INGRESS_KEY))


def remote_turn_tool_refusal(intent: str, source_context: Any) -> str:
    """``""`` when this tool may run for this turn; otherwise why it may not (see turn_is_remote)."""

    if not turn_is_remote(source_context):
        return ""
    if str(intent or "").strip() in REMOTE_TURN_TOOL_INTENTS:
        return ""
    return "owner_local_required"


def strip_reserved_trust_keys(source_context: Any) -> dict[str, Any]:
    """Return a copy of ``source_context`` with every caller-forgeable trust key removed."""
    sc = dict(source_context) if isinstance(source_context, dict) else {}
    for key in RESERVED_TRUST_KEYS:
        sc.pop(key, None)
    return sc


def request_is_owner_local(source_context: Any) -> bool:
    """Whether this request is from the machine owner's own local session.

    Prefers the server-stamped ``_owner_local`` (authoritative for HTTP requests). When that
    key is absent the request never crossed the HTTP forgery boundary, so the in-process
    ``surface`` is trustworthy and used as the fallback.
    """
    sc = source_context if isinstance(source_context, dict) else {}
    if OWNER_LOCAL_KEY in sc:
        return bool(sc.get(OWNER_LOCAL_KEY))
    if sc.get(HTTP_INGRESS_KEY):
        return False
    surface = str(sc.get("surface") or "").strip().lower()
    return surface in _OWNER_SURFACES


#: Who wrote this turn's user message. A turn an agent sends (an agent team's agent session) carries
#: ``"agent"``: the words are an agent's brief, not the owner speaking, so they never become the owner's memory.
#: Deny-only: any value other than ``"owner"`` or absent counts as not the owner, and claiming ``"owner"``
#: grants nothing (owner-local trust stays the server-stamped flag).
TURN_AUTHOR_KEY = "turn_author"


def turn_author_from_request(body: Any, inbound_source_context: Any = None) -> str:
    """The turn author a request declares, normalised: ``"owner"`` unless the body says otherwise."""

    values = []
    for carrier in (body, inbound_source_context):
        if isinstance(carrier, dict):
            values.append(str(carrier.get(TURN_AUTHOR_KEY) or "").strip().lower())
    declared = [value for value in values if value]
    if any(value != "owner" for value in declared):
        return next(value for value in declared if value != "owner")[:40]
    return "owner"


def turn_is_owner_authored(source_context: Any) -> bool:
    """Whether this turn's user message is the owner's own words (and may become the owner's memory)."""

    sc = source_context if isinstance(source_context, dict) else {}
    return str(sc.get(TURN_AUTHOR_KEY) or "owner").strip().lower() == "owner"


__all__ = [
    "HTTP_INGRESS_KEY",
    "IN_PROCESS_OWNER_PEER",
    "OWNER_LOCAL_CAPABILITIES",
    "OWNER_LOCAL_KEY",
    "REMOTE_TURN_REFUSAL_TEXT",
    "REMOTE_TURN_TOOL_INTENTS",
    "RESERVED_TRUST_KEYS",
    "TURN_AUTHOR_KEY",
    "capability_requires_owner_local",
    "execution_peer_host",
    "in_process_owner_extra",
    "in_process_owner_source_context",
    "is_loopback_host",
    "owner_peer_for_turn",
    "remote_turn_tool_refusal",
    "request_is_owner_local",
    "strip_reserved_trust_keys",
    "turn_author_from_request",
    "turn_is_owner_authored",
    "turn_is_remote",
]
