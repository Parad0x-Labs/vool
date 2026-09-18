"""The immutable, serializable projection a turn's policy is frozen from.

WHY THIS EXISTS. `TurnPolicy` froze its decision context with `copy.deepcopy`.
Deepcopy is a *copier*, not a *projector*: asked for a copy of an object it
cannot reproduce -- a `threading.Lock`, an open connection, a client, a
generator, a ContextVar -- it raises. `TurnPolicy.from_source_context` recorded
that raise as `policy_error`, and every gate reads a non-empty `policy_error` as
a denial. So one unrelated runtime handle riding in the turn's context revoked
the whole turn's network authority, with the reason
``policy construction failed closed: decision_context freeze: TypeError: cannot
pickle '_thread.lock' object``. Reproduced live on 0.5.0: a settings search test
and a chat request for current information both died there, while a separate
un-receipted fallback still returned snippets -- so the product looked like it
had searched, and could not say what had actually happened.

The turn context is a working dict. Lanes stash handles in it. Treating "this
value cannot be pickled" as "this turn may not reach the network" conflates
CARGO with POLICY INPUT, and the two must not share a fate:

* **Policy input** -- a key a gate actually reads. If it cannot be projected,
  the gate would be deciding on a value it cannot see, so construction fails and
  every gate denies. That is the fail-closed behaviour, kept exactly.
* **Cargo** -- everything else. It is projected to an opaque type marker: no
  live reference is retained (a "frozen" policy holding a live handle is a
  window onto mutable state, not a freeze), and no value is invented (an audit
  can tell "no such key" from "a value that could not be carried").

WHAT A PROJECTION IS. A tree of scalars, frozen mappings and tuples. It is
immutable by construction, so a gate cannot write through it into what the next
gate reads. It is serializable, because every leaf is a scalar or a marker
string. It never pickles, never deepcopies, and never touches a runtime object
beyond asking for its type name.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence, Set
from typing import Any

#: Keys a policy layer actually reads out of `source_context`. Derived from the
#: real consults, not guessed:
#:   * `core.mode_permission_policy` -- the mode matrix and its grant lookups;
#:   * `core.request_trust.request_is_owner_local` -- `surface`;
#:   * `core.effect_gateway.TurnPolicy.from_source_context` -- `workspace_root`,
#:     `allow_remote_fetch`;
#:   * `core.remote_fetch_policy.remote_fetch_allowed_by_context` -- `surface`,
#:     `platform`, `allow_remote_fetch`.
#: Erring wide is the safe direction: naming a key that is really cargo makes
#: the gate STRICTER, while omitting a real input would let an unreadable policy
#: value pass as ignorable noise -- a fail-open wearing a projection's name.
POLICY_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "_trusted_project_id",
        "allow_remote_fetch",
        "autonomy_override",
        "bypass_token",
        "cancel_turn_id",
        "client_turn_id",
        "mode_approval_token",
        "operating_mode",
        "operating_mode_revision",
        "platform",
        "project_id",
        "project_permissions",
        "runtime_session_id",
        "session_id",
        "surface",
        "workspace",
        "workspace_root",
    }
)

#: How deep the projection walks before it stops describing and starts marking.
#: A turn context is configuration, not a document tree; anything past this is
#: a data structure that has no business deciding a permission.
MAX_PROJECTION_DEPTH = 12

#: Per-container ceiling. A projection is read by gates and written into audit
#: records; an unbounded one turns a large working list into a large policy.
MAX_PROJECTION_ITEMS = 512

_MARKER_PREFIX = "<unserializable:"
_TRUNCATED = "<truncated>"
_CYCLE = "<cycle>"
_TOO_DEEP = "<too-deep>"


def unserializable_marker(value: Any) -> str:
    """The opaque stand-in for a value the projection cannot carry.

    Names the TYPE and nothing else. A repr could quote a secret (a client
    carrying an API key reprs with the key in it on several libraries), and the
    whole point of the marker is that the value did not travel.
    """
    kind = type(value)
    module = getattr(kind, "__module__", "") or ""
    name = getattr(kind, "__qualname__", None) or getattr(kind, "__name__", "") or "object"
    qualified = f"{module}.{name}" if module and module != "builtins" else name
    return f"{_MARKER_PREFIX}{qualified}>"


def is_unserializable_marker(value: Any) -> bool:
    """Whether a projected leaf stands in for a value that could not be carried."""
    return isinstance(value, str) and value.startswith(_MARKER_PREFIX)


class FrozenMapping(Mapping):
    """An immutable mapping that reads like a dict and copies like a value.

    `types.MappingProxyType` was the obvious choice and is the wrong one here:
    it cannot be deep-copied or pickled, so a frozen policy carrying one would
    re-introduce the exact failure this module removes the moment anything
    downstream copied the object holding it. This type is genuinely immutable,
    so copying it can return the same object -- there is nothing to protect a
    copy from.
    """

    __slots__ = ("_items",)

    def __init__(self, items: dict[str, Any]) -> None:
        object.__setattr__(self, "_items", dict(items))

    def __getitem__(self, key: str) -> Any:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"FrozenMapping({self._items!r})"

    # -- immutability, enforced rather than documented -------------------------
    # `Mapping` gives no mutators, but `__setattr__`/`__delattr__` would still
    # let a caller rebind the payload. TypeError (not AttributeError) is what a
    # caller assigning into a read-only mapping already expects.
    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("FrozenMapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenMapping is immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("FrozenMapping is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("FrozenMapping is immutable")

    # -- value semantics --------------------------------------------------------
    def __copy__(self) -> FrozenMapping:
        return self

    def __deepcopy__(self, _memo: Any) -> FrozenMapping:
        return self

    def __reduce__(self):
        return (FrozenMapping, (dict(self._items),))

    def to_dict(self) -> dict[str, Any]:
        """A fresh, fully mutable rendering — see `render_mutable`."""
        return render_mutable(self)


def _is_scalar(value: Any) -> bool:
    # `bool` is an `int`; both are listed for the reader, not for the check.
    return value is None or isinstance(value, (str, bool, int, float))


def project(value: Any, *, _depth: int = 0, _seen: frozenset[int] | None = None) -> Any:
    """Project one value onto the immutable serializable projection.

    Containers are rebuilt (mappings as `FrozenMapping`, sequences and sets as
    tuples); scalars are kept; everything else becomes a type marker. Cycles and
    excessive depth are marked rather than followed, because `deepcopy` handled
    both and a naive recursive projector would take the turn down with a
    RecursionError instead.
    """
    seen = _seen or frozenset()
    if _is_scalar(value):
        return value
    if _depth >= MAX_PROJECTION_DEPTH:
        return _TOO_DEEP
    if id(value) in seen:
        return _CYCLE
    nested_seen = seen | {id(value)}

    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_PROJECTION_ITEMS:
                projected[_TRUNCATED] = len(value) - MAX_PROJECTION_ITEMS
                break
            # A non-string key cannot survive JSON and cannot be looked up by a
            # gate, so it is named rather than coerced into a plausible string.
            projected[key if isinstance(key, str) else unserializable_marker(key)] = project(
                item, _depth=_depth + 1, _seen=nested_seen
            )
        return FrozenMapping(projected)

    if isinstance(value, (Sequence, Set)) and not isinstance(value, (str, bytes, bytearray)):
        items = []
        for index, item in enumerate(value):
            if index >= MAX_PROJECTION_ITEMS:
                items.append(_TRUNCATED)
                break
            items.append(project(item, _depth=_depth + 1, _seen=nested_seen))
        return tuple(items)

    return unserializable_marker(value)


def project_context(value: Any) -> FrozenMapping:
    """Project a turn's `source_context`. A non-mapping projects to empty.

    Empty rather than an error, matching the behaviour this replaces: a caller
    with no context is a caller who declared nothing, which the policy layer
    already reads as "undeclared" through the M2 policy-slot law.
    """
    if not isinstance(value, Mapping):
        return FrozenMapping({})
    projected = project(value)
    return projected if isinstance(projected, FrozenMapping) else FrozenMapping({})


def unreadable_policy_inputs(projection: Mapping[str, Any]) -> tuple[str, ...]:
    """Policy-input keys whose value could not be carried into the projection.

    This is the fail-closed half. A gate consulting a marker is a gate deciding
    on a value it cannot see, so the caller records these and denies. Cargo
    never appears here, which is the whole distinction.
    """
    if not isinstance(projection, Mapping):
        return ()
    unreadable = [
        key
        for key in POLICY_INPUT_KEYS
        if key in projection and is_unserializable_marker(projection[key])
    ]
    return tuple(sorted(unreadable))


def render_mutable(value: Any) -> Any:
    """Render a projection back into fresh plain `dict`/`list` containers.

    A gate consult takes a `source_context` it may write to (`decide_tool_call`
    normalizes into what it is handed), and each consult must get its own, so
    one gate's scribbles cannot rewrite what the next gate reads.
    """
    if isinstance(value, Mapping):
        return {key: render_mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [render_mutable(item) for item in value]
    return value


__all__ = [
    "MAX_PROJECTION_DEPTH",
    "MAX_PROJECTION_ITEMS",
    "POLICY_INPUT_KEYS",
    "FrozenMapping",
    "is_unserializable_marker",
    "project",
    "project_context",
    "render_mutable",
    "unreadable_policy_inputs",
    "unserializable_marker",
]
