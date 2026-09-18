from __future__ import annotations

from typing import Any

from . import presence as public_hive_presence


class PublicHiveBridgePresenceSyncMixin:
    def sync_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "vool_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.sync_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def heartbeat_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "vool_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.heartbeat_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def _presence_request(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str,
        transport_mode: str,
    ) -> Any:
        return public_hive_presence.build_presence_request(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )
