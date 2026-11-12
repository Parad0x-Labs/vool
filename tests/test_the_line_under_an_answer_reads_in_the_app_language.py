"""The line under an answer, its record and the working card say their words in the app language.

Measured 2026-10-12 while filming the Japanese scene (tracker row 124): the answer was in
Japanese, but the line under it stayed English ("tokens, tok/s, cost unreported, sources,
lookups, Record verified"), and so did the working card's "View activity" and "Stop". Rechecked
2026-11-12 on the v0.8 line (0.8.0-beta, 247124ed) with the app language set to Japanese: the
Copy and Pin buttons had since been translated, but the chip still read
"0 actions · 0 sources · cost not recorded · Record incomplete · no lookups", the card still
said "View activity", and its closing line said "No model was available to answer this message."

Two seams are pinned here:

* the server writes its usage line and its coverage label as keyed parts beside its English, and
  each part's English is exactly its English catalog message, so the page can say the same thing
  in any language without a second wording drifting from the first;
* the page renders those parts, its own counts and the card's words through the catalog, driven
  here through the REAL page script booted with the REAL Japanese bundle.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from core.i18n.catalog import MessageCatalog, catalog_for, format_message
from core.proof_projection import _coverage_of
from core.response_usage_details import usage_display_parts, usage_display_segments
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node
from tests.test_proof_chip_ui import PROOF_OK

EN = catalog_for("en")
JA = MessageCatalog("ja")


def _obs(node_id: str, *, ok: bool = True, pending: bool = False) -> dict:
    return {"kind": "observation", "node_id": node_id, "ok": ok, "pending": pending}


def _derived(node_id: str, *, ok: bool = True, depends_on: tuple[str, ...] = ()) -> dict:
    return {"kind": "derived", "node_id": node_id, "ok": ok, "depends_on": list(depends_on)}


COVERAGE_CASES = (
    [],
    [_obs("a")],
    [_obs("a"), _obs("b"), _obs("c")],
    [_obs("a"), _obs("b", ok=False), _obs("c")],
    [_obs("a"), _obs("b", pending=True, ok=False)],
    [_obs("a"), _derived("d", depends_on=("a",))],
    [_obs("a"), _derived("d", depends_on=("zz",))],
    [_obs("a"), _derived("d", depends_on=("zz",)), _derived("e", depends_on=("yy",))],
    [_obs("a", ok=False), _derived("d", ok=False, depends_on=("a",)), _derived("e", depends_on=("yy",))],
)


@pytest.mark.parametrize("rows", COVERAGE_CASES)
def test_every_coverage_part_is_its_english_catalog_message(rows) -> None:
    coverage = _coverage_of(copy.deepcopy(rows))
    parts = coverage["label_parts"]
    assert parts and coverage["label"] == " · ".join(part["text"] for part in parts)
    for part in parts:
        assert part["key"].startswith("proof.coverage.")
        assert format_message(EN.text(part["key"]), part["params"], "en") == part["text"]


USAGE_CASES = (
    [{"request_seconds": 2.0, "output_tokens": 25, "cost_state": "unreported"}],
    [{"cost_state": "exact", "amount": 0.0012, "currency": "USD"}],
    [{"cost_state": "exact", "amount": 0.0012, "currency": "USD"}, {"cost_state": "unreported"}],
    [{"cost_state": "upper_bound", "amount": 0.25, "currency": "USDC", "input_bound": "0.1", "output_bound": "0.15"}],
    [{"cost_state": "upper_bound", "amount": 0.25, "currency": "USDC"}, {"cost_state": "unreported"}],
    [{"cost_state": "free", "amount": 0, "currency": "USD"}],
)


@pytest.mark.parametrize("details", USAGE_CASES)
def test_every_usage_part_is_its_english_catalog_message(details) -> None:
    parts = usage_display_parts(details)
    assert usage_display_segments(details) == [part["text"] for part in parts]
    for part in parts:
        assert part["key"].startswith("proof.usage.")
        assert format_message(EN.text(part["key"]), part["params"], "en") == part["text"]


def _ja_script() -> str:
    html = render_vool_chat_html(ui_locale="ja")
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL), key=len, reverse=True)
    assert '"locale":"ja"' in scripts[0][:400], "the house script no longer opens with the page's own bundle"
    return scripts[0]


def _drive_ja(program: str, proof: dict | None = None) -> dict:
    setup = "globalThis.__proof = " + json.dumps(proof or {}) + ";\n" + """
