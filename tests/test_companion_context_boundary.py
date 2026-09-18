from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.agent_runtime.fast_paths_companion import (
    maybe_handle_companion_memory_fast_path,
)
from core.context_namespace import ensure_chat_namespace
from core.runtime_paths import configure_runtime_home


def test_remote_channel_cannot_load_private_operator_profile(
    tmp_path,
) -> None:
    configure_runtime_home(tmp_path)
    try:
        chat_id = "chat:remote-profile-boundary"
        ensure_chat_namespace(
            chat_id,
            grant_confirmed_profile=True,
        )
        with mock.patch(
            "core.agent_runtime.fast_paths_companion.load_operator_dense_profile",
            side_effect=AssertionError(
                "remote channel must not load private profile"
            ),
        ) as load_profile:
            result = maybe_handle_companion_memory_fast_path(
                SimpleNamespace(),
                "Pick up where we left off.",
                session_id=chat_id,
                source_context={
                    "surface": "channel",
                    "_owner_local": False,
                },
            )

        assert result is None
        load_profile.assert_not_called()
    finally:
        configure_runtime_home(None)


def test_canonical_product_question_is_not_answered_by_canned_fast_path(
    tmp_path,
) -> None:
    configure_runtime_home(tmp_path)
    try:
        chat_id = "chat:canonical-model-grounding"
        ensure_chat_namespace(
            chat_id,
            grant_confirmed_profile=True,
        )
        with mock.patch(
            "core.agent_runtime.fast_paths_companion.load_operator_dense_profile",
            return_value={},
        ):
            result = maybe_handle_companion_memory_fast_path(
                SimpleNamespace(),
                "What is Web0?",
                session_id=chat_id,
                source_context={
                    "surface": "api",
                    "_owner_local": True,
                },
            )

        assert result is None
    finally:
        configure_runtime_home(None)
