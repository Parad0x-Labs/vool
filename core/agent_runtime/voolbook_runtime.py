from __future__ import annotations

from typing import Any

from core.agent_runtime import voolbook as agent_voolbook_runtime
from network import signer as signer_mod


class VoolBookRuntimeMixin:
    _voolbook_pending: dict[str, dict[str, str]]

    @staticmethod
    def _classify_voolbook_intent(lowered: str) -> str | None:
        return agent_voolbook_runtime.classify_voolbook_intent(lowered)

    def _maybe_handle_voolbook_fast_path(
        self,
        user_input: str,
        *,
        raw_user_input: str | None = None,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_voolbook_runtime.maybe_handle_voolbook_fast_path(
            self,
            user_input,
            raw_user_input=raw_user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _handle_voolbook_pending_step(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any] | None:
        return agent_voolbook_runtime.handle_voolbook_pending_step(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
            signer_module=signer_mod,
        )

    def _voolbook_step_handle(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.voolbook_step_handle(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _voolbook_step_bio(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.voolbook_step_bio(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    def _handle_voolbook_post(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.handle_voolbook_post(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _execute_voolbook_post(
        self,
        content: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.execute_voolbook_post(
            self,
            content,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _voolbook_result(
        self,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        response: str,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.voolbook_result(
            self,
            session_id,
            user_input,
            source_context,
            response,
        )

    def _sync_profile_to_hive(self, profile: Any) -> None:
        agent_voolbook_runtime.sync_profile_to_hive(self, profile)

    @staticmethod
    def _is_voolbook_post_request(lowered: str) -> bool:
        return agent_voolbook_runtime.is_voolbook_post_request(lowered)

    @staticmethod
    def _is_voolbook_delete_request(lowered: str) -> bool:
        return agent_voolbook_runtime.is_voolbook_delete_request(lowered)

    @staticmethod
    def _is_voolbook_edit_request(lowered: str) -> bool:
        return agent_voolbook_runtime.is_voolbook_edit_request(lowered)

    def _handle_voolbook_delete(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.handle_voolbook_delete(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_voolbook_edit(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.handle_voolbook_edit(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    @staticmethod
    def _extract_post_id(text: str) -> str:
        return agent_voolbook_runtime.extract_post_id(text)

    @staticmethod
    def _extract_edit_content(text: str) -> str:
        return agent_voolbook_runtime.extract_edit_content(text)

    @staticmethod
    def _is_voolbook_create_request(lowered: str) -> bool:
        return agent_voolbook_runtime.is_voolbook_create_request(lowered)

    @staticmethod
    def _extract_voolbook_bio_update(text: str) -> str:
        return agent_voolbook_runtime.extract_voolbook_bio_update(text)

    @staticmethod
    def _extract_twitter_handle(text: str) -> str:
        return agent_voolbook_runtime.extract_twitter_handle(text)

    @staticmethod
    def _extract_handle_from_text(text: str) -> str | None:
        return agent_voolbook_runtime.extract_handle_from_text(text)

    @staticmethod
    def _looks_like_voolbook_handle_rules_question(text: str, lowered: str) -> bool:
        return agent_voolbook_runtime.looks_like_voolbook_handle_rules_question(text, lowered)

    @staticmethod
    def _extract_post_content(text: str) -> str:
        return agent_voolbook_runtime.extract_post_content(text)

    @staticmethod
    def _is_substantive_post_content(text: str) -> bool:
        return agent_voolbook_runtime.is_substantive_post_content(text)

    @staticmethod
    def _looks_like_direct_social_post_request(lowered: str) -> bool:
        return agent_voolbook_runtime.looks_like_direct_social_post_request(lowered)

    @staticmethod
    def _strip_context_subject_suffix(text: str) -> str:
        return agent_voolbook_runtime.strip_context_subject_suffix(text)

    @staticmethod
    def _extract_display_name(text: str) -> str:
        return agent_voolbook_runtime.extract_display_name(text)

    def _handle_voolbook_rename(
        self,
        new_handle: str,
        profile: Any,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_voolbook_runtime.handle_voolbook_rename(
            self,
            new_handle,
            profile,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )
