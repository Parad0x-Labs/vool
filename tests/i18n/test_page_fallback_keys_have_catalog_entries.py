"""Every page string looked up as T('key', 'English fallback') must exist in the catalogs.

A key that is only in the page source renders its English fallback in every language and
logs "[vool-i18n] missing key" in the console. Settings > API keys shipped sixteen of those.
Helpers that add their own key prefix (routines' T) are listed in PREFIXED and skipped.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = ROOT / "core" / "i18n" / "catalogs"
PREFIXED = {"core/routines_fragment.py"}
LOOKUP = re.compile(r"""(?<![\w.])(?:T|tfmt)\(\s*'([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)'\s*,""")


def _referenced() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "core").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in PREFIXED:
            continue
        for key in LOOKUP.findall(path.read_text(encoding="utf-8", errors="ignore")):
            found.setdefault(key, set()).add(rel)
    return found


def test_every_page_fallback_key_is_in_the_english_catalog() -> None:
    messages = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))["messages"]
    referenced = _referenced()
    assert len(referenced) > 300, f"parser rot: only {len(referenced)} keys found"
    missing = {k: sorted(v) for k, v in referenced.items() if k not in messages}
    assert not missing, f"page keys without catalog entries: {missing}"


def test_every_locale_carries_the_page_fallback_keys() -> None:
    referenced = set(_referenced())
    for catalog in sorted(CATALOGS.glob("*.json")):
        messages = json.loads(catalog.read_text(encoding="utf-8"))["messages"]
        missing = sorted(k for k in referenced if k not in messages)
        assert not missing, f"{catalog.name} lacks {missing}"
