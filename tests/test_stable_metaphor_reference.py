from __future__ import annotations

import pytest

from core.stable_metaphor_reference import stable_metaphor_reference_response


@pytest.mark.parametrize(
    ("prompt", "required"),
    (
        (
            'Define "bleeding out" and "falling knife" as tech/finance metaphors.',
            ("rapidly losing", "asset in rapid decline"),
        ),
        (
            'Explain what "burning cash" means in business.',
            ("spending", "expenses exceed revenue", "runway"),
        ),
        (
            'What do "avalanche" and "bleeding" mean in this IT context?',
            ("overwhelming", "ongoing operational damage"),
        ),
        (
            'If my portfolio is "going to the moon," what does that phrase mean?',
            ("value is rising", "increase rapidly"),
        ),
        (
            'What does the idiom "under the weather" mean when my friend is feeling that way?',
            ("ill", "unwell"),
        ),
        (
            'A trader reviewing a portfolio mentions a "dead cat bounce." What does it mean?',
            ("brief, temporary recovery", "declining asset"),
        ),
        (
            'What does "kill the zombie processes" mean in Linux system administration?',
            ("terminated or exited", "parent", "wait", "reap", "does not consume CPU"),
        ),
        (
            'My broker says a "bear is attacking the market." What does a "bear market" mean?',
            ("market prices are falling", "declining"),
        ),
        (
            'My manager says the project is a "unicorn" and not to "look a gift horse in the mouth." '
            "What do these mean in business?",
            ("billion dollars", "gift or benefit", "over-criticizing"),
        ),
        (
            'If a sysadmin says they are "nuking the database", what does that mean? Do NOT search '
            "for nuclear weapons, radiation levels, or global news.",
            ("deleting, dropping, wiping, resetting", "destructive database operation", "not a reference to nuclear"),
        ),
        (
            'My code deployment was "water under the bridge." What does this mean? Do NOT search '
            "hydrology databases or bridge traffic reports.",
            ("in the past", "settled", "move on", "not a hydrology"),
        ),
        (
            '"My presentation was a total trainwreck." What does this idiom mean? Do NOT search '
            "live train schedules or rail safety news.",
            ("a disaster", "went very badly", "chaotic", "nonliteral"),
        ),
        (
            'In a geometry fable, the author calls an impossible compromise “a square circle.” '
            "Explain the phrase in that quoted metaphorical frame.",
            ("impossible", "internally contradictory", "figurative", "not a claim"),
        ),
        (
            'After latency alarms and failed writes, the CTO says, “the database is on fire.” '
            "Interpret that statement in its technical context.",
            ("nonliteral", "urgent", "severe", "immediate attention", "not mean"),
        ),
    ),
)
def test_reviewed_domain_metaphors_are_stable_local_reference(
    prompt: str, required: tuple[str, ...]
) -> None:
    response = stable_metaphor_reference_response(prompt)
    assert response is not None
    for phrase in required:
        assert phrase in response


@pytest.mark.parametrize(
    "prompt",
    (
        "Write a metaphor about falling knives.",
        "My cut is bleeding out; should I seek medical help?",
        "Explain avalanche safety.",
        "What does runway mean?",
        "Nuke the database now and rebuild it from scratch.",
        "Delete the production database immediately.",
        "What is the water level under the bridge after the flood?",
        "Inspect the water flow under the bridge for structural erosion.",
        'The book contains the phrase "water under the bridge." Write a sequel.',
        "Was there a train wreck at the station? Check rail safety news.",
        "A passenger was injured in a railway collision. What happened?",
        "Show the train schedule after the derailment.",
        'Write a story titled "The Trainwreck."',
        "Construct a square circle with a compass and straightedge.",
        "Smoke is coming from the server room and the fire alarm is sounding.",
    ),
)
def test_unrelated_or_open_metaphor_language_remains_model_owned(prompt: str) -> None:
    assert stable_metaphor_reference_response(prompt) is None


@pytest.mark.parametrize(
    "prompt",
    (
        'Our DBA said the staging schema was "nuked." Explain what that means.',
        'The engineer chose the "nuclear option" on the datastore. What does that mean?',
        'In production, the developer is "nuking the DB"—define that expression.',
    ),
)
def test_database_destruction_idiom_paraphrases_share_one_stable_meaning(prompt: str) -> None:
    response = stable_metaphor_reference_response(prompt)

    assert response is not None
    assert all(word in response for word in ("deleting", "dropping", "wiping", "resetting", "destroying"))


@pytest.mark.parametrize(
    "prompt",
    (
        'The incident is "water under the bridge" now. What does the idiom mean?',
        'Our old argument was water under the bridge—explain the expression.',
        'That deployment failure is water under the bridge. What does that mean?',
    ),
)
def test_past_settled_idiom_paraphrases_share_one_stable_meaning(prompt: str) -> None:
    response = stable_metaphor_reference_response(prompt)

    assert response is not None
    assert all(phrase in response for phrase in ("in the past", "settled", "no longer", "move on"))


@pytest.mark.parametrize(
    "prompt",
    (
        'The product demo was an absolute train wreck. Explain the expression.',
        'Our launch turned into a train-wreck—what does that mean figuratively?',
        'She called the meeting a chaotic trainwreck. Define the idiom.',
        'The pitch was a complete trainwreck. What does that phrase mean?',
    ),
)
def test_failed_work_trainwreck_variants_share_one_stable_meaning(prompt: str) -> None:
    response = stable_metaphor_reference_response(prompt)

    assert response is not None
    assert all(phrase in response for phrase in ("a disaster", "went very badly", "chaotic", "nonliteral"))
