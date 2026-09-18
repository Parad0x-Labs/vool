from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime import hive_topic_drafting
from core.agent_runtime.hive_topic_draft_builder import (
    build_hive_create_pending_variants as builder_build_hive_create_pending_variants,
)
from core.agent_runtime.hive_topic_draft_duplicate_detection import (
    check_hive_duplicate as duplicate_check_hive_duplicate,
)
from core.agent_runtime.hive_topic_draft_intents import (
    wants_hive_create_auto_start as intent_wants_hive_create_auto_start,
)


class _DraftingAgent:
    def _strip_wrapping_quotes(self, text: str) -> str:
        return text.strip().strip('"').strip("'")

    def _normalize_hive_topic_tag(self, text: str) -> str:
        return str(text or "").strip().lower().replace(" ", "_")

    def _infer_hive_topic_tags(self, text: str) -> list[str]:
        lowered = str(text or "").lower()
        if "receipt" in lowered:
            return ["proof", "receipts"]
        if "watcher" in lowered:
            return ["watcher", "ux"]
        return ["general"]

    def _wants_hive_create_auto_start(self, text: str) -> bool:
        return hive_topic_drafting.wants_hive_create_auto_start(text)

    def _looks_like_hive_topic_drafting_request(self, text: str) -> bool:
        return hive_topic_drafting.looks_like_hive_topic_drafting_request(self, text)

    def _looks_like_hive_topic_create_request(self, text: str) -> bool:
        return hive_topic_drafting.looks_like_hive_topic_create_request(self, text)

    def _clean_hive_title(self, text: str) -> str:
        return hive_topic_drafting.clean_hive_title(text)

    def _prepare_public_hive_topic_copy(
        self,
        *,
        raw_input: str,
        title: str,
        summary: str,
        mode: str,
    ) -> dict[str, object]:
        return {
            "ok": True,
            "title": title.strip(),
            "summary": summary.strip(),
            "preview_note": f"{mode} preview",
        }

    def _normalize_hive_create_variant(
        self,
        *,
        title: str,
        summary: str,
        topic_tags: list[str],
        auto_start_research: bool,
        preview_note: str = "",
    ) -> dict[str, object]:
        return hive_topic_drafting.normalize_hive_create_variant(
            self,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            auto_start_research=auto_start_research,
            preview_note=preview_note,
        )

    def _extract_original_hive_topic_create_draft(self, text: str) -> dict[str, object] | None:
        return {
            "title": "Please fix proof receipts now",
            "summary": "tell me what to fix before we publish",
            "topic_tags": ["proof", "receipts"],
            "auto_start_research": False,
        }


def test_extract_hive_topic_create_draft_parses_structured_fields() -> None:
    agent = _DraftingAgent()

    draft = hive_topic_drafting.extract_hive_topic_create_draft(
        agent,
        "create hive task: Task: better watcher UX Goal: cleaner pending states Summary: make the preview easier to scan Tags: watcher, ux",
    )

    assert draft is not None
    assert draft["title"] == "better watcher UX"
    assert draft["summary"] == "make the preview easier to scan"
    assert draft["topic_tags"] == ["watcher", "ux"]
    assert draft["auto_start_research"] is False


def test_build_hive_create_pending_variants_keeps_original_variant_when_distinct() -> None:
    agent = _DraftingAgent()

    result = hive_topic_drafting.build_hive_create_pending_variants(
        agent,
        raw_input='create hive task: "Fix proof receipts now" summary: compare receipt emission and dashboard proof cards tags: proof, receipts',
        draft={
            "title": "create hive task - Fix proof receipts now",
            "summary": "compare receipt emission and dashboard proof cards",
            "topic_tags": ["proof", "receipts"],
            "auto_start_research": False,
        },
        task_id="task-123",
    )

    assert result["ok"] is True
    pending = result["pending"]
    assert pending["default_variant"] == "improved"
    assert set(pending["variants"]) == {"improved", "original"}
    assert pending["variants"]["improved"]["preview_note"] == "improved preview"
    assert pending["variants"]["original"]["preview_note"] == "original preview"
    assert hive_topic_drafting.build_hive_create_pending_variants is builder_build_hive_create_pending_variants


def test_check_hive_duplicate_prefers_recent_overlap() -> None:
    agent = SimpleNamespace(
        public_hive_bridge=SimpleNamespace(
            list_public_topics=lambda limit=50: [
                {
                    "topic_id": "old-topic",
                    "title": "Fix proof receipts",
                    "summary": "stale summary",
                    "updated_at": "2020-01-01T00:00:00",
                },
                {
                    "topic_id": "recent-topic",
                    "title": "Fix proof receipts",
                    "summary": "compare receipt emission and dashboard proof cards",
                    "updated_at": "2099-01-01T00:00:00",
                },
            ]
        )
    )

    duplicate = hive_topic_drafting.check_hive_duplicate(
        agent,
        "Fix proof receipts",
        "compare receipt emission and dashboard proof cards",
    )

    assert duplicate is not None
    assert duplicate["topic_id"] == "recent-topic"
    assert hive_topic_drafting.check_hive_duplicate is duplicate_check_hive_duplicate


def test_looks_like_hive_topic_drafting_request_blocks_script_first_prompt() -> None:
    assert hive_topic_drafting.looks_like_hive_topic_drafting_request(
        None,
        "give me the perfect script first and then i decide if i want to push that to the hive",
    )


def test_wants_hive_create_auto_start_detects_research_phrase() -> None:
    assert hive_topic_drafting.wants_hive_create_auto_start(
        "create it and start researching right away",
    )
    assert hive_topic_drafting.wants_hive_create_auto_start is intent_wants_hive_create_auto_start
