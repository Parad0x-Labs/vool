"""The Proof Chip fragment in the chat page: collapsed by default, honest by construction.

The chip is the message-footer UI over the server's proof projection. These tests drive the REAL
page script (the same inline JavaScript the browser executes, booted in the node DOM harness) so
the assertions cover the code that ships, not a paraphrase of it:

* the collapsed chip reads `actions · sources · cost · STATE`, with the state word taken
  VERBATIM from the server payload — the page never derives an evidence state of its own, so a
  checkmark can only ever mean the server verified;
* opening it shows the expanded view: model/provider, typed actions with outcomes, sources,
  refusals, elapsed time and receipt references;
* a turn with no request id gets NO chip (an absent identity is never invented), and an unbound
  answer (typed 404) gets NO chip either — honest absence, never placeholder truth.
"""
from __future__ import annotations

import json

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

SCRIPT = script()
HTML = render_vool_chat_html()

PROOF_OK = {
    "schema": "vool.turn_proof.v1",
    "bound": True,
    "session_id": "sess",
    "request_id": "req-1",
    "state": "VERIFIED",
    "state_reasons": [],
    "compact": {
        "actions": 2,
        "sources": 1,
        "cost": {"tokens": 120, "usd": None, "source": "provider_usage_events"},
        "state": "VERIFIED",
    },
    "expanded": {
        "turn_keys": ["turn-1"],
        "model": "test-model",
        "provider": "test-provider",
        "actions": [
            {"name": "live_data.weather_lookup", "kind": "tool", "status": "ok", "outcome": "executed", "ok": True, "fact_id": "f1"},
            {"name": "workspace.write_file", "kind": "tool", "status": "refused", "outcome": "refused", "ok": False, "fact_id": "f2"},
        ],
        "sources": [{"host": "wttr.in", "status": "available", "operation": "weather_lookup"}],
        "claims": None,
        "refusals": [
            {"name": "workspace.write_file", "kind": "tool", "status": "refused", "outcome": "refused", "ok": False, "fact_id": "f2"}
        ],
        "elapsed_ms": 812,
        "receipts": ["fc:abc", "fact:tool:live_data.weather_lookup:f1"],
        "finalization": {"finalization_id": "fc:abc", "availability": "available", "content_hash_ok": True},
        "witness": {"consistent": True, "missing": []},
    },
}


def _drive(program: str, *, proof: dict | None = PROOF_OK, not_found: bool = False) -> dict:
    """Boot the page, point its fetch at the given proof document, run the program."""
    setup = ""
    if not_found:
        setup += "globalThis.__notFound = true;\n"
    elif proof is not None:
        setup += "globalThis.__proof = " + json.dumps(proof) + ";\n"
    setup += """
const __realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).indexOf('/api/chat/proof') === 0) {
    if (globalThis.__notFound) return { ok: false, status: 404, json: async () => ({ error: 'proof_not_bound' }) };
    return { ok: true, status: 200, json: async () => globalThis.__proof };
  }
  return __realFetch(url, opts);
};
"""
    return run_node(DOM + SCRIPT + setup + program)


def test_collapsed_chip_renders_the_four_facts_and_the_server_state() -> None:
    result = _drive(
        """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-1');
out({
  mounted: !!chip,
  headText: chip ? chip.querySelector('.proof-chip-head').textContent : '',
  stateWord: chip ? (chip.querySelector('.pc-state') || {}).textContent : '',
  collapsedByDefault: chip ? chip.querySelector('.proof-chip-body').hidden : null,
});
"""
    )
    assert result["mounted"] is True
    head = result["headText"]
    assert "2" in head and "actions" in head
    assert "1" in head and "source" in head
    assert "120" in head
    assert result["stateWord"] == "Record verified"
    assert result["collapsedByDefault"] is True


def test_the_page_never_derives_a_state_of_its_own() -> None:
    """Whatever the server says is what renders — including the unflattering states."""
    proof = dict(PROOF_OK)
    proof["state"] = "INCOMPLETE"
    proof["compact"] = {**PROOF_OK["compact"], "state": "INCOMPLETE"}
    result = _drive(
        """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-1');
out({ stateWord: (chip.querySelector('.pc-state') || {}).textContent });
""",
        proof=proof,
    )
    assert result["stateWord"] == "Record incomplete"


