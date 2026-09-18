"""The one Command Registry.

Registration happens once per process at import of ``core.command_registry.groups``;
collision detection runs at registration time (an import crash on duplicates),
and ``check()`` re-verifies the full contract (orphans, unbound handlers,
contract divergence, schema typing).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from core.command_registry.spec import CommandSpec, GroupSpec


class RegistryError(RuntimeError):
    """Registration-time contract violation (duplicate id/alias/surface)."""


@dataclass(frozen=True)
class RegistrySnapshot:
    commands: tuple[CommandSpec, ...]
    groups: tuple[GroupSpec, ...]

    def command_by_id(self, command_id: str) -> CommandSpec | None:
        for spec in self.commands:
            if spec.command_id == command_id:
                return spec
        return None


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._groups: dict[str, GroupSpec] = {}
        # every surface spelling (command_id + aliases) → owning command_id
        self._surface: dict[str, str] = {}

    # -- registration -------------------------------------------------------

    def add_group(self, group: GroupSpec) -> None:
        if group.group_id in self._groups:
            raise RegistryError(f"duplicate group id: {group.group_id}")
        for alias in group.aliases:
            if alias in self._groups:
                raise RegistryError(f"group alias collision: {alias!r}")
        self._groups[group.group_id] = group

    def add(self, spec: CommandSpec) -> None:
        if spec.command_id in self._commands:
            raise RegistryError(f"duplicate command id: {spec.command_id}")
        for surface in (spec.command_id, *spec.aliases):
            if surface in self._surface:
                owner = self._surface[surface]
                if owner != spec.command_id:
                    raise RegistryError(
                        f"route collision: surface {surface!r} claimed by "
                        f"{owner!r} and {spec.command_id!r}"
                    )
            self._surface[surface] = spec.command_id
        self._commands[spec.command_id] = spec

    # -- reads --------------------------------------------------------------

    def lookup(self, surface: str) -> CommandSpec | None:
        command_id = self._surface.get(surface)
        if command_id is None:
            return None
        return self._commands[command_id]

    def commands(self) -> tuple[CommandSpec, ...]:
        return tuple(self._commands.values())

    def groups(self) -> tuple[GroupSpec, ...]:
        return tuple(self._groups.values())

    def group_of(self, group_id: str) -> GroupSpec | None:
        return self._groups.get(group_id)

    def surface_map(self) -> dict[str, str]:
        return dict(self._surface)

    def snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot(commands=self.commands(), groups=self.groups())


_REGISTRY: CommandRegistry | None = None


def registry() -> CommandRegistry:
    """The process-wide registry; first call imports every group module."""
    global _REGISTRY
    if _REGISTRY is None:
        reg = CommandRegistry()
        import core.command_registry.groups as _groups

        _groups.register_all(reg)
        _REGISTRY = reg
        # Project explicitly model-offerable commands into the capability vocabulary.
        # Import AFTER _REGISTRY is set: the projection reads the registry.
        import contextlib

        from core.command_registry.model_tools import project_model_tools as _project

        with contextlib.suppress(Exception):
            _project()
    return _REGISTRY


def reset_registry() -> None:
    """Test seam: force re-registration on next ``registry()`` call."""
    global _REGISTRY
    _REGISTRY = None


def resolve_dotted(dotted: str) -> Any:
    """Resolve ``package.module:attr`` lazily; raises on unresolvable paths."""
    module_name, _, attr = dotted.partition(":")
    if not module_name or not attr:
        raise ValueError(f"malformed dotted path: {dotted!r}")
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj
