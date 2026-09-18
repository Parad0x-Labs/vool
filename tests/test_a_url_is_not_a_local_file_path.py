"""A web address must reach the web, and a local path must stay off it.

Driven against the running daemon on `5cdce5d` over `/api/chat`, one fresh session per turn:

    "fetch https://example.com and tell me what the page says"
        -> "I cannot read that path in this lane. I can only read files inside: ~/Desktop,
            ~/Downloads, ~/Documents. For repo or project paths, use workspace.read_file ..."  (0.6s)
    "what does https://www.rfc-editor.org/rfc/rfc7231 say about the GET method?"
        -> the same sentence                                                                   (0.6s)
    "can you open https://httpbin.org/json and show me the content?"
        -> "You linked a URL, but I didn't actually open it -- I can't browse arbitrary web pages
            in this build ..."                                                                 (0.7s)

`web.fetch` was working the whole time: called directly it returns `ok` and the page text.

Two causes. The first is a one-character coincidence: `_extract_machine_file_read_target` accepts a
WINDOWS DRIVE path, `[A-Za-z]:[\\/]`, and in `https://example.com` the `s` of `https` is followed by
`://`. So the extractor returned the local path `s://example.com` and the machine lane refused it
for being outside the home folders. The second is that nothing routed a URL to `web.fetch` at all,
leaving the URL-grounding backstop as the only thing that spoke — correctly refusing to invent a
summary, in a sentence that denied a capability the runtime has.

This is the exact mirror of the PDF lane, which exists so a local path never becomes a web search.
Both directions are asserted here, because fixing one by breaking the other is the obvious wrong
repair: "download https://x.com/a.txt to ~/Desktop/a.txt" names both and must still find its
local path.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_machine import _extract_machine_file_read_target
from core.agent_runtime.fast_paths_web import url_read_request

# --------------------------------------------------------------------------------------
# A URL is not a path
# --------------------------------------------------------------------------------------

REPORTED_URL_PHRASINGS = (
    "fetch https://example.com and tell me what the page says",
    "what does https://www.rfc-editor.org/rfc/rfc7231 say about the GET method?",
    "can you open https://httpbin.org/json and show me the content?",
    "read https://example.org/notes.txt out to me",
    "open http://neverssl.com and show me the content",
    "take a look at https://example.com/index.html and summarise it",
    "grab https://example.com/data.json for me",
    "what's on https://example.com/page.html ?",
)


@pytest.mark.parametrize("phrasing", REPORTED_URL_PHRASINGS)
def test_a_url_is_never_extracted_as_a_local_file_path(phrasing: str) -> None:
    """The `s://` coincidence, pinned per phrasing.

    `.txt`, `.json` and `.html` are in there on purpose: those are the URLs whose tail also looks
    like a filename, so they are the ones most likely to be re-captured by a future path pattern.
    """
    assert _extract_machine_file_read_target(phrasing) is None


@pytest.mark.parametrize("phrasing", REPORTED_URL_PHRASINGS)
def test_the_web_lane_claims_every_reported_phrasing(phrasing: str) -> None:
    request = url_read_request(phrasing)
    assert request is not None, f"no lane would fetch {phrasing!r}"
    assert request["url"].startswith(("http://", "https://"))
    assert " " not in request["url"]


def test_trailing_punctuation_is_not_part_of_the_url() -> None:
    assert url_read_request("what's on https://example.com/page.html ?")["url"].endswith(".html")
    assert url_read_request("read https://example.com/a.html, then tell me")["url"].endswith(".html")


# --------------------------------------------------------------------------------------
# A path is not a URL
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrasing,expected",
    [
        ("read ~/Downloads/report.pdf", "~/Downloads/report.pdf"),
        ("open ~/Desktop/notes.txt", "~/Desktop/notes.txt"),
        (r"open C:\Users\me\notes.txt", r"C:\Users\me\notes.txt"),
        # Both in one sentence: stripping the URL must not strip the path with it.
        ("read https://x.com/a.txt then open ~/Desktop/a.txt", "~/Desktop/a.txt"),
    ],
)
def test_a_real_local_path_still_reaches_the_machine_lane(phrasing: str, expected: str) -> None:
    target = _extract_machine_file_read_target(phrasing)
    assert target is not None, phrasing
    assert target["path"] == expected


@pytest.mark.parametrize(
    "phrasing",
    [
        # A URL mentioned in passing is context, not an instruction to go and read it.
        "my project lives at https://github.com/x/y anyway how do I center a div",
        "I found it on https://news.ycombinator.com yesterday and forgot the title",
        # Saving bytes to disk belongs to the download lane, which runs above this one. The first
        # two carry no read verb and would be declined anyway; the rest carry BOTH, and are the
        # ones that actually exercise the download guard. Sabotaging that guard left the first two
        # green, which is how they were found to be measuring nothing.
        "download https://example.com/a.txt to my desktop",
        "save https://example.com/a.txt to ~/Downloads/a.txt",
        "grab https://example.com/a.txt and save it to my desktop",
        "fetch https://example.com/report.csv and write it to ~/Downloads/report.csv",
        "open https://example.com/a.txt and download it to my downloads",
        "read https://example.com/a.txt and put it in ~/Documents",
        # No URL at all.
        "read ~/Downloads/report.pdf",
        "search the web for the population of Iceland",
    ],
)
def test_the_web_lane_leaves_alone_what_is_not_a_fetch_request(phrasing: str) -> None:
    assert url_read_request(phrasing) is None


# --------------------------------------------------------------------------------------
# The lane is actually wired, and it actually fetches
# --------------------------------------------------------------------------------------


class _Agent:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _fast_path_result(self, *, response: str, reason: str, **_: object) -> dict[str, object]:
        return {"response": response, "reason": reason}

    def _emit_runtime_event(self, _source_context: object, **kwargs: object) -> None:
        self.events.append(str(kwargs.get("tool_name") or ""))


def test_the_lane_calls_web_fetch_and_returns_the_tool_s_own_text(monkeypatch) -> None:
    """No network here — the point is the wiring and the rendering, not example.com's uptime."""
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_web

    captured: dict[str, object] = {}

    def _fake_execute(intent, arguments, *, source_context=None):
        captured["intent"] = intent
        captured["arguments"] = dict(arguments)
        return SimpleNamespace(
            ok=True,
            status="ok",
            response_text="",
            details={
                "url": "https://example.com",
                "final_url": "https://example.com",
                "text": "Example Domain. This domain is for use in illustrative examples.",
                "truncated": False,
            },
        )

    monkeypatch.setattr("core.runtime_execution_tools.execute_runtime_tool", _fake_execute)
    agent = _Agent()
    result = fast_paths_web.maybe_handle_url_read(
        agent,
        "fetch https://example.com and tell me what the page says",
        session_id="t",
        source_context={},
    )
    assert captured["intent"] == "web.fetch"
    assert captured["arguments"]["url"] == "https://example.com"
    assert agent.events == ["web.fetch", "web.fetch"]
    assert "This domain is for use in illustrative examples." in str(result["response"])


