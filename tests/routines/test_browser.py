"""My routines in the served page, in Chromium: teach once, try, save, look at the list, run, and the
failure paths a person meets (the folder changed after they looked, a name already taken, a shared
file that is not a routine). Every step checks the files on disk, not only the page.
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.routines.served_rig import call, served, tree
from tests.served_browser import launch_chromium


def _shot(page, name):
    target = os.environ.get("VOOL_ROUTINES_SHOTS")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / (name + ".png")), full_page=True)


def _scans(folder: Path) -> None:
    for name, day in (("scan_001.pdf", 3), ("scan_002.pdf", 5), ("scan_003.pdf", 9)):
        path = folder / name
        path.write_bytes(b"%PDF-1.4 synthetic " + name.encode())
        stamp = time.mktime((2026, 11, day, 10, 0, 0, 0, 0, -1))
        os.utime(path, (stamp, stamp))
    (folder / "receipts.csv").write_text("date,file\n", encoding="utf-8")
    (folder / "notes.txt").write_text("not a scan", encoding="utf-8")


def _do_the_task_by_hand(folder: Path) -> None:
    (folder / "2026" / "11").mkdir(parents=True)
    os.rename(folder / "scan_001.pdf", folder / "2026" / "11" / "2026-11-03 scan_001.pdf")
    os.rename(folder / "scan_002.pdf", folder / "2026" / "11" / "2026-11-05 scan_002.pdf")
    with (folder / "receipts.csv").open("a", encoding="utf-8") as out:
        out.write("2026-11-03,2026-11-03 scan_001.pdf\n2026-11-05,2026-11-05 scan_002.pdf\n")


@pytest.fixture
def daemon(tmp_path):
    with served(tmp_path) as running:
        yield running


def test_teach_try_save_run_and_the_failure_paths(daemon, tmp_path):
    base, person, _vool = daemon
    downloads = person / "Downloads"
    _scans(downloads)
    outside = person / "Documents" / "untouched.txt"
    outside.write_text("outside the watched folder", encoding="utf-8")
    errors: list[str] = []
    refused: list[tuple[str, str, int]] = []
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1180, "height": 900}, accept_downloads=True)
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text)
                if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("response", lambda res: refused.append((res.request.method, res.url.split(base, 1)[-1], res.status))
                if res.status >= 400 else None)
        page.goto(base + "/routines?ui_locale=en")
        expect(page.get_by_role("heading", name="My routines", exact=True)).to_be_visible()
        expect(page.get_by_text("No routines yet. Teach one above.")).to_be_visible()
        _shot(page, "01-empty")

        # Teach: choose Downloads, watch, do the task once by hand, stop.
        page.locator("#rtTeach").get_by_role("button", name="Downloads", exact=True).click()
        page.get_by_role("button", name="Watch this folder while I do the task").click()
        expect(page.get_by_text("VOOL is watching Downloads")).to_be_visible()
        _do_the_task_by_hand(downloads)
        outside.write_text("changed while the watch ran", encoding="utf-8")
        expect(page.locator("#rtWatchCount")).to_have_text("Changes seen so far: 3", timeout=20000)
        _shot(page, "02-watching")
        page.get_by_role("button", name="I'm done").click()
        notes = page.locator("#rtNotes")
        expect(notes).to_contain_text("scan_001.pdf became 2026/11/2026-11-03 scan_001.pdf")
        expect(notes).to_contain_text("Rows added to receipts.csv: 2")
        assert "untouched" not in notes.inner_text(), "the watch noted a file outside its folder"

        # The proposal: read it, name it, try it (nothing changes), save it.
        page.get_by_role("button", name="Make a routine from this").click()
        expect(page.get_by_role("heading", name="Check your new routine")).to_be_visible()
        page.locator("#rtName").fill("Sort scans")
        before_try = tree(downloads)
        page.get_by_role("button", name="Try it", exact=True).click()
        expect(page.locator("#rtTry")).to_contain_text("scan_003.pdf becomes 2026/11/2026-11-09 scan_003.pdf")
        expect(page.locator("#rtTry")).to_contain_text("This was only a try: nothing in the folder changed.")
        assert tree(downloads) == before_try, "Try it changed a file"
        _shot(page, "03-proposal-tried")
        page.get_by_role("button", name="Save the routine").click()
        expect(page.get_by_role("status")).to_contain_text("Saved.")
        routine = page.locator("article.rt-routine").filter(has_text="Sort scans")
        expect(routine).to_contain_text("New name: [the file's date] [the original name]")
        expect(routine).to_contain_text("Goes into: [the file's year]/[the file's month]")
        expect(routine).to_contain_text("Not run yet")
        assert tree(downloads) == before_try, "saving the routine changed a file"
        _shot(page, "04-saved")

        # Look at the list, then the folder changes before Run: the run refuses and shows the new list.
        routine.get_by_role("button", name="See what it would do").click()
        expect(page.locator("#rtOps")).to_contain_text("scan_003.pdf becomes 2026/11/2026-11-09 scan_003.pdf")
        late = downloads / "scan_004.pdf"
        late.write_bytes(b"%PDF-1.4 synthetic late")
        stamp = time.mktime((2026, 11, 10, 9, 0, 0, 0, 0, -1))
        os.utime(late, (stamp, stamp))
        before_run = tree(downloads)
        page.get_by_role("button", name="Run these changes").click()
        expect(page.get_by_role("status")).to_contain_text("The folder changed since you looked. Here is the new list.")
        assert tree(downloads) == before_run, "a run went ahead on a list the person had not seen"
        expect(page.locator("#rtOps")).to_contain_text("scan_004.pdf becomes 2026/11/2026-11-10 scan_004.pdf")
        _shot(page, "05-plan-changed")
        page.get_by_role("button", name="Run these changes").click()
        expect(page.get_by_role("heading", name="What Sort scans did")).to_be_visible()
        expect(page.locator("#rtPanel")).to_contain_text("Done: 2 of 2 changes")
        expect(page.locator("#rtPanel")).to_contain_text("Checked in their new place: 2 of 2 files")
        _shot(page, "06-done")
        after = tree(downloads)
        assert "2026/11/2026-11-09 scan_003.pdf" in after and "scan_003.pdf" not in after
        assert "2026/11/2026-11-10 scan_004.pdf" in after and "scan_004.pdf" not in after
        assert after["receipts.csv"].decode().endswith(
            "2026-11-09,2026-11-09 scan_003.pdf\n2026-11-10,2026-11-10 scan_004.pdf\n")
        assert after["notes.txt"] == b"not a scan"
        page.get_by_role("button", name="Close", exact=True).click()
        routine.get_by_role("button", name="See what it would do").click()
        expect(page.locator("#rtPanel")).to_contain_text("There is nothing for this routine to do right now.")
        expect(page.get_by_role("button", name="Run these changes")).to_have_count(0)
        page.get_by_role("button", name="Cancel", exact=True).click()
        routine.get_by_role("button", name="Past runs").click()
        expect(page.locator("#rtPanel")).to_contain_text("2 of 2 changes done")
        page.get_by_role("button", name="Close", exact=True).click()

        # Change it (version 2), then go back to version 1.
        routine.get_by_role("button", name="Change it").click()
        page.locator("#rtMax").fill("10")
        page.get_by_role("button", name="Save the routine").click()
        expect(page.get_by_role("status")).to_contain_text("Saved.")
        routine.get_by_role("button", name="Earlier versions").click()
        expect(page.locator("#rtPanel")).to_contain_text("Saved version 2")
        expect(page.locator("#rtPanel")).to_contain_text("in use now")
        page.get_by_role("button", name="Go back to this version").click()
        page.get_by_role("button", name="Yes, go back").click()
        expect(page.get_by_role("status")).to_contain_text("The routine is back to that version.")

        # Share: the exact file, without the folder; then bring it back in.
        routine.get_by_role("button", name="Share it").click()
        expect(page.locator("#rtPanel pre").first).to_contain_text('"name": "Sort scans"')
        assert str(person) not in page.locator("#rtPanel").inner_text()
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Save the file").click()
        shared = tmp_path / "shared.zip"
        download_info.value.save_as(shared)
        with zipfile.ZipFile(shared) as archive:
            assert sorted(archive.namelist()) == ["SKILL.md", "routine.json"]
            assert str(person).encode() not in archive.read("routine.json") + archive.read("SKILL.md")
        page.get_by_role("button", name="Cancel", exact=True).click()
        not_a_routine = tmp_path / "holiday.zip"
        with zipfile.ZipFile(not_a_routine, "w") as archive:
            archive.writestr("photo.txt", "not a routine")
        page.locator("#rtImportFile").set_input_files(str(not_a_routine))
        page.get_by_role("button", name="Bring it in").click()
        expect(page.get_by_role("status")).to_contain_text("That file is not a routine shared from VOOL.")
        page.locator("#rtImportFile").set_input_files(str(shared))
        page.get_by_role("button", name="Bring it in").click()
        staged = page.locator("article[data-staged]")
        expect(staged).to_contain_text("Brought in from a shared file.")
        staged.get_by_role("button", name="Documents", exact=True).click()
        staged.get_by_role("button", name="Save it for this folder").click()
        expect(page.get_by_role("status")).to_contain_text("You already have a routine or a skill with that name.")
        staged.get_by_role("textbox").fill("Sort work scans")
        staged.get_by_role("button", name="Save it for this folder").click()
        expect(page.locator("article.rt-routine").filter(has_text="Sort work scans")).to_contain_text("Works in Documents")
        _shot(page, "07-shared-and-imported")

        # Remove it: the routine goes, the files it moved stay.
        moved = tree(downloads)
        routine.get_by_role("button", name="Remove it").click()
        page.get_by_role("button", name="Remove the routine").click()
        expect(page.get_by_role("status")).to_contain_text("The routine is removed.")
        assert tree(downloads) == moved
        expect(page.locator("article.rt-routine").filter(has_text="Sort scans")).to_have_count(0)
    finally:
        browser.close()
        manager.stop()
    assert not errors, errors
    # The only refusals are the ones the journey asked for: a list that changed, a file that is not a
    # routine, a name already taken.
    assert refused == [("POST", "/api/routines/run", 409), ("POST", "/api/routines/import", 400),
                       ("POST", "/api/routines/staged_save", 409)], refused


def _words(locale):
    root = Path(__file__).resolve().parents[2] / "core" / "i18n" / "catalogs"
    return json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))["messages"]


@pytest.mark.parametrize("locale,direction", [("de", "ltr"), ("ar", "rtl")])
def test_my_routines_in_another_language_on_a_phone_in_dark_mode(daemon, locale, direction):
    base, person, _vool = daemon
    downloads = person / "Downloads"
    _scans(downloads)
    routine = {"name": "Sort scans", "folder": str(downloads), "types": [".pdf"],
               "rename": [{"token": "date"}, {"text": " "}, {"token": "name"}], "into": [{"token": "year"}]}
    assert call(base, "/api/routines/save", {"routine": routine, "confirm": True})[0] == 200
    words, english = _words(locale), _words("en")
    before = tree(downloads)
    errors: list[str] = []
    manager, browser = launch_chromium()
    try:
        context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", locale=locale)
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + f"/routines?ui_locale={locale}")
        expect(page.get_by_role("heading", name=words["routines.title"], exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.dir") == direction
        assert page.evaluate("document.documentElement.lang") == locale
        expect(page.get_by_role("heading", name=words["routines.teach_heading"], exact=True)).to_be_visible()
        for key in ("routines.title", "routines.teach_heading", "routines.look", "routines.runs"):
            assert english[key] not in page.locator("body").inner_text(), key
        # Fits a phone: nothing wider than the screen.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # Dark mode: a dark page with light words.
        shade = page.evaluate("""() => {
            const rgb = s => s.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number);
            const lum = c => (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
            return [lum(rgb(getComputedStyle(document.body).backgroundColor)), lum(rgb(getComputedStyle(document.body).color))];
        }""")
        assert shade[0] < 0.25 < shade[1], shade
        _shot(page, f"10-{locale}-phone-dark")
        page.locator("article.rt-routine").get_by_role("button", name=words["routines.look"]).click()
        expect(page.locator("#rtOps")).to_contain_text("scan_001.pdf")
        if direction == "rtl":
            # A path inside a right-to-left sentence is kept whole, in its own direction.
            assert "\u20682026/2026-11-03 scan_001.pdf\u2069" in page.locator("#rtOps").inner_text()
        expect(page.get_by_role("button", name=words["routines.run"])).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, f"11-{locale}-phone-dark-plan")
        context.close()
    finally:
        browser.close()
        manager.stop()
    assert tree(downloads) == before, "looking at the list changed a file"
    assert not errors, errors
