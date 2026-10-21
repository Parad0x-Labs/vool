"""Adversarial audit harness for the shipped public VOOL Auto routing path.

Mission: auto-router-audit-20261002 (base dca4679fccd777554c4ad9f9653f0a3e8406b0ef).
This package is production-independent: it adds new files only, never edits
routing owners. All model endpoints are synthetic; the guard transport refuses
every non-synthetic host, so a run can never make a live call.
"""
