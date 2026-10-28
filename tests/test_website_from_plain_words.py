"""A one-page website from plain words: the door, the page, the follow-up turns.

Audit mailweb 2026-10-27 (B01-B07): "id love a basic one page website for the bakery, can you do
that?" produced nothing -- the only door to a site builder needed the literal word "web0", and the
general builder lane died on "the model did not return a usable file list" or hung to the turn
timeout. These tests drive the real routing gates and the real builder: a scripted model reply
(or a raising one) is INPUT data, not a stub of VOOL code -- every path under test is the code a
person's machine would run.
"""
from __future__ import annotations

import os

import pytest

from core.agent_runtime import build_request_intent
from core.agent_runtime.builder import app_builder as ab
from core.agent_runtime.builder import site_followups
from core.agent_runtime.fast_paths_builder import looks_like_builder_request
from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

# The person's sentence, exactly as they would type it: a business name and a phone number, no
# jargon. Everything the page needs is in here.
_SITE_REQUEST = "make a one page website for my barber shop called Steves Cuts, our phone number is 07000 900981"
_SITE_FOLDER = "steves-cuts"

# Verb-less wants, all fresh wordings (not the audit's bakery/cake-shop/flower-shop set): the shape
# is "I want/love/need/am-after a simple <site noun> for <my own thing>" across different trades.
_VERBLESS_SITE_WANTS = (
    "id love a simple one page website for my barber shop",
    "i want a basic homepage for my bike repair place",
    "i need a simple webpage for my nail studio",
    "id love a one page site for my coffee cart",
    "im after a simple website for my dog walking round",
    "i want a landing page for my tutor service",
    "id love a one page website for our window cleaning round",
    "im looking for a simple homepage for my jewellery stand",
)

# Questions about websites, advice asks and an unrelated want that merely says "phone": talking
# about a site is not asking for one, and a phone is not a phone NUMBER for a page.
_NOT_BUILD_REQUESTS = (
    "how do websites work",
    "should i make a website for my bakery?",
    "what makes a good homepage",
    "can you explain what a landing page is",
    "i want a new phone for my bakery",
    "what is a good website for my bakery",
)


class _FakeAgent:
    """The agent surface this lane hands its turn to -- the boundary pattern of
    tests/test_machine_fast_path_events.py: the fast-path result is recorded, not faked, and the
    code under test is the lane itself."""

    def __init__(self) -> None:
        self.turns: list[dict] = []

    def _fast_path_result(self, **kwargs):
        self.turns.append(kwargs)
        return {"response": kwargs["response"], "reason": kwargs["reason"], "mode": "advice_only"}


