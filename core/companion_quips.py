"""The pet bubble's playful lines (English only).

The bubble above the pet speaks only while VOOL is busy, waiting on the user, or has just
finished. Each line is picked from a pool for the pet's TYPED state and activity, so the joke
changes but the meaning never does:

* ``ask`` lines always say plainly that a decision from the user is needed;
* ``done`` lines always say the job is finished, and never claim it was checked or correct
  (an ordinary finish is unreviewed);
* work lines follow the typed activity (reading, searching, ...), never model text.

Failures, cancels and flagged results are not joked about: the bubble shows the reducer's own
plain phrase for those. Other languages keep the translated plain phrases.

Every line fits the bubble (``MAX_LINE`` characters) and is plain text with no emoji.
"""

from __future__ import annotations

import json

MAX_LINE = 32

QUIPS: dict[str, tuple[str, ...]] = {
    # Thinking, starting, or a working category with no more specific activity.
    "think": (
        "Thinking cap on…",
        "Pondering deeply…",
        "Consulting the goblins…",
        "Warming up the brain…",
        "Doing the thinky thing…",
        "Hmm, let me see…",
        "Brewing an idea…",
        "Connecting the dots…",
        "Untangling the yarn…",
        "Asking the inner owl…",
        "Chewing on it…",
        "Cooking something up…",
        "Summoning the muses…",
        "Herding thoughts…",
        "Spinning the hamster wheel…",
        "Polishing the crystal ball…",
        "Trying to think…",
        "Gears are turning…",
    ),
    "search": (
        "Fetching unicorns…",
        "Scouring the web…",
        "Asking the internet nicely…",
        "Hunting for answers…",
        "Sniffing out sources…",
        "Chasing links…",
        "Raiding the library…",
        "Following the breadcrumbs…",
        "Casting a wide net…",
        "Sending out scouts…",
        "Interrogating the web…",
    ),
    "read": (
        "Reading the scrolls…",
        "Flipping pages…",
        "Skimming the fine print…",
        "Nose in the files…",
        "Studying the archives…",
        "Reading between the lines…",
        "Leafing through files…",
        "Speed-reading…",
        "Squinting at the details…",
    ),
    "dig": (
        "Digging…",
        "Rummaging around…",
        "Excavating…",
        "Turning over rocks…",
        "Spelunking the folders…",
        "Unearthing treasure…",
        "Digging deep…",
        "Checking under the couch…",
    ),
    "code": (
        "Tinkering…",
        "Hammering away…",
        "Making changes…",
        "Tightening the bolts…",
        "Sprinkling fixes…",
        "Rearranging the furniture…",
        "Applying elbow grease…",
        "Welding bits together…",
        "Swapping out the parts…",
    ),
    "exec": (
        "Goblins arrived, working…",
        "Running the machine…",
        "Pulling levers…",
        "Pressing the big button…",
        "Firing up the engines…",
        "Cranking the handle…",
        "Letting the tools work…",
        "Stoking the furnace…",
    ),
    "test": (
        "Poking it with a stick…",
        "Kicking the tyres…",
        "Running the checks…",
        "Testing, testing…",
        "Making sure it holds…",
        "Shaking it to see…",
        "Knocking on wood…",
    ),
    "watch": (
        "Keeping an eye on it…",
        "Watching closely…",
        "On lookout duty…",
        "Staring intently…",
        "Waiting for the kettle…",
        "Eyes peeled…",
    ),
    "retry": (
        "Trying that again…",
        "Round two…",
        "Once more, with feeling…",
        "Dusting off, retrying…",
        "Second attempt incoming…",
        "Not giving up…",
        "Take two…",
    ),
    "queued": (
        "In the queue…",
        "Waiting my turn…",
        "Standing in line…",
        "Holding my place…",
        "Next up, almost…",
        "Tapping my foot in line…",
    ),
    # Needs the user. Every line must say so plainly.
    "ask": (
        "Your decision, my lord",
        "Pit master, decision time",
        "Awaiting your verdict",
        "Need your OK to go on",
        "Your call, boss",
        "Waiting on your approval",
        "The court awaits your word",
        "Approve or deny? Your call",
        "Paused: need your decision",
        "Halting for your blessing",
        "Over to you, chief",
        "Need your say-so",
    ),
    # Finished. Never claims the answer was checked or right.
    "done": (
        "Job's done!",
        "Donzo.",
        "Done, going to sleep",
        "Task completed, heading out",
        "All done here",
        "Finished, back to napping",
        "That's a wrap",
        "Done and dusted",
        "Work's done, clocking out",
        "Done, time for a snack",
        "Finished, off I go",
        "Task done, putting my feet up",
    ),
}


def quips_json() -> str:
    """The pools as a JS object literal for the companion fragment."""
    return json.dumps({key: list(lines) for key, lines in QUIPS.items()}, ensure_ascii=False)
