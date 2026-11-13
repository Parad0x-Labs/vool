"""A skill the person added from Browse is theirs to switch off and to remove.

Measured on the Mac (d5d044b0): gentle-workout-plan, added from Browse, could not be removed.
Asking VOOL to remove it was refused with "VOOL manages it; turn it off in the skills settings".
Only VOOL's own packs are VOOL's to keep. A Browse add has an install record, so removing it takes
out its whole package and closes the record: Browse offers it again, and a later add starts On.
"""

from __future__ import annotations

import json

from core import addon_store as store
from core import skill_tools
from tests.test_vool_skills_install_without_a_key import _post, _runtime, home, no_network

ENTRY = "github-ec341f8c28f1570f773bd87b"
SKILL = "gentle-workout-plan"


def _add(home) -> tuple[str, object]:
    status, review = _post({"action": "local_install", "id": ENTRY, "approved": True})
    assert status == 200 and review["report"]["status"] == "clean", review
    status, done = _post({"action": "install", "review_id": review["review_id"], "accepted": True})
    assert status == 200 and done["ok"], done
    pid = done["plugin_id"]
    return pid, home / "addons" / "plugins" / pid


def _row() -> dict:
    return next(e for e in store.catalog()["entries"] if e["id"] == ENTRY)


def _plugin_ids() -> list[str]:
    from core.plugin_catalog import invalidate_storage_listing, read_plugin_catalog

    invalidate_storage_listing()
    return [p["id"] for p in read_plugin_catalog()["plugins"]]


def test_a_skill_added_from_browse_can_be_switched_off_and_on(home, no_network):
    from core.native_skill_library import set_skill_enabled

    _add(home)
    assert set_skill_enabled(SKILL, False)["status"] == "ok"
    assert set_skill_enabled(SKILL, True)["status"] == "ok"


def test_removing_a_skill_added_from_browse_takes_it_out_and_browse_offers_it_again(home, no_network):
    from core.native_skill_library import set_skill_enabled

    pid, pack = _add(home)
    assert pid in _plugin_ids() and _row()["installed"] is True
    assert set_skill_enabled(SKILL, False)["status"] == "ok"

    result = skill_tools.remove_skill(SKILL)
    assert result["status"] == "ok", result
    assert not pack.exists() and (home / "addons" / "removed-skills").is_dir()
    assert pid not in _plugin_ids()
    assert _row()["installed"] is False
    assert skill_tools.remove_skill(SKILL)["status"] == "not_found"

    # Added again later, it is a fresh add: installed, and On rather than the old Off.
    _add(home)
    assert _row()["installed"] is True
    from core.native_skill_library import _read_disabled_ids

    assert SKILL not in _read_disabled_ids()


def test_a_pack_vool_manages_is_still_refused(home, no_network):
    """A store-shaped package with no install record of the person's is not theirs to remove."""
    pack = home / "addons" / "plugins" / "discover-github-0000000000000000000000ff"
    (pack / ".codex-plugin").mkdir(parents=True)
    (pack / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": pack.name}))
    (pack / "skills" / "stray-skill").mkdir(parents=True)
    (pack / "skills" / "stray-skill" / "SKILL.md").write_text("---\nname: stray-skill\ndescription: x\n---\nx\n")
    result = skill_tools.remove_skill("stray-skill")
    assert result["status"] == "refused" and pack.is_dir()
