"""core.command_registry — the one typed Command Registry.

Every operator surface (CLI, chat discovery, palette, API) is a projection of
this registry; the registry imports existing product authorities incrementally
and never rewrites them.

Public API:
- ``registry()``               — the process-wide CommandRegistry
- ``execute_command(...)``     — the one execution seam (all projections share it)
- ``check_registry(...)``      — the --check lint
- projections: ``commands_json``, ``cli_help``, ``chat_discovery``,
  ``palette_data``, ``api_schema``
"""
from core.command_registry.check import CheckReport, Finding, check_registry
from core.command_registry.envelope import (
    Breadcrumb,
    CommandEnvelope,
    ExecutionBlock,
    ExitCodes,
    FaultBlock,
)
from core.command_registry.execute import ExecutionContext, execute_command
from core.command_registry.projections import (
    api_schema,
    chat_discovery,
    check_output,
    cli_help,
    commands_json,
    palette_data,
)
from core.command_registry.registry import (
    CommandRegistry,
    RegistryError,
    RegistrySnapshot,
    registry,
    reset_registry,
)
from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OpenRead,
    OperatorAuthority,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "AuthorityDecision",
    "Availability",
    "Breadcrumb",
    "CheckReport",
    "CommandEnvelope",
    "CommandRegistry",
    "CommandSpec",
    "ExecutionBlock",
    "ExecutionContext",
    "ExitCodes",
    "FaultBinding",
    "FaultBlock",
    "Finding",
    "GroupSpec",
    "Handler",
    "HandlerFault",
    "HandlerOk",
    "NextAction",
    "OpenRead",
    "OperatorAuthority",
    "RegistryError",
    "RegistrySnapshot",
    "api_schema",
    "chat_discovery",
    "check_output",
    "check_registry",
    "cli_help",
    "commands_json",
    "execute_command",
    "palette_data",
    "registry",
    "reset_registry",
]
