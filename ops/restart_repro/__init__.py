"""Restart-first-turn reproduction harness (P0 lane, 2026-09-02).

The served-reality bench (build/served-reality-bench-20260902, final run
sr-20260902-184211-e362a3e0) recorded the restart-continuity case failing
once in five full runs: the FIRST model-bound turn after a clean daemon
restart answered with the honest refusal "I couldn't get a usable model
response in this run..." even though the model lane was a live loopback
stub. Later turns recovered.

This package reproduces that exact case against THIS worktree over N
isolated cold-restart cycles (each cycle: fresh isolated home, boot, one
memory turn, clean SIGTERM restart, then the first model-bound turn
immediately after /healthz reports ready). Nothing here touches a shared
daemon, real credentials, or real user data: the model lane is a loopback
stub, the home is a scratch dir, and external egress is contained by the
stub's CONNECT gate.
"""
