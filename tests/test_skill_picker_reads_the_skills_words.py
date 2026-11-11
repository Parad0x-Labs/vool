"""The built-in skill picker reads what the person asked, in the skill's own words.

Measured on the served seam before this change (``core.task_router.classify`` then
``core.tool_offer_assembly.skill_guidance_for``, which both the router's tool offer and the prompt
catalog call): selection read only the router's task class and the demand signals, so

- "review the code changes in my project before I push", "my CI is red, fix it" and "cut the first
  10 seconds of this video" classify ``unknown`` and got no skill;
- "open example.com and tell me what it says" got project-archaeology and repo-onboarding through a
  ``workspace.read_file`` demand seven skills share (for a person with the developer setting off);
- every ``general_advisory`` turn got the same presentation + evidence-lens pair, so the strategy
  lens never reached a decision ("should I ... or ...?");
- "explain how vaccines work in simple terms" got the bundled database pack's skill, a developer
  skill, for a person with the developer setting off.

Every row here goes through the same calls a served turn makes: the production classifier, then
``skill_guidance_for`` with a session context. The asks are written for this file; none is an eval
question, and the expectations are what each skill's own description says it is for.
"""
from __future__ import annotations

import pytest

DEVELOPER_IDS = {
    "ci-repair", "code-review", "cumulative-testing", "database", "vool-database", "feature-build",
    "migration", "performance", "project-archaeology", "release-gate", "repo-onboarding",
    "root-cause-repair", "security-audit",
}
LENS_IDS = {
    "lens-architecture", "lens-evidence", "lens-market", "lens-security", "lens-strategy", "lens-ux",
}


@pytest.fixture(autouse=True)
def _world(tmp_path, monkeypatch):
    from core import tool_offer_assembly

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "voolhome"))
    monkeypatch.delenv("VOOL_NATIVE_SKILLS_DIR", raising=False)
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.delenv("VOOL_DEVELOPER_SKILLS", raising=False)
    tool_offer_assembly.reset_skill_cache()
    yield
    tool_offer_assembly.reset_skill_cache()


@pytest.fixture()
def developer(monkeypatch):
    monkeypatch.setenv("VOOL_DEVELOPER_SKILLS", "1")


@pytest.fixture()
def media_worker_up():
    """A machine whose media worker is up: the media family registered as available, through the
    same registration the media lane ships (``core.media_capabilities``)."""
    from core import capability_graph
    from core.media_capabilities import register_media_capabilities

    capability_graph.ensure_registry_bootstrap()
    register_media_capabilities(available=True)
    yield
    # Back to the graph this runtime boots with, so no later test inherits the registration.
    capability_graph.reset()
    capability_graph.ensure_registry_bootstrap()


def _served(text: str) -> tuple[str, list[str]]:
    """(task class, skills that reached the turn) through the served seam."""
    from core.task_router import classify
    from core.tool_offer_assembly import skill_guidance_for

    task_class = str(classify(text, {"chat_surface": True}).get("task_class") or "")
    guidance = skill_guidance_for(text, task_class=task_class, source_context={"session_id": "picker"})
    return task_class, [str(row.get("name")) for row in guidance.skills if row.get("complete", True)]


def _task_skills(names: list[str]) -> list[str]:
    from core.native_skill_library import LANE_TASK, lane_for, load_skill_contracts

    contracts = {c.id: c for c in load_skill_contracts().contracts}
    picked = []
    for name in names:
        if name == "vool-database":  # the bundled pack's copy of the database skill
            name = "database"
        if name in contracts and lane_for(contracts[name]) == LANE_TASK and name not in picked:
            picked.append(name)
    return picked


# --------------------------------------------------------------------------- the right skill

