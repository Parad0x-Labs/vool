"""Canonical presentation model.

Blocks between semantic answer and surface. The model carries WHAT IS TRUE;
renderers decide HOW IT LOOKS. Nothing in this package may add, drop, or
alter semantic content. Derived values (bar fill geometry, rounded display
numbers, domain labels) are presentation calculations only — the exact
source value always remains available on the block itself.

Inline content uses a small run model:

* ``Span``   - styled text (plain / code / strong)
* ``LinkRef``- exact href + derived-safe display labels

Block types kept deliberately minimal:
Paragraph, Heading, List, Table, Code, Quote, Callout,
MetricLine (middle-dot metadata), Progress, Chart, Timeline, Sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence


# --------------------------------------------------------------------------
# Inline runs
# --------------------------------------------------------------------------

SpanStyle = Literal["plain", "code", "strong"]


@dataclass(frozen=True)
class Span:
    text: str
    style: SpanStyle = "plain"


@dataclass(frozen=True)
class LinkRef:
    """A link whose ``href`` is immutable truth.

    ``display_label`` / ``domain_label`` are presentation-only derivations;
    see ``links.safe_link`` for how they are produced safely. If upstream
    secret law forbids exposing the href, ``href_exposed=False`` and
    ``href`` must not leave the trust boundary.
    """

    href: str
    display_label: str
    domain_label: str | None
    href_exposed: bool = True
    redaction_reason: str | None = None


@dataclass(frozen=True)
class Link:
    ref: LinkRef
    # Optional trailing plain text after the link inside a paragraph.


Run = Span | Link


# --------------------------------------------------------------------------
# Typed status vocabulary (semantic states; emoji/symbols are presentation)
# --------------------------------------------------------------------------

StatusName = Literal[
    "PASS", "FAIL", "WARNING", "PENDING", "UNKNOWN",
    "PARTIAL", "BLOCKED", "PARKED", "EXPERIMENT", "SKIP",
]

KNOWN_STATUSES: frozenset[str] = frozenset(
    {"PASS", "FAIL", "WARNING", "PENDING", "UNKNOWN",
     "PARTIAL", "BLOCKED", "PARKED", "EXPERIMENT", "SKIP"}
)


@dataclass(frozen=True)
class Status:
    name: StatusName
    detail: str = ""

    def __post_init__(self) -> None:
        if self.name not in KNOWN_STATUSES:
            raise ValueError(f"unknown status name: {self.name!r}")


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------

@dataclass
class Paragraph:
    runs: list[Run]


@dataclass
class Heading:
    text: str
    level: int = 1                 # 1..3; deeper hierarchy discouraged
    numbered: bool = False         # renderer may number existing structure
    number: str | None = None      # filled by renderer when numbered


@dataclass
class ListItem:
    runs: list[Run]
    children: list["ListItem"] = field(default_factory=list)


@dataclass
class List:
    kind: Literal["ordered", "unordered"] = "unordered"
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    # Cell values are copied verbatim. Missing data stays as supplied;
    # renderers may NOT substitute em-dashes or blanks for UNKNOWN.


@dataclass
class Code:
    text: str                      # copy-exact bytes; never reflowed
    lang: str = ""


@dataclass
class Quote:
    runs: list[Run]


CalloutKind = Literal["info", "warning", "success", "failure"]


@dataclass
class Callout:
    kind: CalloutKind
    runs: list[Run]


@dataclass
class MetricLine:
    """Compact middle-dot metadata line: ``sha · 96 tests · not pushed``."""

    items: list[tuple[str, Run]]   # (label, value-run); empty-label allowed
    separator: str = "·"


@dataclass
class Progress:
    label: str
    value: float                   # exact typed value; renderer rounds display
    unit: str = "%"                # "%" or absolute unit string
    maximum: float | None = None   # required when unit != "%"


@dataclass
class SeriesPoint:
    label: str
    value: float                   # exact value
    unit: str                      # points in one chart MUST share a unit
    status: Status | None = None


ChartKind = Literal["bars_h", "bars_v", "donut", "table"]


@dataclass
class Chart:
    series: list[SeriesPoint]
    kind: ChartKind = "table"
    title: str = ""
    total: float | None = None     # required denominator for donut


@dataclass
class TimelineEvent:
    stamp: str                     # verbatim timestamp/ordering label
    runs: list[Run]
    status: Status | None = None


@dataclass
class Timeline:
    events: list[TimelineEvent]


SourceKind = Literal["web", "docs", "code", "unknown"]
ALLOWED_SOURCE_KINDS: tuple[str, ...] = ("web", "docs", "code", "unknown")


@dataclass
class Source:
    ref: LinkRef
    source_kind: SourceKind = "unknown"   # authority comes from EVIDENCE only
    official: bool = False                # may be True ONLY if upstream said so


@dataclass
class Sources:
    sources: list[Source]


Block = (Paragraph | Heading | List | Table | Code | Quote |
         Callout | MetricLine | Progress | Chart | Timeline | Sources)


@dataclass
class RenderDocument:
    blocks: list[Block] = field(default_factory=list)

    def add(self, block: Block) -> None:
        self.blocks.append(block)


def text(s: str) -> Paragraph:
    return Paragraph([Span(s)])


def code_span(s: str) -> Span:
    return Span(s, "code")


def strong(s: str) -> Span:
    return Span(s, "strong")