def _write_runner(workspace, *, write_ok: bool = True):
    """run_tool_fn with the real filesystem effect of each intent, rooted at the temp workspace.

    The production runner writes through the same intents; performing the effect here is what
    makes the built page a file on disk the follow-up turns can find.
    """
    calls: list[tuple[str, dict]] = []

    def run(intent, arguments=None, **_kwargs):
        arguments = dict(arguments or {})
        calls.append((intent, arguments))
        if intent == "workspace.ensure_directory":
            os.makedirs(os.path.join(str(workspace), arguments["path"]), exist_ok=True)
            return ab.ToolOutcome(ok=True, response_text="")
        if intent == "workspace.write_file":
            if not write_ok:
                return ab.ToolOutcome(ok=False, response_text="write refused")
            target = os.path.join(str(workspace), arguments["path"])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(arguments["content"])
            return ab.ToolOutcome(ok=True, response_text="")
        if intent == "sandbox.run_command":
            return ab.ToolOutcome(ok=True, response_text="Ran 0 tests\n\nOK\n", details={"returncode": 0})
        return ab.ToolOutcome(ok=True, response_text="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _build_site(tmp_path, *, request: str = _SITE_REQUEST, generate_fn, write_ok: bool = True):
    """The real build lane, pointed at a temp workspace, with the model reply scripted."""
    return ab.build_app_from_spec(
        request=request,
        target_rel=_SITE_FOLDER,
        source_context={"workspace": str(tmp_path)},
        generate_fn=generate_fn,
        run_tool_fn=_write_runner(tmp_path, write_ok=write_ok),
    )


def _site_page(tmp_path):
    return os.path.join(str(tmp_path), _SITE_FOLDER, "index.html")


def _snapshot(root) -> dict[str, bytes]:
    """Every file under root as {relative path: bytes} -- the 'was anything touched' truth."""
    snapshot: dict[str, bytes] = {}
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            full = os.path.join(dirpath, name)
            snapshot[os.path.relpath(full, str(root))] = open(full, "rb").read()
    return snapshot


# --------------------------------------------------------------------------------------
# The door
# --------------------------------------------------------------------------------------


def test_verbless_site_wants_open_both_builder_doors() -> None:
    """"id love a simple one page website for my barber shop" is a build request with no build verb.

    Both routing gates must say yes: the front-door builder gate and the agentic-build gate that
    keeps such a turn away from the single-keyword fast paths.
    """
    for wording in _VERBLESS_SITE_WANTS:
        assert looks_like_builder_request(wording.lower()), wording
        assert looks_like_agentic_build_request(wording), wording


def test_verbless_site_wants_match_the_door_helper() -> None:
    """The shared shape rule behind both gates recognises the want itself."""
    for wording in _VERBLESS_SITE_WANTS:
        assert build_request_intent.is_want_site_request(wording.lower()), wording


def test_build_verb_site_requests_already_open_both_doors() -> None:
    for wording in (
        "can you make a one page site for our kayak hire",
        "build me a simple website for my guitar lessons",
    ):
        assert looks_like_builder_request(wording.lower()), wording
        assert looks_like_agentic_build_request(wording), wording


@pytest.mark.parametrize("wording", _NOT_BUILD_REQUESTS)
def test_talking_about_websites_opens_no_door(wording: str) -> None:
    """Asking how sites work, asking for advice, or wanting a new PHONE is not a build request."""
    assert looks_like_builder_request(wording.lower()) is False
    assert looks_like_agentic_build_request(wording) is False
    assert build_request_intent.is_want_site_request(wording.lower()) is False


# --------------------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------------------


def test_a_site_request_builds_a_real_index_html_from_the_persons_words(tmp_path) -> None:
    report = _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    page = _site_page(tmp_path)
    assert os.path.isfile(page), "no index.html was built"
    content = open(page, encoding="utf-8").read()
    assert "Steves Cuts" in content
    assert "07000 900981" in content
    assert "made on this computer" in content.lower()
    # The reply is the receipt a person reads: where the page is, how to open it.
    reply = ab.render_app_build_response(report)
    assert _SITE_FOLDER in reply and "index.html" in reply
    assert str(tmp_path) not in reply


@pytest.mark.parametrize(
    "garbage",
    [
        pytest.param("", id="empty-reply"),
        pytest.param("um i cannot help", id="refusal-reply"),
        pytest.param('["/etc/passwd", "../evil.py", "C:/win"]', id="junk-path-list"),
    ],
)
def test_a_garbage_model_reply_still_leaves_the_person_their_page(tmp_path, garbage: str) -> None:
    """The audit's dead end ("the model did not return a usable file list") is not reachable for
    a site request: the person's own sentence is the whole specification of a one-page site."""
    _build_site(tmp_path, generate_fn=lambda _prompt: garbage)
    page = _site_page(tmp_path)
    assert os.path.isfile(page)
    content = open(page, encoding="utf-8").read()
    assert "Steves Cuts" in content
    assert "07000 900981" in content
    assert "made on this computer" in content.lower()


def _generation_died(_prompt: str) -> str:
    """The shape a provider timeout surfaces as: the generation call itself raises."""
    raise TimeoutError("provider timed out after 240s")


def test_a_generation_that_dies_ends_the_turn_in_one_plain_line(tmp_path) -> None:
    """Nothing can be written either, so the turn must END: one short plain line, no engineering
    words, no hang, and nothing half-built left behind."""
    report = _build_site(tmp_path, generate_fn=_generation_died, write_ok=False)
    reply = ab.render_app_build_response(report)
    assert reply == reply.strip()
    assert "\n" not in reply, "the turn ended in a slab of text, not one line"
    assert len(reply) <= 120
    assert reply.endswith(".")
    lowered = reply.lower()
    for banned in (
        "stack trace", "traceback", "exception", "timeout", "timed out",
        "the model did not return", "file list", "provider",
    ):
        assert banned not in lowered, f"{banned!r} leaked into the reply"
    assert not os.path.exists(_site_page(tmp_path)), "a failed build still wrote a page"


def test_a_generation_that_dies_still_leaves_the_person_their_page(tmp_path) -> None:
    """A raising generation is the same useless turn as a garbage reply: the page is built from
    the person's words with no further model call."""
    report = _build_site(tmp_path, generate_fn=_generation_died)
    page = _site_page(tmp_path)
    assert os.path.isfile(page)
    content = open(page, encoding="utf-8").read()
    assert "Steves Cuts" in content
    assert "07000 900981" in content
    assert "made on this computer" in content.lower()
    assert report.files_written, "the report claims nothing was written"


# --------------------------------------------------------------------------------------
# The follow-up turns
# --------------------------------------------------------------------------------------


def test_put_it_online_publishes_nothing(tmp_path) -> None:
    _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    before = _snapshot(tmp_path)
    agent = _FakeAgent()
    result = site_followups.maybe_handle_site_followup(
        agent, "put it online", {"workspace": str(tmp_path), "session_id": "s1"}
    )
    assert result is not None and "result" in result
    reply = result["result"]["response"]
    lowered = reply.lower()
    assert "nothing" in lowered and ("online" in lowered or "internet" in lowered)
    # No payment, no wallet, and never a promise that publishing will happen.
    for banned in ("pay", "payment", "card", "wallet", "arweave", "i will put it on", "will publish"):
        assert banned not in lowered, f"{banned!r} leaked into the reply"
    # Publishing took no action at all: the workspace is byte-identical, file set included.
    assert _snapshot(tmp_path) == before


def test_see_followups_name_the_folder_and_the_file(tmp_path) -> None:
    _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    agent = _FakeAgent()
    context = {"workspace": str(tmp_path), "session_id": "s1"}
    for ask in ("can i see it", "where do i edit it"):
        result = site_followups.maybe_handle_site_followup(agent, ask, context)
        assert result is not None, ask
        reply = result["result"]["response"].lower()
        assert _SITE_FOLDER in reply, ask
        assert "index.html" in reply, ask
        assert "browser" in reply, ask
        assert str(tmp_path) not in reply, "a raw path the person did not ask for"


def test_an_hours_followup_changes_that_page(tmp_path) -> None:
    _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    page = _site_page(tmp_path)
    before = open(page, encoding="utf-8").read()
    assert "8 till 6" not in before
    agent = _FakeAgent()
    result = site_followups.maybe_handle_site_followup(
        agent, "add our opening hours monday to friday 8 till 6", {"workspace": str(tmp_path), "session_id": "s1"}
    )
    assert result is not None
    after = open(page, encoding="utf-8").read()
    assert "monday to friday 8 till 6" in after
    # The provenance marker survives the edit: later turns still find this page.
    assert "made on this computer" in after.lower()
    assert "monday to friday 8 till 6" in result["result"]["response"].lower()


def test_an_edit_with_no_detail_asks_for_it_in_one_plain_sentence(tmp_path) -> None:
    _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    page = _site_page(tmp_path)
    before = open(page, encoding="utf-8").read()
    agent = _FakeAgent()
    result = site_followups.maybe_handle_site_followup(
        agent, "put our phone number on it", {"workspace": str(tmp_path), "session_id": "s1"}
    )
    assert result is not None
    reply = result["result"]["response"]
    assert "\n" not in reply and reply.endswith(".")
    assert "phone" in reply.lower()
    assert open(page, encoding="utf-8").read() == before, "the page changed with nothing to put on it"


def test_find_built_site_page_finds_the_newest_marked_page_and_ignores_the_rest(tmp_path) -> None:
    root = str(tmp_path)
    marked = "<html><body><footer>Made on this computer</footer></body></html>"
    first = os.path.join(root, "first-site", "index.html")
    second = os.path.join(root, "second-site", "index.html")
    for path in (first, second):
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(marked)
    os.utime(first, (1_800_000_000, 1_800_000_000))
    os.utime(second, (1_900_000_000, 1_900_000_000))
    assert site_followups.find_built_site_page(root) == second
    # Hidden folders and dependency folders hold no one's page.
    for skipped in (".git", "node_modules", "venv"):
        os.makedirs(os.path.join(root, skipped))
        with open(os.path.join(root, skipped, "index.html"), "w", encoding="utf-8") as handle:
            handle.write(marked)
    os.utime(os.path.join(root, ".git", "index.html"), (2_000_000_000, 2_000_000_000))
    assert site_followups.find_built_site_page(root) == second
    # Deeper than three folder levels is out of bounds.
    deep = os.path.join(root, "a", "b", "c", "d", "index.html")
    os.makedirs(os.path.dirname(deep))
    with open(deep, "w", encoding="utf-8") as handle:
        handle.write(marked)
    os.utime(deep, (2_100_000_000, 2_100_000_000))
    assert site_followups.find_built_site_page(root) == second
    # A page without the marker is not this lane's page.
    for path in (first, second):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("<html><body>just a page</body></html>")
    assert site_followups.find_built_site_page(root) is None
    assert site_followups.find_built_site_page(os.path.join(root, "not-there")) is None


def test_the_followup_lane_claims_nothing_it_should_not(tmp_path) -> None:
    agent = _FakeAgent()
    # No page was ever built in this chat: every family stays quiet and the chat answers normally.
    empty = str(tmp_path / "empty-workspace")
    os.makedirs(empty)
    for ask in ("can i see it", "put it online", "add our opening hours monday to friday 8 till 6"):
        assert site_followups.maybe_handle_site_followup(agent, ask, {"workspace": empty}) is None, ask
    assert site_followups.maybe_handle_site_followup(agent, "can i see it", {}) is None
    # With a page present, an unrelated turn still claims nothing.
    _build_site(tmp_path, generate_fn=lambda _prompt: "um i cannot help")
    context = {"workspace": str(tmp_path), "session_id": "s1"}
    for ask in (
        "tell me a joke",
        "how do websites work",
        "should i make a website for my bakery?",
        "can you host a party for me",
        "i want a new phone for my bakery",
    ):
        assert site_followups.maybe_handle_site_followup(agent, ask, context) is None, ask