@pytest.mark.parametrize(
    "status,expected",
    [
        ("disabled_by_policy", "policy setting"),
        ("captcha", "bot check"),
        ("login_wall", "behind a login"),
    ],
)
def test_each_web_barrier_keeps_its_own_words(monkeypatch, status: str, expected: str) -> None:
    """Same rule as the PDF statuses: a policy block, a bot wall and a login wall are three
    different things a user does three different things about."""
    from types import SimpleNamespace

    from core.agent_runtime import fast_paths_web

    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=False, status=status, response_text="", details={"reason": ""}
        ),
    )
    result = fast_paths_web.maybe_handle_url_read(
        _Agent(), "open https://example.com and show me the content", session_id="t", source_context={}
    )
    assert expected in str(result["response"]).lower()


# --------------------------------------------------------------------------------------
# The one that made every fix above invisible on the live daemon
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("phrasing", REPORTED_URL_PHRASINGS)
def test_the_normalizer_leaves_a_url_intact(phrasing: str) -> None:
    """`https://example.com` reached the front door as `https: / /example.com`.

    Found by re-driving the eight phrasings after the extractor and the web lane were both fixed
    and BOTH unit-verified — and getting the identical failures back from the daemon. The literal
    span table in `core/input_normalizer.py` had rules for Windows paths, posix paths, dotted
    filenames, ISO dates, clock times and decimals, and no rule for a URL, so `://` was tokenized
    and rejoined with spaces. Every downstream lane was reading a corrupted string: the web lane
    could not see a URL, while `explicit_path_in` could see the absolute posix path
    `//example.com`. Two correct fixes, invisible in production, because the string never arrived.
    """
    from core.input_normalizer import normalize_user_text

    normalized = normalize_user_text(phrasing).normalized_text
    request = url_read_request(normalized)
    assert request is not None, normalized
    assert "://" in request["url"]
    assert " " not in request["url"]


@pytest.mark.parametrize(
    "phrasing",
    [
        "fetch https://example.com and tell me what the page says",
        "grab the contents of https://httpbin.org/uuid for me",
        # No read verb at all — the web lane declines, so nothing else may claim it as a path.
        "is https://example.com any good?",
    ],
)
def test_no_shared_path_helper_sees_a_url_as_a_path(phrasing: str) -> None:
    """`explicit_path_in` is read by the arbiter AND the machine lane, so it is fixed there once.

    Ordering alone was not enough: a URL with no read verb falls past the web lane, and the next
    lane down must still not treat it as a folder on this disk.
    """
    from core.agent_runtime.intent_claims import probe_claims
    from core.execution.constants import explicit_path_in, machine_path_listing_intent

    assert explicit_path_in(phrasing) == ""
    assert machine_path_listing_intent(phrasing) is None
    assert [claim.family for claim in probe_claims(phrasing)] == []


