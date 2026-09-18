"""Typed, evidence-bearing fresh-data capabilities.

These modules do not route turns.  They define the contracts that a router or conductor may
invoke after permission and effect compatibility have already been decided.
"""

from core.fresh_data.fx import FxQuote, FxQuoteStatus
from core.fresh_data.places import PlaceSearchReport, PlaceSearchStatus
from core.fresh_data.research import ResearchCoverageReport, ResearchFieldStatus

__all__ = [
    "FxQuote",
    "FxQuoteStatus",
    "PlaceSearchReport",
    "PlaceSearchStatus",
    "ResearchCoverageReport",
    "ResearchFieldStatus",
]
