from __future__ import annotations

from .bridge_presence_commons import PublicHiveBridgePresenceCommonsMixin
from .bridge_presence_sync import PublicHiveBridgePresenceSyncMixin
from .bridge_presence_voolbook import PublicHiveBridgePresenceVoolbookMixin


class PublicHiveBridgePresenceMixin(
    PublicHiveBridgePresenceSyncMixin,
    PublicHiveBridgePresenceVoolbookMixin,
    PublicHiveBridgePresenceCommonsMixin,
):
    pass
