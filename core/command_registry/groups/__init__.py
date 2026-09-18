"""Group modules import existing authorities; the registry never rewrites them.

Each module exposes ``register(reg)`` and binds CommandSpecs to small typed
adapters that call the real authority functions. A missing authority is an
import failure here — registration cannot silently degrade.
"""
from __future__ import annotations

from core.command_registry.registry import CommandRegistry

_GROUP_MODULES = (
    "core.command_registry.groups.meta",
    "core.command_registry.groups.receipts",
    "core.command_registry.groups.blackbox_group",
    "core.command_registry.groups.logs_group",
    "core.command_registry.groups.archaeology_group",
    "core.command_registry.groups.models",
    "core.command_registry.groups.council",
    "core.command_registry.groups.approvals",
    "core.command_registry.groups.update_group",
    "core.command_registry.groups.faults_group",
    "core.command_registry.groups.tasks",
    "core.command_registry.groups.convergence",
    "core.command_registry.groups.contacts_group",
    "core.command_registry.groups.ops_surfaces",
    "core.command_registry.groups.context_pages_group",
    "core.command_registry.groups.delegated_routes",
    "core.command_registry.groups.blueprint_gaps",
    "core.command_registry.groups.trust",
    "core.command_registry.groups.first_run",
    "core.command_registry.groups.lifeform_group",
)


def register_all(reg: CommandRegistry) -> None:
    import importlib

    for module_name in _GROUP_MODULES:
        module = importlib.import_module(module_name)
        register = module.register
        register(reg)
