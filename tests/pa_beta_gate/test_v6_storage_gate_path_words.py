"""pa_beta_gate -- revision-6: the storage gate reads the request's words, never the words inside its paths.

Revision 5 made recognition read ``text_without_paths`` but left one gate reading the original text:
``parse_operator_action_intent`` passed ``text`` to ``asks_runtime_for_a_fact``, whose produce/teach guard
found "review ... notes" in a temporary path named ``.../review/02-calendar-notes/...`` and turned four
served disk requests into advice (the review's served originals, ``OperatorActionTests`` under that
TMPDIR). The review probe added two short paths the same way: ``/tmp/review/notes`` and
``/tmp/draft/docs``. The cases here are those shapes and new ones -- unquoted, home-relative, Windows,
URL -- with the extracted path checked unchanged, plus the guards that must keep working: a request to
review notes or write docs is not a measurement, even when it also names a path, and prose that mentions
storage stays prose.
"""
from __future__ import annotations

import pytest

from core.execution.constants import text_without_paths
from core.operator.parser import parse_operator_action_intent

_DISK_REQUESTS = [
    pytest.param('find disk bloat in "/tmp/review/notes"', "/tmp/review/notes", id="review-probe-review-notes"),
    pytest.param('find disk bloat in "/tmp/draft/docs"', "/tmp/draft/docs", id="review-probe-draft-docs"),
    pytest.param('find disk bloat in "/Users/ops/review/02-calendar-notes/revision-6/tmp/operator-case"',
                 "/Users/ops/review/02-calendar-notes/revision-6/tmp/operator-case", id="review-segment-working-directory"),
    pytest.param("find disk bloat in /srv/review/notes-archive", "/srv/review/notes-archive", id="unquoted-posix-review-notes"),
    pytest.param('what is eating space in "~/drafts/explain the docs"', "~/drafts/explain the docs", id="home-path-with-authoring-words"),
    pytest.param(r"find large files in C:\Users\ops\Review\Notes", r"C:\Users\ops\Review\Notes", id="windows-review-notes"),
    pytest.param('find disk bloat in "/Volumes/Archive/write the summary/notes"', "/Volumes/Archive/write the summary/notes", id="quoted-path-with-a-whole-authoring-phrase"),
    pytest.param('can you find disk bloat in "/tmp/review/notes"?', "/tmp/review/notes", id="ask-shaped-review-notes"),
    pytest.param('could you check what is eating space in "/tmp/draft/docs"?', "/tmp/draft/docs", id="ask-shaped-draft-docs"),
]


@pytest.mark.parametrize(("text", "target"), _DISK_REQUESTS)
def test_disk_request_in_a_path_named_like_authoring_still_dispatches_with_the_path_unchanged(text, target):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "inspect_disk_usage", (text, intent)
    assert intent.target_path == target and intent.raw_text == text


# Two guards keep these non-measurement requests: an imperative authoring request carries no ask clause at
# all, while an ask-shaped one ("can you review my notes about ...?") carries the storage words in its ask
# and is held by the produce/teach guard. Both kinds are here, and each keeps its meaning with the gate
# reading path-blind words (evidence/fix5-guard-probe.log records which guard holds which request).
_NOT_MEASUREMENTS = [
    pytest.param("can you review my notes about how much disk space is left?", id="ask-review-notes-about-free-space"),
    pytest.param('could you review my notes on the disk bloat in "/tmp/review/notes"?', id="ask-review-notes-naming-a-review-notes-path"),
    pytest.param("can you write docs about how much disk space this machine has left?", id="ask-write-docs-about-free-space"),
    pytest.param('could you draft docs about what is eating space in "/tmp/draft/docs"?', id="ask-draft-docs-naming-a-draft-docs-path"),
    pytest.param("review my notes about disk space before the meeting", id="review-notes-about-disk-space"),
    pytest.param("write docs about disk bloat for the onboarding guide", id="write-docs-about-disk-bloat"),
    pytest.param('draft docs explaining the disk bloat we found in "/tmp/review/notes"', id="draft-docs-that-also-names-a-path"),
    pytest.param('summarise this review of the storage space usage report at "/srv/reports/q3.txt"', id="summarise-a-review-that-names-a-path"),
    pytest.param("My landlord says the storage space in the basement is included in the rent, is that normal in Lithuania?", id="prose-about-a-basement"),
]


@pytest.mark.parametrize("text", _NOT_MEASUREMENTS)
def test_authoring_and_prose_about_storage_stay_non_measurement_requests(text):
    intent = parse_operator_action_intent(text)
    assert intent is None or intent.kind != "inspect_disk_usage", (text, intent)


def test_the_gate_reads_the_same_path_blind_words_recognition_reads():
    """The produce/teach guard sees 'review ... notes' only when the path's own words are left in."""
    from core.execution.constants import _PRODUCE_OR_TEACH_RE

    text = 'find disk bloat in "/tmp/review/notes"'
    assert _PRODUCE_OR_TEACH_RE.search(text.lower()), "the original text carries the authoring words inside its path"
    assert not _PRODUCE_OR_TEACH_RE.search(text_without_paths(text).lower())
