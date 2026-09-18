"""Machine-readable provider/model execution boundaries.

The zero-extra-call proof observes the exact callable resolved at the canonical production seam.
It does not reimplement Python's internal callable-object or descriptor dispatch: the same resolved
object is passed unchanged to native call machinery. Code-object markers remain supplementary
coverage for direct function aliases and free gateways, and live registration prevents their
discovery snapshot from becoming stale.
"""
from __future__ import annotations

import threading
from abc import ABCMeta
from collections.abc import Callable
from functools import partial
from types import CodeType, FunctionType, MethodType
from typing import Any, TypeVar

_BOUNDARY_ATTRIBUTE = "__vool_provider_execution_boundary__"
_F = TypeVar("_F", bound=Callable[..., Any])
_REGISTRY_LOCK = threading.RLock()
_REGISTERED_BOUNDARIES: dict[CodeType, str] = {}
_BOUNDARY_SUBSCRIBERS: set[Callable[[CodeType, str], None]] = set()
_DISPATCH_SUBSCRIBERS: set[Callable[[Any, str, Any, str], None]] = set()
_MISSING = object()


def _execution_boundary_label(function: Callable[..., Any]) -> str:
    module = str(getattr(function, "__module__", "") or "")
    qualname = str(getattr(function, "__qualname__", "") or getattr(function, "__name__", ""))
    return f"{module}.{qualname}".strip(".")


def provider_execution_boundary(function: _F) -> _F:
    """Mark ``function`` as a provider/model execution authority boundary."""
    setattr(function, _BOUNDARY_ATTRIBUTE, True)
    code = getattr(function, "__code__", None)
    if isinstance(code, CodeType):
        label = _execution_boundary_label(function)
        with _REGISTRY_LOCK:
            _REGISTERED_BOUNDARIES[code] = label
            subscribers = tuple(_BOUNDARY_SUBSCRIBERS)
        for subscriber in subscribers:
            subscriber(code, label)
    return function


def is_provider_execution_boundary(value: Any) -> bool:
    """Whether a function carries the production execution-boundary contract."""
    return bool(getattr(value, _BOUNDARY_ATTRIBUTE, False))


def _descriptor_function(value: Any) -> Callable[..., Any] | None:
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    if isinstance(value, MethodType):
        value = value.__func__
    return value if isinstance(value, FunctionType) else None


def _callable_execution_function(value: Any, seen: set[int] | None = None) -> Callable[..., Any] | None:
    """Return the Python function that calling ``value`` will actually execute.

    Special-method lookup ignores an instance ``__dict__`` and searches the callable object's
    type MRO. Mirror that lookup instead of consulting only the concrete class dictionary. A
    partial delegates to its wrapped callable. Python functions, bound methods, static methods and
    class methods expose the code identity that the profiler can observe; opaque C call slots do
    not manufacture a Python code boundary.
    """
    function = _descriptor_function(value)
    if function is not None:
        return function
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return None
    seen.add(identity)
    if isinstance(value, partial):
        return _callable_execution_function(value.func, seen)
    if not callable(value):
        return None
    for owner in type(value).__mro__:
        call_descriptor = vars(owner).get("__call__", _MISSING)
        if call_descriptor is _MISSING:
            continue
        return _descriptor_function(call_descriptor)
    return None


class _LiveBoundaryDescriptor:
    """Preserve a custom descriptor while registering the callable it actually returns."""

    def __init__(self, descriptor: Any) -> None:
        self._descriptor = descriptor

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        active = self._descriptor.__get__(instance, owner)
        function = getattr(active, "__func__", active)
        if isinstance(function, FunctionType):
            provider_execution_boundary(function)
        return active


class _LiveDataBoundaryDescriptor(_LiveBoundaryDescriptor):
    """Preserve the set/delete behavior of a data descriptor as well as its live get."""

    def __set__(self, instance: Any, value: Any) -> None:
        setter = getattr(self._descriptor, "__set__", None)
        if setter is None:
            raise AttributeError("provider execution descriptor is read-only")
        setter(instance, value)

    def __delete__(self, instance: Any) -> None:
        deleter = getattr(self._descriptor, "__delete__", None)
        if deleter is None:
            raise AttributeError("provider execution descriptor cannot be deleted")
        deleter(instance)


def _is_boundary_role_value(value: Any) -> bool:
    if isinstance(value, _LiveBoundaryDescriptor):
        return True
    function = _descriptor_function(value)
    return function is not None and is_provider_execution_boundary(function)


def _class_has_boundary_role(cls: type, name: str) -> bool:
    for owner in cls.__mro__:
        value = vars(owner).get(name, _MISSING)
        if value is not _MISSING and _is_boundary_role_value(value):
            return True
    return False


