"""VOOL's protected media library: photos, videos and sound kept encrypted on this computer.

* :mod:`.sealed`  the encrypted file format (pieces of AES-256-GCM, each bound to its place).
* :mod:`.store`   the library itself: Local and Mobile sections, never indexed by the system.
* :mod:`.backup`  one-click incremental backup to a USB drive, encrypted and restorable.
* :mod:`.api`     the Settings page's door.
"""
