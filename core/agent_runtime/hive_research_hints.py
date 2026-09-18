from __future__ import annotations

import re
from typing import Any

_HIVE_TOPIC_FULL_ID_RE = re.compile(r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", re.IGNORECASE)
_HIVE_TOPIC_SHORT_ID_RE = re.compile(r"#\s*([0-9a-f]{8,12})\b", re.IGNORECASE)


def extract_hive_topic_hint(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    full_match = _HIVE_TOPIC_FULL_ID_RE.search(clean)
    if full_match:
        return str(full_match.group(1) or "").strip().lower()
    short_match = _HIVE_TOPIC_SHORT_ID_RE.search(clean)
    if short_match:
        return str(short_match.group(1) or "").strip().lower()
    bare_short_match = re.fullmatch(r"[#\s]*([0-9a-f]{8,12})[.!?]*", clean, re.IGNORECASE)
    if bare_short_match:
        return str(bare_short_match.group(1) or "").strip().lower()
    return ""


def history_hive_topic_hints(agent: Any, history: list[dict[str, Any]] | None) -> list[str]:
    hints: list[str] = []
    for message in reversed(list(history or [])[-8:]):
        content = str(message.get("content") or "").strip()
        hint = agent._extract_hive_topic_hint(content)
        if hint:
            hints.append(hint)
    return hints


def looks_like_hive_research_followup(
    agent: Any,
    lowered: str,
    *,
    topic_hint: str,
    has_pending_topics: bool,
    shown_titles: list[str],
    history_has_task_list: bool,
) -> bool:
    text = str(lowered or "").strip().lower()
    normalized_text = agent._normalize_hive_topic_text(text)
    if topic_hint:
        bare_hint = f"#{topic_hint}"
        compact_text = re.sub(r"\s+", "", text.rstrip(".!?"))
        if compact_text in {topic_hint, bare_hint}:
            return True
        if any(
            phrase in text
            for phrase in (
                "this one",
                "that one",
                "go with this one",
                "lets go with this one",
                "let's go with this one",
                "start this",
                "start that",
                "start #",
                "claim #",
                "take this",
                "take #",
                "claim this",
                "pick this",
                "pick #",
                "work on #",
                "research #",
                "do #",
            )
        ):
            return True
        return bool(
            bare_hint in compact_text
            and any(
                phrase in text
                for phrase in (
                    "full research",
                    "research on this",
                    "research this",
                    "do this in full",
                    "do all step by step",
                    "lets do this",
                    "let's do this",
                    "do this",
                    "start this",
                    "start that",
                    "work on this",
                    "work on that",
                    "deliver to hive",
                    "deliver it to hive",
                    "post it to hive",
                    "submit it to hive",
                    "pls",
                    "please",
                    "full",
                )
            )
        )
    if (has_pending_topics or history_has_task_list) and shown_titles:
        normalized_titles = [
            agent._normalize_hive_topic_text(str(title or ""))
            for title in list(shown_titles or [])
            if str(title or "").strip()
        ]
        if normalized_text and normalized_text in normalized_titles:
            return True
    if (has_pending_topics or history_has_task_list) and any(
        phrase in text
        for phrase in (
            "yes",
            "ok",
            "okay",
            "ok let's go",
            "ok lets go",
            "lets go",
            "let's go",
            "go ahead",
            "do it",
            "do one",
            "start it",
            "take it",
            "claim it",
            "work on it",
            "review it",
            "review this",
            "look into it",
            "research it",
            "pick one",
            "do all step by step",
            "deliver to hive",
            "deliver it to hive",
            "post it to hive",
            "submit it to hive",
            "proceed",
            "carry on",
            "continue",
            "do all",
            "start working",
            "all good",
            "proceed with next steps",
            "proceed with that",
            "just do it",
            "deliver it",
            "submit it",
        )
    ):
        return True
    if (has_pending_topics or history_has_task_list) and any(
        phrase in text
        for phrase in (
            "first one",
            "1st one",
            "second one",
            "2nd one",
            "third one",
            "3rd one",
            "take the first one",
            "take the second one",
            "review the first one",
            "review the second one",
            "review the problem",
            "check the problem",
            "help with this",
            "help with that",
            "do all step by step",
        )
    ):
        return True
    if any(
        phrase in text
        for phrase in (
            "go with this one",
            "lets go with this one",
            "let's go with this one",
            "start this one",
            "start that one",
            "take this one",
            "take that one",
            "claim this one",
            "claim that one",
        )
    ) and any(marker in text for marker in ("[researching", "[open", "[disputed", "topic", "task", "hive", "#")):
        return True
    if "hive" in text and any(phrase in text for phrase in ("pick one", "start the hive research", "start hive research", "pick a task", "choose one")):
        return True
    return bool("research" in text and "pick one" in text)