DEVELOPER_ASKS = [
    ("look over my diff before I push and flag anything risky", "code-review"),
    ("can you do a code review of the patch I staged", "code-review"),
    ("the pipeline on main is red since the last merge, make the build green", "ci-repair"),
    ("why does test_invoice_total fail after my change?", "root-cause-repair"),
    ("reproduce the bug where saving an empty form crashes, failing test first", "root-cause-repair"),
    ("run the test suite and give me exact pass and fail counts", "cumulative-testing"),
    ("this report script is really slow, profile it and speed it up", "performance"),
    ("migrate the config loader to the new schema", "migration"),
    ("give me a tour of this codebase, where in the code is the login handled", "repo-onboarding"),
    ("scan the repo for hardcoded secrets and injection holes", "security-audit"),
    ("run the release gate, is this branch shippable", "release-gate"),
    ("which commit changed the billing file last week and why", "project-archaeology"),
    ("implement the feature that exports reports as csv", "feature-build"),
]


@pytest.mark.parametrize(("text", "wanted"), DEVELOPER_ASKS)
def test_a_developer_ask_reaches_the_skill_it_describes(developer, text: str, wanted: str) -> None:
    task_class, names = _served(text)
    tasks = _task_skills(names)
    assert tasks and tasks[0] == wanted, (task_class, names)


@pytest.mark.parametrize(
    "text",
    [
        "show me the schema of my sqlite database and the missing indexes",
        "set up a small database to keep track of my vinyl records",
    ],
)
def test_database_asks_reach_the_database_skill_with_its_pack_installed(developer, tmp_path, monkeypatch, text) -> None:
    from core.runtime_flags import override
    from tests._vool_database_pack import isolated_db_world

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        task_class, names = _served(text)
    assert _task_skills(names)[:1] == ["database"], (task_class, names)


def test_media_asks_reach_the_media_skill_when_the_worker_is_up(media_worker_up) -> None:
    for text in (
        "trim the first 4 seconds off this clip",
        "make this video vertical for reels",
        "compress this mp4 so it fits in an email",
    ):
        task_class, names = _served(text)
        assert _task_skills(names)[:1] == ["media-studio"], (text, task_class, names)


def test_without_the_media_worker_the_media_skill_is_refused_by_availability() -> None:
    """Words never override availability: the skill is reached only where it can run."""
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(task_class="unknown", user_text="trim the first 4 seconds off this clip")
    assert "media-studio" not in [c.id for c in selection.selected]
    record = next(r for r in selection.records if r.id == "media-studio")
    assert record.state == "capability_unavailable", record


# --------------------------------------------------------------------------- lenses


def test_a_decision_gets_the_strategy_lens_not_the_evidence_lens() -> None:
    for text in (
        "should I accept the promotion or stay in my current team?",
        "help me decide between renting a flat in town or buying further out",
    ):
        _task_class, names = _served(text)
        assert "lens-strategy" in names, (text, names)
        assert "lens-evidence" not in names, (text, names)


def test_the_lens_follows_what_the_ask_is_about() -> None:
    _cls, pricing = _served("how should I price my bakery's cakes against the competitors in town")
    assert "lens-market" in pricing and not set(pricing) & (LENS_IDS - {"lens-market"}), pricing


def test_a_turn_keeps_at_most_one_lens_beside_the_presentation_doctrine() -> None:
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(
        task_class="business_advisory",
        user_text="should we prioritise pricing or competitors in our market strategy this quarter?",
    )
    chosen = [c.id for c in selection.selected]
    assert chosen[0] == "answer-presentation", chosen
    assert len([c for c in chosen if c in LENS_IDS]) == 1, chosen


# --------------------------------------------------------------------------- none fits

NONE_ASKS = [
    "hi, how is your evening going",
    "write a short poem about rain on the window",
    "give me a simple pancake recipe",
    "how long should I boil an egg",
    "tell me a joke about cats",
    "what's the capital of Peru",
    "thanks, that was helpful",
    "recommend a film for a quiet sunday",
    "translate good night into Spanish",
    "what is 12 times 14",
    "write a birthday card message for my aunt",
    "I'm feeling a bit low today",
    "who painted the starry night",
    "suggest a name for my goldfish",
    "how do I get coffee out of a white shirt",
    "explain how vaccines work in simple terms",
    # Not here: "write a polite note to my landlord, the boiler has been broken all week". It is
    # classed debugging, no skill's words answer to it, and with the developer setting on the
    # class still seats the developer skills -- the behaviour
    # tests/test_skills_for_people.py::test_with_the_developer_setting_the_same_turn_seats_them_again
    # pins. A person's turn keeps it out (test_a_persons_everyday_turn_never_seats_a_developer_skill).
    "review the novel I just finished in one paragraph",
    "we're moving to Portugal next spring, any tips for the paperwork",
    "the pipe under my sink is leaking, who should I call",
    "what's a good warm-up before a run",
    "plan a weekend in Edinburgh for two",
    "write a haiku about my code",
    "good morning!",
]


