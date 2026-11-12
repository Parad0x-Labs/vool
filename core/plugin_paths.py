"""One file or folder name joined under a plugin store, proven to stay directly inside it."""
from __future__ import annotations

import os
from pathlib import Path


def child_of(base: str | os.PathLike, name: str) -> Path:
    """``base/name`` when ``name`` is one plain component; refuses anything that would leave ``base``.

    The parent is normalized but links are not followed, so callers that refuse linked
    folders still see the link itself.
    """
    root = os.path.abspath(base)
    candidate = os.path.normpath(os.path.join(root, str(name)))
    if not candidate.startswith(root + os.sep) or os.path.dirname(candidate) != root:
        raise ValueError("This name points outside its plugin folder.")
    return Path(candidate)


def inside(base: str | os.PathLike, relative: str) -> Path:
    """``base/relative`` with links resolved, refused unless it lands strictly inside ``base``."""
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, str(relative)))
    if not candidate.startswith(root + os.sep):
        raise ValueError("This path points outside its plugin folder.")
    return Path(candidate)
