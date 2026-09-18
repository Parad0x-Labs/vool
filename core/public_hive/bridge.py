from __future__ import annotations

from typing import Any

from . import client as public_hive_client
from .bridge_presence import PublicHiveBridgePresenceMixin
from .bridge_topics import PublicHiveBridgeTopicsMixin
from .bridge_transport import _UNSET_SENTINEL, PublicHiveBridgeTransportMixin
from .config import PublicHiveBridgeConfig


class PublicHiveBridge(
    PublicHiveBridgeTransportMixin,
    PublicHiveBridgePresenceMixin,
    PublicHiveBridgeTopicsMixin,
):
    def __init__(
        self,
        config: PublicHiveBridgeConfig | None = None,
        *,
        urlopen: Any | None = None,
    ) -> None:
        self.config = config or _load_public_hive_bridge_config()
        # Default is the ONE outbound HTTP door (see client._door_urlopen), so a bare
        # PublicHiveBridge() -- as built by apps/vool_agent.py and core/adaptation_autopilot.py
        # -- enforces the turn's remote-fetch veto before any socket opens.
        self._urlopen = urlopen or public_hive_client._door_urlopen
        self._voolbook_token: str | None = _UNSET_SENTINEL
        self._preferred_topic_base_url: str | None = None
        self._client = public_hive_client.PublicHiveHttpClient(
            self.config,
            urlopen=self._urlopen,
            voolbook_token_fn=self._get_voolbook_token,
        )


def _load_public_hive_bridge_config() -> PublicHiveBridgeConfig:
    from core.public_hive_bridge import load_public_hive_bridge_config

    return load_public_hive_bridge_config()
