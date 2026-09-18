from __future__ import annotations

from typing import Any


def format_hive_create_preview(
    agent: Any,
    *,
    pending: dict[str, Any],
    estimated_cost: float,
    dup_warning: str,
    preview_note: str,
) -> str:
    variants = {
        key: dict(value)
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    improved = dict(variants.get("improved") or {})
    original = dict(variants.get("original") or {})
    tag_line = ""
    improved_tags = [
        str(item).strip()
        for item in list(improved.get("topic_tags") or [])
        if str(item).strip()
    ][:6]
    if improved_tags:
        tag_line = f"\nTags: {', '.join(improved_tags)}"
    cost_line = f"\nEstimated reward pool: {estimated_cost:.1f} credits." if estimated_cost > 0 else ""
    if original or str(pending.get("original_blocked_reason") or "").strip():
        lines = [
            "Ready to post this to the public Hive:",
            "",
            "Improved draft (default):",
            f"**{str(improved.get('title') or '').strip()}**",
            f"Summary: {agent._preview_text_snippet(str(improved.get('summary') or '').strip())}",
        ]
        if tag_line:
            lines.append(tag_line.strip())
        if cost_line:
            lines.append(cost_line.strip())
        if preview_note:
            lines.append(preview_note.strip())
        if original:
            lines.extend(
                [
                    "",
                    "Original draft:",
                    f"**{str(original.get('title') or '').strip()}**",
                    f"Summary: {agent._preview_text_snippet(str(original.get('summary') or '').strip())}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Original draft:",
                    str(pending.get("original_blocked_reason") or "Blocked for privacy."),
                ]
            )
        if dup_warning:
            lines.append(dup_warning.strip())
        reply_line = "Reply: `send improved` / `no`." if not original else "Reply: `send improved` / `send original` / `no`."
        lines.extend(["", reply_line])
        return "\n".join(line for line in lines if line is not None)
    return (
        f"Ready to post this to the public Hive:\n\n"
        f"**{str(improved.get('title') or '').strip()}**{tag_line}{cost_line}{dup_warning}{preview_note}\n\n"
        f"Confirm? (yes / no)"
    )


def preview_text_snippet(text: str, *, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."