@pytest.mark.parametrize("text", NONE_ASKS)
def test_an_everyday_ask_gets_no_task_skill_and_no_lens(developer, text: str) -> None:
    """Even with the developer setting on: the presentation doctrine alone may ride along."""
    _task_class, names = _served(text)
    assert not _task_skills(names), names
    assert not set(names) & LENS_IDS, names


# --------------------------------------------------------------------------- developer setting


@pytest.mark.parametrize(
    "text",
    [
        "open example.com and tell me what it says",
        "explain how vaccines work in simple terms",
        "write a polite note to my landlord, the boiler has been broken all week",
        "the pipe under my sink is leaking, who should I call",
        "we're moving to Portugal next spring, any tips for the paperwork",
        "review the novel I just finished in one paragraph",
    ],
)
def test_a_persons_everyday_turn_never_seats_a_developer_skill(text: str) -> None:
    """The developer setting off: no developer skill from the library or from a bundled pack."""
    _task_class, names = _served(text)
    assert not set(names) & DEVELOPER_IDS, names


def test_a_developers_own_words_still_reach_their_skill_with_the_setting_off() -> None:
    _task_class, names = _served("my CI pipeline is red after the last commit, fix the build")
    assert _task_skills(names)[:1] == ["ci-repair"], names


# --------------------------------------------------------------------------- load-bearing


def test_sabotage_without_the_skills_words_the_unknown_class_asks_get_nothing(developer, monkeypatch) -> None:
    """The word evidence is what reaches these asks: take it away and the class alone is back."""
    import core.native_skill_library as library

    asks = ("look over my diff before I push and flag anything risky", "my CI is red, fix it")
    for text in asks:
        task_class, names = _served(text)
        assert task_class in {"unknown", "chat_conversation"}, (text, task_class)
        assert _task_skills(names), text
    monkeypatch.setattr(library, "_word_evidence", lambda contracts, user_text: {})
    for text in asks:
        assert not _task_skills(_served(text)[1]), f"sabotage no-op: {text!r} still reached a skill"


# --------------------------------------------------------------------------- the class still speaks


def test_where_no_skills_words_speak_a_request_is_still_served_by_its_class(developer) -> None:
    """Words decide which skill when they say anything; when they say nothing, a request keeps
    what its class gives it (the composition and scope tests pin the same for their turns)."""
    from core.native_skill_library import select_native_skills

    selection = select_native_skills(task_class="debugging", user_text="here are my meeting notes, give me the action items")
    assert [c for c in selection.selected if c.audience == "developer"], [c.id for c in selection.selected]
    assert selection.by_class_only, "the class seated them, and the selection must say so"


def test_a_plain_question_no_skills_words_answer_is_not_seated_by_its_class() -> None:
    from core.native_skill_library import select_native_skills

    for task_class, text in (
        ("general_advisory", "how long should I boil an egg"),
        ("security_hardening", "the pipe under my sink is leaking, who should I call"),
        ("research", "who is the fastest runner ever"),
    ):
        chosen = [c.id for c in select_native_skills(task_class=task_class, user_text=text).selected]
        assert not set(chosen) & (LENS_IDS | DEVELOPER_IDS | {"browser"}), (text, chosen)


