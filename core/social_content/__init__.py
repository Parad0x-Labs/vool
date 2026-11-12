"""Social Content Manager orchestration (P1, 2026-09-04).

Deterministic orchestration around EXISTING authorities: current-information
and claim-support stay where they are; this package only sequences, labels,
deduplicates and persists. It never fetches, never decides freshness policy,
never publishes, and never becomes a second memory.
"""
