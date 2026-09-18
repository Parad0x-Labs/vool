from __future__ import annotations

"""Trading and learning-lab runtime fragment for the workstation dashboard client template."""

from core.dashboard.workstation_learning_program_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_runtime import (
    WORKSTATION_LEARNING_PROGRAM_RUNTIME,
)
from core.dashboard.workstation_trading_presence_runtime import (
    WORKSTATION_TRADING_PRESENCE_RUNTIME,
)
from core.dashboard.workstation_trading_surface_runtime import (
    WORKSTATION_TRADING_SURFACE_RUNTIME,
)

WORKSTATION_TRADING_LEARNING_RUNTIME = (
    WORKSTATION_TRADING_PRESENCE_RUNTIME
    + WORKSTATION_TRADING_SURFACE_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_CARDS_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_RUNTIME
)
