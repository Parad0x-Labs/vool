# Fresh-install network consent

Fresh installs start with web fallback and automatic outbound work disabled.
Local model discovery and loopback traffic can still run. Enable web in Settings
or with the explicit `VOOL_ENABLE_WEB=1` operator override; automatic update checks
have their own saved consent switch. A concrete cloud model selection retains
its existing provider and spend controls.

Upgrades read the active home's saved policy before applying defaults. Saved
web, outbound and Local Only choices are preserved, including explicit enabled
values. New defaults do not rewrite an existing policy file. Local Only continues
to veto public traffic even when a web-enable environment variable is set.
