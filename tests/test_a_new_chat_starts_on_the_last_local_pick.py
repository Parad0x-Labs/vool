"""A new chat starts on the local model the person picked last; a cloud pick never carries into a new chat.

Installed app 6f5532699 (chat-brag run): every new chat went back to Auto and the person picked their local model
again each time. A cloud model could charge in a chat where nobody chose it, so after a cloud pick the next new chat
starts on Auto, as before."""
from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script


def drive(code: str) -> dict:
    result = run_node(DOM + script() + "\nawait new Promise(r => setImmediate(r));\n" + code)
    assert result["errors"] == []
    return result


def test_a_local_pick_carries_and_a_cloud_pick_does_not():
    result = drive("""
localModelIds.add('mistral-nemo:12b');
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaab');
setModelValue('mistral-nemo:12b');
const first = displayedChat;
newChat();
const local = {model: modelValue, effective: effectiveModel(displayedChat, 'Explain tides')};
setModelValue('vendor/gamma:free');
newChat();
const afterCloud = modelValue;
setModelValue(LOCAL_ONLY_MODEL);
newChat();
const lane = modelValue;
await openSession(first);
out({local, afterCloud, lane, reopened: modelValue});
""")
    assert result["local"] == {"model": "mistral-nemo:12b", "effective": "mistral-nemo:12b"}
    assert result["afterCloud"] == "vool"
    assert result["lane"] == "vool-local-only"
    assert result["reopened"] == "mistral-nemo:12b"


def test_a_local_model_no_longer_installed_is_not_pinned_to_a_new_chat():
    result = drive("""
localModelIds.add('mistral-nemo:12b');
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaac');
setModelValue('mistral-nemo:12b');
localModelIds.delete('mistral-nemo:12b');
newChat();
out({fresh: modelValue});
""")
    assert result["fresh"] == "vool"
