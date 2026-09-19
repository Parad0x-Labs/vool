#!/usr/bin/env python3
"""RED-A — the closed-contract reroute census (AUD-20260829-003 repair round).

WHY THIS EXISTS
---------------
Two councils lean on a "114 broken flows" measurement to argue about where the RSS
remedy may and may not touch.  Neither council re-ran it.  COUNCIL-20260829-002's own
remedy step 2 instructs re-running it and never did.  E011 established the historical
114 is **not reconstructible**: the reverted guard was never committed to any ref,
branch or stash, and no failure list, census file or test-id list of the 114 exists in
the code repo or in the agent-work-index.

So this harness does NOT claim to reproduce the 114.  It defines a *closest defensible
population*, states its exact size, and measures — per flow — the three quantities the
argument actually turns on:

  * which family/lane claims the turn, and at what scope;
  * whether the closed semantic contract holds (the thing a reroute destroys);
  * whether the flow WOULD reroute under each candidate guard.

The population is re-derived from the tree on every run, so the same command run against
the repair worktree after the fix produces a directly diffable record.  The load-bearing
column for judging a fix is `contract_holds`: a flow that holds today and stops holding
after the fix is a reroute the fix caused, which is precisely the blast radius the 114
was a (now-unverifiable) measurement of.

POPULATION DEFINITION (stated, not implied)
-------------------------------------------
P_harvest = every distinct string literal in the tree's test modules that mention the
            currency/closed-contract *admission seam* (SEAM_SYMBOLS below), filtered by
            PROMPT_FILTER to plausible user utterances (see `_is_prompt_shaped`).
P_closed  = the subset of P_harvest for which `closed_semantic_contract_covers_turn`
            returns True at the measured tree.  ONLY these flows can be rerouted by a
            guard at that door, so P_closed is the census population proper.

Both sizes are printed.  Neither is asserted to equal 114.

USAGE
-----
    PYTHONDONTWRITEBYTECODE=1 <tree>/.venv/bin/python \
        tests/red_a_reroute_census.py --tree /Users/example-user/Desktop/vool-checkout \
        --out <path>.json

    # after the fix lands, against the repair worktree, then diff:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/red_a_reroute_census.py \
        --tree /Users/example-user/vool/worktrees/rss-repair-20260829 \
        --out after.json --baseline before.json

READ-ONLY.  Imports and calls real functions from the measured tree; writes only the
--out path.  Never starts, stops or probes the daemon.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from typing import Any

# The admission seam.  A test module is in the population's source set iff its SOURCE
# TEXT mentions one of these.  This is the door the reverted guards sat on; it is
# re-derived per tree so a fix that adds or renames modules is still measured.
SEAM_SYMBOLS = (
    "currency_fast_path",
    "closed_semantic_contract_covers_turn",
    "_currency_reply",
)

# Frozen contract modules that drive the SAME door through the real front door
# (`agent._handle_turn_frontdoor`) without naming a seam symbol.  Named explicitly
# because omitting them would drop the exact fixtures both councils argue about.
EXTRA_SOURCE_MODULES = (
    "test_the_core_authority_repairs_2026_08_19.py",
    "test_mixed_intent_slice_arbitration.py",
    "test_multi_intent_served_coverage.py",
)

# Reject tokens: a literal containing any of these is code/paths/fixtures, not an utterance.
_REJECT_SUBSTRINGS = (
    "\n", "\t", "{", "}", "%", "://", "__", "::", "core.", "apps.", "adapters.",
    "tests/", ".py", "\\", "  ", "<", ">", "|", "==", "assert ", "()", "[", "]",
    "$(", "sha256", "chat_id", "session_id", "workspace",
)

AUDIT_SESSION = "audit-AUD-20260829-003-RED1-census"


def _is_prompt_shaped(value: str) -> bool:
    """A conservative "could a human have typed this into the chat box" test.

    Deliberately biased toward FALSE (drops real prompts rather than admitting fixture
    junk), because a census padded with expected-answer fragments measures nothing.  The
    reject list and the bounds are printed with every run so the filter is auditable.
    """
    text = value.strip()
    if not (4 <= len(text) <= 400):
        return False
    if any(bad in text for bad in _REJECT_SUBSTRINGS):
        return False
    if not any(ch.islower() for ch in text):
        return False
    if text.upper() == text or (text.replace("_", "").isalnum() and "_" in text):
        return False
    # Needs to read as a clause: at least two words, or an explicit question.
    if len(text.split()) < 3 and not text.rstrip().endswith("?"):
        return False
    # Drop identifier-ish and dotted-path-ish literals.
    return " " in text


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() of every Constant node that is a module/class/function docstring."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    out.add(id(body[0].value))
    return out


def harvest(tree: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return (ordered distinct prompts, {prompt: [source modules]})."""
    tests_dir = os.path.join(tree, "tests")
    modules: list[str] = []
    for name in sorted(os.listdir(tests_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(tests_dir, name)
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if name in EXTRA_SOURCE_MODULES or any(sym in src for sym in SEAM_SYMBOLS):
            modules.append(name)

    origins: dict[str, list[str]] = {}
    order: list[str] = []
    for name in modules:
        path = os.path.join(tests_dir, name)
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            tree_ast = ast.parse(src)
        except SyntaxError:
            continue
        skip = _docstring_nodes(tree_ast)
        for node in ast.walk(tree_ast):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in skip:
                continue
            text = node.value
            if not _is_prompt_shaped(text):
                continue
            if text not in origins:
                origins[text] = []
                order.append(text)
            if name not in origins[text]:
                origins[text].append(name)
    return order, origins


class Predicates:
    """Every measured quantity, bound to ONE tree, with the resolution proved."""

    def __init__(self, tree: str) -> None:
        tree = os.path.realpath(tree)
        sys.path.insert(0, tree)
        import core

        resolved = os.path.realpath(core.__file__)
        if not resolved.startswith(tree + os.sep):
            raise SystemExit(
                f"REFUSING TO MEASURE: asked for tree {tree} but core resolved to {resolved}"
            )
        self.tree = tree
        self.core_file = resolved

        from core.agent_runtime import answer_coverage as ac
        from core.agent_runtime.fast_paths_currency import currency_fast_path
        from core.agent_runtime.turn_frontdoor import (
            closed_semantic_contract_covers_turn,
        )
        from core.currency_comparison import uncovered_residue

        self.ac = ac
        self.families = tuple(
            name for name, _probe in (*ac._PROBES, *ac._machine_family_probes())
        )
        self.currency_fast_path = currency_fast_path
        self.contract = closed_semantic_contract_covers_turn
        self.uncovered_residue = uncovered_residue
        self._arms = self._load_arms()

    def _load_arms(self) -> tuple[tuple[str, Any], ...]:
        """The door's NON-currency admitting arms, in the door's own order.

        Attribution only: these are the same callables `closed_semantic_contract_covers_turn`
        consults at turn_frontdoor.py:100-124, called in the same order, so a flow admitted by
        the raw-output-literal arm is never miscounted as a currency contract.  Nothing is
        re-implemented -- if a name disappears from the tree the arm is dropped and reported.
        """
        specs = [
            ("raw_output_contract", "core.raw_output_contract", "parse_raw_output_contract"),
            ("category_error", "core.stable_category_error_reference",
             "stable_category_error_response"),
            ("acronym", "core.stable_acronym_reference", "stable_acronym_reference_response"),
            ("hypothetical_currency", "core.hypothetical_currency_contract",
             "hypothetical_currency_response"),
            ("currency_reference", "core.stable_currency_reference",
             "stable_currency_reference_response"),
            ("exact_semantic", "core.stable_exact_semantics", "stable_exact_semantic_response"),
            ("file_format", "core.stable_file_format_standard_reference",
             "stable_file_format_standard_response"),
            ("metaphor", "core.stable_metaphor_reference", "stable_metaphor_reference_response"),
            ("si_unit", "core.stable_si_unit_reference", "stable_si_unit_reference_response"),
            ("structured_definition", "core.stable_term_contract", "stable_structured_definition"),
            ("closed_world_semantic", "core.closed_world_semantic_contract",
             "closed_world_semantic_response"),
            ("underspecified_label", "core.underspecified_label_contract",
             "underspecified_label_response"),
        ]
        out: list[tuple[str, Any]] = []
        for label, module, attr in specs:
            try:
                mod = __import__(module, fromlist=[attr])
                out.append((label, getattr(mod, attr)))
            except Exception as exc:
                print(f"  [arm attribution] {label} unavailable: {type(exc).__name__}: {exc}")
        return tuple(out)

    def _admit_arm(self, text: str, contract_holds: bool) -> str:
        """Which arm of the door admitted this flow.  '' when the door declined."""
        if not contract_holds:
            return ""
        for label, fn in self._arms:
            try:
                res = fn(text)
            except Exception:
                continue
            if label == "raw_output_contract":
                if res is not None and getattr(res, "exact_text", None) is not None:
                    return label
                continue
            if res is not None:
                return label
        return "currency"

    def _safe(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:  # a raising predicate is a finding, not a crash
            return f"RAISED:{type(exc).__name__}:{exc}"

    def row(self, text: str) -> dict[str, Any]:
        ac = self.ac
        slices = self._safe(ac.turn_slices, text)
        fams = self._safe(ac.slice_families, text)
        unclaimed = self._safe(ac.unclaimed_slices, text)
        fused = self._safe(ac.fused_cross_domain_slices, text)
        residue = self._safe(self.uncovered_residue, text)

        # Which family claims the turn, and at what scope, for EVERY registered family --
        # not just currency.  "which lane claims the turn" is the census's first column.
        claims: dict[str, dict[str, Any]] = {}
        for family in self.families:
            cov = self._safe(ac.coverage_for, text, family)
            if isinstance(cov, str):
                claims[family] = {"error": cov}
                continue
            if not getattr(cov, "consumed", ()) and not getattr(cov, "covers_whole_turn", False):
                continue  # family reads nothing here; omit to keep the record legible
            claims[family] = {
                "scope": str(getattr(cov, "scope", "")),
                "whole_turn": bool(getattr(cov, "covers_whole_turn", False)),
                "consumed": list(getattr(cov, "consumed", ()) or ()),
                "conflicting": list(getattr(cov, "conflicting", ()) or ()),
            }
        # Split deliberately.  A family granted whole-turn authority while consuming NOTHING
        # is the 508/567 mechanism, not a claimant: reporting the two together makes every
        # flow look like it has eighteen claimants and hides who actually answers the turn.
        whole_turn_with_consumption = sorted(
            f
            for f, v in claims.items()
            if isinstance(v, dict) and v.get("whole_turn") and v.get("consumed")
        )
        whole_turn_without_consumption = sorted(
            f
            for f, v in claims.items()
            if isinstance(v, dict) and v.get("whole_turn") and not v.get("consumed")
        )
        whole_turn_claimants = whole_turn_with_consumption

        cfp = self._safe(self.currency_fast_path, text)
        cfp_kind = (cfp or {}).get("kind") if isinstance(cfp, dict) else (
            cfp if isinstance(cfp, str) else None
        )

        contract = self._safe(
            self.contract, text, session_id=AUDIT_SESSION, source_context=None
        )
        contract_holds = contract is True

        unclaimed_list = list(unclaimed) if isinstance(unclaimed, tuple) else unclaimed
        residue_list = list(residue) if isinstance(residue, tuple) else residue
        fused_list = list(fused) if isinstance(fused, tuple) else fused

        has_unclaimed = bool(unclaimed_list) and not isinstance(unclaimed_list, str)
        has_residue = bool(residue_list) and not isinstance(residue_list, str)

        return {
            "text": text,
            "n_slices": len(slices) if isinstance(slices, tuple) else -1,
            "slice_families": [[sid, list(f)] for sid, f in fams]
            if isinstance(fams, tuple)
            else fams,
            "claims": claims,
            "whole_turn_claimants": whole_turn_claimants,
            "whole_turn_without_consumption": whole_turn_without_consumption,
            "n_whole_turn_without_consumption": len(whole_turn_without_consumption),
            "unclaimed_slices": unclaimed_list,
            "fused_cross_domain": fused_list,
            "uncovered_residue": residue_list,
            "currency_fast_path_kind": cfp_kind,
            "contract_holds": contract_holds,
            "admit_arm": self._admit_arm(text, contract_holds),
            # --- the reroute columns -------------------------------------------------
            # (i) the guard whose revert produced the historical "114 broken flows"
            "reroute_bare_unclaimed_guard": contract_holds and has_unclaimed,
            # (iii) COUNCIL-20260829-002's remedy step 2 conjunct
            "reroute_002_conjunct": contract_holds and has_unclaimed and has_residue,
            # (ii) the unguarded cross-domain rule (>=2 domain groups on one slice)
            "reroute_unguarded_cross_domain": contract_holds
            and self._cross_group(text, fams),
        }

    def _cross_group(self, text: str, fams: Any) -> bool:
        if not isinstance(fams, tuple):
            return False
        groups_map = getattr(self.ac, "_FAMILY_DOMAIN_GROUPS", {})
        for _sid, row in fams:
            groups = {groups_map.get(name, name) for name in row}
            if len(groups) >= 2:
                return True
        return False


def _git(tree: str, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", tree, *args], capture_output=True, text=True, check=False
        ).stdout.strip()
    except Exception:
        return "<git unavailable>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="/Users/example-user/Desktop/vool-checkout")
    ap.add_argument("--out", default="")
    ap.add_argument("--baseline", default="", help="a prior --out json to diff against")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    tree = os.path.realpath(args.tree)
    sha = _git(tree, "rev-parse", "HEAD")
    dirty = _git(tree, "status", "--porcelain")

    prompts, origins = harvest(tree)
    if args.limit:
        prompts = prompts[: args.limit]

    preds = Predicates(tree)

    print("=" * 78)
    print("RED-A  CLOSED-CONTRACT REROUTE CENSUS")
    print("=" * 78)
    print(f"tree            : {tree}")
    print(f"HEAD            : {sha}")
    print(f"tree clean      : {'YES' if not dirty else 'NO -- ' + str(len(dirty.splitlines())) + ' dirty paths'}")
    print(f"core resolved   : {preds.core_file}")
    print(f"interpreter     : {sys.executable} ({sys.version.split()[0]})")
    print(f"seam symbols    : {SEAM_SYMBOLS}")
    print(f"extra modules   : {EXTRA_SOURCE_MODULES}")
    print(f"source modules  : {len({m for ms in origins.values() for m in ms})}")
    print(f"P_harvest       : {len(prompts)} distinct prompt-shaped literals")
    print()

    t0 = time.time()
    rows = [preds.row(p) for p in prompts]
    elapsed = time.time() - t0

    closed = [r for r in rows if r["contract_holds"]]
    r_bare = [r for r in closed if r["reroute_bare_unclaimed_guard"]]
    r_002 = [r for r in closed if r["reroute_002_conjunct"]]
    r_xdom = [r for r in closed if r["reroute_unguarded_cross_domain"]]

    currency_arm = [r for r in closed if r["admit_arm"] == "currency"]
    cur_bare = [r for r in currency_arm if r["reroute_bare_unclaimed_guard"]]
    cur_002 = [r for r in currency_arm if r["reroute_002_conjunct"]]
    cur_xdom = [r for r in currency_arm if r["reroute_unguarded_cross_domain"]]

    print(f"P_closed        : {len(closed)}  <-- THE CENSUS POPULATION")
    print("                  (flows where the closed semantic contract holds today;")
    print("                   only these can be rerouted by a guard at that door)")
    print(f"P_closed_currency: {len(currency_arm)}  <-- admitted by the CURRENCY arm")
    print("                  (the tightest analogue of 'frozen single-family closed")
    print("                   contracts'; the other arms are literal-output/stable-")
    print("                   reference contracts a slot guard would also reroute)")
    print()
    print("ADMITTING ARM distribution over P_closed:")
    by_arm: dict[str, int] = {}
    for r in closed:
        by_arm[r["admit_arm"]] = by_arm.get(r["admit_arm"], 0) + 1
    for key, n in sorted(by_arm.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {key}")
    print()
    print("REROUTE COUNTS                             P_closed(184-style)   currency arm")
    print(f"  bare unclaimed-slice guard ('114' guard) : {len(r_bare):5d}"
          f" ({100.0*len(r_bare)/max(1,len(closed)):5.1f}%)   {len(cur_bare):5d}"
          f" ({100.0*len(cur_bare)/max(1,len(currency_arm)):5.1f}%)")
    print(f"  002 remedy conjunct (unclaimed+residue)  : {len(r_002):5d}"
          f" ({100.0*len(r_002)/max(1,len(closed)):5.1f}%)   {len(cur_002):5d}"
          f" ({100.0*len(cur_002)/max(1,len(currency_arm)):5.1f}%)")
    print(f"  unguarded cross-domain rule              : {len(r_xdom):5d}"
          f" ({100.0*len(r_xdom)/max(1,len(closed)):5.1f}%)   {len(cur_xdom):5d}"
          f" ({100.0*len(cur_xdom)/max(1,len(currency_arm)):5.1f}%)")
    print()
    print(f"predicate wall clock: {elapsed:.2f}s for {len(rows)} flows")
    print()

    by_family: dict[str, int] = {}
    for r in closed:
        key = ",".join(r["whole_turn_claimants"]) or "<NOBODY CONSUMES ANYTHING>"
        by_family[key] = by_family.get(key, 0) + 1
    print("WHOLE-TURN CLAIMANT (whole-turn AND non-empty consumption) over P_closed:")
    for key, n in sorted(by_family.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {key}")
    zero_cons = [r for r in closed if r["n_whole_turn_without_consumption"]]
    total_grants = sum(r["n_whole_turn_without_consumption"] for r in closed)
    print("\nZERO-CONSUMPTION whole-turn grants (the E011 C1 mechanism, on THIS population):")
    print(f"  flows carrying at least one : {len(zero_cons)}/{len(closed)}"
          f"  ({100.0*len(zero_cons)/max(1,len(closed)):.1f}%)")
    print(f"  total (flow, family) grants : {total_grants} over"
          f" {len(closed)}x{len(preds.families)} = {len(closed)*len(preds.families)} pairs")
    print()

    print("SAMPLE of currency-arm flows the bare unclaimed guard would reroute (first 15):")
    for r in cur_bare[:15]:
        print(f"  unclaimed={r['unclaimed_slices']} :: {r['text']!r}")
    print()

    payload = {
        "instrument": "RED-A",
        "tree": tree,
        "head": sha,
        "tree_clean": not dirty,
        "core_file": preds.core_file,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seam_symbols": list(SEAM_SYMBOLS),
        "extra_source_modules": list(EXTRA_SOURCE_MODULES),
        "p_harvest": len(prompts),
        "p_closed": len(closed),
        "p_closed_currency_arm": len(currency_arm),
        "reroute_bare_unclaimed_guard": len(r_bare),
        "reroute_002_conjunct": len(r_002),
        "reroute_unguarded_cross_domain": len(r_xdom),
        "currency_arm_reroute_bare_unclaimed_guard": len(cur_bare),
        "currency_arm_reroute_002_conjunct": len(cur_002),
        "currency_arm_reroute_unguarded_cross_domain": len(cur_xdom),
        "origins": {p: origins.get(p, []) for p in prompts},
        "rows": rows,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        print(f"wrote {args.out}")

    if args.baseline:
        _diff(args.baseline, payload)
    return 0


def _diff(baseline_path: str, now: dict[str, Any]) -> None:
    with open(baseline_path, encoding="utf-8") as fh:
        before = json.load(fh)
    b_rows = {r["text"]: r for r in before["rows"]}
    n_rows = {r["text"]: r for r in now["rows"]}
    print("=" * 78)
    print(f"DIFF vs baseline {before.get('head','?')[:8]} @ {before.get('tree')}")
    print("=" * 78)
    lost, gained, claim_moved = [], [], []
    for text, nrow in n_rows.items():
        brow = b_rows.get(text)
        if brow is None:
            continue
        if brow["contract_holds"] and not nrow["contract_holds"]:
            lost.append(text)
        if not brow["contract_holds"] and nrow["contract_holds"]:
            gained.append(text)
        if brow["whole_turn_claimants"] != nrow["whole_turn_claimants"]:
            claim_moved.append((text, brow["whole_turn_claimants"], nrow["whole_turn_claimants"]))
    print(f"P_closed before : {before['p_closed']}   after : {now['p_closed']}")
    print(f"REROUTED BY THE FIX (contract held, now does not): {len(lost)}")
    for t in lost[:40]:
        print(f"  - {t!r}")
    print(f"NEWLY CLOSED (contract did not hold, now does): {len(gained)}")
    for t in gained[:40]:
        print(f"  + {t!r}")
    print(f"WHOLE-TURN CLAIMANT MOVED: {len(claim_moved)}")
    for t, b, n in claim_moved[:40]:
        print(f"  ~ {b} -> {n}  :: {t!r}")
    only_now = set(n_rows) - set(b_rows)
    only_before = set(b_rows) - set(n_rows)
    print(f"population drift: +{len(only_now)} new literals, -{len(only_before)} removed literals")


if __name__ == "__main__":
    raise SystemExit(main())
