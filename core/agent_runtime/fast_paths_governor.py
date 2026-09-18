"""Resource-governor chat surface: "why is my Mac roaring?" answered with real numbers, and
"free up memory" that actually frees it.

The failure this closes: a big model sits resident in Ollama (loaded by VOOL or ANY other
client), the machine swaps and the fans roar — and the product can neither explain it nor fix
it. Both handlers are deterministic reads/actions on the governor (no model prose, no guesses):
the hog question renders resource_hog_report(), the free action runs free_up_memory() and shows
before/after. Owner-local only — machine state and reclaim are owner powers.
"""
from __future__ import annotations

import re

_HOG_QUESTION_RE = re.compile(
    r"(?:why|what)\s+(?:the\s+\w+\s+)?is\s+(?:my|the|this)\s+(?:mac|imac|macbook|machine|computer|laptop|pc)\s+"
    r"(?:so\s+|always\s+|still\s+)*(?:slow|loud|hot|roaring|screaming|struggling|freezing|lagging|swapping)"
    r"|(?:my|the)\s+(?:mac|imac|macbook|machine|computer|laptop|pc)\s+is\s+(?:so\s+|always\s+|still\s+)*"
    r"(?:slow|loud|hot|roaring|screaming|struggling|freezing|lagging|swapping)"
    r"|fans?\s+(?:are\s+|is\s+|so\s+)*(?:loud|roaring|screaming|going\s+crazy|spinning)"
    r"|what(?:'s|\s+is)\s+(?:eating|using|hogging|taking)\s+(?:all\s+)?(?:my\s+|the\s+)?(?:ram|memory)",
    re.IGNORECASE,
)
_FREE_MEMORY_RE = re.compile(
    r"free\s+up\s+(?:some\s+)?(?:ram|memory)"
    r"|unload\s+(?:the\s+|all\s+)?models?\b"
    r"|reclaim\s+(?:some\s+)?(?:ram|memory)"
    r"|give\s+(?:me\s+)?(?:back\s+)?(?:my\s+)?ram\b",
    re.IGNORECASE,
)


def maybe_handle_resource_hog_question(user_input: str, *, owner_local: bool) -> str | None:
    """"Why is my Mac slow/loud/roaring?" -> the measured answer: pressure, swap, and the hog."""
    text = " ".join(str(user_input or "").split())
    if not text or not _HOG_QUESTION_RE.search(text):
        return None
    if not owner_local:
        return "Machine diagnostics are owner-only, so I can't report this host's numbers from here."
    try:
        from core.resource_governor import resource_hog_report

        report = resource_hog_report()
    except Exception:
        return "I couldn't read the machine's live memory state just now — try again in a moment."
    lines: list[str] = []
    hog = report.get("hog")
    if hog and report.get("squeezed"):
        owner_note = (
            "That's a model VOOL can route to."
            if report.get("hog_is_vool_routable")
            else "VOOL doesn't run that model itself, so another app loaded it."
        )
        lines.append(
            f"Your Mac is working hard because **{hog['name']}** (~{hog['size_gb']} GB) is loaded in "
            f"Ollama — on a {report['total_gb']} GB machine that leaves ~{report['available_gb']} GB free "
            f"(pressure: {report['pressure']}, swap in use: {report['swap_used_gb']} GB). {owner_note}"
        )
    elif report.get("squeezed"):
        lines.append(
            f"Memory is tight: ~{report['available_gb']} GB free of {report['total_gb']} GB "
            f"(pressure: {report['pressure']}, swap in use: {report['swap_used_gb']} GB)."
        )
    else:
        lines.append(
            f"Nothing looks squeezed right now: ~{report['available_gb']} GB free of "
            f"{report['total_gb']} GB, pressure {report['pressure']}, swap {report['swap_used_gb']} GB."
        )
    others = [m for m in (report.get("resident_models") or [])[1:] if m.get("size_gb", 0) >= 0.3]
    if others:
        lines.append("Also loaded: " + ", ".join(f"{m['name']} (~{m['size_gb']} GB)" for m in others) + ".")
    if report.get("comfyui_running"):
        lines.append("The image engine (ComfyUI) is running too (~5 GB while resident).")
    if report.get("resident_models") or report.get("comfyui_running"):
        lines.append('Say "free up memory" and I\'ll unload it all — each piece reloads on its next use.')
    return "\n".join(lines)


def maybe_handle_free_memory_command(user_input: str, *, owner_local: bool) -> str | None:
    """"Free up memory" -> actually unload every resident model (+ idle ComfyUI), with numbers."""
    text = " ".join(str(user_input or "").split())
    if not text or not _FREE_MEMORY_RE.search(text):
        return None
    if not owner_local:
        return "Freeing this machine's memory is owner-only, so I can't do it from here."
    try:
        from core.resource_governor import free_up_memory

        result = free_up_memory()
    except Exception:
        return "I couldn't reclaim memory just now — the local model server didn't respond."
    parts: list[str] = []
    if result.get("unloaded"):
        parts.append("unloaded " + ", ".join(result["unloaded"]))
    if result.get("stopped_comfyui"):
        parts.append("stopped the idle image engine")
    if not parts:
        return (
            f"Nothing to free — no models are loaded right now "
            f"(~{result.get('available_after_gb', 0)} GB already free)."
        )
    return (
        "Done — " + " and ".join(parts) + ". "
        f"Free memory went from ~{result.get('available_before_gb', 0)} GB to "
        f"~{result.get('available_after_gb', 0)} GB. Everything reloads automatically on its next use."
    )


__all__ = ["maybe_handle_free_memory_command", "maybe_handle_resource_hog_question"]