def test_a_person_never_gets_a_developer_skill_the_class_alone_seated() -> None:
    """Even when the words are about code: the class seated it, not the ask."""
    from core.native_skill_library import load_skill_contracts
    from core.tool_offer_assembly import _people_safe

    audit = next(c for c in load_skill_contracts().contracts if c.id == "security-audit")
    text = "the build pipeline went red after the merge"
    assert [c.id for c in _people_safe((audit,), text)] == ["security-audit"], (
        "precondition: words about code let a developer skill through the gate"
    )
    assert _people_safe((audit,), text, frozenset({"security-audit"})) == ()


# --------------------------------------------------------------------------- lenses read the question

LENS_ASKS = [
    # Decisions about the person's own course: the lens whose card declares decisions.
    ("should we renovate the old house or sell it and move somewhere smaller?", "lens-strategy"),
    ("which should I learn first, guitar or piano?", "lens-strategy"),
    ("is it a good idea to take out a loan to expand my shop?", "lens-strategy"),
    ("I can't decide between a cheaper flat with a long commute and a pricier one near work", "lens-strategy"),
    # Willingness to pay, viability: the market lens.
    ("would restaurants pay for software that tracks their food waste?", "lens-market"),
    ("is a phone repair stall a viable business next to a train station", "lens-market"),
    # Is this claim / study / article right: the evidence lens.
    ("a newspaper article says eating eggs every day is bad for your heart, is that right?", "lens-evidence"),
    ("my uncle shared a study claiming red wine helps you live longer, can I trust that", "lens-evidence"),
    # Is this method secure: the security lens.
    ("is a four-digit pin secure enough for the logins on my booking app?", "lens-security"),
    ("is it safe to email customers their invoices as plain attachments", "lens-security"),
    # A screen or flow that is hard to use: the ux lens.
    ("the search page of my site is confusing for customers, how do I fix it", "lens-ux"),
    ("the signup page asks for too many fields, how can I simplify it", "lens-ux"),
    # How to structure a system: the architecture lens.
    ("should our small shop app keep one database or give each service its own?", "lens-architecture"),
    ("for a hobby project, is a monolith or microservices the better fit", "lens-architecture"),
]


@pytest.mark.parametrize(("text", "wanted"), LENS_ASKS)
def test_an_advisory_question_gets_the_lens_its_concern_names(text: str, wanted: str) -> None:
    _task_class, names = _served(text)
    lenses = [name for name in names if name in LENS_IDS]
    assert lenses == [wanted], (text, names)
    assert not _task_skills(names), names


@pytest.mark.parametrize(
    "text",
    [
        "should I wear a scarf today?",
        "what's the difference between a moth and a butterfly",
        "is it going to rain this weekend",
        "summarise this paragraph: the integration team wired the payment gateway into the "
        "orchestration layer and the deployment pipeline now runs every night",
        "rewrite this sentence so it sounds warmer: your request has been processed",
    ],
)
def test_a_plain_verdict_or_carried_material_gets_no_lens(developer, text: str) -> None:
    """No alternatives to weigh, nothing the person decides, or words that belong to pasted
    material rather than to the request: no lens, no task skill."""
    _task_class, names = _served(text)
    assert not set(names) & LENS_IDS, names
    assert not _task_skills(names), names


def test_the_words_of_carried_material_do_not_pick_the_skill() -> None:
    """The request is read by its head: the same words asked directly would pick a lens."""
    from core.native_skill_library import _instruction_head

    asked = "summarise this paragraph: our checkout flow is confusing and users drop off"
    assert _instruction_head(asked) == "summarise this paragraph"
    assert _instruction_head("which is smarter right now: renting or buying a flat") == (
        "which is smarter right now: renting or buying a flat"
    )


def test_a_feature_for_the_persons_app_reaches_feature_build(developer) -> None:
    task_class, names = _served("add a dark mode switch to my app's account page")
    assert _task_skills(names)[:1] == ["feature-build"], (task_class, names)


def test_sabotage_without_the_decision_shape_a_decision_with_no_lens_word_gets_no_lens(monkeypatch) -> None:
    import core.native_skill_library as library

    text = "which should I learn first, guitar or piano?"
    assert "lens-strategy" in _served(text)[1]
    monkeypatch.setattr(library, "_decision_shape", lambda user_text: False)
    assert "lens-strategy" not in _served(text)[1], "sabotage no-op: the decision shape is not what seats it"


