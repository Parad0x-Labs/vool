"""VOOL Auto Local Only — an Auto lane that may never call a cloud model.

There are two Auto modes, and they differ in exactly one way:

* **VOOL Auto** (``vool``) routes local-first and MAY escalate to the user's cloud lane — a
  verified-free OpenRouter model, or a paid one the operator explicitly authorized.
* **VOOL Auto Local Only** (``vool-local-only``) routes automatically among LOCAL models and
  tools. It has no cloud-model arm at all: not a paid one, not a verified-free one, not a "just the
  planner" one, not a model fallback after a local failure. Deterministic tools, web search,
  retrieval, browser tools, filesystem tools and local APIs remain available under their own
  policies.

Both are Auto: neither is a concrete model pin, and both let the runtime choose the lane per turn.
The mode is a *ceiling on where the turn may run*, never an instruction about which local model
answers.

**Why the block is layered rather than a single flag.** A mode that is enforced in one place is
enforced wherever that place is on the path — and the runtime reaches a provider by more than one
path. Selection ranks manifests; the router invokes them; the conductor and the tool planner
dispatch provider calls onto a ``ThreadPoolExecutor``. So the mode is enforced at three independent
model seams, each of which fails closed on its own:

1. **Selection** — :func:`core.model_selection_policy._hard_exclusion_reason` drops every manifest
   whose cost class is not ``free_local``, so no cloud candidate is ever ranked.
2. **Invocation** — :meth:`core.memory_first_router.MemoryFirstRouter._invoke_manifest` refuses a
   non-local manifest before the adapter is built. This is the seam that catches anything reaching
   a provider WITHOUT having gone through ranking: the escalation path, the mux/race path, the
   conductor, the planner.
3. **Model transport** — the adapter and the policy-bound cloud transport refuse a non-local model
   endpoint outright, so even a manifest that lied about its cost class cannot invoke cloud AI.

Web and tool authorization deliberately are not part of this contract. They have separate policy
switches; coupling them to Local Only is both inaccurate and destructive because a deterministic
search or browser plan is not a cloud-model invocation.

**Why the flag rides in the context dict and not in a ContextVar.** The same reason
:mod:`core.turn_model_call_ledger` gives: provider execution crosses a shallow copy of the request
context, and the conductor and planner dispatch onto worker threads where a ContextVar set on the
API thread is simply absent. A mode enforced by a ContextVar would be enforced on the API thread
and silently absent on exactly the workers that make the provider calls. The flag is stamped INTO
``source_context``, so every copy and every worker carries it.

A ContextVar guard exists too (:func:`local_only_egress_scope`), but only as the outermost backstop
for code that has no context dict to consult at all. It can add a refusal; it can never remove one.

When a turn genuinely requires a cloud model and no local model or deterministic tool can perform
it, Local Only may say so explicitly. Live/current data alone is never such a reason: web and
retrieval tools remain eligible. See :data:`REFUSAL_TEXT`.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

#: The composer value for the cloud-capable Auto lane.
AUTO_SELECTOR_VALUE = "vool"
#: The composer value for the local-only Auto lane.
SELECTOR_VALUE = "vool-local-only"

#: Every composer value that means "let the runtime choose", as opposed to a concrete model pin.
#: Call sites that ask "did the operator pin an exact model?" test against THIS set rather than
#: against their own literal, so adding a third Auto lane later cannot leave one of them behind
#: treating it as a model id that does not resolve.
AUTO_SELECTOR_VALUES = frozenset({AUTO_SELECTOR_VALUE, "vool:latest", "auto", SELECTOR_VALUE})

#: The context key carrying the mode into the turn. Deliberately the key
#: `core.agent_runtime.audit_routing._local_only_flagged` already reads, so audit routing honours
#: the composer selection through its existing path rather than through a second one bolted beside
#: it.
CONTEXT_KEY = "local_only"
#: Server-derived provenance: this turn is local-only because the operator SELECTED the Local Only
#: lane, as opposed to the machine being configured local-only globally. Underscore-prefixed, so it
#: is never accepted from an API caller.
SELECTED_KEY = "_auto_local_only_selected"

#: What the runtime says when Local Only cannot answer a turn honestly. One sentence, no hedging,
#: and it names the reason class rather than inventing a partial answer.
REFUSAL_TEXT = (
    "I can't complete this in Local Only mode because it requires a cloud AI/model call and no "
    "local model or deterministic tool can perform it."
)

#: The exclusion reason selection records for a manifest dropped by this mode. Distinct from the
#: pre-existing `not_free_local_in_local_only_mode` so a ranking trace says WHICH local-only rule
#: bound: the machine's global configuration, or the operator's per-turn lane choice.
SELECTION_EXCLUSION_REASON = "not_local_in_auto_local_only_mode"

_EGRESS_BLOCKED: ContextVar[bool] = ContextVar("vool_auto_local_only_egress_blocked", default=False)
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
#: Hostnames that are local by name rather than by address, so the common case needs no resolver.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


class CloudEgressBlockedError(RuntimeError):
    """A call tried to leave this machine while Local Only was in force.

    Raised at the transport seams. It is an ERROR rather than a silent downgrade on purpose: a
    blocked cloud call must not look to the caller like a cloud call that returned nothing, because
    the two lead to different honest answers.
    """


def is_local_only_selection(value: Any) -> bool:
    """Whether a composer selection names the Local Only Auto lane."""
    return str(value or "").strip().lower() == SELECTOR_VALUE


def is_auto_selection(value: Any) -> bool:
    """Whether a composer selection is an Auto lane rather than a concrete model pin."""
    return str(value or "").strip().lower() in AUTO_SELECTOR_VALUES


def bind_turn(source_context: dict[str, Any] | None, requested_model: Any = None) -> bool:
    """Stamp the mode onto this turn's context. Returns whether Local Only is in force.

    Called once at the front door, with the composer selection. The stamp is what crosses the
    thread-pool hop, so every downstream seam reads the same verdict without re-deriving it from a
    selection string that only the front door ever saw.

    A machine configured globally local-only also binds here, so the downstream seams have one
    question to ask instead of two — but ``SELECTED_KEY`` is set only for the composer lane, which
    is what lets the footer distinguish "you chose this" from "this machine is like this".
    """
    if not isinstance(source_context, dict):
        return False
    selected = is_local_only_selection(
        requested_model if requested_model is not None else source_context.get("requested_model")
    )
    if selected:
        source_context[CONTEXT_KEY] = True
        source_context[SELECTED_KEY] = True
        return True
    return bool(turn_is_local_only(source_context))


def turn_is_local_only(source_context: dict[str, Any] | None) -> bool:
    """Whether this turn may not invoke a cloud model.

    A union, never a single source: the stamped context flag, the composer selection if the stamp
    has not happened yet, the ambient egress scope, and the machine's global local-only policy. Any
    one of them binds. Adding a source can only make a turn stricter.
    """
    context = source_context if isinstance(source_context, dict) else {}
    for key in (CONTEXT_KEY, SELECTED_KEY, "local_only_mode", "cloud_disabled"):
        if bool(context.get(key)):
            return True
    if is_local_only_selection(context.get("requested_model")):
        return True
    if _EGRESS_BLOCKED.get():
        return True
    try:
        from core.policy_engine import local_only_mode

        return bool(local_only_mode())
    except Exception:
        # Only the MACHINE-WIDE setting is being read here; every per-turn source of the mode was
        # already consulted above and none of them can reach this line. So an unreadable policy file
        # cannot leak a turn the operator put in Local Only — it can only fail to notice a globally
        # local-only machine, which is the same thing every other reader of that setting does.
        # Returning True instead would put the whole runtime in Local Only over a transient config
        # read, which is a much larger failure than the one it would prevent.
        return False


def turn_selected_local_only(source_context: dict[str, Any] | None) -> bool:
    """Whether the operator chose the Local Only lane for this turn, specifically.

    Distinct from :func:`turn_is_local_only`, which is also True on a machine configured local-only
    globally. The footer uses this to say "you selected Local Only" only when that is what happened.
    """
    context = source_context if isinstance(source_context, dict) else {}
    return bool(context.get(SELECTED_KEY)) or is_local_only_selection(context.get("requested_model"))


def manifest_is_local(manifest: Any) -> bool:
    """Whether a provider manifest runs on this machine.

    Decided by the SAME cost classifier routing uses, so a manifest cannot be local here and cloud
    there. Anything the classifier cannot place is not local: an unknown lane is treated as remote,
    because the failure that matters is admitting a cloud call, not rejecting a local one.
    """
    try:
        from core.model_selection_policy import provider_cost_class

        return provider_cost_class(manifest) == "free_local"
    except Exception:
        return False


def endpoint_is_local(url: Any) -> bool:
    """Whether a URL addresses this machine (or the private network), not the public internet.

    The inverse of the public-address test :mod:`core.cloud_transport` already applies to cloud
    endpoints: loopback, private, link-local, CGNAT and unspecified addresses are this side of the
    boundary; a globally routable address is not. A LAN Ollama box is therefore still local, which
    is the honest reading of "no cloud" — the mode exists to keep turns off third-party providers,
    not to forbid the operator's own hardware.

    A name that does not resolve is NOT local. Unresolvable is unknown, and unknown fails closed.
    """
    host = str(urlparse(str(url or "")).hostname or "").strip().lower().strip("[]")
    if not host:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return _address_is_internal(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        return False
    addresses = [str(info[4][0]) for info in infos if len(info) > 4 and info[4]]
    if not addresses:
        return False
    # EVERY resolved address must be internal. A name that resolves to both a loopback and a public
    # address is a name that can reach the public internet.
    for address in addresses:
        try:
            if not _address_is_internal(ipaddress.ip_address(address)):
                return False
        except ValueError:
            return False
    return True


def _address_is_internal(address: Any) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv4Address) and address in _CGNAT_V4)
    )


def assert_endpoint_allowed(url: Any, *, lane: str = "", source_context: dict[str, Any] | None = None) -> None:
    """Refuse a model-provider call to a non-local endpoint while Local Only is in force.

    The last seam before bytes go on the wire. It consults the endpoint, not the manifest's own
    claim about itself, so a mislabelled lane is still blocked.
    """
    if not turn_is_local_only(source_context):
        return
    if endpoint_is_local(url):
        return
    raise CloudEgressBlockedError(
        f"auto_local_only_blocked_cloud_egress:{str(lane or 'unknown').strip() or 'unknown'}"
    )


def assert_manifest_allowed(manifest: Any, *, source_context: dict[str, Any] | None = None) -> None:
    """Refuse to invoke a non-local manifest while Local Only is in force."""
    if not turn_is_local_only(source_context):
        return
    if manifest_is_local(manifest):
        return
    raise CloudEgressBlockedError(
        "auto_local_only_blocked_cloud_manifest:"
        f"{getattr(manifest, 'provider_id', '') or 'unknown'!s}"
    )


def cloud_lane_permitted(source_context: dict[str, Any] | None = None) -> bool:
    """Whether ANY cloud arm may be considered this turn — paid, verified-free, or planner-only.

    One question with one answer, so the free lane and the paid lane cannot diverge. "Free" is a
    price, not a location: a verified-free OpenRouter call still leaves the machine, so Local Only
    blocks it for the same reason it blocks a paid one.
    """
    return not turn_is_local_only(source_context)


@contextmanager
def local_only_egress_scope(active: bool = True) -> Iterator[None]:
    """Ambient backstop for code with no context dict to consult.

    Strictly additive: inside the scope more calls are refused, never fewer. It does not replace the
    stamped flag, because a ContextVar does not survive the thread-pool hop the provider calls take
    — see this module's header.
    """
    token = _EGRESS_BLOCKED.set(bool(active) or _EGRESS_BLOCKED.get())
    try:
        yield
    finally:
        _EGRESS_BLOCKED.reset(token)


def refusal_result(reason: str = "", *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The turn result for work that cannot proceed without a cloud model.

    A refusal is a first-class answer with its own source label, not an error and not an empty
    response: the caller records it, the footer names it, and the operator can act on it by
    switching lanes.
    """
    return {
        "response": REFUSAL_TEXT,
        "confidence": 1.0,
        "deterministic": True,
        "source": "auto_local_only_refusal",
        "local_only": True,
        "local_only_selected": turn_selected_local_only(source_context),
        "refusal_reason": str(reason or "needs_cloud_model").strip(),
    }


__all__ = [
    "AUTO_SELECTOR_VALUE",
    "AUTO_SELECTOR_VALUES",
    "CONTEXT_KEY",
    "REFUSAL_TEXT",
    "SELECTED_KEY",
    "SELECTION_EXCLUSION_REASON",
    "SELECTOR_VALUE",
    "CloudEgressBlockedError",
    "assert_endpoint_allowed",
    "assert_manifest_allowed",
    "bind_turn",
    "cloud_lane_permitted",
    "endpoint_is_local",
    "is_auto_selection",
    "is_local_only_selection",
    "local_only_egress_scope",
    "manifest_is_local",
    "refusal_result",
    "turn_is_local_only",
    "turn_selected_local_only",
]
