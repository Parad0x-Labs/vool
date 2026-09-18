"""VOOL presentation layer: canonical RenderDocument -> surface output.

The renderer owns HOW answers look. It never owns WHAT IS TRUE.
Semantic ownership stays upstream (kernel / evidence); served-boundary
truth review stays with White Ninja (core.exact_output_seal untouched).
"""
from core.presentation.intent import DEFAULT_INTENT, RenderIntent
from core.presentation.model import (
    Callout, Chart, Code, Heading, Link, LinkRef, List, ListItem,
    MetricLine, Paragraph, Progress, Quote, RenderDocument, SeriesPoint,
    Source, Sources, Span, Status, Table, Timeline, TimelineEvent,
    code_span, strong, text,
)
from core.presentation.render_markdown import MarkdownRenderer
from core.presentation.render_plain import PlainTextRenderer

__all__ = [
    "DEFAULT_INTENT", "RenderIntent",
    "Callout", "Chart", "Code", "Heading", "Link", "LinkRef", "List",
    "ListItem", "MetricLine", "Paragraph", "Progress", "Quote",
    "RenderDocument", "SeriesPoint", "Source", "Sources", "Span",
    "Status", "Table", "Timeline", "TimelineEvent",
    "code_span", "strong", "text",
    "MarkdownRenderer", "PlainTextRenderer",
]
