"""Read chat history exports from other AI apps (readers only, nothing saved).

``read`` turns a file the user picked into a neutral, validated list of chats in memory.
This package never writes to VOOL's database, memory, settings or any file; deciding what to
do with the chats (import screen, privacy rules) belongs to the caller.
"""
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult, Role
from core.chat_import.readers import DEFAULT_LIMITS, FormatReader, Limits, detect, read, register_reader

__all__ = [
    "DEFAULT_LIMITS",
    "FormatReader",
    "ImportedChat",
    "ImportedMessage",
    "Limits",
    "ReadResult",
    "Role",
    "detect",
    "read",
    "register_reader",
]
