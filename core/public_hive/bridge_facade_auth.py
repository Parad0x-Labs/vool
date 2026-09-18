from __future__ import annotations

from . import auth as public_hive_auth
from .config import PublicHiveBridgeConfig


def public_hive_has_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    payload: dict[str, object] | None = None,
) -> bool:
    return public_hive_auth.public_hive_has_auth(config, payload=payload)


def public_hive_write_requires_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    seed_urls: list[str] | tuple[str, ...] | None = None,
    topic_target_url: str | None = None,
) -> bool:
    return public_hive_auth.public_hive_write_requires_auth(
        config,
        seed_urls=seed_urls,
        topic_target_url=topic_target_url,
    )


def public_hive_write_enabled(
    config: PublicHiveBridgeConfig | None = None,
    *,
    load_public_hive_bridge_config_fn,
) -> bool:
    return public_hive_auth.public_hive_write_enabled(
        config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config_fn,
    )


def url_requires_auth(url: str) -> bool:
    return public_hive_auth.url_requires_auth(url)
