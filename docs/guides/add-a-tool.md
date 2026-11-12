---
description: Add a tool to VOOL: what a tool card is, what it may and may not say, and how to test one.
---

# Add a tool

You have a plugin with a tool, or you are writing one, and you want VOOL to offer it when
someone needs it — not only when they type the tool's exact intent token. This guide is about
the **card**: the small block that says *when a person needs your tool*, in the words a person
would actually type.

> Card routing is planned for 0.8, not released yet. The validator, the router and the
> test described here are already in the code you have; nothing here ships in a released build
> until the changelog says so.

## What a card is

A card is one JSON object with four fields, and only these four:

| Field | What it says | Rules |
| --- | --- | --- |
| `use_when` | One plain sentence: the situation in which a person needs this tool. | 12–300 characters. |
| `examples` | Short asks a person would actually type when they need the tool. | 3–24 asks, each up to 200 characters; a repeated ask counts once. Any language. |
| `not_for` | Optional — near misses: asks that must NOT pick this tool (advice, how-to questions, talking about the topic). | Up to 16 asks, same shape as examples. Never the same ask as an example. |
| `surfaces` | Optional: the surfaces the tool is offered on. | `pc`, `phone`, `chat_app`. It can only narrow — no list means every surface. |

The card for a fictional tide-times plugin looks like this
([templates/tool-card.example.json](templates/tool-card.example.json) in this folder):

```json
{
  "use_when": "The person wants to know the tide times or the tide height at a place, today or on a date they name.",
  "examples": [
    "what time is high tide today",
    "when is low tide at the harbour tomorrow",
    "tide times for saturday please",
    "a que hora sube la marea",
    "is the tide going out right now"
  ],
  "not_for": [
    "how do tides work",
    "what causes high tide"
  ]
}
```

Two things make a card work: **examples that sound like real asks** (typed by a person in a
hurry, in any language — the router folds accents off, so *descargar* meets *descárgar*), and
**near misses in `not_for`** for the asks that mention the same words but want an explanation,
not the tool.

## Where a card lives

**Your plugin's card** goes inside the tool's entry in the pack manifest
(`.codex-plugin/plugin.json`), next to `input_schema` and `side_effect_class`:

```json
{
  "name": "tide-tables",
  "version": "1.0.0",
  "runtime": { "contract_version": 1 },
  "tools": [
    {
      "intent": "tide-tables.next_tide",
      "description": "Report the next high and low tide times for a named place.",
      "handler": { "kind": "subprocess", "entry": "bin/run" },
      "input_schema": { "type": "object", "additionalProperties": false, "properties": {} },
      "side_effect_class": "read_only",
      "approval_requirement": "none",
      "card": {
        "use_when": "The person wants to know the tide times or the tide height at a place, today or on a date they name.",
        "examples": [
          "what time is high tide today",
          "when is low tide at the harbour tomorrow",
          "tide times for saturday please",
          "a que hora sube la marea",
          "is the tide going out right now"
        ],
        "not_for": ["how do tides work", "what causes high tide"]
      }
    }
  ]
}
```

Three rules the loader holds you to:

* A plugin's intents must start with its own name (`tide-tables.` above), so two packs can
  never collide.
* A bad card fails the whole manifest at load time, with a message that names your tool and
  the field — not at turn time, and never silently.
* A builtin card file can never describe a plugin tool. Your card is read from your manifest
  only, whatever intent it sits under.

