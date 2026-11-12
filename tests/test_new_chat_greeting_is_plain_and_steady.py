"""A new chat opens on the same greeting every time for the same hour, with no colour emoji.

Before: `showEmpty` added a random colour emoji to the greeting about 45% of the time ("Good
morning 👋", "Still up? 🦉"), so the first words of a new chat changed on every open and broke the
rule that VOOL's own text carries no colour emoji.

The real function is lifted out of the served page and run under node at every hour of the day,
with the page's randomness pinned low and high: the greeting must not change with it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile

import pytest

from core.vool_chat_page import render_vool_chat_html

PICTOGRAPH = re.compile("[⌀-⏿☀-➿\U0001F000-\U0001FAFF]")
EXPECTED = {
    1: "Still up?",
    9: "Good morning",
    15: "Good afternoon",
    20: "Good evening",
    23: "Working late?",
}


def _show_empty_source() -> str:
    html = render_vool_chat_html()
    start = html.index("function showEmpty() {")
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


def _greetings(hour: int, random_value: float) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to run the chat page script")
    script = f"""
const RealDate = Date;
globalThis.Date = class extends RealDate {{ getHours() {{ return {hour}; }} }};
Math.random = () => {random_value};
globalThis.emptyT = (_key, fallback) => fallback;
globalThis.esc = (text) => String(text);
globalThis.logEl = {{ innerHTML: '' }};
{_show_empty_source()}
showEmpty();
const greet = /<div class="empty-greet">([^<]*)<\\/div>/.exec(logEl.innerHTML);
console.log(JSON.stringify({{ greet: greet ? greet[1] : null }}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        path = handle.name
    completed = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("hour", sorted(EXPECTED))
def test_the_greeting_is_the_same_words_whatever_the_page_rolls(hour: int) -> None:
    low = _greetings(hour, 0.0)["greet"]
    high = _greetings(hour, 0.999)["greet"]

    assert low == high == EXPECTED[hour]
    assert not PICTOGRAPH.search(low)


def test_the_greeting_source_carries_no_emoji_and_no_dice() -> None:
    source = _show_empty_source()
    greeting = source[: source.index("const openers")]
    assert not PICTOGRAPH.search(greeting), greeting
    assert "Math.random" not in greeting, greeting
