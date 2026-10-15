"""Chat and project housekeeping as registry commands: search, pin, rename, archive, move.

The plain-words chat lane (``core.agent_runtime.housekeeping_lane``), the sidebar's
``POST /api/chat/pin-chat`` and these commands all call one owner, ``core.chat_housekeeping``,
so a CLI, API or palette caller gets the same reads, the same read-back on every write and the
same refusals as the chat. Every command here is operator-only and owner-local: a model lane or a
remote peer is refused at the authority, before the handler runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)


@dataclass(frozen=True)
class SearchChatsInput:
    query: str = ""
    limit: int = 5
    include_archived: bool = True


@dataclass(frozen=True)
class PinChatInput:
    session_id: str = ""
    pinned: bool = True


@dataclass(frozen=True)
class NoInput:
    pass


@dataclass(frozen=True)
class RenameChatInput:
    session_id: str = ""
    title: str = ""


@dataclass(frozen=True)
class ArchiveChatInput:
    session_id: str = ""
    archived: bool = True


@dataclass(frozen=True)
class MoveChatInput:
    session_id: str = ""
    project: str = ""


@dataclass(frozen=True)
class RenameProjectInput:
    project: str = ""
    name: str = ""


_AUTHORITY = OperatorAuthority(
    kind="chats.owner_local",
    verifier="core.command_registry.groups.delegated_routes:_gate_owner_local",
)
_AVAILABILITY = Availability(probe="core.command_registry.groups.housekeeping_group:_probe_housekeeping")

#: The HTTP doors these commands own, for the operator-action census.
ROUTES: dict[str, str] = {
    "http:POST:/api/chat/pin-chat": "chats.pin",
}


def housekeeping_route_command_map() -> dict[str, str]:
    return dict(ROUTES)


def _probe_housekeeping(_context: dict) -> tuple[bool, str]:
    try:
        from core import chat_housekeeping
        from core.memory.files import conversation_log_path

        return bool(chat_housekeeping) and bool(conversation_log_path()), ""
    except Exception as exc:  # pragma: no cover - surfaced as availability
        return False, f"chat stores unreachable: {exc}"


def _action(result: Any) -> Any:
    data = result.to_dict()
    if result.ok:
        return HandlerOk(data=data, summary=f"{result.action}: done" + ("" if result.changed else " (no change)"))
    code = "fault_validation"
    if result.error in ("chat_not_found", "chat_deleted", "project_not_found"):
        code = "usage"
    return HandlerFault(fault_code=code, summary=f"{result.action} refused: {result.error}", detail=data)


def _search(inp: SearchChatsInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import search_chats

    if not str(inp.query or "").strip():
        return HandlerFault(fault_code="usage", summary="search needs a query", detail={"error": "empty_query"})
    limit = max(1, min(int(inp.limit or 5), 20))
    result = search_chats(str(inp.query), limit=limit, include_archived=bool(inp.include_archived))
    if not result.ok:
        return HandlerFault(fault_code="usage", summary=f"search refused: {result.error}", detail=result.to_dict())
    return HandlerOk(data=result.to_dict(), summary=f"{len(result.hits)} chat(s) match")


def _pinned(_inp: NoInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import describe_chat, list_pinned_chats

    rows = []
    for item in list_pinned_chats():
        info = describe_chat(item["session_id"]) or {}
        rows.append({**item, "title": str(info.get("title") or ""), "project_id": str(info.get("project_id") or "")})
    return HandlerOk(data={"pinned": rows}, summary=f"{len(rows)} pinned chat(s)")


def _pin(inp: PinChatInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import set_chat_pinned

    return _action(set_chat_pinned(str(inp.session_id or ""), bool(inp.pinned)))


def _rename(inp: RenameChatInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import rename_chat

    return _action(rename_chat(str(inp.session_id or ""), str(inp.title or "")))


def _archive(inp: ArchiveChatInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import set_chat_archived

    return _action(set_chat_archived(str(inp.session_id or ""), bool(inp.archived)))


def _move(inp: MoveChatInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import move_chat_to_project

    return _action(move_chat_to_project(str(inp.session_id or ""), str(inp.project or "")))


def _rename_project(inp: RenameProjectInput, _ctx: Any) -> Any:
    from core.chat_housekeeping import rename_project

    return _action(rename_project(str(inp.project or ""), str(inp.name or "")))


_MODULE = "core.command_registry.groups.housekeeping_group"


def register(reg: CommandRegistry) -> None:
    reg.add_group(
        GroupSpec(
            group_id="chats",
            description=(
                "housekeeping over the operator's own chats and projects: search every chat by "
                "content, pin chats to the top, rename, archive, move between projects (owner-local)"
            ),
        )
    )
    common = dict(group="chats", availability=_AVAILABILITY, permission=_AUTHORITY, exit_codes=(0, 2, 21, 22, 30, 42))
    reg.add(CommandSpec(
        command_id="chats.search",
        description="Find which chats talked about something: every chat's turns, titles, projects and pinned messages",
        input_schema=SearchChatsInput, effects="read_only", capabilities=frozenset({"logs.read"}),
        handler=Handler(f"{_MODULE}:_search"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.pinned",
        description="List the chats pinned to the top of the sidebar",
        input_schema=NoInput, effects="read_only", capabilities=frozenset({"logs.read"}),
        handler=Handler(f"{_MODULE}:_pinned"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.pin",
        description="Pin or unpin a whole chat to the top of the sidebar",
        input_schema=PinChatInput, effects="idempotent_write", capabilities=frozenset({"change_settings"}),
        handler=Handler(f"{_MODULE}:_pin"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.rename",
        description="Rename one chat (metadata only; never the transcript)",
        input_schema=RenameChatInput, effects="idempotent_write", capabilities=frozenset({"change_settings"}),
        handler=Handler(f"{_MODULE}:_rename"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.archive",
        description="Archive or restore one chat",
        input_schema=ArchiveChatInput, effects="idempotent_write", capabilities=frozenset({"change_settings"}),
        handler=Handler(f"{_MODULE}:_archive"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.move",
        description="Move one chat into a project by name, or back to General",
        input_schema=MoveChatInput, effects="idempotent_write", capabilities=frozenset({"change_settings"}),
        handler=Handler(f"{_MODULE}:_move"), **common,
    ))
    reg.add(CommandSpec(
        command_id="chats.project.rename",
        description="Rename one project (its folder binding is unchanged)",
        input_schema=RenameProjectInput, effects="idempotent_write", capabilities=frozenset({"change_settings"}),
        handler=Handler(f"{_MODULE}:_rename_project"), **common,
    ))
