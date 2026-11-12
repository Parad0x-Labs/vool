"""My routines' door on a real VOOL daemon: only this computer's own page reaches it, every change waits
for the person's confirm, a run needs the exact list they saw, and a due routine reaches the bell and
opens that list (it never runs by itself)."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.routines.conftest import put
from tests.routines.served_rig import call, in_home, served, tree

QUIET = {"VOOL_ROUTINE_SCHEDULE_POLLER": "0"}  # this test drives the schedule at chosen times itself


@pytest.fixture
def daemon(tmp_path):
    with served(tmp_path, extra_env=QUIET) as running:
        yield running


def _routine(person, **changes):
    folder = person / "Downloads"
    raw = {"name": "Sort scans", "folder": str(folder), "types": [".pdf"],
           "rename": [{"token": "date"}, {"text": " "}, {"token": "name"}], "into": [{"token": "year"}],
           "schedule": {"every": "daily", "time": "09:00", "weekday": 0}}
    raw.update(changes)
    return raw


def _raw_post(base, path, data: bytes, headers: dict[str, str]):
    try:
        response = urlopen(Request(base + path, data=data, headers=headers), timeout=60)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def test_only_this_computers_own_page_reaches_the_door(daemon):
    base, person, _vool = daemon
    host = base.split("//", 1)[1]
    body = json.dumps({"routine": _routine(person)}).encode()
    for headers, status in (({"Content-Type": "application/json", "Origin": "https://example.com"}, 403),
                            ({"Content-Type": "application/json", "Origin": "http://127.0.0.1:1"}, 403),
                            ({"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, 403),
                            ({"Content-Type": "text/plain"}, 415),
                            ({"Content-Type": "application/x-www-form-urlencoded"}, 415)):
        # Refused by the server's own cross-origin check or by this door's: either way, nothing is read.
        got, value = _raw_post(base, "/api/routines/try", body, headers)
        assert got == status and value.get("ok") is not True and "plan" not in value, (headers, got, value)
    assert _raw_post(base, "/api/routines/try", body, {"Content-Type": "application/json", "Origin": "http://" + host})[0] == 200
    for headers in ({"Origin": "https://example.com"}, {"Sec-Fetch-Site": "cross-site"}):
        status, value, _ = call(base, "/api/routines", headers=headers)
        assert status == 403 and "routines" not in value, (headers, status, value)
    status, value, headers = call(base, "/api/routines")
    assert status == 200 and value["ok"] and value["routines"] == [] and headers["Cache-Control"] == "no-store"


def test_every_change_waits_for_the_persons_confirm(daemon):
    base, person, _vool = daemon
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"%PDF first", day=3)
    before = tree(folder)

    def refused(action, body):
        status, value, _ = call(base, "/api/routines/" + action, body)
        assert (status, value["error"]) == (409, "confirmation_required"), (action, status, value)

    refused("save", {"routine": _routine(person)})
    refused("save", {"routine": _routine(person), "confirm": "yes"})
    assert call(base, "/api/routines")[1]["routines"] == []
    status, saved, _ = call(base, "/api/routines/save", {"routine": _routine(person), "confirm": True})
    assert status == 200 and saved["routine"]["version"] == 1
    routine = saved["routine"]
    refused("update", {"routine": dict(routine, max_files=5)})
    seen = call(base, "/api/routines/plan", {"id": routine["id"]})[1]["plan"]
    refused("run", {"id": routine["id"], "digest": seen["digest"]})
    refused("rollback", {"id": routine["id"], "version": 1})
    refused("remove", {"id": routine["id"]})
    assert tree(folder) == before
    assert [item["id"] for item in call(base, "/api/routines")[1]["routines"]] == [routine["id"]]
    # Unknown parts of a request and unknown actions are refused plainly.
    status, value, _ = call(base, "/api/routines/plan", {"id": routine["id"], "folder": "/"})
    assert (status, value["error"]) == (400, "bad_fields")
    status, value, _ = call(base, "/api/routines/delete_everything", {"confirm": True})
    assert (status, value["error"]) == (404, "not_found")
    status, value, _ = call(base, "/api/routines/plan", {"id": "rt-0000000000"})
    assert (status, value["error"]) == (404, "not_found")


def test_a_run_needs_the_exact_list_the_person_saw(daemon):
    base, person, _vool = daemon
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"%PDF first", day=3)
    routine = call(base, "/api/routines/save", {"routine": _routine(person), "confirm": True})[1]["routine"]
    seen = call(base, "/api/routines/plan", {"id": routine["id"]})[1]["plan"]
    put(folder, "scan_002.pdf", b"%PDF second", day=4)  # the folder changes after the person looked
    before = tree(folder)
    status, value, _ = call(base, "/api/routines/run", {"id": routine["id"], "digest": seen["digest"], "confirm": True})
    assert (status, value["error"]) == (409, "plan_changed") and tree(folder) == before
    seen = call(base, "/api/routines/plan", {"id": routine["id"]})[1]["plan"]
    status, value, _ = call(base, "/api/routines/run", {"id": routine["id"], "digest": seen["digest"], "confirm": True})
    assert status == 200 and value["receipt"]["checks"] == {"moved_as_planned": 2, "moved": 2}
    assert sorted(tree(folder)) == ["2026/2026-11-03 scan_001.pdf", "2026/2026-11-04 scan_002.pdf"]
    status, value, _ = call(base, "/api/routines/runs", {"id": routine["id"]})
    assert [run["id"] for run in value["runs"]] == [value["runs"][0]["id"]] and value["runs"][0]["planned"] == 2


TICK = """
from datetime import datetime
from core.routines import schedule
print(schedule.tick(datetime({when})))
"""


def test_a_due_routine_reaches_the_bell_and_opens_its_list_without_running(daemon, tmp_path):
    from tests.served_browser import launch_chromium

    base, person, _vool = daemon
    folder = person / "Downloads"
    put(folder, "scan_001.pdf", b"%PDF first", day=3)
    routine = call(base, "/api/routines/save", {"routine": _routine(person), "confirm": True})[1]["routine"]
    before = tree(folder)
    # The schedule's own pass, on the daemon's homes and database, at two chosen times.
    assert in_home(tmp_path, TICK.format(when="2030, 1, 7, 10, 0")).strip() == "[]"
    ran = in_home(tmp_path, TICK.format(when="2030, 1, 8, 9, 1"))
    assert f"'routine_id': '{routine['id']}'" in ran and "'changes': 1" in ran
    assert in_home(tmp_path, TICK.format(when="2030, 1, 8, 9, 30")).strip() == "[]"
    assert tree(folder) == before
    status, value, _ = call(base, "/api/notifications")
    notes = [item for item in value["items"] if (item.get("payload") or {}).get("kind") == "routine"]
    assert status == 200 and len(notes) == 1
    assert notes[0]["payload"]["open"] == f"/routines?run={routine['id']}" and notes[0]["payload"]["section"] == "needs"

    manager, browser = launch_chromium()
    errors: list[str] = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "/chat?ui_locale=en", wait_until="domcontentloaded")
        page.locator("#vfBell").click()
        page.get_by_text("Sort scans is ready to run").first.click()
        page.get_by_role("button", name="Open My routines").click()
        page.wait_for_url(re.compile(r"/routines\?run=" + routine["id"] + "$"))
        page.locator("#rtOps").wait_for(timeout=20000)
        assert "scan_001.pdf becomes 2026/2026-11-03 scan_001.pdf" in page.locator("#rtOps").inner_text()
        assert page.get_by_role("button", name="Run these changes").is_visible()
    finally:
        browser.close()
        manager.stop()
    assert tree(folder) == before, "opening the list from the bell changed a file"
    assert not errors, errors
