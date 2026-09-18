"""pa_beta_gate -- revision-5: a path NAMES a place; its words never choose the operator action.

Found by the revision-5 neighbour run (evidence/ingress-neighbours-01.log). Under a working directory
named ``.../02-calendar-notes/...``, four served ``OperatorActionTests`` turned a disk request into a
notes search, and a fifth (failing at the df49f096 base too) turned a file move into a calendar move:
the recognizers read ``notes`` and ``calendar`` out of the temporary path. The cases here use
deliberately named paths and the pure parser; the served original cases are those
``OperatorActionTests`` run under the revision-5 TMPDIR.
"""
from __future__ import annotations

import pytest

from core.execution.constants import text_without_paths
from core.operator.parser import parse_operator_action_intent


@pytest.mark.parametrize(("text", "kind", "target", "destination"), [
    ('find disk bloat in "/Users/ops/My Notes/archive"', "inspect_disk_usage", "/Users/ops/My Notes/archive", None),
    ("find disk bloat in ~/calendar/free-slot-exports", "inspect_disk_usage", None, None),
    ('move "/srv/calendar/export.txt" to "/srv/backups"', "move_path", "/srv/calendar/export.txt", "/srv/backups"),
    (r"find disk bloat in C:\Users\ops\Notes", "inspect_disk_usage", r"C:\Users\ops\Notes", None),
    ("find disk bloat in /Users/ops/work/02-calendar-notes/revision-5/tmp/x", "inspect_disk_usage",
     "/Users/ops/work/02-calendar-notes/revision-5/tmp/x", None),
])
def test_words_inside_a_path_never_choose_the_action_and_the_path_argument_is_unchanged(text, kind, target, destination):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind, (text, intent)
    assert (intent.target_path, intent.destination_path) == (target, destination)


@pytest.mark.parametrize(("text", "kind"), [
    ('save a note titled "Log paths" with: logs live in /var/log/notes', "save_note"),
    ("find notes about the /srv/calendar migration", "find_notes"),
    ('cancel the "Ops/Infra sync" meeting', "cancel_calendar_event"),
    ("move my standup/meeting to Friday", "move_calendar_event"),
    ("remind me at 16:00 Europe/Berlin to call the depot", "schedule_reminder"),
    ("move '/tmp/a.txt' to '/tmp/b/'", "move_path"),
    ("check Tuesday afternoon for a free 30-minute slot", "check_availability"),
    ('propose "Q3/Q4 planning"', "propose_calendar_event"),
    ("list my calendars", "list_calendars"),
])
def test_request_words_outside_paths_still_choose_the_action(text, kind):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind, (text, intent)


def test_path_blind_text_hides_path_and_url_words_but_keeps_quotes_titles_and_slashed_words():
    quoted = text_without_paths('find disk bloat in "/Users/ops/My Notes/archive" please')
    assert quoted.startswith('find disk bloat in "') and "Notes" not in quoted and quoted.endswith('" please'), quoted
    for text, hidden in (("find disk bloat in ~/calendar/free-slot-exports", "calendar"),
                         (r"find disk bloat in C:\Users\ops\Notes", "Notes"),
                         ("open https://calendar.example.com/notes/42 later", "notes"),
                         ("move '/tmp/reminders/a.txt' to '/tmp/b/'", "reminders")):
        blind = text_without_paths(text)
        assert hidden not in blind and "path" in blind, (text, blind)
    for unchanged in ("remind me at 16:00 Europe/Berlin to call the depot", 'cancel the "Ops/Infra sync" meeting',
                      "move my standup/meeting to Friday", "check Tuesday afternoon for a free 30-minute slot"):
        assert text_without_paths(unchanged) == unchanged
