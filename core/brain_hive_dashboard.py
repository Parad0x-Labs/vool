from __future__ import annotations

import os
import sys

from core.dashboard.render import (
    _dashboard_canonical_url,
    _dashboard_mode,
    _render_public_dashboard_html,
    _render_public_dashboard_mode_nav,
)
from core.dashboard.render import (
    render_dashboard_html as _render_dashboard_html_impl,
)
from core.dashboard.snapshot import (
    TRADING_SCANNER_AGENT_ID,
    TRADING_SCANNER_LIVE_SEC,
    TRADING_SCANNER_VISIBLE_SEC,
    _agent_is_online,
    _agent_profile_key,
    _agent_profile_rank,
    _augment_dashboard_with_trading_scanner,
    _build_learning_lab_payload,
    _build_task_event_stream,
    _build_trading_learning_payload,
    _display_agent_stats,
    _distinct_visible_agents,
    _is_trading_learning_topic,
    _latest_trading_presence_ts,
    _merge_posts,
    _parse_dashboard_timestamp,
    _post_kind_event_type,
    _safe_list_posts,
    _safe_list_recent_topic_claims_feed,
    _safe_list_research_queue,
    _safe_list_topic_claims,
    _task_event_meta,
)
from core.dashboard.snapshot import (
    build_dashboard_snapshot as _build_dashboard_snapshot_impl,
)
from core.dashboard.topic import render_not_found_html, render_topic_detail_html
from core.vool_user_summary import build_user_summary

try:
    from core.control_plane_workspace import collect_control_plane_status
except Exception:  # pragma: no cover - compatibility fallback for older nodes

    def collect_control_plane_status() -> dict[str, object]:
        return {}


try:
    from core.brain_hive_artifacts import count_artifact_manifests
except Exception:  # pragma: no cover - compatibility fallback for older nodes

    def count_artifact_manifests(*, topic_id: str | None = None) -> int:
        del topic_id
        return 0


_THIS_MODULE = sys.modules[__name__]


def build_dashboard_snapshot(
    hive=None,
    *,
    topic_limit: int = 12,
    post_limit: int = 24,
    agent_limit: int = 24,
) -> dict[str, object]:
    return _build_dashboard_snapshot_impl(
        hive=hive,
        topic_limit=topic_limit,
        post_limit=post_limit,
        agent_limit=agent_limit,
        hooks=_THIS_MODULE,
    )


def render_dashboard_html(
    *,
    api_endpoint: str = "/v1/hive/dashboard",
    topic_base_path: str = "/task",
    initial_mode: str = "overview",
    public_surface: bool = False,
    canonical_url: str = "",
) -> str:
    return _render_dashboard_html_impl(
        api_endpoint=api_endpoint,
        topic_base_path=topic_base_path,
        initial_mode=initial_mode,
        public_surface=public_surface,
        canonical_url=canonical_url,
        hooks=_THIS_MODULE,
    )


def _branding_payload() -> dict[str, str]:
    return {
        "watch_title": os.environ.get("VOOL_WATCH_TITLE", "VOOL Watch"),
        "legal_name": os.environ.get("VOOL_WATCH_LEGAL_NAME", "Parad0x Labs"),
        "x_handle": os.environ.get("VOOL_WATCH_X_HANDLE", "@parad0x_labs"),
        "x_url": os.environ.get("VOOL_WATCH_X_URL", "https://x.com/Parad0x_Labs"),
        "vool_x_label": os.environ.get("VOOL_WATCH_VOOL_X_LABEL", "Follow VOOL on X"),
        "vool_x_url": os.environ.get("VOOL_WATCH_VOOL_X_URL", "https://x.com/vool_ai"),
        "github_url": os.environ.get("VOOL_WATCH_GITHUB_URL", "https://github.com/Parad0x-Labs/"),
        "discord_url": os.environ.get("VOOL_WATCH_DISCORD_URL", "https://discord.gg/WuqCDnyfZ8"),
        "pumpfun_url": os.environ.get(
            "VOOL_WATCH_PUMPFUN_URL",
            "https://pump.fun/coin/8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump",
        ),
        "token_symbol": os.environ.get("VOOL_WATCH_TOKEN_SYMBOL", "$NULL"),
        "token_address": os.environ.get(
            "VOOL_WATCH_TOKEN_ADDRESS",
            "8EeDdvCRmFAzVD4takkBrNNwkeUTUQh4MscRK5Fzpump",
        ),
    }


__all__ = [
    "TRADING_SCANNER_AGENT_ID",
    "TRADING_SCANNER_LIVE_SEC",
    "TRADING_SCANNER_VISIBLE_SEC",
    "_agent_is_online",
    "_agent_profile_key",
    "_agent_profile_rank",
    "_augment_dashboard_with_trading_scanner",
    "_branding_payload",
    "_build_learning_lab_payload",
    "_build_task_event_stream",
    "_build_trading_learning_payload",
    "_dashboard_canonical_url",
    "_dashboard_mode",
    "_display_agent_stats",
    "_distinct_visible_agents",
    "_is_trading_learning_topic",
    "_latest_trading_presence_ts",
    "_merge_posts",
    "_parse_dashboard_timestamp",
    "_post_kind_event_type",
    "_render_public_dashboard_html",
    "_render_public_dashboard_mode_nav",
    "_safe_list_posts",
    "_safe_list_recent_topic_claims_feed",
    "_safe_list_research_queue",
    "_safe_list_topic_claims",
    "_task_event_meta",
    "build_dashboard_snapshot",
    "build_user_summary",
    "collect_control_plane_status",
    "count_artifact_manifests",
    "render_dashboard_html",
    "render_not_found_html",
    "render_topic_detail_html",
]
