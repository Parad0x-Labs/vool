"""A one-page website from plain words: the door, the page, the follow-up turns.

Audit mailweb 2026-10-27 (B01-B07): "id love a basic one page website for the bakery, can you do
that?" produced nothing -- the only door to a site builder needed the literal word "web0", and the
general builder lane died on "the model did not return a usable file list" or hung to the turn
timeout. These tests drive the real routing gates and the real builder: a scripted model reply
(or a raising one) is INPUT data, not a stub of VOOL code -- every path under test is the code a
person's machine would run.
"""
from __future__ import annotations

import html
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
# ("im looking for ..." lived here before A1; the A1 review made "looking for a website for
# <thing>" a SEARCH, so that wording moved to the negatives below.)
_VERBLESS_SITE_WANTS = (
    "id love a simple one page website for my barber shop",
    "i want a basic homepage for my bike repair place",
    "i need a simple webpage for my nail studio",
    "id love a one page site for my coffee cart",
    "im after a simple website for my dog walking round",
    "i want a landing page for my tutor service",
    "id love a one page website for our window cleaning round",
    "im after a simple homepage for my jewellery stand",
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

# A1 (2026-10-28): the door opens by SHAPE, not by the six verbs and two names the review listed.
# Twelve fresh positives of my own -- phrasal making verbs, a cousin phrasal, a bare imperative,
# noun-led asks with please, named things with and without an apostrophe, and an adverb between
# "to" and the making verb. None of these wordings is copied from the task or the amendment.
_A1_FRESH_POSITIVES = (
    "could you knock up a one page site for our village bell ringers",
    "please put together a simple webpage for my cycle repair shed",
    "set up a landing page for my courier round",
    "id love you to whip up a one-pager for our sea cadets troop",
    "just throw together a basic homepage for my knitwear stall",
    "would you mind sorting out a web page for our bowls club",
    "cobble together a simple site for my violin teaching",
    "a one page website for our twitch fishing group, please",
    "i want a simple site for Harringtons Hardware",
    "i need a one page website for Ferns Field Greens",
    "i want to quickly make a homepage for my upholstery work",
    "can you just spin up a simple one-pager for my poetry evenings",
)

# Twelve fresh negatives: searches, questions, advice, an adverb doing nothing to rescue a
# non-making verb, a stoplisted "thing", a polite question about worth, and a fix-it ask whose
# making verb is aimed at a problem, not at a site.
_A1_FRESH_NEGATIVES = (
    "im looking for a website for a decent plumber",
    "looking for a one page website for my church fete",
    "find me a website for cheap stationery",
    "can you find me a homepage for our puzzle club",
    "i want to quickly check the website for my bank",
    "should i throw together a website for my market stall?",
    "how would i go about putting together a website for my guest house?",
    "a website for beginners, please",
    "is a one page site for my dress shop worth it, please",
    "what makes a good homepage for a dog groomer",
    "i want a website for home use",
    "sort out the problem with the website for my launderette",
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
# A1: the widened door, the search stand-down, the names, and the turn entry point
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("wording", _A1_FRESH_POSITIVES)
def test_a1_fresh_wordings_open_both_doors_and_the_project_gate(wording: str) -> None:
    """Phrasal making verbs, noun-led please asks, named things and adverb-split infinitives all
    reach the build lane: the front-door gate (lowercased, as every production caller passes it),
    the agentic gate (raw text) and the project-scope authority that beats the task-class gate."""
    assert build_request_intent.is_want_site_request(wording), wording
    assert looks_like_builder_request(wording.lower()), wording
    assert looks_like_agentic_build_request(wording), wording
    assert build_request_intent.is_build_instruction(wording, scope="project"), wording


@pytest.mark.parametrize("wording", _A1_FRESH_NEGATIVES)
def test_a1_fresh_negatives_open_no_door(wording: str) -> None:
    """Searches for a site, questions, advice, worth-asks, stoplisted things and fix-it asks stay
    conversations: no door opens for any of them, on any of the three authorities."""
    assert build_request_intent.is_want_site_request(wording) is False, wording
    assert build_request_intent.is_want_site_request(wording.lower()) is False, wording
    assert looks_like_builder_request(wording.lower()) is False, wording
    assert looks_like_agentic_build_request(wording) is False, wording
    assert build_request_intent.is_build_instruction(wording, scope="project") is False, wording


@pytest.mark.parametrize(
    "wording",
    (
        "im looking for a website for my plumber",
        "looking for a website for Bea's Plant Corner",
        "find me a website for cheap flowers",
        "can you find me a website for Bea's Plant Corner",
        "im searching for a good website for my bakery",
        "im looking for a simple homepage for my jewellery stand",
    ),
)
def test_looking_for_and_find_me_are_searches_not_builds(wording: str) -> None:
    """"looking for a website for <thing>" and "find me a website for ..." hunt for something that
    already exists; the door must stay shut for them, whatever arm could otherwise claim them."""
    assert build_request_intent.is_want_site_request(wording) is False, wording
    assert looks_like_builder_request(wording.lower()) is False, wording
    assert looks_like_agentic_build_request(wording) is False, wording


@pytest.mark.parametrize(
    "wording",
    (
        "i want to quickly make a site for my shop",
        "i want to very quickly knock up a homepage for my yard",
        "id love to just put together a one-pager for our quiz team",
        "can you quickly sort out a web page for my repair bench",
    ),
)
def test_an_adverb_between_to_and_the_making_verb_still_opens_the_door(wording: str) -> None:
    """The adjacency rule is fixed: "i want to quickly make ..." was vetoed because the making
    infinitive had to sit immediately after "to" (and immediately after a request lead)."""
    assert build_request_intent.is_want_site_request(wording), wording
    assert looks_like_builder_request(wording.lower()), wording


@pytest.mark.parametrize(
    "wording",
    (
        "i want to quickly check the website for my bank",
        "i want to very quickly look at the website for my shop",
        "i want to locally host the website for my bakery",
    ),
)
def test_an_adverb_does_not_rescue_a_doing_verb(wording: str) -> None:
    """The same adverb run must not blur "to quickly check / look at / host" into a making ask."""
    assert build_request_intent.is_want_site_request(wording) is False, wording


@pytest.mark.parametrize(
    "wording",
    (
        "i want a website for Bea's Plant Corner",
        "i need a simple homepage for Ted's Taxis",
        "id love a one-pager for Sugartree",
    ),
)
def test_a_named_thing_opens_the_door_with_or_without_capitals(wording: str) -> None:
    """A proper name after "for" -- no my/our/the -- is the person's own named thing. The raw
    capitals are read when the caller still has them, and the lowered spelling stands on its own
    (every production caller of the front-door gate passes lowercased text)."""
    assert build_request_intent.is_want_site_request(wording), wording
    assert build_request_intent.is_want_site_request(wording.lower()), wording
    assert looks_like_builder_request(wording.lower()), wording
    assert looks_like_agentic_build_request(wording), wording


def test_the_page_carries_the_name_of_a_named_or_owned_thing(tmp_path) -> None:
    """"for Bea's Plant Corner" names the shop outright; "for my bakery Sugartree" owns a bakery
    whose proper name rides at the end of the phrase. The page heading is the name in both."""
    from core.agent_runtime.builder import site_words as sw

    named = sw.extract_site_words("i want a website for Bea's Plant Corner")
    assert named.name == "Bea's Plant Corner"
    owned = sw.extract_site_words("i want a website for my bakery Sugartree")
    assert owned.name == "Sugartree"
    # The shapes the previous round relied on keep their names unchanged.
    assert sw.extract_site_words("make a one page website for my barber shop called Steves Cuts").name == "Steves Cuts"
    assert sw.extract_site_words("id love a simple one page website for my barber shop").name == "My Barber Shop"
    assert sw.extract_site_words("a website for my cake stall, please").name == "My Cake Stall"


def test_a_named_thing_builds_a_page_under_its_own_name(tmp_path) -> None:
    """A named-thing ask runs the real build lane and the page on disk carries the name."""
    report = ab.build_app_from_spec(
        request="i want a website for Bea's Plant Corner, our phone number is 01632 960413",
        target_rel="bea-s-plant-corner",
        source_context={"workspace": str(tmp_path)},
        generate_fn=lambda _prompt: "um i cannot help",
        run_tool_fn=_write_runner(tmp_path),
    )
    page = os.path.join(str(tmp_path), "bea-s-plant-corner", "index.html")
    assert os.path.isfile(page)
    content = open(page, encoding="utf-8").read()
    # The renderer escapes the apostrophe in the title; the person still reads their own name.
    assert "Bea's Plant Corner" in html.unescape(content)
    assert "01632 960413" in content
    assert report.files_written


def test_the_real_turn_entry_point_answers_the_site_followups(tmp_path) -> None:
    """The wiring A1 added: after the build, the follow-up turns go through the real turn entry
    point of a real agent and are answered in plain words -- nothing published, no network."""
    from apps.vool_agent import VoolAgent

    # 1) The build ask, through the real turn entry point, must be handed to the build lane
    # (the front door returns an empty result so the reasoning stage's builder runs it).
    agent = VoolAgent(backend_name="test-backend", device="site-followup-test", persona_id="default")
    build_ask = "knock up a one page website for our village institute, our phone number is 01632 960413"
    context = {
        "workspace": str(tmp_path),
        "workspace_root": str(tmp_path),
        "surface": "api",
        "operating_mode": "auto",
    }
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=build_ask,
        effective_input=build_ask,
        normalized_input=build_ask.casefold(),
        source_surface="api",
        session_id="site-followup-proof",
        source_context=context,
        persona=None,
        interpreted=None,
    )
    assert outcome == {"result": None}, "the build ask did not fall through to the build lane"

    # 2) The real builder writes the page into the chat's workspace (scripted model refusal is
    # input data: the plain-words page needs no model).
    ab.build_app_from_spec(
        request=build_ask,
        target_rel="village-institute",
        source_context=context,
        generate_fn=lambda _prompt: "um i cannot help",
        run_tool_fn=_write_runner(tmp_path),
    )
    page = os.path.join(str(tmp_path), "village-institute", "index.html")
    assert os.path.isfile(page)

    def turn(text: str) -> dict:
        return agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text.casefold(),
            source_surface="api",
            session_id="site-followup-proof",
            source_context=context,
            persona=None,
            interpreted=None,
        )

    # 3) "can i see it" -> the page's location in plain words, from the turn entry point.
    see = turn("can i see it")
    see_result = (see or {}).get("result")
    assert see_result is not None, "the see follow-up fell through unanswered"
    assert see_result["response"].lower().count("village-institute") >= 1
    assert "index.html" in see_result["response"].lower()

    # 4) "add our opening hours monday to friday 9 till 5" -> that page changes.
    before = open(page, encoding="utf-8").read()
    assert "monday to friday 9 till 5" not in before
    edit = turn("add our opening hours monday to friday 9 till 5")
    edit_result = (edit or {}).get("result")
    assert edit_result is not None, "the hours follow-up fell through unanswered"
    after = open(page, encoding="utf-8").read()
    assert "monday to friday 9 till 5" in after
    assert "monday to friday 9 till 5" in edit_result["response"].lower()
    assert "made on this computer" in after.lower()

    # 5) "put it online" -> nothing published: the workspace is byte-identical and the reply says so.
    snapshot_before = _snapshot(tmp_path)
    publish = turn("put it online")
    publish_result = (publish or {}).get("result")
    assert publish_result is not None, "the publish follow-up fell through unanswered"
    lowered = publish_result["response"].lower()
    assert "nothing" in lowered and ("online" in lowered or "internet" in lowered)
    assert _snapshot(tmp_path) == snapshot_before


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
