"""The served operator surface for RepoOps sessions and the plugin lifecycle.

This module holds no authority. It reads the RepoOps journal, and it turns an owner-local
operator request into the ONE thing the operator alone can produce: a server-stamped
`repo_push_authorization`, handed to the runtime through `source_context`. That key is in
`core.request_trust.RESERVED_TRUST_KEYS`, so an inbound body carrying one is stripped before any
runtime sees it -- which is what makes "the operator authorized this push" a fact rather than a
claim a turn can make about itself.

Everything else it does is read: a session's sealed receipt and the plugin lifecycle's state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _sessions() -> list[dict[str, Any]]:
    from core.repoops.plane import _read_journal_bytes, session_dir, session_journal_path

    rows: list[dict[str, Any]] = []
    root = session_dir()
    if not root.is_dir():
        return rows
    for path in sorted(root.glob("*.json")):
        try:
            state, raw = _read_journal_bytes(session_journal_path(path.stem))
            if state != "ok":
                continue
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        binding = dict(payload.get("binding") or {})
        rows.append(
            {
                "repo_session_id": str(payload.get("session_key") or path.stem),
                "objective": str(payload.get("objective") or ""),
                "stage": str(payload.get("stage") or ""),
                "root": str(payload.get("root") or ""),
                "provider": str(binding.get("provider") or ""),
                "namespace": str(binding.get("namespace") or ""),
                "pull_request": str(binding.get("pull_request") or ""),
                "local_head": str(binding.get("local_head") or ""),
                "remote_head": str(binding.get("remote_head") or ""),
                "dirty": bool(binding.get("dirty") or False),
                "seal": str(payload.get("seal") or ""),
                "updated_at": str(payload.get("updated_at") or ""),
                "push_outcome": str(dict(payload.get("push_outcome") or {}).get("outcome") or ""),
                "remote_verified": bool(dict(payload.get("remote_verification") or {}).get("verified") or False),
                "awaiting_authorization": bool(payload.get("push_plan")) and not bool(payload.get("authorization")),
            }
        )
    return rows


def repo_sessions_payload() -> dict[str, Any]:
    rows = _sessions()
    return {
        "sessions": rows,
        "count": len(rows),
        "awaiting_authorization": [r["repo_session_id"] for r in rows if r["awaiting_authorization"]],
    }


def repo_session_payload(repo_session_id: str) -> dict[str, Any]:
    from core.repoops.plane import _read_journal_bytes, session_journal_path

    key = str(repo_session_id or "").strip()
    if not key:
        return {"found": False, "error": "an id is required"}
    try:
        path = session_journal_path(key)
        state, raw = _read_journal_bytes(path)
        if state != "ok":
            return {"found": False, "error": "no readable repo session"}
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"found": False, "error": f"no repo session `{key}`"}
    return {"found": True, "session": payload}


def authorize_push(
    *,
    repo_session_id: str,
    plan_hash: str,
    force: bool = False,
    branch_delete: bool = False,
    workspace_root: str = "",
) -> dict[str, Any]:
    """Mint the operator's authorization SERVER-SIDE and hand it to the runtime.

    The stamp is built here, from this request, and never from the body's own claim to be one.
    """

    from core.repoops.plane import AUTHORIZATION_CONTEXT_KEY, dispatch_repo_intent

    session = str(repo_session_id or "").strip()
    plan = str(plan_hash or "").strip()
    if not session or not plan:
        return {"ok": False, "error": "repo_session_id and plan_hash are both required"}
    context = {
        "workspace": workspace_root,
        "workspace_root": workspace_root,
        "surface": "operator",
        AUTHORIZATION_CONTEXT_KEY: {
            "plan_hash": plan,
            "operator": "owner-local",
            "force": bool(force),
            "branch_delete": bool(branch_delete),
        },
    }
    result = dispatch_repo_intent(
        "repo.push.authorize",
        {"repo_session_id": session, "plan_hash": plan},
        source_context=context,
        workspace_root=Path(workspace_root or "."),
    )
    if result is None:
        return {"ok": False, "error": "repo.push.authorize is not dispatchable on this runtime"}
    details = dict(getattr(result, "details", {}) or {})
    return {
        "ok": bool(result.ok),
        "status": str(result.status),
        "message": str(result.response_text or ""),
        "plan_hash": str(details.get("plan_hash") or ""),
        "expires_at": str(details.get("expires_at") or ""),
    }


_LIFECYCLE_ACTIONS = ("install", "verify", "enable", "disable", "update", "revoke", "uninstall")


def plugin_lifecycle_action(*, action: str, plugin_id: str) -> dict[str, Any]:
    from core import plugin_lifecycle
    from core.plugin_catalog import plugins_root

    name = str(action or "").strip().lower()
    pid = str(plugin_id or "").strip()
    if name not in _LIFECYCLE_ACTIONS:
        return {"ok": False, "error": f"unknown action `{name}`; the acts are {list(_LIFECYCLE_ACTIONS)}"}
    if not pid:
        return {"ok": False, "error": "plugin_id is required"}
    base = plugins_root()
    root = (Path(base) / "plugins" / pid) if base is not None else None
    try:
        if name == "install":
            record = plugin_lifecycle.install(pid, root=root, source="operator")
        elif name == "verify":
            record = plugin_lifecycle.verify(pid, root=root)
        elif name == "enable":
            record = plugin_lifecycle.enable(pid)
        elif name == "disable":
            record = plugin_lifecycle.disable(pid)
        elif name == "update":
            record = plugin_lifecycle.update(pid, root=root)
        elif name == "revoke":
            record = plugin_lifecycle.revoke(pid, reason="revoked from the operator console")
        else:
            record = plugin_lifecycle.uninstall(pid)
    except plugin_lifecycle.LifecycleError as exc:
        return {"ok": False, "action": name, "plugin_id": pid, "error": str(exc)}
    # Re-registration: the act changed what the model may be offered. A pack that was merely
    # present at boot registered as an EXPLAINED ABSENCE; the moment the operator installs,
    # verifies or enables it, its contracts re-enter the ONE registry (and a disable, revoke
    # or uninstall returns them to explained absence). The response's `available` and the
    # registry's live view must never disagree.
    registered, register_error = _reregister_pack(pid, base)
    return {
        "ok": True,
        "action": name,
        "plugin_id": pid,
        "stage": record.stage,
        # Said explicitly on every response: reaching a stage is not becoming available.
        "available": plugin_lifecycle.is_available(pid),
        "contracts_registered": registered,
        **({"register_error": register_error} if register_error else {}),
        "evidence": record.evidence[-1] if record.evidence else {},
    }


def _reregister_pack(pid: str, base) -> tuple[bool, str]:
    """Bring the one registry's contracts for one pack in line with its lifecycle state."""
    from core import plugin_tools, tool_registry

    try:
        manifest = Path(base) / "plugins" / pid / ".codex-plugin" / "plugin.json"
        if not manifest.is_file():
            return False, f"no manifest at {manifest}"
        plugin = plugin_tools.load_manifest(manifest)
        for contract in plugin.contracts:
            tool_registry.unregister(str(contract.intent))
        plugin_tools.register_plugin(plugin)
        return True, ""
    except Exception as exc:  # the lifecycle act stands; registration failure is named, not swallowed
        return False, f"{type(exc).__name__}: {exc}"


