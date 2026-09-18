"""The one contract every external adapter implements, and the one way out of the machine.

Nothing in this module performs I/O, reads policy or reads a credential. It is the vocabulary
the boundary is written in: a request an adapter may ASK for, a response it is HANDED, and the
typed refusals it must not swallow.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Generic, Protocol, TypeVar, runtime_checkable

# --------------------------------------------------------------------------------------
# Transport vocabulary
# --------------------------------------------------------------------------------------


class TransportDeniedError(RuntimeError):
    """VOOL refused the request before any socket. An adapter must let this propagate."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = str(reason or "denied")
        self.detail = str(detail or "")


class TransportUnknownError(RuntimeError):
    """The request left the machine and its outcome could not be proven.

    NOT a failure. A caller that treats this as "it did not happen" invents an effect truth
    it does not have; :mod:`core.effect_reconciliation` owns what happens next.
    """

    def __init__(self, reason: str, *, detail: str = "", logical_effect_id: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = str(reason or "unknown")
        self.detail = str(detail or "")
        self.logical_effect_id = str(logical_effect_id or "")


class TransportAcceptedError(TransportUnknownError):
    """A mutating request was ACCEPTED -- its success status arrived -- but the rest of the reply failed.

    The effect is proven applied; its result is not known. A caller that only understands UNKNOWN stays
    safe (this IS unknown to it, never a failure); a family contract that can say more promotes it (a
    calendar write becomes accepted-but-unverified). ``status_code`` is the success status that proved it.
    """

    def __init__(self, status_code: int, *, detail: str = "", logical_effect_id: str = "") -> None:
        super().__init__("accepted_reply_unreadable", detail=detail, logical_effect_id=logical_effect_id)
        self.status_code = int(status_code)


class ForgeRefusedError(RuntimeError):
    """The forge ANSWERED and definitively did not serve what was asked.

    The reply came back, so this is neither UNKNOWN (the outcome is proven: nothing was
    served) nor a VOOL-side denial. An adapter raises it so no caller can read a refused
    body as an empty list or an absent ref. ``reason`` is one of ``not_found`` (the one
    truthful absence), ``rate_limited`` (see :class:`ForgeRateLimitedError`) or
    ``http_<status>`` for every other definitive answer.
    """

    def __init__(self, status_code: int, *, reason: str = "", detail: str = "") -> None:
        super().__init__(detail or reason or f"forge refused with HTTP {status_code}")
        self.status_code = int(status_code)
        self.reason = str(reason or f"http_{status_code}")
        self.detail = str(detail or "")


class ForgeRateLimitedError(ForgeRefusedError):
    """The definitive rate-limit answer: the forge served NO data and said why.

    Reading this as "zero rows" or "ref absent" turns an unserved read into invented
    absence — the exact confusion that would let a landed push be classified safe-to-retry
    by reconciliation. ``retry_after`` carries the forge's own wait hint when it gave one.
    """

    def __init__(self, *, retry_after: float = 0.0, status_code: int = 403, detail: str = "") -> None:
        super().__init__(status_code, reason="rate_limited", detail=detail)
        self.retry_after = float(retry_after or 0.0)


class ForgeAcceptedUnreadableError(RuntimeError):
    """The forge ACCEPTED a write (2xx) and its reply could not be decoded into the typed result.

    This is neither a refusal nor a failure. The mutation outcome is UNKNOWN in exactly the way
    a lost reply is unknown: the write may already be on the forge with no number or URL to
    cite, and treating the decode error as a definitive refusal would authorize a blind resend
    that could double-apply. Raised by the WRITE methods only — a read whose body cannot be
    decoded served no data and stays a :class:`ForgeRefusedError`.
    """

    def __init__(self, status_code: int, *, reason: str = "unreadable_accepted_reply", detail: str = "") -> None:
        super().__init__(detail or reason or f"forge accepted (HTTP {status_code}) but the reply is unreadable")
        self.status_code = int(status_code)
        self.reason = str(reason or "unreadable_accepted_reply")
        self.detail = str(detail or "")


@dataclass(frozen=True)
class KasRequest:
    """What an adapter asks VOOL to send.

    ``auth`` is a credential BINDING ID — an opaque handle, never a secret. The transport
    resolves it and attaches the credential itself; the adapter never holds, sees or logs a
    secret value. ``headers`` carrying an ``authorization`` key is refused by the transport
    rather than honoured: that would be an adapter minting its own credential authority.
    """

    method: str
    url: str
    purpose: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    auth: str = ""
    timeout: float = 20.0
    mutating: bool = False
    idempotency_key: str = ""

    def with_url(self, url: str) -> KasRequest:
        return KasRequest(
            method=self.method,
            url=url,
            purpose=self.purpose,
            headers=dict(self.headers),
            body=self.body,
            auth=self.auth,
            timeout=self.timeout,
            mutating=self.mutating,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True)
class KasResponse:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= int(self.status) < 300

    def json(self) -> Any:
        import json

        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8", "replace"))

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@runtime_checkable
class KasTransport(Protocol):
    """The ONLY egress an adapter has. Built by VOOL, injected into the adapter."""

    def __call__(self, request: KasRequest) -> KasResponse: ...


# --------------------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterConfig:
    """Everything an adapter is allowed to be configured with.

    ``auth_binding`` is a binding id. There is deliberately no field that can carry a secret.
    """

    provider_id: str
    base_url: str
    namespace: str = ""
    auth_binding: str = ""
    options: Mapping[str, str] = field(default_factory=dict)


class ExternalAdapter:
    """Base for every KAS adapter.

    Deliberately NOT an ABC: it declares no abstract member of its own, and making it one purely
    to look like an interface would be a lie a linter is right to call out. The abstractness lives
    where there is something to be abstract about -- `ForgeAdapter` inherits `abc.ABC` itself, so
    an incomplete forge adapter fails at construction rather than at its first missing call.

    Subclasses translate. They do not decide. The constructor is final on purpose: an adapter
    that wants extra state at construction time is usually an adapter about to hold authority.
    """

    kind: ClassVar[str] = ""
    provider_id: ClassVar[str] = ""

    def __init__(self, *, transport: KasTransport, config: AdapterConfig) -> None:
        self._transport = transport
        self._config = config

    @property
    def config(self) -> AdapterConfig:
        return self._config

    def send(self, request: KasRequest) -> KasResponse:
        """Hand a request to VOOL. This is the adapter's entire outward reach.

        A request that names no binding rides the one the adapter was constructed with: the
        binding travels with the adapter, so a call site cannot silently drop the account's
        credential and send an anonymous request.
        """

        if not request.auth and self._config.auth_binding:
            request = replace(request, auth=self._config.auth_binding)
        return self._transport(request)


# --------------------------------------------------------------------------------------
# The forge contract — ONE contract, GitHub and GitLab are implementations of it
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ForgeRepository:
    provider_id: str
    full_name: str
    default_branch: str
    clone_url: str
    private: bool = False


@dataclass(frozen=True)
class ForgePullRequest:
    provider_id: str
    number: str
    title: str
    state: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    merge_state: str = ""
    draft: bool = False
    #: The forge's own page for this pull request. Empty when a read path's provider payload
    #: carries none; a CREATE answer must carry it, because the URL is the forge's durable
    #: handle, never an identifier this side invents.
    url: str = ""
    #: The body text the forge currently holds. Read paths may leave it empty when the caller
    #: did not ask for content; CREATE/UPDATE answers must carry it, because exact-text
    #: verification ("is what the forge now says what the operator authorized?") is the only
    #: thing that turns a submitted write into a proven one.
    body: str = ""


@dataclass(frozen=True)
class ForgeCiJob:
    provider_id: str
    job_id: str
    name: str
    status: str
    conclusion: str
    head_sha: str
    log_ref: str = ""


@dataclass(frozen=True)
class ForgeArtifact:
    provider_id: str
    artifact_id: str
    name: str
    size_bytes: int
    download_ref: str = ""


_RowT = TypeVar("_RowT")


@dataclass(frozen=True)
class ForgeListing(Generic[_RowT]):
    """A listing a bounded adapter actually finished — or honestly did not.

    ``rows`` is what was read; ``truncated`` is True when the page bound stopped reading
    before the forge ran out of pages. A truncated listing is DATA, never a verdict: "no
    failing job among these rows" must never be read as "CI is green" over a listing the
    runtime admits it did not finish.
    """

    rows: list[_RowT]
    pages_read: int = 1
    truncated: bool = False


@dataclass(frozen=True)
class ForgeIssue:
    provider_id: str
    number: str
    title: str
    state: str
    author: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    comments_count: int = 0


@dataclass(frozen=True)
class ForgeIssueComment:
    provider_id: str
    comment_id: str
    author: str = ""
    body: str = ""
    created_at: str = ""
    #: The forge's own page for this comment once it exists (empty on read paths whose provider
    #: payload omits it). A POST answer carries it; the runtime hands it to the operator as the
    #: proof-of-place for the exact text that was accepted.
    url: str = ""


class ForgeAdapter(ExternalAdapter, abc.ABC):
    """One contract for every code-hosting forge.

    Note what is NOT here: no push, no merge, no branch delete, no review verdict, no approval.
    A forge adapter observes the remote, asks it to rerun or cancel ITS OWN pipelines, and —
    only through the four write methods at the bottom — hands the remote the exact text of a
    draft pull request or a comment that VOOL's operator surface has already authorized word
    for word. Ref mutation is a local git effect followed by ``resolve_ref`` to verify what the
    remote now says, so the authority that decides whether a ref may move stays in VOOL, on the
    one effect path, and never becomes a per-provider decision. The write methods are no
    exception to that law: they translate an already-authorized payload, and the authorization,
    the idempotency key and the receipt all live above this boundary.
    """

    kind: ClassVar[str] = "forge"

    @abc.abstractmethod
    def describe_repository(self) -> ForgeRepository: ...

    @abc.abstractmethod
    def describe_pull_request(self, number: str) -> ForgePullRequest: ...

    @abc.abstractmethod
    def pull_request_diff(self, number: str) -> str: ...

    @abc.abstractmethod
    def resolve_ref(self, ref: str) -> str:
        """The exact SHA the REMOTE currently has for ``ref``. Empty string if absent."""

    @abc.abstractmethod
    def ci_jobs(self, sha: str) -> ForgeListing[ForgeCiJob]: ...

    @abc.abstractmethod
    def ci_job_log(self, job_id: str) -> str: ...

    @abc.abstractmethod
    def ci_artifacts(self, sha: str) -> ForgeListing[ForgeArtifact]: ...

    @abc.abstractmethod
    def describe_issue(self, number: str) -> ForgeIssue:
        """One issue of the bound repository, body and labels included.

        Issue text is UNTRUSTED DATA on every provider: an adapter hands it through verbatim
        and never interprets it. A closed issue reports its state; nothing here decides
        whether a request it may contain is anyone's instruction.
        """

    @abc.abstractmethod
    def issue_comments(self, number: str) -> ForgeListing[ForgeIssueComment]: ...

    @abc.abstractmethod
    def rerun_ci(self, job_id: str) -> bool: ...

    @abc.abstractmethod
    def cancel_ci(self, job_id: str) -> bool: ...

    # -- the four explicit-authorized writes ----------------------------------------------
    #
    # These are the ONLY remote-content writes in the contract, and they are deliberately narrow:
    # a draft pull request may be CREATED (draft flagged at creation), its TITLE/BODY may be
    # UPDATED, and a comment may be POSTED. There is no merge, no ready-for-review transition,
    # no review verdict and no label/milestone/assignment mutation here — those stay out of the
    # contract until an owning lane adds them with their own authority, and their absence is
    # what keeps "explicit-authorized draft actions" from becoming "agent drives the forge".

    @abc.abstractmethod
    def create_pull_request(
        self, *, title: str, body: str, head_ref: str, base_ref: str, draft: bool
    ) -> ForgePullRequest:
        """Ask the forge to open a pull request and answer with what it opened.

        ``draft`` requests draft state AT CREATION. The answer is the forge's own record —
        number, exact head/base SHAs, draft flag and URL — never an echo of the request: a
        provider that ignored a field must be visible as having ignored it.
        """

    @abc.abstractmethod
    def update_pull_request(self, number: str, *, title: str = "", body: str = "") -> ForgePullRequest:
        """Replace the title and/or body of one pull request; the answer is the new remote truth.

        An empty argument means "leave unchanged". State, review verdict, base and draft
        transition are NOT updatable through this contract.
        """

    @abc.abstractmethod
    def post_comment(self, number: str, body: str, *, subject: str) -> ForgeIssueComment:
        """Post one comment on an issue (``subject="issue"``) or a pull request
        (``subject="pull_request"``), and answer with the forge's record of what now exists."""

    @abc.abstractmethod
    def find_pull_request(self, *, head_ref: str, base_ref: str) -> ForgePullRequest | None:
        """The open pull request a head/base pair already has, or None when the completed
        lookup says there is none.

        This is the reconciliation read for an unproven CREATE: it is a SEARCH, so a completed
        empty answer is proven absence — but a refused or rate-limited lookup MUST raise, never
        return None, because "I could not look" is not "it is not there".
        """


# --------------------------------------------------------------------------------------
# The calendar contract — ONE contract, CalDAV is one implementation of it
# --------------------------------------------------------------------------------------


class CalendarRefusedError(RuntimeError):
    """The calendar provider ANSWERED and definitively did not serve what was asked.

    The mirror of :class:`ForgeRefusedError` for the calendar family: the reply came back, so
    this is neither UNKNOWN (outcome proven: nothing was served) nor a VOOL-side denial.
    ``reason`` is one of ``not_found`` (the one truthful absence), ``precondition_failed``
    (the server rejected an If-Match — the entity changed server-side), ``rate_limited``, or
    ``http_<status>`` for every other definitive answer.
    """

    def __init__(self, status_code: int, *, reason: str = "", detail: str = "") -> None:
        super().__init__(detail or reason or f"calendar provider refused with HTTP {status_code}")
        self.status_code = int(status_code)
        self.reason = str(reason or f"http_{status_code}")
        self.detail = str(detail or "")


class CalendarReadUnusableError(CalendarRefusedError):
    """The provider ANSWERED a read, but what it served cannot be read as calendar data.

    A collection reply without its list of rows, a row or event without a usable identity or time,
    a success status whose body is not the event that was asked for. It is NOT ``not_found`` (the
    one reason that proves absence) and NOT an empty result: no availability, recovery or cancellation
    decision may read it as either. ``status_code`` is the status the unusable reply came with
    (0 when the source is not HTTP).
    """

    def __init__(self, status_code: int = 0, *, detail: str = "") -> None:
        super().__init__(status_code, reason="unreadable_response",
                         detail=detail or "the provider's reply could not be read as calendar data")


class CalendarWriteAcceptedError(RuntimeError):
    """The provider ACCEPTED a mutating calendar write, but its result could not be read back or decoded.

    Neither a refusal (the effect happened) nor unknown (acceptance is proven). ``provider_uid`` is
    the provider's identity for the written event when the acceptance carried one ("" when it did
    not), so the caller retains it at once and later verifies by reading exactly that identity.
    Reading this as "nothing was changed" invents absence over an applied effect.
    """

    def __init__(self, *, provider_uid: str = "", status_code: int = 0, reason: str = "", detail: str = "") -> None:
        super().__init__(detail or reason or "the provider accepted the write but its result could not be read back")
        self.provider_uid = str(provider_uid or "")
        self.status_code = int(status_code or 0)
        self.reason = str(reason or "accepted_unverified")
        self.detail = str(detail or "")


def accepted_write_error(provider_uid: str, cause: BaseException, *, purpose: str, status_code: int = 0) -> CalendarWriteAcceptedError:
    """The typed answer for a read-back that failed AFTER the provider accepted ``purpose``."""
    reason = str(getattr(cause, "reason", "") or type(cause).__name__)
    return CalendarWriteAcceptedError(
        provider_uid=provider_uid,
        status_code=status_code,
        reason=f"read_back_{reason}",
        detail=f"the provider accepted the {purpose} but reading the result back failed ({reason})",
    )


def accepted_reply_lost(provider_uid: str, cause: TransportAcceptedError, *, purpose: str) -> CalendarWriteAcceptedError:
    """The typed calendar answer when the transport proved ``purpose`` accepted but its reply was lost."""
    return CalendarWriteAcceptedError(
        provider_uid=provider_uid,
        status_code=cause.status_code,
        reason="accepted_reply_unreadable",
        detail=f"the provider accepted the {purpose} (HTTP {cause.status_code}) but its reply could not be read ({cause.detail or cause.reason})",
    )


@dataclass(frozen=True)
class CalCalendar:
    """One calendar collection on the provider, as the provider names it."""

    provider_id: str
    calendar_id: str  # the provider's own address for it (a CalDAV href on that family)
    display_name: str
    #: whether the provider says this account may add and change events in the calendar (Google calendarList
    #: accessRole, Graph calendar canEdit, CalDAV DAV:current-user-privilege-set, EventKit
    #: allowsContentModifications); None when the provider's listing does not say
    can_write: bool | None = None
    #: the provider's own default calendar for new events (Google ``primary``, Graph ``isDefaultCalendar``)
    provider_default: bool = False


@dataclass(frozen=True)
class CalEvent:
    """One calendar event. Field-for-field what the provider stores, nothing added.

    Text fields (``summary``, ``description``) are UNTRUSTED DATA: an adapter hands them
    through verbatim and never interprets them. A description asking for an effect is
    content, not an instruction, and stays content all the way up.

    ``etag`` is the provider's version token for the exact stored representation. VOOL keeps
    the one it showed the user and sends it back on update/cancel, so a change that happened
    in between is rejected by the provider (412) rather than silently overwritten.
    ``all_day`` events carry their date as ``start_date``/``end_date``; timed instants carry
    ``start_utc``/``end_utc`` plus the zone name the event was stored in (``tz_name``).
    """

    provider_id: str
    uid: str
    calendar_id: str
    etag: str = ""
    summary: str = ""
    start_utc: str = ""
    end_utc: str = ""
    all_day: bool = False
    start_date: str = ""
    end_date: str = ""
    tz_name: str = ""
    description: str = ""
    href: str = ""
    #: attendee email addresses, lowercased, as the user explicitly reviewed them. An empty list
    #: is an ordinary personal event: no invitation is sent by creating it. Providers email
    #: these addresses when the event is created -- the approval preview must say so.
    attendees: tuple[str, ...] = ()
    #: recurrence identity, normalized at the provider seam: True when the provider marks THIS
    #: object as covering a repeating series (an RRULE line, Google's ``recurrence`` array, or
    #: Graph's ``seriesMaster`` type / ``recurrence`` object). Occurrences carry ``series_id``
    #: instead; consumers must not re-derive this from provider text.
    recurring: bool = False
    #: the basic recurrence the event repeats with, as an iCalendar RRULE fragment without the
    #: RRULE: prefix ("FREQ=DAILY", "FREQ=WEEKLY;BYDAY=MO,TU", "FREQ=MONTHLY;BYMONTHDAY=3").
    #: Empty for a one-off. Only the basic shapes the chat vocabulary supports are carried;
    #: a provider rule outside them stays in the provider's own representation.
    recurrence_rule: str = ""
    #: the provider's stored representation verbatim (round-trip fidelity for updates:
    #: unknown properties are preserved rather than dropped and reinvented)
    raw_component: str = ""
    #: a provider-supported correlation token read back from the stored object (Graph's
    #: ``transactionId``): which approved operation wrote it. Matching content never proves that.
    correlation_id: str = ""
    #: where the event is held, as the provider stores it (Google ``location``, Graph
    #: ``location.displayName``, iCalendar LOCATION, EventKit ``location``). Untrusted text, like ``summary``.
    location: str = ""
    #: the join address the provider stores for an online meeting (Google ``hangoutLink`` or a video
    #: conference entry point, Graph ``onlineMeeting.joinUrl``, an RFC 7986 CONFERENCE line). A presenter shows
    #: it only when it is https.
    meeting_url: str = ""
    #: the provider's own page for the event, for a person to open (Google ``htmlLink``, Graph ``webLink``, the
    #: iCalendar URL property, EventKit ``URL``); never the CalDAV resource ``href``, which is a protocol address
    web_url: str = ""
    #: the provider marks the event cancelled (Google ``status`` cancelled, Graph ``isCancelled``, iCalendar
    #: STATUS:CANCELLED, EventKit ``status`` canceled)
    cancelled: bool = False
    #: for one occurrence of a recurring series: the series' provider id and the occurrence's original start
    #: (Google ``recurringEventId``/``originalStartTime``, Graph ``seriesMasterId``/``originalStart``,
    #: iCalendar RECURRENCE-ID, EventKit ``occurrenceDate``)
    series_id: str = ""
    original_start: str = ""


class CalendarAdapter(ExternalAdapter, abc.ABC):
    """One contract for every calendar provider.

    The identity law: an event is addressed by (calendar_id, uid) — a stable provider identity
    that survives client restarts — and versioned by ``etag``. Mutations carry the etag the
    CALLER last saw (If-Match), so a concurrent provider-side change is refused with
    :class:`CalendarRefusedError` ``precondition_failed`` instead of being silently clobbered.
    Nothing here decides whether a mutation may happen; VOOL's approval door owns that.
    """

    kind: ClassVar[str] = "calendar"

    @abc.abstractmethod
    def list_calendars(self) -> list[CalCalendar]: ...

    @abc.abstractmethod
    def events_in_range(self, calendar_id: str, *, start_utc: str, end_utc: str) -> list[CalEvent]:
        """Every stored event overlapping [start_utc, end_utc), in start order."""

    @abc.abstractmethod
    def get_event(self, calendar_id: str, uid: str) -> CalEvent:
        """One event by stable identity. ``CalendarRefusedError(not_found)`` if absent."""

    @abc.abstractmethod
    def create_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        """Store a new event; returns it with the provider's etag and href."""

    @abc.abstractmethod
    def update_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        """Replace an existing event at ``event.etag`` (If-Match); returns the new etag."""

    @abc.abstractmethod
    def cancel_event(self, calendar_id: str, uid: str, etag: str) -> bool:
        """Delete the event at ``etag``. Absence raises not_found; refusal raises typed."""

    def durable_to_provider_id(self, durable_uid: str) -> str:
        """The provider event id a durable logical intent maps to, or "" when the
        provider assigns ids itself.

        The DEFAULT is identity: providers whose clients choose ids (CalDAV UIDs) address the
        durable uid directly. Google maps it into its id alphabet. Graph and EventKit return ""
        because only the provider assigns their ids; identity there comes from an id retained
        at acceptance or a provider-supported correlation (``durable_correlation``), never from
        matching content.
        """
        return str(durable_uid or "")

    def durable_correlation(self, durable_uid: str) -> str:
        """The provider-supported correlation token a durable intent's writes carry, or "".

        A provider that assigns ids itself may document a field the client sets on create and
        reads back later (Graph: ``transactionId``). Recovery uses it to recognise an operation's
        own stored object; an event that merely has the same content is never identified this way.
        """
        return ""


__all__ = [
    "AdapterConfig",
    "CalCalendar",
    "CalEvent",
    "CalendarAdapter",
    "CalendarReadUnusableError",
    "CalendarRefusedError",
    "CalendarWriteAcceptedError",
    "ExternalAdapter",
    "ForgeAcceptedUnreadableError",
    "ForgeAdapter",
    "ForgeArtifact",
    "ForgeCiJob",
    "ForgeIssue",
    "ForgeIssueComment",
    "ForgeListing",
    "ForgePullRequest",
    "ForgeRateLimitedError",
    "ForgeRefusedError",
    "ForgeRepository",
    "KasRequest",
    "KasResponse",
    "KasTransport",
    "TransportAcceptedError",
    "TransportDeniedError",
    "TransportUnknownError",
    "accepted_reply_lost",
    "accepted_write_error",
]
