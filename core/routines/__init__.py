"""Routines: a task the person shows VOOL once, in one folder, that VOOL can do again when asked.

Teaching watches only the folder the person picks, for at most 30 minutes, and keeps nothing but the
file changes it saw there until the routine is saved or discarded. A routine is a private skill (its
SKILL.md plus a typed routine.json). Every run is prepared as an exact list of changes that the person
approves; nothing runs by itself, from a schedule or from a link. A run never replaces or deletes a
file and never leaves the folder. There is no automatic undo: the run's receipt lists every change.
"""
