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
        "Oiling the gears…",
        "Polishing the crystal ball…",
        "Thinking it through…",
        "Gears are turning…",
        "I'm on it…",
        "Give me a sec…",
        "Goblins conferring…",
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
        "Asking around the web…",
        "Fetching more unicorns…",
        "Rounding up sources…",
        "Unicorn hunt underway…",
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
        "Reading up…",
        "Poring over pages…",
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
        "Dusting off old files…",
        "Goblins arrived, digging…",
        "Shovel in hand…",
    ),
    "code": (
        "Tinkering…",
        "Hammering away…",
        "Making changes…",
        "Tightening the bolts…",
        "Adding the finishing bits…",
        "Rearranging the furniture…",
        "Applying elbow grease…",
        "Welding bits together…",
        "Swapping out the parts…",
        "Putting it together…",
        "Fitting the pieces…",
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
        "Goblins on the job…",
        "Gears are whirring…",
        "Machines humming…",
        "Elves at work…",
    ),
    "test": (
        "Poking it with a stick…",
        "Kicking the tyres…",
        "Running the checks…",
        "Testing, testing…",
        "Making sure it holds…",
        "Shaking it to see…",
        "Giving it a test drive…",
        "Taking it for a spin…",
        "Double-checking the bits…",
        "Testing the waters…",
    ),
    "watch": (
        "Keeping an eye on it…",
        "Watching closely…",
        "On lookout duty…",
        "Watching the pot…",
        "Waiting for the kettle…",
        "Eyes peeled…",
        "Keeping watch…",
        "Minding the cauldron…",
        "Ears perked…",
        "Standing guard…",
    ),
    "retry": (
        "Trying that again…",
        "Round two…",
        "Once more, with feeling…",
        "Dusting off, retrying…",
        "Second attempt incoming…",
        "Having another go…",
        "Take two…",
        "One more try…",
        "Back at it…",
        "Again, from the top…",
    ),
    "queued": (
        "In the queue…",
        "Waiting my turn…",
        "Standing in line…",
        "Holding my place…",
        "Almost my turn…",
        "Patiently in line…",
        "Saving my spot…",
        "Queued up nicely…",
        "Waiting in the wings…",
        "Next in line…",
    ),
    # Needs the user. Every line must say so plainly.
    "ask": (
        "Your decision, my lord",
        "Pit master, decision time",
        "Boss, I need your decision",
        "Your OK needed to continue",
        "Waiting on you: approve?",
        "Awaiting your verdict",
        "Need your OK to go on",
        "Waiting on your approval",
        "Approve or deny? Your call",
        "Paused: need your decision",
        "Royal decree needed: approve?",
        "Can't go on without your OK",
        "Decision time, over to you",
        "Your answer needed to go on",
    ),
    # Finished. Never claims the answer was checked or right.
    "done": (
        "Job's done",
        "Donzo",
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
        "Donzo, nap time",
        "Done, clocking off",
        "Finished, kettle's on",
        "All wrapped up",
        "Done, curling up now",
        "Job's done, stretching out",
    ),
}


def quips_json() -> str:
    """The pools as a JS object literal for the companion fragment."""
    return json.dumps({key: list(lines) for key, lines in QUIPS.items()}, ensure_ascii=False)
