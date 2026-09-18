from __future__ import annotations

"""Overview/home section runtime aggregators for the workstation dashboard client template."""

from core.dashboard.workstation_overview_home_runtime import (
    WORKSTATION_OVERVIEW_HOME_RUNTIME,
)
from core.dashboard.workstation_overview_proof_runtime import (
    WORKSTATION_OVERVIEW_PROOF_RUNTIME,
)
from core.dashboard.workstation_overview_stats_runtime import (
    WORKSTATION_OVERVIEW_STATS_RUNTIME,
)
from core.dashboard.workstation_overview_streams_runtime import (
    WORKSTATION_OVERVIEW_STREAMS_RUNTIME,
)

WORKSTATION_OVERVIEW_SURFACE_RUNTIME = (
    WORKSTATION_OVERVIEW_STATS_RUNTIME
    + WORKSTATION_OVERVIEW_PROOF_RUNTIME
    + WORKSTATION_OVERVIEW_STREAMS_RUNTIME
    + WORKSTATION_OVERVIEW_HOME_RUNTIME
)
