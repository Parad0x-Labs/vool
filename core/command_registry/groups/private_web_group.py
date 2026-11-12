"""The operator registry owns Private web's control doors (Settings only, owner-local)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)

ACTIONS = ("settings", "vpn_file", "forget_vpn_file")
_DESCRIPTIONS = {
    "settings": "Choose how VOOL's web traffic leaves: off, Tor, a VPN file, or the computer's VPN",
    "vpn_file": "Add the WireGuard file from a VPN provider",
    "forget_vpn_file": "Remove the stored WireGuard file",
}


@dataclass
class PrivateWebInput:
    body: dict[str, Any] = field(default_factory=dict)


def owner(inp, ctx):
    from core.command_registry.groups.browser_group import owner as browser_owner

    return browser_owner(inp, ctx)


def storage_available(context):
    from core.command_registry.groups.browser_group import storage_available as browser_storage

    ready, _reason = browser_storage(context)
    return ready, "" if ready else "private_web_store_unavailable"


def _run(action: str, inp: PrivateWebInput, _ctx: Any):
    from core.web.api.private_web_api import perform

    response = perform("/api/private-web/" + action, inp.body)
    payload = json.loads(response.body)
    if response.status != 200:
        return HandlerFault("conflict", str(payload.get("error") or "private_web_refused"),
                            {**payload, "legacy_status": response.status})
    return HandlerOk(payload)


def settings(inp, ctx): return _run("settings", inp, ctx)
def vpn_file(inp, ctx): return _run("vpn_file", inp, ctx)
def forget_vpn_file(inp, ctx): return _run("forget_vpn_file", inp, ctx)


def register(reg) -> None:
    reg.add_group(GroupSpec("private_web", "Private web"))
    for action in ACTIONS:
        reg.add(CommandSpec(
            command_id="private_web." + action, group="private_web", description=_DESCRIPTIONS[action],
            input_schema=PrivateWebInput, effects="mutating",
            permission=OperatorAuthority(kind="private_web.owner", verifier="core.command_registry.groups.private_web_group:owner"),
            handler=Handler("core.command_registry.groups.private_web_group:" + action),
            availability=Availability("core.command_registry.groups.private_web_group:storage_available"),
            exit_codes=(0, 2, 21, 30, 42), model_offerable=False,
        ))
