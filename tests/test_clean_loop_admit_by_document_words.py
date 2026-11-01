"""An independently authored development hold-out for document-backed admission."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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

    assert sum(decision.admit for decision in general) == 0
    assert sum(admitted_personal) >= 24


def test_memory_only_fact_does_not_admit_without_document_match(tmp_path, monkeypatch):
    ctx = _fixture(tmp_path, monkeypatch)
    ask = "When does Evaro arrive at Brackenlight?"
    assert not _decide(ask, ctx).admit
