"""Freeze browser authority from the owner's request, before page evidence arrives."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

_CONFINED = ContextVar("supervised_browser_turn", default=None)
_BROWSER = r"(?:(?:the|a|an|my|our|built[\s-]?in|live|integrated|vool)\s+){0,4}browser\b"
_CLAUSE = r"(?:^|[.;!?]\s*|\b(?:and|then)\s+)(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_ASK = re.compile(
    _CLAUSE + r"(?:use|open|launch|start|show)\s+" + _BROWSER
    + r"|" + _CLAUSE + r"(?:open|visit|browse|navigate|load)\b[^\n]{0,500}\b(?:in|with|using)\s+" + _BROWSER
    + r"|^\s*(?:please\s+)?(?:browse|navigate\s+to)\s+https?://",
    re.IGNORECASE,
)
_URL = re.compile(r'https?://[^\s<>"\x27`]+', re.IGNORECASE)
_QUOTED = re.compile(r'```.*?```|`[^`]*`|"[^"\n]*"|\x27[^\x27\n]*\x27', re.DOTALL)


def _instruction_text(raw: str) -> str:
    # A quoted URL is a value. A quoted sentence or code block is data, never
    # additional action authority. This does not inspect any page content.
    def value(match):
        inner = match.group().strip('`"\x27')
        return inner if _URL.fullmatch(inner) else ' '

    return _QUOTED.sub(value, raw)


def requested(text: str) -> bool:
    """An execution request, never an example, quoted page text or prohibition."""
    from core.instructional_request import asks_for_instructions_not_execution
    from core.retrieval_constraints import analyze_retrieval_constraints

    raw = str(text or "")
    if asks_for_instructions_not_execution(raw):
        return False
    constraints = analyze_retrieval_constraints(raw)
    if constraints.forbids_all_tools or constraints.forbids_external_retrieval or constraints.forbids("web_fetch"):
        return False
    return bool(_ASK.search(_instruction_text(raw)))


def _url(value: str) -> str:
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))
    except ValueError:
        return ''


def _asks(text: str, verbs: str) -> bool:
    # Ambiguous work stays read-only. The owner can make a new explicit request
    # or take over in the pane. A page cannot expand these frozen capabilities.
    positive = re.search(r'(?:' + _CLAUSE + r'|\bto\s+)(?:' + verbs + r')\b', text, re.IGNORECASE)
    negative = re.search(r"\b(?:do\s+not|don't|never|without)\s+(?:\w+\s+){0,2}(?:" + verbs + r')\b', text, re.IGNORECASE)
    return bool(positive and not negative)


class _TurnBoundary:
    def __init__(self, text: str):
        from core.instructional_request import asks_for_instructions_not_execution

        self.active = requested(text)
        executable = '' if asks_for_instructions_not_execution(text) else _instruction_text(text)
        self.urls = frozenset(_url(u.rstrip('.,;!?')) for u in _URL.findall(executable))
        self.follow = _asks(executable, r'follow|click|press|tap|visit|navigate')
        self.submit = _asks(executable, r'submit|send|sign\s+in|log\s+in')
        self.fill = _asks(executable, r'fill|enter|type|select|choose')
        self.download = _asks(executable, r'download|save\s+(?:this|the)\s+(?:file|attachment)')
        self.open = bool(self.urls and (self.active or _asks(executable, r'open|browse|visit|navigate|load')))
        self.proposed_urls: set[str] = set()


def confined() -> bool:
    boundary = _CONFINED.get()
    return bool(boundary and boundary.active)


def confine() -> None:
    boundary = _CONFINED.get()
    if boundary is not None:
        boundary.active = True


@contextmanager
def turn_scope(text: str):
    """Child work inherits authority; later text can only activate confinement."""
    from core.remote_fetch_policy import remote_fetch_veto

    parent = _CONFINED.get()
    if parent is not None:
        if requested(text):
            confine()
        yield
        return
    boundary = _TurnBoundary(str(text or ''))
    token = _CONFINED.set(boundary)
    try:
        with remote_fetch_veto(when=lambda: boundary.active):
            yield
    finally:
        _CONFINED.reset(token)


def tool_allowed(intent: str, arguments: dict | None = None) -> bool:
    from core.vool_browser.live_contracts import CLOSE, READS

    boundary = _CONFINED.get()
    if boundary is None or not boundary.active:
        return True
    if intent in READS | CLOSE | {'respond.direct'}:
        return True
    if intent in {'vool-browser.session.open', 'vool-browser.navigate'}:
        if not boundary.open:
            return False
        if arguments is None:
            return boundary.follow or bool(boundary.urls - boundary.proposed_urls)
        destination = _url(str(arguments.get('start_url') if intent.endswith('.open') else arguments.get('url')))
        return destination not in boundary.proposed_urls and (destination in boundary.urls or boundary.follow)
    if intent == 'vool-browser.click':
        return boundary.follow or boundary.submit
    if intent == 'vool-browser.type':
        return boundary.fill and (arguments is None or not arguments.get('submit') or boundary.submit)
    if intent == 'vool-browser.download':
        return boundary.download
    return False


def note_navigation_proposal(url: str) -> None:
    """One original request cannot repeat an approval under another tool alias."""
    boundary = _CONFINED.get()
    if boundary is not None and boundary.active:
        boundary.proposed_urls.add(_url(url))


def submission_allowed() -> bool:
    boundary = _CONFINED.get()
    return boundary is None or not boundary.active or boundary.submit
