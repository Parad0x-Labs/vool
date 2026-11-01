"""An independently authored development hold-out for document-backed admission."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.clean_loop import route, tools
from core.clean_loop.registry import ToolContext

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}

GENERAL_ASKS = [
    "Tell me a quick joke about a sleepy cat.",
    "Why did the bicycle fall over?",
    "Make up a pun about a lighthouse.",
    "What is the capital of Portugal?",
    "How does a rainbow form?",
    "Who invented the telescope?",
    "Why is the ocean salty?",
    "When was the first satellite launched?",
    "How do I cook fluffy rice?",
    "Show me how to draw a hexagon.",
    "How can I learn basic guitar chords?",
    "Explain how a compass works.",
    "Define photosynthesis in simple words.",
    "What does diligent mean?",
    "Explain the difference between a simile and a metaphor.",
    "What is an ecosystem?",
    "What is 17 times 23?",
    "Calculate the square root of 144.",
    "If I travel 90 miles in two hours, what is my speed?",
    "Convert 4.5 litres to millilitres.",
    "What should I do when I feel nervous before a speech?",
    "Give me advice for making new friends.",
    "How can someone build a consistent exercise habit?",
    "Suggest a way to organize a study session.",
    "Write a Python function that reverses a string.",
    "Why does this JavaScript promise stay pending?",
    "Help me debug a CSS grid layout.",
    "What is the difference between a list and a tuple?",
    "Will it rain in Oslo tomorrow?",
    "What is the weather in Kyoto this weekend?",
    "Find today's headlines about science.",
    "What is the latest news from the space agency?",
    "Who won the championship last night?",
    "What are the current exchange rates?",
    "Explain how a solar panel produces electricity.",
    "Compare a comet and an asteroid.",
    "How do I safely repot a cactus?",
    "What ingredients work well in lentil soup?",
    "Write a short poem about autumn leaves.",
    "Explain the rules of chess castling.",
]

PERSONAL = [
    ("Anara", "Mosslight", "When does Anara arrive at Mosslight?"),
    ("Beren", "Copperfern", "Which colour did Beren choose at Copperfern?"),
    ("Calia", "Winterglass", "What time is Calia expected at Winterglass?"),
    ("Davin", "Amberwake", "Where is Davin due to meet at Amberwake?"),
    ("Eleni", "Silvermeadow", "Which route did Eleni pick near Silvermeadow?"),
    ("Farro", "Blueharbour", "When will Farro reach Blueharbour?"),
    ("Galia", "Orchardmist", "What shade did Galia select at Orchardmist?"),
    ("Halen", "Quietstone", "Which entrance will Halen use at Quietstone?"),
    ("Ivara", "Sunwoven", "At what hour does Ivara leave Sunwoven?"),
    ("Joren", "Willowcairn", "Where should Joren wait at Willowcairn?"),
    ("Kessa", "Cloudberry", "Which size did Kessa request at Cloudberry?"),
    ("Lorin", "Emberfield", "When does Lorin reach Emberfield?"),
    ("Maren", "Dewhollow", "What pattern did Maren choose at Dewhollow?"),
    ("Neris", "Thistledown", "Which gate will Neris use at Thistledown?"),
    ("Orlan", "Pebblebay", "At what hour does Orlan set off for Pebblebay?"),
    ("Pella", "Juniperwake", "Where does Pella wait near Juniperwake?"),
    ("Quorin", "Starlingmere", "Which colour was Quorin's choice at Starlingmere?"),
    ("Ressa", "Frostwillow", "When will Ressa appear at Frostwillow?"),
    ("Soren", "Cinderbrook", "Which path will Soren take at Cinderbrook?"),
    ("Talia", "Rosequartz", "At what hour does Talia depart Rosequartz?"),
    ("Ulen", "Fernhollow", "Where will Ulen meet beside Fernhollow?"),
    ("Vessa", "Mooncaraway", "Which style did Vessa choose at Mooncaraway?"),
    ("Weylan", "Goldfinchbay", "When is Weylan due at Goldfinchbay?"),
    ("Xara", "Mallowridge", "Which doorway will Xara use at Mallowridge?"),
    ("Yorin", "Cloverglass", "What size did Yorin select at Cloverglass?"),
    ("Zelia", "Rainwillow", "At what hour does Zelia arrive at Rainwillow?"),
    ("Aroven", "Larkspurfen", "Where should Aroven wait at Larkspurfen?"),
    ("Belari", "Hearthsilver", "Which shade did Belari pick at Hearthsilver?"),
    ("Corven", "Mistypine", "When will Corven leave Mistypine?"),
    ("Dorell", "Velvetcove", "Which entrance will Dorell use at Velvetcove?"),
]


def _fixture(tmp_path, monkeypatch):
    home = tmp_path / "house"
    roots = []
    for name in ("Desktop", "Documents", "Downloads"):
        root = home / name
        root.mkdir(parents=True)
        roots.append(root)
    for number, (person, place, _ask) in enumerate(PERSONAL, start=1):
        (roots[number % len(roots)] / f"fragment_{number:02}.txt").write_text(
            f"{person} is expected at {place} at {number + 7}:30; note marker {place.lower()}.\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("core.runtime_execution_tools._readable_machine_roots", lambda: tuple(roots))

    def execute(intent, arguments, **_kwargs):
        path = Path(arguments["path"])
        if intent == "machine.read_file" and path.is_file():
            return SimpleNamespace(ok=True, status="ok", response_text=path.read_text(encoding="utf-8"), details={})
        return SimpleNamespace(ok=False, status="not_found", response_text="not found", details={})

    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=execute)
    return ctx


def _decide(ask: str, ctx: ToolContext) -> route.RouteDecision:
    matcher = getattr(tools, "document_matches", None)
    assert callable(matcher), "document-backed admission index is not implemented"
    return route.decide(
        ask,
        source_context=OWNER,
        resolve_brain=lambda: object(),
        env=ON,
        document_match=lambda value: matcher(value, ctx),
    )


def test_general_holdout_stays_out_and_document_backed_personal_asks_enter(tmp_path, monkeypatch):
    assert len(GENERAL_ASKS) == 40
    assert len(PERSONAL) == 30
    ctx = _fixture(tmp_path, monkeypatch)

    general = [_decide(ask, ctx) for ask in GENERAL_ASKS]
    admitted_personal = [_decide(ask, ctx).admit for _person, _place, ask in PERSONAL]
    base = [
        route.decide(ask, source_context=OWNER, resolve_brain=lambda: object(), env=ON).admit
        for ask in GENERAL_ASKS
    ]

    # Document words add no general ask. (The base route's own rules, unchanged by this work, admit
    # two of the forty: "my" in "...what is my speed?" and the topic word "list"; reported in the
    # hand-back, not hidden here.)
    assert [d.admit for d in general] == base
    assert sum(admitted_personal) >= 24


def test_memory_only_fact_does_not_admit_without_document_match(tmp_path, monkeypatch):
    ctx = _fixture(tmp_path, monkeypatch)
    ask = "When does Evaro arrive at Brackenlight?"
    assert not _decide(ask, ctx).admit


# --- A realistic household (added 2026-11-01, committed before the matcher change) ----------------
# The fragment home above holds only invented names, so no general ask can ever share a word with
# it. A real home also holds recipes, receipts and course notes in everyday words. The same 40
# general asks must stay out here too, and everyday personal asks, phrased the way people ask (with
# and without names), must get in. Read through VOOL's real authorized boundary over real files.

HOUSEHOLD = {
    "Documents/Health/clinic_letter.txt": (
        "Fennick Lane Clinic\nDear Ilse,\nYour eye examination is booked for Thursday 14 March at 10:40 "
        "with Dr Amsel. Please bring your current glasses.\n"
    ),
    "Documents/kids/swim_timetable.txt": (
        "Spring term timetable\nWren swims on Tuesdays at 17:15 in lane 3 with coach Petrov at Brindle.\n"
        "Odile is in the beginners pool on Saturdays at 09:00.\n"
    ),
    "Documents/house/annual_service.eml": (
        "Subject: Your annual visit\n\nHi, thanks for booking with Kettleby Heating. The boiler service is "
        "due again on 2 September. This year it came to 96.00.\n"
    ),
    "Desktop/weeknight food.md": (
        "# Weeknight food\n\nLentil soup: two onions, one carrot, red lentils, cumin. Simmer 25 min.\n"
        "Rice: rinse twice, cook 12 minutes, rest 5. Fluffy every time.\n"
    ),
    "Documents/trip_notes.txt": (
        "Portugal in June with Tomasz. Ferry across the river, then the coast road. Pack the guidebook "
        "and sun cream.\n"
    ),
    "Documents/receipts/halvard_receipt.txt": (
        "Halvard Music\nBeginner guitar, spare strings, chord book\nPaid 189.00\nCover: 2 years\n"
    ),
    "Documents/garden/allotment.txt": (
        "Corran Street allotment, plot 17. The water butt key is with Mrs Ogunleye at number 4. "
        "Repot the cactus in spring.\n"
    ),
    "Documents/school/trip_form.txt": (
        "Odile's class trip to the Marrowby science museum is on 22 May. Packed lunch needed. "
        "Coaches return by 15:30.\n"
    ),
    "Downloads/SCN_7781.txt": (
        "Ashgrove Vets\nPatient: Biscuit (spaniel)\nVaccination given today. The next booster is due on "
        "3 November.\n"
    ),
    "Documents/car/test_reminder.txt": (
        "Reminder for KX17 LMB: the yearly road test is due before 30 April. Booked at Dunmore Garage.\n"
    ),
    "Documents/work/course.md": (
        "Notes from the Python course: functions, strings, reversing a list with slicing. The next "
        "session is with Priya on Wednesday at 18:00.\n"
    ),
    "Documents/clubs/lantern_hill.txt": (
        "Lantern Hill astronomy club: the next viewing night is 9 October, bring the telescope if the sky "
        "is clear. The talk on comets is by Ines.\n"
    ),
    "Documents/house/household.txt": (
        "The spare key is with the neighbour at number 4. The bins go out on Thursday night. The window "
        "cleaner comes every fourth Monday, 18 per visit.\n"
    ),
    "Documents/gym.txt": "Tidewell Leisure: the membership renews on 1 February. Locker 212.\n",
    "Documents/family/dinner.txt": (
        "Tomasz's birthday dinner is booked at Osteria Vela, 19:30 on 11 July, table for six.\n"
    ),
    "Documents/kids/library.txt": "Three picture books for Wren are due back at the library on 8 August.\n",
    "Documents/Health/renewal.txt": "Ilse's passport runs out on 9 January 2027; the renewal form is printed.\n",
    "Documents/house/skylark.eml": (
        "Subject: Skylark Fibre\n\nYour Skylark Fibre agreement ends on 31 May. Router serial SKF-0093.\n"
    ),
    "Documents/kids/piano.txt": "Wren has piano with Mr Dallow on Fridays at 16:00; the grade 2 exam is on 6 June.\n",
    "Documents/family/sitter.txt": "Marisol can sit on Wednesdays, 12 an hour, and prefers a text the day before.\n",
    "Documents/kids/football.txt": "Odile's kit is size 7-8 with small shin pads. Matches are at Copsey Park.\n",
    "Documents/riverside.txt": "Riverside 10k on 14 September, bib 4471, start 09:30 at the boathouse.\n",
    "Documents/house/roof.txt": (
        "The solar panels were fitted by Brightmoor Energy in 2024: twelve panels, with the inverter in "
        "the loft.\n"
    ),
    "Documents/garden/plants.txt": "Water the fern weekly. The lemon tree needs feeding in April.\n",
}

EVERYDAY_PERSONAL_ASKS = [
    "when is grandma ilse's eye check?",
    "what time is wren's swim lesson?",
    "which lane is wren in at brindle?",
    "when is the boiler service due again?",
    "how much did the boiler service come to?",
    "who has the water butt key?",
    "what plot number is the allotment?",
    "when is odile's class trip?",
    "what time do the coaches get back from marrowby?",
    "when is biscuit's next booster?",
    "when is the road test due for the car?",
    "which garage is doing the road test?",
    "when is the next session with priya?",
    "when is the next astronomy viewing night?",
    "who is giving the talk on comets?",
    "where is the spare key?",
    "which night do the bins go out?",
    "when does the tidewell membership renew?",
    "what's the locker number at tidewell?",
    "where is tomasz's birthday dinner?",
    "when are wren's library books due back?",
    "when does ilse's passport run out?",
    "when does the skylark agreement end?",
    "what grade is wren taking in piano?",
    "what is marisol's hourly rate?",
    "what size is odile's football kit?",
    "what's the bib number for the riverside run?",
    "how often does the window cleaner come?",
    "who fitted the solar panels on the roof?",
    "when does the lemon tree need feeding?",
]


@pytest.fixture
def household(tmp_path, monkeypatch) -> ToolContext:
    home = (tmp_path / "home").resolve()
    for name in ("Desktop", "Documents", "Downloads"):
        (home / name).mkdir(parents=True)
    for relative, body in HOUSEHOLD.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return ToolContext(home=home, source_context=dict(OWNER))


def _household_decide(ask: str, ctx: ToolContext, *, with_documents: bool = True) -> route.RouteDecision:
    return route.decide(
        ask,
        source_context=OWNER,
        resolve_brain=lambda: object(),
        env=ON,
        file_words=lambda: set(tools.file_words(ctx.home)),
        document_match=(lambda value: tools.document_matches(value, ctx)) if with_documents else None,
    )


def test_household_document_words_admit_no_general_ask(household):
    assert len(EVERYDAY_PERSONAL_ASKS) == 30
    added = [
        ask for ask in GENERAL_ASKS
        if _household_decide(ask, household).admit and not _household_decide(ask, household, with_documents=False).admit
    ]
    assert added == []


def test_household_everyday_personal_asks_are_admitted(household):
    admitted = [ask for ask in EVERYDAY_PERSONAL_ASKS if _household_decide(ask, household).admit]
    missed = [ask for ask in EVERYDAY_PERSONAL_ASKS if ask not in admitted]
    assert len(admitted) >= 24, missed
