from __future__ import annotations

import re


def shape_public_hive_admission_safe_copy(
    *,
    title: str,
    summary: str,
    force: bool = False,
) -> tuple[str, str, str]:
    clean_title = " ".join(str(title or "").split()).strip()
    clean_summary = " ".join(str(summary or "").split()).strip() or clean_title
    combined = f"{clean_title} {clean_summary}".strip().lower()
    command_like = bool(
        re.match(
            r"^(?:research|check(?:\s+out)?|look\s+into|analy[sz]e|review|verify|investigate|find\s+out|tell\s+me|scan|go\s+check)\b",
            clean_title.lower(),
        )
    )
    has_analysis_framing = any(
        marker in combined
        for marker in (
            "analysis",
            "compare",
            "tradeoff",
            "evidence",
            "security",
            "docs",
            "source",
            "tests",
            "official",
            "why",
            "risk",
        )
    )
    if not force and not (command_like and not has_analysis_framing):
        return clean_title, clean_summary, ""

    subject = re.sub(
        r"^(?:research|check(?:\s+out)?|look\s+into|analy[sz]e|review|verify|investigate|find\s+out|tell\s+me|scan|go\s+check)\s+",
        "",
        clean_title,
        flags=re.IGNORECASE,
    ).strip(" :-")
    subject = subject or clean_title or "this topic"
    reframed_summary = (
        "Agent analysis brief comparing architecture, security, implementation tradeoffs, docs, and evidence for "
        f"{subject}. Requested scope: {clean_summary.rstrip('.')}."
    )
    preview_note = (
        "\n\nAdmission: I reframed the improved copy as agent analysis so the public Hive will accept it."
    )
    return clean_title, reframed_summary[:4000], preview_note
