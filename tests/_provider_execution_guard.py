"""Discover and observe the production provider/model execution boundary."""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import CodeType, ModuleType
from typing import Any

from core.provider_execution_boundary import (
    is_provider_execution_boundary,
    subscribe_provider_execution_boundaries,
    subscribe_provider_execution_dispatches,
)


@dataclass(frozen=True)
class ExecutionBoundary:
    label: str
    code: CodeType


@dataclass
class ExecutionBoundaryLog:
    touches: list[str] = field(default_factory=list)
    from_semantic: list[str] = field(default_factory=list)


def _marked_functions(module: ModuleType) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for name, value in vars(module).items():
        if inspect.isfunction(value) and is_provider_execution_boundary(value):
            found.append((f"{module.__name__}.{name}", value))
        if not inspect.isclass(value) or value.__module__ != module.__name__:
            continue
        for method_name, method in vars(value).items():
            if inspect.isfunction(method) and is_provider_execution_boundary(method):
                found.append((f"{module.__name__}.{value.__name__}.{method_name}", method))
    return found


def discover_provider_execution_boundaries() -> tuple[ExecutionBoundary, ...]:
    """All marked execution functions in the shipped adapter/provider graph."""
    modules = [
        importlib.import_module("adapters.base_adapter"),
        importlib.import_module("core.cloud_provider_contract"),
        importlib.import_module("core.cloud_broker"),
        importlib.import_module("core.provider_invocation_gateway"),
    ]
    adapters = importlib.import_module("adapters")
    for entry in pkgutil.iter_modules(adapters.__path__):
        modules.append(importlib.import_module(f"adapters.{entry.name}"))
    by_code: dict[CodeType, str] = {}
    for module in modules:
        for label, function in _marked_functions(module):
            by_code.setdefault(function.__code__, label)
    return tuple(
        ExecutionBoundary(label=label, code=code)
        for code, label in sorted(by_code.items(), key=lambda item: item[1])
    )


def _semantic_on_stack(frame: Any) -> bool:
    current = frame.f_back
    marker = os.path.join("core", "semantic") + os.sep
    while current is not None:
        filename = os.path.normpath(str(current.f_code.co_filename or ""))
        if marker in filename + os.sep or filename.endswith(os.path.join("core", "semantic")):
            return True
        current = current.f_back
    return False


@contextmanager
def guard_provider_execution() -> Iterator[ExecutionBoundaryLog]:
    """Profile current and future marked code; aliases cannot evade code identity."""
    # Import the complete shipped graph first, then subscribe to the production registration
    # stream. The replay covers everything imported so far; notifications cover anything that
    # appears after installation in this thread or another one.
    discover_provider_execution_boundaries()
    labels: dict[CodeType, str] = {}
    log = ExecutionBoundaryLog()
    lock = threading.Lock()
    previous_sys = sys.getprofile()
    previous_thread = threading.getprofile()

    def _register(code: CodeType, label: str) -> None:
        with lock:
            labels[code] = label

    unsubscribe_boundaries = subscribe_provider_execution_boundaries(_register)

    def _dispatch(instance: Any, name: str, active: Any, label: str) -> None:
        frame = inspect.currentframe()
        if frame is None:  # pragma: no cover - CPython always supplies this; fail closed elsewhere
            raise RuntimeError("provider dispatch observation has no Python frame")
        from_semantic = _semantic_on_stack(frame)
        with lock:
            log.touches.append(label)
            if from_semantic:
                log.from_semantic.append(label)

    unsubscribe_dispatches = subscribe_provider_execution_dispatches(_dispatch)

    def _profile(frame, event, arg):
        if event == "call":
            with lock:
                label = labels.get(frame.f_code)
                if label is not None:
                    log.touches.append(label)
                    if _semantic_on_stack(frame):
                        log.from_semantic.append(label)
        if previous_sys is not None:
            previous_sys(frame, event, arg)
        return _profile

    sys.setprofile(_profile)
    threading.setprofile(_profile)
    try:
        yield log
    finally:
        sys.setprofile(previous_sys)
        threading.setprofile(previous_thread)
        unsubscribe_dispatches()
        unsubscribe_boundaries()


__all__ = [
    "ExecutionBoundaryLog",
    "discover_provider_execution_boundaries",
    "guard_provider_execution",
]
