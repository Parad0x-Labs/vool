"""Bring chats from other AI apps into VOOL.

* ``readers`` reads a data-export zip the user picked; ``local_sources`` finds and reads other
  apps' chat stores on this computer. Readers only read and return the neutral ``ReadResult``.
* ``store`` keeps imported chats in VOOL's history, apart from every memory store.
* ``importer`` lands chosen chats there: new ids, nothing resident overwritten, secrets masked,
  the same source chat never brought in twice.
* ``flow`` is the one owner of a preview and an import run for the settings screen.
"""
from core.chat_import.common import DEFAULT_LIMITS, Limits
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult, Role, describe_problem
from core.chat_import.readers import FormatReader, detect, read, register_reader

__all__ = [
    "DEFAULT_LIMITS",
    "FormatReader",
    "ImportedChat",
    "ImportedMessage",
    "Limits",
    "ReadResult",
    "Role",
    "describe_problem",
    "detect",
    "read",
    "register_reader",
]
