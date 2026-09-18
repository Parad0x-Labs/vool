from __future__ import annotations

import re
from typing import Any


def clean_hive_title(raw: str) -> str:
    title = re.sub(
        r"^(?:create\s+(?:a\s+)?(?:hive\s+)?task\s*[-:—]*\s*)",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    title = re.sub(r"^[-:—]+\s*", "", title).strip()
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title or raw


def extract_hive_topic_create_draft(agent: Any, text: str) -> dict[str, Any] | None:
    clean = " ".join(str(text or "").split()).strip()
    lowered = clean.lower()
    if not agent._looks_like_hive_topic_create_request(lowered):
        return None

    sections = {
        "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\b(?:exact\s+)?summary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|(?:exact\s+)?summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\b(?:exact\s+)?summary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "summary": re.search(r"\b(?:exact\s+)?summary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", clean, re.IGNORECASE),
    }
    title = ""
    if sections["title"] is not None:
        title = str(sections["title"].group(1) or "")
    elif sections["task"] is not None:
        title = str(sections["task"].group(1) or "")
    elif ":" in clean:
        title = clean.rsplit(":", 1)[-1]
    else:
        title = re.sub(r"^.*?\bhive\b[?!.,:;-]*\s*", "", clean, flags=re.IGNORECASE)
    title = re.sub(r"^(?:name it|title|call it|called)\b\s*[:=-]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"^(?:(?:ok\s+)?(?:lets?|let'?s|can you|please|pls|now)\s+)*"
        r"(?:create|make|start|open|add)\s+"
        r"(?:(?:a|the|new|hive|brain hive|this)\s+)*"
        r"(?:task|topic|thread)\s*"
        r"(?:(?:on|in|for|to|at)\s+(?:(?:the\s+)?(?:hive|hive mind|brain hive))\s*)?",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip().lstrip("-–—:;/.,!? ")
    if not title:
        for prefix in ("create task", "create new task", "create hive task", "new task", "add task"):
            if clean.lower().startswith(prefix):
                title = clean[len(prefix):].strip().lstrip("-:–/")
                break
    if re.match(r"^.{0,30}---+", title):
        title = re.sub(r"^.{0,30}---+\s*", "", title).strip()
    if " - " in title and len(title.split(" - ", 1)[1].strip()) > 15:
        title = title.split(" - ", 1)[1].strip()
    title = re.sub(r"^(?:task|goal|summary)\s*[:=-]\s*", "", title, flags=re.IGNORECASE).strip()
    title = agent._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))

    summary = ""
    if sections["summary"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
    elif sections["goal"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["goal"].group(1) or "").split()).strip().strip("."))
    if not summary and title:
        summary = title

    topic_tags: list[str] = []
    if sections["tags"] is not None:
        raw_tags = str(sections["tags"].group(1) or "")
        topic_tags = [
            normalized
            for normalized in (
                agent._normalize_hive_topic_tag(item)
                for item in re.split(r"[,;|/]+", raw_tags)
            )
            if normalized
        ][:8]
    if not topic_tags and title:
        topic_tags = agent._infer_hive_topic_tags(title)

    return {
        "title": title[:180],
        "summary": summary[:4000],
        "topic_tags": topic_tags[:8],
        "auto_start_research": agent._wants_hive_create_auto_start(clean),
    }


def extract_original_hive_topic_create_draft(agent: Any, text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    compact = " ".join(raw.split()).strip()
    if not agent._looks_like_hive_topic_create_request(compact.lower()):
        return None
    sections = {
        "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\b(?:exact\s+)?summary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|(?:exact\s+)?summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\b(?:exact\s+)?summary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "summary": re.search(r"\b(?:exact\s+)?summary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", compact, re.IGNORECASE),
    }
    title = ""
    if sections["title"] is not None:
        title = str(sections["title"].group(1) or "")
    elif sections["task"] is not None:
        title = str(sections["task"].group(1) or "")
    elif ":" in compact:
        title = compact.rsplit(":", 1)[-1]
    title = re.sub(r"^(?:task|title|name it|call it|called)\s*[:=-]\s*", "", title, flags=re.IGNORECASE).strip()
    title = agent._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))
    summary = ""
    if sections["summary"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
    elif sections["goal"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["goal"].group(1) or "").split()).strip().strip("."))
    if not summary and title:
        summary = title
    topic_tags: list[str] = []
    if sections["tags"] is not None:
        raw_tags = str(sections["tags"].group(1) or "")
        topic_tags = [
            normalized
            for normalized in (
                agent._normalize_hive_topic_tag(item)
                for item in re.split(r"[,;|/]+", raw_tags)
            )
            if normalized
        ][:8]
    if not topic_tags and title:
        topic_tags = agent._infer_hive_topic_tags(title)
    if not title:
        return None
    return {
        "title": title[:180],
        "summary": summary[:4000],
        "topic_tags": topic_tags[:8],
        "auto_start_research": agent._wants_hive_create_auto_start(compact),
    }
