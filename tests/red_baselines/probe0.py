import os
import sys
import time

TREE = sys.argv[1]
sys.path.insert(0, TREE)
import core

assert core.__file__.startswith(TREE), core.__file__
print("core resolved:", core.__file__)
from core.agent_runtime.answer_coverage import (
    FAMILY_CURRENCY,
    coverage_for,
    fused_cross_domain_slices,
    slice_families,
    turn_slices,
    unclaimed_slices,
)
from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn
from core.currency_comparison import uncovered_residue

TESTS = [
  "100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5.",
  "how much is 100 EUR in USD at 1.10? and 500 CHF in JPY at 170?",
  "good morning! what is 500 GBP in EUR? thank you very much",
  "hi there. 1000 TRY to USD?",
]
for t in TESTS:
    t0=time.time()
    ch = closed_semantic_contract_covers_turn(t, session_id="audit-AUD-20260829-003-RED1-census", source_context=None)
    dt=time.time()-t0
    cfp = currency_fast_path(t)
    print(f"{dt:6.3f}s contract={ch!s:5} unclaimed={unclaimed_slices(t)} residue={uncovered_residue(t)} fused={fused_cross_domain_slices(t)} kind={(cfp or {}).get('kind')!r} :: {t!r}")
