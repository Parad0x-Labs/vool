"""Served proof: VOOL Doctor over the real /api/doctor door of a running VOOL, before and after its model
server goes away, and the Settings page words what it says.

- a working model, a readable Documents folder and a sound database read as working;
- parts that could not be read (an updater with no install receipt) keep the summary off all-green;
- the model server stops: the next check says so, names the repair, and the summary is a problem;
- the page shows the part, its plain words and a repair button, in the person's language.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _Provider

REPO = Path(__file__).resolve().parents[1]


def _doctor(daemon: ServedDaemon) -> dict:
    with urlopen(f"{daemon.base_url}/api/doctor", timeout=60) as response:
        return json.loads(response.read().decode())


def _rows(report: dict) -> dict[str, dict]:
    return {row["id"]: row for row in report["checks"]}


def _summary_is_never_greener_than_its_parts(report: dict) -> None:
    states = [row["state"] for row in report["checks"]]
    if "problem" in states:
        assert report["summary"] == "problem", report
    elif "unknown" in states:
        assert report["summary"] == "unknown", report
    else:
        assert report["summary"] == "ok", report


def _page(daemon: ServedDaemon, lang: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/settings?ui_locale={lang}#about/doctor", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid=doctor-list] .doctor-item", timeout=30000)
            assert page.query_selector("[data-check=local_model] [data-repair=models]"), "the problem offers its repair"
            return page.inner_text("[data-testid=doctor-summary]") + "\n" + page.inner_text("[data-testid=doctor-list]")
        finally:
            browser.close()


def test_the_doctor_reads_each_part_and_says_when_the_model_server_stops(tmp_path):
    home = tmp_path / "home"
    person = tmp_path / "person"
    (person / "Documents").mkdir(parents=True)
    provider = _Provider({MODEL: "Okay."})
    provider.__enter__()
    try:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(person)}
        with ServedDaemon(home, env_extra=env) as daemon:
            first = _doctor(daemon)
            rows = _rows(first)
            assert set(rows) == {"local_model", "cloud", "folders", "phone", "storage", "updates"}, first
            assert (rows["local_model"]["state"], rows["local_model"]["values"]["model"]) == ("ok", MODEL), rows
            assert (rows["folders"]["state"], rows["folders"]["values"]["names"]) == ("ok", "Documents"), rows
            assert rows["storage"]["state"] == "ok", rows
            assert (rows["cloud"]["state"], rows["phone"]["state"]) == ("off", "off"), rows
            # A source checkout has no install receipt: the updater cannot say it is current.
            assert rows["updates"]["state"] == "unknown", rows
            _summary_is_never_greener_than_its_parts(first)
            assert first["summary"] != "ok"

            # The model server goes away while VOOL keeps running.
            provider.__exit__(None, None, None)
            provider = None
            broken = _doctor(daemon)
            local = _rows(broken)["local_model"]
            assert (local["state"], local["reason"], local["repair"]) == ("problem", "local.not_running", "models"), local
            assert broken["summary"] == "problem" and broken["problems"] >= 1
            _summary_is_never_greener_than_its_parts(broken)

            english = _page(daemon, "en")
            assert "The private model is not running" in english, english
            assert "need attention: 1" in english and "Could not be checked: 1" in english, english
            lithuanian = _page(daemon, "lt")
            assert "The private model is not running" not in lithuanian, lithuanian
    finally:
        if provider is not None:
            provider.__exit__(None, None, None)
