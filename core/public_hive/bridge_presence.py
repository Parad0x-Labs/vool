from __future__ import annotations

from .bridge_presence_commons import PublicHiveBridgePresenceCommonsMixin
from .bridge_presence_voolbook import PublicHiveBridgePresenceVoolbookMixin
from .bridge_presence_sync import PublicHiveBridgePresenceSyncMixin


class PublicHiveBridgePresenceMixin(
    PublicHiveBridgePresenceSyncMixin,
    PublicHiveBridgePresenceVoolbookMixin,
    PublicHiveBridgePresenceCommonsMixin,
):
    pass
