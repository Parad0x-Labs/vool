from __future__ import annotations

"""Overview/home runtime fragment for the workstation dashboard client template."""

from core.dashboard.workstation_overview_movement_runtime import (
    WORKSTATION_OVERVIEW_MOVEMENT_RUNTIME,
)
from core.dashboard.workstation_overview_surface_runtime import (
    WORKSTATION_OVERVIEW_SURFACE_RUNTIME,
)

WORKSTATION_OVERVIEW_RUNTIME = (
    WORKSTATION_OVERVIEW_MOVEMENT_RUNTIME + WORKSTATION_OVERVIEW_SURFACE_RUNTIME
)