def authorize_forge_action(
    *,
    repo_session_id: str,
    action_hash: str = "",
    resolve: str = "",
    workspace_root: str = "",
) -> dict[str, Any]:
    """Mint the operator's FORGE-ACTION authorization SERVER-side, or resolve an unproven one.

    The stamp is built here, from this owner-local request, never from a body's claim to be one:
    `repo_forge_action_authorization` is a reserved trust key, so a turn cannot author its own
    consent to put a draft PR, a PR text update or a comment on a forge. `resolve` states the
    operator inspected the forge and the unproven action did NOT land
    (`failed_safe_to_retry`); asserting the opposite is refused -- an applied outcome must come
    from the forge's own record."""

    from core.repoops.plane import FORGE_ACTION_CONTEXT_KEY, dispatch_repo_intent

    session = str(repo_session_id or "").strip()
    if not session:
        return {"ok": False, "error": "repo_session_id is required"}
    stamp: dict[str, Any] = {"operator": "owner-local"}
    if str(resolve or "").strip():
        stamp["resolve"] = str(resolve or "").strip().lower()
    if str(action_hash or "").strip():
        stamp["action_hash"] = str(action_hash or "").strip()
    context = {
        "workspace": workspace_root,
        "workspace_root": workspace_root,
        "surface": "operator",
        FORGE_ACTION_CONTEXT_KEY: stamp,
    }
    arguments = {"repo_session_id": session}
    if str(action_hash or "").strip():
        arguments["action_hash"] = str(action_hash or "").strip()
    result = dispatch_repo_intent(
        "repo.pr.authorize",
        arguments,
        source_context=context,
        workspace_root=Path(workspace_root or "."),
    )
    if result is None:
        return {"ok": False, "error": "repo.pr.authorize is not dispatchable on this runtime"}
    details = dict(getattr(result, "details", {}) or {})
    return {
        "ok": bool(result.ok),
        "status": str(result.status),
        "message": str(result.response_text or ""),
        "action_hash": str(details.get("action_hash") or ""),
        "expires_at": str(details.get("expires_at") or ""),
        "resolved": str(details.get("resolved") or ""),
    }


__all__ = [
    "authorize_forge_action",
    "authorize_push",
    "plugin_lifecycle_action",
    "repo_session_payload",
    "repo_sessions_payload",
]
