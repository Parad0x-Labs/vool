"""TRIAL #4 ground-truth fixture — scorer-only.

Freeze note (disclosed in report): this file was created AFTER the arms ran,
because the session disconnected before the original freeze step. Leak-safety
holds de facto: the file did not exist while any arm ran, so no arm could have
read it. Every entry below is mechanically derived:

- H1: exhaustive brute force over all worker assignments x priority orders
      (greedy earliest-start simulation) -> MIN=18, 4 distinct optima.
- G1: J3 (9m) cannot start before J1 (6m) ends -> makespan >= 15; feasible
      (W1: J1 0-6, J3 6-15 / W2: J2 0-4, J4 4-7, J5 7-12) -> MIN=15.
- All others: arithmetic or explicit-structure deductions as annotated.
"""

H1_TRUTH = {
    "min_makespan": 18,
    # multiple distinct schedules achieve 18 -> optimum not unique
    "unique": False,
}

TRUTH = {
    "H1_scheduling": {
        "must": ["18"],
        "must_not": ["17", "19", "20", "21", "22", "16"],
        "note": "min makespan 18; uniqueness answer should be 'not unique'",
    },
    "H2_epistemic": {
        # rates: A=18/(2h40m online)=6.75/h ; C=15/2h=7.5/h ; B unknown
        "must": ["6.75", "7.5", "cannot", "b"],   # must name B undeterminable + both computable rates
        "must_not": [],   # no numeric rate for B may be invented (checked manually)
        "forbidden_patterns": [r"B\s*[:=]\s*\d"],  # any fabricated rate for server B
    },
    "H3_ci_state": {
        "must": ["not_ready"],
        "should": ["pending", "skipped", "stale", "base"],
    },
    "G1_makespan": {"must": ["15"], "must_not": ["12", "13", "14", "16", "18"]},
    "G2_budget": {
        # 16 VM-months reserved at $0.03*720=$21.60 each => $345.60
        "must": ["345.60", "345.6"],
    },
    "G3_deadline": {"must": ["yes", "150"], "must_not": []},
    "G4_epistemic_rate": {
        # P=8.0 L/100km, R=7.2 L/100km -> R best verified; Q incomparable
        "must": ["r", "7.2", "q"],
    },
    "G5_epistemic_survey": {"must": ["no"], "should": ["response", "nonresponse", "missing"]},
    "G6_logic": {"must": ["invalid"]},
    "G7_causal": {"must": ["cache-flush", "cache flush"], "should": ["deploy", "control", "confound", "isolat"]},
    "G8_code_state": {"must": ["done"], "must_not": ["running", "idle"]},
    "G9_config": {"must": ["no", "95"], "must_not": ["yes"]},
}
