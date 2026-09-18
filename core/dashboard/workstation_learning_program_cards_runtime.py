from __future__ import annotations

"""Learning-program card builders for the workstation dashboard client template."""

from core.dashboard.workstation_learning_program_knowledge_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_KNOWLEDGE_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_shared_runtime import (
    WORKSTATION_LEARNING_PROGRAM_SHARED_RUNTIME,
)
from core.dashboard.workstation_learning_program_topic_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TOPIC_CARDS_RUNTIME,
)
from core.dashboard.workstation_learning_program_trading_cards_runtime import (
    WORKSTATION_LEARNING_PROGRAM_TRADING_CARDS_RUNTIME,
)

WORKSTATION_LEARNING_PROGRAM_CARDS_RUNTIME = (
    WORKSTATION_LEARNING_PROGRAM_SHARED_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_TRADING_CARDS_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_KNOWLEDGE_CARDS_RUNTIME
    + WORKSTATION_LEARNING_PROGRAM_TOPIC_CARDS_RUNTIME
)
