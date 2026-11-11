"""Freeze browser authority from the owner's request, before page evidence arrives."""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit

_CONFINED = ContextVar("supervised_browser_turn", default=None)
_BROWSER = r"(?:(?:the|teh|hte|a|an|my|our|built[\s-]?in|live|integrated|vool)\s+){0,4}(?:browser|broswer|brower|browswer)\b"
_OPEN = r"(?:open|opne|oepn|opn|visit|browse|navigate|load|go\s+to)"
_CLAUSE = r"(?:^|[.;!?]\s*|\b(?:and|then)\s+)(?:(?:now|next|ok|okay|alright|great|good|thanks)[,!]?\s+){0,2}(?:(?:please|pls|plz)\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:(?:please|pls|plz)\s+)?"
_ASK = re.compile(
    _CLAUSE + r"(?:use|open|launch|start|show)\s+" + _BROWSER
    + r"|" + _CLAUSE + _OPEN + r"\b[^\n]{0,500}\b(?:in|with|using)\s+" + _BROWSER
    + r"|^\s*(?:please\s+)?(?:browse|navigate\s+to)\s+https?://"
    # "browse to the wikipedia page for Riga", "can you browse over to example.com"
    + r"|" + _CLAUSE + r"browse\s+(?:over\s+)?to\b",
    re.IGNORECASE,
)
_URL = re.compile(r'https?://[^\s<>"\x27`]+', re.IGNORECASE)
# A site named without "https://" ("open news.ycombinator.com"), on a common public suffix. File
# names (notes.md, run.py, setup.sh) are not sites: those suffixes are left out on purpose.
_TLDS = (r"com|org|net|io|dev|app|ai|co|uk|de|fr|lv|lt|ee|eu|info|edu|gov|me|tv|news|xyz|page|site|online|"
         r"blog|us|ca|au|jp|ru|nl|se|no|fi|pl|es|it|ch|at|be|cz|dk|pt|br|in|ua|tr|kr|cn|tw|hk|sg|nz|ie|il|gr")
_BARE = re.compile(r"(?<![@\w./:-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:" + _TLDS + r")(?:/[^\s<>\"\x27`]*)?)(?![\w@-])",
                   re.IGNORECASE)


def _trim(raw: str) -> str:
    """An address as written in a sentence: no closing punctuation or unmatched bracket after it."""
    value = str(raw or "")
    while value:
        last = value[-1]
        if last in ".,;:!?\"\x27":
            value = value[:-1]
        elif last in ")]}" and value.count({")": "(", "]": "[", "}": "{"}[last]) < value.count(last):
            value = value[:-1]
        else:
            break
    return value


def addresses(text: str) -> list[str]:
    """Every address the words name, as written (scheme ones first, then bare site names)."""
    raw = str(text or "")
    found = [_trim(u) for u in _URL.findall(raw)]
    rest = _URL.sub(" ", raw)
    found += ["https://" + _trim(m) for m in _BARE.findall(rest)]
    return [u for u in found if u]
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


_NEGATION = re.compile(r"\b(?:not|no|never|nobody|nothing|neither|nor|without|avoid|refrain|stop)\b|n't\b|\bno\s+one\b", re.IGNORECASE)


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
        question = sentence.rstrip().endswith('?') and not _POLITE.match(sentence)
        for clause in re.split(r'[,;:]|\bbut\b', sentence):
            # A clause with any negation in it ("I never asked you to submit the form",
            # "nobody wants you to submit it", "no need to submit it") grants nothing.
            if question or _NEGATION.search(clause):
                continue
            if re.search(r'(?:' + _CLAUSE + r'|\b(?:you|vool|browser)\s+to\s+)(?:' + verbs + r')\b', clause.strip(), re.IGNORECASE):
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
        self.urls = frozenset(_url(u) for u in addresses(executable))
        self.follow = _asks(executable, r'follow|click|press|tap|visit|navigate|open\s+(?:the|that|this)\s+link')
        # "search <site> for <words>" is filling the site's search box and sending it; the
        # form send still shows its fields on the card before anything is sent.
        searches = _asks(executable, r'search')
        self.submit = _asks(executable, r'submit|send|sign\s+in|log\s+in') or searches
        self.fill = _asks(executable, r'fill|enter|type|select|choose') or searches
        self.download = _asks(executable, r'download|save\s+(?:this|the)\s+(?:file|attachment)')
        self.open = bool(self.urls and (self.active or _asks(executable, r'open|opne|oepn|browse|visit|navigate|load')))
        self.proposed_urls: set[str] = set()
        # How this turn's browser request ended when it never ran: the reply names it truthfully.
        self.unanswered: dict[str, str] | None = None
        # The browser steps of this turn that ran after Allow, so a reply that fails later
        # never claims nothing was looked at.
        self.completed: list[dict[str, str]] = []
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


def note_unanswered(op: str, url: str, status: str) -> None:
    """Record that this turn's browser step did not run, and why, for the turn's reply."""
    boundary = _CONFINED.get()
    if boundary is not None:
        boundary.unanswered = {"op": str(op or ""), "url": str(url or ""), "status": str(status or "")}


def note_completed(op: str, url: str) -> None:
    """Record that one of this turn's browser steps ran and observed the page."""
    boundary = _CONFINED.get()
    if boundary is not None and len(boundary.completed) < 20:
        boundary.completed.append({"op": str(op or ""), "url": str(url or "")})


def completed() -> list[dict[str, str]]:
    """This turn's browser steps that ran, oldest first."""
    boundary = _CONFINED.get()
    return [dict(step) for step in boundary.completed] if boundary is not None else []


def unanswered() -> dict[str, str] | None:
    """This turn's browser step that never ran (refused, timed out, taken over), if any."""
    boundary = _CONFINED.get()
    return dict(boundary.unanswered) if boundary is not None and boundary.unanswered else None


def turn_request() -> str:
    """The person's own words for this turn, as the browser boundary holds them."""
    boundary = _CONFINED.get()
    return boundary.text if boundary is not None else ""


def written_addresses(text: str, target: str) -> tuple[str, ...]:
    """The spellings in the person's words of the address a proposal opened, without a
    trailing slash, so they bind to the words around them at word boundaries."""
    wanted = _url(str(target or ''))
    raw_text = str(text or '')
    found = [_trim(u) for u in _URL.findall(raw_text)] + [_trim(m) for m in _BARE.findall(_URL.sub(' ', raw_text))]
    return tuple(dict.fromkeys(raw.rstrip('/') for raw in found if wanted and _url(raw if '://' in raw else 'https://' + raw) == wanted))


def within_turn() -> bool:
    return _CONFINED.get() is not None


def submission_allowed() -> bool:
    boundary = _CONFINED.get()
    return boundary is None or not boundary.active or boundary.submit
