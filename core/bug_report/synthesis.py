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

#: Plain-words labels for why an answer was wrong (schema.ANSWER_REASONS).
ANSWER_REASON_LABELS = {
    "wrong": "Wrong or made up",
    "outdated": "Out of date",
    "not_what_i_asked": "Didn't do what I asked",
    "unsafe": "Unsafe or rude",
    "other": "Other",
}

_MACHINE_LABELS = (
    ("os_version", "OS version"),
    ("chip", "chip"),
    ("cpu_cores", "CPU cores"),
    ("ram_gb", "RAM (GB)"),
    ("gpu", "GPU"),
    ("gpu_memory_gb", "GPU memory (GB)"),
    ("ollama_version", "Ollama"),
    ("install_kind", "install"),
)
_HEALTH_LABELS = (
    ("local_model_server", "local model server answering"),
    ("local_models_installed", "local text models installed"),
    ("report_model_installed", "reported model installed"),
    ("disk_free", "free disk"),
    ("providers_cut_off", "providers currently cut off"),
)
# A report lands in a public issue tracker: "@name" in a user's text would notify a stranger.
_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9])")


def _no_mentions(text: str) -> str:
    return _MENTION_RE.sub("@\u200b", str(text))


def _quoted(text: str) -> list[str]:
    return ["> " + line if line else ">" for line in _no_mentions(text).splitlines()] or [">"]


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
    kind = str(payload.get("kind") or "bug")
    prefix = "[wrong-answer]" if kind == "wrong_answer" else "[bug-report]"
    show_category = category and kind != "wrong_answer"  # the prefix already says it
    title = f"{prefix} {category}: {title_text}" if show_category else f"{prefix} {title_text}"

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
        machine = payload.get("machine") or {}
        for key, label in _MACHINE_LABELS:
            if key in machine:
                lines.append(f"- {label}: {machine[key]}")
        lines.append("")

    health = payload.get("health") or {}
    if health:
        lines.append("## Health")
        for key, label in _HEALTH_LABELS:
            if key in health:
                lines.append(f"- {label}: {health[key]}")
        lines.append("")

    answer = payload.get("answer") or {}
    if answer:
        lines.append("## Wrong answer")
        reason = str(answer.get("reason") or "other")
        lines.append(f"- reason: {ANSWER_REASON_LABELS.get(reason, reason)}")
        if "excerpt" in answer:
            where = "the part of the answer the user selected" if answer.get("excerpt_source") == "selection" else "the whole answer"
            lines.append(f"- quoted: {where}")
            lines.append("")
            lines.extend(_quoted(str(answer.get("excerpt") or "")))
        if answer.get("question"):
            lines.append("")
            lines.append("The user's question (included by the user):")
            lines.append("")
            lines.extend(_quoted(str(answer["question"])))
        lines.append("")

    wrong_answer = kind == "wrong_answer"
    if payload.get("expected"):
        lines.append("## What it should have said" if wrong_answer else "## Expected")
        lines.append(_no_mentions(payload["expected"]))
        lines.append("")

    if payload.get("actual"):
        lines.append("## What was wrong" if wrong_answer else "## Actual")
        lines.append(_no_mentions(payload["actual"]))
        lines.append("")

    receipt = payload.get("receipt") or {}
    if receipt:
        lines.append("## Answer receipt")
        lines.append(f"- record: {receipt.get('record_state', 'unknown')}"
                     + (f" ({', '.join(receipt.get('state_reasons') or [])})" if receipt.get("state_reasons") else ""))
        lines.append(f"- model: {receipt.get('model') or 'unknown'} | provider: {receipt.get('provider') or 'unknown'}")
        elapsed = str(receipt.get("elapsed_ms", "unknown"))
        elapsed = f"{elapsed} ms" if elapsed.isdigit() else elapsed
        lines.append(f"- tokens: {receipt.get('tokens', 'unknown')} | time: {elapsed}"
                     f" | web sources: {receipt.get('sources_count', 'unknown')}")
        lines.append(f"- saved answer intact: {receipt.get('integrity_ok', 'unknown')}"
                     f" | action records agree: {receipt.get('witness_consistent', 'unknown')}")
        actions = receipt.get("actions") or []
        if actions:
            lines.append(f"- actions ({len(actions)}):")
            for action in actions:
                mark = "ok" if action.get("ok") else "failed"
                lines.append(f"  - {action.get('name', '')} [{action.get('kind', '')}] {action.get('status', '')} ({mark})")
        else:
            lines.append("- actions: none recorded")
        if receipt.get("fault_codes"):
            lines.append(f"- fault codes: {', '.join(receipt['fault_codes'])}")
        lines.append("")

    repro = payload.get("repro_steps") or []
    if repro:
        lines.append("## Minimal reproduction")
        for index, step in enumerate(repro, start=1):
            lines.append(f"{index}. {_no_mentions(step)}")
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


__all__ = ["ANSWER_REASON_LABELS", "MAX_TITLE_CHARS", "cloud_synthesis_enabled", "synthesize_local", "synthesize_with_model"]
