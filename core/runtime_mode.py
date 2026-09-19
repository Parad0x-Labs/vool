"""The production/research boundary for VOOL's networking systems.

VOOL grew a family of peer-networking research systems beside its production
runtime: the UDP/TCP mesh daemon (``core.agent_runtime.daemon``,
``network.transport``), the public-hive presence bridge (``core.public_hive``),
the meet-and-greet swarm, the idle commons / autonomous hive research loops and
the remote task ("hive") execution lane. They are EXPERIMENTAL RESEARCH: not
part of the standard VOOL production runtime, not shipped or enabled in
official releases, and their security assumptions, APIs and architecture may
change or be discarded.

This module is the ONE authority every production choke point consults:

* :func:`research_networking_enabled` — true only under an explicit research
  invocation (``VOOL_RESEARCH_NETWORKING=1``). Never implied by user settings,
  preferences, cluster configs or product edition.
* :func:`mesh_daemon_boot_allowed` — may the API runtime boot the mesh daemon?
* :func:`background_presence_threads_allowed` — may the agent start the
  presence heartbeat / idle-commons / autonomous-research threads?

Research runners (``apps.vool_daemon``, ``apps.meet_and_greet_node``,
``apps.brain_hive_watch_server``, the ops cluster tooling) export the variable
themselves; that is the explicit research invocation. Production never sets it,
so every gate fails closed. See ``research/README.md`` for the full map.
"""
from __future__ import annotations

import os

#: The single explicit opt-in. Deliberately not a preference, not an edition
#: flag, not a config file: accidental activation through normal user settings
#: must be impossible.
_RESEARCH_NETWORKING_ENV = "VOOL_RESEARCH_NETWORKING"


def research_networking_enabled(environ: dict[str, str] | None = None) -> bool:
    """True only under an explicit research/dev invocation of the networking stack."""
    env = os.environ if environ is None else environ
    return str(env.get(_RESEARCH_NETWORKING_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def mesh_daemon_boot_allowed(
    *,
    edition_allows_mesh: bool,
    disable_env_set: bool,
    environ: dict[str, str] | None = None,
) -> bool:
    """May the production API runtime boot the mesh daemon?

    The historical gate (product edition + ``VOOL_DISABLE_MESH_DAEMON``) allowed
    the daemon in the default PERSONAL edition — the shipped app bound
    ``0.0.0.0:49152`` (UDP) and attempted TCP 49153 at every launch. The daemon
    is research: it boots only under the explicit research invocation.
    """
    if disable_env_set:
        return False
    if not edition_allows_mesh:
        return False
    return research_networking_enabled(environ)


def background_presence_threads_allowed(
    *,
    is_test_runtime: bool,
    environ: dict[str, str] | None = None,
) -> bool:
    """May the agent start the public-presence heartbeat, idle-commons and
    autonomous hive-research threads (or the inline startup presence sync)?

    Those threads announce presence to configured hive seeds, periodically
    publish commons updates under the user's identity and pull work from the
    public research queue — research behavior, never production.
    """
    if is_test_runtime:
        return False
    return research_networking_enabled(environ)


