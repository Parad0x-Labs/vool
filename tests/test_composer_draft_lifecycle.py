"""Sent text is consumed; failed queue delivery and newer drafts remain recoverable."""
from __future__ import annotations

import json
import re

import pytest

from core.composer_extras_fragment import render_composer_extras_fragment
from tests.chat_page_js_harness import DOM, run_node, script

EXTRAS = re.search(r"<script>(.*?)</script>", render_composer_extras_fragment(), re.S).group(1)
SETUP = """
await Promise.resolve(); await Promise.resolve();
councilOwnsComposer = () => false;
refreshQueue = () => {};
setDisplayedChat('draft-a');
isChatBusy = () => false;
runTurn = async (text) => { globalThis.sentText = text; };
"""


def drive(program: str) -> dict:
    result = run_node(DOM + script() + EXTRAS + SETUP + program)
    assert not result["errors"], result
    return result


@pytest.mark.parametrize("text", ["Read harbor-notes.txt", "Summarize the résumé without edits."])
def test_sent_draft_is_gone_immediately_and_stays_gone_after_boot(text: str) -> None:
    result = drive("""
const typed = """ + json.dumps(text) + """;
inputEl.value = typed;
localStorage.setItem('vool_ui_draft:draft-a', typed);
await send();
const saved = localStorage.getItem('vool_ui_draft:draft-a');
inputEl.value = ''; delete window.VoolComposerExtras;
""" + EXTRAS + """
out({saved, restored: inputEl.value, sent: globalThis.sentText});
""")
    assert result["sent"] == text
    assert result["saved"] is None
    assert result["restored"] == ""


@pytest.mark.parametrize("switch_chat", [False, True])
def test_queue_failure_restores_text_without_losing_a_newer_draft(switch_chat: bool) -> None:
    result = drive("""
isChatBusy = () => true;
let finishQueue;
queueOp = async () => new Promise(resolve => { finishQueue = resolve; });
inputEl.value = 'First unsent message';
localStorage.setItem('vool_ui_draft:draft-a', inputEl.value);
const pending = send();
inputEl.value = 'Second unsent message';
localStorage.setItem('vool_ui_draft:draft-a', inputEl.value);
""" + ("setDisplayedChat('draft-b'); inputEl.value = 'Other chat untouched';" if switch_chat else "") + """
finishQueue(null); await pending;
out({stored: localStorage.getItem('vool_ui_draft:draft-a'), visible: inputEl.value});
""")
    assert result["stored"] == "First unsent message\n\nSecond unsent message"
    assert result["visible"] == ("Other chat untouched" if switch_chat else result["stored"])


def test_queue_success_does_not_clear_the_newer_draft() -> None:
    result = drive("""
isChatBusy = () => true;
let finishQueue;
queueOp = async () => new Promise(resolve => { finishQueue = resolve; });
inputEl.value = 'Queued message';
localStorage.setItem('vool_ui_draft:draft-a', inputEl.value);
const pending = send();
inputEl.value = 'Keep this draft';
localStorage.setItem('vool_ui_draft:draft-a', inputEl.value);
finishQueue({item: {queue_item_id: 'queued-1'}}); await pending;
out({stored: localStorage.getItem('vool_ui_draft:draft-a'), visible: inputEl.value});
""")
    assert result["stored"] == result["visible"] == "Keep this draft"


def test_rejected_send_preserves_its_draft() -> None:
    result = drive("""
councilOwnsComposer = () => true;
inputEl.value = 'Never sent';
localStorage.setItem('vool_ui_draft:draft-a', inputEl.value);
await send();
out({stored: localStorage.getItem('vool_ui_draft:draft-a'), visible: inputEl.value});
""")
    assert result["stored"] == result["visible"] == "Never sent"
