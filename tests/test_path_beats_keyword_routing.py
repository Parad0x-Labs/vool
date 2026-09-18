"""A filesystem path the user typed is a LOCATION, and must never be read as a topic keyword.

Every case here was reproduced by driving the daemon after a full state wipe, each with different
wording and a different folder, because the same fault kept reappearing behind a new matcher:

* "take a look inside /Users/me/Desktop/vool-audit-scratch/ledger-demo and tell me what the billing
  helper does" listed the WHOLE of ~/Desktop -- `\\bdesktop\\b` matched inside the path and "tell me
  what" supplied the listing verb.
* "whats inside ~/Desktop/vool-audit-scratch/route-planner" answered with the VOOL identity glossary
  -- "vool" matched inside the FOLDER NAME.
* "give me an inventory of everything stored in ~/Desktop/tide-charts" was routed to a tools-less
  chat lane (`file_inspection` is a chat-lane class) and the model replied "I'll check the contents
  ... Let me run the scan" while nothing ran.

On macOS practically every user file lives under Desktop/Downloads/Documents, so a bare keyword test
over the raw message misfires constantly. These assert the discriminator directly: prose decides
topics, paths decide places.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_machine import looks_like_supported_machine_read_request
from core.agent_runtime.runtime_checkpoint_lane_policy import _names_a_concrete_path
from core.agent_runtime.turn_frontdoor import _explicit_path_in
from core.bootstrap_context import _web0_null_facts_for
from core.web0_project_grounding import _ecosystem_definition_response, prose_only, web0_boot_context

# A path was named while asking for something a listing does not answer ("tell me what it does",
# "give me an inventory of everything stored in"). The home-folder lane has nothing to offer these,
# so it must keep standing down rather than dumping the parent folder.
PATH_REQUESTS_ASKING_MORE_THAN_A_LISTING = [
    "take a look inside /Users/me/Desktop/vool-audit-scratch/ledger-demo and tell me what it does",
    "give me an inventory of everything stored in /Users/me/Documents/pollen-index",
]

# A path was named AND the question is what is inside it. Originally these stood down too, because
# the lane could only resolve Desktop/Downloads/Documents and standing down was the only way to
# stop it listing the parent. It now lists the path the user typed, so standing down would leave a
# question it can answer exactly to whichever other lane won the run -- which is how one of these
# came back as a machine-specs block on a blind QA drive. The invariant was never "never claim a
# path", it was "never answer about a subfolder by listing its parent", and that is what is
# asserted below.
PATH_REQUESTS_ASKING_FOR_THE_CONTENTS = [
    "whats inside ~/Desktop/vool-audit-scratch/route-planner",
    "which files are sitting in /Users/me/Desktop/vool-audit-scratch/tide-charts right now",
    "show me what is in /Users/me/Downloads/invoices-2026",
]

PATH_REQUESTS = PATH_REQUESTS_ASKING_MORE_THAN_A_LISTING + PATH_REQUESTS_ASKING_FOR_THE_CONTENTS

PROSE_REQUESTS = [
    "list what is on my desktop",
    "show me the contents of my downloads folder",
    "what do we have on documents",
]


@pytest.mark.parametrize("request_text", PATH_REQUESTS_ASKING_MORE_THAN_A_LISTING)
def test_the_home_folder_lane_stands_down_when_a_path_is_named(request_text: str) -> None:
    assert looks_like_supported_machine_read_request(request_text) is False


@pytest.mark.parametrize("request_text", PATH_REQUESTS_ASKING_FOR_THE_CONTENTS)
def test_a_contents_question_lists_the_named_path_and_never_its_parent(request_text: str) -> None:
    from core.execution.constants import machine_path_listing_intent

    assert looks_like_supported_machine_read_request(request_text) is True
    listed = machine_path_listing_intent(request_text)
    assert listed == _explicit_path_in(request_text), listed
    # The failure this guards is the parent dump: the path must keep every segment the user typed.
    assert listed.rstrip("/").count("/") >= 2, listed


@pytest.mark.parametrize("request_text", PROSE_REQUESTS)
def test_a_plain_folder_request_still_reaches_that_lane(request_text: str) -> None:
    assert looks_like_supported_machine_read_request(request_text) is True


@pytest.mark.parametrize("request_text", PATH_REQUESTS)
def test_a_named_path_is_extracted_verbatim(request_text: str) -> None:
    # The caller uses this instead of trusting a 0.6B model to copy a path back: asked about
    # "/Users/me/Desktop/route-planner" the arbiter answered argument="Desktop".
    extracted = _explicit_path_in(request_text)
    assert extracted.startswith(("/", "~")), extracted
    assert extracted.count("/") >= 2, extracted


@pytest.mark.parametrize("request_text", PROSE_REQUESTS)
def test_no_path_is_invented_when_the_user_named_none(request_text: str) -> None:
    assert _explicit_path_in(request_text) == ""


@pytest.mark.parametrize(
    "request_text",
    [
        "whats inside ~/Desktop/vool-audit-scratch/route-planner",
        "peek into /Users/me/Desktop/vool-audit-scratch/ledger-demo and list what is stored",
        "read /Users/me/vool-local-product/README.md",
    ],
)
def test_a_project_name_inside_a_path_is_not_a_topic(request_text: str) -> None:
    assert web0_boot_context(request_text) == ""
    assert _web0_null_facts_for(request_text.lower()) == ""
    assert _ecosystem_definition_response(request_text) is None


@pytest.mark.parametrize(
    "request_text",
    ["what is VOOL?", "what is web0?", "who is Parad0x Labs?", "explain the vool runtime"],
)
def test_the_same_terms_in_prose_still_ground(request_text: str) -> None:
    assert _ecosystem_definition_response(request_text) is not None or web0_boot_context(request_text)


def test_prose_only_blanks_paths_and_keeps_words() -> None:
    masked = prose_only("peek into /Users/me/Desktop/vool-audit-scratch/ledger-demo and list it")
    assert "vool" not in masked.lower()
    assert "peek into" in masked and "list it" in masked


@pytest.mark.parametrize(
    ("text", "is_path"),
    [
        ("give me an inventory of everything in /Users/me/Desktop/tide-charts", True),
        ("open ~/Desktop/notes.md", True),
        ("check ./src/app.py", True),
        ("cd ../parent-project and build", True),
        ('look at "/etc/hosts" please', True),
        ("how do I list a directory in Python?", False),
        ("what is the read/write difference?", False),
        ("we run 24/7 support", False),
        ("the ratio is 3/4 of total", False),
        ("is it and/or?", False),
    ],
)
def test_only_a_rooted_path_counts_as_naming_a_place(text: str, is_path: bool) -> None:
    # An unrooted slash between two words is punctuation, not a location -- misreading "read/write"
    # as a path would drag ordinary advice questions into the tool lane.
    assert _names_a_concrete_path(text) is is_path
