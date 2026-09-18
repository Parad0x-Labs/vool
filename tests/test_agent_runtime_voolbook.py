from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import voolbook


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_classify_voolbook_intent_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._classify_voolbook_intent("delete my voolbook post") == voolbook.classify_voolbook_intent(
        "delete my voolbook post"
    )


def test_maybe_handle_voolbook_fast_path_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.voolbook.maybe_handle_voolbook_fast_path",
        return_value={"response": "ok"},
    ) as maybe_handle_voolbook_fast_path:
        result = agent._maybe_handle_voolbook_fast_path(
            "post to vool book: hello",
            session_id="session-voolbook-facade",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "ok"}
    maybe_handle_voolbook_fast_path.assert_called_once_with(
        agent,
        "post to vool book: hello",
        raw_user_input=None,
        session_id="session-voolbook-facade",
        source_context={"surface": "openclaw"},
        signer_module=mock.ANY,
    )


def test_maybe_handle_voolbook_fast_path_uses_app_level_post_handler_override() -> None:
    agent = _build_agent()
    profile = SimpleNamespace(
        peer_id="peer-1",
        handle="vool",
        bio="",
        display_name="VOOL",
        twitter_handle="",
        post_count=0,
        claim_count=0,
    )

    with mock.patch("core.agent_runtime.agent.signer_mod.get_local_peer_id", return_value="peer-1"), mock.patch(
        "core.voolbook_identity.get_profile",
        return_value=profile,
    ), mock.patch.object(
        agent,
        "_handle_voolbook_post",
        return_value={"response": "patched-post-handler"},
    ) as handle_voolbook_post:
        result = agent._maybe_handle_voolbook_fast_path(
            "post to vool book: hello world",
            session_id="session-voolbook-post",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "patched-post-handler"}
    handle_voolbook_post.assert_called_once()


def test_extract_post_content_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "post to vool book: shipping the extracted runtime module tonight"

    assert agent._extract_post_content(text) == voolbook.extract_post_content(text)


def test_execute_voolbook_post_marks_runtime_posts_as_ai_origin() -> None:
    agent = _build_agent()
    agent.public_hive_bridge.sync_voolbook_post = mock.Mock(return_value={"ok": False})  # type: ignore[assignment]
    profile = SimpleNamespace(
        peer_id="peer-1",
        handle="vool",
        bio="",
        display_name="VOOL",
        twitter_handle="",
        post_count=0,
        claim_count=0,
    )

    with mock.patch("core.voolbook_identity.increment_post_count"), mock.patch(
        "storage.voolbook_store.create_post",
        return_value=SimpleNamespace(post_id="post-1"),
    ) as create_post:
        result = voolbook.execute_voolbook_post(
            agent,
            "Ship the runtime split.",
            profile,
            session_id="session-voolbook-origin",
            source_context={"surface": "openclaw"},
        )

    assert "Posted to VoolBook" in result["response"]
    assert create_post.call_args.kwargs["origin_kind"] == "ai"
    assert create_post.call_args.kwargs["origin_channel"] == "runtime_fast_path"
    assert create_post.call_args.kwargs["origin_peer_id"] == "peer-1"
