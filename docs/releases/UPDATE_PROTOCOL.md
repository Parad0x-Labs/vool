# v0.7 update protocol

One background checker owns the signed beta feed. It accepts an offer only for a
strictly newer runtime version and the exact local OS/architecture. An Intel Mac
does not offer an Apple Silicon or Linux download. Missing compatible artifacts,
invalid signatures, older releases and network failures are explained in status.
Equal versions show **Up to date**. The header and Settings > About both expose
**Check for updates** through the same API.

One Update press closes admission to new work and refuses while destructive work
is active. It downloads and verifies both publisher signatures and SHA-256,
extracts a bounded link-preserving ZIP, checks the platform payload, snapshots
SQLite through its backup API and saves runtime config. A detached continuation
outside the replacement target survives app shutdown. The Mac helper swaps the
bundle; Linux reuses the installer's protected code-swap and rollback helpers,
retaining the runtime home, virtual environment, receipt and local protected paths.
Raw Git checkouts use `git pull` instead of the installer-source swap.

The continuation relaunches and requires the release's pinned build identity on
the health endpoint. An unhealthy or failed migration restores the prior code and
saved data. Failed restoration or relaunch is reported as incomplete recovery;
backups are retained. Rollback does not claim that unsnapshotted external effects
or concurrent post-snapshot activity were reversed.

Startup recovery also runs outside any app it may stop. The installed-source
launcher starts recovery before waiting on an interrupted update lock. A Linux
runtime hosted in `vool-runtime.service` moves its continuation into a separate
user scope with `systemd-run`; if that separation is unavailable, installation
fails before stopping the runtime. Background feed checks retain update failures
until an explicit check, and incomplete rollback blocks another installation.

The production macOS policy and publisher ceremony are documented in
[Publisher keys and beta releases](../RELEASE_KEY_CEREMONY.md). Linux arm64 and
Windows have no supported installer in this release.

Older 0.6 beta builds with an empty feed cannot discover this change. Those users
must manually install v0.7 once with the same runtime home; subsequent compatible
signed releases can use the in-app flow.
