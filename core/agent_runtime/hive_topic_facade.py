from __future__ import annotations

import re
from typing import Any

from core.agent_runtime import hive_followups as agent_hive_followups
from core.agent_runtime import hive_topics as agent_hive_topics


class HiveTopicFacadeMixin:
    def _maybe_handle_hive_topic_create_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_topic_create_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_create_confirmation(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_create_confirmation(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _has_pending_hive_create_confirmation(
        self,
        *,
        session_id: str,
        hive_state: dict[str, Any],
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_hive_topics.has_pending_hive_create_confirmation(
            self,
            session_id=session_id,
            hive_state=hive_state,
            source_context=source_context,
        )

    def _is_pending_hive_create_confirmation_input(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any] | None = None,
    ) -> bool:
        return agent_hive_topics.is_pending_hive_create_confirmation_input(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
            session_hive_state_fn=self._session_hive_state,
        )

    def _execute_confirmed_hive_create(
        self,
        pending: dict[str, Any],
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        user_input: str,
        variant: str,
    ) -> dict[str, Any]:
        return agent_hive_topics.execute_confirmed_hive_create(
            self,
            pending,
            task=task,
            session_id=session_id,
            source_context=source_context,
            user_input=user_input,
            variant=variant,
            research_topic_from_signal_fn=self._research_topic_from_signal,
        )

    def _check_hive_duplicate(self, title: str, summary: str) -> dict[str, Any] | None:
        return agent_hive_topics.check_hive_duplicate(self, title, summary)

    @staticmethod
    def _clean_hive_title(raw: str) -> str:
        return agent_hive_topics.clean_hive_title(raw)

    def _extract_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_hive_topic_create_draft(self, text)

    def _extract_original_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_original_hive_topic_create_draft(self, text)

    def _build_hive_create_pending_variants(
        self,
        *,
        raw_input: str,
        draft: dict[str, Any],
        task_id: str,
    ) -> dict[str, Any]:
        return agent_hive_topics.build_hive_create_pending_variants(
            self,
            raw_input=raw_input,
            draft=draft,
            task_id=task_id,
        )

    def _normalize_hive_create_variant(
        self,
        *,
        title: str,
        summary: str,
        topic_tags: list[str],
        auto_start_research: bool,
        preview_note: str = "",
    ) -> dict[str, Any]:
        return agent_hive_topics.normalize_hive_create_variant(
            self,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            auto_start_research=auto_start_research,
            preview_note=preview_note,
        )

    def _format_hive_create_preview(
        self,
        *,
        pending: dict[str, Any],
        estimated_cost: float,
        dup_warning: str,
        preview_note: str,
    ) -> str:
        return agent_hive_topics.format_hive_create_preview(
            self,
            pending=pending,
            estimated_cost=estimated_cost,
            dup_warning=dup_warning,
            preview_note=preview_note,
        )

    @staticmethod
    def _preview_text_snippet(text: str, *, limit: int = 220) -> str:
        return agent_hive_topics.preview_text_snippet(text, limit=limit)

    @staticmethod
    def _parse_hive_create_variant_choice(text: str) -> str:
        return agent_hive_topics.parse_hive_create_variant_choice(text)

    def _remember_hive_create_pending(self, session_id: str, pending: dict[str, Any]) -> None:
        agent_hive_topics.remember_hive_create_pending(
            self,
            session_id,
            pending,
            set_hive_interaction_state_fn=self._set_hive_interaction_state,
        )

    def _clear_hive_create_pending(self, session_id: str) -> None:
        agent_hive_topics.clear_hive_create_pending(
            self,
            session_id,
            session_hive_state_fn=self._session_hive_state,
            clear_hive_interaction_state_fn=self._clear_hive_interaction_state,
        )

    def _load_pending_hive_create(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        fallback_task_id: str,
        allow_history_recovery: bool,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.load_pending_hive_create(
            self,
            session_id=session_id,
            source_context=source_context,
            fallback_task_id=fallback_task_id,
            allow_history_recovery=allow_history_recovery,
            session_hive_state_fn=self._session_hive_state,
        )

    def _recover_hive_create_pending_from_history(
        self,
        *,
        history: list[dict[str, Any]],
        fallback_task_id: str,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.recover_hive_create_pending_from_history(
            self,
            history=history,
            fallback_task_id=fallback_task_id,
        )

    @staticmethod
    def _wants_hive_create_auto_start(text: str) -> bool:
        return agent_hive_topics.wants_hive_create_auto_start(text)

    def _prepare_public_hive_topic_copy(
        self,
        *,
        raw_input: str,
        title: str,
        summary: str,
        mode: str = "improved",
    ) -> dict[str, Any]:
        return agent_hive_topics.prepare_public_hive_topic_copy(
            self,
            raw_input=raw_input,
            title=title,
            summary=summary,
            mode=mode,
        )

    @staticmethod
    def _sanitize_public_hive_text(text: str) -> str:
        return agent_hive_topics.sanitize_public_hive_text(text)

    @classmethod
    def _shape_public_hive_admission_safe_copy(
        cls,
        *,
        title: str,
        summary: str,
        force: bool = False,
    ) -> tuple[str, str, str]:
        return agent_hive_topics.shape_public_hive_admission_safe_copy(
            title=title,
            summary=summary,
            force=force,
        )

    @staticmethod
    def _has_structured_hive_public_brief(text: str) -> bool:
        return agent_hive_topics.has_structured_hive_public_brief(text)

    @staticmethod
    def _looks_like_raw_chat_transcript(text: str) -> bool:
        return agent_hive_topics.looks_like_raw_chat_transcript(text)

    def _looks_like_hive_topic_create_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_create_request(self, lowered)

    def _looks_like_hive_topic_drafting_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_drafting_request(self, lowered)

    def _infer_hive_topic_tags(self, title: str) -> list[str]:
        return agent_hive_topics.infer_hive_topic_tags(self, title)

    def _normalize_hive_topic_tag(self, raw: str) -> str:
        return agent_hive_topics.normalize_hive_topic_tag(raw)

    def _strip_wrapping_quotes(self, text: str) -> str:
        return agent_hive_topics.strip_wrapping_quotes(text)

    def _hive_topic_create_failure_text(self, status: str) -> str:
        return agent_hive_topics.hive_topic_create_failure_text(status)

    def _maybe_handle_hive_topic_mutation_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_topic_mutation_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_hive_topic_update_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_update_request(self, lowered)

    def _looks_like_hive_topic_delete_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_delete_request(self, lowered)

    def _extract_hive_topic_update_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_hive_topic_update_draft(self, text)

    def _resolve_hive_topic_for_mutation(
        self,
        *,
        session_id: str,
        topic_hint: str,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.resolve_hive_topic_for_mutation(
            self,
            session_id=session_id,
            topic_hint=topic_hint,
            session_hive_state_fn=self._session_hive_state,
        )

    def _handle_hive_topic_update_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_topics.handle_hive_topic_update_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_hive_topic_delete_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_topics.handle_hive_topic_delete_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_status_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_status_followup(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            session_hive_state_fn=self._session_hive_state,
        )

    def _resolve_hive_status_topic_id(
        self,
        *,
        topic_hint: str,
        watched_topic_ids: list[str],
        history: list[dict[str, Any]],
        interaction_state: dict[str, Any] | None = None,
    ) -> str:
        return agent_hive_followups.resolve_hive_status_topic_id(
            self,
            topic_hint=topic_hint,
            watched_topic_ids=watched_topic_ids,
            history=history,
            interaction_state=interaction_state,
        )

    def _looks_like_hive_status_followup(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_status_followup(lowered)

    def _history_hive_topic_hints(self, history: list[dict[str, Any]] | None) -> list[str]:
        return agent_hive_followups.history_hive_topic_hints(self, history)

    def _looks_like_hive_research_followup(
        self,
        lowered: str,
        *,
        topic_hint: str,
        has_pending_topics: bool,
        shown_titles: list[str],
        history_has_task_list: bool,
    ) -> bool:
        return agent_hive_followups.looks_like_hive_research_followup(
            self,
            lowered,
            topic_hint=topic_hint,
            has_pending_topics=has_pending_topics,
            shown_titles=shown_titles,
            history_has_task_list=history_has_task_list,
        )

    def _looks_like_ambiguous_hive_selection_followup(
        self,
        lowered: str,
        *,
        has_pending_topics: bool,
        history_has_task_list: bool,
    ) -> bool:
        text = str(lowered or "").strip().lower()
        if not text or not (has_pending_topics or history_has_task_list):
            return False
        if any(marker in text for marker in ("#1", "#2", "#3", "first one", "1st one", "second one", "2nd one", "third one", "3rd one")):
            return False
        return any(
            phrase in text
            for phrase in (
                "yes",
                "ok",
                "okay",
                "go ahead",
                "do it",
                "do one",
                "pick one",
                "review the problem",
                "check the problem",
                "review it",
                "review this",
                "help with this",
                "help with that",
                "research it",
                "look into it",
                "take one",
                "do all step by step",
                "deliver to hive",
                "deliver it to hive",
                "post it to hive",
                "submit it to hive",
            )
        )

    def _history_mentions_hive_task_list(self, history: list[dict[str, Any]] | None) -> bool:
        for message in reversed(list(history or [])[-6:]):
            if str(message.get("role") or "").strip().lower() != "assistant":
                continue
            content = str(message.get("content") or "")
            normalized = " ".join(content.split()).lower()
            if "available hive tasks right now" in normalized:
                return True
            if "i see" in normalized and "hive task(s) open" in normalized:
                return True
        return False

    def _interaction_pending_topic_ids(self, hive_state: dict[str, Any]) -> list[str]:
        payload = dict(hive_state.get("interaction_payload") or {})
        return [
            str(item).strip()
            for item in list(payload.get("shown_topic_ids") or [])
            if str(item).strip()
        ]

    def _interaction_shown_titles(self, hive_state: dict[str, Any]) -> list[str]:
        payload = dict(hive_state.get("interaction_payload") or {})
        return [
            str(item).strip()
            for item in list(payload.get("shown_titles") or [])
            if str(item).strip()
        ]

    def _interaction_scoped_queue_rows(
        self,
        queue_rows: list[dict[str, Any]],
        hive_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        scoped_ids = {item.lower() for item in self._interaction_pending_topic_ids(hive_state)}
        if not scoped_ids:
            return []
        return [
            dict(row)
            for row in list(queue_rows or [])
            if str(row.get("topic_id") or "").strip().lower() in scoped_ids
        ]

    def _select_hive_research_signal(
        self,
        queue_rows: list[dict[str, Any]],
        *,
        lowered: str,
        topic_hint: str,
        pending_topic_ids: list[str] | None = None,
        allow_default_pick: bool = True,
    ) -> dict[str, Any] | None:
        rows = [dict(row) for row in list(queue_rows or [])]
        if topic_hint:
            for row in rows:
                topic_id = str(row.get("topic_id") or "").strip().lower()
                if topic_id == topic_hint or topic_id.startswith(topic_hint):
                    return row
        ordinal_index = self._extract_hive_topic_ordinal(lowered)
        if ordinal_index is not None and 0 <= ordinal_index < len(rows):
            return rows[ordinal_index]
        normalized_input = self._normalize_hive_topic_text(lowered)
        for row in rows:
            title = self._normalize_hive_topic_text(str(row.get("title") or ""))
            if title and title in normalized_input:
                return row
        if pending_topic_ids:
            pending_lookup = [str(item).strip().lower() for item in list(pending_topic_ids or []) if str(item).strip()]
            if allow_default_pick:
                for pending_id in pending_lookup:
                    for row in rows:
                        topic_id = str(row.get("topic_id") or "").strip().lower()
                        if topic_id == pending_id or topic_id.startswith(pending_id):
                            return row
        if topic_hint:
            return None
        if rows and allow_default_pick:
            return self._pick_autonomous_research_signal(rows) or rows[0]
        return None

    def _tool_failure_user_message(
        self,
        *,
        execution: Any,
        effective_input: str,
        session_id: str,
    ) -> str:
        safe = str(getattr(execution, "user_safe_response_text", "") or "").strip()
        if safe:
            base = safe
        else:
            status = str(getattr(execution, "status", "")).strip().lower()
            if status == "missing_intent":
                base = "I couldn't map that cleanly to a real action."
            elif status == "unsupported":
                base = "That action is not wired on this runtime yet."
            else:
                base = (
                    "I wasn't able to turn that into a completed action. Tell me the specific "
                    "thing you want done or remembered and I'll take another pass at it."
                )

        # The Hive follow-up is a CURRENT BOUND CONTINUATION, not a word-triggered advertisement
        # (product decision of 2026-09-17). It is offered only when this session actually holds
        # pending Hive topics -- words like "task" or "research" in a failed build's own spec
        # must not decorate a tool failure with Hive boilerplate the request never asked about.
        state = self._session_hive_state(session_id)
        pending = self._interaction_pending_topic_ids(state) or [
            str(item).strip()
            for item in list(state.get("pending_topic_ids") or [])
            if str(item).strip()
        ]
        if pending:
            return f"{base} I still have real Hive tasks ready. Want me to list them again?"
        return base

    def _extract_hive_topic_ordinal(self, lowered: str) -> int | None:
        text = str(lowered or "").strip().lower()
        if not text:
            return None
        ordinal_markers = (
            (0, ("first one", "1st one", "number one", "#1", "task one", "topic one")),
            (1, ("second one", "2nd one", "number two", "#2", "task two", "topic two")),
            (2, ("third one", "3rd one", "number three", "#3", "task three", "topic three")),
        )
        for index, markers in ordinal_markers:
            if any(marker in text for marker in markers):
                return index
        return None

    def _render_hive_research_queue_choices(self, queue_rows: list[dict[str, Any]], *, lead: str) -> str:
        lines = [str(lead or "").strip()]
        for row in list(queue_rows or [])[:5]:
            title = str(row.get("title") or "Untitled topic").strip()
            status = str(row.get("status") or "open").strip()
            topic_id = str(row.get("topic_id") or "").strip()
            suffix = f" (#{topic_id[:8]})" if topic_id else ""
            lines.append(f"- [{status}] {title}{suffix}")
        return "\n".join(line for line in lines if line.strip())

    def _normalize_hive_topic_text(self, text: str) -> str:
        normalized = re.sub(r"\[[^\]]+\]", " ", str(text or "").lower())
        normalized = re.sub(r"#([0-9a-f]{8,12})\b", " ", normalized)
        return " ".join(normalized.split()).strip()
