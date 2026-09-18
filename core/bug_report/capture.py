"""Bounded diagnostics capture: what may enter the pipeline at all.

Hard bounds, all enforced here before any storage: log excerpts are capped per source and
in total bytes; prose and repro steps are capped; conversation-kind sources are REFUSED
(message bodies never enter the bug-report pipeline); component identifiers must match the
strict id pattern. Environment capture records version, source SHA, OS/arch and Python --
never the hostname, never the username, never env values.
"""
from __future__ import annotations

import platform
from pathlib import Path

from core.app_version import VOOL_VERSION
from core.bug_report.allowlist import (
    parse_stack,
    require_identifier,
    sanitize_identifier,
    sanitize_log_line,
    sanitize_prose,
)
from core.bug_report.redaction import redact_text as _redact
from core.bug_report.schema import CaptureRefusedError, LogExcerpt, ReportEnvironment
from core.build_provenance import capture_build_source

# parse_stack / require_identifier / sanitize_identifier / sanitize_prose are re-exported
# here so callers have ONE capture-side import point; they are declared re-exports.
__all__ = [
    "MAX_LOG_LINES_PER_SOURCE",
    "MAX_LOG_SOURCES",
    "MAX_LOG_TOTAL_BYTES",
    "MAX_PROSE_CHARS",
    "MAX_REPRO_STEPS",
    "MAX_REPRO_STEP_CHARS",
    "capture_environment",
    "capture_logs",
    "parse_stack",
    "require_identifier",
    "sanitize_identifier",
    "sanitize_prose",
    "validate_repro_steps",
]

MAX_LOG_LINES_PER_SOURCE = 200
MAX_LOG_TOTAL_BYTES = 64 * 1024
MAX_LOG_SOURCES = 8
MAX_PROSE_CHARS = 4000
MAX_REPRO_STEPS = 12
MAX_REPRO_STEP_CHARS = 500

# A log source whose name or kind matches one of these can contain conversation turns;
# conversation material is not log material and is refused at the door.
_CONVERSATION_KINDS = (
    "conversation", "chat", "message", "session_log", "chat_history", "transcript", "dialogue",
)


def capture_environment(project_root: Path | None) -> ReportEnvironment:
    root = Path(project_root) if project_root is not None else None
    source = capture_build_source(root) if root is not None else {"source_kind": "unknown"}
    return ReportEnvironment(
        version=VOOL_VERSION,
        source_kind=str(source.get("source_kind", "unknown")),
        source_sha=str(source.get("commit_full", "") or ""),
        source_dirty=bool(source["dirty_state"]) if "dirty_state" in source else None,
        os=platform.system() or "unknown",
        arch=platform.machine() or "unknown",
        python=platform.python_version(),
    )


def _is_conversation_kind(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(kind in lowered for kind in _CONVERSATION_KINDS)


def capture_logs(log_sources, *, summary=None) -> tuple[LogExcerpt, ...]:
    """Bound, redact and shape-filter log sources. Refuses conversation-kind sources.

    When ``summary`` is given, the per-line redaction counts are absorbed into it so the
    draft's redaction summary reflects every stage, not only the prose fields.
    """
    sources = list(log_sources or [])
    if not sources:
        return ()
    if len(sources) > MAX_LOG_SOURCES:
        raise CaptureRefusedError(f"too many log sources: {len(sources)} > {MAX_LOG_SOURCES}")

    excerpts: list[LogExcerpt] = []
    budget = MAX_LOG_TOTAL_BYTES
    for source in sources:
        name = str(source.get("name", ""))
        kind = str(source.get("kind", ""))
        if _is_conversation_kind(name) or _is_conversation_kind(kind):
            raise CaptureRefusedError(
                "conversation-kind log source refused — message bodies never enter a bug report"
            )
        if not name or any(ch in name for ch in "/\\\0") or len(name) > 64:
            raise CaptureRefusedError(f"unsafe log source name refused: {name!r}")
        lines = [str(line) for line in (source.get("lines") or [])]
        original_count = len(lines)
        kept: list[str] = []
        dropped_shapes = 0
        truncated = False
        for raw_line in lines[-MAX_LOG_LINES_PER_SOURCE:]:
            sanitized, safe_shape = sanitize_log_line(raw_line, summary=summary)
            if not safe_shape:
                dropped_shapes += 1
                continue
            cost = len(sanitized.encode("utf-8")) + 1
            if cost > budget:
                truncated = True
                break
            budget -= cost
            kept.append(sanitized)
        if original_count > MAX_LOG_LINES_PER_SOURCE:
            truncated = True
        excerpts.append(
            LogExcerpt(
                source=name,
                lines=tuple(kept),
                truncated=truncated,
                original_line_count=original_count,
                dropped_lines=dropped_shapes + max(0, original_count - MAX_LOG_LINES_PER_SOURCE),
            )
        )
    return tuple(excerpts)


def validate_repro_steps(steps, *, summary=None) -> list[str]:
    values = [str(s) for s in (steps or [])]
    if len(values) > MAX_REPRO_STEPS:
        raise CaptureRefusedError(f"too many repro steps: {len(values)} > {MAX_REPRO_STEPS}")
    if summary is None:
        return [sanitize_prose(step, max_chars=MAX_REPRO_STEP_CHARS) for step in values]
    steps_out: list[str] = []
    for step in values:
        sanitized, step_summary = _redact(step)
        for rule, count in step_summary.rule_counts.items():
            summary.rule_counts[rule] = summary.rule_counts.get(rule, 0) + count
        summary.total_replacements += step_summary.total_replacements
        if len(sanitized) > MAX_REPRO_STEP_CHARS:
            sanitized = sanitized[:MAX_REPRO_STEP_CHARS].rstrip() + "…[truncated]"
        steps_out.append(sanitized)
    return steps_out
