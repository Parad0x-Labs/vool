"""Bridges carry ONE RequestGraph's stable ids into the runtime layers that used to re-derive the
request from text: the conductor's semantic frames, the demand ledger, the obligation ledger, the
planner's clauses, and publication. Each bridge is a separate module so a downstream layer can be
swapped or removed without touching the others (the near-zero blast-radius rule)."""
