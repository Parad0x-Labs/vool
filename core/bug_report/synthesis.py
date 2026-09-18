"""Reproduction synthesis: local/model-free by default; the LLM never owns sanitization.

``synthesize_local`` builds the report body deterministically from typed, already-
sanitized fields — no model is involved, and it must keep working with every cloud path
disabled. ``synthesize_with_model`` is the OPT-IN cloud path: it accepts ONLY
SanitizedMaterial (a type whose constructor re-runs the outbound scanner, so handing it
unsanitized text fails closed), and the model's output is restricted to the known section
set and re-scanned before it can be used; anything unsafe falls back to the local
rendering. A model may restructure sanitized material; it may never introduce content.
"""
from __future__ import annotations

import re

from core.bug_report.redaction import redact_text
from core.bug_report.scanner import scan_text
from core.bug_report.schema import SanitizedMaterial

MAX_TITLE_CHARS = 120

_SECTION_HEADING_RE = re.compile(r"^## .+$")


def cloud_synthesis_enabled() -> bool:
    from core.runtime_flags import flag_enabled

    return flag_enabled("bug_report_cloud_synthesis")


def _clean_title(text: str) -> str:
    sanitized, _summary = redact_text(str(text or ""))
    sanitized = sanitized.strip()
    if len(sanitized) > MAX_TITLE_CHARS:
        sanitized = sanitized[:MAX_TITLE_CHARS].rstrip() + "…"
    return sanitized


def synthesize_local(material: SanitizedMaterial) -> tuple[str, str]:
    """Deterministic, model-free (title, body) rendering of sanitized material."""
    payload = material.payload
    category = _clean_title(payload.get("category", "")).lower().replace(" ", "-")
    title_text = _clean_title(payload.get("title", "") or "untitled issue")
    title = f"[bug-report] {category}: {title_text}" if category else f"[bug-report] {title_text}"

    lines: list[str] = []

    environment = payload.get("environment") or {}
    if environment:
        lines.append("## Environment")
        lines.append(f"- VOOL version: {environment.get('version', 'unknown')}")
        source = environment.get("source_kind", "unknown")
        short_sha = str(environment.get("source_sha", ""))[:12]
        sha_note = f" {short_sha}" if short_sha else ""
        dirty = ", dirty tree" if environment.get("source_dirty") else ""
        lines.append(f"- source: {source}{sha_note}{dirty}")
        lines.append(f"- OS: {environment.get('os', 'unknown')} | arch: {environment.get('arch', 'unknown')}"
                     f" | Python {environment.get('python', 'unknown')}")
        lines.append("")

    if payload.get("expected"):
        lines.append("## Expected")
        lines.append(str(payload["expected"]))
        lines.append("")

    if payload.get("actual"):
        lines.append("## Actual")
        lines.append(str(payload["actual"]))
        lines.append("")

    repro = payload.get("repro_steps") or []
    if repro:
        lines.append("## Minimal reproduction")
        for index, step in enumerate(repro, start=1):
            lines.append(f"{index}. {step}")
        lines.append("")

    error = payload.get("error")
    if error:
        lines.append("## Error / stack")
        lines.append("```")
        lines.append("Traceback (most recent call last):")
        for frame in error.get("frames", []):
            lines.append(f"  File \"{frame.get('file', '')}\", line {frame.get('line', 0)}, in {frame.get('function', '')}")
        lines.append(f"{error.get('exc_type', 'Error')}: {error.get('message', '')}".rstrip(": "))
        lines.append("```")
        lines.append("")

    components = payload.get("components") or {}
    if components.get("lanes") or components.get("tools") or components.get("models"):
        lines.append("## Involved")
        lines.append(f"- lanes: {', '.join(components.get('lanes', [])) or '—'}")
        lines.append(f"- tools: {', '.join(components.get('tools', [])) or '—'}")
        lines.append(f"- models: {', '.join(components.get('models', [])) or '—'}")
        lines.append("")

    for excerpt in payload.get("logs") or []:
        meta = f" (truncated, original {excerpt.get('original_line_count', '?')} lines)" if excerpt.get("truncated") else ""
        lines.append(f"### {excerpt.get('source', 'log')}{meta}")
        lines.append("```")
        lines.extend(str(line) for line in excerpt.get("lines", []))
        lines.append("```")
        lines.append("")

    flags = payload.get("flags")
    if flags:
        lines.append("## Flags")
        for name in sorted(flags):
            lines.append(f"- {name}: {str(flags[name]).lower()}")
        lines.append("")

    fingerprint = payload.get("fingerprint", "")
    lines.append("---")
    lines.append(f"bug-report-fingerprint: {fingerprint}")
    lines.append("")
    return title, "\n".join(lines)


_SECTION_HEADING_RE = re.compile(r"^## .+$")


def _extract_allowed_sections(text: str, allowed_headings: set[str]) -> str:
    """Keep only section blocks that exist in the local rendering; drop everything else."""
    kept: list[str] = []
    current: list[str] | None = None
    for line in str(text).splitlines():
        if _SECTION_HEADING_RE.match(line):
            if current:
                kept.extend(current)
                kept.append("")
            current = [line] if line in allowed_headings else None
            continue
        if current is not None:
            current.append(line)
    if current:
        kept.extend(current)
    return "\n".join(kept).strip()


def synthesize_with_model(
    material: SanitizedMaterial,
    *,
    model_fn,
    enabled: bool,
) -> tuple[str, str]:
    """Opt-in cloud restructuring of ALREADY-sanitized material.

    ``model_fn`` receives the sanitized payload dict. Its text output may only carry the
    sections the local rendering built from sanitized fields, is re-scanned, and any
    unsafe finding discards it in favour of the deterministic local rendering -- so a
    hostile or leaky model cannot widen what leaves the machine.
    """
    title, local_body = synthesize_local(material)
    if not enabled:
        return title, local_body
    try:
        candidate = model_fn(material.payload)
    except Exception:
        return title, local_body
    allowed = {line for line in local_body.splitlines() if _SECTION_HEADING_RE.match(line)}
    fingerprint = str(material.payload.get("fingerprint", ""))
    body = _extract_allowed_sections(str(candidate), allowed)
    if body:
        body = f"{body}\n\n---\nbug-report-fingerprint: {fingerprint}"
    else:
        body = local_body
    findings = scan_text(body)
    if findings:
        return title, local_body
    return title, body


__all__ = ["MAX_TITLE_CHARS", "cloud_synthesis_enabled", "synthesize_local", "synthesize_with_model"]
