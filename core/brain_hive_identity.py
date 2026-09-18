from __future__ import annotations

from core.agent_name_registry import get_agent_name
from core.brain_hive_models import HiveClaimLinkRecord, HiveClaimLinkRequest
from storage.brain_hive_store import list_claim_links, upsert_claim_link


class BrainHiveIdentityMixin:
    def claim_link(self, request: HiveClaimLinkRequest) -> HiveClaimLinkRecord:
        claim_id = upsert_claim_link(
            agent_id=request.agent_id,
            platform=request.platform,
            handle=request.handle,
            owner_label=request.owner_label,
            visibility=request.visibility,
            verified_state=request.verified_state,
        )
        row = next(item for item in list_claim_links(request.agent_id) if item["claim_id"] == claim_id)
        return HiveClaimLinkRecord(**row)

    def _display_fields(self, agent_id: str, fallback_name: str | None = None) -> tuple[str, str | None]:
        display_name = get_agent_name(agent_id) or str(fallback_name or "").strip() or f"agent-{agent_id[:8]}"
        links = [item for item in list_claim_links(agent_id) if item.get("visibility") == "public"]
        if not links:
            return display_name, None
        top = links[0]
        owner = str(top.get("owner_label") or "").strip()
        handle = str(top.get("handle") or "").strip()
        platform = str(top.get("platform") or "").strip()
        if owner and handle:
            return display_name, f"{display_name} by @{handle}"
        if handle:
            return display_name, f"@{handle} on {platform}"
        return display_name, None

    def _known_agent_ids(self, *, limit: int) -> list[str]:
        from core import brain_hive_queries

        return brain_hive_queries._known_agent_ids(limit=limit)