**Built-in cards** (VOOL's own tools) live as JSON files in `core/tool_card_data/`, one file
per family (`file.json`, `email.json`, …), keyed by intent. A key starting with `_` (like
`_about`) is an author note and is skipped. If you are changing VOOL itself, that is where
your card goes.

## What a card can never do

A card **grants nothing**. It carries no side-effect class, no approval, no permission action,
no credential, no network host and no money field — a card that tries to carry any of these is
refused loudly at load time, because someone might believe it took effect. What a tool may do
is declared once in the tool spec (`side_effect_class`, `approval_requirement`,
`permission_actions`) and every gate — approvals, spending caps, receipts — reads that, exactly
as if the card did not exist. The most a card can do is put your tool in front of the model;
the model still decides whether to call it, and every gate still applies.

A card is read **only on your own local turns**. A remote session — a chat app bridge, a
shared link — never gains a tool from card words, whatever its card says. Whatever a remote
lane is offered today, it keeps; cards add nothing there.

A card **runs no code**. It is data. It is never executed, and it cannot change what another
tool does.

## How the router uses it

When a turn's words come in, every registered tool that carries a card is scored against them,
locally and with no chat-model call (a plugin tool with no card is never matched by words):

* The ask and each example are reduced to word stems; stems shared with an example count more
  when they are rare across all cards (a stem every card uses, like *find*, counts for almost
  nothing; *tide* counts for a lot). Character trigrams add robustness for typos and word forms
  the stemmer misses.
* Common words of the other app languages (German *welche*, Spanish *dónde*, French *quel*,
  …) are dropped, and the three safe folder names in any language become one word — so a
  listing ask in German meets the listing card's examples.
* A `not_for` ask that fits the turn at least as well as the best example vetoes the card.
* The best few cards win: at most four card seats per turn, and a runner-up close to the top
  (85% of the best score) is seated too, because the ask may fit both.

The scores gate two doors (the same matches also feed two smaller readers — a tool-use retry
that insists on a near-certain fit before it overrides an answer, and one folder check):

1. **The chat lane.** A strong match (a score of 0.50 with meaning in play, or 0.75 for a
   near-verbatim word fit) tells VOOL this turn needs tools, so it is not answered tool-less
   from the model's general knowledge.
2. **The offer.** Matched intents are seated into what the model can see (inside the existing
   seat cap). The model then decides whether to call your tool.

Word overlap alone only counts when the ask is nearly one of your examples (0.75). When the
local meaning embedder is running, asks are also compared by meaning, and a genuinely
different phrasing can match. That is why examples should be the asks people really type.

Install a pack while VOOL runs and its tools are routable on the next turn — the card index
follows the tool registry, with no restart and no router edit. Remove the pack, and its words
route nowhere. The whole mechanism sits behind the `tools.card_routing` policy flag, so a
person can turn it off (`VOOL_TOOL_CARD_ROUTING=0` does the same for a session).

## Test your card

From the repository root:

```bash
# 1. The checker: your template file and every built-in card through the real validator.
PYTHONPATH=. python -m pytest tests/test_tool_card_template.py -q

# 2. See what your card matches, with no app running:
PYTHONPATH=. python - <<'PY'
import json
from pathlib import Path

from core.plugin_tools import parse_manifest
from core.tool_card_router import match_cards

manifest = {
    "name": "tide-tables",
    "version": "1.0.0",
    "runtime": {"contract_version": 1},
    "tools": [{
        "intent": "tide-tables.next_tide",
        "description": "Report the next high and low tide times for a named place.",
        "handler": {"kind": "subprocess", "entry": "bin/run"},
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
        "side_effect_class": "read_only",
        "approval_requirement": "none",
        "card": json.loads(Path("docs/guides/templates/tool-card.example.json").read_text()),
    }],
}
contracts = parse_manifest(manifest, path=Path("tide-tables/.codex-plugin/plugin.json")).contracts
for ask in ("what time is high tide today", "how do tides work", "sea level rise explained"):
    print(ask, "->", [(m.intent, m.score) for m in match_cards(ask, contracts=contracts)])
PY
```

`match_cards` reads no policy and no trust, so it is safe to call from a script. What you
want to see: real asks score above 0.42 (the seat floor) — or above 0.75 if only word overlap
can run — and the near misses in your `not_for` score higher than any example, so they veto.
If an ask you care about scores low, add it (or its phrasing) to `examples`; if a wrong ask
scores high, add it to `not_for`. Then re-run the snippet.