def _prepare_boundary_replacement(value: Any) -> Any:
    function = _descriptor_function(value)
    if function is not None:
        provider_execution_boundary(function)
        return value
    if hasattr(value, "__get__"):
        if hasattr(type(value), "__set__") or hasattr(type(value), "__delete__"):
            return _LiveDataBoundaryDescriptor(value)
        return _LiveBoundaryDescriptor(value)
    return value


def observe_provider_execution_boundary(instance: Any, name: str, active: Any) -> Any:
    """Register the callable actually resolved from one provider instance boundary role.

    Class registration cannot see a later instance ``__dict__`` shadow. Resolving the attribute is
    the last common production point before direct invocation, and therefore covers ``setattr``,
    ``MethodType``, raw dictionary shadows, descriptors, aliases, and restore/re-replace cycles.
    The callable is returned unchanged; only its code identity is registered.
    """
    if not _class_has_boundary_role(type(instance), name):
        return active
    function = _callable_execution_function(active)
    if function is not None:
        provider_execution_boundary(function)
    return active


def invoke_provider_execution_boundary(
    instance: Any,
    name: str,
    *args: Any,
    before_call: Callable[[], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Resolve, observe, and natively invoke the callable active at a provider seam.

    A plugin may override ``__getattribute__`` and bypass the base adapter's lookup hook entirely.
    The caller that owns execution therefore observes the exact object returned by its one real
    lookup. The seam publishes that identity immediately before calling it unchanged; Python alone
    owns any nested ``__call__`` descriptor dispatch.
    """
    active = getattr(instance, name)
    active = observe_provider_execution_boundary(instance, name, active)
    if before_call is not None:
        before_call()
    owner = type(instance)
    label = f"{owner.__module__}.{owner.__qualname__}.{name}".strip(".")
    with _REGISTRY_LOCK:
        subscribers = tuple(_DISPATCH_SUBSCRIBERS)
    for subscriber in subscribers:
        subscriber(instance, name, active, label)
    return active(*args, **kwargs)


class ProviderExecutionBoundaryMeta(ABCMeta):
    """Keep a provider boundary role attached when its active method is replaced later."""

    def __setattr__(cls, name: str, value: Any) -> None:
        if _class_has_boundary_role(cls, name):
            value = _prepare_boundary_replacement(value)
        super().__setattr__(name, value)


def inherit_provider_execution_boundaries(cls: type) -> None:
    """Propagate marked abstract/base methods to concrete overrides on ``cls``.

    Adapter subclasses override ``invoke``/``send_request``. Requiring every override author to
    remember another decorator would create exactly the drift this contract exists to remove, so
    the base role propagates automatically when the subclass is created.
    """
    own = vars(cls)
    for base in cls.__mro__[1:]:
        for name, base_value in vars(base).items():
            if not is_provider_execution_boundary(base_value) or name not in own:
                continue
            value = own[name]
            if callable(value):
                provider_execution_boundary(value)


def subscribe_provider_execution_boundaries(
    subscriber: Callable[[CodeType, str], None],
) -> Callable[[], None]:
    """Observe current and future boundaries until the returned unsubscribe is called.

    Registration is live rather than a discovery snapshot: subclasses, dynamic imports and plugin
    providers created after a proof starts are delivered when their marked override is created.
    """
    with _REGISTRY_LOCK:
        _BOUNDARY_SUBSCRIBERS.add(subscriber)
        current = tuple(_REGISTERED_BOUNDARIES.items())
    for code, label in current:
        subscriber(code, label)

    def unsubscribe() -> None:
        with _REGISTRY_LOCK:
            _BOUNDARY_SUBSCRIBERS.discard(subscriber)

    return unsubscribe


def subscribe_provider_execution_dispatches(
    subscriber: Callable[[Any, str, Any, str], None],
) -> Callable[[], None]:
    """Observe future canonical seam dispatches and the exact callable each seam invokes."""
    with _REGISTRY_LOCK:
        _DISPATCH_SUBSCRIBERS.add(subscriber)

    def unsubscribe() -> None:
        with _REGISTRY_LOCK:
            _DISPATCH_SUBSCRIBERS.discard(subscriber)

    return unsubscribe


__all__ = [
    "ProviderExecutionBoundaryMeta",
    "inherit_provider_execution_boundaries",
    "invoke_provider_execution_boundary",
    "is_provider_execution_boundary",
    "observe_provider_execution_boundary",
    "provider_execution_boundary",
    "subscribe_provider_execution_boundaries",
    "subscribe_provider_execution_dispatches",
]
