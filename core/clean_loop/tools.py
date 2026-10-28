"""The clean loop's tool table: a short vocabulary over VOOL's real machine intents.

Every call crosses `core.authorized_tool_execution.execute_authorized_runtime_tool`, the one
authorized boundary (permission decision, cancellation, receipts). Paths are resolved here first and
must land inside the owner's home folder; a home that resolves to a filesystem root is refused
outright, so no tool in this loop can ever walk "/". The runtime's own handlers then apply their
narrower rules on top (today: Desktop, Downloads and Documents), and a refusal from them is passed
to the model as a plain "not available" line.

Nothing here writes, moves or deletes. `propose_change` records a pending proposal and returns
"waiting for your approval"; the change itself is left to VOOL's normal approval ceremony.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from core.clean_loop.registry import Registry, ToolContext, ToolOutcome

_RESULT_CHARS = 3000
_FIND_WORDS = 3
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "my", "me", "i", "you",
        "your", "this", "that", "what", "where", "which", "how", "find", "file", "files", "folder",
        "folders", "doc", "docs", "document", "documents",
    }
)


class OutsideHome(ValueError):
    """A path the loop refuses before any tool runs."""


def home_dir() -> Path:
    """The owner's home, resolved the way the runtime's machine tools resolve it."""
    try:
        from core.runtime_execution_tools import _machine_home

        home = Path(_machine_home()).expanduser().resolve()
    except Exception:
        home = Path.home().resolve()
    if home == Path(home.anchor) or str(home) in {"", "/"}:
        raise OutsideHome("the home folder resolves to a filesystem root")
    return home


def scope_path(raw: Any, home: Path) -> Path:
    """`raw` (relative to home, `~/...`, or absolute) as a resolved path inside `home`, or OutsideHome."""
    if home == Path(home.anchor):
        raise OutsideHome("the home folder resolves to a filesystem root")
    text = str(raw or "").strip().strip("'\"")
    if text in {"", ".", "~", "~/"}:
        return home
    if text.startswith("~/") or text.startswith("~\\"):
        text = text[2:]
    candidate = Path(text)
    full = (candidate if candidate.is_absolute() else home / candidate).resolve()
    if full != home and home not in full.parents:
        raise OutsideHome("that path is outside your home folder")
    return full


def _plain(text: str, home: Path) -> str:
    """Tool prose for the model: home-relative paths, no internal intent names."""
    out = str(text or "").replace(str(home) + os.sep, "").replace(str(home), "~")
    out = re.sub(r"`[a-z_]+\.[a-z_.]+`", "that action", out)
    return out[:_RESULT_CHARS]


def _call(ctx: ToolContext, intent: str, arguments: dict[str, Any]) -> ToolOutcome:
    execute = ctx.execute
    if execute is None:
        from core.authorized_tool_execution import execute_authorized_runtime_tool as execute
    try:
        result = execute(intent, arguments, task_id=ctx.task_id, source_context=ctx.source_context)
    except Exception as exc:  # a tool fault is evidence for the model, never a crash of the turn
        result = None
        status, ok, text = "error", False, f"that did not work ({type(exc).__name__})"
    else:
        if result is None:
            status, ok, text = "unsupported", False, "that is not available on this computer"
        else:
            status = str(getattr(result, "status", "") or "")
            ok = bool(getattr(result, "ok", False))
            text = str(getattr(result, "response_text", "") or "")
    details = dict(getattr(result, "details", {}) or {}) if result is not None else {}
    ctx.receipts.append(
        {
            "tool_name": intent,
            "mode": "tool_executed" if ok else "tool_failed",
            "status": status or ("executed" if ok else "failed"),
            "execution": {"executed": ok, "ok": ok, "status": status},
            "observation": details.get("observation") or {},
            "permission": details.get("permission") or {},
            "lane": "clean_local_loop",
        }
    )
    plain = _plain(text, ctx.home) if ok else "not available: " + (_plain(text, ctx.home) or status)
    return ToolOutcome(text=plain or "(empty)", ok=ok, status=status, intents=(intent,))


def _merge(outcomes: list[ToolOutcome], text: str) -> ToolOutcome:
    intents = tuple(i for o in outcomes for i in o.intents)
    ok = any(o.ok for o in outcomes)
    status = "executed" if ok else (outcomes[-1].status if outcomes else "error")
    return ToolOutcome(text=text[:_RESULT_CHARS] or "(empty)", ok=ok, status=status, intents=intents)


def _scoped(args: dict[str, Any], ctx: ToolContext, key: str = "path") -> Path | ToolOutcome:
    try:
        return scope_path(args.get(key), ctx.home)
    except OutsideHome as exc:
        return ToolOutcome(text=f"not available: {exc}", ok=False, status="outside_home")


def _safe_roots(ctx: ToolContext) -> list[Path]:
    try:
        from core.runtime_execution_tools import _safe_machine_roots

        roots = [Path(root) for root in _safe_machine_roots()]
    except Exception:
        roots = [ctx.home / name for name in ("Desktop", "Downloads", "Documents")]
    return [root for root in roots if root.is_dir() and (ctx.home in root.parents)]


def list_dir(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    if target == ctx.home:
        parts, outcomes = [], []
        for root in _safe_roots(ctx):
            outcome = _call(ctx, "machine.list_directory", {"path": str(root)})
            outcomes.append(outcome)
            parts.append(f"[{root.name}]\n{outcome.text[:1200]}")
        if not outcomes:
            return ToolOutcome(text="no readable folders in your home", ok=False, status="no_results")
        return _merge(outcomes, "\n\n".join(parts))
    return _call(ctx, "machine.list_directory", {"path": str(target)})


def _query_words(query: str) -> list[str]:
    words = [w for w in re.findall(r"[\w\-]+", str(query or "").lower()) if len(w) > 1 and w not in _STOP]
    seen: list[str] = []
    for word in sorted(words, key=len, reverse=True):
        if word not in seen:
            seen.append(word)
    return seen[:_FIND_WORDS]


def find(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    words = _query_words(args.get("query") or args.get("name") or "")
    if not words:
        return ToolOutcome(text="tell me a word from the file name to look for", ok=False, status="missing_argument")
    outcomes = [_call(ctx, "machine.find_file", {"name": word}) for word in words]
    hits: list[str] = []
    for outcome in outcomes:
        for line in outcome.text.splitlines():
            if line.startswith("- ") and line not in hits:
                hits.append(line)
    if hits:
        return _merge(outcomes, "\n".join(hits[:20]))
    return _merge(outcomes, "nothing found for: " + ", ".join(words))


def read_file(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    if target.suffix.lower() == ".pdf":
        return _call(ctx, "pdf.extract_text", {"path": str(target), "max_pages": 5})
    return _call(ctx, "machine.read_file", {"path": str(target)})


def largest(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    return _call(ctx, "machine.find_largest", {"path": str(target)})


def disk_usage(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return _call(ctx, "machine.disk_usage", {})


def system_info(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    specs = _call(ctx, "machine.inspect_specs", {})
    procs = _call(ctx, "machine.list_processes", {})
    return _merge([specs, procs], f"{specs.text[:1500]}\n\n{procs.text[:1400]}")


def propose_change(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Never executes. The proposal waits for the owner; the loop's answer says so."""
    what = " ".join(str(args.get("what") or args.get("change") or "").split())[:400]
    proposal = {"what": what or "(unspecified change)", "status": "pending_approval", "executed": False}
    ctx.proposals.append(proposal)
    ctx.receipts.append(
        {
            "tool_name": "propose_change",
            "mode": "pending_approval",
            "status": "pending_approval",
            "execution": {"executed": False, "ok": True, "status": "pending_approval"},
            "lane": "clean_local_loop",
        }
    )
    return ToolOutcome(
        text="proposal shown to the owner; waiting for their approval (nothing changed yet)",
        ok=True,
        status="pending_approval",
        proposal=proposal,
    )