globalThis.fetch = async (url) => {
  if (String(url).indexOf('/api/chat/proof') === 0) return { ok: true, status: 200, json: async () => globalThis.__proof };
  return { ok: false, status: 404, json: async () => ({}) };
};
"""
    return run_node(DOM + _ja_script() + setup + program)


def _measured_proof() -> dict:
    proof = copy.deepcopy(PROOF_OK)
    usage = [{"request_seconds": 2.0, "output_tokens": 25, "cost_state": "unreported"}]
    coverage = _coverage_of([_obs("a"), _obs("b"), _obs("c")])
    proof["compact"].update(
        actions=2,
        sources=1,
        cost={"tokens": 120, "usd": None, "display": " · ".join(usage_display_segments(usage)),
              "parts": usage_display_parts(usage), "source": "provider_usage_events"},
        state="VERIFIED",
        state_label="Record verified",
        state_key="proof.state.verified",
        coverage=coverage,
    )
    proof["expanded"]["coverage"] = coverage
    proof["expanded"]["timeline"] = [{"stage": "total", "ms": 2800}, {"stage": "classification", "ms": 458}]
    proof["state_reasons"] = ["task_failed"]
    return proof


MOUNT = """
const host = document.createElement('div');
const chip = await mountProofChip(host, 'sess', 'req-1');
out({
  head: chip ? chip.querySelector('.proof-chip-head').textContent : '',
  state: chip ? chip.querySelector('.pc-state').textContent : '',
  cost: chip ? chip.querySelector('.pc-cost').textContent : '',
  coverage: chip ? chip.querySelector('.pc-coverage').textContent : '',
  body: chip ? chip.querySelector('.proof-chip-body').textContent : '',
});
"""

ENGLISH_ON_THE_CHIP = re.compile(r"\b(actions?|sources?|lookups?|Record|verified|output|tok/s|request time|cost|unreported)\b")


def test_the_chip_head_says_every_word_in_japanese() -> None:
    result = _drive_ja(MOUNT, _measured_proof())

    assert result["state"] == JA.text("proof.state.verified")
    assert result["cost"] == JA.format("proof.usage.rate", rate="12.5") + " · " + JA.text("proof.usage.unreported")
    assert result["coverage"] == JA.format("proof.coverage.done", n=3, total=3)
    assert JA.format("activity.rollup.actions", n=2) in result["head"]
    assert JA.format("proof.sources_count", n=1) in result["head"]
    assert not ENGLISH_ON_THE_CHIP.search(result["head"]), result["head"]


def test_the_open_record_labels_its_rows_in_japanese() -> None:
    body = _drive_ja(MOUNT, _measured_proof())["body"]

    for key in ("proof.verification_scope", "proof.verification_explanation", "proof.model_row", "proof.head_actions",
                "proof.head_sources", "proof.head_timing", "proof.elapsed", "proof.head_receipts", "run.total_word",
                "proof.stage.classification"):
        assert JA.text(key) in body, key
    assert JA.format("proof.why_state", state="VERIFIED") in body
    for english in ("Verification scope", "Checks saved output", "Timing", "Elapsed", "Receipts", "classification"):
        assert english not in body, english


def test_a_proof_without_parts_still_shows_the_servers_words() -> None:
    """An older record (no parts, no state key) keeps its English text instead of going blank."""

    proof = copy.deepcopy(PROOF_OK)
    proof["compact"]["coverage"] = {"label": "2/2 lookups"}
    proof["compact"]["cost"] = {"tokens": None, "usd": None, "display": "3.0 output tok/s (request time)"}
    result = _drive_ja(MOUNT, proof)

    assert result["coverage"] == "2/2 lookups"
    assert result["cost"] == "3.0 output tok/s (request time)"
    assert result["state"] == JA.text("proof.state.verified")


def test_a_part_with_a_foreign_key_is_shown_as_its_text() -> None:
    result = _drive_ja("out({ said: proofPartsText([{ key: 'msg.copy', params: {}, text: 'as written' }]) });")
    assert result["said"] == "as written"


def test_the_working_card_says_its_words_in_japanese() -> None:
    result = _drive_ja("""
const run = {
  status: 'completed', steps: [{ status: 'completed' }, { status: 'completed' }], events: [], ledger: [],
  files: {}, tests: { done: 1, total: 1, failed: 0 }, model: null, cost: { usd_actual: 0.0123 },
  permission: false, verifierPassed: false, verifierFailed: false, reviewState: 'passed', refs: null,
};
applyResultSummary(run);
out({
  parts: run.resultParts,
  noModel: runSummaryWords('No model was available to answer this message.'),
  other: runSummaryWords('A summary nobody mapped'),
  template: buildCard.toString(),
});
""")
    assert JA.format("activity.rollup.actions", n=2) in result["parts"]
    assert JA.text("run.evidence.tested_passed") in result["parts"]
    assert JA.text("run.evidence.tool_completed") in result["parts"]
    assert JA.text("run.review.evidence.passed") in result["parts"]
    assert any(part.startswith("回答と確認に") for part in result["parts"]), result["parts"]
    assert not any(re.search(r"[A-Za-z]{3,}", part) for part in result["parts"]), result["parts"]
    assert result["noModel"] == JA.text("run.summary.no_model")
    assert result["other"] == "A summary nobody mapped"
    assert "pageT('run.view_activity'" in result["template"] and "pageT('run.stop'" in result["template"]


def test_every_summary_the_page_translates_is_still_written_by_the_server() -> None:
    """The page says a fixed set of the server's closing sentences in the app language. If the
    server rewords one, the page would silently fall back to English: this keeps the two in step."""

    page = Path("core/vool_chat_page.py").read_text(encoding="utf-8")
    block = re.search(r"const RUN_SUMMARY_KEYS = Object\.freeze\(\{(.*?)\}\);", page, re.DOTALL)
    assert block, "RUN_SUMMARY_KEYS not found"
    mapped = dict(re.findall(r"'([^']+)':\s*'([a-z_.]+)'", block.group(1)))
    assert len(mapped) >= 7
    server = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("core/web/api/runtime.py", "core/agent_runtime/turn_reasoning.py", "core/agent_runtime/chat_surface.py")
    )
    for english, key in mapped.items():
        assert f'"{english}"' in server, f"the server no longer writes {english!r}"
        assert EN.text(key) == english, key
