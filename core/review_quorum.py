from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuorumDecision:
    accepted: bool
    approvals: int
    rejections: int
    threshold: int


def evaluate_quorum(votes: list[bool], *, threshold: int = 2) -> QuorumDecision:
    approvals = sum(1 for vote in votes if vote)
    rejections = len(votes) - approvals
    return QuorumDecision(accepted=approvals >= threshold, approvals=approvals, rejections=rejections, threshold=threshold)