BUILTIN_TOOLS = (
    ("list_dir", '{"tool": "list_dir", "path": "Downloads"}  list a folder (path relative to home; "" = home)', list_dir),
    ("find", '{"tool": "find", "query": "boarding"}  find files whose name contains the words', find),
    ("read_file", '{"tool": "read_file", "path": "Desktop/todo.txt"}  read a text, csv, md or pdf file', read_file),
    ("largest", '{"tool": "largest", "path": ""}  biggest files under a folder', largest),
    ("disk_usage", '{"tool": "disk_usage"}  how full the disk is', disk_usage),
    ("system_info", '{"tool": "system_info"}  computer specs, memory, running processes', system_info),
    ("propose_change", '{"tool": "propose_change", "what": "..."}  ask the owner to approve a change (write, move, rename, delete)', propose_change),
)


PENDING_NOTICE = "This is waiting for your approval; nothing has been changed yet."


def pending_proposal_notice(answer: str, result: Any) -> str:
    """Answer gate: a turn that proposed a change always says the change has not happened."""
    if not getattr(result, "proposals", None) or "approv" in str(answer or "").lower():
        return answer
    return f"{str(answer or '').rstrip()}\n\n{PENDING_NOTICE}".strip()


def install_builtin_tools(registry: Registry) -> None:
    for name, doc, run in BUILTIN_TOOLS:
        registry.register_tool(name, doc, run)
    registry.register_answer_gate("pending_proposal_notice", pending_proposal_notice)


__all__ = [
    "BUILTIN_TOOLS",
    "PENDING_NOTICE",
    "OutsideHome",
    "home_dir",
    "install_builtin_tools",
    "pending_proposal_notice",
    "scope_path",
]
