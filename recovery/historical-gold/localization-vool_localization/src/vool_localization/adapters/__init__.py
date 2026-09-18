"""Platform adapter exports."""
from vool_localization.adapters.android import AndroidAdapter
from vool_localization.adapters.apple import AppleAdapter
from vool_localization.adapters.base import PlatformAdapter
from vool_localization.adapters.gettext_adapter import GettextAdapter
from vool_localization.adapters.voice import VoiceAdapter
from vool_localization.adapters.web_json import WebJsonAdapter
from vool_localization.adapters.windows import WindowsAdapter

ADAPTERS: dict[str, type[PlatformAdapter]] = {
    a.name: a for a in (AppleAdapter, AndroidAdapter, WindowsAdapter, GettextAdapter, WebJsonAdapter, VoiceAdapter)
}

__all__ = [
    "PlatformAdapter", "AppleAdapter", "AndroidAdapter", "WindowsAdapter",
    "GettextAdapter", "WebJsonAdapter", "VoiceAdapter", "ADAPTERS",
]
