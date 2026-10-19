"""Freeze browser authority from the owner's request, before page evidence arrives."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

_CONFINED = ContextVar("supervised_browser_turn", default=None)
_BROWSER = r"(?:(?:the|a|an|my|our|built[\s-]?in|live|integrated|vool)\s+){0,4}browser\b"
_CLAUSE = r"(?:^|[.;!?]\s*|\b(?:and|then)\s+)(?:(?:now|next|ok|okay|alright|great|good|thanks)[,!]?\s+){0,2}(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
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


def _executable(raw: str) -> bool:
    from core.instructional_request import asks_for_instructions_not_execution
    from core.retrieval_constraints import analyze_retrieval_constraints

    if asks_for_instructions_not_execution(raw):
        return False
    constraints = analyze_retrieval_constraints(raw)
    return not (constraints.forbids_all_tools or constraints.forbids_external_retrieval or constraints.forbids("web_fetch"))


def requested(text: str) -> bool:
    """An execution request, never an example, quoted page text or prohibition.

    A follow-up about the page already open in this chat ("fill in this form")
    is a request too, but only for the turn whose own words made it one: the
    turn boundary decides that once, from the chat's open page.
    """

    raw = str(text or "")
    if not _executable(raw):
        return False
    if _ASK.search(_instruction_text(raw)):
        return True
    boundary = _CONFINED.get()
    return bool(boundary and boundary.followup and _same_words(raw, boundary.text))


def _words(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _same_words(text: str, original: str) -> bool:
    # The turn's own words as later readers hold them: trimmed or wrapped, not
    # some other text (a page, a quoted example) that merely runs in this turn.
    words, original = _words(text), _words(original)
    return bool(words and original and (words in original or original in words))


def _url(value: str) -> str:
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))
    except ValueError:
        return ''


_POLITE = re.compile(r"^\s*(?:(?:now|next|ok|okay|alright|great|good|thanks)[,!]?\s+){0,2}(?:please\s+)?(?:can|could|would|will)\s+you\b", re.IGNORECASE)


def _asks(text: str, verbs: str) -> bool:
    # Ambiguous work stays read-only. The owner can make a new explicit request
    # or take over in the pane. A page cannot expand these frozen capabilities.
    # Only an instruction grants: "submit the form", "I want you to submit it" or
    # "can you submit it?". A question about the action ("is it safe to submit
    # this form?") and a prohibition ("no need to submit it") grant nothing.
    negative = re.search(r"\b(?:do\s+not|don't|never|without|no\s+need\s+to|not\s+to|refrain\s+from|avoid)\s+(?:\w+\s+){0,2}(?:" + verbs + r')\b', text, re.IGNORECASE)
    if negative:
        return False
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
        positive = re.search(r'(?:' + _CLAUSE + r'|\b(?:you|vool)\s+to\s+)(?:' + verbs + r')\b', sentence, re.IGNORECASE)
        if positive and (not sentence.rstrip().endswith('?') or _POLITE.match(sentence)):
            return True
    return False


# A follow-up names the open page or one of its parts; a bare "fill it in" stays
# ordinary chat, because nothing in its words says which page it means.
_PAGE_PART = re.compile(
    r"\b(?:this|that|the|its|their)\s+(?:(?:web\s*)?page|form|field|fields|box|button|link|site|website|address\s+field)\b",
    re.IGNORECASE,
)


def _page_open(chat: str) -> bool:
    from core.vool_browser import live

    if not chat or not live.available():
        return False
    try:
        item = live.session(chat)
    except live.BrowserFault:
        return False
    return bool(item is not None and not item.closed and item.url)


class _TurnBoundary:
    def __init__(self, text: str, chat: str = ""):
        from core.instructional_request import asks_for_instructions_not_execution

        self.text = str(text or "")
        self.followup = False
        self.active = requested(text)
        executable = '' if asks_for_instructions_not_execution(text) else _instruction_text(text)
        self.urls = frozenset(_url(u.rstrip('.,;!?')) for u in _URL.findall(executable))
        self.follow = _asks(executable, r'follow|click|press|tap|visit|navigate')
        self.submit = _asks(executable, r'submit|send|sign\s+in|log\s+in')
        self.fill = _asks(executable, r'fill|enter|type|select|choose')
        self.download = _asks(executable, r'download|save\s+(?:this|the)\s+(?:file|attachment)')
        self.open = bool(self.urls and (self.active or _asks(executable, r'open|browse|visit|navigate|load')))
        self.proposed_urls: set[str] = set()
        # A follow-up acts on the page already open in this chat. Its authority is
        # what its own words grant (fill, submit, follow); it opens nothing new
        # unless it names an address itself.
        if (not self.active and executable.strip() and _executable(self.text)
                and (self.fill or self.submit or self.follow) and _PAGE_PART.search(executable) and _page_open(chat)):
            self.active = self.followup = True


def confined() -> bool:
    boundary = _CONFINED.get()
    return bool(boundary and boundary.active)


def confine() -> None:
    boundary = _CONFINED.get()
    if boundary is not None:
        boundary.active = True


@contextmanager
def turn_scope(text: str, chat: str = ""):
    """Child work inherits authority; later text can only activate confinement."""
    from core.remote_fetch_policy import remote_fetch_veto

    parent = _CONFINED.get()
    if parent is not None:
        if requested(text):
            confine()
        yield
        return
    boundary = _TurnBoundary(str(text or ''), str(chat or ''))
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
        # Only an address the person wrote can be opened. Following a link on the
        # page is a click on an observed element, never an address the page supplied.
        if arguments is None:
            return bool(boundary.urls - boundary.proposed_urls)
        destination = _url(str(arguments.get('start_url') if intent.endswith('.open') else arguments.get('url')))
        return destination not in boundary.proposed_urls and destination in boundary.urls
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


def within_turn() -> bool:
    return _CONFINED.get() is not None


def submission_allowed() -> bool:
    boundary = _CONFINED.get()
    return boundary is None or not boundary.active or boundary.submit
