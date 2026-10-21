"""The operator registry owns the live browser's control doors."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)

ACTIONS = ("settings", "propose", "decide", "takeover", "stop")


@dataclass
class BrowserInput:
    body: dict[str, Any] = field(default_factory=dict)


def owner(_inp, ctx):
    from core.web.api.service import _credential_page_refusal

    extra = dict(getattr(ctx, "extra", None) or {})
    allowed = (ctx.principal == "operator" and ctx.projection == "api"
               and not _credential_page_refusal(extra.get("headers"), str(extra.get("client_host") or "")))
    return AuthorityDecision(granted=allowed, reason=None if allowed else "owner_local_required")


def storage_available(_context):
    import os

    from core.runtime_paths import active_data_dir

    # Settings and stop remain callable while browsing is disabled. The handler
    # owns policy refusals; this probe checks the local control store itself.
    target = active_data_dir()
    while not target.exists() and target != target.parent:
        target = target.parent
    ready = target.is_dir() and os.access(target, os.W_OK | os.X_OK)
    return ready, "" if ready else "browser_store_unavailable"


def _run(action: str, inp: BrowserInput, _ctx: Any):
    from core.web.api.browser_api import perform

    response = perform("/api/browser/" + action, inp.body)
    payload = json.loads(response.body)
    if response.status != 200:
        return HandlerFault("conflict", str(payload.get("error") or "browser_refused"),
                            {**payload, "legacy_status": response.status})
    return HandlerOk(payload)


def settings(inp, ctx): return _run("settings", inp, ctx)
def propose(inp, ctx): return _run("propose", inp, ctx)
def decide(inp, ctx): return _run("decide", inp, ctx)
def takeover(inp, ctx): return _run("takeover", inp, ctx)
def stop(inp, ctx): return _run("stop", inp, ctx)


def register(reg) -> None:
    from core.i18n import catalog_for

    cat = catalog_for("en")
    reg.add_group(GroupSpec("browser", cat.text("live.browser")))
    labels = {"settings": "browser", "propose": "open", "decide": "approve", "takeover": "takeover", "stop": "stopped"}
    for action in ACTIONS:
        reg.add(CommandSpec(
            command_id="browser." + action, group="browser", description=cat.text("live." + labels[action]),
            input_schema=BrowserInput, effects="external_send" if action == "decide" else "mutating",
            permission=OperatorAuthority(kind="browser.owner", verifier="core.command_registry.groups.browser_group:owner"),
            handler=Handler("core.command_registry.groups.browser_group:" + action),
            availability=Availability("core.command_registry.groups.browser_group:storage_available"),
            exit_codes=(0, 2, 21, 30, 42), model_offerable=False,
        ))
