from __future__ import annotations

import uuid
from unittest import mock

from core.voolbook_identity import get_profile, register_voolbook_account
from network.signer import get_local_peer_id
from storage.voolbook_store import create_post, list_user_posts


def test_voolbook_token_style_posts_default_to_human_origin() -> None:
    profile = register_voolbook_account(
        f"acceptancehuman_{uuid.uuid4().hex[:8]}",
        peer_id=f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
    ).profile

    post = create_post(profile.peer_id, profile.handle, "Human-authored profile update.")

    assert post.origin_kind == "human"
    assert post.origin_channel == "voolbook_token"


def test_runtime_posts_are_marked_ai_origin(make_agent) -> None:
    agent = make_agent()
    peer_id = get_local_peer_id()
    existing = get_profile(peer_id)
    profile = existing or register_voolbook_account(f"acceptanceai_{uuid.uuid4().hex[:8]}", peer_id=peer_id).profile
    agent.public_hive_bridge.sync_voolbook_post = mock.Mock(return_value={"ok": False})  # type: ignore[assignment]

    result = agent._execute_voolbook_post(
        "Autonomous agent status update.",
        profile,
        session_id="acceptance:voolbook-ai",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    posts = list_user_posts(profile.handle, limit=5)

    assert "Posted to VoolBook" in result["response"]
    assert posts[0].origin_kind == "ai"
    assert posts[0].origin_channel == "runtime_fast_path"
