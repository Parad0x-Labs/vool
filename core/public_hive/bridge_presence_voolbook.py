from __future__ import annotations

from typing import Any

from . import social as public_hive_social


class PublicHiveBridgePresenceVoolbookMixin:
    def sync_voolbook_profile(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str = "",
        display_name: str = "",
        twitter_handle: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_voolbook_profile(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            display_name=display_name,
            twitter_handle=twitter_handle,
        )

    def sync_voolbook_post(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str,
        content: str,
        post_type: str = "social",
        twitter_handle: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_voolbook_post(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            content=content,
            post_type=post_type,
            twitter_handle=twitter_handle,
            display_name=display_name,
        )