# --------------------------------------------------------------------------- developer words in their everyday sense

EVERYDAY_SENSES = [
    "look over my cover letter before I commit to sending it to the recruiter",
    "the ticket machine at the station showed error code 17 and ate my card",
    "the council deployed extra gritters before the snow",
    "go through my essay and point out the bugs in my reasoning",
    "which branch of the bank is open on saturdays",
    "the builders said the extension build is a week behind",
]
CODE_WORK = [
    "look over my diff before I push and flag anything risky",
    "the nightly build is broken since this morning's merge, fix it",
    "why does parse_invoice() return None for an empty file?",
    "the deploy script throws a traceback after my last commit",
    "run the tests again and tell me which ones still fail",
]


@pytest.mark.parametrize("text", EVERYDAY_SENSES)
def test_an_everyday_sense_of_a_developer_word_is_not_code(text: str) -> None:
    from core.native_skill_library import about_code

    assert not about_code(text), text


@pytest.mark.parametrize("text", CODE_WORK)
def test_software_work_is_code(text: str) -> None:
    from core.native_skill_library import about_code

    assert about_code(text), text


@pytest.mark.parametrize("text", EVERYDAY_SENSES)
def test_a_person_s_everyday_sense_seats_no_developer_skill(text: str) -> None:
    _task_class, names = _served(text)
    assert not set(names) & DEVELOPER_IDS, (text, names)


def test_a_developer_s_word_skill_needs_code_even_with_the_setting_on(developer) -> None:
    """The developer setting shows the developer skills; it does not turn a cover letter into
    a code review. Where the class alone seats them (a turn no skill's words speak to) the
    setting keeps them, as tests/test_skills_for_people.py pins."""
    _task_class, names = _served("look over my cover letter before I commit to sending it to the recruiter")
    assert "code-review" not in names, names
    _task_class, names = _served("look over my diff before I push and flag anything risky")
    assert _task_skills(names)[:1] == ["code-review"], names


@pytest.mark.parametrize("text", [CODE_WORK[1], CODE_WORK[3], CODE_WORK[4]])
def test_a_person_s_own_code_work_still_reaches_a_developer_skill(text: str) -> None:
    """The setting off: the words that make it software work still let a developer skill in."""
    _task_class, names = _served(text)
    assert set(names) & DEVELOPER_IDS, (text, names)


# --------------------------------------------------------------------------- one word of a name

def _cards():
    from core.native_skill_library import skill_cards
    from core.plugin_skills import Skill

    mine = (
        Skill(name="rent-splitter",
              description="Work out a fair split of the monthly rent and shared bills between flatmates.",
              body="Ask for each room's size."),
        Skill(name="trip-packer", description="Build a packing list for a trip from the destination, "
              "the dates and the weather.", body="Ask where and when."),
    )
    return mine, skill_cards()


@pytest.mark.parametrize(
    "text",
    [
        "split this scanned contract into one file per page",
        "I'll be late with the rent this month, write a note to my landlord",
        "what's the rent for a two-bed in Porto these days",
        "we're on a trip to the coast, recommend a fish restaurant",
    ],
)
def test_one_everyday_word_of_a_skill_s_name_does_not_pick_it(text: str) -> None:
    from core.plugin_skills import rank_skills

    mine, peers = _cards()
    assert not rank_skills(mine, text, limit=2, peers=peers), text


@pytest.mark.parametrize(
    ("text", "wanted"),
    [
        ("help me split the rent fairly with my two flatmates", "rent-splitter"),
        ("my flatmate's room is bigger, how should we share the rent and bills", "rent-splitter"),
        ("make me a packing list for my trip to Iceland in March", "trip-packer"),
        ("use the trip packer for my weekend in Rome", "trip-packer"),
    ],
)
def test_the_name_with_its_card_still_picks_it(text: str, wanted: str) -> None:
    from core.plugin_skills import rank_skills

    mine, peers = _cards()
    assert [s.name for s in rank_skills(mine, text, limit=1, peers=peers)] == [wanted], text
