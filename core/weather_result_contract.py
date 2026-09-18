"""A structured weather observation, mirroring `core.live_quote_contract.LiveQuoteResult`.

Why this exists: the fast_live_info weather path (DuckDuckGo search + prose scraping) never keeps a
numeric temperature -- only a rendered sentence. Computing "warmest current city" from that would
mean regex-parsing a number back out of freeform search-result prose, which is exactly the kind of
fragile, unverifiable derivation the project's verification discipline exists to rule out. wttr.in's
own JSON API (`?format=j1`, already used as `tools.web.web_research._weather_fallback`'s source)
returns a real numeric `temp_C` field; this type keeps it as a float instead of folding it into a
sentence, so a derived comparison can be computed from the number the source actually reported, not
a re-parsed guess.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WeatherResult:
    location: str
    place_label: str
    condition: str
    temperature_c: float
    feels_like_c: float | None
    humidity_pct: float | None
    wind_kmph: float | None
    observed_at: str
    source_label: str
    source_url: str
    confidence: float = 0.95
    # Today's forecast high/low -- wttr.in's own `weather[0]` daily-forecast entry, kept separate
    # from `temperature_c` (the CURRENT reading) rather than derived from it: a request for
    # "today's high and low" asks for the day's forecast range, not the instant reading twice.
    # None (not 0.0) when the source didn't report one -- rendered as its own "unavailable", never
    # silently substituted with the current temperature.
    high_c: float | None = None
    low_c: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def summary_text(self) -> str:
        return f"{self.place_label}: {self.condition}, {self.temperature_c:.0f} C"

    def answer_text(self) -> str:
        line = f"{self.place_label}: {self.condition}, {self.temperature_c:.0f} C"
        if self.feels_like_c is not None:
            line += f" (feels like {self.feels_like_c:.0f} C)"
        if self.humidity_pct is not None:
            line += f", humidity {self.humidity_pct:.0f}%"
        if self.wind_kmph is not None:
            line += f", wind {self.wind_kmph:.0f} km/h"
        if self.high_c is not None and self.low_c is not None:
            line += f", today's high {self.high_c:.0f} C / low {self.low_c:.0f} C"
        if self.observed_at:
            line += f". Observed {self.observed_at}"
        line += f". Source: [{self.source_label}]({self.source_url})."
        return line
