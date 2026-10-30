"""The playful words on the working card's title while a turn runs (English only).

While VOOL is genuinely working, the card's title rotates through these made-up words
("Vooling for 12s") instead of a flat "Working". They are decoration, never status:

* the plain state stays on the title's tooltip and on the screen-reader line, and the line under
  the title still says what VOOL is actually doing;
* waiting for approval, a paused price, the answer review, and every finished state show their
  plain words -- those are facts the user acts on, so they are never joked about;
* other languages keep the translated plain state (these words are not translated).

Every word is plain text with no emoji, short enough for one line, and harmless: playful nonsense,
a few German- and Spanish-flavoured inventions, nothing about people, bodies, or groups.
"""

from __future__ import annotations

import json

MAX_WORD = 28

WORKING_WORDS: tuple[str, ...] = (
    # VOOL's own.
    "Vooling",
    "Brooling",
    "Shmiging",
    "Revaporizing",
    "Drooling a little",
    "Digging",
    "Running",
    "Looking important",
    "Preparing the trick",
    "Wondering why I signed up",
    # Plain nonsense.
    "Noodling",
    "Percolating",
    "Zhuzhing",
    "Kerfuffling",
    "Tinkering",
    "Whirring",
    "Snorfling",
    "Moseying",
    "Doodling",
    "Juggling the bits",
    "Fluffing the pixels",
    "Wrangling the bytes",
    "Untangling spaghetti",
    "Shaking the snow globe",
    "Blooping",
    "Voolinating",
    # German-flavoured.
    "Festingbesting",
    "Denkenschmenking",
    "Kopfkratzing",
    "Knobeling",
    "Tüfteling",
    "Wunderwerkeling",
    "Gründlichkeiting",
    "Schnickschnacking",
    # Spanish-flavoured.
    "Pensandoing",
    "Buscandoing",
    "Arreglandoing",
    "Vamos-vamosing",
    "Trabajandito",
    "Cocinando ideas",
    "Poquito-más-ing",
)


def working_words_json() -> str:
    """The words as a JS array literal for the chat page (``<`` escaped, so a word can never
    close the script element)."""
    return json.dumps(list(WORKING_WORDS), ensure_ascii=False).replace("<", "\\u003c")