def test_expanded_view_shows_model_actions_sources_refusals_elapsed_receipts() -> None:
    result = _drive(
        """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-1');
const head = chip.querySelector('.proof-chip-head');
head.__click();
const body = chip.querySelector('.proof-chip-body');
out({ opened: !body.hidden, text: body.textContent });
"""
    )
    assert result["opened"] is True
    body = result["text"]
    assert "test-model" in body and "test-provider" in body
    assert "live_data.weather_lookup" in body
    assert "workspace.write_file" in body
    assert "refused" in body
    assert "wttr.in" in body
    assert "0.8s" in body or "812" in body
    assert "fc:abc" in body


def test_no_request_id_means_no_chip() -> None:
    result = _drive(
        """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', '');
out({ mounted: !!chip, children: host.children.length });
""",
        proof=None,
    )
    assert result["mounted"] is False
    assert result["children"] == 0


def test_an_unbound_answer_stays_chipless() -> None:
    result = _drive(
        """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-never');
out({ mounted: !!chip, children: host.children.length });
""",
        not_found=True,
    )
    assert result["mounted"] is False
    assert result["children"] == 0


def test_the_page_fetches_the_proof_by_canonical_identity() -> None:
    result = _drive(
        """
globalThis.__lastUrl = '';
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).indexOf('/api/chat/proof') === 0) { globalThis.__lastUrl = String(url); return { ok: true, status: 200, json: async () => globalThis.__proof }; }
  return realFetch(url, opts);
};
const host = document.createElement('div');
await mountProofChip(host, 'my chat & id', 'req/1?a=b');
out({ url: globalThis.__lastUrl });
"""
    )
    assert result["url"].startswith("/api/chat/proof?")
    assert "session=my+chat+%26+id" in result["url"] or "session=my%20chat%20%26%20id" in result["url"]
    assert "request_id=req%2F1%3Fa%3Db" in result["url"]


def test_the_server_owns_the_vocabulary_and_the_page_dresses_all_four_states() -> None:
    """The four honest states are the SERVER's vocabulary (core.proof_projection); the page only
    dresses them (one CSS state class each) and renders the word verbatim — never a bare
    checkmark glyph in place of the word."""
    from core.proof_projection import (
        STATE_INCOMPLETE,
        STATE_RECORDED,
        STATE_UNVERIFIED,
        STATE_VERIFIED,
    )

    assert {STATE_VERIFIED, STATE_RECORDED, STATE_INCOMPLETE, STATE_UNVERIFIED} == {
        "VERIFIED",
        "RECORDED",
        "INCOMPLETE",
        "UNVERIFIED",
    }
    for word in ("verified", "recorded", "incomplete", "unverified"):
        assert f"pc-state-{word}" in HTML
    for word in ("VERIFIED", "RECORDED", "INCOMPLETE", "UNVERIFIED"):
        # No `✓ VERIFIED`-style mapping: state words render as themselves.
        assert f"'{word}': '✓" not in SCRIPT and f'"{word}": "✓' not in SCRIPT


def test_hydration_and_live_paths_address_the_chip_by_identity() -> None:
    """The reloaded transcript and the finished live run both carry the identity end to end."""
    assert "m.request_id" in SCRIPT
    assert "run.responseCommit && run.responseCommit.request_id" in SCRIPT


def test_boot_restore_hydration_keeps_the_request_id() -> None:
    """Found on the live browser proof: `restoreCurrent()` is the BOOT-time hydration and it had
    its own history push that dropped the request id — chips mounted from the sidebar reopen but
    never after a page reload."""
    # CONVERGENCE, 2026-09-02: boot hydration and the sidebar reopen now share ONE row
    # constructor (`historyEntryFromServer`), because the attachments lane needs the same row to
    # carry its attachments. The law this test defends is unchanged and now has one seam to
    # defend: the constructor carries the request id, and restoreCurrent goes through it. A push
    # that built its own literal row here is exactly the drift that dropped the id before.
    assert "for (const m of msgs) target.history.push(historyEntryFromServer(m));" in SCRIPT
    constructor = SCRIPT[SCRIPT.index("function historyEntryFromServer(m) {") :]
    constructor = constructor[: constructor.index("\n}")]
    assert "request_id: m.request_id || ''" in constructor


def test_the_commit_frame_losing_the_race_still_mounts_the_chip() -> None:
    """Found on the live browser proof: finishRun can paint the finished card BEFORE the final
    NDJSON frame carries the commit identity, and the chip then never mounted. The commit
    applier must stamp the transcript entry and mount the chip when the run already ended."""
    assert "if (run.ended && run.assistantMsgEl && isDisplayed(run.chatId))" in SCRIPT
    assert "_owner.history[run.historyIndex].request_id = commitRequest;" in SCRIPT
