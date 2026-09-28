"""Retired OpenClaw agent registration.

The OpenClaw integration is removed from VOOL product paths: the installer, the
served API startup, and onboarding no longer register an agent in, write tokens
to, or seed workspaces under a third-party OpenClaw config. This module remains
importable (wheel smoke and delivery contracts import it) and keeps the historic
``register`` entrypoint as an honest, side-effect-free refusal: it never reads,
creates, or modifies any OpenClaw state, and it always reports failure.
"""

from __future__ import annotations

import sys
from typing import Any

RETIREMENT_NOTICE = (
    "OpenClaw integration is retired from VOOL. VOOL installs, starts and chats "
    "natively (Start_VOOL / Talk_To_VOOL / Open_Web0) and never registers an agent "
    "in, or writes config for, a third-party OpenClaw installation. Existing "
    "OpenClaw installations are left untouched. OpenClaw-specific skills live "
    "separately: https://github.com/Parad0x-Labs/openclaw-skills"
)


def register(*_args: Any, **_kwargs: Any) -> bool:
    """Refuse OpenClaw registration without side effects.

    Historic callers passed project_root/vool_home/model_tag/display_name/openclaw_home;
    every argument is ignored, nothing is read or written anywhere, and registration
    is reported as not performed.
    """
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    del argv  # historic CLI positionally took project_root/runtime_home/model/agent-name
    print(RETIREMENT_NOTICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