def test_a_real_path_beside_a_url_still_survives_every_helper() -> None:
    from core.execution.constants import explicit_path_in

    both = "read https://x.com/a.txt then open ~/Desktop/a.txt"
    assert explicit_path_in(both) == "~/Desktop/a.txt"


def test_a_failed_fetch_says_why_and_not_just_that_it_failed() -> None:
    """"I could not fetch `X`: error." is a status word where the cause was already available.

    Measured live: `web.fetch` puts the real cause in its own `response_text` ("Web fetch for `X`
    failed: <urlopen error ...>") and carries no `reason` key, so reading only `reason` reduced a
    DNS failure to the word "error".
    """
    from types import SimpleNamespace
    from unittest import mock

    from core.agent_runtime import fast_paths_web

    with mock.patch(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=False,
            status="error",
            response_text="Web fetch for `https://nope.invalid` failed: <urlopen error nodename nor servname provided>",
            details={"observation": {"error": "<urlopen error nodename nor servname provided>"}},
        ),
    ):
        reply = str(
            fast_paths_web.maybe_handle_url_read(
                _Agent(), "load https://nope.invalid and tell me what came back",
                session_id="t", source_context={},
            )["response"]
        )
    assert "nodename nor servname" in reply
    assert reply.strip() != "I could not fetch `https://nope.invalid`: error."


def test_an_http_status_reaches_the_user_when_that_is_all_there_is() -> None:
    from types import SimpleNamespace
    from unittest import mock

    from core.agent_runtime import fast_paths_web

    with mock.patch(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda intent, arguments, *, source_context=None: SimpleNamespace(
            ok=False, status="error", response_text="",
            details={"observation": {"http_status": 503}},
        ),
    ):
        reply = str(
            fast_paths_web.maybe_handle_url_read(
                _Agent(), "open https://example.com and show me the content",
                session_id="t", source_context={},
            )["response"]
        )
    assert "503" in reply


def test_a_fetch_through_the_runtime_tool_counts_as_a_fetch_attempt() -> None:
    """The counter the URL-grounding backstop reads must see the fetches this runtime really makes.

    `tools/web/http_fetch.py` records its attempts; `_web_fetch` in `core/runtime_execution_tools.py`
    goes to urllib directly and recorded none. Measured live 2026-07-30, with the URL lane already
    landed: "take a look at https://example.org and summarise what is there" and "read out what's at
    http://neverssl.com" both fetched in ~2s, and the user was shown "You linked a URL and I did not
    open it on this turn" — the backstop overwriting a real page with a denial that it had been
    fetched. Only phrasings that miss `_URL_REVIEW_INTENT_RE` survived, which is exactly why
    "fetch ... and tell me what the page says" worked while "open ..." did not. No network here:
    the counter is asserted around a stubbed transport.
    """
    from unittest import mock

    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
    from core.runtime_execution_tools import _web_fetch

    class _Response:
        headers = {"content-type": "text/plain"}

        def geturl(self):
            return "https://example.org"

        def read(self, _n):
            return b"hello"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    # `_web_fetch` directly, not through `execute_runtime_tool`: the web contracts are unsupported
    # in the test environment, so the dispatcher short-circuits with status "disabled" and this
    # code path never runs. `allow_remote_fetch` is the explicit per-call policy override.
    with remote_fetch_policy_scope({}):
        assert remote_fetch_attempt_count() == 0
        with mock.patch("urllib.request.urlopen", return_value=_Response()):
            result = _web_fetch(
                {"url": "https://example.org"}, source_context={"allow_remote_fetch": True}
            )
        assert result.status == "ok"
        assert remote_fetch_attempt_count() >= 1, (
            "a fetched page still looks unfetched to the URL-grounding backstop"
        )


def test_a_fetch_blocked_by_policy_is_not_counted_as_an_attempt() -> None:
    """The counter must mean "we went to the network", or the backstop learns nothing from it."""
    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
    from core.runtime_execution_tools import _web_fetch

    with remote_fetch_policy_scope({}):
        result = _web_fetch({"url": "https://example.org"}, source_context={"allow_remote_fetch": False})
        assert result.status == "disabled_by_policy"
        assert remote_fetch_attempt_count() == 0


def test_the_web_lane_is_wired_into_the_front_door_above_the_machine_lane() -> None:
    from pathlib import Path

    source = Path("core/agent_runtime/turn_frontdoor.py").read_text()
    web_at = source.index("url_read = maybe_handle_url_read(")
    machine_at = source.index("_maybe_handle_direct_machine_read_request(")
    live_at = source.index("_maybe_handle_live_info_fast_path(")
    assert web_at < machine_at < live_at
