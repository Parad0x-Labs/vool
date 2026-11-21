"""The activity page (/trace) is served by the main app, which has no /brain-hive pages.

Its header used to link Overview/Hive/Fabric to /brain-hive, which answered 404 for a person
who opened it from Settings. The header must only link to pages the serving app has.
"""
import re

from core.runtime_task_rail_document import render_runtime_task_rail_document
from core.vool_workstation_ui import render_workstation_header


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', html)


def test_activity_page_header_links_only_to_chat_and_activity() -> None:
    html = render_runtime_task_rail_document(client_script="")
    header = html.split("</header>", 1)[0]
    hrefs = _hrefs(header)
    assert "/chat" in hrefs
    assert "/trace" in hrefs
    assert not any("brain-hive" in h for h in hrefs)


def test_watch_server_header_keeps_all_its_pages() -> None:
    hrefs = _hrefs(render_workstation_header(title="Hive", subtitle="x", default_mode="overview", surface="hive"))
    assert any("brain-hive" in h for h in hrefs)
    assert "/trace" in hrefs
