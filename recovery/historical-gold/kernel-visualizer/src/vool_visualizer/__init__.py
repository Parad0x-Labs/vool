"""VOOL kernel trace visualizer — read-only observer over the kernel journal.

Consumes the durable `repl_sessions.jsonl` rows (and optionally a live event
side-channel written by core/kernel/vis_events.py when the operator opts in).
It never mutates kernel behavior: all knobs are render-side intents, and the
write-back path is gated behind the VOOL_VIS_WRITEBACK env flag (default 0).
"""
